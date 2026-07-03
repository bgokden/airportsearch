"""In-memory index and fuzzy search over the bundled airport dataset."""
from __future__ import annotations

import gzip
import heapq
import json
import re
from functools import lru_cache
from importlib import resources
from typing import Dict, Iterable, List, Optional, Set, Tuple

from rapidfuzz import fuzz, process
from unidecode import unidecode

from .models import Airport, SearchResult

_DATA_PACKAGE = "airportsearch.data"
_DATA_FILE = "airports.jsonl.gz"

# Weight of popularity (page_rank) vs. textual similarity in the final score.
# final = (1 - _POP_WEIGHT) * fuzzy + _POP_WEIGHT * 100 * normalized_page_rank
_POP_WEIGHT = 0.15

# Trigram prefilter tuning. Rather than fuzzy-score all ~134k aliases per query,
# we shortlist the aliases that share the most character trigrams with the query
# and only fuzzy-score those. This turns an O(N) scan into a small rerank.
_CAND_LIMIT = 4000  # max aliases handed to RapidFuzz after prefiltering
_DF_CAP = 4000      # ignore ultra-common trigrams once we already have candidates

_ws_re = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold to lowercase ASCII, collapse whitespace. Makes ``Zürich``==``zurich``==``苏黎世``-transliterated."""
    if not text:
        return ""
    return _ws_re.sub(" ", unidecode(text).lower()).strip()


def _trigrams(text: str) -> Set[str]:
    """Space-padded character trigrams of a normalized string (empty set if too short)."""
    if not text:
        return set()
    padded = f"  {text}  "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


class AirportIndex:
    """Loads the bundled dataset and answers fuzzy ``search`` queries.

    Construction reads and indexes the whole dataset once. Reuse a single
    instance (or the module-level :func:`search`) across many queries.
    """

    def __init__(self, records: List[dict]):
        self.airports: List[Airport] = []
        # Exact-match lookup: normalized code/name -> airport row index.
        self._by_iata: Dict[str, int] = {}
        self._by_city_iata: Dict[str, List[int]] = {}
        # Alias table for fuzzy matching: parallel lists (rapidfuzz-friendly).
        self._alias_strings: List[str] = []
        self._alias_owner: List[int] = []
        self._alias_display: List[str] = []

        max_pr = 0.0
        for rec in records:
            idx = len(self.airports)
            airport = Airport(
                iata=rec["ia"],
                icao=rec.get("ic"),
                name=rec["n"],
                city_iata=rec.get("ci"),
                city_name=rec.get("cy"),
                country_code=rec.get("co"),
                country_name=rec.get("cn"),
                region=rec.get("rg"),
                latitude=rec.get("la"),
                longitude=rec.get("lo"),
                type=rec.get("ty"),
                page_rank=rec.get("pr", 0.0) or 0.0,
                geoname_id=rec.get("gi"),
                source=rec.get("src"),
                alt_names=rec.get("al", []),
            )
            self.airports.append(airport)
            max_pr = max(max_pr, airport.page_rank)

            self._by_iata[airport.iata.lower()] = idx
            if airport.city_iata:
                self._by_city_iata.setdefault(airport.city_iata.lower(), []).append(idx)

            # Build the alias set: everything a user might type.
            aliases = {airport.name}
            if airport.city_name:
                aliases.add(airport.city_name)
            if airport.country_name:
                aliases.add(f"{airport.name} {airport.country_name}")
            aliases.update(airport.alt_names)
            for alias in aliases:
                norm = normalize(alias)
                if norm:
                    self._alias_strings.append(norm)
                    self._alias_owner.append(idx)
                    self._alias_display.append(alias)

        self._max_page_rank = max_pr or 1.0

        # Trigram -> alias positions, for fast candidate shortlisting.
        self._tri_index: Dict[str, List[int]] = {}
        for pos, norm in enumerate(self._alias_strings):
            for tg in _trigrams(norm):
                self._tri_index.setdefault(tg, []).append(pos)

    # -- public API -------------------------------------------------------

    def __len__(self) -> int:
        return len(self.airports)

    def get(self, code: str) -> Optional[Airport]:
        """Return the airport with this exact IATA code, or ``None``."""
        idx = self._by_iata.get((code or "").strip().lower())
        return self.airports[idx] if idx is not None else None

    def _candidate_positions(self, q: str) -> Iterable[int]:
        """Alias positions worth fuzzy-scoring, shortlisted by shared trigrams.

        Falls back to scanning every alias if the query is too short to form
        trigrams or shares none with the corpus, so recall is never silently lost.
        """
        qtris = _trigrams(q)
        if not qtris:
            return range(len(self._alias_strings))

        counts: Dict[int, int] = {}
        # Process rarer (more discriminative) trigrams first; once we already
        # have candidates, skip ultra-common trigrams that add only noise.
        for tg in sorted(qtris, key=lambda t: len(self._tri_index.get(t, ()))):
            postings = self._tri_index.get(tg)
            if not postings:
                continue
            if counts and len(postings) > _DF_CAP:
                continue
            for pos in postings:
                counts[pos] = counts.get(pos, 0) + 1

        if not counts:
            return range(len(self._alias_strings))
        if len(counts) <= _CAND_LIMIT:
            return counts.keys()
        return heapq.nlargest(_CAND_LIMIT, counts, key=counts.get)

    def search(self, query: str, k: int = 5, score_cutoff: float = 40.0) -> List[SearchResult]:
        """Return up to ``k`` airports best matching ``query``, ordered by score desc.

        ``query`` may be an airport name, alternate/multilingual name, airport IATA,
        city IATA, city name, or a combination. Missing/partial parts are fine.
        """
        q = normalize(query)
        if not q:
            return []

        # best score + matched string per airport index
        best: Dict[int, Tuple[float, str]] = {}

        def offer(idx: int, score: float, matched: str) -> None:
            cur = best.get(idx)
            if cur is None or score > cur[0]:
                best[idx] = (score, matched)

        # 1) Exact code hits (airport IATA, then city IATA) — treated as strong signals.
        if len(q) == 3 and q.isalpha():
            up = q.upper()
            idx = self._by_iata.get(q)
            if idx is not None:
                offer(idx, 100.0, up)
            for cidx in self._by_city_iata.get(q, []):
                offer(cidx, 97.0, up)

        # 2) Fuzzy over the trigram-shortlisted alias candidates.
        subset = {pos: self._alias_strings[pos] for pos in self._candidate_positions(q)}
        limit = max(k * 20, 100)
        matches = process.extract(
            q,
            subset,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        for _alias, score, pos in matches:
            offer(self._alias_owner[pos], float(score), self._alias_display[pos])

        if not best:
            return []

        results = []
        for idx, (fuzzy_score, matched) in best.items():
            airport = self.airports[idx]
            pop = 100.0 * (airport.page_rank / self._max_page_rank)
            final = (1 - _POP_WEIGHT) * fuzzy_score + _POP_WEIGHT * pop
            if final < score_cutoff:
                continue
            results.append(SearchResult(airport=airport, score=round(final, 2), matched=matched))

        results.sort(key=lambda r: (r.score, r.airport.page_rank), reverse=True)
        return results[:k]


# -- module-level lazy singleton -----------------------------------------


def _load_records() -> List[dict]:
    with resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).open("rb") as fh:
        with gzip.open(fh, "rt", encoding="utf-8") as gz:
            return [json.loads(line) for line in gz if line.strip()]


@lru_cache(maxsize=1)
def get_index() -> AirportIndex:
    """Return the process-wide :class:`AirportIndex`, loading it on first use."""
    return AirportIndex(_load_records())


def search(query: str, k: int = 5, score_cutoff: float = 40.0) -> List[SearchResult]:
    """Convenience wrapper around :meth:`AirportIndex.search` using the shared index."""
    return get_index().search(query, k=k, score_cutoff=score_cutoff)

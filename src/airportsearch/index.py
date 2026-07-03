"""In-memory index and fuzzy search over the bundled airport dataset."""
from __future__ import annotations

import gzip
import json
import math
from functools import lru_cache
from importlib import resources
from typing import Dict, List, Optional, Tuple

from . import cities as _cities
from ._match import TrigramMatcher, normalize, trigrams
from .geo import get_landmask, haversine_km
from .models import Airport, City, SearchResult  # noqa: F401  (City re-exported)

_DATA_PACKAGE = "airportsearch.data"
_DATA_FILE = "airports.jsonl.gz"

# Weight of popularity (page_rank) vs. textual similarity in the final score.
# final = (1 - _POP_WEIGHT) * fuzzy + _POP_WEIGHT * 100 * normalized_page_rank
_POP_WEIGHT = 0.15

# Default minimum score for a *name* match to be returned. Raised from the old,
# too-permissive 40 so weak fuzzy noise is dropped rather than shown.
DEFAULT_SCORE_CUTOFF = 70.0

# We trust the airport-name result (and skip the nearest fallback) when the top
# hit both scores well AND its matched alias is genuinely trigram-similar to the
# query. The cosine part is what distinguishes a real name match ("Charles de
# Gaulle" -> "Charles de Gaulle Airport") from a nickname coincidence ("The
# Hague" -> "The Windy City"), which score similarly but share few trigrams.
_NAME_TRUST_SCORE = 82.0
_NAME_TRUST_COSINE = 0.34


def _trigram_cosine(a: str, b: str) -> float:
    ta, tb = trigrams(normalize(a)), trigrams(normalize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / ((len(ta) * len(tb)) ** 0.5)

# "Logical nearest" tuning.
_MAX_NEAREST_KM = 400.0        # don't offer airports absurdly far from the city
_CROSS_BORDER_FACTOR = 1.8     # a foreign airport must be ~1.8x closer to win
_WATER_FACTOR = 4.0            # multiplier applied to the share of the path over water


class AirportIndex:
    """Loads the bundled dataset and answers fuzzy ``search`` queries.

    Construction reads and indexes the whole dataset once. Reuse a single
    instance (or the module-level :func:`search`) across many queries.
    """

    def __init__(self, records: List[dict]):
        self.airports: List[Airport] = []
        self._by_iata: Dict[str, int] = {}
        self._by_city_iata: Dict[str, List[int]] = {}
        self._matcher = TrigramMatcher()

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

            # Alias set: everything a user might type.
            aliases = {airport.name}
            if airport.city_name:
                aliases.add(airport.city_name)
            if airport.country_name:
                aliases.add(f"{airport.name} {airport.country_name}")
            aliases.update(airport.alt_names)
            for alias in aliases:
                self._matcher.add(alias, idx)

        self._matcher.build()
        self._max_page_rank = max_pr or 1.0

    # -- public API -------------------------------------------------------

    def __len__(self) -> int:
        return len(self.airports)

    def get(self, code: str) -> Optional[Airport]:
        """Return the airport with this exact IATA code, or ``None``."""
        idx = self._by_iata.get((code or "").strip().lower())
        return self.airports[idx] if idx is not None else None

    def search(
        self,
        query: str,
        k: int = 5,
        score_cutoff: float = DEFAULT_SCORE_CUTOFF,
        nearest_fallback: bool = True,
    ) -> List[SearchResult]:
        """Return up to ``k`` airports best matching ``query``, ordered by score desc.

        ``query`` may be an airport name, alternate/multilingual name, airport IATA,
        city IATA, city name, or a combination. Missing/partial parts are fine.

        If no airport *name* matches confidently and ``nearest_fallback`` is set,
        the query is resolved to a city and the nearest logical airports are
        returned instead (``via="nearest"``), respecting country borders and water.
        """
        q = normalize(query)
        if not q:
            return []

        name_hits = self._name_search(q, k, score_cutoff)

        # Trust the name result only if the top hit is a strong, genuine match.
        trusted = False
        if name_hits:
            top = name_hits[0]
            if top.score >= _NAME_TRUST_SCORE and _trigram_cosine(q, top.matched) >= _NAME_TRUST_COSINE:
                trusted = True

        if not trusted and nearest_fallback:
            nearest = self._nearest_fallback(query, k)
            if nearest:
                return nearest
        return name_hits[:k]

    def nearest_airports(
        self, latitude: float, longitude: float, k: int = 5,
        country_code: Optional[str] = None, max_km: float = _MAX_NEAREST_KM,
    ) -> List[Tuple[Airport, float, float]]:
        """The ``k`` nearest commercial airports to a point, border/water-aware.

        Ranking is by an *effective* distance that penalizes crossing a national
        border and crossing water, so the result is reachable by land rather than
        merely close as the crow flies. Returns ``(airport, real_km, effective_km)``
        sorted by effective distance ascending.
        """
        landmask = get_landmask()
        scored: List[Tuple[float, float, Airport]] = []  # (effective, real, airport)
        for ap in self.airports:
            if ap.latitude is None or ap.longitude is None:
                continue
            real = haversine_km(latitude, longitude, ap.latitude, ap.longitude)
            if real > max_km * 2:  # cheap prefilter; effective can only grow
                continue
            effective = real
            if country_code and ap.country_code and ap.country_code != country_code:
                effective *= _CROSS_BORDER_FACTOR
            if landmask is not None:
                wf = landmask.water_fraction(latitude, longitude, ap.latitude, ap.longitude)
                effective *= 1.0 + _WATER_FACTOR * wf
            if effective <= max_km:
                scored.append((effective, real, ap))
        scored.sort(key=lambda t: t[0])
        return [(ap, real, eff) for eff, real, ap in scored[:k]]

    # -- internals --------------------------------------------------------

    def _name_search(self, q: str, k: int, score_cutoff: float) -> List[SearchResult]:
        best: Dict[int, Tuple[float, str]] = {}

        def offer(idx: int, score: float, matched: str) -> None:
            cur = best.get(idx)
            if cur is None or score > cur[0]:
                best[idx] = (score, matched)

        # Exact code hits (airport IATA, then city IATA) — strong signals.
        if len(q) == 3 and q.isalpha():
            up = q.upper()
            idx = self._by_iata.get(q)
            if idx is not None:
                offer(idx, 100.0, up)
            for cidx in self._by_city_iata.get(q, []):
                offer(cidx, 97.0, up)

        limit = max(k * 20, 100)
        for owner, score, display in self._matcher.query(q, limit=limit, score_cutoff=score_cutoff):
            offer(owner, score, display)

        results = []
        for idx, (fuzzy_score, matched) in best.items():
            airport = self.airports[idx]
            pop = 100.0 * (airport.page_rank / self._max_page_rank)
            final = (1 - _POP_WEIGHT) * fuzzy_score + _POP_WEIGHT * pop
            if final < score_cutoff:
                continue
            results.append(SearchResult(airport=airport, score=round(final, 2), matched=matched))
        results.sort(key=lambda r: (r.score, r.airport.page_rank), reverse=True)
        return results

    def _nearest_fallback(self, query: str, k: int) -> List[SearchResult]:
        gaz = _cities.get_gazetteer()
        if gaz is None:
            return []
        resolved = gaz.resolve(query)
        if resolved is None:
            return []
        city, _city_score = resolved
        near = self.nearest_airports(
            city.latitude, city.longitude, k=k, country_code=city.country_code
        )
        label = f"{city.name}" + (f", {city.country_code}" if city.country_code else "")
        out = []
        for airport, dist, effective in near:
            # Score on effective (border/water-aware) distance so the ordering and
            # the score agree; distance_km still reports the real crow-flies value.
            score = round(100.0 * math.exp(-effective / 500.0), 2)
            out.append(
                SearchResult(
                    airport=airport, score=score, matched=label,
                    via="nearest", distance_km=round(dist, 1),
                )
            )
        return out


# -- module-level lazy singleton -----------------------------------------


def _load_records() -> List[dict]:
    with resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).open("rb") as fh:
        with gzip.open(fh, "rt", encoding="utf-8") as gz:
            return [json.loads(line) for line in gz if line.strip()]


@lru_cache(maxsize=1)
def get_index() -> AirportIndex:
    """Return the process-wide :class:`AirportIndex`, loading it on first use."""
    return AirportIndex(_load_records())


def search(
    query: str, k: int = 5, score_cutoff: float = DEFAULT_SCORE_CUTOFF,
    nearest_fallback: bool = True,
) -> List[SearchResult]:
    """Convenience wrapper around :meth:`AirportIndex.search` using the shared index."""
    return get_index().search(
        query, k=k, score_cutoff=score_cutoff, nearest_fallback=nearest_fallback
    )

"""Shared text-normalization and trigram-accelerated fuzzy matching.

Used by both the airport index and the city gazetteer so they share one fast,
Unicode-folding fuzzy matcher.
"""
from __future__ import annotations

import heapq
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from rapidfuzz import fuzz, process
from unidecode import unidecode

# Any run of non-alphanumeric characters is a separator. This makes commas,
# periods, slashes, hyphens and apostrophes behave like spaces, so "Paris,
# France", "JFK, New York" and "O'Hare" tokenize the same as their spaced forms.
_sep_re = re.compile(r"[^a-z0-9]+")

# Trigram prefilter: shortlist aliases sharing the most trigrams with the query,
# then only fuzzy-score those. Turns an O(N) scan into a small rerank.
_CAND_LIMIT = 4000
_DF_CAP = 4000  # ignore ultra-common trigrams once we already have candidates


def normalize(text: str) -> str:
    """Fold to lowercase ASCII, punctuation to spaces. ``Zürich``==``zurich``, ``St.-Nazaire``==``st nazaire``."""
    if not text:
        return ""
    return _sep_re.sub(" ", unidecode(text).lower()).strip()


def trigrams(text: str) -> Set[str]:
    """Space-padded character trigrams of a normalized string (empty if too short)."""
    if not text:
        return set()
    padded = f"  {text}  "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


class TrigramMatcher:
    """An alias table with a trigram inverted index for fast fuzzy lookup.

    Each alias maps to an arbitrary ``owner`` (e.g. an airport/city row index).
    :meth:`query` returns ``(owner, score, display)`` tuples.
    """

    def __init__(self) -> None:
        self._strings: List[str] = []
        self._owners: List[int] = []
        self._display: List[str] = []
        self._tri_index: Dict[str, List[int]] = {}
        self._built = False

    def add(self, text: str, owner: int, display: Optional[str] = None) -> None:
        norm = normalize(text)
        if not norm:
            return
        self._strings.append(norm)
        self._owners.append(owner)
        self._display.append(text if display is None else display)

    def build(self) -> None:
        for pos, norm in enumerate(self._strings):
            for tg in trigrams(norm):
                self._tri_index.setdefault(tg, []).append(pos)
        self._built = True

    def __len__(self) -> int:
        return len(self._strings)

    def _candidate_positions(self, qtris: Set[str]) -> Iterable[int]:
        counts: Dict[int, int] = {}
        for tg in sorted(qtris, key=lambda t: len(self._tri_index.get(t, ()))):
            postings = self._tri_index.get(tg)
            if not postings:
                continue
            if counts and len(postings) > _DF_CAP:
                continue
            for pos in postings:
                counts[pos] = counts.get(pos, 0) + 1
        if not counts:
            return range(len(self._strings))
        if len(counts) <= _CAND_LIMIT:
            return counts.keys()
        return heapq.nlargest(_CAND_LIMIT, counts, key=counts.get)

    def query(
        self, q: str, limit: int, score_cutoff: float, min_cosine: float = 0.0
    ) -> List[Tuple[int, float, str]]:
        """Return ``(owner, score, display)`` best matches for ``q``.

        ``min_cosine`` adds a length-aware gate: a hit is dropped unless the query
        and alias share at least that trigram cosine similarity. This rejects the
        short-vs-long false positives WRatio alone accepts (e.g. "ann" ⊂ "cannes",
        "utrecth" ~ "recife"). Leave it 0 for airport-name matching, where partial
        multi-word queries ("Kennedy airport new york") legitimately have low overlap.
        """
        q = normalize(q)  # single source of truth; alias strings are normalized too
        qtris = trigrams(q)
        if not q or not qtris:
            return []

        subset = {pos: self._strings[pos] for pos in self._candidate_positions(qtris)}
        matches = process.extract(
            q, subset, scorer=fuzz.WRatio, limit=limit, score_cutoff=score_cutoff
        )

        out: List[Tuple[int, float, str]] = []
        nq = len(qtris)
        for _s, wscore, pos in matches:
            if min_cosine > 0.0 and self._strings[pos] != q:
                b = trigrams(self._strings[pos])
                cosine = len(qtris & b) / ((nq * len(b)) ** 0.5) if b else 0.0
                if cosine < min_cosine:
                    continue  # length-aware gate; exact string matches always pass
            out.append((self._owners[pos], float(wscore), self._display[pos]))
        return out

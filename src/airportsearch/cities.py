"""City gazetteer used to resolve airport-less queries to a location."""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib import resources
from typing import List, Optional, Tuple

from ._match import TrigramMatcher
from .models import City

_DATA_PACKAGE = "airportsearch.data"
_DATA_FILE = "cities.jsonl.gz"

# A resolved city must clear this fuzzy score to be trusted for the fallback.
CITY_MIN_SCORE = 85.0


class CityGazetteer:
    """Fuzzy-resolves a query string to the most likely populated place."""

    def __init__(self, records: List[dict]):
        self.cities: List[City] = []
        self._matcher = TrigramMatcher()
        for rec in records:
            idx = len(self.cities)
            self.cities.append(
                City(
                    name=rec["n"],
                    country_code=rec.get("co"),
                    latitude=rec["la"],
                    longitude=rec["lo"],
                    population=rec.get("pop", 0) or 0,
                )
            )
            self._matcher.add(rec["n"], idx)
            if rec.get("a") and rec["a"] != rec["n"]:
                self._matcher.add(rec["a"], idx)
            for alt in rec.get("al", []):
                self._matcher.add(alt, idx)
        self._matcher.build()

    def __len__(self) -> int:
        return len(self.cities)

    def resolve(self, query: str, min_score: float = CITY_MIN_SCORE) -> Optional[Tuple[City, float]]:
        """Return ``(city, score)`` for the best-matching city, or ``None``.

        Match score comes first: only among cities whose score is within a small
        band of the best do we prefer the more populous one. So "Utrecht" resolves
        to the Dutch city (exact match, 376k) rather than a higher-population city
        that merely fuzzy-matches worse.
        """
        # Cities are (mostly) single tokens, so require real trigram overlap: this
        # is the length-aware gate that stops "utrecth" resolving to "recife".
        hits = self._matcher.query(query, limit=50, score_cutoff=min_score, min_cosine=0.34)
        if not hits:
            return None
        top_score = hits[0][1]  # process.extract returns best-first
        band = top_score - 5.0
        best_idx = None
        best_pop = -1
        best_score = 0.0
        for owner, score, _display in hits:
            if score < band:
                continue
            city = self.cities[owner]
            if city.population > best_pop:
                best_pop, best_idx, best_score = city.population, owner, score
        return (self.cities[best_idx], best_score) if best_idx is not None else None


def _load_records() -> List[dict]:
    with resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).open("rb") as fh:
        with gzip.open(fh, "rt", encoding="utf-8") as gz:
            return [json.loads(line) for line in gz if line.strip()]


@lru_cache(maxsize=1)
def get_gazetteer() -> Optional[CityGazetteer]:
    """Load the bundled city gazetteer, or ``None`` if it was not built in."""
    try:
        return CityGazetteer(_load_records())
    except (FileNotFoundError, OSError):
        return None

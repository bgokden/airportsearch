"""airportsearch — fast, multilingual fuzzy search for commercial airports.

Basic usage::

    import airportsearch

    for hit in airportsearch.search("Frankfurt", k=3):
        print(hit.score, hit.iata, hit.airport.name)

The dataset is built from OpenTravelData (city + airport IATA codes, page-rank,
multilingual alternate names) joined with OurAirports (commercial / scheduled
service filtering). See ``scripts/build_data.py``.
"""
from __future__ import annotations

from .index import AirportIndex, get_index, normalize, search
from .models import Airport, SearchResult

__all__ = [
    "search",
    "get_index",
    "AirportIndex",
    "Airport",
    "SearchResult",
    "normalize",
]

__version__ = "0.1.0"

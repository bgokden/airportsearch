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

from ._match import normalize
from .cities import CityGazetteer, get_gazetteer
from .index import AirportIndex, get_index, search
from .models import Airport, City, SearchResult

__all__ = [
    "search",
    "get_index",
    "get_gazetteer",
    "AirportIndex",
    "CityGazetteer",
    "Airport",
    "City",
    "SearchResult",
    "normalize",
]

__version__ = "0.1.0"

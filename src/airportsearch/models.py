"""Data models for airportsearch."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Airport:
    """A single commercial airport record.

    Attributes:
        iata: 3-letter IATA code of the airport itself (e.g. ``"JFK"``).
        icao: 4-letter ICAO code (e.g. ``"KJFK"``), if known.
        name: Primary (English) airport name.
        city_iata: IATA code of the metropolitan / city the airport serves
            (e.g. ``"NYC"`` for JFK). May equal ``iata`` for single-airport cities.
        city_name: Name of the served city / municipality.
        country_code: ISO 3166-1 alpha-2 country code (e.g. ``"US"``).
        country_name: Human-readable country name.
        region: Top-level administrative subdivision (state / province), if known.
        latitude: Decimal degrees, north positive.
        longitude: Decimal degrees, east positive.
        type: OurAirports facility type (``large_airport`` / ``medium_airport`` / ...),
            or ``None`` for airports sourced only from OpenTravelData in broad mode.
        page_rank: OpenTravelData popularity weight (traffic-derived); larger = busier.
        geoname_id: GeoNames feature id for the airport, if known (join key for more data).
        source: Which source vouched for this airport: ``"ourairports"``,
            ``"optd"``, or ``"both"``.
        alt_names: De-duplicated alternate / multilingual names and aliases used for matching.
    """

    iata: str
    icao: Optional[str]
    name: str
    city_iata: Optional[str]
    city_name: Optional[str]
    country_code: Optional[str]
    country_name: Optional[str]
    region: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    type: Optional[str]
    page_rank: float = 0.0
    geoname_id: Optional[int] = None
    source: Optional[str] = None
    alt_names: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class City:
    """A populated place from the gazetteer, used to resolve airport-less queries.

    Attributes:
        name: Primary city name.
        country_code: ISO 3166-1 alpha-2 country code.
        latitude / longitude: Decimal degrees.
        population: Population (used to disambiguate same-named cities).
    """

    name: str
    country_code: Optional[str]
    latitude: float
    longitude: float
    population: int = 0


@dataclass(frozen=True)
class SearchResult:
    """A scored search hit.

    Attributes:
        airport: The matched :class:`Airport`.
        score: Relevance score in ``[0, 100]`` (higher is better). For ``via="name"``
            this is textual-match + popularity; for ``via="nearest"`` it reflects
            proximity to the resolved city.
        matched: The alias/field value that produced the match (``via="name"``), or
            the resolved city description (``via="nearest"``).
        via: How the hit was found — ``"name"`` (matched airport/city name) or
            ``"nearest"`` (geographic fallback to the nearest logical airport).
        distance_km: Great-circle distance from the resolved city to the airport,
            for ``via="nearest"`` hits (``None`` otherwise).
    """

    airport: Airport
    score: float
    matched: str
    via: str = "name"
    distance_km: Optional[float] = None

    @property
    def iata(self) -> str:
        return self.airport.iata

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        a = self.airport
        extra = f", {self.distance_km:.0f}km" if self.distance_km is not None else ""
        return (
            f"SearchResult(score={self.score:.1f}, iata={a.iata!r}, name={a.name!r}, "
            f"city={a.city_name!r}, country={a.country_code!r}, via={self.via!r}{extra})"
        )

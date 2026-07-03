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

# Country name/alias -> ISO2 comes from the bundled, GeoNames-derived
# ``countries.json`` (built by scripts/build_data.py); nothing is hard-coded here.
_COUNTRIES_FILE = "countries.json"

# Generic words dropped from a code's "place tokens" so they don't create false
# consistency (every airport shares "airport"/"international").
_GENERIC_NAME_TOKENS = {
    "airport", "international", "intl", "regional", "municipal", "national",
    "city", "field", "aerodrome", "airfield", "air", "base", "de", "the",
}


def _trigram_cosine(a: str, b: str) -> float:
    ta, tb = trigrams(normalize(a)), trigrams(normalize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / ((len(ta) * len(tb)) ** 0.5)


def _load_country_lookup() -> Dict[str, str]:
    """Load the bundled GeoNames-derived country map, or ``{}`` if absent."""
    try:
        with resources.files(_DATA_PACKAGE).joinpath(_COUNTRIES_FILE).open("rb") as fh:
            return json.load(fh)
    except (FileNotFoundError, OSError, ValueError):
        return {}

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
        self._by_country: Dict[str, List[int]] = {}   # ISO2 -> airport indices
        self._country_lookup: Dict[str, str] = {}      # normalized name/alias -> ISO2
        self._iso2: set = set()                        # valid ISO2 codes (lowercased)
        self._code_place: Dict[str, set] = {}          # code -> tokens of its city/country/name
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
            if airport.country_code:
                cc = airport.country_code.upper()
                self._by_country.setdefault(cc, []).append(idx)
                self._iso2.add(cc.lower())
                if airport.country_name:
                    self._country_lookup.setdefault(normalize(airport.country_name), cc)

            # Tokens that describe where this airport's code points (its city and
            # country), so "<code> <place>" can be validated as a real code + place.
            place = set(normalize(airport.city_name or "").split())
            place |= set(normalize(airport.country_name or "").split())
            place |= set(normalize(airport.name).split()) - _GENERIC_NAME_TOKENS
            if airport.country_code:
                place.add(airport.country_code.lower())
            self._code_place.setdefault(airport.iata.lower(), set()).update(place)
            if airport.city_iata:
                self._code_place.setdefault(airport.city_iata.lower(), set()).update(place)

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
        # Country name/alias -> ISO2 from the bundled GeoNames-derived file (falls
        # back to the country names already gathered from the airport records).
        for alias, code in _load_country_lookup().items():
            self._country_lookup.setdefault(alias, code)
            self._iso2.add(code.lower())
        # Order each country's airports by popularity, for "only country" queries.
        for cc, idxs in self._by_country.items():
            idxs.sort(key=lambda i: self.airports[i].page_rank, reverse=True)
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

        ``query`` may be any of — or any combination of — an airport name or
        alternate/multilingual name, an airport IATA code, a city name or city
        IATA code, and a country name or code (e.g. "London", "LHR", "Paris
        France", "airports in Japan", "JFK New York"). Missing/partial parts are fine.

        When the query text doesn't match an airport *name* better than it matches
        a *city* (and ``nearest_fallback`` is set), the city is resolved and the
        nearest logical airports are returned (``via="nearest"``), respecting
        country borders and water. A bare country returns that country's busiest
        airports (``via="country"``).
        """
        q = normalize(query)
        if not q or not any(c.isalpha() for c in q):
            return []  # empty, punctuation-only, or digits-only

        country, text = self._split_country(q)
        if country and not text:
            return self._top_in_country(country, k)  # "France", "Japan", "USA"

        results = self._search_text(text or q, k, score_cutoff, nearest_fallback, country)
        if not results and country:
            # The trailing token was a country name but nothing matches inside that
            # country — it was probably a US state / region sharing the name (e.g.
            # "Atlanta Georgia", where Georgia is the US state, not the country).
            # Retry the whole query with no country restriction.
            results = self._search_text(q, k, score_cutoff, nearest_fallback, None)
        return results

    def _search_text(
        self, text: str, k: int, score_cutoff: float,
        nearest_fallback: bool, country: Optional[str],
    ) -> List[SearchResult]:
        name_hits = self._name_search(text, k, score_cutoff)
        # When a country is specified, restrict name hits to it (so "Bath UK"
        # cannot return "Bata" in Equatorial Guinea).
        if country:
            name_hits = [h for h in name_hits if h.airport.country_code == country]

        airport_cos = _trigram_cosine(text, name_hits[0].matched) if name_hits else 0.0

        resolved = None
        if nearest_fallback:
            gaz = _cities.get_gazetteer()
            if gaz is not None:
                resolved = gaz.resolve(text, country_code=country)

        # Decide between the airport-name interpretation and the city interpretation.
        # Both can match at cosine 1.0 by coincidence (a foreign-script alias of
        # "Bata" romanizes to "bath"; "羽田" romanizes to "yu tian" == a Chinese
        # town). We keep the name result only when it is genuinely the better read:
        #   - an explicit IATA code was typed, or
        #   - the name clearly out-matches the city, or
        #   - the matched airport is a major hub (trust the famous name), or
        #   - the matched airport actually sits near the resolved city.
        if name_hits:
            top = name_hits[0].airport
            city_cos = resolved[1] if resolved else 0.0
            near = resolved is not None and self._airport_near_city(top, resolved[0], 200.0)
            major = (top.page_rank / self._max_page_rank) >= 0.05
            tie_or_better = resolved is None or airport_cos >= city_cos - 0.01
            trust_name = (
                resolved is None
                or self._has_exact_code(text)
                or airport_cos > city_cos + 0.05        # name clearly out-matches the city
                or (near and airport_cos >= 0.5)         # names an airport located in the city
                or (tie_or_better and major)             # famous hub even if far from a same-named town
            )
            if trust_name:
                return name_hits[:k]

        if resolved is not None:
            near = self._nearest_results(resolved[0], k, country)
            if near:
                return near
        return name_hits[:k]

    def _code_tokens(self, text: str) -> List[str]:
        """Tokens that are genuinely explicit airport/city IATA codes.

        A lone 3-letter code is taken at face value ("CAN", "FOR"). Inside a phrase
        a token counts as a code only if the rest of the query names that code's
        place — so "JFK New York" seeds JFK (JFK is in New York) but "San Fransico"
        does not seed SAN (San Diego), and "Los Angeles" does not seed LOS (Lagos).
        """
        toks = text.split()
        out = []
        for t in toks:
            if len(t) != 3 or not t.isalpha():
                continue
            if t not in self._by_iata and t not in self._by_city_iata:
                continue
            if len(toks) == 1:
                out.append(t)
                continue
            rest = set(toks) - {t}
            if rest & self._code_place.get(t, ()):  # the code's city/country is mentioned
                out.append(t)
        return out

    def _has_exact_code(self, text: str) -> bool:
        return bool(self._code_tokens(text))

    @staticmethod
    def _airport_near_city(airport: Airport, city: City, within_km: float) -> bool:
        if airport.latitude is None or airport.longitude is None:
            return False
        return haversine_km(
            city.latitude, city.longitude, airport.latitude, airport.longitude
        ) <= within_km

    def _nearest_results(self, city: City, k: int, country: Optional[str]) -> List[SearchResult]:
        near = self.nearest_airports(
            city.latitude, city.longitude, k=k,
            country_code=country or city.country_code,
            restrict_country=country,
        )
        label = f"{city.name}" + (f", {city.country_code}" if city.country_code else "")
        out = []
        for airport, dist, effective in near:
            score = round(100.0 * math.exp(-effective / 500.0), 2)  # closer => higher
            out.append(SearchResult(
                airport=airport, score=score, matched=label,
                via="nearest", distance_km=round(dist, 1),
            ))
        return out

    def _top_in_country(self, country: str, k: int) -> List[SearchResult]:
        idxs = self._by_country.get(country, [])[:k]  # pre-sorted by page_rank
        if not idxs:
            return []
        top_pr = self.airports[idxs[0]].page_rank or 1.0
        out = []
        for i in idxs:
            ap = self.airports[i]
            score = round(60.0 + 40.0 * (ap.page_rank / top_pr), 2) if top_pr else 60.0
            out.append(SearchResult(airport=ap, score=score, matched=ap.country_name or country, via="country"))
        return out

    def _split_country(self, q: str) -> Tuple[Optional[str], str]:
        """Split ``q`` into ``(country_code, remaining_text)``.

        Detects a country as the whole query (name, alias, or bare ISO2 code) or
        as a trailing span of up to three words ("Paris France", "sfo united
        states"). Only the whole-query form accepts a bare 2-letter code.

        A whole query that is itself a known IATA code usually wins over country
        detection (so "FRA" is Frankfurt, not France). The exception: when the code
        belongs to a very minor airport but is also a common country alias, the
        country wins (so "USA" is the United States, not tiny Concord Regional, NC).
        """
        if len(q) == 3 and q.isalpha() and (q in self._by_iata or q in self._by_city_iata):
            code = self._country_lookup.get(q)
            ai = self._by_iata.get(q)
            minor = ai is not None and (self.airports[ai].page_rank / self._max_page_rank) < 0.02
            if code and (ai is None or minor):
                return code, ""
            return None, q
        code = self._country_lookup.get(q)
        if code is None and len(q) == 2 and q in self._iso2:
            code = q.upper()
        if code:
            return code, ""
        tokens = q.split()
        for span in (3, 2, 1):
            if len(tokens) > span:  # must leave at least one remainder token
                tail = " ".join(tokens[-span:])
                code = self._country_lookup.get(tail)
                if code:
                    return code, " ".join(tokens[:-span])
        return None, q

    def nearest_airports(
        self, latitude: float, longitude: float, k: int = 5,
        country_code: Optional[str] = None, max_km: float = _MAX_NEAREST_KM,
        restrict_country: Optional[str] = None,
    ) -> List[Tuple[Airport, float, float]]:
        """The ``k`` nearest commercial airports to a point, border/water-aware.

        Ranking is by an *effective* distance that penalizes crossing a national
        border and crossing water, so the result is reachable by land rather than
        merely close as the crow flies. Returns ``(airport, real_km, effective_km)``
        sorted by effective distance ascending. ``country_code`` softly prefers that
        country; ``restrict_country`` hard-limits results to it.
        """
        landmask = get_landmask()
        scored: List[Tuple[float, float, Airport]] = []  # (effective, real, airport)
        for ap in self.airports:
            if ap.latitude is None or ap.longitude is None:
                continue
            if restrict_country and ap.country_code != restrict_country:
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
        exact: Dict[int, float] = {}  # idx -> fixed score for typed IATA codes

        def offer(idx: int, score: float, matched: str) -> None:
            cur = best.get(idx)
            if cur is None or score > cur[0]:
                best[idx] = (score, matched)

        # Exact code hits: any token that is a known airport or city IATA
        # (so "JFK" and "JFK New York" both surface JFK as a strong signal). These
        # bypass the popularity blend so a typed code can't be outranked by a fuzzy
        # match on a busier airport ("FOR" -> Fortaleza, not Dallas-Fort Worth).
        for tok in set(self._code_tokens(q)):
            up = tok.upper()
            idx = self._by_iata.get(tok)
            if idx is not None:
                offer(idx, 100.0, up)
                exact[idx] = max(exact.get(idx, 0.0), 100.0)
            for cidx in self._by_city_iata.get(tok, []):
                offer(cidx, 97.0, up)
                exact[cidx] = max(exact.get(cidx, 0.0), 97.0)

        limit = max(k * 20, 100)
        for owner, score, display in self._matcher.query(q, limit=limit, score_cutoff=score_cutoff):
            offer(owner, score, display)

        results = []
        for idx, (fuzzy_score, matched) in best.items():
            airport = self.airports[idx]
            if idx in exact:
                final = exact[idx]  # a typed code dominates
            else:
                pop = 100.0 * (airport.page_rank / self._max_page_rank)
                final = (1 - _POP_WEIGHT) * fuzzy_score + _POP_WEIGHT * pop
            if final < score_cutoff:
                continue
            results.append(SearchResult(airport=airport, score=round(final, 2), matched=matched))
        results.sort(key=lambda r: (r.score, r.airport.page_rank), reverse=True)
        return results


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

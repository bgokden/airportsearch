"""Tests for fielded queries: name / IATA / city / country and combinations."""
import pytest

import airportsearch as a


def _top(query, **kw):
    hits = a.search(query, k=5, **kw)
    return hits[0].iata if hits else None


def _iatas(query, **kw):
    return [h.iata for h in a.search(query, k=5, **kw)]


# -- single fields --------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Heathrow", "LHR"),          # airport name
    ("Charles de Gaulle", "CDG"), # airport name
    ("LHR", "LHR"),               # airport IATA
    ("JFK", "JFK"),               # airport IATA
    ("London", "LHR"),            # city with airports
    ("Paris", "CDG"),
])
def test_single_field(query, expected):
    assert expected in _iatas(query)


# -- only country ---------------------------------------------------------

@pytest.mark.parametrize("query,cc", [
    ("France", "FR"), ("Germany", "DE"), ("Japan", "JP"),
    ("United States", "US"), ("USA", "US"), ("UK", "GB"), ("Netherlands", "NL"),
])
def test_only_country_returns_that_countrys_airports(query, cc):
    hits = a.search(query, k=5)
    assert hits, f"no results for {query!r}"
    assert all(h.via == "country" for h in hits)
    assert all(h.airport.country_code == cc for h in hits)
    # busiest first
    prs = [h.airport.page_rank for h in hits]
    assert prs == sorted(prs, reverse=True)


def test_only_country_france_top_is_paris():
    assert _top("France") in {"CDG", "ORY"}


# -- combinations ---------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Paris France", "CDG"),
    ("London UK", "LHR"),
    ("Frankfurt Germany", "FRA"),
    ("Cambridge UK", "CBG"),
    ("Heathrow UK", "LHR"),
    ("Charles de Gaulle France", "CDG"),
])
def test_city_or_name_plus_country(query, expected):
    assert expected in _iatas(query)


def test_country_restricts_results():
    # "Bath" alone is ambiguous with "Bata"; "Bath UK" must stay in the UK.
    hits = a.search("Bath UK", k=5)
    assert hits and all(h.airport.country_code == "GB" for h in hits)
    assert "BSG" not in [h.iata for h in hits]  # Bata, Equatorial Guinea


@pytest.mark.parametrize("query,expected", [
    ("JFK New York", "JFK"),      # IATA + city
    ("Kennedy New York", "JFK"),  # name + city
    ("LHR London", "LHR"),        # IATA + city
])
def test_iata_or_name_plus_city(query, expected):
    assert _top(query) == expected


# -- the Bath/Bata short-name ambiguity ----------------------------------

def test_bath_resolves_to_city_not_bata():
    # "Bath" (UK city, no airport) must not return "Bata" (BSG) via a foreign-
    # script alias that happens to romanize to "bath"; it resolves to the city.
    hits = a.search("Bath", k=3)
    assert hits and hits[0].via == "nearest"
    assert "BSG" not in [h.iata for h in hits]
    assert all(h.airport.country_code == "GB" for h in hits)


def test_famous_hub_name_not_hijacked():
    # Symmetric case: 羽田 romanizes to "yu tian" (a real Chinese town) but the
    # airport is a major hub, so the name wins.
    hits = a.search("羽田", k=3)
    assert hits[0].iata == "HND" and hits[0].via == "name"


def test_stopword_not_treated_as_iata():
    # "the" is IATA THE (Teresina); it must not hijack "The Hague".
    hits = a.search("The Hague", k=3)
    assert hits and hits[0].via == "nearest"
    assert all(h.airport.country_code == "NL" for h in hits)


# -- country lookup is data-driven (GeoNames), incl. multilingual ---------

@pytest.mark.parametrize("query,cc", [
    ("Deutschland", "DE"), ("Allemagne", "DE"), ("Alemania", "DE"),
    ("日本", "JP"), ("Japon", "JP"), ("Frankreich", "FR"),
])
def test_multilingual_country_names(query, cc):
    hits = a.search(query, k=3)
    assert hits and hits[0].via == "country"
    assert all(h.airport.country_code == cc for h in hits)


def test_country_lookup_is_loaded_from_data():
    # The mapping comes from the bundled GeoNames-derived file, not hard-coded.
    idx = a.get_index()
    assert len(idx._country_lookup) > 1000  # thousands of names/aliases/codes
    assert idx._country_lookup.get("germany") == "DE"


# -- IATA vs country 3-letter collisions ----------------------------------

@pytest.mark.parametrize("query,expected_iata", [
    ("FRA", "FRA"),  # major airport (Frankfurt) beats any country reading
    ("CAN", "CAN"),  # Guangzhou; "can" is a stopword only inside phrases
    ("FOR", "FOR"),  # Fortaleza
])
def test_code_beats_country_for_major_airports(query, expected_iata):
    assert _top(query) == expected_iata


def test_country_beats_minor_airport_code():
    # "USA" is IATA for tiny Concord Regional (NC) but the country is meant.
    hits = a.search("USA", k=3)
    assert all(h.via == "country" and h.airport.country_code == "US" for h in hits)

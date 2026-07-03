"""Input-format variations: commas/no commas, IATA present/absent, IATA in parens."""
import pytest

import airportsearch as a


def _top(query, **kw):
    hits = a.search(query, k=5, **kw)
    return hits[0].iata if hits else None


def _iatas(query, **kw):
    return [h.iata for h in a.search(query, k=5, **kw)]


# -- IATA in parentheses (booking-UI style) -------------------------------

@pytest.mark.parametrize("query,iata", [
    ("Tokyo (HND)", "HND"),
    ("Haneda (HND)", "HND"),
    ("London Heathrow (LHR)", "LHR"),
    ("Paris (CDG)", "CDG"),
    ("New York (JFK)", "JFK"),
    ("Frankfurt Airport (FRA)", "FRA"),
    ("Amsterdam (AMS)", "AMS"),
    ("Los Angeles (LAX)", "LAX"),
    ("Chicago O'Hare (ORD)", "ORD"),
    ("Dubai (DXB)", "DXB"),
    ("Sydney (SYD)", "SYD"),
])
def test_iata_in_parentheses(query, iata):
    assert _top(query) == iata


@pytest.mark.parametrize("query,iata", [
    ("(LHR)", "LHR"),
    ("Tokyo [HND]", "HND"),
    ("Tokyo(HND)", "HND"),        # no space
    ("tokyo (hnd)", "HND"),       # lowercase code
    ("haneda(hnd)", "HND"),
    ("HND - Tokyo Haneda", "HND"),
    ("HND | Tokyo", "HND"),
    ("Haneda / HND", "HND"),
    ("LHR (LHR)", "LHR"),         # code repeated
])
def test_iata_bracket_and_separator_wrappers(query, iata):
    assert _top(query) == iata


# -- metro code in parentheses --------------------------------------------

def test_metro_code_in_parens():
    assert set(_iatas("London (LON)")) & {"LHR", "LGW", "LTN", "STN", "LCY"}
    assert set(_iatas("New York (NYC)")) & {"JFK", "EWR", "LGA"}


# -- IATA + city + country, with and without commas -----------------------

@pytest.mark.parametrize("query,iata", [
    ("Frankfurt (FRA), Germany", "FRA"),
    ("Frankfurt (FRA) Germany", "FRA"),
    ("Seoul (ICN), South Korea", "ICN"),
    ("Sydney (SYD) Australia", "SYD"),
    ("Paris (CDG), France", "CDG"),
    ("London Heathrow (LHR), UK", "LHR"),
])
def test_iata_city_country_combo(query, iata):
    assert _top(query) == iata


# -- with-IATA and without-IATA give the same answer ----------------------

@pytest.mark.parametrize("with_iata,without_iata,iata", [
    ("Tokyo Haneda (HND)", "Tokyo Haneda", "HND"),
    ("London Heathrow (LHR)", "London Heathrow", "LHR"),
    ("Paris Charles de Gaulle (CDG)", "Paris Charles de Gaulle", "CDG"),
    ("New York (JFK)", "JFK", "JFK"),
])
def test_iata_optional(with_iata, without_iata, iata):
    assert _top(with_iata) == iata
    assert iata in _iatas(without_iata)


# -- commas vs spaces are equivalent (same result set order) --------------

@pytest.mark.parametrize("spaced,comma", [
    ("Tokyo Haneda HND", "Tokyo, Haneda, HND"),
    ("Paris CDG France", "Paris, CDG, France"),
    ("New York JFK", "New York, JFK"),
    ("London Heathrow LHR", "London, Heathrow, LHR"),
])
def test_comma_and_space_equivalent(spaced, comma):
    assert _iatas(spaced) == _iatas(comma)


# -- a parenthesized code that contradicts the city: city text still wins --

def test_conflicting_iata_defers_to_text():
    # "Tokyo (LHR)" — the code doesn't match Tokyo, so it isn't trusted; the
    # city text drives the result to a Tokyo airport, not London.
    assert _top("Tokyo (LHR)") in {"HND", "NRT"}

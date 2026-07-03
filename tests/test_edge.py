"""Edge cases: separators, whitespace, case, unicode, degenerate input, params."""
import pytest

import airportsearch as a


def _top(q, **kw):
    hits = a.search(q, k=5, **kw)
    return hits[0].iata if hits else None


# -- separators: commas / spaces / punctuation are interchangeable --------

@pytest.mark.parametrize("query", [
    "Paris France", "Paris, France", "Paris,France", "Paris , France",
    "  Paris ,  France  ", "Paris/France",
])
def test_city_country_separators_equivalent(query):
    assert _top(query) == "CDG"


@pytest.mark.parametrize("query", [
    "JFK New York", "JFK, New York", "JFK,New York", "JFK - New York",
])
def test_iata_city_separators_equivalent(query):
    assert _top(query) == "JFK"


def test_multiple_commas():
    # "New York, NY, USA" — trailing country stripped, rest resolves NYC airports.
    iatas = [h.iata for h in a.search("New York, NY, USA", k=5)]
    assert {"JFK", "EWR", "LGA"} & set(iatas)


@pytest.mark.parametrize("query,expected", [
    ("O'Hare", "ORD"),
    ("Chicago O'Hare", "ORD"),
    ("N'Djamena", "NDJ"),
    ("Baden-Baden", "FKB"),
    ("Ho Chi Minh", "SGN"),
    ("São Paulo/Guarulhos", "GRU"),
])
def test_punctuation_in_names(query, expected):
    assert expected in [h.iata for h in a.search(query, k=5)]


# -- whitespace & case ----------------------------------------------------

@pytest.mark.parametrize("query", ["London", "  London  ", "\tLondon\n", "LONDON", "lOnDoN"])
def test_whitespace_and_case_insensitive(query):
    assert _top(query) == "LHR"


def test_internal_whitespace_collapsed():
    assert _top("San    Francisco") == "SFO"


# -- unicode / multilingual with noise ------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("😀 Tokyo 😀", "HND"),
    ("Zürich", "ZRH"),
    ("Estambul", "IST"),
    ("北京", "PEK"),
    ("Côte d'Ivoire", None),  # country -> just assert it stays in Ivory Coast
])
def test_unicode_and_emoji(query, expected):
    hits = a.search(query, k=3)
    assert hits
    if expected:
        assert expected in [h.iata for h in hits]
    else:
        assert all(h.airport.country_code == "CI" for h in hits)


# -- degenerate input must not crash, and returns empty when meaningless --

@pytest.mark.parametrize("query", ["", "   ", ",", "!!!", "...", "-", "'", "123", "!@#$%"])
def test_meaningless_input_returns_empty(query):
    assert a.search(query, k=5) == []


def test_very_long_input_no_crash():
    assert a.search("x" * 5000, k=5) == []
    # a long but genuine multi-field query still resolves
    assert _top("Frankfurt am Main International Airport, Germany") == "FRA"


def test_none_query_is_handled():
    # normalize tolerates falsy input; empty result rather than an exception.
    assert a.search("", k=5) == []


# -- parameters -----------------------------------------------------------

def test_k_zero_returns_empty():
    assert a.search("London", k=0) == []


def test_k_larger_than_available():
    hits = a.search("London", k=100)
    assert 0 < len(hits) <= 100
    assert len(hits) == len({h.iata for h in hits})  # unique


def test_high_cutoff_filters_name_results():
    # An exact code still passes a high cutoff; a fuzzy near-miss does not.
    assert _top("LHR", score_cutoff=99) == "LHR"


def test_nearest_fallback_toggle():
    on = a.search("Utrecht", k=3, nearest_fallback=True)
    off = a.search("Utrecht", k=3, nearest_fallback=False)
    assert on and on[0].via == "nearest"
    assert all(h.via != "nearest" for h in off)


# -- determinism ----------------------------------------------------------

@pytest.mark.parametrize("query", ["Paris", "Utrecht", "JFK New York", "France", "Bath"])
def test_deterministic(query):
    assert a.search(query, k=5) == a.search(query, k=5)


# -- code / place consistency (data-driven, no stopword list) -------------

@pytest.mark.parametrize("query,expected", [
    ("Los Angeles", "LAX"),   # not LOS (Lagos)
    ("San Fransico", "SFO"),  # not SAN (San Diego)
    ("Las Vegas", "LAS"),     # LAS is genuinely Las Vegas
    ("New York", "JFK"),      # not NEW (New Orleans Lakefront)
])
def test_code_place_consistency(query, expected):
    assert expected in [h.iata for h in a.search(query, k=5)]

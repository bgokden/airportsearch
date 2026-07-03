"""Tests for the nearest-airport geographic fallback (borders + water aware)."""
import pytest

import airportsearch as a


def _iatas(query, **kw):
    return [r.iata for r in a.search(query, k=5, **kw)]


def test_airportless_city_falls_back_to_nearest():
    # Utrecht has no airport of its own; it is served by Amsterdam Schiphol.
    hits = a.search("Utrecht", k=3)
    assert hits and hits[0].via == "nearest"
    assert all(h.airport.country_code == "NL" for h in hits)
    assert "AMS" in [h.iata for h in hits]


def test_fallback_survives_typo():
    assert _iatas("Utrecth") == _iatas("Utrecht")  # misspelling resolves the same


def test_nearest_results_carry_distance():
    top = a.search("Utrecht", k=1)[0]
    assert top.via == "nearest"
    assert top.distance_km is not None and top.distance_km > 0


def test_nearest_ordered_by_score():
    hits = a.search("Utrecht", k=5)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_fallback_respects_borders():
    # Tijuana sits on the US border; SAN/SDM are close but across it. The nearest
    # *logical* airport is the Mexican one.
    hits = a.search("Tijuana", k=3)
    assert hits[0].iata == "TIJ" and hits[0].airport.country_code == "MX"


def test_fallback_avoids_water():
    # Berkeley's crow-flies-nearest big airport is SFO across the bay; the water
    # penalty should keep East-Bay airports (same landmass) ahead of it.
    hits = a.search("Berkeley", k=3)
    assert hits and hits[0].via == "nearest"
    assert hits[0].iata != "SFO"


# Regression guard: these are airport names that score modestly and/or transliterate
# to a real city name (羽田 -> "yu tian" == Yutian, CN). They must NOT be hijacked
# by the fallback — the name match is genuine (high trigram overlap).
@pytest.mark.parametrize(
    "query,expected",
    [("Charles de Gaulle", "CDG"), ("羽田", "HND"), ("Saigon", "SGN"),
     ("Madras", "MAA"), ("Kennedy airport new york", "JFK")],
)
def test_genuine_name_not_hijacked_by_fallback(query, expected):
    hits = a.search(query, k=5)
    assert hits and hits[0].via == "name"
    assert expected in [h.iata for h in hits]


def test_fallback_can_be_disabled():
    off = a.search("Utrecht", k=5, nearest_fallback=False)
    assert all(h.via == "name" for h in off)  # no nearest results when disabled


def test_gazetteer_resolves_city():
    gaz = a.get_gazetteer()
    assert gaz is not None
    city, cosine = gaz.resolve("Utrecht")  # returns (City, match_cosine)
    assert city.country_code == "NL" and cosine >= 0.9


def test_gazetteer_rejects_substring_false_positive():
    # The length-aware cosine gate: "utrecht" must not resolve to the higher-
    # population "Recife" that WRatio alone scores highly.
    city, _ = a.get_gazetteer().resolve("Utrecht")
    assert city.name != "Recife"

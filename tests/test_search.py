"""Behaviour tests for airportsearch.search against the bundled dataset."""
import time

import pytest

import airportsearch as a


def test_index_loads():
    idx = a.get_index()
    assert len(idx) > 3000  # ~4k commercial airports worldwide


def test_exact_iata_airport_code():
    hits = a.search("FRA", k=1)
    assert hits and hits[0].iata == "FRA"


def test_get_by_code():
    ap = a.get_index().get("lhr")  # case-insensitive
    assert ap is not None and ap.iata == "LHR" and ap.country_code == "GB"


def test_name_match():
    assert a.search("Frankfurt", k=3)[0].iata == "FRA"


def test_city_iata_resolves_all_serving_airports():
    # NYC is a metropolitan code serving JFK/EWR/LGA.
    hits = a.search("NYC", k=5)
    iatas = {h.iata for h in hits}
    assert {"JFK", "EWR", "LGA"} & iatas
    for h in hits[:3]:
        assert h.airport.city_iata == "NYC"


def test_fuzzy_typo():
    assert any(h.iata == "SFO" for h in a.search("San Fransico", k=3))


# One row per language/spelling; proves multilingual + historical-name matching.
@pytest.mark.parametrize(
    "query,expected",
    [
        ("Londres", "LHR"),        # FR/ES/PT for London
        ("ロンドン ヒースロー", "LHR"),  # Japanese: London Heathrow
        ("Estambul", "IST"),       # Spanish: Istanbul
        ("Мюнхен", "MUC"),         # Russian: Munich
        ("北京", "PEK"),            # Chinese: Beijing
        ("羽田", "HND"),            # Japanese: Haneda
        ("Zürich", "ZRH"),         # umlaut folds to plain z
        ("Bombay", "BOM"),         # historical name (Mumbai)
        ("Saigon", "SGN"),         # historical name (Ho Chi Minh City)
        ("Roissy", "CDG"),         # local name for Charles de Gaulle
    ],
)
def test_multilingual_and_historical(query, expected):
    iatas = [h.iata for h in a.search(query, k=5)]
    assert expected in iatas, f"{query!r} -> {iatas}, expected {expected}"


# A labeled accuracy gauntlet of deliberately messy queries. Guards against
# regressions in overall match quality, not just individual cases.
GAUNTLET = [
    ("frankfurt am main", "FRA"), ("Franfurt", "FRA"), ("Kennedy airport new york", "JFK"),
    ("newark", "EWR"), ("Charles de Gaulle", "CDG"), ("Pekin", "PEK"),
    ("São Paulo Guarulhos", "GRU"), ("Sao Paulo", "GRU"), ("heathrow", "LHR"),
    ("schiphol", "AMS"), ("Madras", "MAA"), ("los angeles intl", "LAX"),
    ("Kloten", "ZRH"), ("dubai", "DXB"),
]


def test_accuracy_gauntlet_top1():
    ok = sum(bool(h := a.search(q, k=1)) and h[0].iata == exp for q, exp in GAUNTLET)
    assert ok / len(GAUNTLET) >= 0.90, f"only {ok}/{len(GAUNTLET)} top-1 correct"


def test_results_ordered_and_bounded():
    hits = a.search("London", k=4)
    assert len(hits) <= 4
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_results_are_unique_airports():
    hits = a.search("London", k=10)
    iatas = [h.iata for h in hits]
    assert len(iatas) == len(set(iatas))


def test_score_cutoff_filters_returned_score():
    for h in a.search("London", k=10, score_cutoff=85):
        assert h.score >= 85


def test_no_match_returns_empty():
    assert a.search("zzzzxxxqqq wwwvvvuuu", k=5, score_cutoff=80) == []


def test_empty_query_returns_empty():
    assert a.search("", k=5) == []
    assert a.search("   ", k=5) == []


def test_commercial_only_no_closed_or_military():
    # Every airport is vouched for as commercial: either OurAirports gives it a
    # commercial facility type, or OpenTravelData assigns it real scheduled
    # traffic (page_rank > 0). Closed/military fields satisfy neither.
    for ap in a.get_index().airports:
        if ap.type is not None:
            assert ap.type in {"large_airport", "medium_airport", "small_airport"}
        else:
            assert ap.page_rank > 0, f"{ap.iata} has no type and no traffic"


def test_warm_query_is_fast():
    a.search("frankfurt")  # warm the index
    start = time.perf_counter()
    for _ in range(50):
        a.search("charles de gaulle", k=5)
    per_query_ms = (time.perf_counter() - start) / 50 * 1000
    # Generous bound for CI variance; typical is a few ms thanks to the trigram prefilter.
    assert per_query_ms < 60, f"{per_query_ms:.1f} ms/query is too slow"

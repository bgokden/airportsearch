"""Smoke + behaviour tests against the bundled dataset."""
import airportsearch as a


def test_index_loads():
    idx = a.get_index()
    assert len(idx) > 3000  # ~4k commercial airports worldwide


def test_exact_iata():
    hits = a.search("FRA", k=1)
    assert hits and hits[0].iata == "FRA"


def test_name_match():
    hits = a.search("Frankfurt", k=3)
    assert hits[0].iata == "FRA"


def test_city_iata_resolves_all_airports():
    # NYC is a metropolitan code serving JFK/EWR/LGA.
    hits = a.search("NYC", k=5)
    iatas = {h.iata for h in hits}
    assert {"JFK", "EWR", "LGA"} & iatas
    for h in hits[:3]:
        assert h.airport.city_iata == "NYC"


def test_fuzzy_typo():
    hits = a.search("San Fransico", k=3)  # misspelled
    assert any(h.iata == "SFO" for h in hits)


def test_multilingual_alias():
    # "Londres" is the French/Spanish/Portuguese name for London.
    hits = a.search("Londres", k=3)
    assert all(h.airport.country_code == "GB" for h in hits)
    assert any(h.iata in {"LHR", "LGW", "LTN", "STN", "LCY"} for h in hits)


def test_result_ordering_and_bounds():
    hits = a.search("London", k=4)
    assert len(hits) <= 4
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_no_match_returns_empty():
    assert a.search("zzzzxxxqqq wwwvvvuuu", k=5, score_cutoff=80) == []


def test_commercial_only():
    # Every bundled airport has a commercial facility type.
    idx = a.get_index()
    for ap in idx.airports[:200]:
        assert ap.type in {"large_airport", "medium_airport", "small_airport"}

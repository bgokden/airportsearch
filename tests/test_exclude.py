"""Tests for the exclude_countries parameter."""
import airportsearch as a


def _codes(query, **kw):
    return [h.airport.country_code for h in a.search(query, k=5, **kw)]


def test_exclude_drops_country_from_name_path():
    assert "GB" in _codes("London")
    out = _codes("London", exclude_countries=["GB"])
    assert out and "GB" not in out  # other Londons (Ontario, Kentucky, ...)


def test_exclude_applies_to_nearest_fallback():
    # Tijuana normally resolves to TIJ (Mexico); excluding MX gives US airports.
    hits = a.search("Tijuana", k=3, exclude_countries=["MX"])
    assert hits and hits[0].via == "nearest"
    assert all(h.airport.country_code != "MX" for h in hits)


def test_exclude_applies_to_country_query():
    # A bare country that is itself excluded returns nothing.
    assert a.search("France", exclude_countries=["FR"]) == []
    assert a.search("France", exclude_countries=["France"]) == []


def test_exclude_applies_to_exact_code():
    # JFK is in the US; excluding the US drops it.
    assert a.search("JFK", exclude_countries=["US"]) == []


def test_exclude_accepts_country_names_and_aliases():
    by_code = _codes("Moscow", exclude_countries=["RU"])
    by_name = _codes("Moscow", exclude_countries=["Russia"])
    assert "RU" not in by_code and "RU" not in by_name
    assert by_code == by_name


def test_exclude_multiple_countries():
    out = _codes("London", exclude_countries=["GB", "US", "CA"])
    assert out and not ({"GB", "US", "CA"} & set(out))


def test_exclude_none_is_noop():
    assert a.search("London", k=3) == a.search("London", k=3, exclude_countries=None)
    assert a.search("London", k=3) == a.search("London", k=3, exclude_countries=[])


def test_exclude_unknown_value_ignored():
    # A value that resolves to no country simply has no effect.
    assert a.search("London", k=3, exclude_countries=["Narnia"]) == a.search("London", k=3)

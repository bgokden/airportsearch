"""Integrity checks on the bundled dataset itself."""
import airportsearch as a


def test_every_airport_has_valid_iata():
    for ap in a.get_index().airports:
        assert isinstance(ap.iata, str)
        assert len(ap.iata) == 3 and ap.iata.isalpha() and ap.iata.isupper()


def test_iata_codes_are_unique():
    iatas = [ap.iata for ap in a.get_index().airports]
    assert len(iatas) == len(set(iatas))


def test_records_have_names():
    for ap in a.get_index().airports:
        assert ap.name and ap.name.strip()


def test_coordinates_in_range():
    for ap in a.get_index().airports:
        if ap.latitude is not None:
            assert -90 <= ap.latitude <= 90
        if ap.longitude is not None:
            assert -180 <= ap.longitude <= 180


def test_country_codes_are_two_letters():
    for ap in a.get_index().airports:
        if ap.country_code:
            assert len(ap.country_code) == 2 and ap.country_code.isalpha()


def test_page_rank_non_negative():
    for ap in a.get_index().airports:
        assert ap.page_rank >= 0


def test_major_hubs_present():
    idx = a.get_index()
    for code in ["LHR", "JFK", "CDG", "FRA", "DXB", "HND", "SIN", "LAX", "AMS", "IST"]:
        assert idx.get(code) is not None, f"missing major hub {code}"

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


def test_every_airport_has_known_source():
    for ap in a.get_index().airports:
        assert ap.source in {"ourairports", "optd", "both"}


def test_broad_coverage_includes_optd_only_airports():
    # Broad coverage should contribute airports OurAirports lacks.
    sources = {ap.source for ap in a.get_index().airports}
    assert "optd" in sources


def test_geonames_enrichment_present():
    # Major hubs should carry many multilingual aliases from the GeoNames join.
    hnd = a.get_index().get("HND")
    assert hnd is not None and hnd.geoname_id
    assert len(hnd.alt_names) > 20, "expected rich multilingual alt-names for HND"


def test_geoname_ids_are_positive_ints():
    for ap in a.get_index().airports:
        if ap.geoname_id is not None:
            assert isinstance(ap.geoname_id, int) and ap.geoname_id > 0

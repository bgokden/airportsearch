"""Popular real-world queries: busy hubs, common names, typos, natural language."""
import pytest

import airportsearch as a


def _iatas(query, **kw):
    return [h.iata for h in a.search(query, k=5, **kw)]


# -- world's busiest airports, by city name -------------------------------

@pytest.mark.parametrize("city,iata", [
    ("Atlanta", "ATL"), ("Beijing", "PEK"), ("Dubai", "DXB"), ("Los Angeles", "LAX"),
    ("Tokyo", "HND"), ("Chicago", "ORD"), ("London", "LHR"), ("Shanghai", "PVG"),
    ("Paris", "CDG"), ("Dallas", "DFW"), ("Guangzhou", "CAN"), ("Amsterdam", "AMS"),
    ("Frankfurt", "FRA"), ("Istanbul", "IST"), ("Delhi", "DEL"), ("Seoul", "ICN"),
    ("Singapore", "SIN"), ("Bangkok", "BKK"), ("Madrid", "MAD"), ("Mumbai", "BOM"),
    ("Toronto", "YYZ"), ("Sydney", "SYD"), ("Hong Kong", "HKG"), ("Munich", "MUC"),
    ("San Francisco", "SFO"), ("Barcelona", "BCN"), ("Miami", "MIA"), ("Rome", "FCO"),
])
def test_busiest_hubs_by_city(city, iata):
    assert iata in _iatas(city)


# -- famous airports by their own name / nickname -------------------------

@pytest.mark.parametrize("name,iata", [
    ("Haneda", "HND"), ("Narita", "NRT"), ("Changi", "SIN"), ("Heathrow", "LHR"),
    ("Gatwick", "LGW"), ("Schiphol", "AMS"), ("Charles de Gaulle", "CDG"),
    ("Orly", "ORY"), ("Fiumicino", "FCO"), ("Suvarnabhumi", "BKK"), ("O'Hare", "ORD"),
    ("LaGuardia", "LGA"), ("Pearson", "YYZ"), ("Incheon", "ICN"),
    ("Kingsford Smith", "SYD"), ("Sheremetyevo", "SVO"), ("Kastrup", "CPH"),
])
def test_famous_airport_names(name, iata):
    assert iata in _iatas(name)


# -- the Tokyo cluster the user cares about -------------------------------

@pytest.mark.parametrize("query", [
    "Tokyo", "tokyo airport", "Tokyo Haneda", "Haneda", "hanada", "haneda airport",
    "羽田", "羽田空港", "airport near Tokyo", "airports in Tokyo", "nearest airport to Tokyo",
])
def test_tokyo_returns_haneda_or_narita(query):
    iatas = _iatas(query)
    assert "HND" in iatas or "NRT" in iatas
    assert iatas[0] in {"HND", "NRT"}


# -- common misspellings --------------------------------------------------

@pytest.mark.parametrize("typo,iata", [
    ("hanada", "HND"), ("heathow", "LHR"), ("schipol", "AMS"), ("frankfrut", "FRA"),
    ("singapre", "SIN"), ("istanul", "IST"), ("amsterdm", "AMS"), ("bangok", "BKK"),
    ("San Fransico", "SFO"),
])
def test_common_typos(typo, iata):
    assert iata in _iatas(typo)


# -- natural-language phrasings -------------------------------------------

@pytest.mark.parametrize("query,iata", [
    ("Tokyo airport", "HND"), ("Dubai airport", "DXB"), ("Heathrow airport", "LHR"),
    ("Changi airport", "SIN"), ("Delhi airport", "DEL"), ("airport near Tokyo", "HND"),
    ("airports in London", "LHR"), ("nearest airport to Paris", "CDG"),
    ("airport in Dubai", "DXB"),
])
def test_natural_language(query, iata):
    assert iata in _iatas(query)


# -- closed airports route to the live replacement ------------------------

def test_closed_airport_routes_to_live_one():
    # Berlin Tegel (TXL) closed in 2020; it must not appear, and the query should
    # surface Berlin's operating airport (BER) instead.
    iatas = _iatas("Tegel")
    assert "TXL" not in iatas
    assert "BER" in iatas

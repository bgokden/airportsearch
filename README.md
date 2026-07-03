# airportsearch

Fast, multilingual **fuzzy search for commercial airports**. Give it any text — an
airport name, an alternate or foreign-language name, an IATA code, a city, a city
code, or a partial/misspelled combination — and get back the best-matching
airports, scored and ranked.

- 🔎 **Fuzzy & partial** — handles typos, missing words, and abbreviations.
- 🌍 **Multilingual** — matches alternate names in many languages via GeoNames
  (e.g. `Londres`, `ロンドン`, `Мюнхен`, `北京`, `Estambul`, `Constantinople`).
- 🏙️ **City *and* airport IATA codes** — `NYC` → `JFK` / `EWR` / `LGA`,
  each tagged with its metropolitan `city_iata`.
- ✈️ **Commercial only** — airports with scheduled airline service; military
  bases, closed fields, and GA strips are filtered out.
- 📊 **Popularity-aware ranking** — busier hubs float to the top via
  OpenTravelData page-rank.
- ⚡ **Fast** — a trigram prefilter keeps queries at ~5 ms even over ~160k aliases.
- 📦 **Zero network at runtime** — a compact dataset (~1.5 MB, ~5,000 airports)
  ships in the wheel.

## Install

```bash
pip install airportsearch
```

## Usage

```python
import airportsearch

for hit in airportsearch.search("Frankfurt", k=3):
    a = hit.airport
    print(f"{hit.score:.1f}  {a.iata} (city {a.city_iata})  {a.name}  {a.country_code}")
# 95.6  FRA (city FRA)  Frankfurt Airport  DE
# 85.3  HHN (city FRA)  Frankfurt-Hahn Airport  DE
```

Each result is a `SearchResult`:

```python
hit.score          # blended relevance 0..100 (fuzzy similarity + popularity)
hit.iata           # shortcut for hit.airport.iata
hit.matched        # the alias/name that produced the match
hit.airport        # Airport dataclass:
#   iata, icao, name, city_iata, city_name, country_code, country_name,
#   region, latitude, longitude, type, page_rank, geoname_id, source, alt_names
```

Look up a known code directly:

```python
airportsearch.get_index().get("LHR")   # -> Airport(iata='LHR', ...)
```

### Command line

```bash
$ airportsearch "londres" -k 3
  94.8  LHR/LON  London Heathrow Airport  —  London (GB)
  89.3  LGW/LON  London Gatwick Airport  —  London (GB)
  87.1  LTN/LON  Luton Airport  —  London (GB)
```

## How matching works

1. Exact IATA hits (airport code, then city/metropolitan code) are seeded as
   strong signals.
2. A [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) pass scores the query
   against every name/alias, after Unicode-folding both sides
   (`Zürich` == `zurich`) so non-Latin scripts transliterate and match.
3. The best textual score per airport is blended with its popularity
   (`page_rank`) and the top `k` are returned.

## Data sources

The bundled dataset (~5,000 airports) is built by
[`scripts/build_data.py`](scripts/build_data.py) from open data:

| Source | Provides | License |
| --- | --- | --- |
| [OpenTravelData](https://github.com/opentraveldata/opentraveldata) | City + airport IATA codes, page-rank, geoname ids, alternate names | Open (attribution) |
| [OurAirports](https://ourairports.com/data/) | Commercial filter (facility type + scheduled service), coordinates | Public domain |
| [GeoNames](https://www.geonames.org/) | Deep multilingual alternate names (joined by geoname id) | CC BY 4.0 |

Each record carries a `source` (`ourairports` / `optd` / `both`) so you can see
which dataset vouched for it.

### Rebuilding the dataset

```bash
python scripts/build_data.py                    # broad + GeoNames (what ships)
python scripts/build_data.py --coverage strict  # OurAirports scheduled-service only
python scripts/build_data.py --no-geonames      # skip the ~200 MB GeoNames download
python scripts/build_data.py --refresh          # force re-download of all sources
```

**Coverage modes**

- `broad` (default): OurAirports commercial airports **plus** any OpenTravelData
  airport with `page_rank > 0`. OPTD only page-ranks airports with real scheduled
  traffic, so this adds ~1,000 airports OurAirports misses while staying commercial.
- `strict`: only airports OurAirports flags with scheduled airline service.

GeoNames enrichment streams the large `alternateNamesV2.zip`, keeping only names
for the geoname ids in the dataset (curated names are prioritized before the cap
so native-script names are never truncated away).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT © Berk Gokden. Airport data © their respective providers (see above).

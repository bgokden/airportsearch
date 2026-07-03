# airportsearch

Fast, multilingual **fuzzy search for commercial airports**. Give it any text — an
airport name, an alternate or foreign-language name, an IATA code, a city, a city
code, or a partial/misspelled combination — and get back the best-matching
airports, scored and ranked.

- 🔎 **Fuzzy & partial** — handles typos, missing words, and abbreviations.
- 🌍 **Multilingual** — matches alternate names in many languages via GeoNames
  (e.g. `Londres`, `ロンドン`, `Мюнхен`, `北京`, `Estambul`, `Constantinople`).
- 🧩 **Any field, any combination** — airport name, IATA, city, or country, and
  mixes of them: `LHR`, `Heathrow`, `London`, `Paris France`, `airports in Japan`,
  `JFK New York`. A bare country returns its busiest airports (`via="country"`).
  Separators are flexible — commas, spaces, or punctuation all work
  (`Paris, France` == `Paris France`, `JFK, New York` == `JFK New York`).
- 🏙️ **City *and* airport IATA codes** — `NYC` → `JFK` / `EWR` / `LGA`,
  each tagged with its metropolitan `city_iata`.
- ✈️ **Commercial only** — airports with scheduled airline service; military
  bases, closed fields, and GA strips are filtered out.
- 📊 **Popularity-aware ranking** — busier hubs float to the top via
  OpenTravelData page-rank.
- 📍 **Nearest-airport fallback** — a query with no airport of its own (e.g.
  `Utrecht`) resolves to the city and returns the nearest *reachable* airports,
  respecting **national borders and water** (no "closest across the bay").
- ⚡ **Fast** — a trigram prefilter keeps queries at ~5 ms even over ~160k aliases.
- 📦 **Zero network at runtime** — a compact dataset (~2.8 MB, ~5,000 airports +
  city gazetteer + land mask) ships in the wheel.

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
hit.score          # relevance 0..100 (name match+popularity, or proximity)
hit.iata           # shortcut for hit.airport.iata
hit.matched        # the alias that matched, or the resolved city (for nearest)
hit.via            # "name" (matched a name) or "nearest" (geographic fallback)
hit.distance_km    # city->airport distance for via="nearest" hits, else None
hit.airport        # Airport dataclass:
#   iata, icao, name, city_iata, city_name, country_code, country_name,
#   region, latitude, longitude, type, page_rank, geoname_id, source, alt_names
```

Fielded queries — name, IATA, city, country, and combinations:

```python
airportsearch.search("Heathrow")        # airport name  -> LHR
airportsearch.search("LHR")             # airport IATA   -> LHR
airportsearch.search("London")          # city           -> LHR, LGW, LTN ...
airportsearch.search("Japan")           # country        -> HND, NRT, FUK ... (via="country")
airportsearch.search("Paris France")    # city + country -> CDG, ORY, BVA
airportsearch.search("JFK New York")    # IATA + city    -> JFK
airportsearch.search("Deutschland")     # multilingual country -> Frankfurt, Munich ...
```

Nearest-airport fallback for a city with no airport of its own:

```python
for hit in airportsearch.search("Utrecht", k=3):
    print(hit.via, hit.iata, hit.airport.name, f"{hit.distance_km:.0f} km")
# nearest AMS Amsterdam Airport Schiphol 34 km
# nearest RTM Rotterdam The Hague Airport 49 km
# nearest EIN Eindhoven Airport 73 km
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
2. A trigram inverted index shortlists candidate aliases, then
   [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) scores them, after
   Unicode-folding both sides (`Zürich` == `zurich`) so non-Latin scripts
   transliterate and match.
3. The best textual score per airport is blended with its popularity
   (`page_rank`) and the top `k` are returned.
4. **If nothing matches confidently** (the top hit's alias isn't trigram-similar
   enough to the query — distinguishing a real name match from a nickname
   coincidence), the query is resolved to a city and the **nearest logical
   airports** are returned (`via="nearest"`, with `distance_km`).

### Nearest-airport ("logical") ranking

Naive great-circle nearest is wrong when the closest airport is across a border
or a body of water. Airports are ranked by an **effective distance**:

```
effective = crow_flies_km × border_factor × water_factor
```

- `border_factor` penalizes airports in a different country, so a city is served
  by its own country's airports unless a foreign one is dramatically closer.
- `water_factor` samples the straight path against a bundled land/water bitmap and
  penalizes sea/bay crossings (you can't drive across water).

So `Utrecht` → `AMS`/`RTM`/`EIN` (Netherlands), `Tijuana` → `TIJ` (Mexico, not the
closer US fields across the border), and `Berkeley` → East-Bay airports rather
than `SFO` across the bay.

### Why not plain fuzzy search?

Plain fuzzy scoring (`WRatio`) is *length-blind*: it scores short strings against
unrelated long ones highly (`ann` ⊂ `cannes`, `utrecth` ~ `recife`). City
resolution therefore adds a **length-aware trigram-cosine gate** so only genuine
name overlaps resolve, while airport-name matching keeps full fuzzy for partial
multi-word queries.

## Data sources

The bundled dataset (~5,000 airports) is built by
[`scripts/build_data.py`](scripts/build_data.py) from open data:

| Source | Provides | License |
| --- | --- | --- |
| [OpenTravelData](https://github.com/opentraveldata/opentraveldata) | City + airport IATA codes, page-rank, geoname ids, alternate names | Open (attribution) |
| [OurAirports](https://ourairports.com/data/) | Commercial filter (facility type + scheduled service), coordinates | Public domain |
| [GeoNames](https://www.geonames.org/) | Deep multilingual alternate names, city gazetteer (`cities15000`), country names/aliases (`countryInfo` + alternate names, incl. multilingual & `UK`/`USA`) | CC BY 4.0 |
| [global-land-mask](https://pypi.org/project/global-land-mask/) | Coarse land/water bitmap (built once, ~12 KB) for water-aware nearest airport | build-time only |

Each record carries a `source` (`ourairports` / `optd` / `both`) so you can see
which dataset vouched for it.

### Rebuilding the dataset

```bash
# The water-aware land mask needs two build-time-only deps:
pip install -e ".[build-data]"

python scripts/build_data.py                    # broad + GeoNames + cities + land mask
python scripts/build_data.py --coverage strict  # OurAirports scheduled-service only
python scripts/build_data.py --no-geonames      # skip the ~200 MB GeoNames download
python scripts/build_data.py --cities none      # skip the nearest-airport gazetteer
python scripts/build_data.py --no-landmask      # nearest airport stays country-aware only
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

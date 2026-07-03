#!/usr/bin/env python3
"""Build the bundled airport dataset for airportsearch.

Sources (all open data):
  * OurAirports airports.csv           -> commercial filtering (type +
                                          scheduled_service), coordinates.
  * OpenTravelData optd_por_public.csv -> city/metropolitan IATA codes,
                                          page_rank (popularity), geoname ids,
                                          and multilingual alternate names.
  * GeoNames alternateNamesV2.zip      -> deeper multilingual alternate names,
                                          joined by geoname id (optional).

Coverage modes:
  strict  Only airports OurAirports marks with scheduled airline service.
  broad   The above PLUS any OpenTravelData airport with page_rank > threshold.
          OPTD only assigns a page_rank to airports with real scheduled traffic,
          so this stays "commercial" while adding airports OurAirports misses.

Output:
  src/airportsearch/data/airports.jsonl.gz  (one compact JSON record per line)

Examples:
  python scripts/build_data.py                       # broad + geonames (default)
  python scripts/build_data.py --coverage strict     # OurAirports-only set
  python scripts/build_data.py --no-geonames         # skip the big GeoNames step
  python scripts/build_data.py --refresh             # re-download all sources
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set

from unidecode import unidecode

_sep_re = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Match airportsearch._match.normalize (fold to lowercase ASCII, punct -> space)."""
    return _sep_re.sub(" ", unidecode(text or "").lower()).strip()

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OPTD_URL = (
    "https://raw.githubusercontent.com/opentraveldata/opentraveldata/"
    "master/opentraveldata/optd_por_public.csv"
)
GEONAMES_URL = "https://download.geonames.org/export/dump/alternateNamesV2.zip"
CITIES_URL = "https://download.geonames.org/export/dump/{}.zip"
COUNTRYINFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build_cache"
DATA_DIR = ROOT / "src" / "airportsearch" / "data"
OUT = DATA_DIR / "airports.jsonl.gz"
CITIES_OUT = DATA_DIR / "cities.jsonl.gz"
LANDMASK_OUT = DATA_DIR / "landmask.bin.gz"
COUNTRIES_OUT = DATA_DIR / "countries.json"

# Land mask grid (must match airportsearch/geo.py).
LM_RES = 0.25
LM_NLAT = int(180 / LM_RES)
LM_NLON = int(360 / LM_RES)
MAX_CITY_ALT_NAMES = 6

COMMERCIAL_TYPES = {"large_airport", "medium_airport", "small_airport"}

# GeoNames "isolanguage" values that are codes/links, not real names to match on.
GEONAMES_SKIP_LANGS = {
    "link", "wkdt", "post", "iata", "icao", "faac", "tcid", "unlc",
    "iso", "abbr", "fr_1793",
}
MAX_ALT_NAMES = 250  # cap per airport to keep the bundle compact (curated names first)

csv.field_size_limit(10_000_000)


def download(url: str, dest: Path, refresh: bool) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not refresh:
        print(f"  using cached {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "airportsearch-build/0.1"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    print(f"  saved {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
    return dest


def parse_alt_names(section: str) -> List[str]:
    """OPTD alt_name_section: ``lang|name|qualifier=lang|name|qualifier=...``."""
    names: List[str] = []
    for entry in (section or "").split("="):
        parts = entry.split("|")
        if len(parts) >= 2 and parts[1].strip():
            names.append(parts[1].strip())
    return names


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_optd(path: Path):
    """Index OpenTravelData points.

    Returns:
      airports: iata -> airport-point dict (active, location_type contains 'A')
      city_alt: city-iata -> [alternate names of the city]
      city_gid: city-iata -> geoname id of the city
    """
    airports: Dict[str, dict] = {}
    city_alt: Dict[str, List[str]] = {}
    city_gid: Dict[str, int] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="^"):
            iata = (row.get("iata_code") or "").strip()
            if not iata:
                continue
            loc = row.get("location_type") or ""
            alt = parse_alt_names(row.get("alt_name_section") or "")
            gid = row.get("geoname_id") or ""

            if "C" in loc:  # city / metropolitan point
                if alt:
                    city_alt.setdefault(iata, []).extend(alt)
                if gid.isdigit():
                    city_gid.setdefault(iata, int(gid))

            if "A" in loc and not (row.get("envelope_id") or "").strip():
                city_list = (row.get("city_code_list") or "").split(",")
                airports[iata] = {
                    "iata": iata.upper(),
                    "icao": (row.get("icao_code") or "").strip() or None,
                    "name": (row.get("name") or "").strip(),
                    "asciiname": (row.get("asciiname") or "").strip(),
                    "city_iata": (city_list[0].strip() or None) if city_list else None,
                    "city_name": (row.get("city_name_list") or "").split("=")[0].strip() or None,
                    "country_code": (row.get("country_code") or "").strip() or None,
                    "country_name": (row.get("country_name") or "").strip() or None,
                    "region": (row.get("adm1_name_utf") or "").strip() or None,
                    "lat": _num(row.get("latitude")),
                    "lon": _num(row.get("longitude")),
                    "page_rank": _num(row.get("page_rank")) or 0.0,
                    "geoname_id": int(gid) if gid.isdigit() else None,
                    "alt_names": alt,
                }
    return airports, city_alt, city_gid


def load_ourairports(path: Path) -> Dict[str, dict]:
    """Commercial airports (scheduled service + sensible type), keyed by IATA."""
    out: Dict[str, dict] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            iata = (row.get("iata_code") or "").strip()
            if len(iata) != 3 or not iata.isalpha():
                continue
            if (row.get("scheduled_service") or "").strip() != "yes":
                continue  # drops military bases, GA strips, unused fields
            if (row.get("type") or "").strip() not in COMMERCIAL_TYPES:
                continue
            keywords = [k.strip() for k in (row.get("keywords") or "").split(",") if k.strip()]
            out[iata.upper()] = {
                "iata": iata.upper(),
                "icao": (row.get("icao_code") or "").strip() or None,
                "name": (row.get("name") or "").strip(),
                "municipality": (row.get("municipality") or "").strip() or None,
                "country_code": (row.get("iso_country") or "").strip() or None,
                "region": (row.get("iso_region") or "").strip() or None,
                "type": (row.get("type") or "").strip() or None,
                "lat": _num(row.get("latitude_deg")),
                "lon": _num(row.get("longitude_deg")),
                "keywords": keywords,
            }
    return out


def load_geonames_altnames(path: Path, wanted: Set[int], country_gids: Set[int]):
    """Stream the (large) GeoNames alt-names zip once.

    Returns ``(place_names, country_names)``: alternate names for ``wanted``
    (airport/city) geoname ids, and separately for ``country_gids`` — for the
    latter abbreviations (UK, USA, UAE) are kept so users can type them.
    """
    result: Dict[int, List[str]] = {}
    countries: Dict[int, List[str]] = {}
    seen: Dict[int, Set[str]] = {}
    with zipfile.ZipFile(path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("alternateNamesV2.txt"))
        with zf.open(member) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 4 or not cols[1].isdigit():
                    continue
                gid = int(cols[1])
                is_place = gid in wanted
                is_country = gid in country_gids
                if not (is_place or is_country):
                    continue
                lang, name = cols[2], cols[3].strip()
                if not name or len(name) > 60:
                    continue
                key = name.casefold()
                if is_place and lang not in GEONAMES_SKIP_LANGS:
                    bucket = seen.setdefault(gid, set())
                    if key not in bucket:
                        bucket.add(key)
                        result.setdefault(gid, []).append(name)
                # Countries: keep language names + abbreviations, drop only pure links.
                if is_country and lang not in {"link", "wkdt", "post", "unlc", "fr_1793"}:
                    countries.setdefault(gid, []).append(name)
    return result, countries


def load_countryinfo(refresh: bool):
    """GeoNames countryInfo.txt -> (rows, gid_to_iso).

    rows: list of (iso2, iso3, name, geoname_id). Comment lines start with '#'.
    """
    path = download(COUNTRYINFO_URL, CACHE / "countryInfo.txt", refresh)
    rows = []
    gid_to_iso: Dict[int, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            c = line.rstrip("\n").split("\t")
            if len(c) < 17 or not c[0].strip():
                continue
            iso2, iso3, name, gid = c[0].strip(), c[1].strip(), c[4].strip(), c[16].strip()
            rows.append((iso2, iso3, name, gid))
            if gid.isdigit():
                gid_to_iso[int(gid)] = iso2
    return rows, gid_to_iso


def build_countries(rows, country_alt: Dict[int, List[str]]) -> int:
    """Write countries.json: normalized country name/alias/code -> ISO2.

    Names come entirely from GeoNames (countryInfo.txt primary names + alternate
    names, including multilingual forms and abbreviations like UK / USA), so no
    country mapping is hard-coded.
    """
    mapping: Dict[str, str] = {}

    def put(alias: str, iso2: str) -> None:
        key = normalize(alias)
        if key and key not in mapping:
            mapping[key] = iso2

    for iso2, iso3, name, gid in rows:
        put(name, iso2)
        put(iso2, iso2)
        # NB: ISO3 codes are intentionally omitted — they are 3 letters and collide
        # with IATA airport codes (FRA=Frankfurt vs France, CAN=Guangzhou vs Canada).
        if gid.isdigit():
            for alt in country_alt.get(int(gid), []):
                put(alt, iso2)

    with open(COUNTRIES_OUT, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, sort_keys=True)
    print(f"  countries: {len(mapping)} name/alias -> ISO2 -> {COUNTRIES_OUT.name} "
          f"({COUNTRIES_OUT.stat().st_size/1e3:.0f} KB)")
    return len(mapping)


def build_cities(cities_file: str, refresh: bool) -> int:
    """Build the city gazetteer from a GeoNames cities dump (e.g. cities15000)."""
    path = download(CITIES_URL.format(cities_file), CACHE / f"{cities_file}.zip", refresh)
    written = 0
    with zipfile.ZipFile(path) as zf:
        member = next(n for n in zf.namelist() if n.endswith(".txt"))
        with zf.open(member) as raw, gzip.open(CITIES_OUT, "wt", encoding="utf-8") as gz:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                c = line.rstrip("\n").split("\t")
                if len(c) < 15:
                    continue
                lat, lon = _num(c[4]), _num(c[5])
                if lat is None or lon is None:
                    continue
                alts = [a.strip() for a in c[3].split(",") if a.strip()][:MAX_CITY_ALT_NAMES]
                rec = {
                    "n": c[1].strip(),
                    "a": c[2].strip() or None,
                    "co": c[8].strip() or None,
                    "la": round(lat, 5),
                    "lo": round(lon, 5),
                    "pop": int(c[14]) if c[14].isdigit() else 0,
                    "al": alts,
                }
                rec = {k: v for k, v in rec.items() if v not in (None, "", [])}
                gz.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    print(f"  city gazetteer: {written} cities -> {CITIES_OUT.name} "
          f"({CITIES_OUT.stat().st_size/1e6:.1f} MB)")
    return written


def build_landmask() -> bool:
    """Generate a coarse packed land/water bitmap using global-land-mask + numpy.

    Build-time only; if the deps are missing we skip it and the runtime falls
    back to country-aware (but not water-aware) nearest-airport ranking.
    """
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:
        print("  land mask SKIPPED (pip install numpy global-land-mask to enable water-awareness)")
        return False
    lats = 90.0 - (np.arange(LM_NLAT) + 0.5) * LM_RES
    lons = -180.0 + (np.arange(LM_NLON) + 0.5) * LM_RES
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    land = globe.is_land(lat_grid, lon_grid)  # (NLAT, NLON) bool, row 0 = north
    packed = np.packbits(land.astype(np.uint8).ravel())  # MSB-first
    with gzip.open(LANDMASK_OUT, "wb") as gz:
        gz.write(packed.tobytes())
    print(f"  land mask: {LM_NLAT}x{LM_NLON} grid -> {LANDMASK_OUT.name} "
          f"({LANDMASK_OUT.stat().st_size/1e3:.0f} KB)")
    return True


def build(coverage: str = "broad", geonames: bool = True,
          min_page_rank: float = 0.0, refresh: bool = False,
          cities_file: str = "cities15000", landmask: bool = True) -> int:
    print("Downloading sources...")
    oa_path = download(OURAIRPORTS_URL, CACHE / "ourairports.csv", refresh)
    optd_path = download(OPTD_URL, CACHE / "optd_por_public.csv", refresh)

    print("Parsing OpenTravelData...")
    optd, city_alt, city_gid = load_optd(optd_path)
    print(f"  {len(optd)} OPTD airport points, {len(city_alt)} cities with alt-names")

    print("Parsing OurAirports (commercial filter)...")
    commercial = load_ourairports(oa_path)
    print(f"  {len(commercial)} commercial airports pass the filter")

    # Assemble the airport set + provenance.
    chosen: Dict[str, dict] = {}
    for iata, oa in commercial.items():
        chosen[iata] = {"oa": oa, "optd": optd.get(iata), "source": "ourairports"}
    added = 0
    if coverage == "broad":
        for iata, op in optd.items():
            if op["page_rank"] <= min_page_rank:
                continue
            if iata in chosen:
                chosen[iata]["source"] = "both"
            else:
                chosen[iata] = {"oa": None, "optd": op, "source": "optd"}
                added += 1
        print(f"  broad coverage adds {added} OPTD-only airports (page_rank > {min_page_rank})")
    print(f"  total airports selected: {len(chosen)}")

    print("Parsing GeoNames countryInfo...")
    country_rows, gid_to_iso = load_countryinfo(refresh)
    country_gids = set(gid_to_iso)
    print(f"  {len(country_rows)} countries")

    # GeoNames enrichment (optional, streamed). Country alternate names are
    # collected in the same pass so the country lookup is data-driven too.
    geo_names: Dict[int, List[str]] = {}
    country_alt: Dict[int, List[str]] = {}
    if geonames:
        wanted: Set[int] = set()
        for entry in chosen.values():
            op = entry["optd"]
            if op and op.get("geoname_id"):
                wanted.add(op["geoname_id"])
            ci = (op or {}).get("city_iata")
            if ci and ci in city_gid:
                wanted.add(city_gid[ci])
        print(f"Downloading GeoNames alt-names for {len(wanted)} features...")
        gn_path = download(GEONAMES_URL, CACHE / "alternateNamesV2.zip", refresh)
        print("  streaming + filtering (this takes a minute)...")
        geo_names, country_alt = load_geonames_altnames(gn_path, wanted, country_gids)
        total_names = sum(len(v) for v in geo_names.values())
        print(f"  kept {total_names} place names + country aliases for {len(country_alt)} countries")

    print("Writing dataset...")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as gz:
        for iata, entry in sorted(chosen.items()):
            oa = entry["oa"] or {}
            op = entry["optd"] or {}
            name = op.get("name") or oa.get("name") or iata
            city_iata = op.get("city_iata")
            city_name = op.get("city_name") or oa.get("municipality")
            gid = op.get("geoname_id")

            # Ordered de-dup: curated OPTD/OurAirports names first (they carry the
            # key native-script names), GeoNames tail last. Preserving this order
            # before the cap keeps multilingual names from being truncated away.
            ordered: List[str] = []
            seen: Set[str] = {name.casefold()}

            def add(names) -> None:
                for n in names:
                    n = (n or "").strip()
                    key = n.casefold()
                    if n and key not in seen:
                        seen.add(key)
                        ordered.append(n)

            # High-signal, small sets first (served city + former/historic names
            # like "Saigon"/"Bombay"), THEN the large multilingual sets. This keeps
            # the cap from ever starving city/historic names of a slot.
            add([city_name] if city_name else [])
            add(oa.get("keywords", []))
            add(city_alt.get(city_iata, []) if city_iata else [])
            add([op.get("asciiname")] if op.get("asciiname") else [])
            add(op.get("alt_names", []))
            add(geo_names.get(gid, []) if gid else [])
            add(geo_names.get(city_gid.get(city_iata), []) if city_iata else [])
            alt_list = ordered[:MAX_ALT_NAMES]

            rec = {
                "ia": iata,
                "ic": op.get("icao") or oa.get("icao"),
                "n": name,
                "ci": city_iata,
                "cy": city_name,
                "co": oa.get("country_code") or op.get("country_code"),
                "cn": op.get("country_name"),
                "rg": op.get("region") or oa.get("region"),
                "la": oa.get("lat") if oa.get("lat") is not None else op.get("lat"),
                "lo": oa.get("lon") if oa.get("lon") is not None else op.get("lon"),
                "ty": oa.get("type"),
                "pr": round(op.get("page_rank", 0.0), 8),
                "gi": gid,
                "src": entry["source"],
                "al": alt_list,
            }
            rec = {k: v for k, v in rec.items() if v not in (None, "", [])}
            gz.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    size_mb = OUT.stat().st_size / 1e6
    print(f"  airports: {written} -> {OUT.name} ({size_mb:.1f} MB)")

    print("Building country lookup...")
    build_countries(country_rows, country_alt)

    if cities_file != "none":
        print("Building city gazetteer (for nearest-airport fallback)...")
        build_cities(cities_file, refresh)
    if landmask:
        print("Building land/water mask (for water-aware nearest airport)...")
        build_landmask()

    print("Done.")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--coverage", choices=["strict", "broad"], default="broad",
                    help="strict = OurAirports only; broad = + OPTD page-ranked (default).")
    gn = ap.add_mutually_exclusive_group()
    gn.add_argument("--geonames", dest="geonames", action="store_true", default=True,
                    help="Enrich with GeoNames alternate names (default).")
    gn.add_argument("--no-geonames", dest="geonames", action="store_false",
                    help="Skip the GeoNames download/enrichment step.")
    ap.add_argument("--min-page-rank", type=float, default=0.0,
                    help="Broad-mode inclusion threshold on OPTD page_rank (default: 0).")
    ap.add_argument("--cities", default="cities15000",
                    help="GeoNames cities dump for the nearest-airport gazetteer "
                         "(cities15000/cities5000/cities1000/cities500), or 'none'.")
    lm = ap.add_mutually_exclusive_group()
    lm.add_argument("--landmask", dest="landmask", action="store_true", default=True,
                    help="Build the land/water mask for water-aware nearest airport (default).")
    lm.add_argument("--no-landmask", dest="landmask", action="store_false",
                    help="Skip the land mask (nearest airport stays country-aware only).")
    ap.add_argument("--refresh", action="store_true", help="Force re-download of sources.")
    args = ap.parse_args()
    try:
        build(coverage=args.coverage, geonames=args.geonames,
              min_page_rank=args.min_page_rank, refresh=args.refresh,
              cities_file=args.cities, landmask=args.landmask)
    except Exception as exc:  # pragma: no cover
        print(f"Build failed: {exc}", file=sys.stderr)
        raise

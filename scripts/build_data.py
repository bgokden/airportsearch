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
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OPTD_URL = (
    "https://raw.githubusercontent.com/opentraveldata/opentraveldata/"
    "master/opentraveldata/optd_por_public.csv"
)
GEONAMES_URL = "https://download.geonames.org/export/dump/alternateNamesV2.zip"

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build_cache"
OUT = ROOT / "src" / "airportsearch" / "data" / "airports.jsonl.gz"

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


def load_geonames_altnames(path: Path, wanted: Set[int]) -> Dict[int, List[str]]:
    """Stream the (large) GeoNames alt-names zip, keeping only ``wanted`` geoname ids."""
    result: Dict[int, List[str]] = {}
    seen: Dict[int, Set[str]] = {}
    with zipfile.ZipFile(path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("alternateNamesV2.txt"))
        with zf.open(member) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 4:
                    continue
                if not cols[1].isdigit():
                    continue
                gid = int(cols[1])
                if gid not in wanted:
                    continue
                lang, name = cols[2], cols[3].strip()
                if not name or len(name) > 60 or lang in GEONAMES_SKIP_LANGS:
                    continue
                key = name.casefold()
                bucket = seen.setdefault(gid, set())
                if key in bucket:
                    continue
                bucket.add(key)
                result.setdefault(gid, []).append(name)
    return result


def build(coverage: str = "broad", geonames: bool = True,
          min_page_rank: float = 0.0, refresh: bool = False) -> int:
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

    # GeoNames enrichment (optional, streamed).
    geo_names: Dict[int, List[str]] = {}
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
        geo_names = load_geonames_altnames(gn_path, wanted)
        total_names = sum(len(v) for v in geo_names.values())
        print(f"  kept {total_names} names across {len(geo_names)} features")

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
    print(f"Done: {written} airports -> {OUT.relative_to(ROOT)} ({size_mb:.1f} MB)")
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
    ap.add_argument("--refresh", action="store_true", help="Force re-download of sources.")
    args = ap.parse_args()
    try:
        build(coverage=args.coverage, geonames=args.geonames,
              min_page_rank=args.min_page_rank, refresh=args.refresh)
    except Exception as exc:  # pragma: no cover
        print(f"Build failed: {exc}", file=sys.stderr)
        raise

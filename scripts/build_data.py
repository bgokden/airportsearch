#!/usr/bin/env python3
"""Build the bundled airport dataset for airportsearch.

Sources (all open data):
  * OurAirports airports.csv  -> commercial filtering (type + scheduled_service)
                                 + municipality, keywords (aliases).
  * OpenTravelData optd_por_public.csv -> city/metropolitan IATA codes,
                                 page_rank (popularity), and multilingual
                                 alternate names.

Output:
  src/airportsearch/data/airports.jsonl.gz  (one compact JSON record per line)

Run:  python scripts/build_data.py           # uses .build_cache/ for downloads
      python scripts/build_data.py --refresh # force re-download of sources
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OPTD_URL = (
    "https://raw.githubusercontent.com/opentraveldata/opentraveldata/"
    "master/opentraveldata/optd_por_public.csv"
)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build_cache"
OUT = ROOT / "src" / "airportsearch" / "data" / "airports.jsonl.gz"

# OurAirports facility types we consider "commercial" candidates.
COMMERCIAL_TYPES = {"large_airport", "medium_airport", "small_airport"}

# csv field-size limit — OPTD alt_name_section rows can be large.
csv.field_size_limit(10_000_000)


def download(url: str, dest: Path, refresh: bool) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not refresh:
        print(f"  using cached {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "airportsearch-build/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    print(f"  saved {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
    return dest


def parse_alt_names(section: str) -> List[str]:
    """OPTD alt_name_section: ``lang|name|qualifier=lang|name|qualifier=...``."""
    names: List[str] = []
    if not section:
        return names
    for entry in section.split("="):
        parts = entry.split("|")
        if len(parts) >= 2 and parts[1].strip():
            names.append(parts[1].strip())
    return names


def load_optd(path: Path):
    """Return (airport_rows_by_iata, city_altnames_by_iata).

    OPTD may hold several rows per IATA code (city vs. airport). We keep the
    airport-typed active row per code, and separately index city alternate
    names so an airport can inherit its metropolitan area's names.
    """
    airports: Dict[str, dict] = {}
    city_alt: Dict[str, List[str]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="^")
        for row in reader:
            iata = (row.get("iata_code") or "").strip()
            if not iata:
                continue
            loc = (row.get("location_type") or "")
            envelope = (row.get("envelope_id") or "").strip()
            alt = parse_alt_names(row.get("alt_name_section") or "")

            if "C" in loc:  # city / metropolitan point
                if alt:
                    city_alt.setdefault(iata, []).extend(alt)

            if "A" in loc and not envelope:  # active airport point
                try:
                    pr = float(row.get("page_rank") or 0.0)
                except ValueError:
                    pr = 0.0
                city_list = (row.get("city_code_list") or "").split(",")
                airports[iata] = {
                    "icao": (row.get("icao_code") or "").strip() or None,
                    "name": (row.get("name") or "").strip(),
                    "asciiname": (row.get("asciiname") or "").strip(),
                    "city_iata": (city_list[0].strip() or None) if city_list else None,
                    "city_name": (row.get("city_name_list") or "").split("=")[0].strip() or None,
                    "country_code": (row.get("country_code") or "").strip() or None,
                    "country_name": (row.get("country_name") or "").strip() or None,
                    "region": (row.get("adm1_name_utf") or "").strip() or None,
                    "page_rank": pr,
                    "alt_names": alt,
                }
    return airports, city_alt


def load_ourairports(path: Path) -> List[dict]:
    """Commercial airports only: scheduled service + reasonable facility type."""
    out: List[dict] = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            iata = (row.get("iata_code") or "").strip()
            if len(iata) != 3 or not iata.isalpha():
                continue
            if (row.get("scheduled_service") or "").strip() != "yes":
                continue  # drops military bases, GA strips, unused fields
            if (row.get("type") or "").strip() not in COMMERCIAL_TYPES:
                continue

            def num(v: str) -> Optional[float]:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            keywords = [k.strip() for k in (row.get("keywords") or "").split(",") if k.strip()]
            out.append(
                {
                    "iata": iata.upper(),
                    "icao": (row.get("icao_code") or "").strip() or None,
                    "name": (row.get("name") or "").strip(),
                    "municipality": (row.get("municipality") or "").strip() or None,
                    "country_code": (row.get("iso_country") or "").strip() or None,
                    "region": (row.get("iso_region") or "").strip() or None,
                    "type": (row.get("type") or "").strip() or None,
                    "lat": num(row.get("latitude_deg")),
                    "lon": num(row.get("longitude_deg")),
                    "keywords": keywords,
                }
            )
    return out


def build(refresh: bool = False) -> int:
    print("Downloading sources...")
    oa_path = download(OURAIRPORTS_URL, CACHE / "ourairports.csv", refresh)
    optd_path = download(OPTD_URL, CACHE / "optd_por_public.csv", refresh)

    print("Parsing OpenTravelData...")
    optd_airports, city_alt = load_optd(optd_path)
    print(f"  {len(optd_airports)} OPTD airport points, {len(city_alt)} cities with alt-names")

    print("Parsing OurAirports (commercial filter)...")
    commercial = load_ourairports(oa_path)
    print(f"  {len(commercial)} commercial airports pass the filter")

    print("Joining + writing dataset...")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as gz:
        for oa in commercial:
            iata = oa["iata"]
            optd = optd_airports.get(iata, {})

            alt = set()
            alt.update(optd.get("alt_names", []))
            if optd.get("asciiname"):
                alt.add(optd["asciiname"])
            alt.update(oa["keywords"])
            city_iata = optd.get("city_iata")
            city_name = optd.get("city_name") or oa["municipality"]
            if city_name:
                alt.add(city_name)
            if city_iata:
                alt.update(city_alt.get(city_iata, []))
            name = optd.get("name") or oa["name"]
            alt.discard(name)
            alt = sorted(a for a in alt if a)

            rec = {
                "ia": iata,
                "ic": optd.get("icao") or oa["icao"],
                "n": name,
                "ci": city_iata,
                "cy": city_name,
                "co": oa["country_code"] or optd.get("country_code"),
                "cn": optd.get("country_name"),
                "rg": optd.get("region") or oa["region"],
                "la": oa["lat"],
                "lo": oa["lon"],
                "ty": oa["type"],
                "pr": round(optd.get("page_rank", 0.0), 8),
                "al": alt,
            }
            # drop null/empty values to keep the file compact
            rec = {k: v for k, v in rec.items() if v not in (None, "", [])}
            gz.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    size_mb = OUT.stat().st_size / 1e6
    print(f"Done: {written} airports -> {OUT.relative_to(ROOT)} ({size_mb:.1f} MB)")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="Force re-download of source files.")
    args = ap.parse_args()
    try:
        build(refresh=args.refresh)
    except Exception as exc:  # pragma: no cover
        print(f"Build failed: {exc}", file=sys.stderr)
        raise

"""Command-line interface: ``airportsearch "frankfurt" -k 5``."""
from __future__ import annotations

import argparse
import sys

from . import __version__, search


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="airportsearch",
        description="Fuzzy-search commercial airports by name, alias, IATA, city or country.",
    )
    parser.add_argument("query", nargs="+", help="Search text (name / alias / IATA / city).")
    parser.add_argument("-k", type=int, default=5, help="Number of results (default: 5).")
    parser.add_argument("--cutoff", type=float, default=40.0, help="Minimum fuzzy score (default: 40).")
    parser.add_argument("--version", action="version", version=f"airportsearch {__version__}")
    args = parser.parse_args(argv)

    query = " ".join(args.query)
    results = search(query, k=args.k, score_cutoff=args.cutoff)
    if not results:
        print(f"No airports found for {query!r}", file=sys.stderr)
        return 1

    if results[0].via == "nearest":
        print(f"No airport named {query!r}; nearest to {results[0].matched}:")
    for r in results:
        a = r.airport
        city_code = f"/{a.city_iata}" if a.city_iata and a.city_iata != a.iata else ""
        tail = f"  [{r.distance_km:.0f} km]" if r.distance_km is not None else ""
        city = f"{a.city_name} " if a.city_name else ""
        print(f"{r.score:6.1f}  {a.iata}{city_code:>5}  {a.name}  —  {city}({a.country_code}){tail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

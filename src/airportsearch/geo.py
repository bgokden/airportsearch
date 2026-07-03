"""Geographic helpers: great-circle distance and a bundled land/water mask.

The land mask lets the nearest-airport fallback avoid returning airports that
are close as-the-crow-flies but across a sea or bay (i.e. not reachable by road).
It is a coarse packed bitmap generated at build time; see ``scripts/build_data.py``.
"""
from __future__ import annotations

import gzip
import math
from functools import lru_cache
from importlib import resources
from typing import Optional

_DATA_PACKAGE = "airportsearch.data"
_LANDMASK_FILE = "landmask.bin.gz"

# Grid geometry (must match the build step). Row 0 is the northernmost band.
LM_RES = 0.25
LM_NLAT = int(180 / LM_RES)  # 720
LM_NLON = int(360 / LM_RES)  # 1440

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class LandMask:
    """Coarse global land/water bitmap; ``True`` = land at a coordinate."""

    def __init__(self, data: bytes):
        self._data = data

    def is_land(self, lat: float, lon: float) -> bool:
        row = int((90.0 - lat) / LM_RES)
        col = int((lon + 180.0) / LM_RES)
        row = min(max(row, 0), LM_NLAT - 1)
        col = min(max(col, 0), LM_NLON - 1)
        idx = row * LM_NLON + col
        byte = self._data[idx >> 3]
        return bool((byte >> (7 - (idx & 7))) & 1)  # packbits is MSB-first

    def water_fraction(self, lat1: float, lon1: float, lat2: float, lon2: float,
                       samples: int = 12) -> float:
        """Fraction of interior points on the straight path that fall over water.

        Linear interpolation in lat/lon is accurate enough for regional distances
        and keeps this dependency-free. Endpoints are excluded (an airport or city
        may sit right on a coastline).
        """
        water = 0
        n = 0
        for i in range(1, samples):
            t = i / samples
            lat = lat1 + (lat2 - lat1) * t
            lon = lon1 + (lon2 - lon1) * t
            n += 1
            if not self.is_land(lat, lon):
                water += 1
        return water / n if n else 0.0


@lru_cache(maxsize=1)
def get_landmask() -> Optional[LandMask]:
    """Load the bundled land mask, or ``None`` if it was not built into the package."""
    try:
        res = resources.files(_DATA_PACKAGE).joinpath(_LANDMASK_FILE)
        with res.open("rb") as fh:
            with gzip.open(fh, "rb") as gz:
                return LandMask(gz.read())
    except (FileNotFoundError, OSError):
        return None

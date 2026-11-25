"""City open-data adapters: assessor, permits and 311 for a ~200 m box.

    from usa.adapters import ADAPTERS, for_point
    a = for_point(-104.9395, 39.7575)       # -> usa.adapters.denver
    data = a.fetch((w, s, e, n))           # {"assessor": [...], "permits": [...], "cases311": [...], "status": {...}}

Each module defines NAME, STATE, COUNTY_FIPS, BBOX (rough city/county extent, lon/lat), LICENCES and fetch(bbox).
cities.json lists a verified test point per city.
"""
import importlib
import json
from pathlib import Path

SLUGS = ["minneapolis", "saint_paul", "dallas", "austin", "denver", "boulder", "columbus", "cincinnati", "tulsa",
         "kansas_city", "huntsville", "birmingham", "maricopa"]
ADAPTERS = {s: importlib.import_module(f"{__name__}.{s}") for s in SLUGS}
CITIES = json.loads((Path(__file__).with_name("cities.json")).read_text(encoding="utf-8"))


def for_point(lon, lat):
    """Adapter whose city box contains the point; when boxes overlap (Minneapolis/Saint Paul, Denver/Boulder)
    the one whose box centre is nearest wins. None if no city covers the point."""
    hits = []
    for m in ADAPTERS.values():
        w, s, e, n = m.BBOX
        if w <= lon <= e and s <= lat <= n:
            hits.append((((lon - (w + e) / 2) ** 2 + (lat - (s + n) / 2) ** 2), m))
    return min(hits, key=lambda t: t[0])[1] if hits else None

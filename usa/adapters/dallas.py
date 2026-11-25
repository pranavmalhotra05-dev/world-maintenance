"""Dallas, TX (Dallas County, FIPS 48113).

assessor  REMOVED. Dallas Central Appraisal District (DCAD) files say "All Rights Reserved" and give no licence
          (red in usa/research/licence_registry_local.json). Nothing is read from DCAD or data/dallas/homes.parquet.
permits   REMOVED. City of Dallas Open Data "Building Permits" e7gq-4sah has no licence set (grey).
311       City of Dallas Open Data "311 Service Requests October 1, 2020 to Present" d7e7-envw (Socrata, ODC-PDDL).
          lat_location is a "(lat,lon)" text column, so rows are selected by the text prefix of the 0.01-degree
          cells that cover the box and then filtered to the box.
"""
import math
import re

from ._common import case_rec, date, in_bbox, removed, run, socrata

NAME, STATE, COUNTY_FIPS = "Dallas", "TX", "48113"
BBOX = (-97.00, 32.61, -96.55, 33.03)  # City of Dallas
LICENCES = {"assessor": "removed (DCAD: 'All Rights Reserved', no licence - red)",
            "permits": "removed (Dallas Open Data e7gq-4sah: no licence set - grey)",
            "311": "ODC-PDDL (public domain, Dallas Open Data d7e7-envw)"}
DOMAIN = "www.dallasopendata.com"


def _prefixes(bbox):
    """Text prefixes '(32.72' / '-96.88' of the 0.01-degree cells covering the box (truncation toward zero)."""
    w, s, e, n = bbox
    trunc = lambda v: math.trunc(round(v * 100, 6)) / 100
    lats = sorted({f"{trunc(s + i * 0.01):.2f}" for i in range(int((n - s) / 0.01) + 2) if s + i * 0.01 <= n + 0.01} | {f"{trunc(n):.2f}"})
    lons = sorted({f"{trunc(w + i * 0.01):.2f}" for i in range(int((e - w) / 0.01) + 2) if w + i * 0.01 <= e + 0.01} | {f"{trunc(e):.2f}"})
    return lats, lons


def cases311(bbox):
    lats, lons = _prefixes(bbox)
    w = " OR ".join(f"lat_location like '({la}%,{lo}%'" for la in lats for lo in lons)   # one scan for all cells
    out = []
    for c in socrata(DOMAIN, "d7e7-envw", where=w, limit=50000,
                     select="service_request_type,department,status,created_date,lat_location"):
        m = re.match(r"\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)", c.get("lat_location") or "")
        if not m:
            continue
        lat, lon = float(m.group(1)), float(m.group(2))
        if in_bbox(lon, lat, bbox):
            out.append(case_rec(lon=lon, lat=lat, date=date(c.get("created_date")), type=c.get("department"),
                                title=c.get("service_request_type"), status=c.get("status")))
    return out


def fetch(bbox):
    return run({"assessor": removed("DCAD appraisal data is 'All Rights Reserved' with no licence (red)"),
                "permits": removed("Dallas Open Data building permits e7gq-4sah have no licence set (grey)"),
                "311": cases311}, bbox)

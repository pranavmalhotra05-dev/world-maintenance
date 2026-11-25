"""Birmingham, AL (Jefferson County, FIPS 01073).

assessor  REMOVED. Jefferson County Board of Equalization parcels (jccgis.jccal.org BOE/JCParcelsWMS): disclaimer /
          "no use limitations" only, no licence grant (grey in usa/research/licence_registry_local.json).
permits   data.birminghamal.gov CKAN "Building Permits and Valuations" (CC BY): case no, address, issue date only,
          2015-07 .. 2017-06 (the city stopped publishing). No coordinates: rows whose street is one of the
          TIGERweb road names crossing the box are geocoded with the US Census Geocoder (public domain) and kept
          when they fall in the box. The Jefferson County parcels are no longer used for placement.
311       not open in usable form: CKAN "311 Cases - Yearly" (2015-2019) has no location (and a citizen-name column).
"""
import re
from functools import lru_cache

from ._common import S, census_geocode, date, in_bbox, permit_rec, removed, run, tiger_streets

NAME, STATE, COUNTY_FIPS = "Birmingham", "AL", "01073"
BBOX = (-87.00, 33.38, -86.62, 33.68)
LICENCES = {"assessor": "removed (Jefferson County AL BOE parcels: disclaimer only, no licence - grey)",
            "permits": "CC BY (data.birminghamal.gov, Creative Commons Attribution); placed with the US Census Geocoder (public domain)",
            "311": "not open"}
CKAN = "https://data.birminghamal.gov/api/3/action/datastore_search"
PERMIT_RES = ["7f72c1ef-5381-4e00-95b0-f11eaac3a9c5", "66ff50a9-bb6c-4c71-ad95-4055723f23f4", "359e6339-a6f2-47ce-81aa-db9abf085287"]
SUF = {"STREET": "ST", "AVENUE": "AVE", "DRIVE": "DR", "ROAD": "RD", "LANE": "LN", "BOULEVARD": "BLVD", "COURT": "CT",
       "CIRCLE": "CIR", "PLACE": "PL", "PARKWAY": "PKWY", "PKY": "PKWY", "TERRACE": "TER", "TRAIL": "TRL",
       "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


def norm(a):
    a = re.sub(r"\b\d{5}(-\d{4})?\s*$", "", str(a or "").upper().strip())
    a = re.sub(r"[^A-Z0-9 ]", " ", a)
    return " ".join(SUF.get(w, w) for w in a.split())


def _street(a):
    """'2862 STRATFORD RD' -> 'STRATFORD RD' (house number dropped)."""
    w = norm(a).split()
    return " ".join(w[1:]) if len(w) > 1 and w[0][:1].isdigit() else None


@lru_cache(maxsize=1)
def _all_permits():
    rows = []
    for rid in PERMIT_RES:
        off = 0
        while True:
            j = S.get(CKAN, params={"resource_id": rid, "limit": 5000, "offset": off}, timeout=90).json()["result"]
            rows += j["records"]
            off += len(j["records"])
            if not j["records"] or off >= j["total"]:
                break
    return tuple((r.get("Case No"), r.get("Issued"), r.get("Address")) for r in rows)


def permits(bbox):
    streets = {norm(n) for n in tiger_streets(bbox)}
    cand = []
    for case, issued, addr in _all_permits():
        if not addr or _street(addr) not in streets:
            continue
        m = re.search(r"\b(\d{5})\s*$", addr.strip())
        street = re.sub(r"\s+\d{5}(-\d{4})?\s*$", "", addr.strip())
        street = re.sub(r"\s+", " ", street)
        cand.append((case, issued, street, m.group(1) if m else ""))
    geo = census_geocode([(st, "Birmingham", "AL", z) for _, _, st, z in cand])
    out = []
    for case, issued, street, z in cand:
        ll = geo.get((street, "Birmingham", "AL", z))
        if ll and in_bbox(ll[0], ll[1], bbox):
            out.append(permit_rec(lon=ll[0], lat=ll[1], address=street, date=date(issued), type="Building", work=None,
                                  status="issued", value=None, comments=case))
    return out


def fetch(bbox):
    return run({"assessor": removed("Jefferson County AL BOE parcels carry only a disclaimer, no licence (grey)"),
                "permits": permits, "311": None}, bbox)

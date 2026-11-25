"""Maricopa County, AZ (FIPS 04013): Phoenix, Scottsdale, Paradise Valley and the rest of the county.

assessor  REMOVED. Maricopa County Assessor data (parcels, pool / pool_area, limited value): commercial use must go through
          the A.R.S. 39-121.03 commercial-purpose request (red in usa/research/licence_registry_local.json).
          The Assessor's parcel service is no longer queried at all.
permits   REMOVED. City of Phoenix Planning_Permit layer 1: no dataset licence, terms page behind a bot check (grey).
          (City of Scottsdale permits were never used: their licence excludes commercial use.)
311       City of Phoenix NSD Property Maintenance complaints (code enforcement, CC BY via phoenixopendata.com).
          The layer is a table with no geometry. Rows on the TIGERweb streets that cross the box (US Census, public
          domain) are geocoded with the US Census Geocoder (public domain) and kept when they fall in the box.
          Date is the year embedded in the case number (PEFyyyy-nnnnn). Phoenix only.
"""
import re

from ._common import _get, case_rec, census_geocode, in_bbox, removed, run, tiger_streets

NAME, STATE, COUNTY_FIPS = "Maricopa", "AZ", "04013"
BBOX = (-112.55, 33.20, -111.58, 33.95)   # Phoenix metro core: Phoenix, Scottsdale, Paradise Valley, Tempe, Mesa, Glendale
LICENCES = {
    "assessor": "removed (Maricopa County Assessor: commercial use only via A.R.S. 39-121.03 request - red)",
    "permits": "removed (City of Phoenix Planning_Permit layer 1: no dataset licence - grey)",
    "311": "CC BY (City of Phoenix NSD Property Maintenance, phoenixopendata.com); placed with the US Census Geocoder (public domain) - Phoenix only",
}
NSD = "https://maps.phoenix.gov/pub/rest/services/Public/NSD_Property_Maintenance/MapServer/0"
_ADDR = re.compile(r"^(.*?)\s+PHOENIX\s+(\d{5})(-\d{4})?\s*$")


def _nsd(where):
    """NSD table query (f=json; the table rejects resultOffset/resultRecordCount)."""
    j = _get(NSD + "/query", {"where": where, "outFields": "CSM_CASENO,CSM_ADDRESS,CSM_STATUS,NOTES",
                              "returnGeometry": "false", "f": "json"})
    return [f.get("attributes") or {} for f in j.get("features", [])]


def cases311(bbox):
    streets = sorted(tiger_streets(bbox))
    rows, seen = [], set()
    for i in range(0, len(streets), 15):
        w = " OR ".join("CSM_ADDRESS LIKE '% " + s.replace("'", "''") + " %'" for s in streets[i:i + 15])
        for a in _nsd(w):
            k = (a.get("CSM_CASENO"), a.get("CSM_ADDRESS"))
            if k not in seen:   # the table repeats each case once per inspection
                seen.add(k); rows.append(a)
    parsed = []
    for a in rows:
        m = _ADDR.match(str(a.get("CSM_ADDRESS") or "").upper().strip())
        if m:
            parsed.append((a, re.sub(r"\s+", " ", m.group(1)), m.group(2)))
    geo = census_geocode([(st, "Phoenix", "AZ", z) for _, st, z in parsed])
    out = []
    for a, st, z in parsed:
        ll = geo.get((st, "Phoenix", "AZ", z))
        if not ll or not in_bbox(ll[0], ll[1], bbox):
            continue
        m = re.search(r"(\d{4})", str(a.get("CSM_CASENO") or ""))
        note = re.sub(r"^(It has been (reported|observed) that )", "", str(a.get("NOTES") or ""), flags=re.I)
        out.append(case_rec(lon=ll[0], lat=ll[1], date=m.group(1) if m else None, type="Property maintenance (NSD)",
                            title=note[:140] or None, status=a.get("CSM_STATUS")))
    return out


def fetch(bbox):
    return run({"assessor": removed("Maricopa County Assessor data needs an A.R.S. 39-121.03 commercial-purpose request (red)"),
                "permits": removed("City of Phoenix Planning_Permit layer 1 has no dataset licence (grey)"),
                "311": cases311}, bbox)

"""Pull San Francisco open data for the study area (DataSF / Socrata API, no key needed).

Licence (checked 2026-11-24): all three datasets carry licenseId "PDDL" (Open Data Commons Public Domain
Dedication and License 1.0) in data.sfgov.org/api/views/<id>.json, and the DataSF Terms of Use section IX
make PDDL v1.0 the default. Same terms, section VI: do not re-identify the data or "contact individuals
whose information is represented by the Data".

- Building footprints (ynuv-fyni): polygon outline of every building.
- Assessor secured roll (wv5m-vpq2): building facts only (address, use, units, stories, year built,
  construction type). No owner names, homeowner exemption, sale dates or values (see section VI above).
- Building permits (i98e-djp9): roof / solar related permits, for each building's roof age.
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
lon0, lat0, lon1, lat1 = CFG["bbox_lonlat"]
API = "https://data.sfgov.org/resource"
S = requests.Session()


def soql(dataset, where, select=None, limit=50000):
    params = {"$where": where, "$limit": limit}
    if select:
        params["$select"] = select
    r = S.get(f"{API}/{dataset}.json", params=params, timeout=180)
    r.raise_for_status()
    return r.json()


# Socrata within_box(field, north_lat, west_lon, south_lat, east_lon)
box = f"{lat1}, {lon0}, {lat0}, {lon1}"

footprints = soql("ynuv-fyni", f"within_box(shape, {box})",
                  select="sf16_bldgid,shape,gnd_min_m,hgt_median_m,peak_1st_m")
print("footprints:", len(footprints))

roll = soql("wv5m-vpq2", f"closed_roll_year = '2025' AND within_box(the_geom, {box})",
            select=("parcel_number,block,lot,property_location,use_definition,"
                    "property_class_code_definition,year_property_built,number_of_units,"
                    "number_of_stories,property_area,lot_area,construction_type,the_geom"))
print("assessor parcels:", len(roll))

roof_words = ["roof", "solar", "photovoltaic", " pv ", "skylight", "gutter"]
like = " OR ".join(f"lower(description) like '%{w.strip()}%'" for w in roof_words)
permits = soql("i98e-djp9", f"within_box(location, {box}) AND ({like})",
               select=("permit_number,block,lot,street_number,street_name,description,"
                       "status,filed_date,issued_date,completed_date,estimated_cost,location"))
print("roof/solar permits:", len(permits))

for name, rows in [("footprints", footprints), ("assessor", roll), ("permits", permits)]:
    (ROOT / "data" / f"{name}.json").write_text(json.dumps(rows))

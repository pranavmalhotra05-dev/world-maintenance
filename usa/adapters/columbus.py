"""Columbus, OH (Franklin County, FIPS 39049).

assessor  removed: Franklin County Auditor parcels (owner decision - no clear legal standing: the public-domain
          statement is only on the Auditor's map item, the data service itself shows only a disclaimer). Houses get no
          h.assessor; money falls back to the labelled ACS census-tract median.
permits   City of Columbus Building Permits (Accela, CC0). Applicant business/full name not kept.
311       Columbus does not publish raw 311 requests as open data; City of Columbus Code Enforcement Cases
          (Accela enforcement, CC0) are returned instead (type = "Code enforcement: <type>").
"""
from ._common import arcgis, case_rec, date, permit_rec, pos, removed, run

NAME, STATE, COUNTY_FIPS = "Columbus", "OH", "39049"
BBOX = (-83.21, 39.81, -82.77, 40.16)
LICENCES = {"assessor": "removed (Franklin County Auditor parcels: owner - no clear legal standing)",
            "permits": "CC0 (City of Columbus)", "311": "CC0 (City of Columbus code enforcement cases; no open 311 feed)"}
PERMITS = "https://services1.arcgis.com/9yy6msODkIBzkUXU/arcgis/rest/services/Building_Permits/FeatureServer/0"
CODE = "https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/23"


def permits(bbox):
    rows = arcgis(PERMITS, bbox, fields="SITE_ADDRESS,ISSUED_DT,B1_PER_TYPE,B1_PER_SUB_TYPE,B1_PER_CATEGORY,GENERAL_TYPE,PERMIT_STATUS,G3_VALUE_TTL,VALUE_DESC")
    out = []
    for p, lon, lat in rows:
        work = " / ".join(x for x in (p.get("B1_PER_SUB_TYPE"), p.get("B1_PER_CATEGORY")) if x and x != "NA") or None
        out.append(permit_rec(lon=lon, lat=lat, address=p.get("SITE_ADDRESS"), date=date(p.get("ISSUED_DT")),
                              type=p.get("GENERAL_TYPE") or p.get("B1_PER_TYPE"), work=work, status=p.get("PERMIT_STATUS"),
                              value=pos(p.get("G3_VALUE_TTL")), comments=p.get("VALUE_DESC")))
    return out


def cases311(bbox):
    rows = arcgis(CODE, bbox, fields="B1_PER_TYPE,B1_PER_SUB_TYPE,B1_FILE_DD,B1_APPL_STATUS,INSP_LAST_RESULT")
    return [case_rec(lon=lon, lat=lat, date=date(p.get("B1_FILE_DD")), type=f"Code enforcement: {p.get('B1_PER_TYPE')}",
                     title=p.get("B1_PER_SUB_TYPE"), status=p.get("B1_APPL_STATUS")) for p, lon, lat in rows]


def fetch(bbox):
    return run({"assessor": removed("Franklin County Auditor parcels - owner: no clear legal standing (public-domain note only on the map item, service shows a disclaimer)"), "permits": permits, "311": cases311}, bbox)

"""Minneapolis, MN (Hennepin County, FIPS 27053) - City of Minneapolis Open Data (CC0 waiver on every layer).

Same endpoints as mpls/osint.py:
assessor  Assessing_Department_Parcel_Data_2026 (table, no geometry; has X/Y in NAD83 HARN Adj MN Hennepin feet,
          ESRI:103734 -> queried by an X/Y box). Roof, exterior wall, garage stalls, beds, baths, values.
          OWNERNAME is not requested. No sale fields in this table.
permits   CCS_Permits (points). applicantName / fullName / applicantAddress are not requested.
311       Public_311_<year> point layers, YEARS_311 most recent years.
"""
from datetime import date as _d

from pyproj import Transformer

from ._common import arcgis, assessor_rec, case_rec, date, num, permit_rec, pos, run, year

NAME, STATE, COUNTY_FIPS = "Minneapolis", "MN", "27053"
BBOX = (-93.33, 44.89, -93.19, 45.06)
LICENCES = {"assessor": "CC0 (City of Minneapolis Open Data waiver)", "permits": "CC0 (City of Minneapolis Open Data waiver)",
            "311": "CC0 (City of Minneapolis Open Data waiver)"}
B = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
ASSESS = B + "Assessing_Department_Parcel_Data_2026/FeatureServer/0"
PERMITS = B + "CCS_Permits/FeatureServer/0"
YEARS_311 = 6
_STATUS = {0: "Closed", 1: "Open"}  # inferred: 0 rows all have CLOSEDDATETIME, 1 rows none
_to_ft = Transformer.from_crs(4326, "ESRI:103734", always_xy=True)
_to_ll = Transformer.from_crs("ESRI:103734", 4326, always_xy=True)


def assessor(bbox):
    w, s, e, n = bbox
    xs, ys = _to_ft.transform([w, e, w, e], [s, s, n, n])
    where = f"X >= {min(xs)} AND X <= {max(xs)} AND Y >= {min(ys)} AND Y <= {max(ys)}"
    f = ("X,Y,ADDRESSFORMATTED,ZONING,PROPERTYTYPE,LANDVALUE,BUILDINGVALUE,TOTALVALUE,BUILDINGUSE,YEARBUILT,ABOVEGROUNDAREA,"
         "STORIES,EXTERIORWALL,ROOF,GARAGESTALLS,TOTALBEDROOMS,TOTALBATHROOMS")
    out = []
    for a, _, _ in arcgis(ASSESS, where=where, fields=f, geom=False):
        if a.get("X") is None:
            continue
        lon, lat = _to_ll.transform(a["X"], a["Y"])
        g = pos(a.get("GARAGESTALLS"))
        out.append(assessor_rec(
            lon=lon, lat=lat, address=a.get("ADDRESSFORMATTED"), year_built=year(a.get("YEARBUILT")), stories=pos(a.get("STORIES")),
            living_area_sqft=pos(a.get("ABOVEGROUNDAREA")), roof=a.get("ROOF"), exterior_wall=a.get("EXTERIORWALL"),
            garage=f"{g} stalls" if g else None, bedrooms=pos(a.get("TOTALBEDROOMS")), baths=pos(a.get("TOTALBATHROOMS")),
            land_value=num(a.get("LANDVALUE")), building_value=num(a.get("BUILDINGVALUE")), total_value=num(a.get("TOTALVALUE")),
            zoning=a.get("ZONING"), property_type=a.get("PROPERTYTYPE") or a.get("BUILDINGUSE")))
    return out


def permits(bbox):
    rows = arcgis(PERMITS, bbox, fields="Display,permitType,workType,status,issueDate,permit_Value,comments,occupancyType")
    return [permit_rec(lon=lon, lat=lat, address=p.get("Display"), date=date(p.get("issueDate")), type=p.get("permitType"),
                       work=p.get("workType"), status=p.get("status"), value=num(p.get("permit_Value")), comments=p.get("comments"))
            for p, lon, lat in rows]


def cases311(bbox):
    out, y1 = [], _d.today().year
    for yr in range(y1 - YEARS_311 + 1, y1 + 1):
        try:
            rows = arcgis(f"{B}Public_311_{yr}/FeatureServer/0", bbox, fields="TYPENAME,TITLE,OPENEDDATETIME,CASESTATUS")
        except Exception:
            continue  # year layer not published (yet)
        out += [case_rec(lon=lon, lat=lat, date=date(p.get("OPENEDDATETIME")), type=p.get("TYPENAME"), title=p.get("TITLE"),
                         status=_STATUS.get(p.get("CASESTATUS"), p.get("CASESTATUS"))) for p, lon, lat in rows]
    return out


def fetch(bbox):
    return run({"assessor": assessor, "permits": permits, "311": cases311}, bbox)

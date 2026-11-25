"""Kansas City, MO (Jackson County, FIPS 29095; city also spans Clay/Platte/Cass).

assessor  REMOVED. City of Kansas City GIS Parcels / Zoning (mapd.kcmo.org) and the land-use code table 83fx-3sa2:
          "For Public/Internal Use" disclaimer, no licence, data.kcmo.org terms reserve the city's IP (grey).
permits   REMOVED. data.kcmo.org "Permits - CPD Dataset" ntw8-aacc: no licence (grey); the public-domain status-change
          dataset 6h9j-mu65 has no address/type, so it cannot replace it.
311       data.kcmo.org 311 Call Center Reported Issues (d4px-6rwg, 2021 -> today, Public Domain)
          + 311 Call Center Service Requests 2007 - March 2021 (7at3-sxhp, Public Domain).
"""
from ._common import case_rec, date, latlon_where, num, removed, run, socrata, soql_box

NAME, STATE, COUNTY_FIPS = "Kansas City", "MO", "29095"
BBOX = (-94.77, 38.82, -94.38, 39.36)
LICENCES = {"assessor": "removed (City of Kansas City GIS parcels/zoning + land-use codes: no licence, IP reserved - grey)",
            "permits": "removed (data.kcmo.org CPD permits ntw8-aacc: no licence - grey)",
            "311": "Public Domain (data.kcmo.org)"}
DOM = "data.kcmo.org"


def cases311(bbox):
    new = socrata(DOM, "d4px-6rwg", where=soql_box("lat_long", bbox), limit=50000,
                  select="latitude,longitude,open_date_time,issue_type,issue_sub_type,current_status")
    out = [case_rec(lon=num(r.get("longitude")), lat=num(r.get("latitude")), date=date(r.get("open_date_time")),
                    type=r.get("issue_type"), title=r.get("issue_sub_type"), status=r.get("current_status")) for r in new]
    old = socrata(DOM, "7at3-sxhp", where=latlon_where("xcoordinate", "ycoordinate", bbox), limit=50000,
                  select="xcoordinate,ycoordinate,creation_date,category,request_type,status")
    out += [case_rec(lon=num(r.get("xcoordinate")), lat=num(r.get("ycoordinate")), date=date(r.get("creation_date")),
                     type=r.get("category"), title=r.get("request_type"), status=r.get("status")) for r in old]
    return out


def fetch(bbox):
    return run({"assessor": removed("City of Kansas City GIS parcels, zoning and land-use codes have no licence; data.kcmo.org terms reserve IP (grey)"),
                "permits": removed("data.kcmo.org CPD permits ntw8-aacc have no licence (grey)"),
                "311": cases311}, bbox)

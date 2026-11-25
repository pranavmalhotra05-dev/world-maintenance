"""Cincinnati, OH (Hamilton County, FIPS 39061) - replacement for Fairfax County VA.

Fairfax County (terms: "internal or personal use" only) and Arlington County VA (website terms: "non commercial,
personal use only") both fail the commercial-use rule, so the slot went to Cincinnati.

assessor  "Hamilton County Parcel Polygons" as listed on City of Cincinnati Open Data (g24g-2pi5, licence "Public Domain"),
          read from its access point, CAGIS HCE/Cadastral MapServer layer 0 (item 9ab9d942593d4b0fb06a8e99e7e0ff23):
          situs address, market land / improvement / total value, Ohio land-use class, last sale date and amount.
          Owner / mailing / previous-owner fields exist on the layer but are never requested.
          Year built, area, rooms, roof, walls are not on the open layer (Auditor building data is web-lookup only).
          Zoning: none. The CAGIS "Cincinnati Zoning" layer has no licence (grey), and the public-domain
          "Hamilton County Zoning Designation" (f25h-aupn, Zoning MapServer layer 11) covers only the Rural Zoning
          Commission townships, not the City of Cincinnati.
permits   City of Cincinnati Building Permits (data.cincinnati-oh.gov uhjb-xac9, Socrata, Public Domain).
          companyname (contractor) is not requested.
311       Cincinnati 311 (Non-Emergency) Service Requests (gcej-gmiw, Socrata, Public Domain). Staff user_id not kept.
"""
from ._common import addr_join, arcgis, assessor_rec, case_rec, date, num, permit_rec, pos, run, socrata

NAME, STATE, COUNTY_FIPS = "Cincinnati", "OH", "39061"
BBOX = (-84.72, 39.05, -84.36, 39.23)
LICENCES = {"assessor": "Public Domain (City of Cincinnati Open Data g24g-2pi5 'Hamilton County Parcel Polygons'; no zoning)",
            "permits": "Public Domain (City of Cincinnati Open Data)",
            "311": "Public Domain (City of Cincinnati Open Data)"}
PARCELS = "https://cagisonline.hamilton-co.org/arcgis/rest/services/HCE/Cadastral/MapServer/0"
DOMAIN = "data.cincinnati-oh.gov"
CLASS = {500: "Residential vacant land", 510: "Single family", 520: "Two family", 530: "Three family",
         550: "Condominium", 560: "Mobile home", 599: "Other residential", 400: "Commercial vacant land",
         401: "Apartments 4-19 units", 402: "Apartments 20-39 units", 403: "Apartments 40+ units"}


def _box(bbox, lon="longitude", lat="latitude"):
    w, s, e, n = bbox
    return f"{lat}::number between {s} and {n} and {lon}::number between {w} and {e}"


def assessor(bbox):
    rows = arcgis(PARCELS, bbox, fields="ADDRNO,ADDRST,ADDRSF,MKTLND,MKTIMP,MKT_TOTAL_VAL,CLASS,SALDAT,SALAMT,NUM_UNITS")
    out = []
    for p, lon, lat in rows:
        c = num(p.get("CLASS"))
        out.append(assessor_rec(
            lon=lon, lat=lat, address=addr_join(p.get("ADDRNO"), p.get("ADDRST"), p.get("ADDRSF")),
            land_value=num(p.get("MKTLND")), building_value=num(p.get("MKTIMP")), total_value=pos(p.get("MKT_TOTAL_VAL")),
            sale_date=date(p.get("SALDAT")), sale_price=pos(p.get("SALAMT")),
            property_type=CLASS.get(c, f"Land use {c}" if c else None)))
    return out


def permits(bbox):
    rows = socrata(DOMAIN, "uhjb-xac9", where=_box(bbox), limit=5000, order="issueddate DESC",
                   select="originaladdress1,issueddate,applieddate,permittypemapped,workclassmapped,description,"
                          "statuscurrentmapped,estprojectcostdec,latitude,longitude")
    return [permit_rec(lon=num(r.get("longitude")), lat=num(r.get("latitude")), address=r.get("originaladdress1"),
                       date=date(r.get("issueddate") or r.get("applieddate")), type=r.get("permittypemapped"),
                       work=r.get("workclassmapped"), status=r.get("statuscurrentmapped"), value=pos(r.get("estprojectcostdec")),
                       comments=r.get("description")) for r in rows]


def cases311(bbox):
    rows = socrata(DOMAIN, "gcej-gmiw", where=_box(bbox), limit=5000, order="date_created DESC",
                   select="sr_type_desc,dept_name,group_title,sr_status,sr_status_flag,date_created,latitude,longitude")
    return [case_rec(lon=num(r.get("longitude")), lat=num(r.get("latitude")), date=date(r.get("date_created")),
                     type=r.get("dept_name") or r.get("group_title"), title=r.get("sr_type_desc"),
                     status=r.get("sr_status") or r.get("sr_status_flag")) for r in rows]


def fetch(bbox):
    return run({"assessor": assessor, "permits": permits, "311": cases311}, bbox)

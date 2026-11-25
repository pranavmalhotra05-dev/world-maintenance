"""Saint Paul, MN (Ramsey County, FIPS 27123).

assessor  Metropolitan Council "Metropolitan 7-County Parcel Polygons - 2025" (Parcels_2025) layer 4 "Ramsey County Parcels"
          (public domain under the Minnesota Government Data Practices Act): situs address, EMV land/building/total,
          tax year, year built, finished sq ft, garage, dwelling type / home style, use class, last sale.
          Owner / tax-payer name and address fields are never requested. No roof, walls, beds, baths, stories or zoning.
          (Replaces the Ramsey County CAD_AttributedParcelPoly_ViewOnly service, which states no licence.)
permits   City of Saint Paul "Building Permits Dataset" Building_Permits_ (points; contractor name/address not requested)
311       City of Saint Paul "Resident Service Requests" (points)
"""
from ._common import addr_join, arcgis, assessor_rec, case_rec, date, num, permit_rec, pos, run, year

NAME, STATE, COUNTY_FIPS = "Saint Paul", "MN", "27123"
BBOX = (-93.21, 44.88, -93.00, 45.00)
LICENCES = {"assessor": "Public domain (Metropolitan Council Parcels_2025 layer 4, Minnesota Government Data Practices Act)",
            "permits": "Public Domain (City of Saint Paul open data)",
            "311": "Public Domain (City of Saint Paul open data)"}
SP = "https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/"
PARCELS = "https://arcgis.metc.state.mn.us/data1/rest/services/parcels/Parcels_2025/FeatureServer/4"
PERMITS = SP + "Building_Permits_/FeatureServer/0"
CASES = SP + "Resident_Service_Requests/FeatureServer/0"


def assessor(bbox):
    f = ("ANUMBER,ANUMBERSUF,ST_PRE_DIR,ST_PRE_TYP,ST_NAME,ST_POS_TYP,ST_POS_DIR,SUB_TYPE1,SUB_ID1,EMV_LAND,EMV_BLDG,EMV_TOTAL,"
         "TAX_YEAR,USECLASS1,DWELL_TYPE,HOME_STYLE,FIN_SQ_FT,GARAGE,GARAGESQFT,YEAR_BUILT,SALE_DATE,SALE_VALUE")
    out = []
    for p, lon, lat in arcgis(PARCELS, bbox, fields=f):
        gsf = pos(p.get("GARAGESQFT"))
        # GARAGE is "No"/"Unknown" for every Ramsey parcel in the 2025 layer (not maintained), so only the sq ft is used
        garage = f"{gsf} sqft" if gsf else None
        addr = addr_join(p.get("ANUMBER"), p.get("ANUMBERSUF"), p.get("ST_PRE_DIR"), p.get("ST_PRE_TYP"), p.get("ST_NAME"),
                         p.get("ST_POS_TYP"), p.get("ST_POS_DIR"))
        if addr and p.get("SUB_ID1"):
            addr += f" {p.get('SUB_TYPE1') or '#'} {p['SUB_ID1']}".replace("# ", "#")
        r = assessor_rec(
            lon=lon, lat=lat, address=addr, year_built=year(p.get("YEAR_BUILT")),
            living_area_sqft=pos(p.get("FIN_SQ_FT")), garage=garage,
            land_value=num(p.get("EMV_LAND")), building_value=num(p.get("EMV_BLDG")), total_value=num(p.get("EMV_TOTAL")),
            sale_date=date(p.get("SALE_DATE")), sale_price=pos(p.get("SALE_VALUE")),
            property_type=p.get("DWELL_TYPE") or p.get("HOME_STYLE") or p.get("USECLASS1"))
        if p.get("TAX_YEAR"):
            r["tax_year"] = p.get("TAX_YEAR")
        out.append(r)
    return out


def permits(bbox):
    rows = arcgis(PERMITS, bbox, fields="ADDRESS,STATUS,ISSUEDATE,FOLDER_TYPE,SUB_TYPE,WORK_TYPE,FOLDERDESCRIPTION,EST_VALUE_OF_WORK,STATE_VALUATION")
    return [permit_rec(lon=lon, lat=lat, address=p.get("ADDRESS"), date=date(p.get("ISSUEDATE")),
                       type=" / ".join(x for x in (p.get("FOLDER_TYPE"), p.get("SUB_TYPE")) if x) or None, work=p.get("WORK_TYPE"),
                       status=p.get("STATUS"), value=num(p.get("EST_VALUE_OF_WORK")) or num(p.get("STATE_VALUATION")),
                       comments=p.get("FOLDERDESCRIPTION")) for p, lon, lat in rows]


def cases311(bbox):
    rows = arcgis(CASES, bbox, fields="REQUEST_DATE,STATUS,REQUEST_TYPE,REQUEST_DESCRIPTION")
    return [case_rec(lon=lon, lat=lat, date=date(p.get("REQUEST_DATE")), type=p.get("REQUEST_TYPE"),
                     title=p.get("REQUEST_DESCRIPTION"), status=p.get("STATUS")) for p, lon, lat in rows]


def fetch(bbox):
    return run({"assessor": assessor, "permits": permits, "311": cases311}, bbox)

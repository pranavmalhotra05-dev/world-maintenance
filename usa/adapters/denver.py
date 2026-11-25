"""Denver, CO (City and County of Denver, FIPS 08031) - Denver Open Data Catalog (CC BY 3.0).

assessor  ODC_PROP_PARCELS_A (parcel polygons: values, zoning, year built, area, last sale)
          + ODC_real_property_residential_characteristics (stories, beds, baths) joined on SCHEDNUM=PARID
          Owner fields are not requested. Roof / exterior wall are not published.
permits   ODC_DEV_RESIDENTIALCONSTPERMIT_P (residential construction permits, points). Contractor name not kept.
311       ODC_service_requests_311 (table with Latitude/Longitude, recent years)
"""
from ._common import arcgis, assessor_rec, case_rec, chunked_in, date, latlon_where, num, permit_rec, pos, run, year, addr_join, baths

NAME, STATE, COUNTY_FIPS = "Denver", "CO", "08031"
BBOX = (-105.11, 39.61, -104.60, 39.92)
LICENCES = {"assessor": "CC BY 3.0 (City and County of Denver Open Data Catalog)", "permits": "CC BY 3.0 (City and County of Denver Open Data Catalog)",
            "311": "CC BY 3.0 (City and County of Denver Open Data Catalog)"}
B = "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
PARCELS = B + "ODC_PROP_PARCELS_A/FeatureServer/245"
RESCHAR = B + "ODC_real_property_residential_characteristics/FeatureServer/50"
PERMITS = B + "ODC_DEV_RESIDENTIALCONSTPERMIT_P/FeatureServer/316"
CASES = B + "ODC_service_requests_311/FeatureServer/53"


def assessor(bbox):
    pf = ("SCHEDNUM,SITUS_ADDRESS_LINE1,ZONE_10,D_CLASS_CN,APPRAISED_LAND_VALUE,APPRAISED_IMP_VALUE,APPRAISED_TOTAL_VALUE,"
          "RES_ORIG_YEAR_BUILT,RES_ABOVE_GRADE_AREA,COM_ORIG_YEAR_BUILT,COM_GROSS_AREA,SALE_DATE,SALE_PRICE")
    parcels = arcgis(PARCELS, bbox, fields=pf)
    chars = {}
    for a in chunked_in(RESCHAR, "PARID", [p["SCHEDNUM"] for p, _, _ in parcels],
                        fields="PARID,STORY,BED_RMS,FULL_B,HLF_B,AREA_ABG,CCYRBLT,STYLE_CN,PROP_CLASS"):
        chars.setdefault(a["PARID"], a)
    out = []
    for p, lon, lat in parcels:
        c = chars.get(p["SCHEDNUM"], {})
        out.append(assessor_rec(
            lon=lon, lat=lat, address=p.get("SITUS_ADDRESS_LINE1"),
            year_built=year(p.get("RES_ORIG_YEAR_BUILT")) or year(c.get("CCYRBLT")) or year(p.get("COM_ORIG_YEAR_BUILT")),
            stories=pos(c.get("STORY")), living_area_sqft=pos(p.get("RES_ABOVE_GRADE_AREA")) or pos(c.get("AREA_ABG")) or pos(p.get("COM_GROSS_AREA")),
            bedrooms=pos(c.get("BED_RMS")), baths=baths(c.get("FULL_B"), c.get("HLF_B")),
            land_value=num(p.get("APPRAISED_LAND_VALUE")), building_value=num(p.get("APPRAISED_IMP_VALUE")),
            total_value=num(p.get("APPRAISED_TOTAL_VALUE")), sale_date=date(p.get("SALE_DATE")), sale_price=pos(p.get("SALE_PRICE")),
            zoning=p.get("ZONE_10"), property_type=c.get("PROP_CLASS") or p.get("D_CLASS_CN")))
    return out


def permits(bbox):
    rows = arcgis(PERMITS, bbox, fields="DATE_ISSUED,ADDRESS,CLASS,VALUATION,FINAL_DATE,CANCEL,UNITS")
    out = []
    for p, lon, lat in rows:
        st = "cancelled" if p.get("CANCEL") else ("final" if p.get("FINAL_DATE") else "issued")
        out.append(permit_rec(lon=lon, lat=lat, address=p.get("ADDRESS"), date=date(p.get("DATE_ISSUED")), type="Residential construction",
                              work=p.get("CLASS"), status=st, value=num(p.get("VALUATION")), comments=None))
    return out


def cases311(bbox):
    rows = arcgis(CASES, where=latlon_where("Longitude", "Latitude", bbox), geom=False,
                  fields="Case_Summary,Case_Status,Case_Created_Date,Agency,Type,Topic,Longitude,Latitude")
    return [case_rec(lon=num(a.get("Longitude")), lat=num(a.get("Latitude")), date=date(a.get("Case_Created_Date")),
                     type=a.get("Type") or a.get("Agency"), title=a.get("Topic") or a.get("Case_Summary"), status=a.get("Case_Status"))
            for a, _, _ in rows]


def fetch(bbox):
    return run({"assessor": assessor, "permits": permits, "311": cases311}, bbox)

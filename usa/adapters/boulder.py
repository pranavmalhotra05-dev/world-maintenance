"""Boulder, CO (Boulder County, FIPS 08013).

assessor  Boulder County "Assessor's Property Data" bulk CSVs (CC BY 4.0, catalog item d39100d04be34354b97ad52874f5e7d4,
          https://www.bouldercounty.org/property-and-land/assessor/data-download/): Buildings (year built, design,
          stories, finished sqft, beds, baths, garage/car storage, roof cover, exterior wall), Values (latest tax year
          actual values) and Sales (latest sale). Joined on account number (strap) to the parcel polygons of catalog item
          90e3547382df4d498b779672d2685b0b "Parcels (with address and ownership information)" (CC BY 4.0,
          maps.bouldercounty.org PARCELS/PARCELS_OWNER). Owner / care-of / mailing fields are never requested and the
          Owner_Address.csv file is never downloaded. CSVs are cached in usa/cache/boulder/ (refresh: delete the folder).
          Zoning from City of Boulder "Zoning Districts" (CC0 1.0), point in polygon.
          (Replaces the CamaView services and the Zoning_2024 layer, which carry no licence text.)
permits   City of Boulder "Construction Permits" (table, CC0, 1979 -> today, no geometry): joined to the county parcels in
          the box on BOCOPIN = ParcelNo. ContractorCompanyName is never requested.
311       Inquire Boulder (the city's 311) is published only as counts by topic with no location, so the City of Boulder
          "Code Enforcement Reports" points (CC0 1.0, gis.bouldercolorado.gov plan/CodeEnforcement) are used instead.
"""
import csv

from shapely.geometry import Point, shape

from ._common import CACHE, S, addr_join, arcgis, assessor_rec, baths, case_rec, chunked_in, date, num, permit_rec, pos, run, year

NAME, STATE, COUNTY_FIPS = "Boulder", "CO", "08013"
BBOX = (-105.31, 39.95, -105.17, 40.10)
LICENCES = {"assessor": "CC BY 4.0 (Boulder County Assessor's Property Data bulk CSVs + Boulder County Parcels); zoning CC0 1.0 (City of Boulder Zoning Districts)",
            "permits": "CC0 1.0 (City of Boulder Construction Permits)",
            "311": "CC0 1.0 (City of Boulder Code Enforcement Reports)"}
PARCELS = "https://maps.bouldercounty.org/arcgis/rest/services/PARCELS/PARCELS_OWNER/MapServer/0"
CSV_URL = "https://assessor.boco.solutions/ASR_PublicDataFiles/{}.csv"
CSV_DIR = CACHE / "boulder"
CITY = "https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/"
ZONING = CITY + "ZoningDistricts/MapServer/0"
CASES = CITY + "CodeEnforcement/MapServer/0"
PERMITS = "https://services.arcgis.com/ePKBjXrBZ2vEEgWd/arcgis/rest/services/Construction_Permits/FeatureServer/0"


def _csv(name):
    """Path of a cached bulk CSV, downloading it on first use."""
    p = CSV_DIR / f"{name}.csv"
    if not p.exists() or p.stat().st_size == 0:
        CSV_DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        with S.get(CSV_URL.format(name), stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        tmp.replace(p)
    return p


def _rows(name, accts):
    with open(_csv(name), encoding="utf-8-sig", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("strap") in accts:
                yield r


def _parcels(bbox):
    return arcgis(PARCELS, bbox, fields="AccountNo,ParcelNo,StrNum,StrPrx,Street,StrSuf,StrUnit,StrCity,AcctType")


def _stories(b):
    s = num(b.get("Stories"))
    if s and 0 < s < 10:
        return s
    d = (b.get("designCodeDscr") or "").lower()
    for k, v in (("1 story", 1), ("ranch", 1), ("2-3 story", 2), ("2 story", 2), ("split", 2), ("bi-level", 2), ("3 story", 3)):
        if k in d:
            return v
    return None


def _zones(bbox):
    try:
        j = S.get(ZONING + "/query", params={"where": "1=1", "geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope",
                                             "inSR": 4326, "outSR": 4326, "outFields": "ZONING", "f": "geojson"}, timeout=90).json()
        return [(shape(f["geometry"]), (f.get("properties") or {}).get("ZONING")) for f in j.get("features", []) if f.get("geometry")]
    except Exception:
        return []


def assessor(bbox):
    parcels = _parcels(bbox)
    accts = {p["AccountNo"] for p, _, _ in parcels if p.get("AccountNo")}
    bld = {}
    for b in _rows("Buildings", accts):   # main building = largest finished area
        a = b["strap"]
        if a not in bld or (num(b.get("TotalFinishedSF")) or 0) > (num(bld[a].get("TotalFinishedSF")) or 0):
            bld[a] = b
    vals = {}
    for v in _rows("Values", accts):
        a = v["strap"]
        if a not in vals or (num(v.get("tax_yr")) or 0) > (num(vals[a].get("tax_yr")) or 0):
            vals[a] = v
    sales = {}
    for s in _rows("Sales", accts):
        a, d = s["strap"], date(s.get("Tdate"))
        if d and (a not in sales or d > sales[a][0]):
            sales[a] = (d, s)
    zones = _zones(bbox)
    out = []
    for p, lon, lat in parcels:
        ac = p.get("AccountNo"); b = bld.get(ac, {}); v = vals.get(ac, {}); sd, s = sales.get(ac, (None, {}))
        z = next((zn for poly, zn in zones if lon is not None and poly.contains(Point(lon, lat))), None)
        bt = baths(b.get("nbrFullBaths"), b.get("nbrHalfBaths"))
        if num(b.get("nbrThreeQtrBaths")):
            bt = (bt or 0) + 0.75 * num(b["nbrThreeQtrBaths"])
        car = pos(b.get("carStorageSF"))
        garage = f"{car} sqft {(b.get('carStorageTypeDscr') or '').strip().lower()}".strip() if car else None
        addr = addr_join(p.get("StrNum"), p.get("StrPrx"), p.get("Street"), p.get("StrSuf"))
        if addr and p.get("StrUnit"):
            addr += f" #{p['StrUnit']}"
        r = assessor_rec(
            lon=lon, lat=lat, address=addr, year_built=year(b.get("builtYear")), stories=_stories(b),
            living_area_sqft=pos(b.get("TotalFinishedSF")), roof=(b.get("Roof_CoverDscr") or "").strip() or None,
            exterior_wall=(b.get("ExtWallDscrPrim") or "").strip() or None, garage=garage,
            bedrooms=pos(b.get("nbrBedRoom")), baths=bt, land_value=num(v.get("LandAcutalVal")),
            building_value=num(v.get("bldAcutalVal")), total_value=pos(v.get("totalActualVal")),
            sale_date=sd, sale_price=pos(s.get("price")), zoning=z,
            property_type=(b.get("bldgClassDscr") or "").strip() or p.get("AcctType"))
        if v.get("tax_yr"):
            r["tax_year"] = num(v["tax_yr"])
        out.append(r)
    return out


def permits(bbox):
    loc = {p["ParcelNo"]: (lon, lat) for p, lon, lat in _parcels(bbox) if p.get("ParcelNo")}
    rows = chunked_in(PERMITS, "BOCOPIN", loc, fields="BOCOPIN,OriginalAddress,IssuedDate,AppliedDate,StatusCurrent,PermitType,PermitWorkType,Description,EstProjectCost")
    out = []
    for p in rows:
        lon, lat = loc.get(p.get("BOCOPIN"), (None, None))
        out.append(permit_rec(lon=lon, lat=lat, address=p.get("OriginalAddress"), date=date(p.get("IssuedDate") or p.get("AppliedDate")),
                              type=p.get("PermitType"), work=p.get("PermitWorkType"), status=p.get("StatusCurrent"),
                              value=num(p.get("EstProjectCost")), comments=p.get("Description")))
    return out


def cases311(bbox):
    rows = arcgis(CASES, bbox, fields="SHORTDESCRIPTION,CATEGORY,OPENDATE,STATUS")
    return [case_rec(lon=lon, lat=lat, date=date(p.get("OPENDATE")), type=p.get("CATEGORY"), title=p.get("SHORTDESCRIPTION"),
                     status=p.get("STATUS")) for p, lon, lat in rows]


def fetch(bbox):
    return run({"assessor": assessor, "permits": permits, "311": cases311}, bbox)

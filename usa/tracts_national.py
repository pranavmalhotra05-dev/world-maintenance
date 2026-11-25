"""Nationwide census-tract layer (2020 tracts, 50 states + DC + PR), one JSON per state for lazy loading.

  .venv\\Scripts\\python -u -W ignore usa/tracts_national.py            # all states
  .venv\\Scripts\\python -u -W ignore usa/tracts_national.py TX MN      # subset (index.json is merged)

Outputs
  web/usa/data/tracts/<ST>.json   {meta:{state,count,sources,fields,simplify_deg,decimals},
                                   tracts:[{g, geom, c, m:{...}}]}   (null metrics dropped)
  web/usa/data/tracts/index.json  {meta, states:{ST:{fips,name,count,bbox,bytes,file}}}

Only US federal government works (17 U.S.C. 105, public domain; commercial use allowed):
  geometry   Census cartographic boundary tracts cb_2024_us_tract_500k (2020 tracts; CT planning-region codes)
  ACS        2020-2024 ACS 5-year table-based summary file (www2.census.gov) B19013 B25077 B25035 B25003 B25024 B01003
  OZ 2018    CDFI Fund 2018 QOZ shapefile (2010 tracts) -> 2020 tracts via Census 2020<->2010 tract relationship file
  OZ 2027    CDFI Fund final_oz2_designations.xlsx (2020 tracts)
  QCT 2026   HUD eGIS QUALIFIED_CENSUS_TRACTS_2026 (2020 tracts)
  NMTC       CDFI Fund NMTC low-income community (2016-2020 ACS) via HUD eGIS NMTC20 (2020 tracts)
  NRI        FEMA National Risk Index census tracts (ArcGIS FeatureServer, FEMA-owned service)
  FHFA HPI   FHFA annual tract HPI (2010 tracts) -> 2020 tracts via dominant-land-area 2010 tract
Not included: HMDA tract counts (no single bulk tract file without the per-state loan-level CSVs);
housing-demand proxies (no green tract-level source beyond the ACS fields above).
"""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache"
TC = CACHE / "tracts"
OUT = ROOT / "web" / "usa" / "data" / "tracts"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) roofprint-usa/0.1 (open-data research)"}

ACS_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
REL_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/tab20_tract20_tract10_natl.txt"
CB2024_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_tract_500k.zip"
CB2020_URL = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_tract_500k.zip"
OZ18_URL = "https://www.cdfifund.gov/system/files/documents/opportunity-zones=8764.-9-10-2019.zip"
OZ27_URL = "https://www.cdfifund.gov/system/files/2026-11/final_oz2_designations.xlsx"
HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services/"
L_QCT = HUD + "QUALIFIED_CENSUS_TRACTS_2026/FeatureServer/0"
L_NMTC = HUD + "NMTC20/FeatureServer/0"
L_NRI = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0"
FHFA_URL = "https://www.fhfa.gov/hpi/download/annual/hpi_at_tract.csv"

FIPS_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}
ABBR_FIPS = {v: k for k, v in FIPS_ABBR.items()}
TOLS = (0.0005, 0.00075, 0.001, 0.0015, 0.002, 0.003)  # degrees, tried in order until the file fits
DECIMALS = 4
PREF_BYTES, MAX_BYTES = 4_000_000, 8_000_000

FED = "US federal government work, not subject to copyright (17 U.S.C. 105): https://www.usa.gov/government-copyright"
SOURCES = [
    {"id": "geometry", "name": "U.S. Census Bureau, 2024 Cartographic Boundary File, census tracts 1:500,000 "
                               "(2020 tracts)", "url": CB2024_URL, "licence": FED},
    {"id": "acs", "name": "U.S. Census Bureau, 2020-2024 American Community Survey 5-year estimates, table-based "
                          "summary file (B19013, B25077, B25035, B25003, B25024, B01003)", "url": ACS_BASE,
     "licence": FED},
    {"id": "oz18", "name": "U.S. Treasury CDFI Fund, Designated Qualified Opportunity Zones (IRS Notice 2018-48), "
                           "2010 tracts mapped to 2020 tracts with the Census 2020-2010 tract relationship file",
     "url": OZ18_URL + " ; " + REL_URL, "licence": FED},
    {"id": "oz27", "name": "U.S. Treasury CDFI Fund, final 2027 Opportunity Zone designations", "url": OZ27_URL,
     "licence": FED},
    {"id": "qct", "name": "U.S. Department of Housing and Urban Development (HUD) PD&R, Qualified Census Tracts 2026 "
                          "(HUD eGIS open data). Not endorsed by HUD.", "url": L_QCT, "licence": FED},
    {"id": "nmtc", "name": "U.S. Treasury CDFI Fund, New Markets Tax Credit low-income community eligibility "
                           "(2016-2020 ACS), via HUD eGIS layer NMTC20", "url": L_NMTC, "licence": FED},
    {"id": "nri", "name": "FEMA National Risk Index, census tracts (risk score and hazard risk scores, 0-100 "
                          "national percentile-based)", "url": L_NRI + " ; https://hazards.fema.gov/nri/",
     "licence": FED},
    {"id": "fhfa_hpi", "name": "Federal Housing Finance Agency, annual House Price Index by census tract "
                               "(developmental index, 2010 tracts; mapped to the 2020 tract's dominant 2010 tract "
                               "by land area)", "url": FHFA_URL, "licence": FED},
]
FIELDS = {
    "g": "2020 census tract GEOID (11 digits; Connecticut uses 2022+ planning-region county codes)",
    "geom": "MultiPolygon coordinates: [polygon[ring[[lon,lat],...]]], simplified (meta.simplify_deg), "
            "rounded to meta.decimals",
    "c": "[lon,lat] representative point inside the tract",
    "m.income": "ACS B19013 median household income, USD (2024 inflation-adjusted)",
    "m.home_value": "ACS B25077 median value, owner-occupied units, USD",
    "m.year_built": "ACS B25035 median year structure built (1939 = 1939 or earlier)",
    "m.owner_pct": "ACS B25003 owner-occupied / occupied housing units, percent",
    "m.sf_pct": "ACS B25024 1-unit detached / all housing units, percent",
    "m.pop": "ACS B01003 total population",
    "m.oz18": "1 = at least half of the tract's land area lies in 2018-round Qualified Opportunity Zones "
              "(in force to 2028-12-31)",
    "m.oz27": "1 = 2027-round Qualified Opportunity Zone (effective 2027-01-01..2036-12-31); 2 = rural QOZ",
    "m.qct": "1 = HUD 2026 Qualified Census Tract",
    "m.nmtc": "1 = NMTC low-income community (2016-2020 ACS)",
    "m.nri": "FEMA NRI composite risk score (0-100)",
    "m.nri_hail": "FEMA NRI hail risk score (0-100)",
    "m.nri_wind": "FEMA NRI strong-wind risk score (0-100)",
    "m.nri_flood": "FEMA NRI flood risk score: max of riverine (IFLD) and coastal (CFLD) risk scores (0-100)",
    "m.hpi_1y": "FHFA tract HPI annual change, latest year, percent",
    "m.hpi_5y": "FHFA tract HPI change over 5 years to the latest year, percent",
}
S = requests.Session()
S.headers.update(UA)


# ---------------------------------------------------------------- fetch helpers
def get(url, params=None, timeout=300, tries=4):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=timeout)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def cached(name, url, *alts):
    """Reuse an existing cache file (any of alts) or download into usa/_cache/tracts/."""
    for p in alts:
        if p.exists() and p.stat().st_size > 1000:
            return p
    p = TC / name
    if not p.exists() or p.stat().st_size < 1000:
        TC.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {url}")
        r = get(url, timeout=900)
        if r.content[:15].lower().startswith(b"<!doctype html"):
            raise RuntimeError(f"{url} returned HTML")
        p.write_bytes(r.content)
    return p


def arc_all(layer, fields, cache_name):
    p = TC / cache_name
    if p.exists():
        return json.loads(p.read_text())
    rows, off = [], 0
    while True:
        j = get(layer + "/query", params={"where": "1=1", "outFields": fields, "returnGeometry": "false",
                                          "resultOffset": off, "resultRecordCount": 2000,
                                          "orderByFields": "OBJECTID", "f": "json"}).json()
        if "error" in j:
            raise RuntimeError(j["error"])
        feats = j.get("features", [])
        rows += [f["attributes"] for f in feats]
        off += len(feats)
        if not feats or not j.get("exceededTransferLimit"):
            break
    TC.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows))
    return rows


# ---------------------------------------------------------------- sources
def acs(tbl, cols):
    name = f"acsdt5y2024-{tbl}.dat"
    p = cached(name, ACS_BASE + name, CACHE / "cleaning" / name, CACHE / name)
    df = pd.read_csv(p, sep="|", usecols=["GEO_ID"] + cols, dtype=str)
    df = df[df["GEO_ID"].str.startswith("1400000US")]
    df["g"] = df["GEO_ID"].str[9:]
    for c in cols:
        v = pd.to_numeric(df[c], errors="coerce")
        df[c] = v.where(v >= 0)  # negative = Census jam code (no estimate)
    return df.set_index("g")[cols]


def load_acs():
    a = pd.concat([acs("b19013", ["B19013_E001"]), acs("b25077", ["B25077_E001"]), acs("b25035", ["B25035_E001"]),
                   acs("b25003", ["B25003_E001", "B25003_E002"]), acs("b25024", ["B25024_E001", "B25024_E002"]),
                   acs("b01003", ["B01003_E001"])], axis=1)
    out = pd.DataFrame(index=a.index)
    out["income"] = a["B19013_E001"].where(a["B19013_E001"] > 0)
    out["home_value"] = a["B25077_E001"].where(a["B25077_E001"] > 0)
    out["year_built"] = a["B25035_E001"].where(a["B25035_E001"] > 1000)
    out["owner_pct"] = (100 * a["B25003_E002"] / a["B25003_E001"].where(a["B25003_E001"] > 0)).round(1)
    out["sf_pct"] = (100 * a["B25024_E002"] / a["B25024_E001"].where(a["B25024_E001"] > 0)).round(1)
    out["pop"] = a["B01003_E001"]
    return out


def ct_map(cb24):
    """CT 2020 tract GEOIDs with legacy county codes (09001..) -> 2022+ planning-region GEOIDs (0911x)."""
    p = cached("cb_2020_us_tract_500k.zip", CB2020_URL, CACHE / "oz" / "cb_2020_us_tract_500k.zip")
    old = gpd.read_file(f"zip://{p.as_posix()}", where="STATEFP='09'")[["GEOID", "geometry"]].to_crs(4326)
    new = cb24[cb24.STATEFP == "09"][["GEOID", "geometry"]]
    pts = gpd.GeoDataFrame({"old": old.GEOID.values}, geometry=old.geometry.representative_point().values, crs=4326)
    j = gpd.sjoin(pts, new, how="left", predicate="within")
    j = j[~j.index.duplicated()].dropna(subset=["GEOID"])
    m = dict(zip(j["old"], j["GEOID"]))
    bad = [o for o, n in m.items() if o[-6:] != n[-6:]]
    print(f"  CT map: {len(m)} tracts, tract-code mismatches {len(bad)}")
    return m


def relationship(ctm):
    p = cached("tab20_tract20_tract10_natl.txt", REL_URL, CACHE / "cleaning" / "tab20_tract20_tract10_natl.txt")
    rel = pd.read_csv(p, sep="|", dtype=str, usecols=["GEOID_TRACT_20", "GEOID_TRACT_10", "AREALAND_TRACT_20",
                                                       "AREALAND_PART", "AREAWATER_PART"])
    rel["g"] = rel["GEOID_TRACT_20"].map(lambda x: ctm.get(x, x))
    rel["part"] = pd.to_numeric(rel["AREALAND_PART"]).fillna(0)
    rel["wpart"] = pd.to_numeric(rel["AREAWATER_PART"]).fillna(0)
    rel["land20"] = pd.to_numeric(rel["AREALAND_TRACT_20"]).fillna(0)
    # water-only tracts: weight by water part instead
    rel["w"] = np.where(rel["land20"] > 0, rel["part"], rel["wpart"])
    return rel[["g", "GEOID_TRACT_10", "w"]].rename(columns={"GEOID_TRACT_10": "g10"})


def load_oz18(rel):
    p = cached("oz2018_cdfi.zip", OZ18_URL, CACHE / "oz" / "oz2018_cdfi.zip")
    zf = zipfile.ZipFile(p)
    dbf = [n for n in zf.namelist() if n.lower().endswith(".dbf")][0]
    shp = gpd.read_file(f"zip://{p.as_posix()}!{dbf[:-4]}.shp", ignore_geometry=True)
    z = set(shp["CENSUSTRAC"].astype(str)) | {"51019050100"}  # Bedford city VA retired 2013
    r = rel.copy()
    r["in"] = r["g10"].isin(z) * r["w"]
    share = r.groupby("g")["in"].sum() / r.groupby("g")["w"].sum().replace(0, np.nan)
    print(f"  OZ2018: {len(z)} 2010 zones -> {(share >= 0.5).sum()} 2020 tracts with >=50% land share")
    return share


def load_oz27():
    p = cached("final_oz2_designations.xlsx", OZ27_URL, CACHE / "oz" / "final_oz2_designations.xlsx")
    lst = pd.read_excel(p, dtype=str)
    lst.columns = ["geoid", "qozid", "state", "county", "rural"]
    lst = lst.dropna(subset=["geoid"])
    return {g: (2 if str(r).strip().lower() == "rural" else 1) for g, r in zip(lst.geoid.str.strip(), lst.rural)}


def load_hpi(rel):
    p = cached("hpi_at_tract.csv", FHFA_URL, CACHE / "federal" / "hpi_at_tract.csv")
    h = pd.read_csv(p, dtype={"tract": str})
    h["hpi"] = pd.to_numeric(h["hpi"], errors="coerce")
    h["annual_change"] = pd.to_numeric(h["annual_change"], errors="coerce")
    last = int(h.loc[h.hpi.notna(), "year"].max())
    cur = h[h.year == last].set_index("tract")
    old = h[h.year == last - 5].set_index("tract")["hpi"]
    t = pd.DataFrame({"hpi_1y": cur["annual_change"], "hpi_5y": 100 * (cur["hpi"] / old.reindex(cur.index) - 1)})
    t = t.dropna(how="all")
    r = rel[rel.g10.isin(t.index)].sort_values("w", ascending=False).drop_duplicates("g")
    out = t.reindex(r.g10.values)
    out.index = r.g.values
    print(f"  FHFA HPI: latest year {last}; {len(t)} 2010 tracts with data -> {out.notna().any(axis=1).sum()} 2020")
    return out.round(1), last


def load_flags(ctm):
    def fix(g):
        g = str(g).zfill(11)
        return ctm.get(g, g)
    q = arc_all(L_QCT, "OBJECTID,GEOID", "hud_qct2026.json")
    qct = {fix(r["GEOID"]) for r in q if r.get("GEOID")}
    n = arc_all(L_NMTC, "OBJECTID,GEOID,NMTC_LIC_INC", "hud_nmtc20.json")
    nmtc = {fix(r["GEOID"]): int(r["NMTC_LIC_INC"] or 0) for r in n if r.get("GEOID")}
    nr = arc_all(L_NRI, "OBJECTID,TRACTFIPS,RISK_SCORE,HAIL_RISKS,SWND_RISKS,IFLD_RISKS,CFLD_RISKS,NRI_VER",
                 "nri_tracts.json")
    nri = pd.DataFrame(nr)
    nri["g"] = nri["TRACTFIPS"].astype(str).str.zfill(11).map(lambda g: ctm.get(g, g))
    nri["nri_flood"] = nri[["IFLD_RISKS", "CFLD_RISKS"]].max(axis=1)
    nri = nri.rename(columns={"RISK_SCORE": "nri", "HAIL_RISKS": "nri_hail", "SWND_RISKS": "nri_wind"})
    ver = sorted(nri["NRI_VER"].dropna().astype(str).unique())
    nri = nri.drop_duplicates("g").set_index("g")[["nri", "nri_hail", "nri_wind", "nri_flood"]].round(1)
    print(f"  QCT {len(qct)}  NMTC rows {len(nmtc)} (LIC {sum(nmtc.values())})  NRI {len(nri)} ver {ver}")
    return qct, nmtc, nri, ver


# ---------------------------------------------------------------- geometry
def rings(geom, nd):
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if
                                                        g.geom_type == "Polygon"]
    out = []
    for p in polys:
        rr = [np.round(np.asarray(p.exterior.coords), nd)] + [np.round(np.asarray(i.coords), nd) for i in p.interiors]
        rr = [r for r in rr if len(r) >= 4]
        if rr and len(rr[0]) >= 4:
            out.append([[[float(x), float(y)] for x, y in r] for r in rr])
    return out


def state_geoms(gdf, tol, nd):
    simp = shapely.simplify(gdf.geometry.values, tol, preserve_topology=True)
    res = []
    for orig, g in zip(gdf.geometry.values, simp):
        geom = rings(g, nd)
        if not geom:  # collapsed sliver -> tiny square at the representative point
            geom = rings(orig.representative_point().buffer(tol, quad_segs=1), nd)
        res.append(geom)
    return res


def num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def main(argv):
    only = {a.upper() for a in argv}
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading tracts ...")
    cbp = cached("cb_2024_us_tract_500k.zip", CB2024_URL, CACHE / "oz" / "cb_2024_us_tract_500k.zip")
    cb = gpd.read_file(f"zip://{cbp.as_posix()}").to_crs(4326)
    cb = cb[cb.STATEFP.isin(FIPS_ABBR)].copy()
    print(f"  {len(cb)} tracts in 50 states + DC + PR")
    ctm = ct_map(cb)
    print("loading ACS ...")
    a = load_acs()
    print(f"  ACS tract rows {len(a)}")
    rel = relationship(ctm)
    oz18 = load_oz18(rel)
    oz27 = load_oz27()
    hpi, hpi_year = load_hpi(rel)
    qct, nmtc, nri, nri_ver = load_flags(ctm)

    g = cb.set_index("GEOID")
    ids = g.index
    m = a.reindex(ids)
    m["oz18"] = (oz18.reindex(ids) >= 0.5).astype(int).where(oz18.reindex(ids).notna())
    m["oz27"] = pd.Series({i: oz27.get(i, 0) for i in ids})
    m["qct"] = pd.Series({i: int(i in qct) for i in ids})
    m["nmtc"] = pd.Series({i: nmtc.get(i) for i in ids}, dtype=float)
    m = m.join(nri.reindex(ids)).join(hpi.reindex(ids))
    cols = list(m.columns)
    cov = {c: int(m[c].notna().sum()) for c in cols}
    cov_pos = {c: int((m[c] > 0).sum()) for c in ("oz18", "oz27", "qct", "nmtc")}
    missing = {"oz27": sorted(set(oz27) - set(ids))[:20], "qct": sorted(qct - set(ids))[:20],
               "nri": sorted(set(nri.index) - set(ids))[:10]}
    print("coverage (non-null):", cov)
    print("flag positives:", cov_pos)
    print("ids not in geometry:", {k: len(v) for k, v in missing.items()}, missing)

    sources = [dict(s) for s in SOURCES]
    for s in sources:
        if s["id"] == "nri":
            s["version"] = ", ".join(nri_ver)
        if s["id"] == "fhfa_hpi":
            s["latest_year"] = hpi_year
    idx_path = OUT / "index.json"
    index = json.loads(idx_path.read_text()) if (only and idx_path.exists()) else {"states": {}}
    cent = g.geometry.representative_point()
    report = []
    for fp, st in sorted(FIPS_ABBR.items(), key=lambda kv: kv[1]):
        if only and st not in only:
            continue
        sub = g[g.STATEFP == fp]
        if sub.empty:
            continue
        mm = m.loc[sub.index]
        recs_m = []
        for gid, row in zip(mm.index, mm.itertuples(index=False)):
            d = {}
            for c, v in zip(cols, row):
                v = num(v)
                if v is not None:
                    d[c] = v
            recs_m.append(d)
        cc = cent.loc[sub.index]
        # finest tolerance that fits 4 MB; within the requested 0.0005-0.001 deg band accept up to 8 MB;
        # coarser than 0.001 deg only if 0.001 deg is still over the 8 MB cap
        for tol in TOLS:
            geoms = state_geoms(sub, tol, DECIMALS)
            tracts = [{"g": gid, "geom": ge, "c": [round(p.x, DECIMALS), round(p.y, DECIMALS)], "m": md}
                      for gid, ge, p, md in zip(sub.index, geoms, cc.values, recs_m)]
            doc = {"meta": {"state": st, "fips": fp, "name": sub.STATE_NAME.iloc[0], "count": len(tracts),
                            "simplify_deg": tol, "decimals": DECIMALS, "sources": sources, "fields": FIELDS},
                   "tracts": tracts}
            txt = json.dumps(doc, separators=(",", ":"))
            n = len(txt.encode())
            if n <= PREF_BYTES:
                break
            if n <= MAX_BYTES and tol >= 0.001:  # band edge reached (or passed) and under the hard cap
                break
        p = OUT / f"{st}.json"
        p.write_text(txt, encoding="utf-8")
        size = p.stat().st_size
        b = sub.total_bounds
        index["states"][st] = {"fips": fp, "name": sub.STATE_NAME.iloc[0], "count": len(tracts),
                               "bbox": [round(float(x), 4) for x in b], "bytes": size, "file": f"{st}.json",
                               "simplify_deg": tol}
        flag = " OVER 8MB" if size > MAX_BYTES else (" >4MB" if size > PREF_BYTES else "")
        report.append((st, len(tracts), size, tol))
        print(f"  {st}: {len(tracts):5d} tracts  {size/1e6:5.2f} MB  tol {tol}{flag}")
    index["states"] = dict(sorted(index["states"].items()))
    index["meta"] = {"desc": "Nationwide 2020 census tracts with green federal metrics, one file per state",
                     "fields": FIELDS, "sources": sources, "bbox": "[west,south,east,north] EPSG:4326",
                     "total_tracts": sum(v["count"] for v in index["states"].values()),
                     "total_bytes": sum(v["bytes"] for v in index["states"].values()),
                     "coverage_nonnull": cov, "flag_positive": cov_pos}
    idx_path.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    print(f"index: {len(index['states'])} states, {index['meta']['total_tracts']} tracts, "
          f"{index['meta']['total_bytes']/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""National county overview for the whole USA -> web/usa/data/.

Inputs (all permanently free, commercial use allowed):
  data/national/county_overlap.csv   - our overlap score + USGS 3DEP LiDAR coverage (public domain) + NOAA SPC
                                       hail 2015-2024 (public domain) + Census ACS homes (public domain)
  data/national/counties.zip         - Census cartographic county boundaries 1:5M, 2023 (public domain)
Downloaded here (cached in usa/_cache/):
  NOAA SPC daily storm reports, last 24 months  https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered.csv
                                                (US Government work, public domain)
  FEMA National Risk Index, county table        FEMA ArcGIS Online feature service National_Risk_Index_Counties
                                                (US Government work, public domain)
  Census ACS 5-year 2024 B25077 median home value, B25035 median year built (summary-file tables, public domain)

Outputs:
  web/usa/data/counties.geojson  simplified county polygons, properties {f: fips}
  web/usa/data/states.geojson    simplified state outlines
  web/usa/data/counties.json     {cols:[...], rows:[[...]], meta:{...}}  one row per county
"""
import datetime as dt
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
NAT = ROOT / "data" / "national"
CACHE = Path(__file__).resolve().parent / "_cache"
SPC_DIR = CACHE / "spc"
OUT = ROOT / "web" / "usa" / "data"
for d in (CACHE, SPC_DIR, OUT):
    d.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "open-data-research/1.0 (county aggregates)"}
TODAY = dt.date.today()
START = TODAY - dt.timedelta(days=730)

BLOCK_LAYERS = {"27053": "Full city block (Minneapolis)", "48113": "Full city block (Dallas)"}


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=120, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- counties
counties = gpd.read_file(f"zip://{NAT / 'counties.zip'}").to_crs(4326)
counties = counties[~counties["STATEFP"].isin(["60", "66", "69", "78"])].copy()
counties["fips"] = counties["GEOID"]


def fix_antimeridian(g):
    """Alaska's Aleutians cross 180: move positive longitudes to < -180 so the polygon stays contiguous."""
    def shift(x, y, z=None):
        x = np.where(np.asarray(x) > 0, np.asarray(x) - 360, x)
        return (x, y) if z is None else (x, y, z)
    return shapely.ops.transform(shift, g)


import shapely.ops  # noqa: E402

ak = counties["STATEFP"] == "02"
counties.loc[ak, "geometry"] = counties.loc[ak, "geometry"].apply(fix_antimeridian)

# ---------------------------------------------------------------- SPC storm reports (24 months)
def spc_day(d):
    name = d.strftime("%y%m%d") + "_rpts_filtered.csv"
    p = SPC_DIR / name
    recent = (TODAY - d).days < 4  # the newest files can still be revised
    if p.exists() and not recent:
        return p.read_text(encoding="utf-8", errors="replace")
    try:
        t = get("https://www.spc.noaa.gov/climo/reports/" + name).text
    except Exception as e:  # noqa: BLE001
        print("  spc fail", name, e)
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    p.write_text(t, encoding="utf-8")
    return t


def parse_spc(text, day):
    recs, kind = [], None
    for line in text.splitlines():
        if line.startswith("Time,"):
            h = line.split(",")[1]
            kind = {"F_Scale": "torn", "Speed": "wind", "Size": "hail"}.get(h)
            continue
        if not kind or not line.strip():
            continue
        parts = line.split(",", 7)
        if len(parts) < 7:
            continue
        try:
            lat, lon = float(parts[5]), float(parts[6])
        except ValueError:
            continue
        mag = pd.to_numeric(parts[1], errors="coerce")
        recs.append((day, kind, mag, lat, lon))
    return recs


days = [START + dt.timedelta(days=i) for i in range((TODAY - START).days)]
print(f"SPC daily reports {days[0]} .. {days[-1]} ({len(days)} days)")
with ThreadPoolExecutor(8) as ex:
    texts = list(ex.map(spc_day, days))
recs = [r for d, t in zip(days, texts) for r in parse_spc(t, d)]
spc = pd.DataFrame(recs, columns=["day", "kind", "mag", "lat", "lon"])
print("  reports:", spc["kind"].value_counts().to_dict(), "days with data:", sum(1 for t in texts if t))
pts = gpd.GeoDataFrame(spc, geometry=gpd.points_from_xy(spc["lon"], spc["lat"]), crs=4326)
pts = gpd.sjoin(pts, counties[["fips", "geometry"]], how="inner", predicate="within")
hail = pts[pts["kind"] == "hail"].copy()
hail["inch"] = hail["mag"] / 100.0  # SPC size is in hundredths of an inch
g_h = hail.groupby("fips").agg(hail24=("inch", "size"), hail24_1in=("inch", lambda s: int((s >= 1).sum())),
                               hail24_2in=("inch", lambda s: int((s >= 2).sum())), hail24_max=("inch", "max"),
                               hail24_last=("day", "max"))
g_w = pts[pts["kind"] == "wind"].groupby("fips").size().rename("wind24")
g_t = pts[pts["kind"] == "torn"].groupby("fips").size().rename("torn24")
storms = pd.concat([g_h, g_w, g_t], axis=1).reset_index().rename(columns={"index": "fips"})
storms["hail24_last"] = storms["hail24_last"].astype(str).replace({"NaT": None, "nan": None})

# ---------------------------------------------------------------- FEMA National Risk Index
NRI_URL = ("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
           "National_Risk_Index_Counties/FeatureServer/0/query")
NRI_F = {"STCOFIPS": "fips", "RISK_SCORE": "nri_risk", "RISK_RATNG": "nri_rating", "EAL_VALT": "nri_eal",
         "SOVI_SCORE": "nri_sovi", "RESL_SCORE": "nri_resl", "HAIL_RISKS": "nri_hail", "SWND_RISKS": "nri_wind",
         "TRND_RISKS": "nri_torn", "IFLD_RISKS": "nri_rflood", "CFLD_RISKS": "nri_cflood",
         "WFIR_RISKS": "nri_fire", "HRCN_RISKS": "nri_hurr", "WNTW_RISKS": "nri_winter"}
nri_cache = CACHE / "nri_counties.json"
if nri_cache.exists():
    nri_rows = json.loads(nri_cache.read_text())
else:
    nri_rows, off = [], 0
    while True:
        j = get(NRI_URL, params={"where": "1=1", "outFields": ",".join(NRI_F), "returnGeometry": "false",
                                 "resultOffset": off, "resultRecordCount": 2000, "orderByFields": "OBJECTID",
                                 "f": "json"}).json()
        if "error" in j:
            raise RuntimeError(j["error"])
        feats = j.get("features", [])
        nri_rows += [f["attributes"] for f in feats]
        off += len(feats)
        if not feats or not j.get("exceededTransferLimit"):
            break
    nri_cache.write_text(json.dumps(nri_rows))
nri = pd.DataFrame(nri_rows).rename(columns=NRI_F)
print("NRI counties:", len(nri))

# ---------------------------------------------------------------- ACS 2024 5-year B25077 / B25035
def acs_table(tbl, col):
    p = CACHE / f"acsdt5y2024-{tbl}.dat"
    if not p.exists():
        url = f"https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/{p.name}"
        p.write_bytes(get(url).content)
    df = pd.read_csv(p, sep="|", usecols=["GEO_ID", col], dtype=str)
    df = df[df["GEO_ID"].str.startswith("0500000US")]
    df["fips"] = df["GEO_ID"].str[-5:]
    v = pd.to_numeric(df[col], errors="coerce")
    df[col] = v.where(v > 0)  # negative values are Census jam codes (no estimate)
    return df[["fips", col]]


acs = acs_table("b25077", "B25077_E001").merge(acs_table("b25035", "B25035_E001"), on="fips", how="outer")
acs.columns = ["fips", "home_value", "year_built"]
print("ACS counties:", len(acs))

# ---------------------------------------------------------------- merge
ov = pd.read_csv(NAT / "county_overlap.csv", dtype={"fips": str})
ov["rank"] = ov["overlap"].rank(ascending=False, method="first").astype(int)
df = ov.merge(storms, on="fips", how="left").merge(nri.drop_duplicates("fips"), on="fips", how="left") \
    .merge(acs, on="fips", how="left")
df = df.merge(counties[["fips", "STATE_NAME"]], on="fips", how="left")
for c in ["hail24", "hail24_1in", "hail24_2in", "wind24", "torn24"]:
    df[c] = df[c].fillna(0).astype(int)
df["layers"] = df["fips"].map(BLOCK_LAYERS)
print("missing NRI:", df["nri_risk"].isna().sum(), "missing ACS value:", df["home_value"].isna().sum())
print("  e.g. no NRI:", df.loc[df["nri_risk"].isna(), ["fips", "NAME", "STUSPS"]].head(12).values.tolist())

# centroids for fly-to / pins
cent = counties.set_index("fips").geometry.representative_point()
df["lon"] = df["fips"].map(cent.x).round(3)
df["lat"] = df["fips"].map(cent.y).round(3)

COLS = ["fips", "NAME", "STUSPS", "STATE_NAME", "rank", "overlap", "lidar_year", "lidar_density",
        "lidar_flights_since_2010", "lidar_years", "hail_1in_per_yr", "hail_2in_per_yr", "hail24", "hail24_1in",
        "hail24_2in", "hail24_max", "hail24_last", "wind24", "torn24", "nri_risk", "nri_rating", "nri_eal",
        "nri_hail", "nri_wind", "nri_torn", "nri_rflood", "nri_cflood", "nri_fire", "nri_hurr", "nri_winter",
        "nri_sovi", "nri_resl", "home_value", "year_built", "sf_detached", "owner_occ", "layers", "lon", "lat"]
RND = {"overlap": 1, "lidar_density": 2, "hail_1in_per_yr": 1, "hail_2in_per_yr": 1, "hail24_max": 2,
       "nri_risk": 1, "nri_hail": 1, "nri_wind": 1, "nri_torn": 1, "nri_rflood": 1, "nri_cflood": 1,
       "nri_fire": 1, "nri_hurr": 1, "nri_winter": 1, "nri_sovi": 1, "nri_resl": 1}


def clean(v, c):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if c == "lidar_years":
        return [int(x) for x in re.findall(r"\d{4}", str(v))]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if c in RND:
            return round(v, RND[c])
        return int(round(v)) if c in ("lidar_year", "home_value", "year_built", "nri_eal") else round(v, 3)
    return v


df = df.sort_values("rank")
rows = [[clean(r[c], c) for c in COLS] for r in df[COLS].to_dict("records")]
meta = {"generated": TODAY.isoformat(), "storm_window": [START.isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()],
        "n": len(rows),
        "sources": {
            "overlap": "Project overlap score (scripts/rank_counties.py): LiDAR recency/density/refresh, hail 2015-24, homes",
            "lidar": "USGS 3DEP LiDAR (Entwine EPT footprints + WESM) - public domain",
            "spc": "NOAA/NWS Storm Prediction Center daily filtered storm reports - public domain",
            "nri": "FEMA National Risk Index county table (ArcGIS feature service) - public domain",
            "acs": "US Census ACS 5-year 2024 B25077, B25035; ACS 2023 B25024, B25003 - public domain",
            "geom": "US Census cartographic boundary counties 1:5,000,000 (2023) - public domain"}}
(OUT / "counties.json").write_text(json.dumps({"cols": COLS, "rows": rows, "meta": meta}, separators=(",", ":")),
                                   encoding="utf-8")

# ---------------------------------------------------------------- geometry
def simp(g, tol):
    s = shapely.simplify(g, tol, preserve_topology=True)
    return shapely.set_precision(s, 0.001) if not s.is_empty else g


def write_geo(gdf, key, path, tol):
    feats = []
    for k, g in zip(gdf[key], gdf.geometry):
        s = simp(g, tol)
        if s.is_empty:
            s = shapely.set_precision(g, 0.001)
        feats.append({"type": "Feature", "properties": {"f": k}, "geometry": mapping(s)})
    txt = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
    txt = re.sub(r"(\d+\.\d{3})\d+", r"\1", txt)  # floating noise after set_precision
    path.write_text(txt, encoding="utf-8")
    print(f"  {path.name}: {len(feats)} features, {path.stat().st_size / 1e6:.2f} MB")


keep = set(df["fips"])
write_geo(counties[counties["fips"].isin(keep)], "fips", OUT / "counties.geojson", 0.006)
states = counties.dissolve(by="STUSPS", as_index=False)[["STUSPS", "geometry"]]
write_geo(states, "STUSPS", OUT / "states.geojson", 0.01)
print("done", OUT)

"""Stage 5 - per-home radar hail exposure and a 0-100 hail exposure score for Dallas County.

Inputs that remain (all clear-green): NOAA MRMS radar hail (public domain) at each home location, and the
home's Overture footprint area (ODbL). Nothing else is known about the home: no year built, roof material,
owner occupancy, value or re-roof permits (those came from red/grey sources and were removed).

So the score ranks HAIL EXPOSURE, not roof condition or likelihood to buy:
  biggest hail in the last 24 months  60
  how recent that hit is              25
  repeat 1 in+ hits in 24 months      10
  roof size (footprint area)           5

Output: data/dallas/scored.parquet, data/dallas/storms.csv (one row per hail day with homes affected)
"""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "dallas"
TODAY = pd.Timestamp(date.today())
IN = 25.4
SQFT = 10.7639
PITCH = 1.15  # assumed pitch factor (footprint -> sloped roof area); not measured
EAVES = 1.05  # assumed overhang beyond the footprint outline

homes = pd.read_parquet(D / "homes.parquet")
homes = homes[homes["lon"].notna()].reset_index(drop=True)
# ~11% of NAD points (mostly Irving and Seagoville) have no ZIP: use the Census 2020 ZCTA the home sits in
# (TIGERweb, public domain). ZCTAs approximate USPS ZIPs. Missing city: nearest home with one (~400 m).
import geopandas as gpd  # noqa: E402
import requests  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

ZC = D / "raw" / "zcta2020.geojson"
if not ZC.exists():
    r = requests.get("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2/query",
                     params={"geometry": f"{homes.lon.min()},{homes.lat.min()},{homes.lon.max()},{homes.lat.max()}",
                             "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
                             "spatialRel": "esriSpatialRelIntersects", "outFields": "ZCTA5", "f": "geojson"}, timeout=180)
    r.raise_for_status()
    ZC.write_text(r.text)
zc = gpd.read_file(ZC)[["ZCTA5", "geometry"]]
miss = homes["zip"].str.len() != 5
pts = gpd.GeoDataFrame(index=homes.index[miss], geometry=gpd.points_from_xy(homes.lon[miss], homes.lat[miss]), crs=4326)
hit = gpd.sjoin(pts, zc.to_crs(4326), how="left", predicate="within")
hit = hit[~hit.index.duplicated()]
homes.loc[hit.index, "zip"] = hit["ZCTA5"].fillna("").values
homes["zip_source"] = np.where(miss, "census_zcta", "nad")
for col, ok in (("city", homes["city"].str.len() > 0),):
    xy = np.column_stack([homes["lon"] * np.cos(np.radians(32.8)), homes["lat"]])
    dist, j = cKDTree(xy[ok.values]).query(xy[~ok.values], distance_upper_bound=0.004)
    src = homes.loc[ok, col].reset_index(drop=True)
    fill = pd.Series([src.iat[k] if np.isfinite(d) else "" for d, k in zip(dist, j)], index=homes.index[~ok])
    homes.loc[~ok, col] = fill
print("homes still without ZIP:", int((homes["zip"].str.len() != 5).sum()))

# ---------------------------------------------------------------- hail at each home, every hail day
files = sorted((D / "hail").glob("*.npz"))
hits, storms = [], []
for f in files:
    day = pd.Timestamp(f.stem)
    z = np.load(f)
    lats, lons, mesh = z["lats"], z["lons"], z["mesh"].astype(np.float32)
    r = np.clip(np.round((lats[0] - homes["lat"].values) / (lats[0] - lats[1])).astype(int), 0, len(lats) - 1)
    c = np.clip(np.round((homes["lon"].values - lons[0]) / (lons[1] - lons[0])).astype(int), 0, len(lons) - 1)
    v = np.minimum(mesh[r, c] / IN, 4.5)  # MESH overestimates in rare cores; cap at softball size
    idx = np.flatnonzero(v >= 0.75)
    hits.append(pd.DataFrame({"i": idx, "day": day, "inches": v[idx]}))
    storms.append({"storm_day": f.stem, "county_max_in": round(float(mesh.max()) / IN, 2),
                   "homes_075": int((v >= 0.75).sum()), "homes_100": int((v >= 1.0).sum()),
                   "homes_150": int((v >= 1.5).sum()), "homes_175": int((v >= 1.75).sum())})
H = pd.concat(hits, ignore_index=True) if hits else pd.DataFrame(columns=["i", "day", "inches"])
pd.DataFrame(storms).sort_values("storm_day").to_csv(D / "storms.csv", index=False)
print(f"{len(files)} hail days, {len(H):,} home-hits, {len(homes):,} homes")

agg = H.groupby("i").agg(hail_max_in=("inches", "max"), hail_days_1in=("inches", lambda s: int((s >= 1).sum())))
best = H.sort_values(["i", "inches", "day"], ascending=[True, False, False]).drop_duplicates("i").set_index("i")
agg["worst_hit_day"] = best["day"]
agg["last_1in_day"] = H[H["inches"] >= 1.0].groupby("i")["day"].max()
homes = homes.join(agg)
homes["hail_days_1in"] = homes["hail_days_1in"].fillna(0).astype(int)

# ---------------------------------------------------------------- hail exposure score (0-100) with reasons
recent = H[(TODAY - H["day"]).dt.days <= 730]
rbest = recent.sort_values(["i", "inches", "day"], ascending=[True, False, False]).drop_duplicates("i").set_index("i")
homes["recent_hail_in"] = rbest["inches"]
homes["recent_hit_day"] = rbest["day"]
homes["recent_days_1in"] = recent[recent["inches"] >= 1].groupby("i").size()
homes["recent_days_1in"] = homes["recent_days_1in"].fillna(0).astype(int)
size = homes["recent_hail_in"].fillna(0)
p_hail = np.select([size >= 1.75, size >= 1.5, size >= 1.25, size >= 1.0, size >= 0.75], [60, 51, 42, 30, 12], 0)
p_hail = np.maximum(p_hail, np.where(homes["hail_max_in"].fillna(0) >= 1.5, 8, 0))  # older big hit
age_days = (TODAY - homes["recent_hit_day"]).dt.days
p_recent = np.select([age_days <= 120, age_days <= 365, age_days <= 730], [25, 18, 10], 0)
p_repeat = np.select([homes["recent_days_1in"] >= 3, homes["recent_days_1in"] >= 2], [10, 6], 0)
homes["roof_squares"] = (homes["area_m2"] * SQFT * EAVES * PITCH / 100).round(1)
p_size = np.clip((homes["roof_squares"] - 15) / 25, 0, 1) * 5
homes["score"] = (p_hail + p_recent + p_repeat + p_size).round().astype(int)
homes["pts"] = [list(map(int, t)) for t in zip(p_hail, p_recent, p_repeat, np.round(p_size))]

cols = ["bid", "address", "city", "zip", "zip_source", "lon", "lat", "area_m2", "roof_squares", "hail_max_in", "hail_days_1in",
        "worst_hit_day", "last_1in_day", "recent_hail_in", "recent_hit_day", "recent_days_1in", "score", "pts"]
homes[cols].to_parquet(D / "scored.parquet", index=False)
print(homes["score"].describe())
print("homes with >=1 in hail in 24 months:", int((homes["recent_hail_in"].fillna(0) >= 1).sum()))

"""Score every US county on how many free data layers overlap there, then rank.

Layers that vary by place (scored):
  LiDAR   - newest collection, laser point density, how many times it has been re-flown (refresh rate)
  Hail    - damaging hail reports (>= 1 inch) per year, NOAA SPC 2015-2024 (event-driven demand)
  Homes   - single-family detached homes and owner-occupied homes (ACS 2023 5-year)
Layers that are the same everywhere in the US (not scored, but part of the stack):
  NOAA MRMS hail size every 2 min, NASA OPERA ground motion every 6-12 days,
  Sentinel-1/2 + Landsat imagery every few days, NAIP aerial photos every 2 years.
Open parcel + permit data is checked afterwards for the top counties (it has no national index).
"""
import io
import json
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.strtree import STRtree

N = Path(__file__).resolve().parents[1] / "data" / "national"
YEAR_NOW = 2026

# ---------------------------------------------------------------- counties
counties = gpd.read_file(f"zip://{N / 'counties.zip'}").to_crs(5070)  # equal-area metres
counties = counties[~counties["STATEFP"].isin(["60", "66", "69", "78"])]  # drop small territories
counties["fips"] = counties["GEOID"]
counties["area_m2"] = counties.area
print("counties:", len(counties))

# ---------------------------------------------------------------- LiDAR projects
wesm = pd.read_csv(N / "wesm.csv", usecols=["workunit", "project", "collect_end", "ql"])
wesm["year"] = pd.to_datetime(wesm["collect_end"], errors="coerce").dt.year
by_unit = dict(zip(wesm["workunit"], zip(wesm["year"], wesm["ql"])))
by_proj = dict(zip(wesm["project"], zip(wesm["year"], wesm["ql"])))

ept = gpd.read_file(N / "ept_boundaries.geojson").to_crs(5070)


def year_of(name):
    if name in by_unit and not np.isnan(by_unit[name][0]):
        return int(by_unit[name][0]), by_unit[name][1]
    if name in by_proj and not np.isnan(by_proj[name][0]):
        return int(by_proj[name][0]), by_proj[name][1]
    m = re.findall(r"(19|20)(\d\d)", name)
    if m:
        return int(m[-1][0] + m[-1][1]), None
    m = re.search(r"_[A-Z](\d\d)$", name)  # e.g. ..._B23, ..._D21
    if m:
        return 2000 + int(m.group(1)), None
    return None, None


ept[["year", "ql"]] = ept["name"].apply(lambda n: pd.Series(year_of(n)))
ept["area_m2"] = ept.area
ept["density"] = ept["count"] / ept["area_m2"]  # laser points per m2
print("lidar projects:", len(ept), "with year:", ept["year"].notna().sum())

tree = STRtree(ept.geometry.values)
rows = []
for c in counties.itertuples():
    hits = tree.query(c.geometry, predicate="intersects")
    best_year, best_density, years = None, 0.0, set()
    for j in hits:
        p = ept.iloc[j]
        if p["year"] is None or np.isnan(p["year"]):
            continue
        cover = p.geometry.intersection(c.geometry).area / c.area_m2
        if cover < 0.3:  # project must cover a real share of the county
            continue
        y = int(p["year"])
        years.add(y)
        if best_year is None or y > best_year or (y == best_year and p["density"] > best_density):
            best_year, best_density = y, float(p["density"])
    rows.append((c.fips, best_year, best_density, len([y for y in years if y >= 2010]), sorted(years)))
lid = pd.DataFrame(rows, columns=["fips", "lidar_year", "lidar_density", "lidar_flights_since_2010", "lidar_years"])

# ---------------------------------------------------------------- hail
with zipfile.ZipFile(N / "hail.zip") as z:
    hail = pd.read_csv(z.open(z.namelist()[0]))
hail = hail[(hail["yr"] >= 2015) & (hail["mag"] >= 1.0)]
hail["fips"] = hail["stf"].astype(int).map("{:02d}".format) + hail["f1"].astype(int).map("{:03d}".format)
hail_c = hail.groupby("fips").agg(hail_1in_per_yr=("mag", lambda s: len(s) / 10),
                                  hail_2in_per_yr=("mag", lambda s: (s >= 2.0).sum() / 10)).reset_index()

# ---------------------------------------------------------------- homes
def acs(table, cols):
    df = pd.read_csv(N / f"{table}.dat", sep="|", usecols=["GEO_ID"] + cols, dtype=str)
    df = df[df["GEO_ID"].str.startswith("0500000US")]
    df["fips"] = df["GEO_ID"].str[-5:]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop(columns="GEO_ID")


homes = acs("b25024", ["B25024_E002"]).merge(acs("b25003", ["B25003_E002"]), on="fips")
homes.columns = ["sf_detached", "fips", "owner_occ"] if homes.columns[0] != "fips" else homes.columns
homes = homes.rename(columns={"B25024_E002": "sf_detached", "B25003_E002": "owner_occ"})

# ---------------------------------------------------------------- score
df = counties[["fips", "NAME", "STUSPS", "area_m2"]].merge(lid, on="fips", how="left") \
    .merge(hail_c, on="fips", how="left").merge(homes, on="fips", how="left")
df = df.fillna({"hail_1in_per_yr": 0, "hail_2in_per_yr": 0, "lidar_density": 0, "lidar_flights_since_2010": 0})


def clip01(x):
    return np.clip(x, 0, 1)


df["s_recency"] = clip01((df["lidar_year"].fillna(2000) - 2015) / (2023 - 2015))
df["s_density"] = clip01(np.log2(df["lidar_density"].clip(lower=0.25) / 0.5) / np.log2(20 / 0.5))
df["s_refresh"] = clip01(df["lidar_flights_since_2010"] / 3)
df["s_hail"] = clip01(np.log1p(df["hail_1in_per_yr"]) / np.log1p(40))
df["s_homes"] = clip01(np.log10(df["sf_detached"].clip(lower=1000) / 1000) / np.log10(600))
S = ["s_recency", "s_density", "s_refresh", "s_hail", "s_homes"]
# geometric mean: a county must be decent on EVERY layer; one weak layer drags it down
df["overlap"] = np.exp(np.log(df[S].clip(lower=0.02)).mean(axis=1)) * 100

out = df.sort_values("overlap", ascending=False)
cols = ["fips", "NAME", "STUSPS", "overlap", "lidar_year", "lidar_density", "lidar_flights_since_2010",
        "hail_1in_per_yr", "hail_2in_per_yr", "sf_detached", "owner_occ"]
out[cols + ["lidar_years"]].to_csv(N / "county_overlap.csv", index=False)
pd.set_option("display.width", 200)
print(out[cols].head(30).to_string(index=False, float_format=lambda v: f"{v:,.1f}"))

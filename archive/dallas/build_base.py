"""Stage 1 - Dallas County home base table, from open data only (no appraisal-district data).

Sources (all clear-green):
  - Dallas County outline: US Census TIGERweb (US federal work, public domain, 17 U.S.C. 105)
  - Building footprints: Overture Maps buildings theme (ODbL; OSM, Microsoft ML footprints, Google Open
    Buildings CC BY 4.0, Esri Community Maps CC BY 4.0)
  - Street addresses: Overture Maps addresses theme, ONLY rows sourced from the USDOT National Address
    Database (public domain). NAD condition: it "is not intended for use as a mailing list".

What a "home" is here: a building footprint of 50-600 m2 (house-sized), not tagged as a non-residential
building, that has exactly one street address (number + street) inside it or within 25 m.
What is NOT known (no green source in Dallas County): year built, roof material, owner occupancy,
value, re-roof history. Roof area is the footprint area, not a measured roof.

Output: data/dallas/homes.parquet
"""
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import from_wkb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "dallas" / "raw"
OUT = ROOT / "data" / "dallas"
RELEASE = "2026-11-18.0"
S3 = f"s3://overturemaps-us-west-2/release/{RELEASE}"
NAD = "National Address Database"
# Overture building subtypes that are not homes (residential and untagged are kept)
NOT_HOME = ("agricultural", "civic", "commercial", "education", "entertainment", "industrial", "medical",
            "military", "outbuilding", "religious", "service", "transportation")
UTM = 32614  # metres


def county():
    r = requests.get("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query",
                     params={"where": "GEOID='48113'", "outSR": 4326, "f": "geojson", "outFields": "NAME,GEOID"},
                     timeout=120)
    r.raise_for_status()
    return gpd.GeoDataFrame.from_features(r.json()["features"], crs=4326).geometry.iloc[0]


def overture(sql, cache):
    if cache.exists():
        return pd.read_parquet(cache)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    df = con.execute(sql).df()
    df.to_parquet(cache, index=False)
    return df


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    poly = county()
    W, S, E, N = poly.bounds
    box = f"bbox.xmin > {W} AND bbox.xmax < {E} AND bbox.ymin > {S} AND bbox.ymax < {N}"

    b = overture(f"""SELECT id, ST_AsWKB(geometry) AS wkb, subtype, class, height, num_floors,
                            sources[1].dataset AS src
                     FROM read_parquet('{S3}/theme=buildings/type=building/*', hive_partitioning=1) WHERE {box}""",
                 RAW / "overture_buildings.parquet")
    print(f"Overture buildings in county box: {len(b):,}")
    a = overture(f"""SELECT id, number, street, unit, postcode, postal_city, ST_X(geometry) AS lon, ST_Y(geometry) AS lat,
                            sources[1].dataset AS src, sources[1].license AS lic
                     FROM read_parquet('{S3}/theme=addresses/type=address/*', hive_partitioning=1) WHERE {box}""",
                 RAW / "overture_addresses.parquet")
    print(f"Overture addresses in county box: {len(a):,}; by source:", a["src"].value_counts().to_dict())
    a = a[a["src"].eq(NAD) & a["number"].notna() & a["street"].notna()].copy()

    bg = gpd.GeoDataFrame(b.drop(columns="wkb"), geometry=from_wkb(b["wkb"].map(bytes)), crs=4326)
    bg = bg[bg.representative_point().within(poly)]
    bm = bg.to_crs(UTM)
    bg["area_m2"] = bm.area.round(1)
    ag = gpd.GeoDataFrame(a, geometry=gpd.points_from_xy(a["lon"], a["lat"]), crs=4326)
    ag = ag[ag.within(poly)].to_crs(UTM)
    print(f"in Dallas County: {len(bg):,} buildings, {len(ag):,} NAD addresses")

    # address -> building: inside the footprint, else nearest footprint within 25 m
    j = gpd.sjoin(ag[["id", "geometry"]], bm[["geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    miss = j["index_right"].isna()
    near = gpd.sjoin_nearest(ag.loc[miss[miss].index, ["geometry"]], bm[["geometry"]], how="left", max_distance=25)
    near = near[~near.index.duplicated()]
    j.loc[near.index, "index_right"] = near["index_right"]
    ag["bidx"] = j["index_right"]
    ag = ag[ag["bidx"].notna()]
    ag["street_addr"] = (ag["number"].str.strip() + " " + ag["street"].str.strip()).str.upper()
    per = ag.groupby("bidx").agg(n_addr=("street_addr", "nunique"), n_units=("id", "size"),
                                 address=("street_addr", "first"), zip=("postcode", "first"),
                                 city=("postal_city", "first"))

    h = bg.join(per, how="inner")
    house = (h["area_m2"].between(50, 600) & ~h["subtype"].isin(NOT_HOME) & h["n_addr"].eq(1) & h["n_units"].le(2)
             & (h["height"].isna() | (h["height"] < 15)))
    h = h[house].copy()
    c = h.geometry.representative_point()
    homes = pd.DataFrame({
        "bid": h["id"].values, "address": h["address"].str.title().values,
        "city": h["city"].fillna("").str.title().values, "zip": h["zip"].fillna("").str[:5].values,
        "lon": c.x.round(6).values, "lat": c.y.round(6).values, "area_m2": h["area_m2"].values,
        "subtype": h["subtype"].values, "bsrc": h["src"].values,
    })
    homes = homes.drop_duplicates("bid").reset_index(drop=True)
    print(f"homes (house-sized footprint with one NAD street address): {len(homes):,}")
    print("footprint sources:", homes["bsrc"].value_counts().to_dict())
    homes.to_parquet(OUT / "homes.parquet", index=False)
    print(homes.sample(3, random_state=1).T)


if __name__ == "__main__":
    main()

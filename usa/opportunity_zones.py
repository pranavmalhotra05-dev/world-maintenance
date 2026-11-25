"""Build national Qualified Opportunity Zone (QOZ) layers for the USA viewer.

Two designation rounds (both US federal government works; see web/usa/data/oz_licence.json):
  r="2018"  IRS Notice 2018-48 / 2019-42 list (CDFI Fund xlsx + shapefile, 2010 census tracts).
            In force now; ends 2028-12-31 (Puerto Rico deemed zones: 2027-12-31).
  r="2027"  Treasury/IRS designations announced 2026-11-05 (CDFI Fund final_oz2_designations.xlsx),
            2020 census tracts (Connecticut uses the 2022+ planning-region county codes),
            effective 2027-01-01 through 2036-12-31.  Geometry: Census TIGER 2024 tracts
            (same 2020 tract boundaries; CT planning-region GEOIDs match the list).

Outputs
  web/usa/data/oz_tracts.geojson   simplified display polygons, props {g, s, t, r, ru?}
  web/usa/data/oz_counties.json    {fips5: {n, share, r2018:{n,share}, r2027:{n,share,rural}}}
                                   keyed to the county codes used by web/usa/data/counties.geojson
Downloads are cached under usa/_cache/oz/ (git-ignored).

Run:  .venv\\Scripts\\python -u -W ignore usa/opportunity_zones.py
"""
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "oz"
OUT = ROOT / "web" / "usa" / "data"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) roofprint-usa/0.1 (open-data research)"}

SRC = {
    "oz2018_shp": ("oz2018_cdfi.zip",
                   "https://www.cdfifund.gov/system/files/documents/opportunity-zones=8764.-9-10-2019.zip"),
    "oz2018_xlsx": ("designated-qozs.12.14.18.xlsx",
                    "https://www.cdfifund.gov/system/files/documents/designated-qozs.12.14.18.xlsx"),
    "oz2027_xlsx": ("final_oz2_designations.xlsx",
                    "https://www.cdfifund.gov/system/files/2026-11/final_oz2_designations.xlsx"),
    "cb2024": ("cb_2024_us_tract_500k.zip",
               "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_tract_500k.zip"),
    "gaz2010": ("Gaz_tracts_national.zip",
                "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_tracts_national.zip"),
}
# 2010 county retired since: Bedford city VA (51515) folded into Bedford County (51019) in 2013;
# the CDFI shapefile still carries 51515050100 while Notice 2018-48 / the xlsx list 51019050100.
GEOID_FIX_2018 = {"51515050100": "51019050100"}
FIPS_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
    "72": "PR", "78": "VI",
}
SIMPLIFY_DEG = 0.002
MAX_BYTES = 8_000_000


def fetch(key):
    name, url = SRC[key]
    p = CACHE / name
    if not p.exists() or p.stat().st_size < 1000:
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {url}")
        r = requests.get(url, headers=UA, timeout=600)
        r.raise_for_status()
        if r.content[:15].lower().startswith(b"<!doctype html"):
            raise RuntimeError(f"{url} returned HTML, not data")
        p.write_bytes(r.content)
    return p


def load_2018():
    """Full-resolution 2018 zones (EPSG:4326) with GEOID + tract type."""
    shp = gpd.read_file(f"zip://{fetch('oz2018_shp').as_posix()}!8764oz.shp").to_crs(4326)
    shp["g"] = shp["CENSUSTRAC"].map(lambda x: GEOID_FIX_2018.get(x, x))
    lst = pd.read_excel(fetch("oz2018_xlsx"), header=4, dtype=str)
    lst.columns = ["state", "county", "geoid", "type", "acs"]
    lst = lst.dropna(subset=["geoid"])
    tmap = {"Low-Income Community": "low-income", "Non-LIC Contiguous": "contiguous"}
    lst["t"] = lst["type"].map(tmap)
    shp = shp.merge(lst[["geoid", "t"]], left_on="g", right_on="geoid", how="left")
    shp["r"] = "2018"
    shp["s"] = shp["g"].str[:2].map(FIPS_ABBR)
    missing = set(lst.geoid) - set(shp.g)
    return shp[["g", "s", "t", "r", "geometry"]], lst, missing


def load_2027(cb=None):
    lst = pd.read_excel(fetch("oz2027_xlsx"), dtype=str)
    lst.columns = ["geoid", "qozid", "state", "county", "rural"]
    lst = lst.dropna(subset=["geoid"])
    if cb is None:
        cb = gpd.read_file(f"zip://{fetch('cb2024').as_posix()}")
    g = cb[cb.GEOID.isin(lst.geoid)].to_crs(4326).merge(lst, left_on="GEOID", right_on="geoid")
    g["g"] = g["GEOID"]
    g["s"] = g["g"].str[:2].map(FIPS_ABBR)
    g["t"] = "low-income"  # OBBBA (Pub. L. 119-21) dropped the contiguous-tract option; all 2027 QOZs are LICs
    g["r"] = "2027"
    g["ru"] = (g["rural"].str.strip().str.lower() == "rural").astype(int)
    missing = set(lst.geoid) - set(g.g)
    return g[["g", "s", "t", "r", "ru", "geometry"]], lst, missing, cb


def _round_geom(geom, nd):
    geom = shapely.transform(geom, lambda a: np.round(a, nd))
    geom = shapely.make_valid(geom) if not geom.is_valid else geom
    return geom


def write_geojson(frames, tol, nd):
    feats = []
    for df in frames:
        simp = shapely.simplify(df.geometry.values, tol, preserve_topology=True)
        for row, geom in zip(df.itertuples(index=False), simp):
            geom = _round_geom(geom, nd)
            if geom.is_empty:
                geom = _round_geom(row.geometry.representative_point().buffer(tol), nd)
            # keep only polygonal parts
            if geom.geom_type == "GeometryCollection":
                geom = shapely.union_all([p for p in geom.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
            props = {"g": row.g, "s": row.s, "t": row.t, "r": row.r}
            if getattr(row, "ru", 0):
                props["ru"] = 1
            feats.append({"type": "Feature", "properties": props, "geometry": mapping(geom)})
    fc = {"type": "FeatureCollection",
          "meta": {"source": "CDFI Fund / IRS Notice 2018-48 + 2019-42 (r=2018); CDFI Fund final_oz2_designations "
                             "(announced 2026-11-05, r=2027); geometry: CDFI 2018 QOZ shapefile (2010 tracts), "
                             "Census cartographic boundary 2024 tracts 1:500k (2020 tracts). See oz_licence.json.",
                   "simplify_deg": tol, "decimals": nd,
                   "props": {"g": "11-digit census tract GEOID (2010 tracts for r=2018, 2020 tracts for r=2027)",
                             "s": "state/territory USPS code", "t": "low-income | contiguous",
                             "r": "designation round (2018: in force to 2028-12-31; 2027: 2027-01-01..2036-12-31)",
                             "ru": "1 = rural QOZ (2027 round)"}},
          "features": feats}
    p = OUT / "oz_tracts.geojson"
    p.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    return p, p.stat().st_size, len(feats)


def county_stats(z18, z27, cb):
    """County counts keyed to counties.geojson codes (CT planning regions, AK 2019 split)."""
    map_counties = {f["properties"]["f"] for f in json.load(open(OUT / "counties.geojson"))["features"]}
    cbp = cb.to_crs(4326)[["GEOID", "geometry"]]

    zf = zipfile.ZipFile(fetch("gaz2010"))
    gaz = pd.read_csv(zf.open(zf.namelist()[0]), sep="\t", dtype=str)
    gaz.columns = [c.strip() for c in gaz.columns]
    gaz["GEOID"] = gaz["GEOID"].map(lambda x: GEOID_FIX_2018.get(x, x))
    gaz["c"] = gaz["GEOID"].str[:5]
    # 2010 counties that no longer exist on the map -> locate by the tract's internal point in 2024 tracts
    bad = ~gaz["c"].isin(map_counties)
    if bad.any():
        pts = gpd.GeoDataFrame(gaz[bad], geometry=gpd.points_from_xy(gaz[bad].INTPTLONG.astype(float),
                                                                      gaz[bad].INTPTLAT.astype(float)), crs=4326)
        j = gpd.sjoin(pts, cbp, how="left", predicate="within")
        j = j[~j.index.duplicated()]
        gaz.loc[bad, "c"] = j["GEOID_right"].str[:5].values
    c2010 = gaz.set_index("GEOID")["c"]
    tot2010 = gaz.groupby("c").size()

    z18 = z18.copy()
    z18["c"] = z18["g"].map(c2010)
    nomap = z18["c"].isna() | ~z18["c"].isin(map_counties)
    if nomap.any():  # island areas (not in 2010 gazetteer) / leftovers: representative point join
        pts = gpd.GeoDataFrame(z18[nomap][["g"]], geometry=z18[nomap].geometry.representative_point(), crs=4326)
        j = gpd.sjoin(pts, cbp, how="left", predicate="within")
        j = j[~j.index.duplicated()]
        z18.loc[nomap, "c"] = j["GEOID"].str[:5].fillna(z18.loc[nomap, "g"].str[:5]).values
    tot2020 = cb.groupby(cb.GEOID.str[:5]).size()
    z27 = z27.copy()
    z27["c"] = z27["g"].str[:5]

    out = {}
    for c, grp in z18.groupby("c"):
        tot = int(tot2010.get(c, 0))
        out.setdefault(c, {})["r2018"] = {"n": len(grp), "share": round(len(grp) / tot, 4) if tot else None,
                                          "contig": int((grp.t == "contiguous").sum())}
    for c, grp in z27.groupby("c"):
        tot = int(tot2020.get(c, 0))
        out.setdefault(c, {})["r2027"] = {"n": len(grp), "share": round(len(grp) / tot, 4) if tot else None,
                                          "rural": int(grp.ru.sum())}
    for c, v in out.items():  # headline n/share = zones in force today (2018 round, through 2028-12-31)
        a = v.get("r2018", {"n": 0, "share": 0.0})
        v["n"], v["share"] = a["n"], a["share"]
    out = dict(sorted(out.items()))
    p = OUT / "oz_counties.json"
    p.write_text(json.dumps({"meta": {"n_share": "2018-round zones in force today (expire 2028-12-31); "
                                               "share = designated / all census tracts in county (2010 tracts)",
                                      "r2027": "zones effective 2027-01-01 (share vs 2020 tracts)",
                                      "keys": "county FIPS as in counties.geojson (CT planning regions)",
                                      "unmapped": sorted(set(out) - map_counties)},
                             "counties": out}, separators=(",", ":")), encoding="utf-8")
    return p, out


def main():
    print("loading 2018 round ...")
    z18, l18, miss18 = load_2018()
    print(f"  2018: {len(z18)} tract polygons, list rows {len(l18)}, list GEOIDs without geometry: {sorted(miss18)}")
    print("loading 2027 round ...")
    z27, l27, miss27, cb = load_2027()
    print(f"  2027: {len(z27)} tract polygons, list rows {len(l27)}, without geometry: {sorted(miss27)}")

    tol, nd = SIMPLIFY_DEG, 4
    p, size, n = write_geojson([z18, z27], tol, nd)
    print(f"  {p.name}: {n} features, {size/1e6:.2f} MB (tol {tol}, {nd} dp)")
    for tol2, nd2 in ((0.002, 3), (0.003, 3), (0.005, 3)):
        if size <= MAX_BYTES:
            break
        tol, nd = tol2, nd2
        p, size, n = write_geojson([z18, z27], tol, nd)
        print(f"  over budget -> {p.name}: {size/1e6:.2f} MB (tol {tol}, {nd} dp)")

    pc, counties = county_stats(z18, z27, cb)
    print(f"  {pc.name}: {len(counties)} counties, {pc.stat().st_size/1e3:.0f} KB")

    # sanity checks
    print("sanity:")
    print("  total 2018:", len(z18), " total 2027:", len(z27))
    for st in ("MN", "OH", "TX"):
        print(f"  {st}: 2018={int((z18.s == st).sum())}  2027={int((z27.s == st).sum())}")
    print("  2018 type counts:", z18.t.value_counts(dropna=False).to_dict())
    known = "39049001400"  # Ohio, Franklin County - listed in Notice 2018-48
    print(f"  known OH tract {known} (Notice 2018-48) present:", known in set(z18.g))
    print("  Franklin Co OH:", counties.get("39049"), " Hennepin MN:", counties.get("27053"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

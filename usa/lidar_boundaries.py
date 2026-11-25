"""Build the area-selection boundaries for the LiDAR viewer (web/lidar/index.html 'Select area').

Source: US Census Bureau cartographic boundary files (latest vintage found, 1:500k), US public domain:
  cb_<YYYY>_us_county_500k.zip  -> web/lidar/boundaries/counties_<ST>.json
  cb_<YYYY>_us_place_500k.zip   -> web/lidar/boundaries/places_<ST>.json   (incorporated places only, LSAD != 57)
  + web/lidar/boundaries/index.json (state list: fips, abbr, bbox, file sizes, vintage, licence)

Geometry: EPSG:4269 (NAD83 ~ WGS84 at this scale), simplified with Shapely (preserve topology) to 0.0002 deg
(~20 m) and rounded to 1e-5 deg (~1 m). Display / selection outlines only; no data is derived from them.

Run:  .venv\\Scripts\\python -u -W ignore usa/lidar_boundaries.py [--tol 0.0002] [--vintage 2025]
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "lidar" / "boundaries"
CACHE = ROOT / "usa" / "_cache" / "lidar_boundaries"
BASE = "https://www2.census.gov/geo/tiger/GENZ{y}/shp/cb_{y}_us_{layer}_500k.zip"
UA = {"User-Agent": "roofprint-usa/0.1 (open-data research)"}
MAX_BYTES = 3_000_000
LICENCE = {
    "source": "US Census Bureau, Cartographic Boundary Files (1:500,000), counties and places",
    "url": "https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
    "licence": "US public domain (17 U.S.C. 105, work of the US Government); commercial use allowed",
    "quote": "17 U.S.C. 105: \"Copyright protection under this title is not available for any work of the United States Government\"",
}


def fetch(layer, vintage):
    CACHE.mkdir(parents=True, exist_ok=True)
    years = [vintage] if vintage else list(range(2026, 2019, -1))
    for y in years:
        p = CACHE / f"cb_{y}_us_{layer}_500k.zip"
        if p.exists() and p.stat().st_size > 1000:
            return y, p
        url = BASE.format(y=y, layer=layer)
        r = requests.head(url, headers=UA, timeout=60)
        if r.status_code != 200:
            continue
        print(f"download {url}")
        r = requests.get(url, headers=UA, timeout=600)
        r.raise_for_status()
        p.write_bytes(r.content)
        return y, p
    raise SystemExit(f"no cartographic boundary file found for {layer}")


def rnd(o, nd=5):
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)):
            return [round(o[0], nd), round(o[1], nd)]
        return [rnd(x, nd) for x in o]
    return o


def feature(row, geom, kind):
    b = [round(v, 5) for v in geom.bounds]
    g = mapping(geom)
    props = {"g": row["GEOID"], "n": row["NAME"], "ln": row.get("NAMELSAD") or row["NAME"], "st": row["STUSPS"],
             "al": int(row.get("ALAND") or 0), "aw": int(row.get("AWATER") or 0), "b": b, "k": kind}
    return {"type": "Feature", "properties": props, "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])}}


def dump(path, feats, tol):
    fc = {"type": "FeatureCollection", "features": feats}
    s = json.dumps(fc, separators=(",", ":"))
    path.write_text(s, encoding="utf-8")
    return len(s.encode())


def build(layer, kind, vintage, tol):
    y, p = fetch(layer, vintage)
    gdf = gpd.read_file(f"zip://{p}")
    if kind == "place":
        gdf = gdf[gdf["LSAD"].astype(str) != "57"]  # incorporated places (and consolidated/unified govts); LSAD 57 = census-designated place, excluded
    print(f"{layer} {y}: {len(gdf)} features")
    out = {}
    for st, part in gdf.groupby("STUSPS"):
        t = tol
        while True:
            feats = []
            for _, row in part.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                gs = geom.simplify(t, preserve_topology=True)
                if gs.is_empty:
                    gs = geom
                feats.append(feature(row, gs, kind))
            fp = OUT / f"{'counties' if kind == 'county' else 'places'}_{st}.json"
            n = dump(fp, feats, t)
            if n <= MAX_BYTES or t > 0.005:
                break
            t *= 1.5  # keep every file <= 3 MB
        out[st] = {"file": fp.name, "n": len(feats), "bytes": n, "tol": round(t, 6),
                   "fips": str(part["STATEFP"].iloc[0]), "bbox": [round(v, 4) for v in part.total_bounds]}
    return y, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.0002)
    ap.add_argument("--vintage", type=int, default=None)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    yc, counties = build("county", "county", a.vintage, a.tol)
    yp, places = build("place", "place", a.vintage, a.tol)
    states = {}
    for st in sorted(set(counties) | set(places)):
        c, p = counties.get(st), places.get(st)
        base = c or p
        states[st] = {"fips": base["fips"], "bbox": base["bbox"], "counties": c and c["file"], "places": p and p["file"],
                      "n_counties": c and c["n"], "n_places": p and p["n"],
                      "bytes": (c["bytes"] if c else 0) + (p["bytes"] if p else 0),
                      "tol": max(c["tol"] if c else 0, p["tol"] if p else 0)}
    idx = {"vintage_counties": yc, "vintage_places": yp, "simplify_deg": a.tol, "round_deg": 1e-5,
           "places_filter": "incorporated places only (LSAD != 57); census-designated places excluded",
           "licence": LICENCE, "states": states}
    (OUT / "index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    big = max(states.items(), key=lambda kv: kv[1]["bytes"])
    tot = sum(v["bytes"] for v in states.values())
    print(f"wrote {len(states)} states, {tot / 1e6:.1f} MB total; largest {big[0]} {big[1]['bytes'] / 1e6:.2f} MB (two files)")


if __name__ == "__main__":
    sys.exit(main())

"""Map layers for the 3D block, to sit under and around the LiDAR points.

  python scene.py          (run after scan3d.py and score.py; reads web/dallas/data/scan.json + scan_*.txt,
                            data/dallas/scored.parquet)

aerial.jpg       USGS National Map orthoimagery (public domain), Web-Mercator aligned to the block bounds
buildings.json   Overture Maps building footprints (buildings theme, ODbL) for the block, each raised to the
                 height measured from the LiDAR roof points inside it; homes carry their USDOT NAD address
                 (public domain, via Overture addresses) and radar hail exposure score from score.py
streets.json     Overture Maps transportation road segments (ODbL, from OpenStreetMap) with names, z on the ground
"""
import base64
import json
from pathlib import Path

import duckdb
import numpy as np
import requests
from pyproj import Transformer
from shapely import contains_xy, from_wkb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "dallas" / "data"
RELEASE = "2026-11-18.0"
meta = json.load(open(OUT / "scan.json"))
lon0, lat0 = meta["origin"]
half = meta["half_m"]
to_m = Transformer.from_crs(4326, 3857, always_xy=True)
to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
cx, cy = to_m.transform(lon0, lat0)
k = 1 / np.cos(np.radians(lat0))
X0, Y0, X1, Y1 = cx - half * k, cy - half * k, cx + half * k, cy + half * k
W_, S_ = to_ll.transform(X0, Y0)
E_, N_ = to_ll.transform(X1, Y1)

# ---------------------------------------------------------------- aerial photo
r = requests.get("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export",
                 params={"bbox": f"{X0},{Y0},{X1},{Y1}", "bboxSR": 3857, "imageSR": 3857, "size": "2048,2048",
                         "format": "jpg", "f": "image"}, timeout=180)
r.raise_for_status()
assert r.headers.get("content-type", "").startswith("image"), r.text[:200]
(OUT / "aerial.jpg").write_bytes(r.content)
print(f"aerial.jpg {len(r.content) / 1e6:.1f} MB")

# ---------------------------------------------------------------- LiDAR points (decode our own pack)
pts = []
for i in range(meta["parts"]):
    b = base64.b64decode((OUT / f"scan_{i}.txt").read_text())
    pts.append(np.frombuffer(b, dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("i", "u1"), ("s", "u1")]))
p = np.concatenate(pts)
plon = lon0 + p["x"] * meta["unit_deg"]
plat = lat0 + p["y"] * meta["unit_deg"]
pz = p["z"] / 100.0
kind = p["k"] >> 6
hag = (p["k"] & 63) / 1.5
roof = kind == 1
ground = kind == 0

# ---------------------------------------------------------------- Overture buildings in the block
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
rows = con.execute(f"""
  SELECT id, ST_AsWKB(geometry) AS wkb, height, num_floors, sources[1].dataset AS src
  FROM read_parquet('s3://overturemaps-us-west-2/release/{RELEASE}/theme=buildings/type=building/*', hive_partitioning=1)
  WHERE bbox.xmin > {W_} AND bbox.xmax < {E_} AND bbox.ymin > {S_} AND bbox.ymax < {N_}
""").fetchall()
print(f"{len(rows)} Overture buildings")

sc = pd.read_parquet(ROOT / "data" / "dallas" / "scored.parquet")
sc = sc[sc.lon.between(W_, E_) & sc.lat.between(S_, N_)].set_index("bid")

# ground elevation lookup: median ground z within ~6 m of a point
gl, ga, gz = plon[ground], plat[ground], pz[ground]
cell = 0.00006  # ~6 m
gkey = np.round(gl / cell).astype(np.int64) * 1_000_003 + np.round(ga / cell).astype(np.int64)
order = np.argsort(gkey)
uk, st = np.unique(gkey[order], return_index=True)
gmed = np.array([np.median(v) for v in np.split(gz[order], st[1:])])
G = dict(zip(uk.tolist(), gmed.tolist()))


def ground_at(lo, la):
    kk = int(round(lo / cell)) * 1_000_003 + int(round(la / cell))
    for d in (0, 1, -1, 1_000_003, -1_000_003):
        if kk + d in G:
            return G[kk + d]
    return float(np.median(gz))


out = []
rl, ra, rh = plon[roof], plat[roof], hag[roof]
for bid, wkb, h_ov, floors, src in rows:  # noqa: B007
    g = from_wkb(bytes(wkb))
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    if g.geom_type != "Polygon":
        continue
    minx, miny, maxx, maxy = g.bounds
    sel = (rl >= minx) & (rl <= maxx) & (ra >= miny) & (ra <= maxy)
    inside = contains_xy(g, rl[sel], ra[sel])
    hh = rh[sel][inside]
    if len(hh) >= 8:
        top, eave = float(np.percentile(hh, 92)), float(np.percentile(hh, 30))
        how = "lidar"
    elif h_ov:
        top = eave = float(h_ov)
        how = "overture"
    else:
        top = eave = 3.5
        how = "default"
    c = g.centroid
    base = ground_at(c.x, c.y)
    rec = {"ring": [[round(x, 7), round(y, 7)] for x, y in g.exterior.coords], "base": round(base, 2),
           "eave": round(max(eave, 2.0), 2), "top": round(max(top, 2.4), 2), "how": how,
           "n": int(len(hh)), "source": src, "address": None, "score": None}
    if bid in sc.index:
        h = sc.loc[bid]
        rec.update({"address": h.address, "city": h.city, "zip": h.zip, "score": int(h.score),
                    "pts": [int(v) for v in h.pts], "lon": float(h.lon), "lat": float(h.lat),
                    "hail": 0.0 if pd.isna(h.recent_hail_in) else round(float(h.recent_hail_in), 2),
                    "hit_day": None if pd.isna(h.recent_hit_day) else h.recent_hit_day.strftime("%Y-%m-%d"),
                    "hail_ever": 0.0 if pd.isna(h.hail_max_in) else round(float(h.hail_max_in), 2),
                    "hail_days": int(h.recent_days_1in), "squares": float(h.roof_squares), "area": int(round(h.area_m2))})
    out.append(rec)
json.dump(out, open(OUT / "buildings.json", "w"), separators=(",", ":"))
hs = np.array([b["top"] for b in out if b["how"] == "lidar"])
print(f"{len(out)} buildings, {len(hs)} measured from LiDAR (median top {np.median(hs):.1f} m), "
      f"{sum(b['address'] is not None for b in out)} are scored homes")

# ---------------------------------------------------------------- streets (Overture transportation, ODbL)
segs = con.execute(f"""
  SELECT ST_AsWKB(geometry) AS wkb, names.primary AS name, class
  FROM read_parquet('s3://overturemaps-us-west-2/release/{RELEASE}/theme=transportation/type=segment/*', hive_partitioning=1)
  WHERE subtype = 'road' AND bbox.xmax > {W_} AND bbox.xmin < {E_} AND bbox.ymax > {S_} AND bbox.ymin < {N_}
    AND class NOT IN ('footway', 'cycleway', 'path', 'steps', 'track', 'bridleway', 'driveway', 'parking_aisle')
""").fetchall()
streets = []
for wkb, name, cls in segs:
    g = from_wkb(bytes(wkb))
    for line in (g.geoms if g.geom_type == "MultiLineString" else [g]):
        pts = [(x, y) for x, y in line.coords if W_ <= x <= E_ and S_ <= y <= N_]
        if len(pts) >= 2:
            streets.append({"name": name, "path": [[round(x, 6), round(y, 6), round(ground_at(x, y) + 0.4, 2)] for x, y in pts]})
json.dump(streets, open(OUT / "streets.json", "w"), separators=(",", ":"))
print(f"{len(streets)} street segments (Overture / OSM)")
json.dump({**meta, "bounds": [W_, S_, E_, N_]}, open(OUT / "scan.json", "w"))

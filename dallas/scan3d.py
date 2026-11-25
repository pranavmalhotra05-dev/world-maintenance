"""Full-density 3D scene for one Dallas block: USGS 3DEP LiDAR points (public domain).

  python scan3d.py --lon -96.8877 --lat 32.7260 --half-m 280

Writes web/dallas/data/:
  scan_0.txt, scan_1.txt ...  points, base64, 8 bytes each:
      int16 dlon, int16 dlat (units 2e-7 deg), uint16 z (cm above block floor),
      uint8 class (0 ground, 1 roof, 2 canopy, 3 other) * 64 + height above ground (*1.5, 0-63),
      uint8 laser intensity (0-255, scaled)
  scan.json      origin, counts, parts
  (streets.json and buildings.json are written by scene.py from Overture Maps; no parcel data is used)
Nothing is thinned: every laser return in the block is kept (noise removed).
"""
import argparse
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import laspy
import numpy as np
import pandas as pd
import requests
from pyproj import Transformer
from scipy import ndimage

BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/TX_Pecos_Dallas_B3_2018"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "dallas" / "data"
UNIT = 2e-7
PART = 800_000  # points per file (keeps each file under the 16 MB publish limit)
S = requests.Session()


def nodes_in(root, box):
    def b(d, x, y):
        s = 2 ** d
        w, h = (root[3] - root[0]) / s, (root[4] - root[1]) / s
        return root[0] + x * w, root[1] + y * h, root[0] + (x + 1) * w, root[1] + (y + 1) * h
    found, todo = [], ["0-0-0-0"]
    while todo:
        k0 = todo.pop()
        for k, n in S.get(f"{BASE}/ept-hierarchy/{k0}.json", timeout=60).json().items():
            d, x, y, _ = map(int, k.split("-"))
            bb = b(d, x, y)
            if bb[2] < box[0] or bb[0] > box[2] or bb[3] < box[1] or bb[1] > box[3]:
                continue
            if n == -1:
                if k != k0:
                    todo.append(k)
            else:
                found.append(k)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--half-m", type=float, default=280)
    a = ap.parse_args()
    to_m = Transformer.from_crs(4326, 3857, always_xy=True)
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    cx, cy = to_m.transform(a.lon, a.lat)
    k = 1 / np.cos(np.radians(a.lat))
    box = (cx - a.half_m * k, cy - a.half_m * k, cx + a.half_m * k, cy + a.half_m * k)
    root = S.get(f"{BASE}/ept.json", timeout=60).json()["bounds"]
    keys = nodes_in(root, box)
    print(f"{len(keys)} octree nodes")

    def fetch(key):
        las = laspy.read(io.BytesIO(S.get(f"{BASE}/ept-data/{key}.laz", timeout=180).content))
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= box[0]) & (x <= box[2]) & (y >= box[1]) & (y <= box[3])
        return (x[m], y[m], np.asarray(las.z)[m], np.asarray(las.classification)[m],
                np.asarray(las.number_of_returns)[m], np.asarray(las.intensity)[m])

    with ThreadPoolExecutor(12) as ex:
        parts = list(ex.map(fetch, keys))
    x, y, z, cl, nr, it = (np.concatenate([p[i] for p in parts]) for i in range(6))
    keep = ~np.isin(cl, [7, 18])
    x, y, z, cl, nr, it = x[keep], y[keep], z[keep], cl[keep], nr[keep], it[keep]
    area = (2 * a.half_m) ** 2
    print(f"{len(x):,} points = {len(x) / area:.1f} per m2")

    # ground model at 1 m (true metres), gaps filled from nearest ground
    gx = ((x - box[0]) / k).astype(int); gy = ((y - box[1]) / k).astype(int)
    W, H = gx.max() + 1, gy.max() + 1
    g = cl == 2
    G = np.full(W * H, np.nan)
    flat = (gx + gy * W)[g]; o = np.argsort(flat)
    u, st = np.unique(flat[o], return_index=True)
    G[u] = np.minimum.reduceat(z[g][o], st)
    G = G.reshape(H, W)
    _, (iy, ix) = ndimage.distance_transform_edt(np.isnan(G), return_indices=True)
    G = ndimage.median_filter(G[iy, ix], size=3)
    hag = np.clip(z - G[gy, gx], 0, 60)
    z0 = float(np.percentile(G, 0.5))

    roof = (~g) & (hag > 2.2) & (nr == 1)
    veg = (~g) & (hag > 1.0) & (nr > 1)
    kind = np.select([g, roof, veg], [0, 1, 2], 3)
    # intensity: stretch the 2-98 percentile range of ground returns to 0-255
    lo, hi = np.percentile(it[g], [2, 98])
    inten = np.clip((it.astype(float) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)

    # ---- surface shading (what makes a point cloud read as 3D): hillshade of a 0.5 m surface model,
    # darkened where a point sits beside or below something taller (eye-dome-lighting style edges)
    R = 0.5
    sx = ((x - box[0]) / k / R).astype(int); sy = ((y - box[1]) / k / R).astype(int)
    SW, SH = sx.max() + 1, sy.max() + 1
    idx = sx + sy * SW
    top = np.full(SW * SH, -np.inf); np.maximum.at(top, idx, z)
    top = top.reshape(SH, SW)
    holes = ~np.isfinite(top)
    _, (iy2, ix2) = ndimage.distance_transform_edt(holes, return_indices=True)
    top = top[iy2, ix2]
    dzdy, dzdx = np.gradient(top, R)
    az, alt = np.radians(315), np.radians(42)  # sun from the north-west, like most hillshades
    slope = np.arctan(np.hypot(dzdx, dzdy)); aspect = np.arctan2(-dzdx, dzdy)
    hill = np.clip(np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect), 0, 1)
    gdz_y, gdz_x = np.gradient(G, 1.0)
    gs = np.arctan(np.hypot(gdz_x, gdz_y)); ga = np.arctan2(-gdz_x, gdz_y)
    ghill = np.clip(np.sin(alt) * np.cos(gs) + np.cos(alt) * np.sin(gs) * np.cos(az - ga), 0, 1)
    near_max = ndimage.maximum_filter(top, size=5)  # tallest thing within ~1 m
    zt = top[sy, sx]
    below = z < zt - 1.2  # under a canopy or eave: light it as ground
    shade = np.where(below, ghill[gy, gx] * 0.75, hill[sy, sx])
    occl = np.clip((near_max[sy, sx] - z) / 4.0, 0, 1)
    shade = np.clip(shade * (1 - 0.55 * occl), 0, 1)
    shade_b = np.round(shade * 255).astype(np.uint8)

    lon, lat = to_ll.transform(x, y)
    q = np.zeros(len(x), dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("i", "u1"), ("s", "u1")])
    q["x"] = np.round((lon - a.lon) / UNIT); q["y"] = np.round((lat - a.lat) / UNIT)
    q["z"] = np.clip(np.round((z - z0) * 100), 0, 65535)
    q["k"] = kind.astype(np.uint8) * 64 + np.clip(np.round(hag * 1.5), 0, 63).astype(np.uint8)
    q["i"] = inten
    q["s"] = shade_b
    rng = np.random.default_rng(1)
    q = q[rng.permutation(len(q))]  # any prefix is an even sample, so the page can draw part 0 first
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("scan_*.txt"):
        f.unlink()
    nparts = int(np.ceil(len(q) / PART))
    for p in range(nparts):
        (OUT / f"scan_{p}.txt").write_text(base64.b64encode(q[p * PART:(p + 1) * PART].tobytes()).decode())
    (OUT / "scan.txt").unlink(missing_ok=True)

    # streets (Overture transportation, ODbL) and buildings linked to scored homes are added by scene.py
    json.dump({"origin": [a.lon, a.lat], "count": int(len(q)), "parts": nparts, "unit_deg": UNIT, "bytes_per_point": 9,
               "z_base": z0, "half_m": a.half_m, "pts_per_m2": round(len(q) / area, 1),
               "lidar": "USGS 3DEP TX_Pecos_Dallas_B3_2018 (flown 2018-19)"}, open(OUT / "scan.json", "w"))
    for f in sorted(OUT.glob("scan_*.txt")):
        print(f"  {f.name}: {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

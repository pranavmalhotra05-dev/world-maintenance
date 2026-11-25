"""Stream classified USGS LiDAR for one Dallas hotspot and pack it for the 3D view.

  python lidar_hotspot.py --lon -96.8877 --lat 32.7260 --dry-run
  python lidar_hotspot.py --lon -96.8877 --lat 32.7260 --depth 11

Output (web/dallas/data/): scan.txt (base64, 7 bytes a point), scan.json (origin, count, encoding),
roofs.json (per home in the hotspot - Overture footprint + NAD address from score.py: roof height above
ground and LiDAR roof-area estimate).
Classes in this survey follow ASPRS: 2 ground, 3-5 vegetation, 6 building, 9 water, 17 bridge.
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

BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/TX_Pecos_Dallas_B3_2018"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "dallas" / "data"
S = requests.Session()


def nodes_in(root, box, max_depth):
    def bounds(d, x, y, z):
        s = 2 ** d
        w, h = (root[3] - root[0]) / s, (root[4] - root[1]) / s
        return root[0] + x * w, root[1] + y * h, root[0] + (x + 1) * w, root[1] + (y + 1) * h
    found, todo = {}, ["0-0-0-0"]
    while todo:
        k0 = todo.pop()
        for k, n in S.get(f"{BASE}/ept-hierarchy/{k0}.json", timeout=60).json().items():
            d, x, y, z = map(int, k.split("-"))
            if d > max_depth:
                continue
            b = bounds(d, x, y, z)
            if b[2] < box[0] or b[0] > box[2] or b[3] < box[1] or b[1] > box[3]:
                continue
            if n == -1:
                if k != k0:
                    todo.append(k)
                continue
            found[k] = n
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--half-m", type=float, default=600)
    ap.add_argument("--depth", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-points", type=int, default=1_200_000)
    a = ap.parse_args()

    root = S.get(f"{BASE}/ept.json", timeout=60).json()["bounds"]
    t = Transformer.from_crs(4326, 3857, always_xy=True)
    cx, cy = t.transform(a.lon, a.lat)
    k = 1 / np.cos(np.radians(a.lat))  # web-mercator metres per ground metre
    box = (cx - a.half_m * k, cy - a.half_m * k, cx + a.half_m * k, cy + a.half_m * k)
    nodes = nodes_in(root, box, a.depth)
    per = {}
    for key, n in nodes.items():
        per[int(key.split("-")[0])] = per.get(int(key.split("-")[0]), 0) + n
    c = 0
    for d in sorted(per):
        c += per[d]
        print(f"depth {d:2d}: {per[d]:>12,} cumulative {c:>12,}")
    if a.dry_run:
        return

    def fetch(key):
        las = laspy.read(io.BytesIO(S.get(f"{BASE}/ept-data/{key}.laz", timeout=120).content))
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= box[0]) & (x <= box[2]) & (y >= box[1]) & (y <= box[3])
        return (x[m], y[m], np.asarray(las.z)[m], np.asarray(las.classification)[m],
                np.asarray(las.number_of_returns)[m])

    with ThreadPoolExecutor(12) as ex:
        parts = list(ex.map(fetch, sorted(nodes)))
    x, y, z, cl, nr = (np.concatenate([p[i] for p in parts]) for i in range(5))
    keep = ~np.isin(cl, [7, 18])  # drop noise
    x, y, z, cl, nr = x[keep], y[keep], z[keep], cl[keep], nr[keep]
    print(f"{len(x):,} points; classes:", dict(zip(*np.unique(cl, return_counts=True))))
    if len(x) > a.max_points:  # thin for the browser: keep roofs dense, thin ground and trees more
        rng = np.random.default_rng(3)
        w = np.where(cl == 2, 0.5, np.where(nr == 1, 2.0, 1.0))
        p = w / w.sum()
        pick = np.sort(rng.choice(len(x), a.max_points, replace=False, p=p))
        x, y, z, cl, nr = x[pick], y[pick], z[pick], cl[pick], nr[pick]
        print(f"thinned to {len(x):,}")

    inv = Transformer.from_crs(3857, 4326, always_xy=True)
    lon, lat = inv.transform(x, y)
    # ground model (2 m) for height above ground
    g = cl == 2
    gx = ((x - box[0]) / (2 * k)).astype(int); gy = ((y - box[1]) / (2 * k)).astype(int)
    W, H = gx.max() + 1, gy.max() + 1
    G = np.full(W * H, np.nan)
    order = np.argsort(gx[g] + gy[g] * W)
    idx = (gx[g] + gy[g] * W)[order]
    u, st = np.unique(idx, return_index=True)
    G[u] = np.minimum.reduceat(z[g][order], st)
    G = G.reshape(H, W)
    from scipy import ndimage
    _, (iy, ix) = ndimage.distance_transform_edt(np.isnan(G), return_indices=True)
    G = G[iy, ix]
    hag = np.clip(z - G[gy, gx], 0, 60)

    z0 = float(np.percentile(z[g], 1))
    UNIT = 2e-7
    # survey is unclassified beyond ground: one echo above 2.2 m = roof, several echoes = canopy
    roof = (cl != 2) & (hag > 2.2) & (nr == 1)
    veg = (cl != 2) & (hag > 1.5) & (nr > 1)
    kind = np.select([roof, veg], [1, 2], 0)
    q = np.zeros(len(x), dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1")])
    q["x"] = np.round((lon - a.lon) / UNIT); q["y"] = np.round((lat - a.lat) / UNIT)
    q["z"] = np.clip(np.round((z - z0) * 100), 0, 65535)
    q["k"] = kind.astype(np.uint8) * 64 + np.clip(np.round(hag * 1.5), 0, 63).astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scan.txt").write_text(base64.b64encode(q.tobytes()).decode())
    json.dump({"origin": [a.lon, a.lat], "count": int(len(x)), "unit_deg": UNIT, "z_base": z0,
               "half_m": a.half_m, "lidar": "USGS TX_Pecos_Dallas_B3_2018 (flown 2018-19)"}, open(OUT / "scan.json", "w"))

    # roofs: building points per home parcel point -> roof height and planimetric roof area (1 m cells)
    s = pd.read_parquet(ROOT / "data" / "dallas" / "scored.parquet")
    hm = s[(s.lon.between(*inv.transform(box[0], box[1])[0:1], *inv.transform(box[2], box[3])[0:1])) &
           (s.lat.between(inv.transform(box[0], box[1])[1], inv.transform(box[2], box[3])[1]))].copy()
    b = roof
    bx, by = t.transform(hm.lon.values, hm.lat.values)
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([x[b], y[b]]))
    roof_h, roof_m2 = [], []
    for px, py in zip(bx, by):
        ids = tree.query_ball_point([px, py], r=14 * k)
        if len(ids) < 20:
            roof_h.append(None); roof_m2.append(None); continue
        hh = hag[b][ids]
        cells = np.unique(np.column_stack([(x[b][ids] / k).astype(int), (y[b][ids] / k).astype(int)]), axis=0)
        roof_h.append(round(float(np.percentile(hh, 90)), 1)); roof_m2.append(int(len(cells)))
    hm["roof_h"], hm["roof_m2"] = roof_h, roof_m2
    hm["z"] = (G[np.clip(((by - box[1]) / (2 * k)).astype(int), 0, H - 1), np.clip(((bx - box[0]) / (2 * k)).astype(int), 0, W - 1)] - z0).round(2)
    cols = ["address", "zip", "lon", "lat", "score", "recent_hail_in", "recent_hit_day", "roof_squares", "area_m2",
            "roof_h", "roof_m2", "z", "pts"]
    hm = hm[cols]
    hm["pts"] = hm["pts"].map(lambda v: [int(x) for x in v])
    hm["recent_hit_day"] = hm["recent_hit_day"].dt.strftime("%Y-%m-%d")
    hm["recent_hail_in"] = hm["recent_hail_in"].round(2)
    hm.to_json(OUT / "roofs.json", orient="records")
    print(f"{len(hm)} homes in hotspot, {hm.roof_h.notna().sum()} matched to LiDAR roofs")
    print(f"scan.txt {(OUT / 'scan.txt').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

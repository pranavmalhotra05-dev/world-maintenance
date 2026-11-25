"""Download USGS 3DEP LiDAR points for a small bounding box from the public EPT (Entwine) bucket.

EPT stores a point cloud as an octree: every depth adds more detail. We walk the octree,
keep only the nodes that overlap our area, and stop at the depth that gives enough density
for roof geometry (~10-20 pts/m2 is plenty; the full dataset is far denser than we need).

Usage:
  python fetch_lidar.py --dry-run          # show points per depth, no download
  python fetch_lidar.py --max-depth 12     # download up to that depth
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import laspy
import numpy as np
import requests
from pyproj import Transformer

BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/CA_SanFrancisco_1_B23"
ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
OUT = ROOT / "data" / "lidar_points.npz"

session = requests.Session()


def get_json(path):
    r = session.get(f"{BASE}/{path}", timeout=60)
    r.raise_for_status()
    return r.json()


def node_bounds(root, d, x, y, z):
    (x0, y0, z0, x1, y1, z1) = root
    s = 2 ** d
    w, h, t = (x1 - x0) / s, (y1 - y0) / s, (z1 - z0) / s
    return (x0 + x * w, y0 + y * h, z0 + z * t, x0 + (x + 1) * w, y0 + (y + 1) * h, z0 + (z + 1) * t)


def overlaps(b, box):
    return not (b[3] < box[0] or b[0] > box[2] or b[4] < box[1] or b[1] > box[3])


def collect_nodes(root, box, max_depth):
    """Return {key: count} for nodes overlapping box, down to max_depth."""
    found = {}
    pending_files = ["0-0-0-0"]
    while pending_files:
        key = pending_files.pop()
        h = get_json(f"ept-hierarchy/{key}.json")
        for k, count in h.items():
            d, x, y, z = map(int, k.split("-"))
            if d > max_depth:
                continue
            if not overlaps(node_bounds(root, d, x, y, z), box):
                continue
            if count == -1:  # subtree lives in its own hierarchy file
                if k != key:
                    pending_files.append(k)
                continue
            found[k] = count
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-depth", type=int, default=30)
    args = ap.parse_args()

    ept = get_json("ept.json")
    root = ept["bounds"]
    lon0, lat0, lon1, lat1 = CFG["bbox_lonlat"]
    to3857 = Transformer.from_crs(4326, 3857, always_xy=True)
    x0, y0 = to3857.transform(lon0, lat0)
    x1, y1 = to3857.transform(lon1, lat1)
    box = (x0, y0, x1, y1)

    nodes = collect_nodes(root, box, args.max_depth)
    by_depth = {}
    for k, c in nodes.items():
        d = int(k.split("-")[0])
        by_depth[d] = by_depth.get(d, 0) + c
    print("points in overlapping nodes per depth (nodes overhang the box, so real counts are lower):")
    cum = 0
    for d in sorted(by_depth):
        cum += by_depth[d]
        print(f"  depth {d:2d}: {by_depth[d]:>12,}  cumulative {cum:>12,}")
    if args.dry_run:
        return

    def fetch(k):
        r = session.get(f"{BASE}/ept-data/{k}.laz", timeout=120)
        r.raise_for_status()
        las = laspy.read(__import__("io").BytesIO(r.content))
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        return (x[m], y[m], np.asarray(las.z)[m], np.asarray(las.classification)[m],
                np.asarray(las.return_number)[m], np.asarray(las.number_of_returns)[m])

    keys = sorted(nodes)
    parts = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, p in enumerate(ex.map(fetch, keys), 1):
            parts.append(p)
            if i % 25 == 0 or i == len(keys):
                print(f"  downloaded {i}/{len(keys)} nodes", file=sys.stderr)
    cols = [np.concatenate([p[i] for p in parts]) for i in range(6)]
    np.savez_compressed(OUT, x=cols[0], y=cols[1], z=cols[2].astype(np.float32),
                        cls=cols[3].astype(np.uint8), rn=cols[4].astype(np.uint8),
                        nr=cols[5].astype(np.uint8))
    print(f"saved {len(cols[0]):,} points -> {OUT}")


if __name__ == "__main__":
    main()

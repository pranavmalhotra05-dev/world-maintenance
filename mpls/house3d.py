"""Build every house in a Minneapolis block in 3D from its own LiDAR points.

  python house3d.py --lon -93.2400 --lat 44.9235 --half-m 100
  python house3d.py --parcels-only        (re-join parcel fields onto the existing houses.json)

Sources (all free/public):
  USGS 3DEP LiDAR MN_CentralMissRiver_4_B22 (2022, ~64 points/m2), streamed from the public EPT bucket
  Overture Maps building footprints (release 2026-11-18.0)
  Met Council 7-county parcels 2025, Hennepin layer (address, build year, homestead, value; public domain under the
  Minnesota Government Data Practices Act; owner / taxpayer name and address fields are never requested)
  USGS National Map orthoimagery for the ground

Method
  1. every laser point in the block; a 0.5 m ground model from ground-class points
  2. each footprint is matched to the parcel (address) it sits on
  3. every point inside a footprint (+0.6 m for eaves) and >1 m above ground belongs to that house
  4. roof surface: 0.5 m grid of the house's LAST-echo heights (passes through overhanging leaves);
     triangulated straight from the points; walls dropped from the roof edge to the ground
  5. ground: 1 m terrain mesh, coloured by the aerial photo; trees stay as laser points
Output (web/mpls/data/): mesh.txt (base64 binary meshes), points_*.txt, houses.json, meta.json, aerial.jpg
"""
import argparse
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import laspy
import numpy as np
import requests
from PIL import Image
from pyproj import Transformer
from scipy import ndimage
from shapely import contains_xy, from_wkb
from shapely.geometry import Point, shape

EPT = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/MN_CentralMissRiver_4_B22"
PARCELS = "https://arcgis.metc.state.mn.us/data1/rest/services/parcels/Parcels_2025/FeatureServer/3/query"
# never OWNER_*, TAX_NAME or TAX_ADD_* (personal data)
PARCEL_FIELDS = "COUNTY_PIN,ANUMBER,ANUMBERSUF,ST_PRE_DIR,ST_PRE_TYP,ST_NAME,ST_POS_TYP,ST_POS_DIR,ZIP,YEAR_BUILT,HOMESTEAD,USECLASS1,EMV_TOTAL,SALE_DATE,ACRES_POLY"
_ABBR = {"AVENUE": "AVE", "STREET": "ST", "ROAD": "RD", "BOULEVARD": "BLVD", "PARKWAY": "PKWY", "PLACE": "PL", "LANE": "LN",
         "DRIVE": "DR", "COURT": "CT", "TERRACE": "TER", "CIRCLE": "CIR", "HIGHWAY": "HWY", "TRAIL": "TRL",
         "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW"}


def fetch_parcels(w, s, e, n):
    """Met Council Parcels_2025 (Hennepin layer) polygons in the box -> [(shapely geom, props)]."""
    pr = S.get(PARCELS, params={"where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                                "inSR": 4326, "outSR": 4326, "f": "geojson", "resultRecordCount": 2000, "outFields": PARCEL_FIELDS},
               timeout=120).json()
    return [(shape(f["geometry"]), f["properties"]) for f in pr.get("features", []) if f.get("geometry")]


def parcel_fields(par):
    """houses.json parcel fields (same keys as before) from one Met Council parcel record (or None)."""
    if not par:
        return {"address": None, "zip": None, "year_built": None, "homestead": None, "type": None, "value": None}
    ab = lambda v: _ABBR.get(str(v).strip().upper(), str(v).strip().upper()) if v and str(v).strip() else None
    num = f"{par.get('ANUMBER') or ''}{(par.get('ANUMBERSUF') or '').strip()}".strip()
    street = " ".join(x for x in (ab(par.get("ST_PRE_DIR")), ab(par.get("ST_PRE_TYP")), (par.get("ST_NAME") or "").strip().upper() or None,
                                  ab(par.get("ST_POS_TYP")), ab(par.get("ST_POS_DIR"))) if x)
    yb = par.get("YEAR_BUILT")
    return {"address": f"{num} {street.title()}" if num and street else None, "zip": par.get("ZIP"),
            "year_built": int(yb) if yb else None, "homestead": (par.get("HOMESTEAD") or "").strip().lower() == "yes",
            "type": (par.get("USECLASS1") or "").strip().title() or None, "value": par.get("EMV_TOTAL")}
OVERTURE = "s3://overturemaps-us-west-2/release/2026-11-18.0/theme=buildings/type=building/*"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
S = requests.Session()


FED = "public domain (US federal work, USGS)"
USGS_LIC = "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits"
BASE_SOURCES = [
    {"name": "USGS 3DEP LiDAR (MN_CentralMissRiver_4_B22, 2022)", "detail": "every laser point; roofs, walls, ground, trees",
     "url": "https://registry.opendata.aws/usgs-lidar/", "licence": FED, "licence_url": USGS_LIC},
    {"name": "Overture Maps buildings", "detail": "building footprints (release 2026-11-18.0)", "url": "https://overturemaps.org",
     "licence": "ODbL 1.0 (theme licence; includes CC BY 4.0 inputs) - © OpenStreetMap contributors, Overture Maps Foundation",
     "licence_url": "https://docs.overturemaps.org/attribution/"},
    {"name": "Metropolitan Council parcels 2025 (Hennepin layer)", "detail": "address, ZIP, year built, homestead, use class, county market value; owner names not read",
     "url": PARCELS.rsplit("/query", 1)[0], "licence": "public domain under the Minnesota Government Data Practices Act (Minn. Stat. ch. 13)",
     "licence_url": "https://www.arcgis.com/home/item.html?id=27d9f56cfec6454688110d08ae6826a0"},
    {"name": "USGS National Map orthoimagery", "detail": "ground texture (aerial.jpg)",
     "url": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer", "licence": FED, "licence_url": USGS_LIC},
]


def write_sources():
    """Merge this script's entries into sources.json (entries carry "by" = the script that wrote them)."""
    try:
        other = [s_ for s_ in json.load(open(OUT / "sources.json")) if s_.get("by") and s_["by"] != "house3d.py"]
    except Exception:  # noqa: BLE001
        other = []
    json.dump([{**s_, "by": "house3d.py"} for s_ in BASE_SOURCES] + other, open(OUT / "sources.json", "w"), indent=1)


def fit_roof_planes(cx, cy, cz, max_planes=6, tol=0.12, iters=250, seed=0):
    """RANSAC: peel off flat roof facets one at a time. Returns list of (a, b, c) with z = a*x + b*y + c."""
    rng = np.random.default_rng(seed)
    left = np.arange(len(cz)); planes = []
    while len(left) >= 12 and len(planes) < max_planes:
        best, best_in = None, None
        X, Y, Z = cx[left], cy[left], cz[left]
        for _ in range(iters):
            t = rng.choice(len(left), 3, replace=False)
            A = np.column_stack([X[t], Y[t], np.ones(3)])
            if abs(np.linalg.det(A)) < 1e-6:
                continue
            pa = np.linalg.solve(A, Z[t])
            if np.hypot(pa[0], pa[1]) > 1.6:  # steeper than ~58 deg is a wall or tree, not a roof
                continue
            inl = np.abs(X * pa[0] + Y * pa[1] + pa[2] - Z) < tol
            if best_in is None or inl.sum() > best_in.sum():
                best, best_in = pa, inl
        if best is None or best_in.sum() < max(12, 0.06 * len(cz)):
            break
        A = np.column_stack([X[best_in], Y[best_in], np.ones(best_in.sum())])
        pa, *_ = np.linalg.lstsq(A, Z[best_in], rcond=None)  # refine on all inliers
        planes.append(pa)
        left = left[~best_in]
    return planes


def ept_nodes(root, box):
    found, todo = [], ["0-0-0-0"]
    while todo:
        k0 = todo.pop()
        for k, n in S.get(f"{EPT}/ept-hierarchy/{k0}.json", timeout=60).json().items():
            d, x, y, _ = map(int, k.split("-"))
            s = 2 ** d; w = (root[3] - root[0]) / s; h = (root[4] - root[1]) / s
            if root[0] + (x + 1) * w < box[0] or root[0] + x * w > box[2] or root[1] + (y + 1) * h < box[1] or root[1] + y * h > box[3]:
                continue
            if n == -1:
                if k != k0:
                    todo.append(k)
            elif n > 0:
                found.append(k)
    return found


def refresh_parcels():
    """Re-join only the parcel fields onto the existing houses.json (no LiDAR rebuild; other fields untouched)."""
    meta = json.load(open(OUT / "meta.json"))
    W_, S_, E_, N_ = meta["bounds"]
    parcels = fetch_parcels(W_ - 0.0005, S_ - 0.0005, E_ + 0.0005, N_ + 0.0005)
    houses = json.load(open(OUT / "houses.json"))  # re-read right before writing: other scripts add fields to it
    hit = 0
    for h in houses:
        par = next((pp for poly, pp in parcels if poly.contains(Point(h["lon"], h["lat"]))), None)
        h.update(parcel_fields(par)); hit += par is not None
    json.dump(houses, open(OUT / "houses.json", "w"), separators=(",", ":"))
    print(f"{len(parcels)} Met Council parcels; {hit}/{len(houses)} houses joined")
    write_sources()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--half-m", type=float, default=100)
    ap.add_argument("--parcels-only", action="store_true", help="only re-join Met Council parcel fields onto houses.json")
    a = ap.parse_args()
    if a.parcels_only:
        return refresh_parcels()
    if a.lon is None or a.lat is None:
        ap.error("--lon and --lat are required")
    OUT.mkdir(parents=True, exist_ok=True)
    to_m = Transformer.from_crs(4326, 3857, always_xy=True)
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    cx, cy = to_m.transform(a.lon, a.lat)
    k = 1 / np.cos(np.radians(a.lat))
    box = (cx - a.half_m * k, cy - a.half_m * k, cx + a.half_m * k, cy + a.half_m * k)
    W_, S_ = to_ll.transform(box[0], box[1]); E_, N_ = to_ll.transform(box[2], box[3])

    # ---------------------------------------------------------------- 1. every laser point
    root = S.get(f"{EPT}/ept.json", timeout=60).json()["bounds"]
    keys = ept_nodes(root, box)

    def fetch(key):
        las = laspy.read(io.BytesIO(S.get(f"{EPT}/ept-data/{key}.laz", timeout=180).content))
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= box[0]) & (x <= box[2]) & (y >= box[1]) & (y <= box[3])
        return (x[m], y[m], np.asarray(las.z)[m], np.asarray(las.classification)[m],
                np.asarray(las.return_number)[m], np.asarray(las.number_of_returns)[m], np.asarray(las.intensity)[m])

    with ThreadPoolExecutor(12) as ex:
        parts = list(ex.map(fetch, keys))
    x, y, z, cl, rn, nr, it = (np.concatenate([p[i] for p in parts]) for i in range(7))
    keep = ~np.isin(cl, [7, 18])
    x, y, z, cl, rn, nr, it = (v[keep] for v in (x, y, z, cl, rn, nr, it))
    # local metres east/north of the origin (what deck.gl METER_OFFSETS expects)
    ex_ = (x - cx) / k; ny_ = (y - cy) / k
    print(f"{len(x):,} laser points = {len(x) / (2 * a.half_m) ** 2:.1f}/m2 from {len(keys)} nodes")

    # ground model, 0.5 m
    R = 0.5
    n = int(2 * a.half_m / R) + 1
    gi = np.clip(((ex_ + a.half_m) / R).astype(int), 0, n - 1); gj = np.clip(((ny_ + a.half_m) / R).astype(int), 0, n - 1)
    g = cl == 2
    G = np.full(n * n, np.inf); np.minimum.at(G, (gj * n + gi)[g], z[g]); G = G.reshape(n, n)
    _, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(G), return_indices=True)
    G = ndimage.median_filter(G[iy, ix], size=5)
    z0 = float(np.percentile(G, 1))
    hag = z - G[gj, gi]

    # ---------------------------------------------------------------- 2. footprints + addresses
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT id, ST_AsWKB(geometry) FROM read_parquet('{OVERTURE}', hive_partitioning=1)
        WHERE bbox.xmin > {W_} AND bbox.xmax < {E_} AND bbox.ymin > {S_} AND bbox.ymax < {N_}""").fetchall()
    parcels = fetch_parcels(W_, S_, E_, N_)
    print(f"{len(rows)} footprints, {len(parcels)} parcels")

    def to_local(lon, lat):
        mx, my = to_m.transform(np.asarray(lon), np.asarray(lat))
        return (mx - cx) / k, (my - cy) / k

    # ---------------------------------------------------------------- 3 + 4. points per house, meshes
    pos, nrm, col, idx = [], [], [], []
    vbase = 0
    house_of_point = np.full(len(x), -1, dtype=np.int32)
    resid = np.zeros(len(x), dtype=np.float32)  # metres above (+) / below (-) the fitted roof
    houses = []
    for bid, wkb in rows:
        geom = from_wkb(bytes(wkb))
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda q: q.area)
        if geom.geom_type != "Polygon":
            continue
        lx, ly = to_local(*np.array(geom.exterior.coords).T)
        from shapely.geometry import Polygon
        fp = Polygon(np.column_stack([lx, ly]))
        if fp.area < 10:
            continue
        zone = fp.buffer(0.6)
        bx0, by0, bx1, by1 = zone.bounds
        sel = np.flatnonzero((ex_ >= bx0) & (ex_ <= bx1) & (ny_ >= by0) & (ny_ <= by1) & (hag > 1.0))
        inside = sel[contains_xy(zone, ex_[sel], ny_[sel])]
        if len(inside) < 30:
            continue
        hid = len(houses)
        house_of_point[inside] = hid
        # roof grid from last echoes (fall back to all echoes where a cell has none)
        last = inside[rn[inside] == nr[inside]]
        use = last if len(last) > 0.5 * len(inside) else inside
        c0x, c0y = np.floor(bx0 / R) * R, np.floor(by0 / R) * R
        W = int((bx1 - c0x) / R) + 1; H = int((by1 - c0y) / R) + 1
        ci = ((ex_[use] - c0x) / R).astype(int); cj = ((ny_[use] - c0y) / R).astype(int)
        grid = np.full((H, W), np.nan)
        flat = cj * W + ci; o = np.argsort(flat)
        u, st = np.unique(flat[o], return_index=True)
        grid.flat[u] = [np.percentile(v, 80) for v in np.split(z[use][o], st[1:])]
        gx_c = c0x + (np.arange(W) + 0.5) * R; gy_c = c0y + (np.arange(H) + 0.5) * R
        GX, GY = np.meshgrid(gx_c, gy_c)
        inmask = contains_xy(fp.buffer(0.35), GX, GY)
        if inmask.sum() < 8:
            continue
        hole = np.isnan(grid)
        if hole.all():
            continue
        # ---- fit the roof's flat facets, then give every roof cell the height of its facet
        ok = inmask & ~hole
        rr0, cc0 = np.nonzero(ok)
        planes = fit_roof_planes(GX[rr0, cc0], GY[rr0, cc0], grid[rr0, cc0])
        label = -np.ones((H, W), dtype=int)
        if planes:
            pz_all = np.stack([GX * pa[0] + GY * pa[1] + pa[2] for pa in planes])  # (planes, H, W)
            res = np.abs(pz_all - np.where(hole, np.nan, grid)[None])
            lab = np.nanargmin(np.where(np.isnan(res), np.inf, res), axis=0)
            good = ~hole & (np.take_along_axis(res, lab[None], 0)[0] < 0.35)
            label[good] = lab[good]
            # cells without a fitting echo (trees on the roof, gaps) take the nearest facet
            if (label >= 0).any():
                _, (ly, lx) = ndimage.distance_transform_edt(label < 0, return_indices=True)
                label = label[ly, lx]
                label = ndimage.median_filter(label, size=3)  # clean facet borders
                grid = np.take_along_axis(pz_all, label[None], 0)[0]
            else:
                planes = []
        if not planes:  # no clear facets (complex roof): fall back to the smoothed echo surface
            _, (iy2, ix2) = ndimage.distance_transform_edt(hole, return_indices=True)
            grid = ndimage.median_filter(grid[iy2, ix2], size=3)
        # never let a facet run past what the laser actually measured on this house
        meas = z[use]
        lo_z = float(np.percentile(G[np.clip(((ny_[use] + a.half_m) / R).astype(int), 0, n - 1), np.clip(((ex_[use] + a.half_m) / R).astype(int), 0, n - 1)], 50)) + 1.5
        hi_z = float(np.percentile(meas[meas < np.percentile(meas, 99)], 97)) + 0.25 if len(meas) > 50 else float(meas.max())
        grid = np.clip(grid, lo_z, max(hi_z, lo_z + 0.5))
        # points well above the fitted roof are canopy, not house
        pi = np.clip(((ex_[inside] - c0x) / R).astype(int), 0, W - 1); pj = np.clip(((ny_[inside] - c0y) / R).astype(int), 0, H - 1)
        tree_pts = z[inside] > grid[pj, pi] + 0.6
        _r = z[inside] - grid[pj, pi]
        resid[inside] = _r - np.median(_r[~tree_pts]) if (~tree_pts).any() else _r
        house_of_point[inside[tree_pts]] = -1
        base_z = float(np.median(G[np.clip(((GY[inmask] + a.half_m) / R).astype(int), 0, n - 1),
                                   np.clip(((GX[inmask] + a.half_m) / R).astype(int), 0, n - 1)]))
        # roof vertices = cell centres inside the footprint; smooth normals from the height field
        dzy, dzx = np.gradient(grid, R)
        vid = -np.ones((H, W), dtype=np.int64)
        rr, cc = np.nonzero(inmask)
        vid[rr, cc] = vbase + np.arange(len(rr))
        top = grid[rr, cc]
        pos.append(np.column_stack([GX[rr, cc], GY[rr, cc], top - z0]))
        nv = np.column_stack([-dzx[rr, cc], -dzy[rr, cc], np.ones(len(rr))])
        nrm.append(nv / np.linalg.norm(nv, axis=1, keepdims=True))
        hgt = top - base_z
        col.append(np.column_stack([np.full(len(rr), hid % 256), np.clip(hgt * 10, 0, 255), np.full(len(rr), 1), np.full(len(rr), 255)]))
        vbase += len(rr)
        q = (vid[:-1, :-1] >= 0) & (vid[1:, :-1] >= 0) & (vid[:-1, 1:] >= 0) & (vid[1:, 1:] >= 0)
        a00, a10, a01, a11 = vid[:-1, :-1][q], vid[1:, :-1][q], vid[:-1, 1:][q], vid[1:, 1:][q]
        idx.append(np.column_stack([a00, a01, a11, a00, a11, a10]).reshape(-1, 3))
        # walls: walk the footprint edge every ~0.5 m; top = roof height at the nearest roof vertex
        ring = np.array(fp.exterior.coords)
        wall_pts = []
        for p0, p1 in zip(ring[:-1], ring[1:]):
            L = np.hypot(*(p1 - p0)); m_ = max(int(L / 0.5), 1)
            for t in np.linspace(0, 1, m_, endpoint=False):
                wall_pts.append(p0 + (p1 - p0) * t)
        wall_pts.append(ring[0])
        wp = np.array(wall_pts)
        wc = np.clip(((wp[:, 0] - c0x) / R).astype(int), 0, W - 1); wr = np.clip(((wp[:, 1] - c0y) / R).astype(int), 0, H - 1)
        wt = grid[wr, wc] - z0
        wb = G[np.clip(((wp[:, 1] + a.half_m) / R).astype(int), 0, n - 1), np.clip(((wp[:, 0] + a.half_m) / R).astype(int), 0, n - 1)] - z0
        m = len(wp)
        tops = np.column_stack([wp, wt]); bots = np.column_stack([wp, wb])
        pos.append(np.vstack([tops, bots]))
        e = np.diff(wp, axis=0); e = np.vstack([e, e[-1:]])
        wn = np.column_stack([e[:, 1], -e[:, 0], np.zeros(m)])
        wn /= np.maximum(np.linalg.norm(wn, axis=1, keepdims=True), 1e-9)
        if fp.exterior.is_ccw:
            wn = -wn
        nrm.append(np.vstack([wn, wn]))
        col.append(np.column_stack([np.full(2 * m, hid % 256), np.zeros(2 * m), np.full(2 * m, 2), np.full(2 * m, 255)]))
        t_ = vbase + np.arange(m - 1); b_ = t_ + m
        idx.append(np.column_stack([t_, b_, t_ + 1, t_ + 1, b_, b_ + 1]).reshape(-1, 3))
        vbase += 2 * m
        # address from the parcel under the footprint centroid
        cl_lon, cl_lat = to_ll.transform(cx + fp.centroid.x * k, cy + fp.centroid.y * k)
        par = next((pp for poly, pp in parcels if poly.contains(Point(cl_lon, cl_lat))), None)
        houses.append({"id": hid, "ring": [[round(float(u), 2), round(float(v), 2)] for u, v in fp.exterior.coords],
                       "garage": bool(fp.area < 45), "points": int((~tree_pts).sum()), "planes": len(planes), "area_m2": round(fp.area, 1),
                       "roof_dev": (lambda d: (lambda c: {"spread_cm": round(float(1.4826 * np.median(np.abs(c))) * 100, 1),
                                               "p95_cm": round(float(np.percentile(np.abs(c), 95)) * 100, 1),
                                               "below_8cm_pct": round(float((c < -0.08).mean()) * 100, 1),
                                               "above_8cm_pct": round(float((c > 0.08).mean()) * 100, 1)})(d - np.median(d)) if len(d) > 20 else None)(
                                    (lambda dd, core: dd[core & (np.abs(dd) < 1.5)])(z[inside] - grid[pj, pi],
                                    ~tree_pts & contains_xy(fp.buffer(-0.5), ex_[inside], ny_[inside]) if not fp.buffer(-0.5).is_empty else ~tree_pts)),
                       "height_m": round(float(np.percentile(hgt, 95)), 1), "eave_m": round(float(np.percentile(hgt, 20)), 1),
                       "lon": round(cl_lon, 6), "lat": round(cl_lat, 6),
                       **parcel_fields(par)})

    P = np.vstack(pos).astype(np.float32); Nn = np.vstack(nrm).astype(np.float32)
    C = np.vstack(col).astype(np.uint8); I = np.vstack(idx).astype(np.uint32)
    print(f"{len(houses)} houses meshed: {len(P):,} vertices, {len(I):,} triangles; "
          f"{(house_of_point >= 0).sum():,} laser points assigned to houses")

    # ---------------------------------------------------------------- 5. terrain mesh (1 m) + aerial colour
    img = S.get("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export",
                params={"bbox": f"{box[0]},{box[1]},{box[2]},{box[3]}", "bboxSR": 3857, "imageSR": 3857, "size": "2048,2048",
                        "format": "jpg", "f": "image"}, timeout=180).content
    (OUT / "aerial.jpg").write_bytes(img)
    T = 1.0
    tn = int(2 * a.half_m / T) + 1
    tx = -a.half_m + np.arange(tn) * T; ty = -a.half_m + np.arange(tn) * T
    TX, TY = np.meshgrid(tx, ty)
    TZ = G[np.clip(((TY + a.half_m) / R).astype(int), 0, n - 1), np.clip(((TX + a.half_m) / R).astype(int), 0, n - 1)] - z0
    tdzy, tdzx = np.gradient(TZ, T)
    tnv = np.column_stack([-tdzx.ravel(), -tdzy.ravel(), np.ones(TZ.size)]); tnv /= np.linalg.norm(tnv, axis=1, keepdims=True)
    tid = np.arange(tn * tn).reshape(tn, tn)
    ti = np.column_stack([tid[:-1, :-1].ravel(), tid[:-1, 1:].ravel(), tid[1:, 1:].ravel(),
                          tid[:-1, :-1].ravel(), tid[1:, 1:].ravel(), tid[1:, :-1].ravel()]).reshape(-1, 3)
    terrain = {"pos": np.column_stack([TX.ravel(), TY.ravel(), TZ.ravel()]).astype(np.float32),
               "nrm": tnv.astype(np.float32), "idx": ti.astype(np.uint32),
               "uv": np.column_stack([(TX.ravel() + a.half_m) / (2 * a.half_m), 1 - (TY.ravel() + a.half_m) / (2 * a.half_m)]).astype(np.float32)}

    # ---------------------------------------------------------------- points: EVERY laser return, nothing dropped
    # kind: 0 ground (roads, sidewalks, yards), 1 house (roof/walls), 2 vegetation (multi-echo), 3 other above-ground
    kind = np.where(cl == 2, 0, np.where(house_of_point >= 0, 1, np.where(nr > 1, 2, 3))).astype(np.uint8)
    lo_i, hi_i = np.percentile(it[cl == 2], [2, 98])
    q = np.zeros(len(x), dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")])
    q["x"] = np.round(ex_ * 100 / 2).astype(np.int16)   # 2 cm units (block is +-100 m)
    q["y"] = np.round(ny_ * 100 / 2).astype(np.int16)
    q["z"] = np.clip(np.round((z - z0) * 100), 0, 65535)
    q["k"] = kind
    q["h"] = np.clip(np.round(np.maximum(hag, 0) * 8), 0, 255)
    q["r"] = np.where(kind == 1, np.clip(np.round(resid * 100 / 2), -127, 127), 0)  # 2 cm units
    q["i"] = np.clip((it.astype(float) - lo_i) / max(hi_i - lo_i, 1) * 255, 0, 255).astype(np.uint8)
    q = q[np.random.default_rng(0).permutation(len(q))]
    for f in OUT.glob("points_*.txt"):
        f.unlink()
    PART = 900_000
    nparts = int(np.ceil(len(q) / PART))
    for i in range(nparts):
        (OUT / f"points_{i}.txt").write_text(base64.b64encode(q[i * PART:(i + 1) * PART].tobytes()).decode())

    def b64(arr):
        return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode()

    (OUT / "mesh.txt").write_text(json.dumps({
        "houses": {"pos": b64(P), "nrm": b64(Nn), "col": b64(C), "idx": b64(I)},
        "terrain": {k_: b64(v) for k_, v in terrain.items()}}))
    json.dump(houses, open(OUT / "houses.json", "w"), separators=(",", ":"))
    json.dump({"origin": [a.lon, a.lat], "half_m": a.half_m, "z_base": z0, "bounds": [W_, S_, E_, N_],
               "points_total": int(len(x)), "points_per_m2": round(len(x) / (2 * a.half_m) ** 2, 1),
               "points_shipped": int(len(q)), "point_parts": nparts, "point_bytes": 10, "point_unit_m": 0.02, "house_points": int((house_of_point >= 0).sum()),
               "lidar": "USGS 3DEP MN_CentralMissRiver_4_B22 (2022)"}, open(OUT / "meta.json", "w"))
    write_sources()
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name}: {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

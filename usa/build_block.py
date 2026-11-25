"""Build every building on a block anywhere in the USA in 3D from its own LiDAR points (national sources only).

  python usa/build_block.py --address "4116 21st Ave S, Minneapolis, MN" --half-m 60 --slug mpls-test
  python usa/build_block.py --lon -83.0098 --lat 39.9904 --half-m 60 --slug columbus-oh

Same method, file set and schema as mpls/house3d.py (web/mpls/data/), written to web/usa/blocks/<slug>/:
  meta.json, points_0..N.txt, mesh.txt, houses.json, aerial.jpg, img/<year>.jpg (NAIP), layers.json, sources.json

Sources (all free, keyless, commercial use allowed):
  US Census Geocoder                 address -> lon/lat, state/county/tract/block (public domain)
  USGS 3DEP LiDAR (EPT, AWS)         every laser point; project chosen from data/national/ept_boundaries.geojson (public domain)
  Overture Maps buildings            footprints (ODbL / CC-BY per source)
  Overture Maps addresses            only records whose licence allows commercial use (mostly US DOT NAD, public domain)
  OpenStreetMap addr:* (Geofabrik)   fallback addresses (ODbL; local state extract via usa/osm_extract.py)
  USGS National Map orthoimagery     ground texture aerial.jpg (public domain)
  USDA NAIP (Planetary Computer)     one aerial per NAIP year -> img/<year>.jpg (public domain)
No owner names anywhere.
"""
import argparse
import base64
import io
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import laspy
import numpy as np
from PIL import Image
from pyproj import CRS, Transformer
from scipy import ndimage
from shapely import contains_xy, from_wkb
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BLOCKS, EPT_BASE, OK_LICENCE, S, block_box_3857, census_point, dump_json, ept_nodes, geocode,  # noqa: E402
                    get, pick_ept, raster_to_block, write_files_list)

OVERTURE_FALLBACK = "2026-11-18.0"


def fit_roof_planes(cx, cy, cz, max_planes=6, tol=0.12, iters=250, seed=0):
    """RANSAC (as mpls/house3d.py): peel off flat roof facets one at a time. Returns [(a, b, c)] with z = a*x + b*y + c."""
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
        pa, *_ = np.linalg.lstsq(A, Z[best_in], rcond=None)
        planes.append(pa)
        left = left[~best_in]
    return planes


def overture_release():
    try:
        r = get("https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/", params={"list-type": "2", "prefix": "release/", "delimiter": "/"}, timeout=60)
        rel = sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", r.text))
        if rel:
            return rel[-1]
    except Exception as e:  # noqa: BLE001
        print("Overture release listing failed, using fallback:", e)
    return OVERTURE_FALLBACK


def tiger_approx(fips, houses, fps, to_ll, cx, cy, k, bounds):
    """Census TIGER/Line ADDRFEAT (public domain): interpolate a house number along the nearest street's address range.
    Written as address_approx (never as address): ranges are potential numbers, not verified addresses."""
    import tempfile
    import zipfile
    from shapely import wkt as swkt
    from shapely.geometry import LineString
    W_, S_, E_, N_ = bounds
    tmp = Path(tempfile.mkdtemp(prefix="tiger_"))
    for yr in (2025, 2024, 2023):
        r = get(f"https://www2.census.gov/geo/tiger/TIGER{yr}/ADDRFEAT/tl_{yr}_{fips}_addrfeat.zip", timeout=300)
        if r.ok and r.content[:2] == b"PK":
            break
    else:
        raise RuntimeError("no ADDRFEAT file")
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(tmp)
    shp = next(tmp.glob("*.shp"))
    con = duckdb.connect(); con.execute("INSTALL spatial; LOAD spatial;")
    d = 0.001
    rows = con.execute(f"""SELECT FULLNAME, LFROMHN, LTOHN, RFROMHN, RTOHN, ZIPL, ZIPR, ST_AsText(geom) FROM ST_Read('{shp.as_posix()}')
        WHERE ST_Intersects(geom, ST_MakeEnvelope({W_ - d}, {S_ - d}, {E_ + d}, {N_ + d}))""").fetchall()
    to_m = Transformer.from_crs(4326, 3857, always_xy=True)
    segs = []
    for name, lf, lt, rf, rt, zl, zr, g in rows:
        geom = swkt.loads(g)
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        for ln in lines:
            xy = np.array(ln.coords)[:, :2]
            mx, my = to_m.transform(xy[:, 0], xy[:, 1])
            segs.append((LineString(np.column_stack([(mx - cx) / k, (my - cy) / k])), name, lf, lt, rf, rt, zl, zr))
    num = lambda s: int(re.sub(r"\D", "", s)) if s and re.sub(r"\D", "", s) else None
    nn = 0
    for h, fp in zip(houses, fps):
        if h["address"] or h["garage"]:
            continue
        c = fp.centroid
        best = min(((s[0].distance(c), s) for s in segs if s[1]), default=None, key=lambda t: t[0])
        if not best or best[0] > 45:
            continue
        ln, name, lf, lt, rf, rt, zl, zr = best[1]
        t = ln.project(c); p0 = ln.interpolate(max(t - 0.5, 0)); p1 = ln.interpolate(min(t + 0.5, ln.length))
        side_left = (p1.x - p0.x) * (c.y - p0.y) - (p1.y - p0.y) * (c.x - p0.x) > 0
        a_, b_ = (num(lf), num(lt)) if side_left else (num(rf), num(rt))
        if a_ is None or b_ is None:
            continue
        f = t / max(ln.length, 1e-6)
        v = a_ + f * (b_ - a_)
        v = int(round((v - a_) / 2)) * 2 + a_  # keep the side's odd/even parity
        h["address_approx"] = f"~{v} {name}"
        h["address_approx_source"] = "Census TIGER/Line ADDRFEAT address-range interpolation (approximate)"
        if not h["zip"]:
            h["zip"] = (zl if side_left else zr) or None
        nn += 1
    return nn


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "block"


GEOFABRIK_US = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado", "CT": "connecticut",
                "DE": "delaware", "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
                "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
                "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
                "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
                "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
                "PR": "puerto-rico", "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee", "TX": "texas",
                "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming"}


def osm_has_street_address(kind, t):
    """OSM features carrying both addr:housenumber and addr:street (the old Overpass filter)."""
    return bool(t.get("addr:housenumber") and t.get("addr:street"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--half-m", type=float, default=60)
    ap.add_argument("--slug")
    ap.add_argument("--ept", help="force a USGS EPT project name")
    ap.add_argument("--no-naip", action="store_true", help="skip the NAIP year-by-year aerials")
    ap.add_argument("--tiger-approx", action="store_true", help="EXPERIMENTAL: interpolated TIGER house numbers into address_approx (often wrong; off by default)")
    a = ap.parse_args()
    if not a.address and (a.lon is None or a.lat is None):
        ap.error("give --address or --lon and --lat")
    if a.half_m > 600:
        ap.error("--half-m must be <= 600 (int16 point coordinates in 2 cm units)")

    # ---------------------------------------------------------------- 0. where
    place = geocode(a.address) if a.address else census_point(a.lon, a.lat)
    lon, lat = (place["lon"], place["lat"]) if a.address else (a.lon, a.lat)
    slug = a.slug or slugify(a.address or f"{lon:.5f}_{lat:.5f}")
    OUT = BLOCKS / slug
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"{place.get('matched_address') or (lon, lat)} -> {OUT}")
    print(f"  {place.get('county')} ({place.get('county_fips')}), tract {place.get('tract')}")
    half = a.half_m
    to_m = Transformer.from_crs(4326, 3857, always_xy=True)
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    cx, cy, k, box = block_box_3857(lon, lat, half)
    W_, S_ = to_ll.transform(box[0], box[1]); E_, N_ = to_ll.transform(box[2], box[3])
    tz = None
    try:
        tz = get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", timeout=60).json()["properties"]["timeZone"]
    except Exception as e:  # noqa: BLE001
        print("NWS time zone lookup failed:", e)

    # ---------------------------------------------------------------- 1. every laser point
    if a.ept:
        proj, cands = {"name": a.ept, "year": None, "density": None, "year_source": None, "box_coverage": None}, []
    else:
        proj, cands = pick_ept(lon, lat, (W_, S_, E_, N_))
    for c in cands[:6]:
        print(f"  EPT candidate {c['name']}: year {c['year']} ({c['year_source']}), {c['density']} pts/m2, box coverage {c['box_coverage']}")
    EPT = f"{EPT_BASE}/{proj['name']}"
    info = get(f"{EPT}/ept.json", timeout=60).json()
    root = info["bounds"]
    srs = info.get("srs") or {}
    src_epsg = f"{srs.get('authority', 'EPSG')}:{srs.get('horizontal', '3857')}"
    src_crs = CRS.from_wkt(srs["wkt"]) if srs.get("wkt") else CRS.from_user_input(src_epsg)
    same = src_crs.to_epsg() == 3857
    z_scale = 1.0
    try:
        if not same and "foot" in src_crs.axis_info[0].unit_name.lower():
            z_scale = 0.3048006096  # feet project: assume vertical in the same unit
    except Exception:  # noqa: BLE001
        pass
    if same:
        qbox = box
        back = None
    else:
        fwd = Transformer.from_crs(3857, src_crs, always_xy=True)
        bx, by = fwd.transform(np.array([box[0], box[2], box[0], box[2], (box[0] + box[2]) / 2]),
                               np.array([box[1], box[1], box[3], box[3], (box[1] + box[3]) / 2]))
        pad = 0.02 * (max(bx) - min(bx))
        qbox = (min(bx) - pad, min(by) - pad, max(bx) + pad, max(by) + pad)
        back = Transformer.from_crs(src_crs, 3857, always_xy=True)
        print(f"  EPT srs {src_epsg} differs from EPSG:3857: reprojecting points")
    keys = ept_nodes(EPT, root, qbox)

    def fetch(key):
        las = laspy.read(io.BytesIO(get(f"{EPT}/ept-data/{key}.laz", timeout=240).content))
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= qbox[0]) & (x <= qbox[2]) & (y >= qbox[1]) & (y <= qbox[3])
        x, y = x[m], y[m]
        if back is not None:
            x, y = back.transform(x, y)
            m2 = (x >= box[0]) & (x <= box[2]) & (y >= box[1]) & (y <= box[3])
        else:
            m2 = slice(None)
        cols = [np.asarray(v)[m] for v in (las.z, las.classification, las.return_number, las.number_of_returns, las.intensity)]
        return (np.asarray(x)[m2], np.asarray(y)[m2], *[c[m2] for c in cols])

    with ThreadPoolExecutor(12) as ex:
        parts = list(ex.map(fetch, keys))
    x, y, z, cl, rn, nr, it = (np.concatenate([p[i] for p in parts]) for i in range(7))
    z = z * z_scale
    noise = np.isin(cl, [7, 18])  # LAS low/high noise classes: not a surface (dropped exactly as house3d.py does, and counted)
    n_noise = int(noise.sum())
    x, y, z, cl, rn, nr, it = (v[~noise] for v in (x, y, z, cl, rn, nr, it))
    if len(x) < 1000:
        raise SystemExit(f"only {len(x)} LiDAR points in the block from {proj['name']}")
    ex_ = (x - cx) / k; ny_ = (y - cy) / k
    dens = len(x) / (2 * half) ** 2
    print(f"{len(x):,} laser points = {dens:.1f}/m2 from {len(keys)} nodes of {proj['name']} ({n_noise:,} noise-class returns excluded)")

    # ground model, 0.5 m
    R = 0.5
    n = int(2 * half / R) + 1
    gi = np.clip(((ex_ + half) / R).astype(int), 0, n - 1); gj = np.clip(((ny_ + half) / R).astype(int), 0, n - 1)
    g = cl == 2
    if g.sum() < 100:  # unclassified project: lowest return per 2 m cell stands in for ground
        lo = np.full(n * n, np.inf); np.minimum.at(lo, gj * n + gi, z); lo = lo.reshape(n, n)
        lo = ndimage.minimum_filter(np.where(np.isfinite(lo), lo, np.inf), size=9)
        g = z < lo[gj, gi] + 0.3
        print("  project has no ground class: using lowest returns")
    G = np.full(n * n, np.inf); np.minimum.at(G, (gj * n + gi)[g], z[g]); G = G.reshape(n, n)
    _, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(G), return_indices=True)
    G = ndimage.median_filter(G[iy, ix], size=5)
    z0 = float(np.percentile(G, 1))
    hag = z - G[gj, gi]

    # ---------------------------------------------------------------- 2. footprints (Overture) + addresses
    rel = overture_release()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT id, ST_AsWKB(geometry), subtype, class, height, num_floors, roof_shape, sources[1].dataset
        FROM read_parquet('s3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*', hive_partitioning=1)
        WHERE bbox.xmin > {W_} AND bbox.xmax < {E_} AND bbox.ymin > {S_} AND bbox.ymax < {N_}""").fetchall()
    print(f"{len(rows)} Overture footprints (release {rel})")
    dl, dt = 30 / (111320 * math.cos(math.radians(lat))), 30 / 110540
    addr_pts, addr_src = [], {}
    try:
        ar = con.execute(f"""SELECT number, street, unit, postcode, postal_city, bbox.xmin, bbox.ymin, sources[1].license, sources[1].dataset
            FROM read_parquet('s3://overturemaps-us-west-2/release/{rel}/theme=addresses/type=address/*', hive_partitioning=1)
            WHERE bbox.xmin > {W_ - dl} AND bbox.xmax < {E_ + dl} AND bbox.ymin > {S_ - dt} AND bbox.ymax < {N_ + dt}""").fetchall()
        for num, st, unit, pc, city, ax, ay, lic, ds in ar:
            if not num or not st:
                continue
            if not OK_LICENCE.search(lic or ""):
                addr_src[f"rejected: {lic}"] = addr_src.get(f"rejected: {lic}", 0) + 1
                continue
            addr_pts.append({"lon": ax, "lat": ay, "address": f"{num} {st.strip().title()}", "zip": (pc or "")[:5] or None, "unit": unit,
                             "src": f"Overture addresses / {ds} ({lic})"})
            addr_src[f"{ds} ({lic})"] = addr_src.get(f"{ds} ({lic})", 0) + 1
    except Exception as e:  # noqa: BLE001
        print("Overture addresses failed:", e)
    print(f"{len(addr_pts)} licensed Overture address points {addr_src}")

    def to_local(lo_, la_):
        mx, my = to_m.transform(np.asarray(lo_), np.asarray(la_))
        return (mx - cx) / k, (my - cy) / k

    # ---------------------------------------------------------------- 3 + 4. points per house, meshes (house3d.py method)
    pos, nrm, col, idx = [], [], [], []
    vbase = 0
    house_of_point = np.full(len(x), -1, dtype=np.int32)
    resid = np.zeros(len(x), dtype=np.float32)  # metres above (+) / below (-) the fitted roof, relative to the house median
    houses, fps = [], []
    for bid, wkb, b_sub, b_cls, b_h, b_fl, b_roof, b_ds in rows:
        geom = from_wkb(bytes(wkb))
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda q: q.area)
        if geom.geom_type != "Polygon":
            continue
        lx, ly = to_local(*np.array(geom.exterior.coords).T)
        fp = Polygon(np.column_stack([lx, ly]))
        if not fp.is_valid:
            fp = fp.buffer(0)
            if fp.geom_type != "Polygon":
                continue
        if fp.area < 10:
            continue
        zone = fp.buffer(0.6)
        bx0, by0, bx1, by1 = zone.bounds
        sel = np.flatnonzero((ex_ >= bx0) & (ex_ <= bx1) & (ny_ >= by0) & (ny_ <= by1) & (hag > 1.0))
        inside = sel[contains_xy(zone, ex_[sel], ny_[sel])]
        if len(inside) < 30:
            continue
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
        hid = len(houses)
        house_of_point[inside] = hid
        ok = inmask & ~hole
        rr0, cc0 = np.nonzero(ok)
        planes = fit_roof_planes(GX[rr0, cc0], GY[rr0, cc0], grid[rr0, cc0])
        label = -np.ones((H, W), dtype=int)
        if planes:
            pz_all = np.stack([GX * pa[0] + GY * pa[1] + pa[2] for pa in planes])
            res = np.abs(pz_all - np.where(hole, np.nan, grid)[None])
            lab = np.nanargmin(np.where(np.isnan(res), np.inf, res), axis=0)
            good = ~hole & (np.take_along_axis(res, lab[None], 0)[0] < 0.35)
            label[good] = lab[good]
            if (label >= 0).any():
                _, (ly_, lx_) = ndimage.distance_transform_edt(label < 0, return_indices=True)
                label = label[ly_, lx_]
                label = ndimage.median_filter(label, size=3)
                grid = np.take_along_axis(pz_all, label[None], 0)[0]
            else:
                planes = []
        if not planes:
            _, (iy2, ix2) = ndimage.distance_transform_edt(hole, return_indices=True)
            grid = ndimage.median_filter(grid[iy2, ix2], size=3)
        meas = z[use]
        lo_z = float(np.percentile(G[np.clip(((ny_[use] + half) / R).astype(int), 0, n - 1), np.clip(((ex_[use] + half) / R).astype(int), 0, n - 1)], 50)) + 1.5
        hi_z = float(np.percentile(meas[meas < np.percentile(meas, 99)], 97)) + 0.25 if len(meas) > 50 else float(meas.max())
        grid = np.clip(grid, lo_z, max(hi_z, lo_z + 0.5))
        pi = np.clip(((ex_[inside] - c0x) / R).astype(int), 0, W - 1); pj = np.clip(((ny_[inside] - c0y) / R).astype(int), 0, H - 1)
        tree_pts = z[inside] > grid[pj, pi] + 0.6
        _r = z[inside] - grid[pj, pi]
        resid[inside] = _r - np.median(_r[~tree_pts]) if (~tree_pts).any() else _r
        house_of_point[inside[tree_pts]] = -1
        base_z = float(np.median(G[np.clip(((GY[inmask] + half) / R).astype(int), 0, n - 1),
                                   np.clip(((GX[inmask] + half) / R).astype(int), 0, n - 1)]))
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
        wb = G[np.clip(((wp[:, 1] + half) / R).astype(int), 0, n - 1), np.clip(((wp[:, 0] + half) / R).astype(int), 0, n - 1)] - z0
        m = len(wp)
        pos.append(np.vstack([np.column_stack([wp, wt]), np.column_stack([wp, wb])]))
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
        # roof deviation stats (house3d.py): core of the roof, canopy removed, relative to the house median, robust spread
        core = ~tree_pts & contains_xy(fp.buffer(-0.5), ex_[inside], ny_[inside]) if not fp.buffer(-0.5).is_empty else ~tree_pts
        dd = (z[inside] - grid[pj, pi])[core]
        dd = dd[np.abs(dd) < 1.5]
        if len(dd) > 20:
            c_ = dd - np.median(dd)
            roof_dev = {"spread_cm": round(float(1.4826 * np.median(np.abs(c_))) * 100, 1), "p95_cm": round(float(np.percentile(np.abs(c_), 95)) * 100, 1),
                        "below_8cm_pct": round(float((c_ < -0.08).mean()) * 100, 1), "above_8cm_pct": round(float((c_ > 0.08).mean()) * 100, 1)}
        else:
            roof_dev = None
        cl_lon, cl_lat = to_ll.transform(cx + fp.centroid.x * k, cy + fp.centroid.y * k)
        cls = (b_cls or "").lower()
        ov = {kk: vv for kk, vv in {"subtype": b_sub, "class": b_cls, "height_m": round(float(b_h), 1) if b_h else None,
                                    "num_floors": b_fl, "roof_shape": b_roof, "footprint_source": b_ds, "id": bid}.items() if vv is not None}
        houses.append({"id": hid, "ring": [[round(float(u_), 2), round(float(v_), 2)] for u_, v_ in fp.exterior.coords],
                       "garage": bool(fp.area < 45 or cls in ("garage", "garages", "carport", "shed")), "points": int((~tree_pts).sum()),
                       "planes": len(planes), "area_m2": round(fp.area, 1), "roof_dev": roof_dev,
                       "height_m": round(float(np.percentile(hgt, 95)), 1), "eave_m": round(float(np.percentile(hgt, 20)), 1),
                       "lon": round(cl_lon, 6), "lat": round(cl_lat, 6), "address": None, "zip": None,
                       "type": (b_sub or "").replace("_", " ").title() or None, "overture": ov})
        fps.append(fp)

    if not pos:
        sys.exit("no building footprints in this box (Overture returned none): pick a spot with buildings or a larger --half-m")
    P = np.vstack(pos).astype(np.float32); Nn = np.vstack(nrm).astype(np.float32)
    C = np.vstack(col).astype(np.uint8); I = np.vstack(idx).astype(np.uint32)
    print(f"{len(houses)} buildings meshed: {len(P):,} vertices, {len(I):,} triangles; {(house_of_point >= 0).sum():,} laser points on buildings")

    # ---------------------------------------------------------------- addresses -> buildings
    def assign(pts, label):
        if not pts:
            return 0
        lx, ly = to_local([p["lon"] for p in pts], [p["lat"] for p in pts])
        cand = {}
        for j, (px_, py_) in enumerate(zip(lx, ly)):
            pt = Point(px_, py_)
            inside_ = [i for i, f in enumerate(fps) if f.buffer(1.0).contains(pt)]
            if inside_:
                i = min(inside_, key=lambda i: fps[i].area)  # smallest containing footprint (garage inside lot polygons etc.)
                d = 0.0
            else:
                dists = [(fps[i].distance(pt), i) for i in range(len(fps)) if not houses[i]["garage"]]
                if not dists:
                    continue
                d, i = min(dists)
                if d > 25:
                    continue
            cand.setdefault(i, []).append((d, fps[i].centroid.distance(pt), j))
        nn = 0
        for i, lst in cand.items():
            if houses[i]["address"]:
                continue
            d, _, j = min(lst)
            houses[i]["address"] = pts[j]["address"]; houses[i]["zip"] = pts[j]["zip"]; houses[i]["address_source"] = pts[j]["src"]
            if len(lst) > 1:
                houses[i]["addresses_n"] = len(lst)
            nn += 1
        print(f"  {nn} buildings addressed from {label}")
        return nn

    n_ov = assign(addr_pts, "Overture addresses")
    n_osm = 0
    if any(not h["address"] and not h["garage"] for h in houses):
        try:
            from osm_extract import query as osm_query
            st_ = place.get("state")
            if st_ not in GEOFABRIK_US:
                raise RuntimeError(f"no Geofabrik US state extract for state {st_!r}")
            osm_region = GEOFABRIK_US[st_]
            addr = [("addr:housenumber", None)]
            els = osm_query(osm_region, nodes=addr, ways=addr, rels=addr, bbox=(W_ - dl, S_ - dt, E_ + dl, N_ + dt), match=osm_has_street_address)
            op = []
            for e_ in els:
                t_ = e_["tags"]
                op.append({"lon": e_["lon"], "lat": e_["lat"], "address": f"{t_['addr:housenumber']} {t_['addr:street']}",
                           "zip": (t_.get("addr:postcode") or "")[:5] or None, "src": f"OpenStreetMap addr:* (ODbL; Geofabrik {osm_region} extract)"})
            n_osm = assign(op, "OpenStreetMap")
        except Exception as e:  # noqa: BLE001
            print("  OSM address fallback failed:", e)
    n_tiger = 0
    if a.tiger_approx and place.get("county_fips") and any(not h["address"] and not h["garage"] for h in houses):
        try:
            n_tiger = tiger_approx(place["county_fips"], houses, fps, to_ll, cx, cy, k, (W_, S_, E_, N_))
            print(f"  {n_tiger} buildings got an approximate address from Census TIGER address ranges (address_approx)")
        except Exception as e:  # noqa: BLE001
            print("  TIGER address-range fallback failed:", e)
    for h in houses:  # the block's ZIP from the Census match when the address record had none
        if h["address"] and not h["zip"] and place.get("zip"):
            h["zip"] = place["zip"]

    # ---------------------------------------------------------------- 5. terrain mesh (1 m) + aerial colour
    sources = [{"name": f"USGS 3DEP LiDAR {proj['name']}", "detail": f"{proj.get('year') or '?'} · {dens:.0f} points/m² in this block, every point kept", "url": "registry.opendata.aws/usgs-lidar"},
               {"name": f"Overture Maps buildings {rel}", "detail": "footprints (OSM ODbL, Microsoft/Google/Esri CC-BY/ODbL)", "url": "overturemaps.org"},
               {"name": "Overture Maps addresses", "detail": f"{n_ov} buildings; only commercial-use licences ({', '.join(sorted(k for k in addr_src if not k.startswith('rejected')))[:160]})", "url": "overturemaps.org"},
               {"name": "US Census Geocoder", "detail": "location, state/county/tract/block", "url": "geocoding.geo.census.gov"}]
    if n_osm:
        sources.append({"name": "OpenStreetMap addresses", "detail": f"{n_osm} buildings; © OpenStreetMap contributors, ODbL "
                        f"(local Geofabrik extract download.geofabrik.de/north-america/us/{osm_region}-latest.osm.pbf)", "url": "openstreetmap.org/copyright"})
    aerial_src = None
    try:
        r = get("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export",
                params={"bbox": f"{box[0]},{box[1]},{box[2]},{box[3]}", "bboxSR": 3857, "imageSR": 3857, "size": "2048,2048", "format": "jpg", "f": "image"}, timeout=180)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            (OUT / "aerial.jpg").write_bytes(r.content); aerial_src = "USGS National Map USGSImageryOnly"
            sources.append({"name": "USGS National Map orthoimagery", "detail": "ground texture (aerial.jpg)", "url": "basemap.nationalmap.gov"})
    except Exception as e:  # noqa: BLE001
        print("USGS imagery failed:", e)
    img_years, naip_items = [], {}
    if not a.no_naip:
        try:
            import planetary_computer
            from pystac_client import Client
            (OUT / "img").mkdir(exist_ok=True)
            cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
            items = list(cat.search(collections=["naip"], bbox=[W_, S_, E_, N_], max_items=60).items())
            from shapely.geometry import box as sbox, shape as sshape
            blk = sbox(W_, S_, E_, N_)
            for itm in sorted(items, key=lambda i: i.properties["datetime"]):
                yr = int(itm.properties["datetime"][:4])
                if sshape(itm.geometry).contains(blk):
                    naip_items[yr] = itm  # latest flight that year that covers the whole block
            for yr, itm in sorted(naip_items.items()):
                try:
                    arr = raster_to_block(itm.assets["image"].href, box, 1024, bands=[1, 2, 3])
                    Image.fromarray(np.clip(arr.transpose(1, 2, 0), 0, 255).astype(np.uint8)).save(OUT / "img" / f"{yr}.jpg", quality=88)
                    img_years.append(yr)
                except Exception as e:  # noqa: BLE001
                    print(f"  NAIP {yr} failed: {e}")
            print(f"NAIP years: {img_years}")
            if img_years:
                sources.append({"name": "USDA NAIP aerial imagery", "detail": f"{img_years[0]}–{img_years[-1]} ({len(img_years)} flights, ~0.6–1 m)", "url": "planetarycomputer.microsoft.com/dataset/naip"})
        except Exception as e:  # noqa: BLE001
            print("NAIP failed:", e)
    if not aerial_src and img_years:  # USGS service down: the newest NAIP is the ground texture
        Image.open(OUT / "img" / f"{img_years[-1]}.jpg").resize((2048, 2048)).save(OUT / "aerial.jpg", quality=88)
        aerial_src = f"NAIP {img_years[-1]}"

    T = 1.0
    tn = int(2 * half / T) + 1
    tx = -half + np.arange(tn) * T; ty = -half + np.arange(tn) * T
    TX, TY = np.meshgrid(tx, ty)
    TZ = G[np.clip(((TY + half) / R).astype(int), 0, n - 1), np.clip(((TX + half) / R).astype(int), 0, n - 1)] - z0
    tdzy, tdzx = np.gradient(TZ, T)
    tnv = np.column_stack([-tdzx.ravel(), -tdzy.ravel(), np.ones(TZ.size)]); tnv /= np.linalg.norm(tnv, axis=1, keepdims=True)
    tid = np.arange(tn * tn).reshape(tn, tn)
    ti = np.column_stack([tid[:-1, :-1].ravel(), tid[:-1, 1:].ravel(), tid[1:, 1:].ravel(),
                          tid[:-1, :-1].ravel(), tid[1:, 1:].ravel(), tid[1:, :-1].ravel()]).reshape(-1, 3)
    terrain = {"pos": np.column_stack([TX.ravel(), TY.ravel(), TZ.ravel()]).astype(np.float32),
               "nrm": tnv.astype(np.float32), "idx": ti.astype(np.uint32),
               "uv": np.column_stack([(TX.ravel() + half) / (2 * half), 1 - (TY.ravel() + half) / (2 * half)]).astype(np.float32)}

    # ---------------------------------------------------------------- points: EVERY laser return, nothing thinned
    kind = np.where(g, 0, np.where(house_of_point >= 0, 1, np.where(nr > 1, 2, 3))).astype(np.uint8)
    lo_i, hi_i = np.percentile(it[g], [2, 98]) if g.any() else np.percentile(it, [2, 98])
    q = np.zeros(len(x), dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")])
    q["x"] = np.round(ex_ * 100 / 2).astype(np.int16)
    q["y"] = np.round(ny_ * 100 / 2).astype(np.int16)
    q["z"] = np.clip(np.round((z - z0) * 100), 0, 65535)
    q["k"] = kind
    q["h"] = np.clip(np.round(np.maximum(hag, 0) * 8), 0, 255)
    q["r"] = np.where(kind == 1, np.clip(np.round(resid * 100 / 2), -127, 127), 0)
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

    (OUT / "mesh.txt").write_text(json.dumps({"houses": {"pos": b64(P), "nrm": b64(Nn), "col": b64(C), "idx": b64(I)},
                                              "terrain": {k_: b64(v) for k_, v in terrain.items()}}))
    dump_json(OUT, "houses.json", houses)
    yr = proj.get("year")
    name = place.get("matched_address") or f"{lat:.5f}, {lon:.5f}"
    dump_json(OUT, "meta.json", {
        "origin": [lon, lat], "half_m": half, "z_base": z0, "bounds": [W_, S_, E_, N_],
        "points_total": int(len(x)), "points_per_m2": round(dens, 1), "points_shipped": int(len(q)), "point_parts": nparts,
        "point_bytes": 10, "point_unit_m": 0.02, "house_points": int((house_of_point >= 0).sum()),
        "points_noise_excluded": n_noise,
        "lidar": f"USGS 3DEP {proj['name']}" + (f" ({yr})" if yr else ""),
        "slug": slug, "name": name, "address_query": a.address, "city": place.get("city") or place.get("place"),
        "state": place.get("state"), "state_fips": place.get("state_fips"), "county": place.get("county"),
        "county_fips": place.get("county_fips"), "fips": place.get("county_fips"),
        "tract": place.get("tract"), "block_geoid": place.get("block_geoid"), "zip": place.get("zip"), "tz": tz,
        "ept_project": proj["name"], "lidar_year": yr, "lidar_year_source": proj.get("year_source"), "ept_project_density": proj.get("density"),
        "ept_srs": src_epsg, "ept_candidates": cands[:6], "overture_release": rel, "aerial": aerial_src, "img_years": img_years,
        "buildings": len(houses), "houses": sum(1 for h in houses if not h["garage"]),
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes")})
    layers = {"pavement": [], "alleys": [], "planimetric": [], "cases311": [], "permits": [], "spc_hail": [], "hail_days": [], "img_years": img_years}
    old = OUT / "layers.json"
    if old.exists():  # keep layers that enrich_national.py added on an earlier run
        try:
            prev = json.load(open(old)); prev["img_years"] = img_years; layers = {**layers, **prev}
        except Exception:  # noqa: BLE001
            pass
    dump_json(OUT, "layers.json", layers)
    if n_tiger:
        sources.append({"name": "Census TIGER/Line address ranges", "detail": f"{n_tiger} approximate house numbers (address_approx, interpolated)", "url": "census.gov/geographies/mapping-files"})
    dump_json(OUT, "sources.json", sources, indent=1)
    write_files_list(OUT)
    tot = 0
    for f in sorted(OUT.rglob("*")):
        if f.is_file():
            tot += f.stat().st_size
            print(f"  {f.relative_to(OUT)}: {f.stat().st_size / 1e6:.2f} MB")
    print(f"  total {tot / 1e6:.1f} MB; {sum(1 for h in houses if h['address'])}/{len(houses)} buildings addressed")


if __name__ == "__main__":
    main()

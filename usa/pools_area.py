"""Every swimming pool in the City of Sacramento, its water state on every NAIP snapshot (2012-2024), and the official
parcel (house) that contains it.

  python usa/pools_area.py prep                       # boundary (TIGER 2024), 1 km tiles, Overture buildings split per tile
  python usa/pools_area.py detect [--tiles 631_4270 ...] [--workers 3] [--keep-img]   # heavy per-tile pass (resumable)
  python usa/pools_area.py finalize                   # states (per-tile references), web JSON (fast, re-runnable)
  python usa/pools_area.py render --tiles ...         # overview PNGs for the local accuracy check (never published)

Reuses usa/pools.py (radiometric normalisation `features`, water-pixel test, `site_stats`, state `classify`) without
editing it; usa/parcels_hoa.py `query` + PARCEL_SRC (Sacramento County parcels, CC0; APN + situs only, never owner,
mailing or sale fields); Overture buildings through DuckDB S3 (usa/build_block.overture_release).
Differences from pools.run_block, which assumes a 3D block with LiDAR:
  * the LiDAR "> 1.5 m above ground" roof/tree test is replaced by Overture footprints buffered by 1.0 m (roofs) and
    the per-snapshot NDVI test already inside the water rule (relative NDVI < -0.05: leaf canopy is never water)
  * the analysis unit is a 1 km UTM-10N tile (+40 m margin); a pool belongs to the tile and the city when its centre
    is inside them; references for the relative colour test are the tile's own clean pools, or the 3x3 tile window
    when the tile has < 4 on that snapshot; the colour-cast guard is applied the same way
  * blobs > 150 m2 (up to 1500 m2) that pass the same water + shape tests are kept as kind 'community'
Outputs: web/app/data/pools_area/sacramento/index.json + <tx>_<ty>.json.  Cache: usa/_cache/pools_area/sacramento/.
Method, results and accuracy check: usa/research/pools_area.md
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pools as P  # noqa: E402
from common import ROOT, _np_default, block_box_3857, raster_to_block  # noqa: E402

AREA = "sacramento"
NAME = "City of Sacramento, CA"
GEOID = "0664000"
TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2024/MapServer/28"
CACHE = ROOT / "usa" / "_cache" / "pools_area" / AREA
OUT = ROOT / "web" / "app" / "data" / "pools_area" / AREA
LOG = ROOT / "usa" / "_cache" / "pools_area" / "run.log"
R = P.R
TILE = 1000.0
MARGIN = 40.0
BLD_BUF = -1.5          # roof INTERIOR only: Overture footprints sit 1-3 m off NAIP (lean + misregistration)
MIN_M2, MAX_M2, BIG_MAX_M2 = 5.0, P.MAX_M2, 1500.0   # detected water area (edge pixels mix with deck: ~60-70 % of true)
WATER_STATES = ("clean_blue", "turquoise", "green_algae", "greenish_uncertain")
NAIP_LIC = "public domain (USDA FSA)"


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def utm():
    from pyproj import Transformer
    return Transformer.from_crs(4326, 32610, always_xy=True), Transformer.from_crs(32610, 4326, always_xy=True)


# ---------------------------------------------------------------- prep: boundary, tiles, buildings
def prep():
    import duckdb
    from shapely import wkb
    from shapely.geometry import box as sbox, mapping, shape
    from shapely.ops import transform
    from shapely.strtree import STRtree
    from common import get
    from usa.build_block import overture_release
    CACHE.mkdir(parents=True, exist_ok=True)
    bf = CACHE / "boundary.geojson"
    if not bf.exists():
        r = get(TIGER + "/query", params={"where": f"GEOID='{GEOID}'", "outFields": "GEOID,NAME,AREALAND,AREAWATER",
                                          "outSR": 4326, "f": "geojson"}, timeout=120)
        bf.write_text(r.text)
    fb = json.load(open(bf))["features"][0]
    city = shape(fb["geometry"])
    fwd, inv = utm()
    cu = transform(fwd.transform, city)
    x0, y0, x1, y1 = cu.bounds
    tiles = []
    for tx in range(int(x0 // TILE), int(x1 // TILE) + 1):
        for ty in range(int(y0 // TILE), int(y1 // TILE) + 1):
            b = sbox(tx * TILE, ty * TILE, (tx + 1) * TILE, (ty + 1) * TILE)
            if b.intersects(cu):
                ll = transform(inv.transform, b)
                lon, lat = inv.transform((tx + 0.5) * TILE, (ty + 0.5) * TILE)
                tiles.append({"id": f"{tx}_{ty}", "tx": tx, "ty": ty, "centre": [round(lon, 6), round(lat, 6)],
                              "bbox": [round(v, 6) for v in ll.bounds], "city_share": round(b.intersection(cu).area / b.area, 3)})
    json.dump({"tiles": tiles}, open(CACHE / "tiles.json", "w"))
    log(f"prep: {len(tiles)} tiles touch the city boundary ({fb["properties"]["NAME"]})")
    # Overture buildings for the whole city bbox once, then split per tile (each tile's file = its grid square)
    bdir = CACHE / "buildings"
    bdir.mkdir(exist_ok=True)
    if all((bdir / f"{t['id']}.json").exists() for t in tiles):
        return
    rel = overture_release()
    W, S_, E, N = city.bounds
    pad = 0.002
    t0 = time.time()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT id, ST_AsWKB(geometry) FROM read_parquet(
        's3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*', hive_partitioning=1)
        WHERE bbox.xmax > {W - pad} AND bbox.xmin < {E + pad} AND bbox.ymax > {S_ - pad} AND bbox.ymin < {N + pad}""").fetchall()
    log(f"prep: Overture {rel}: {len(rows)} buildings in the city bbox ({time.time() - t0:.0f}s)")
    geoms, ids = [], []
    for oid, gb in rows:
        gg = wkb.loads(bytes(gb))
        for k_, pg in enumerate([gg] if gg.geom_type == "Polygon" else list(gg.geoms)):
            geoms.append(pg); ids.append(oid if k_ == 0 else f"{oid}#{k_}")
    tree = STRtree(geoms)
    for t in tiles:
        g = Grid(t)
        W_, S2, E_, N_ = g.bounds_ll
        sel = tree.query(sbox(W_, S2, E_, N_))
        out = [[ids[j], [[round(x, 7), round(y, 7)] for x, y in geoms[j].exterior.coords]] for j in sel]
        json.dump({"release": rel, "buildings": out}, open(bdir / f"{t['id']}.json", "w"))
    log(f"prep: buildings split into {len(tiles)} tile files")


class Grid:
    """Square analysis grid around a tile: local metres = mercator metres / k (block convention), row 0 = south."""

    def __init__(self, t):
        from pyproj import Transformer
        lon, lat = t["centre"]
        self.H = TILE / 2 + MARGIN
        self.n = int(round(2 * self.H / R))
        self.cx, self.cy, self.k, self.box = block_box_3857(lon, lat, self.H)
        self.gx = -self.H + (np.arange(self.n) + 0.5) * R
        self.to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
        self.to_m = Transformer.from_crs(4326, 3857, always_xy=True)
        W, S_ = self.to_ll.transform(self.box[0], self.box[1]); E, N = self.to_ll.transform(self.box[2], self.box[3])
        self.bounds_ll = [W, S_, E, N]
        self.meta = {"origin": [lon, lat], "half_m": self.H, "bounds": self.bounds_ll}

    def ll(self, x, y):
        return self.to_ll.transform(self.cx + np.asarray(x) * self.k, self.cy + np.asarray(y) * self.k)

    def local(self, lon, lat):
        mx, my = self.to_m.transform(np.asarray(lon), np.asarray(lat))
        return (mx - self.cx) / self.k, (my - self.cy) / self.k

    def ring_ll(self, coords):
        c = np.asarray(coords, float)
        lo, la = self.ll(c[:, 0], c[:, 1])
        return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lo, la)]


def rasterize(g, polys, buf=0.0):
    from rasterio.features import rasterize as rz
    from rasterio.transform import from_origin
    tr = from_origin(-g.H, g.H, R, R)
    geoms = [p.buffer(buf, join_style=2) if buf else p for p in polys]
    m = rz(((gm, 1) for gm in geoms if not gm.is_empty), out_shape=(g.n, g.n), transform=tr, fill=0, dtype="uint8")
    return np.flipud(m).astype(bool)


def drop_collinear(c):
    """Remove vertices lying on a straight run (pixel staircase corners stay; no smoothing)."""
    c = [tuple(p) for p in c]
    if c[0] == c[-1]:
        c = c[:-1]
    out = []
    m = len(c)
    for i in range(m):
        a, b, d = c[i - 1], c[i], c[(i + 1) % m]
        if abs((b[0] - a[0]) * (d[1] - b[1]) - (b[1] - a[1]) * (d[0] - b[0])) > 1e-9:
            out.append(b)
    out.append(out[0])
    return out


def mask_ring(sl, m):
    """Outline of a cropped boolean site mask (detected pixels) -> local-metre ring of its largest part."""
    from rasterio.features import shapes
    from rasterio.transform import Affine
    from shapely.geometry import shape
    # cropped mask row 0 = south: affine maps (col,row) -> local metres with y increasing with row
    x0 = -Grid.H_ + sl[1].start * R
    y0 = -Grid.H_ + sl[0].start * R
    tr = Affine(R, 0, x0, 0, R, y0)
    polys = [shape(s) for s, v in shapes(m.astype(np.uint8), mask=m, transform=tr) if v == 1]
    if not polys:
        return None
    return drop_collinear(list(max(polys, key=lambda q: q.area).exterior.coords))


Grid.H_ = TILE / 2 + MARGIN


# ---------------------------------------------------------------- imagery (one NAIP mosaic per year, uint8 on disk)
def fetch_naip(g, tdir):
    """One mosaic per NAIP year over the tile grid, uint8 RGBN on disk (tile temp dir); 3 years fetched at once."""
    from concurrent.futures import ThreadPoolExecutor
    items = list(P._stac().search(collections=["naip"], bbox=g.bounds_ll, max_items=200).items())
    by_year = {}
    for it in items:
        by_year.setdefault(int(it.properties["datetime"][:4]), []).append(it)

    def one(y):
        f = tdir / f"naip_{y}.npz"
        its = sorted(by_year[y], key=lambda i: i.properties["datetime"])
        if not f.exists():
            arr = None; used = []; nb = 4
            for it in reversed(its):
                nb = len(it.assets["image"].extra_fields.get("eo:bands", [1, 2, 3, 4])) or 4
                a = None
                for attempt in range(3):
                    try:
                        a = raster_to_block(it.assets["image"].href, g.box, g.n, bands=list(range(1, min(nb, 4) + 1)), margin=0.02)
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            log(f"  NAIP {it.id} failed: {str(e)[:100]}")
                        time.sleep(3 * (attempt + 1))
                if a is None:
                    continue
                if a.shape[0] == 3:
                    a = np.concatenate([a, np.zeros((1, g.n, g.n), np.float32)])
                if arr is None:
                    arr = a
                else:
                    hole = arr[:3].sum(0) == 0
                    arr[:, hole] = a[:, hole]
                used.append(it)
                if (arr[:3].sum(0) > 0).mean() > 0.995:
                    break
            if arr is None:
                return None
            arr = np.flipud(np.clip(arr, 0, 255).round().astype(np.uint8).transpose(1, 2, 0)).copy()
            info = {"source": "USDA NAIP", "licence": NAIP_LIC, "gsd_m": float(used[0].properties.get("gsd") or 1.0),
                    "date": used[0].properties["datetime"][:10], "year": y, "items": [i.id for i in used], "nir": nb >= 4}
            np.savez(f, arr=arr, info=json.dumps(info))
            del arr
        z = np.load(f, allow_pickle=True)
        return {"file": f, **json.loads(str(z["info"]))}

    with ThreadPoolExecutor(3) as ex:
        return [s_ for s_ in ex.map(one, sorted(by_year)) if s_]


SHADE_WATER = True
NDWI_SHADE = 0.55   # relative NDWI (pools.py uses 0.40 together with the brightness and chroma tests)
SHIFT_MAX = 24   # px (12 m): NAIP ortho accuracy spec is +-6 m per flight, so years disagree by up to ~10 m


def _hp_grey(f):
    a = np.load(f, allow_pickle=True)["arr"][..., :3].astype(np.float32).mean(2)
    return a - ndimage.gaussian_filter(a, 4)


def coregister(snaps):
    """Integer-pixel shift of every year onto the newest year (phase correlation of high-passed brightness over the
    tile). Stored as s['shift'] = (drow, dcol) and applied in load_snap. Building footprints are not moved: they are
    only used as an eroded roof-interior mask, which tolerates the residual offset."""
    ref = _hp_grey(snaps[-1]["file"])
    Fr = np.fft.rfft2(ref)
    n0, n1 = ref.shape
    for s in snaps:
        if s is snaps[-1]:
            s["shift"] = [0, 0]; s["shift_peak"] = 1.0; continue
        b = _hp_grey(s["file"])
        X = Fr * np.conj(np.fft.rfft2(b))
        c = np.fft.irfft2(X / np.maximum(np.abs(X), 1e-6), s=ref.shape)
        c = np.fft.fftshift(c)
        cy, cx = n0 // 2, n1 // 2
        win = c[cy - SHIFT_MAX:cy + SHIFT_MAX + 1, cx - SHIFT_MAX:cx + SHIFT_MAX + 1]
        iy, ix = np.unravel_index(np.argmax(win), win.shape)
        s["shift"] = [int(iy - SHIFT_MAX), int(ix - SHIFT_MAX)]
        s["shift_peak"] = round(float(win.max() / max(np.median(np.abs(win)), 1e-9)), 1)
        if s["shift_peak"] < 4:   # no clear peak: leave the year unshifted
            s["shift"] = [0, 0]


def load_snap(s, BLD):
    z = np.load(s["file"], allow_pickle=True)
    arr = z["arr"].astype(np.float32)
    if any(s.get("shift", (0, 0))):
        arr = np.roll(arr, tuple(s["shift"]), axis=(0, 1))
    if not s["nir"]:
        arr[..., 3] = np.nan
    d = {"arr": arr, "nir": s["nir"]}
    P.features(d, BLD)
    d.pop("arr"); d.pop("ndwi_abs", None)
    # pool-blue surface without the NDWI test: solar / safety covers (blue, NIR-bright) and open water alike
    brn_ = (d["rn"] + d["gn"] + d["bn"]) / 3
    d["blue"] = d["valid"] & (d["bn"] > 1.5 * d["rn"]) & (d["gn"] > 1.15 * d["rn"]) & (d["bn"] >= 0.85 * d["gn"])         & ((d["bn"] - d["rn"]) > P.CHROMA * brn_) & (brn_ > P.DARK * d["ref_bright"]) & ~BLD
    if d["has_nir"] and SHADE_WATER:
        # shaded / dark-finish pool water: pools.py's rule needs brightness >= 0.55 x reference and a saturated colour,
        # which misses pools in house or tree shade. Water keeps a very high NDWI (NIR absorbed) in shade, roofs and
        # asphalt shadows do not; still bluish, not vegetation, not a roof interior.
        rn, gn, bn = d["rn"], d["gn"], d["bn"]
        brn = (rn + gn + bn) / 3
        d["water"] |= d["valid"] & (d["ndwi"] > NDWI_SHADE) & (d["ndvi"] < -0.15) & (bn > 1.15 * rn) & (gn > rn)             & (brn > 0.25 * d["ref_bright"]) & ~BLD
    return d


# ---------------------------------------------------------------- detect: one tile -> cache/tiles/<id>.json
def detect_tile(t, keep_img=False):
    from shapely.geometry import MultiPoint, Point, Polygon, shape
    from shapely.strtree import STRtree
    import parcels_hoa
    tf = CACHE / "tiles" / f"{t['id']}.json"
    if tf.exists():
        return t["id"], "cached"
    t0 = time.time()
    g = Grid(t)
    n = g.n
    tdir = CACHE / "img" / t["id"]
    tdir.mkdir(parents=True, exist_ok=True)
    fwd, inv = utm()
    city = shape(json.load(open(CACHE / "boundary.geojson"))["features"][0]["geometry"])

    ob = json.load(open(CACHE / "buildings" / f"{t['id']}.json"))
    bpolys = []
    for oid, ring in ob["buildings"]:
        c = np.asarray(ring)
        x, y = g.local(c[:, 0], c[:, 1])
        p = Polygon(np.column_stack([x, y])).buffer(0)
        if p.area > 4:
            bpolys.append((oid, p))
    BLD = rasterize(g, [p for _, p in bpolys], BLD_BUF)

    snaps = fetch_naip(g, tdir)
    t_fetch = time.time() - t0
    coregister(snaps)
    # pass 1: water masks of every year -> candidate sites (count of years a pixel sat in a pool-sized blob)
    count = np.zeros((n, n), np.int16)
    for s in snaps:
        d = load_snap(s, BLD)
        w = ndimage.binary_opening(d["water"] | d["blue"], iterations=1)
        lab, nl = ndimage.label(w)
        if nl:
            sz = np.bincount(lab.ravel()) * R * R
            okl = (sz >= 0.6 * MIN_M2) & (sz <= 1.3 * BIG_MAX_M2); okl[0] = False
            count += okl[lab]
        s["has_nir"] = d["has_nir"]
        del d, w, lab
    lab, nl = ndimage.label(ndimage.binary_closing(count > 0, iterations=1))
    sites, rej = [], {"area": 0, "shape": 0}
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        sl = (slice(max(sl[0].start - 2, 0), min(sl[0].stop + 2, n)), slice(max(sl[1].start - 2, 0), min(sl[1].stop + 2, n)))
        m = lab[sl] == i
        area = int(m.sum()) * R * R
        if area < MIN_M2 * 0.8 or area > BIG_MAX_M2 * 1.3:
            rej["area"] += 1; continue
        yy, xx = np.nonzero(m)
        pts_ = MultiPoint(np.column_stack([g.gx[xx + sl[1].start], g.gx[yy + sl[0].start]]))
        hull = pts_.convex_hull.buffer(R / 2, join_style=2)
        e_ = np.array(hull.minimum_rotated_rectangle.exterior.coords)
        sides = sorted([np.hypot(*(e_[1] - e_[0])), np.hypot(*(e_[2] - e_[1]))])
        aspect = sides[1] / max(sides[0], 0.1)
        fill = area / max(hull.area, 1)
        big = area > MAX_M2
        if aspect > (6 if big else 5) or fill < (0.5 if big else 0.6) or sides[0] < 1.8:
            rej["shape"] += 1; continue
        cnt = count[sl]
        core = m & (cnt >= max(1, math.ceil(cnt[m].max() / 2)))
        outline = core.copy()
        if core.sum() > 80:
            ce = ndimage.binary_erosion(core)
            core = ce if ce.sum() >= 30 else core
        c = pts_.centroid
        lon, lat = g.ll(c.x, c.y)
        ux, uy = fwd.transform(float(lon), float(lat))
        in_tile = int(ux // TILE) == t["tx"] and int(uy // TILE) == t["ty"]
        sites.append({"sl": sl, "mask": m, "core": core, "outline": outline, "x": c.x, "y": c.y, "aspect": aspect, "fill": fill,
                      "lon": float(lon), "lat": float(lat), "in_tile": in_tile,
                      "in_city": bool(in_tile and city.contains(Point(float(lon), float(lat)))), "stats": [], "water_px": [], "any_px": []})
    del lab
    # pass 2: per-year stats on the fixed site pixels
    for s in snaps:
        d = load_snap(s, BLD)
        s["ref_bright"] = d["ref_bright"]
        for st in sites:
            sl = st["sl"]
            sub = {k: d[k][sl] for k in ("rn", "gn", "bn", "water", "valid", "blue")}
            sub.update(has_nir=d["has_nir"], ref_bright=d["ref_bright"],
                       ndwi=d["ndwi"][sl] if d["has_nir"] else None, ndvi=d["ndvi"][sl] if d["has_nir"] else None)
            mm = st["core"] & sub["valid"]
            x = P.site_stats(sub, mm) if mm.sum() >= 0.6 * st["core"].sum() else None
            if x is not None:
                x["blue_frac"] = round(float((sub["blue"] | sub["water"])[mm].mean()), 2)
            st["stats"].append(x)
            st["water_px"].append(int((sub["water"] & st["mask"]).sum()))
            st["any_px"].append(int(((sub["water"] | sub["blue"]) & st["mask"]).sum()))
        del d
    kept = []
    for st in sites:
        # water (NDWI) on >= 2 years; or once plus a pool-blue surface (NIR-bright: solar / safety cover) on another
        # year; or water only on the newest year (a new pool). Blue surfaces alone (tarps, trampolines) never qualify.
        hit = [i for i, x in enumerate(st["stats"]) if x and x["water_frac"] >= 0.4]
        hit_any = [i for i, x in enumerate(st["stats"]) if x and (x["water_frac"] >= 0.4 or x["blue_frac"] >= 0.5)]
        part = any(x and x["water_frac"] >= 0.1 for x in st["stats"])   # NDWI-confirmed water on part of it once
        st["keep"] = len(hit) >= 2 or (len(hit) == 1 and (hit[0] == len(snaps) - 1 or len(hit_any) >= 2))             or (part and len(hit_any) >= 3)   # pool under a blue solar cover most years
        if not hit and st["keep"]:
            hit = hit_any
        wp = [st["water_px"][i] for i in hit] if len(hit) >= 2 and all(st["stats"][i]["water_frac"] >= 0.4 for i in hit) else [st["any_px"][i] for i in hit_any]
        st["area_m2"] = float(np.median(wp)) * R * R if wp else st["core"].sum() * R * R
        if st["keep"] and MIN_M2 <= st["area_m2"] <= BIG_MAX_M2:
            kept.append(st)

    # parcels (Sacramento County GIS, CC0): APN + situs only
    parcels_hoa.CACHE = CACHE / "parcels"
    parcels_hoa.CACHE.mkdir(exist_ok=True)
    src = parcels_hoa.PARCEL_SRC["Sacramento County"]
    feats = parcels_hoa.query(src["url"], g.bounds_ll, src["fields"], t["id"], "parcels")
    parcels = []
    for ft in feats:
        try:
            gm = shape(ft["geometry"])
        except Exception:  # noqa: BLE001
            continue
        for pg in ([gm] if gm.geom_type == "Polygon" else list(getattr(gm, "geoms", []))):
            c = np.array(pg.exterior.coords)[:, :2]
            x, y = g.local(c[:, 0], c[:, 1])
            lp = Polygon(np.column_stack([x, y])).buffer(0)
            if not lp.is_empty:
                parcels.append({"props": ft["properties"], "poly": lp, "ring": [[round(a, 6), round(b, 6)] for a, b in c]})
    ptree = STRtree([p["poly"] for p in parcels]) if parcels else None
    btree = STRtree([p for _, p in bpolys]) if bpolys else None

    out_sites = []
    for st in kept:
        pt = Point(st["x"], st["y"])
        hits = [parcels[j] for j in ptree.query(pt) if parcels[j]["poly"].contains(pt)] if ptree else []
        par = min(hits, key=lambda p: p["poly"].area) if hits else None
        pinfo = bring = None
        n_main = 0
        if par:
            pr = par["props"]
            nbr = str(pr.get("STREET_NBR") or "").strip()
            nbr = "" if nbr in ("0", "None") else nbr
            street = str(pr.get("STREET_NAM") or "").strip()
            addr = " ".join(v for v in (nbr, street) if v and v != "None")
            city_ = str(pr.get("CITY") or "").strip()
            pinfo = {"apn": pr.get("APN"), "address": (f"{addr}, {city_}" if city_ and city_ != "None" else addr) if nbr else None,
                     "ring": par["ring"], "area_m2": round(par["poly"].area)}
            inb = [bpolys[j] for j in btree.query(par["poly"]) if par["poly"].contains(bpolys[j][1].representative_point())] if btree else []
            n_main = sum(1 for _, b in inb if b.area >= 100)
            if inb:
                oid, bp = max(inb, key=lambda b: b[1].area)
                bring = g.ring_ll(list(bp.exterior.coords))
                pinfo["building_id"] = oid
                pinfo["building_area_m2"] = round(bp.area)
        ring = mask_ring(st["sl"], st["outline"])
        out_sites.append({"lon": round(st["lon"], 6), "lat": round(st["lat"], 6), "in_tile": st["in_tile"], "in_city": st["in_city"],
                          "area_m2": round(st["area_m2"], 1), "aspect": round(st["aspect"], 2), "fill": round(st["fill"], 2),
                          "ring": g.ring_ll(ring) if ring else None, "stats": st["stats"], "parcel": pinfo, "building_ring": bring,
                          "parcel_main_buildings": n_main})
    res = {"id": t["id"], "snapshots": [{k: s[k] for k in ("source", "licence", "gsd_m", "date", "year", "items", "nir", "has_nir", "shift", "shift_peak")} for s in snaps],
           "sites": out_sites, "candidates": len(sites), "rejected": rej, "dropped_one_off": len(sites) - len(kept),
           "buildings": len(bpolys), "parcels": len(parcels), "secs": round(time.time() - t0), "fetch_secs": round(t_fetch)}
    (CACHE / "tiles").mkdir(exist_ok=True)
    tmp = tf.with_suffix(".tmp")
    json.dump(res, open(tmp, "w"), separators=(",", ":"), default=_np_default)
    os.replace(tmp, tf)
    if not keep_img:
        shutil.rmtree(tdir, ignore_errors=True)
    return t["id"], f"{len(out_sites)} sites ({sum(s_['in_city'] for s_ in out_sites)} in tile+city), {len(snaps)} yrs, {res['secs']}s (fetch {res['fetch_secs']}s)"


def _detect_safe(t, keep_img):
    try:
        return detect_tile(t, keep_img)
    except Exception as e:  # noqa: BLE001
        return t["id"], "FAILED " + repr(e)[:200] + " " + traceback.format_exc()[-400:]


def detect(tile_ids=None, workers=3, keep_img=False):
    tiles = json.load(open(CACHE / "tiles.json"))["tiles"]
    if tile_ids:
        tiles = [t for t in tiles if t["id"] in tile_ids]
    todo = [t for t in tiles if not (CACHE / "tiles" / f"{t['id']}.json").exists()]
    # densest-first is unknown; go by city share so the urban core finishes early
    todo.sort(key=lambda t: -t["city_share"])
    log(f"detect: {len(tiles)} tiles, {len(tiles) - len(todo)} already done, {len(todo)} to run with {workers} workers")
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_detect_safe, t, keep_img) for t in todo]
        for f in as_completed(futs):
            tid, msg = f.result()
            done += 1
            el = time.time() - t0
            log(f"  [{done}/{len(todo)}] {tid}: {msg}   elapsed {el / 60:.1f} min, eta {el / done * (len(todo) - done) / 60:.0f} min")


def _pool_like(x):
    """Clear pool water on one flight: water test passed, not dark, cyan-blue hue, not greenish."""
    return bool(x and x["water_frac"] >= 0.4 and x["bright"] >= P.DARK and x["hue"] is not None
                and 185 <= x["hue"] <= 222 and x["greenness"] < 0.0)


# ---------------------------------------------------------------- finalize: states with per-tile references -> web JSON
def finalize():
    from shapely.geometry import shape
    tiles = json.load(open(CACHE / "tiles.json"))["tiles"]
    data = {}
    for t in tiles:
        f = CACHE / "tiles" / f"{t['id']}.json"
        if f.exists():
            data[(t["tx"], t["ty"])] = json.load(open(f))
    years = sorted({s["year"] for d in data.values() for s in d["snapshots"]})
    yi = {y: i for i, y in enumerate(years)}
    # per site: stats aligned to the global year list
    for d in data.values():
        tyears = [s["year"] for s in d["snapshots"]]
        for st in d["sites"]:
            al = [None] * len(years)
            for y, x in zip(tyears, st["stats"]):
                al[yi[y]] = x
            st["ystats"] = al
            st["small"] = st["area_m2"] <= MAX_M2

    def window(tx, ty, r=1):
        return [st for dx in range(-r, r + 1) for dy in range(-r, r + 1) for st in data.get((tx + dx, ty + dy), {"sites": []})["sites"]
                if st["in_tile"] and st["small"]]

    def refs_for(own, win, i):
        for pool_set, basis in ((own, "tile"), (win, "3x3 tiles")):
            good = [st["ystats"][i] for st in pool_set if st["ystats"][i] and st["ystats"][i]["water_frac"] > 0.6]
            if len(good) >= 4:
                return {"greenness": float(np.median([x["greenness"] for x in good])), "hue": float(np.median([x["hue"] or 0 for x in good])),
                        "greenness_q75": float(np.percentile([x["greenness"] for x in good], 75)), "n": len(good),
                        "basis": f"clean water this snapshot ({basis})"}
        good = [st["ystats"][i] for st in win if st["ystats"][i] and st["ystats"][i]["bright"] >= P.DARK]
        if len(good) >= 4:
            return {"greenness": float(np.median([x["greenness"] for x in good])), "hue": float(np.median([x["hue"] or 0 for x in good])),
                    "greenness_q75": float(np.percentile([x["greenness"] for x in good], 75)), "n": len(good), "basis": "all known pool sites (3x3 tiles)"}
        return None

    # pass A: references and raw states for every site of every tile
    for (tx, ty), d in data.items():
        own = [st for st in d["sites"] if st["in_tile"] and st["small"]]
        win = window(tx, ty)
        d["refs"] = [refs_for(own, win, i) for i in range(len(years))]
        for st in d["sites"]:
            st["states"] = [P.classify(x, r_) if x is not None else "no_data" for x, r_ in zip(st["ystats"], d["refs"])]
            # vegetation-looking median but pool-blue pixels still visible on a third of the site: canopy overhangs the
            # pool (seen on Land Park renders). Not judged -> 'dark' (shade / canopy, colour not judged), never 'filled'.
            st["states"] = [("dark" if z == "not_present" and x and x.get("blue_frac", 0) >= 0.3 else z)
                            for z, x in zip(st["states"], st["ystats"])]
    # pass B: colour-cast guard per tile (3x3 window when the tile has < 6 judged pools)
    for (tx, ty), d in data.items():
        meds = [r_["greenness"] for r_ in d["refs"] if r_]
        ref_med = float(np.median(meds)) if len(meds) >= 3 else None
        d["cast"] = []
        for i in range(len(years)):
            judged = [st["states"][i] for st in d["sites"] if st["in_tile"] and st["small"] and st["states"][i] in ("clean_blue", "turquoise", "green_algae")]
            if len(judged) < 6:
                judged = [st["states"][i] for st in window(tx, ty) if st["states"][i] in ("clean_blue", "turquoise", "green_algae")]
            share = sum(z == "green_algae" for z in judged) / max(len(judged), 1)
            shift = d["refs"][i]["greenness"] - ref_med if d["refs"][i] and ref_med is not None else 0.0
            c = len(judged) >= 6 and ((share > 0.25 and shift > 0.04) or share > 0.5)
            d["cast"].append(c)
    for (tx, ty), d in data.items():
        for st in d["sites"]:
            for i in range(len(years)):
                if d["cast"][i] and st["states"][i] == "green_algae":
                    st["states"][i] = "greenish_uncertain"
            wat = [i for i, z in enumerate(st["states"]) if z in WATER_STATES]
            st["first_i"] = wat[0] if wat else None
            for i in range(len(years)):
                x = st["ystats"][i] or {}
                if st["first_i"] is not None and i < st["first_i"] and st["states"][i] in ("covered_or_empty", "not_present", "green_algae") \
                        and x.get("water_frac", 0) < 0.1 and x.get("ndvi") is not None and x["ndvi"] > 0.15:
                    st["states"][i] = "not_present"
            if st["first_i"] is not None:
                for i in range(st["first_i"] + 1, len(years)):
                    if st["states"][i] == "not_present":
                        st["states"][i] = "filled_or_removed"
            st["confidence"] = round(min(0.35 + 0.12 * min(len(wat), 4) + (0.1 if st["fill"] > 0.8 else 0) + (0.05 if st["aspect"] < 3 else 0), 0.98), 2)

    # snapshots (global ids by NAIP year; each tile mosaics that year's flight(s), dates per tile vary by a few days)
    snap_meta = {}
    for d in data.values():
        for s in d["snapshots"]:
            m = snap_meta.setdefault(s["year"], {"id": f"naip_{s['year']}", "source": "USDA NAIP", "dates": set(), "res_m": s["gsd_m"],
                                                 "nir": True, "licence": NAIP_LIC})
            m["dates"].add(s["date"]); m["nir"] = m["nir"] and bool(s["has_nir"]); m["res_m"] = max(m["res_m"], s["gsd_m"])
    snapshots = []
    for y in years:
        m = snap_meta[y]
        ds = sorted(m.pop("dates"))
        snapshots.append({**m, "date": ds[len(ds) // 2], "date_range": [ds[0], ds[-1]]})
    sids = [s["id"] for s in snapshots]

    city = json.load(open(CACHE / "boundary.geojson"))["features"][0]
    src_parcel = __import__("parcels_hoa").PARCEL_SRC["Sacramento County"]
    rel = None
    sources = [{"name": "USDA NAIP aerial imagery (Microsoft Planetary Computer STAC 'naip')", "licence": NAIP_LIC,
                "url": "https://planetarycomputer.microsoft.com/dataset/naip"},
               {"name": src_parcel["name"], "licence": src_parcel["licence"], "url": src_parcel["licence_url"]},
               {"name": "Overture Maps buildings", "licence": "ODbL 1.0 (OpenStreetMap-derived parts) / CDLA-Permissive-2.0",
                "url": "https://docs.overturemaps.org/attribution/"},
               {"name": "US Census TIGER/Line 2024 Places (TIGERweb ACS2024 layer 28), Sacramento city GEOID 0664000",
                "licence": "public domain (US Government work)", "url": TIGER}]
    licence_note = ("Pool outlines are detected water pixels on public-domain NAIP (no synthesized shapes). Parcel rings, APNs and situs "
                    "addresses: Sacramento County GIS, CC0 (owner, mailing and sale fields never requested). Building rings: Overture Maps "
                    "(ODbL - attribute and share-alike for the building geometry). City boundary: Census TIGER (public domain). States "
                    "describe the NAIP capture day only, not how a pool is maintained.")
    OUT.mkdir(parents=True, exist_ok=True)
    totals = {"pools": 0, "residential": 0, "community": 0, "unknown": 0, "in_parcel": 0, "no_parcel": 0, "with_address": 0,
              "with_building": 0, "large_over_150m2": 0, "green_ever": 0, "latest_state": {}, "green_by_year": {}, "first_seen": {}}
    tiles_out = []
    pid = 0
    dropped = {"community": 0, "unknown": 0}
    weak_per_parcel = {}
    for d_ in data.values():
        for st in d_["sites"]:
            if st["in_city"] and st["parcel"] and sum(1 for x in st["ystats"] if _pool_like(x)) <= 2:
                weak_per_parcel[st["parcel"]["apn"]] = weak_per_parcel.get(st["parcel"]["apn"], 0) + 1
    for t in tiles:
        d = data.get((t["tx"], t["ty"]))
        if d is None:
            tiles_out.append({"id": t["id"], "bbox": t["bbox"], "status": "pending"}); continue
        pools = []
        for st in sorted([s for s in d["sites"] if s["in_city"]], key=lambda s: (-s["lat"], s["lon"])):
            pid += 1
            par = st["parcel"]
            if par:
                big_lot = par["area_m2"] > 4000 or st["parcel_main_buildings"] >= 3
                if st["area_m2"] > MAX_M2 or not par["address"] or big_lot:
                    kind, own = "community", "community / common lot"
                else:
                    kind, own = "residential", "house parcel"
            else:
                kind, own = ("community", "no parcel (large pool)") if st["area_m2"] > MAX_M2 else ("unknown", "no parcel")
            # false-positive screen (finalize only; see pools_area.md "False-positive screen"). A pool-like year is clear,
            # bright, cyan-blue water (the plain pools.py look). Streets, parking lots, river / pond water and roofs pass
            # the shade-water rule on some flights but almost never look like this twice.
            npl = sum(1 for x in st["ystats"] if _pool_like(x))
            nw = sum(1 for x in st["ystats"] if x and x["water_frac"] >= 0.4)
            ncov = sum(1 for x in st["ystats"] if x and x["water_frac"] < 0.4 and x.get("blue_frac", 0) >= 0.5)
            check = None
            if kind != "residential" and npl < 2:
                dropped[kind] += 1
                continue
            if kind != "residential" and npl <= 2 and par and weak_per_parcel.get(par["apn"], 0) >= 5:
                dropped[kind] += 1      # a cluster of marginal 'pools' on one big lot: pond / wetland / basin
                continue
            if kind != "residential" and npl == 2:
                check = "needs check: clear blue on only two flights (pond, fountain or roof possible)"
            if kind == "residential" and npl == 0 and nw <= 1 and ncov < 2:
                check = "needs check: water on one flight only and never clear blue (could be shade, tarp or paving)"
            elif kind == "residential" and npl == 0:
                check = "dark / green finish or shaded: never clear blue on any flight"
            states = {sid: z for sid, z in zip(sids, st["states"])}
            last = next((z for z in reversed(st["states"]) if z != "no_data"), None)
            green = sorted({years[i] for i, z in enumerate(st["states"]) if z == "green_algae"})
            pools.append({"id": f"{t['id']}-{len(pools) + 1}", "ring": st["ring"], "area_m2": st["area_m2"], "centre": [st["lon"], st["lat"]],
                          "states": states, "latest_state": last, "green_years": green,
                          "first_seen": str(years[st["first_i"]]) if st["first_i"] is not None else None,
                          "confidence": round(min(st["confidence"], 0.5), 2) if check and check.startswith("needs") else st["confidence"],
                          "parcel": par, "building_ring": st["building_ring"], "kind": kind, "ownership": own,
                          "pool_like_years": npl, "check": check})
        c = {"pools": len(pools), "residential": sum(p["kind"] == "residential" for p in pools),
             "community": sum(p["kind"] == "community" for p in pools), "unknown": sum(p["kind"] == "unknown" for p in pools),
             "in_parcel": sum(p["parcel"] is not None for p in pools), "no_parcel": sum(p["parcel"] is None for p in pools),
             "with_address": sum(bool(p["parcel"] and p["parcel"]["address"]) for p in pools),
             "with_building": sum(p["building_ring"] is not None for p in pools),
             "large_over_150m2": sum(p["area_m2"] > MAX_M2 for p in pools), "green_ever": sum(bool(p["green_years"]) for p in pools),
             "green_now": sum(p["latest_state"] == "green_algae" for p in pools),
             "needs_check": sum(bool(p["check"] and p["check"].startswith("needs")) for p in pools)}
        for k_ in c:
            if k_ in totals:
                totals[k_] += c[k_]
        totals["green_now"] = totals.get("green_now", 0) + c["green_now"]
        for p in pools:
            totals["latest_state"][p["latest_state"]] = totals["latest_state"].get(p["latest_state"], 0) + 1
            for y in p["green_years"]:
                totals["green_by_year"][str(y)] = totals["green_by_year"].get(str(y), 0) + 1
            if p["first_seen"]:
                totals["first_seen"][p["first_seen"]] = totals["first_seen"].get(p["first_seen"], 0) + 1
        tile_doc = {"tile": t["id"], "bbox": t["bbox"], "area": NAME, "snapshots": sids,
                    "references": [{"snapshot": sid, "n": (r_ or {}).get("n", 0), "basis": (r_ or {}).get("basis"), "colour_cast": bool(cc)}
                                   for sid, r_, cc in zip(sids, d["refs"], d["cast"])],
                    "pools": pools, "counts": c}
        if pools:
            json.dump(tile_doc, open(OUT / f"{t['id']}.json", "w", encoding="utf-8"), separators=(",", ":"))
        else:
            (OUT / f"{t['id']}.json").unlink(missing_ok=True)
        tiles_out.append({"id": t["id"], "bbox": t["bbox"], "status": "done", "file": f"{t['id']}.json" if pools else None, "counts": c})
    done = [t for t in tiles_out if t["status"] == "done"]
    totals["dropped_not_pool_water"] = dropped
    totals["needs_check"] = sum(t_["counts"].get("needs_check", 0) for t_ in done)
    totals["tiles_done"] = len(done)
    totals["tiles_total"] = len(tiles)
    totals["casts"] = {sid: sum(1 for d in data.values() if d["cast"][i]) for i, sid in enumerate(sids)}
    idx = {"name": NAME, "slug": AREA, "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "boundary": {"source": "US Census TIGER/Line 2024 Places", "geoid": GEOID, "name": city["properties"]["NAME"],
                        "geometry": {"type": city["geometry"]["type"],
                                     "coordinates": [[[round(x, 6), round(y, 6)] for x, y in r_] for r_ in city["geometry"]["coordinates"]]}},
           "bbox": [round(v, 6) for v in shape(city["geometry"]).bounds], "tile_size_m": TILE, "tile_crs": "EPSG:32610 (UTM 10N), id = <E km>_<N km>",
           "tiles": tiles_out, "snapshots": snapshots, "sources": sources, "licence_note": licence_note,
           "method": "usa/research/pools_area.md", "totals": totals}
    json.dump(idx, open(OUT / "index.json", "w", encoding="utf-8"), separators=(",", ":"))
    log(f"finalize: {len(done)}/{len(tiles)} tiles, totals {json.dumps(totals)}")
    return idx


# ---------------------------------------------------------------- local accuracy check (never published)
def render(tile_ids):
    from PIL import Image, ImageDraw
    tiles = {t["id"]: t for t in json.load(open(CACHE / "tiles.json"))["tiles"]}
    for tid in tile_ids:
        t = tiles[tid]
        g = Grid(t)
        fs = sorted((CACHE / "img" / tid).glob("naip_*.npz"))
        if not fs:
            log(f"render {tid}: no kept image"); continue
        z = np.load(fs[-1], allow_pickle=True)
        a = z["arr"][..., :3].astype(np.float32)
        lo, hi = np.percentile(a[a > 0], [1, 99.5])
        img = Image.fromarray(np.flipud(np.clip((a - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)))
        d = ImageDraw.Draw(img)
        n = g.n
        tf = OUT / f"{tid}.json"
        pools = json.load(open(tf))["pools"] if tf.exists() else []
        cache = json.load(open(CACHE / "tiles" / f"{tid}.json"))

        def px(lon, lat):
            x, y = g.local(lon, lat)
            return (float(x) + g.H) / R, n - (float(y) + g.H) / R
        cols = {"residential": (255, 0, 255), "community": (255, 160, 0), "unknown": (255, 255, 0)}
        letter = {"clean_blue": "B", "turquoise": "T", "green_algae": "G", "greenish_uncertain": "g", "covered_or_empty": "C",
                  "dark": "D", "filled_or_removed": "F", "not_present": "N", "no_data": "-"}
        fwd, inv = utm()
        sq = [inv.transform((t["tx"] + a_) * TILE, (t["ty"] + b_) * TILE) for a_, b_ in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
        d.line([px(*c) for c in sq], fill=(255, 0, 0), width=1)
        for st in cache["sites"]:
            if not st["in_city"] and st["ring"]:
                d.line([px(*c) for c in st["ring"]], fill=(160, 160, 160), width=2)
        for k_, p in enumerate(pools, start=1):
            if p["ring"]:
                d.line([px(*c) for c in p["ring"]], fill=cols[p["kind"]], width=2)
                x, y = px(*p["centre"])
                d.text((x + 9, y - 6), f"{k_}{letter.get(p['latest_state'], '?')}", fill=(255, 255, 255))
        img.save(CACHE / f"render_{tid}_full.png")
        h = n // 2
        for nm, (x0, y0) in {"nw": (0, 0), "ne": (h, 0), "sw": (0, h), "se": (h, h)}.items():
            img.crop((x0, y0, x0 + h, y0 + h)).save(CACHE / f"render_{tid}_{nm}.png")
        log(f"render {tid}: {len(pools)} pools drawn on {fs[-1].name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "detect", "finalize", "render", "all"])
    ap.add_argument("--tiles", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--keep-img", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("prep", "all"):
        prep()
    if a.cmd in ("detect", "all"):
        detect(a.tiles, a.workers, a.keep_img)
    if a.cmd in ("finalize", "all"):
        finalize()
    if a.cmd == "render":
        render(a.tiles)


if __name__ == "__main__":
    main()

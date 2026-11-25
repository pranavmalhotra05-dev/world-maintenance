"""Derived per-house intelligence from the laser points + open layers (run after house3d.py and osint.py).

  trees     individual trees found in the LiDAR canopy (top = local height maximum), height, crown size,
            distance to each house, overhang (USGS 3DEP LiDAR only; no species - the MSPtrees_2023 copy we used had no licence)
  sun       a full year of sun positions (pvlib) traced across the LiDAR surface: hours of direct sun on each roof,
            clear-sky irradiance kWh/m2/yr, share of roof shaded by trees/neighbours, usable solar kW
  yard      lot (Hennepin parcel) split into building / tree canopy / lawn / hard surface, front vs back yard,
            lawn greenness from NAIP 2023 near-infrared (NDVI) where reachable, else photo excess-green
  pools     blue water in the summer aerials on a flat, ground-level LiDAR surface
  parking   garage stalls (assessor), detached garage footprints, driveway/hard-surface area off the street
  stories   assessor stories vs LiDAR eave height (consistency check)
Writes houses.json (adds fields), trees.json
"""
import base64
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
import requests
from PIL import Image
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import Point, Polygon, shape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json"))
houses = json.load(open(OUT / "houses.json"))
layers = json.load(open(OUT / "layers.json"))
LON, LAT = meta["origin"]; HALF = meta["half_m"]; W, So, E, N = meta["bounds"]
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1"


def merge_json_houses(path, houses, keys=(), dict_keys=(), sub=None, drop=()):
    """Re-read houses.json just before writing and merge ONLY this script's keys (other lanes write other keys).
    keys: replaced whole; dict_keys: dict-merged (other sub-keys kept); sub: {key: [sub-keys]} set inside a dict; drop: removed."""
    cur = json.load(open(path, encoding="utf-8"))
    mine = {h.get("id", i): h for i, h in enumerate(houses)}
    for i, c in enumerate(cur):
        h = mine.get(c.get("id", i))
        if h is None:
            continue
        for k in keys:
            if k in h:
                c[k] = h[k]
        for k in dict_keys:
            if k in h:
                c[k] = h[k] if not isinstance(h[k], dict) else {**(c.get(k) if isinstance(c.get(k), dict) else {}), **h[k]}
        for k, subs in (sub or {}).items():
            if isinstance(h.get(k), dict):
                if not isinstance(c.get(k), dict):
                    c[k] = {}
                for s_ in subs:
                    if s_ in h[k]:
                        c[k][s_] = h[k][s_]
        for k in drop:
            c.pop(k, None)
    tmp = Path(str(path) + ".tmp")
    json.dump(cur, open(tmp, "w", encoding="utf-8"), separators=(",", ":"))
    tmp.replace(path)


def merge_json_dict(path, new, keys):
    """Same for a dict file (layers.json / extras.json): re-read, set only `keys`."""
    cur = json.load(open(path, encoding="utf-8")) if Path(path).exists() else {}
    for k in keys:
        if k in new:
            cur[k] = new[k]
    tmp = Path(str(path) + ".tmp")
    json.dump(cur, open(tmp, "w", encoding="utf-8"), separators=(",", ":"))
    tmp.replace(path)

# ---------------------------------------------------------------- decode every laser point
parts = []
for i in range(meta["point_parts"]):
    b = base64.b64decode((OUT / f"points_{i}.txt").read_text())
    parts.append(np.frombuffer(b, dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")]))
p = np.concatenate(parts)
u = meta.get("point_unit_m", 0.02)
px, py, pz = p["x"] * u, p["y"] * u, p["z"] / 100.0
kind, hag = p["k"], p["h"] / 8.0
print(f"{len(p):,} points")

R = 0.5
n = int(2 * HALF / R) + 1
ci = np.clip(((px + HALF) / R).astype(int), 0, n - 1); cj = np.clip(((py + HALF) / R).astype(int), 0, n - 1)
DSM = np.full(n * n, -np.inf); np.maximum.at(DSM, cj * n + ci, pz); DSM = DSM.reshape(n, n)
_, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DSM), return_indices=True); DSM = DSM[iy, ix]
g = kind == 0
DTM = np.full(n * n, np.inf); np.minimum.at(DTM, (cj * n + ci)[g], pz[g]); DTM = DTM.reshape(n, n)
_, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DTM), return_indices=True); DTM = ndimage.median_filter(DTM[iy, ix], 5)
CHM = DSM - DTM
VEG = np.zeros(n * n); np.maximum.at(VEG, (cj * n + ci)[kind == 2], hag[kind == 2]); VEG = VEG.reshape(n, n)
gx = -HALF + (np.arange(n) + 0.5) * R
GX, GY = np.meshgrid(gx, gx)
polys = [Polygon(h["ring"]) for h in houses]
BLD = np.zeros((n, n), bool)
for poly in polys:
    BLD |= contains_xy(poly, GX, GY)

# ---------------------------------------------------------------- trees: tops of the canopy height model
veg_h = ndimage.gaussian_filter(np.where(VEG > 2.5, VEG, 0), 1.0)
peaks = (veg_h == ndimage.maximum_filter(veg_h, size=9)) & (veg_h > 4.0) & ~BLD
lab, nl = ndimage.label(VEG > 2.5)
ty, tx = np.nonzero(peaks)
trees = []
for y_, x_ in zip(ty, tx):
    h_ = float(VEG[y_, x_])
    crown = lab == lab[y_, x_]
    # crown radius: canopy cells within 0.6*h of the top (simple watershed-free estimate)
    yy, xx = np.nonzero(crown[max(0, y_ - 24):y_ + 25, max(0, x_ - 24):x_ + 25])
    d = np.hypot(yy + max(0, y_ - 24) - y_, xx + max(0, x_ - 24) - x_) * R
    rad = float(np.percentile(d[d < 0.6 * h_ + 2], 70)) if (d < 0.6 * h_ + 2).any() else 2.0
    trees.append({"x": round(float(gx[x_]), 1), "y": round(float(gx[y_]), 1), "h": round(h_, 1), "r": round(min(max(rad, 1.5), 12), 1)})
print(f"{len(trees)} trees detected in the LiDAR")

# ---------------------------------------------------------------- sun: a year of sun positions traced over the LiDAR surface
times = pd.date_range("2025-01-01", "2025-12-31 23:00", freq="1h", tz="America/Chicago")
loc = pvlib.location.Location(LAT, LON, tz="America/Chicago", altitude=250)
sp = loc.get_solarposition(times)
cs = loc.get_clearsky(times, model="ineichen")
up = sp.apparent_elevation > 3
sp, cs = sp[up], cs[up]
sun_az = np.radians(sp.azimuth.values); sun_el = np.radians(sp.apparent_elevation.values)
sx_, sy_, sz_ = np.sin(sun_az) * np.cos(sun_el), np.cos(sun_az) * np.cos(sun_el), np.sin(sun_el)
# sample roof points per house; normal from the DSM gradient at that spot
dzy, dzx = np.gradient(DSM, R)


def lit(x0, y0, z0, k_):
    """Is the sun visible from (x0,y0,z0) along sun vector k_? March 1 m steps across the DSM up to 120 m."""
    for s_ in np.arange(1.0, 120.0, 1.0):
        x1, y1 = x0 + sx_[k_] / max(math.cos(sun_el[k_]), 1e-3) * 0 + sx_[k_] * s_ / max(math.hypot(sx_[k_], sy_[k_]), 1e-6), y0 + sy_[k_] * s_ / max(math.hypot(sx_[k_], sy_[k_]), 1e-6)
        if abs(x1) >= HALF or abs(y1) >= HALF:
            return True
        zr = z0 + math.tan(sun_el[k_]) * s_
        c, r_ = int((x1 + HALF) / R), int((y1 + HALF) / R)
        if DSM[r_, c] > zr + 0.3:
            return False
    return True


rng = np.random.default_rng(1)
step = 3  # every 3rd daylight hour keeps runtime sane; weights scale back up
sel_t = np.arange(0, len(sun_el), step)
house_pts = np.flatnonzero(kind == 1)
for hi, h in enumerate(houses):
    poly = polys[hi]
    inside = house_pts[contains_xy(poly.buffer(-0.4) if not poly.buffer(-0.4).is_empty else poly, px[house_pts], py[house_pts])]
    if len(inside) < 10:
        h["sun"] = None; continue
    smp = rng.choice(inside, min(30, len(inside)), replace=False)
    kwh, hours, shaded = [], [], []
    for q in smp:
        c, r_ = int((px[q] + HALF) / R), int((py[q] + HALF) / R)
        nrm = np.array([-dzx[r_, c], -dzy[r_, c], 1.0]); nrm /= np.linalg.norm(nrm)
        e_sum = 0.0; h_sum = 0; blocked = 0; possible = 0
        for k_ in sel_t:
            cosi = nrm[0] * sx_[k_] + nrm[1] * sy_[k_] + nrm[2] * sz_[k_]
            if cosi <= 0:
                continue
            possible += 1
            if lit(px[q], py[q], pz[q] + 0.2, k_):
                e_sum += cs.dni.values[k_] * cosi + cs.dhi.values[k_] * (1 + nrm[2]) / 2
                h_sum += 1
            else:
                blocked += 1
                e_sum += cs.dhi.values[k_] * (1 + nrm[2]) / 2
        kwh.append(e_sum * step / 1000); hours.append(h_sum * step); shaded.append(blocked / max(possible, 1))
    roof_m2 = len(inside) / meta["points_per_m2"]
    good = np.array(kwh) > 1000
    h["sun"] = {"clear_sky_kwh_m2_yr": round(float(np.median(kwh))), "direct_sun_hours_yr": int(np.median(hours)),
                "shaded_pct": round(float(np.mean(shaded)) * 100, 1), "roof_m2": round(roof_m2, 1),
                "good_solar_m2": round(roof_m2 * float(good.mean()), 1), "solar_kw": round(roof_m2 * float(good.mean()) * 0.6 * 0.2, 1)}
print("sun done")

# ---------------------------------------------------------------- lots, yards, lawn, pools, parking
par = S.get("https://gis.hennepin.us/arcgis/rest/services/HennepinData/LAND_PROPERTY/MapServer/1/query",
            params={"where": "1=1", "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
                    "outFields": "PID,HOUSE_NO,STREET_NM", "f": "geojson", "resultRecordCount": 2000}, timeout=120).json()["features"]
lots = []
for f in par:
    g_ = shape(f["geometry"])
    if g_.geom_type == "MultiPolygon":
        g_ = max(g_.geoms, key=lambda q: q.area)
    xy = np.array(g_.exterior.coords)
    lots.append((Polygon(np.column_stack([(xy[:, 0] - LON) * MX, (xy[:, 1] - LAT) * MY])), f["properties"]))

# lawn greenness: NAIP 2023 4-band (NIR) if reachable, else excess-green from the summer Hennepin photo
ndvi = None
try:
    import rasterio
    from pystac_client import Client
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    it = next(Client.open("https://earth-search.aws.element84.com/v1").search(collections=["naip"], bbox=[W, So, E, N], sortby=[{"field": "properties.datetime", "direction": "desc"}], max_items=1).items())
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", AWS_REQUEST_PAYER="requester"), rasterio.open(it.assets["image"].href) as src:
        bb = transform_bounds("EPSG:4326", src.crs, W, So, E, N)
        a = src.read(window=from_bounds(*bb, src.transform), out_shape=(4, n, n)).astype(float)
    ndvi = np.flipud((a[3] - a[0]) / np.maximum(a[3] + a[0], 1))
    green_src = f"NAIP {it.properties['datetime'][:10]} NDVI"
except Exception as e:
    print("NAIP unavailable, using photo excess-green:", str(e)[:120])
summer = next((y for y in (2023, 2022, 2025, 2024, 2021) if (OUT / "img" / f"{y}.jpg").exists()), None)
photo = np.asarray(Image.open(OUT / "img" / f"{summer}.jpg").convert("RGB").resize((n, n))).astype(float)
photo = np.flipud(photo)  # row 0 = south, like our grids
if ndvi is None:
    rr, gg, bb_ = photo[..., 0], photo[..., 1], photo[..., 2]
    ndvi = (2 * gg - rr - bb_) / np.maximum(rr + gg + bb_, 1)
    green_src = f"Hennepin {summer} photo excess-green"
LAWN = (ndvi > (0.25 if green_src.startswith("NAIP") else 0.06)) & (CHM < 1.0) & ~BLD
CANOPY = VEG > 2.0
HARD = ~LAWN & ~CANOPY & ~BLD & (CHM < 0.5)
# pools: strongly blue pixels, flat and at/under ground level
rr, gg, bb_ = photo[..., 0], photo[..., 1], photo[..., 2]
POOL = (bb_ > gg + 12) & (bb_ > rr + 35) & (gg > rr) & (CHM < 0.6) & ~BLD
streets = [np.array(s["path"]) for s in layers["pavement"]]


def street_dist(x, y):
    best = 1e9
    for pth in streets:
        d = np.hypot(pth[:, 0] - x, pth[:, 1] - y).min()
        best = min(best, d)
    return best


cell_area = R * R
plan = layers.get("planimetric", [])
for hi, h in enumerate(houses):
    c = polys[hi].centroid
    lot = next(((lp, pr) for lp, pr in lots if lp.contains(c)), None)
    if lot is None:
        h["yard"] = None; continue
    lp = lot[0]
    m = contains_xy(lp, GX, GY)
    # front yard: the lot cells nearer the street than the house centre is
    hd = street_dist(c.x, c.y)
    ys_, xs_ = np.nonzero(m)
    front = np.zeros_like(m)
    sub = np.array([street_dist(gx[x_], gx[y_]) for y_, x_ in zip(ys_[::4], xs_[::4])])
    thr = hd
    for (y_, x_), d in zip(list(zip(ys_[::4], xs_[::4])), sub):
        if d < thr:
            front[max(0, y_ - 1):y_ + 3, max(0, x_ - 1):x_ + 3] = True
    front &= m
    back = m & ~front
    tot = m.sum() * cell_area
    h["yard"] = {"lot_m2": round(tot, 1), "building_pct": round((m & BLD).sum() * cell_area / tot * 100, 1),
                 "canopy_pct": round((m & CANOPY).sum() * cell_area / tot * 100, 1),
                 "lawn_m2": round((m & LAWN).sum() * cell_area, 1), "lawn_pct": round((m & LAWN).sum() * cell_area / tot * 100, 1),
                 "front_lawn_m2": round((front & LAWN).sum() * cell_area, 1), "back_lawn_m2": round((back & LAWN).sum() * cell_area, 1),
                 "hard_m2": round((m & HARD).sum() * cell_area, 1),
                 "lawn_greenness": round(float(np.nanmean(ndvi[m & LAWN])), 3) if (m & LAWN).any() else None, "greenness_source": green_src}
    pool_cells = ndimage.label(m & POOL)[0]
    sizes = np.bincount(pool_cells.ravel())[1:] * cell_area if pool_cells.max() else np.array([])
    h["pool"] = {"likely": bool((sizes >= 8).any()), "area_m2": round(float(sizes.max()), 1) if len(sizes) else 0}
    # parking: garages + hard surface in the back half (alley side) + driveway lines in the lot
    garages = [i for i, q in enumerate(polys) if houses[i].get("garage") and lp.contains(q.centroid)]
    A = h.get("assessor") or {}
    drive_lines = sum(1 for ln in plan if (ln.get("SYMBOL_NAME") or "").upper().find("DRIVE") >= 0 and lp.contains(Point(*ln["path"][0])))
    h["parking"] = {"garage_stalls": A.get("garagestalls"), "detached_garages": len(garages),
                    "garage_m2": round(sum(polys[i].area for i in garages), 1), "paved_m2": round((m & HARD).sum() * cell_area, 1),
                    "driveway_lines": drive_lines,
                    "est_spots": int((A.get("garagestalls") or 0) + max(0, round(((back & HARD).sum() * cell_area) / 14 - 0.5)))}
    # stories: assessor vs LiDAR eave height (~2.7 m per storey plus ~0.6 m foundation)
    st = A.get("stories")
    est = max(1.0, round((h["eave_m"] - 0.6) / 2.7 * 2) / 2)
    h["stories_check"] = {"assessor": st, "lidar_estimate": est,
                          "agrees": None if st is None else abs(float(st) - est) <= 0.75}
    # trees touching this house
    near = [t for t in trees if poly_d < 1e9] if False else None
    td = [(t, polys[hi].distance(Point(t["x"], t["y"]))) for t in trees]
    close = [(t, d) for t, d in td if d < t["r"] + 3]
    h["trees_near"] = [{"h": t["h"], "crown_r": t["r"], "gap_m": round(d - t["r"], 1)} for t, d in sorted(close, key=lambda z: z[1])[:6]]
    h.pop("ash_nearby", None)  # needed species data (MSPtrees_2023, no licence) - removed

for h in houses:  # also clear species fields left from older runs on houses skipped above
    h.pop("ash_nearby", None)
    for t in h.get("trees_near") or []:
        t.pop("genus", None)
merge_json_houses(OUT / "houses.json", houses, keys=("sun", "pool", "parking", "stories_check", "trees_near"),
                  dict_keys=("yard",), drop=("ash_nearby",))
json.dump(trees, open(OUT / "trees.json", "w"), separators=(",", ":"))
ok = [h for h in houses if h.get("yard")]
print(f"lots matched {len(ok)}/{len(houses)}; pools {sum(1 for h in ok if h['pool']['likely'])}; "
      f"stories agree {sum(1 for h in ok if h['stories_check']['agrees'])}/{sum(1 for h in ok if h['stories_check']['agrees'] is not None)}; "
      f"greenness from {ok[0]['yard']['greenness_source'] if ok else '-'}")
print("solar kW median", np.median([h["sun"]["solar_kw"] for h in houses if h.get("sun")]),
      "| shaded % median", np.median([h["sun"]["shaded_pct"] for h in houses if h.get("sun")]))

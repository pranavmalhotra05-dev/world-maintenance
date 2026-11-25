"""Turn raw LiDAR + city records into one row of physical roof facts per building.

Steps
1. Reproject points to UTM 10N (true metres; Web Mercator stretches distances ~26% at SF's latitude).
2. Ground model: lowest ground return per 1 m cell, gaps (under houses) filled from nearest ground.
3. Roof surface model (0.5 m): highest single-echo, above-ground point per cell inside each footprint.
   Trees are separated by echo count: a laser pulse through leaves returns several echoes,
   a pulse hitting a roof returns one.
4. Per roof: slope (pitch), facing direction, true surface area, height, tree overhang.
5. Join to the assessor parcel for building facts only (address, use, units, year built) and to permits
   (last re-roof, solar).

No household fields and no ranking of households. DataSF Terms of Use section VI says users will not
"contact individuals whose information is represented by the Data", so this output carries no owner
occupancy (homeowner exemption), no sale or tenure dates, and no per-home lead scores or job values.
Only physical facts about each building; anything commercial is done on aggregates in the page.
"""
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import Point, shape, mapping

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEB = ROOT / "web" / "data"
WEB.mkdir(parents=True, exist_ok=True)
CFG = json.loads((ROOT / "config.json").read_text())
TODAY = date(2026, 11, 23)
UTM = CFG["utm_epsg"]

to_utm_from_3857 = Transformer.from_crs(3857, UTM, always_xy=True)
to_utm = Transformer.from_crs(4326, UTM, always_xy=True)
to_ll = Transformer.from_crs(UTM, 4326, always_xy=True)

# ---------------------------------------------------------------- points
print("loading points")
d = np.load(DATA / "lidar_points.npz")
px, py = to_utm_from_3857.transform(d["x"], d["y"])
pz = d["z"].astype(np.float64)
cls, rn, nr = d["cls"], d["rn"], d["nr"]
is_ground = cls == 2
keep = cls != 7  # 7 = noise
px, py, pz, cls, rn, nr, is_ground = px[keep], py[keep], pz[keep], cls[keep], rn[keep], nr[keep], is_ground[keep]
print(f"  {len(px):,} points")

X0, Y0 = math.floor(px.min()), math.floor(py.min())
X1, Y1 = math.ceil(px.max()), math.ceil(py.max())


def grid_reduce(x, y, v, res, how):
    """Rasterise points: per-cell max/min of v. Row index increases northward."""
    W = int((X1 - X0) / res) + 1
    H = int((Y1 - Y0) / res) + 1
    ix = ((x - X0) / res).astype(np.int64)
    iy = ((y - Y0) / res).astype(np.int64)
    idx = iy * W + ix
    order = np.argsort(idx, kind="stable")
    idx_s, v_s = idx[order], v[order]
    uniq, start = np.unique(idx_s, return_index=True)
    red = (np.maximum if how == "max" else np.minimum).reduceat(v_s, start)
    out = np.full(H * W, np.nan)
    out[uniq] = red
    return out.reshape(H, W)


# ---------------------------------------------------------------- ground model (1 m)
print("ground model")
G = grid_reduce(px[is_ground], py[is_ground], pz[is_ground], 1.0, "min")
missing = np.isnan(G)
_, (iy, ix) = ndimage.distance_transform_edt(missing, return_indices=True)
G = G[iy, ix]


def ground_at(x, y):
    ix = np.clip(((x - X0) / 1.0).astype(int), 0, G.shape[1] - 1)
    iy = np.clip(((y - Y0) / 1.0).astype(int), 0, G.shape[0] - 1)
    return G[iy, ix]


hag = pz - ground_at(px, py)  # height above ground
above = (~is_ground) & (hag > 1.5)
single = above & (nr == 1)
multi = above & (nr > 1) & (hag > 2.5)

RES = 0.5
ROOF = grid_reduce(px[single], py[single], pz[single], RES, "max")
VEG = grid_reduce(px[multi], py[multi], pz[multi], RES, "max")
VEG_N = grid_reduce(px[multi], py[multi], np.ones(multi.sum()), RES, "max")  # placeholder shape
H, W = ROOF.shape
# number of multi-echo points per cell: real canopy gives many, a roof edge gives a few
_ix = ((px[multi] - X0) / RES).astype(np.int64); _iy = ((py[multi] - Y0) / RES).astype(np.int64)
VEG_N = np.bincount(_iy * W + _ix, minlength=H * W).reshape(H, W)
VEG = np.where(VEG_N >= 3, VEG, np.nan)
cy = Y0 + (np.arange(H) + 0.5) * RES
cx = X0 + (np.arange(W) + 0.5) * RES

# ---------------------------------------------------------------- footprints
print("footprints")
fp_raw = json.loads((DATA / "footprints.json").read_text())
fps = []
for f in fp_raw:
    g_ll = shape(f["shape"])
    g = shapely.transform(g_ll, lambda c: np.column_stack(to_utm.transform(c[:, 0], c[:, 1])))
    if g.area < 12:  # sheds, noise
        continue
    fps.append({"id": f["sf16_bldgid"], "geom": g, "geom_ll": g_ll})


def footprint_mask():
    m = np.zeros((H, W), bool)
    for fp in fps:
        (r0, r1, c0, c1), inside = cells_in(fp["geom"])
        m[r0:r1, c0:c1] |= inside
    return ndimage.binary_dilation(m, iterations=2)  # 1 m margin: walls, eaves, gutters


def cells_in(geom, pad=0):
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(int((minx - X0) / RES) - pad, 0); c1 = min(int((maxx - X0) / RES) + pad + 1, W)
    r0 = max(int((miny - Y0) / RES) - pad, 0); r1 = min(int((maxy - Y0) / RES) + pad + 1, H)
    gx, gy = np.meshgrid(cx[c0:c1], cy[r0:r1])
    return (r0, r1, c0, c1), contains_xy(geom, gx, gy)


def roof_metrics(fp):
    g = fp["geom"]
    (r0, r1, c0, c1), inside = cells_in(g)
    if inside.sum() < 20:
        return None
    dsm = ROOF[r0:r1, c0:c1].copy()
    veg = VEG[r0:r1, c0:c1]
    valid = inside & ~np.isnan(dsm)
    coverage = valid.sum() / inside.sum()
    if valid.sum() < 15:
        return None
    # fill holes inside the outline, smooth sensor noise, then take slopes
    hole = inside & np.isnan(dsm)
    if hole.any():
        _, (iy, ix) = ndimage.distance_transform_edt(np.isnan(dsm), return_indices=True)
        dsm = np.where(hole, dsm[iy, ix], dsm)
    dsm = np.where(inside, dsm, np.nan)
    sm = ndimage.median_filter(np.nan_to_num(dsm, nan=np.nanmedian(dsm)), size=3)
    dzdy, dzdx = np.gradient(sm, RES)
    core = ndimage.binary_erosion(inside, iterations=2)
    if core.sum() < 10:
        core = inside
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360) % 360  # 0=N, 90=E, 180=S
    s = slope[core]
    a = aspect[core]
    roofish = s < 60  # steeper = wall/parapet step, not roof surface
    s, a = s[roofish], a[roofish]
    if len(s) < 10:
        return None
    flat = s < 8
    pitched = s >= 15
    cell_area = RES * RES
    surface_m2 = float(np.sum(cell_area / np.cos(np.radians(slope[inside].clip(0, 60)))))
    # facing: share of pitched cells per compass quadrant
    quad = lambda lo, hi: float(np.mean((a[pitched] >= lo) & (a[pitched] < hi))) if pitched.any() else 0.0
    south = quad(135, 225)
    # solar-usable: flat cells + pitched cells facing ESE..WSW with sane pitch
    good_pitch = (s >= 8) & (s <= 45) & (a >= 100) & (a <= 260)
    w = np.where(flat, 1.0, np.where(good_pitch, np.cos(np.radians(a - 180)) * 0.3 + 0.7, 0.0))
    solar_m2 = float(np.sum(w) * cell_area / max(len(s), 1) * inside.sum())
    gz = float(np.median(ground_at(np.array([g.centroid.x]), np.array([g.centroid.y]))))
    roof_z = float(np.nanmedian(dsm[inside]))
    # tree overhang: canopy within / around the footprint that rises above the roof line
    # canopy over the roof must rise >1 m above the roof surface at that spot
    over = inside & ~np.isnan(veg) & (veg > dsm + 1.0)
    overhang = float(over.sum() / max(inside.sum(), 1))
    (tr0, tr1, tc0, tc1), near = cells_in(g.buffer(4.0).difference(g), pad=0)
    tv = VEG[tr0:tr1, tc0:tc1]
    off_bldg = ~FPM[tr0:tr1, tc0:tc1]
    tall_tree_m2 = float(np.sum(near & off_bldg & ~np.isnan(tv) & (tv - gz > 4)) * cell_area)
    pitch_med = float(np.median(s[~flat])) if (~flat).sum() > 5 else float(np.median(s))
    return {
        "footprint_m2": round(g.area, 1),
        "roof_surface_m2": round(surface_m2, 1),
        "roof_height_m": round(roof_z - gz, 1),
        "pitch_deg": round(pitch_med, 1),
        "pitch_x12": round(math.tan(math.radians(pitch_med)) * 12, 1),
        "flat_share": round(float(flat.mean()), 2),
        "pitched_share": round(float(pitched.mean()), 2),
        "south_share": round(south, 2),
        "roof_type": "Flat / low-slope" if flat.mean() > 0.6 else ("Pitched" if pitched.mean() > 0.5 else "Mixed"),
        "solar_m2": round(min(solar_m2, surface_m2), 1),
        "overhang": round(min(overhang, 1.0), 2),
        "tree_m2_near": round(tall_tree_m2, 1),
        "lidar_coverage": round(float(coverage), 2),
        "ground_z": round(gz, 2),
    }


FPM = footprint_mask()
print("roof metrics")
for fp in fps:
    fp["m"] = roof_metrics(fp)
fps = [f for f in fps if f["m"]]
print(f"  {len(fps)} roofs measured")

# ---------------------------------------------------------------- parcels + permits
roll = json.loads((DATA / "assessor.json").read_text())
permits = json.loads((DATA / "permits.json").read_text())


def yr(s):
    try:
        return int(str(s)[:4])
    except Exception:
        return None


roof_hist, solar_hist = {}, {}
earliest = 9999
for p in permits:
    key = f"{p.get('block')}{p.get('lot')}"
    desc = (p.get("description") or "").lower()
    status = (p.get("status") or "").lower()
    y = yr(p.get("completed_date")) or yr(p.get("issued_date"))
    if not y or status in ("cancelled", "withdrawn", "disapproved", "filed", "plancheck"):
        continue
    earliest = min(earliest, yr(p.get("filed_date")) or y)
    is_solar = any(w in desc for w in ("solar", "photovoltaic", " pv "))
    is_roof = ("roof" in desc) and not is_solar
    if is_solar:
        solar_hist[key] = max(solar_hist.get(key, 0), y)
    if is_roof:
        roof_hist[key] = max(roof_hist.get(key, 0), y)
print(f"  permits: {len(roof_hist)} parcels re-roofed, {len(solar_hist)} with solar, records since {earliest}")

parcels = []
for r in roll:
    if not r.get("the_geom"):
        continue
    lon, lat = r["the_geom"]["coordinates"]
    x, y = to_utm.transform(lon, lat)
    parcels.append((Point(x, y), r))

# match each parcel to its main building: footprint containing the parcel point, else nearest
from shapely.strtree import STRtree
tree = STRtree([f["geom"] for f in fps])
taken = {}
for pt, r in parcels:
    hits = tree.query(pt, predicate="intersects")
    if len(hits) == 0:
        j = tree.nearest(pt)
        if fps[j]["geom"].distance(pt) > 12:
            continue
    else:
        j = max(hits, key=lambda k: fps[k]["geom"].area)
    # several parcels (condos) may hit one building: keep the first, count units
    taken.setdefault(j, []).append(r)


def addr(loc):
    # "0000 2334 LAWTON              ST0000" -> "2334 Lawton St"
    parts = loc.split()
    if len(parts) < 3:
        return loc.title()
    num = parts[1].lstrip("0") or parts[0].lstrip("0")
    street = " ".join(parts[2:])
    street = street[:-4] if street[-4:].isdigit() else street
    return f"{num} {street.title()}"


def num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


KWH_PER_KW = 1450                     # SF annual yield per kW of panels (fog-adjusted rough figure)
features = []
for j, rs in taken.items():
    f = fps[j]
    m = f["m"]
    r = rs[0]
    key = f"{r.get('block')}{r.get('lot')}"
    built = int(num(r.get("year_property_built"))) or None
    last_roof = roof_hist.get(key)
    units = int(num(r.get("number_of_units"), 1)) or 1
    squares = m["roof_surface_m2"] * 10.7639 / 100
    # fire-code setbacks, vents, skylights: ~30% of usable area ends up panelled; 210 W/m2 panels
    kw = min(m["solar_m2"] * 0.30 * 0.21, 10.0)
    lon, lat = f["geom_ll"].centroid.x, f["geom_ll"].centroid.y
    props = {
        "id": key,
        "address": addr(r.get("property_location", "")),
        "use": r.get("use_definition") or "",
        "units": units,
        "year_built": built,
        "last_reroof": last_roof,
        "roof_age_yrs": (TODAY.year - last_roof) if last_roof else None,
        "solar_permit": solar_hist.get(key),
        **m,
        "squares": round(squares, 1),
        "solar_kw": round(kw, 1),
        "solar_kwh_yr": round(kw * KWH_PER_KW, -1),
        "lon": round(lon, 6),
        "lat": round(lat, 6),
    }
    geom = f["geom_ll"]
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    features.append({"type": "Feature", "geometry": mapping(geom), "properties": props})

print(f"  {len(features)} buildings with roof facts")
fc = {"type": "FeatureCollection", "features": features,
      "meta": {"area": CFG["name"], "permits_since": earliest, "lidar": "USGS 3DEP CA_SanFrancisco_1_B23 (2023)",
               "per_square": [900, 1400],
               "built": TODAY.isoformat()}}
(WEB / "homes.geojson").write_text(json.dumps(fc, separators=(",", ":")))

# ---------------------------------------------------------------- sample of points for the 3D view
print("point sample")
rng = np.random.default_rng(7)
cxm, cym = (X0 + X1) / 2, (Y0 + Y1) / 2
olon, olat = to_ll.transform(cxm, cym)
idx_g = np.flatnonzero(is_ground); idx_r = np.flatnonzero(single); idx_v = np.flatnonzero(multi)
pick = np.concatenate([rng.choice(idx_g, min(len(idx_g), 120_000), replace=False),
                       rng.choice(idx_r, min(len(idx_r), 330_000), replace=False),
                       rng.choice(idx_v, min(len(idx_v), 110_000), replace=False)])
kind = np.concatenate([np.zeros(min(len(idx_g), 120_000)), np.ones(min(len(idx_r), 330_000)),
                       np.full(min(len(idx_v), 110_000), 2)]).astype(np.float32)
zmin = float(np.percentile(pz[is_ground], 1))
plon, plat = to_ll.transform(px[pick], py[pick])  # degree offsets: exact alignment with footprints
arr = np.stack([plon - olon, plat - olat, pz[pick] - zmin, np.clip(hag[pick], 0, 40), kind], axis=1).astype(np.float32)
# pack: lon/lat offsets in 2e-7 deg (~2 cm) as int16, height in cm as uint16, kind*64+height-above-ground as uint8
import base64
UNIT = 2e-7
q = np.zeros(len(arr), dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1")])
q["x"] = np.round(arr[:, 0] / UNIT); q["y"] = np.round(arr[:, 1] / UNIT)
q["z"] = np.clip(np.round(arr[:, 2] * 100), 0, 65535)
q["k"] = arr[:, 4].astype(np.uint8) * 64 + np.clip(np.round(arr[:, 3] * 1.5), 0, 63).astype(np.uint8)
assert np.abs(arr[:, :2]).max() / UNIT < 32767
(WEB / "points.txt").write_text(base64.b64encode(q.tobytes()).decode())
(WEB / "points.bin").unlink(missing_ok=True)
json.dump({"origin": [olon, olat], "count": int(len(arr)), "unit_deg": UNIT, "z_base": zmin, "bytes_per_point": 7},
          open(WEB / "points.json", "w"))
print(f"  {len(arr):,} points, origin {olon:.5f},{olat:.5f}")

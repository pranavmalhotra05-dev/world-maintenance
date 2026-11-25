"""Convert one 3D block (web/usa/blocks/<slug>/) into an intermediate scene that Blender reads (usa/render_blender.py).

  .venv/Scripts/python usa/render_export.py minneapolis-mn [phoenix-az ...]     (or --all)

Writes usa/_cache/renders/<slug>/scene.npz + scene.json. Nothing is invented: every array comes from the block's files.
  points      EVERY LiDAR return in points_*.txt (none thinned), xyz in metres above z_base, class k, colour sampled from
              the newest NAIP image at the point's x,y (ground/roof) or NAIP darkened with depth under the canopy (vegetation)
  houses      roof + wall triangles from mesh.txt (build_block.py surfaces fitted to the block's own LiDAR), per-face kind
              (1 roof, 2 wall) and building id; roof albedo = median NAIP colour inside the footprint (shrunk 0.6 m)
  terrain     1 m ground mesh from mesh.txt (LiDAR ground class), UV = NAIP pixel
  streets     streets.json from the streets lane if present (road polygons, lane lines, crosswalks, signals, street lamps)
  sun         NOAA solar position at the block's lon/lat for the chosen dates/times (golden hour, noon, night)
  cameras     hero oblique, top-down, street-level (1.7 m eye on the main street: streets.json centreline if present, else
              the line fitted through each house's 'facing' street point from houses.json), night (same as hero, lower)
"""
import argparse
import base64
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "renders"
PT_DTYPE = np.dtype([("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")])


# ------------------------------------------------------------------------------------------------ solar position (NOAA)
def solar_position(lon, lat, when_utc):
    """NOAA spreadsheet algorithm. Returns (elevation_deg, azimuth_deg clockwise from north)."""
    jd = when_utc.timestamp() / 86400.0 + 2440587.5
    T = (jd - 2451545.0) / 36525.0
    L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    Mr = math.radians(M)
    C = (math.sin(Mr) * (1.914602 - T * (0.004817 + 0.000014 * T)) + math.sin(2 * Mr) * (0.019993 - 0.000101 * T)
         + math.sin(3 * Mr) * 0.000289)
    true_long = L0 + C
    omega = 125.04 - 1934.136 * T
    lam = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam))))
    y = math.tan(math.radians(eps / 2)) ** 2
    L0r = math.radians(L0)
    eqt = 4 * math.degrees(y * math.sin(2 * L0r) - 2 * e * math.sin(Mr) + 4 * e * y * math.sin(Mr) * math.cos(2 * L0r)
                           - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * Mr))
    minutes = when_utc.hour * 60 + when_utc.minute + when_utc.second / 60
    tst = (minutes + eqt + 4 * lon) % 1440
    ha = tst / 4 - 180 if tst / 4 >= 0 else tst / 4 + 180
    latr, decr, har = math.radians(lat), math.radians(decl), math.radians(ha)
    cosz = math.sin(latr) * math.sin(decr) + math.cos(latr) * math.cos(decr) * math.cos(har)
    zen = math.degrees(math.acos(max(-1, min(1, cosz))))
    az = math.degrees(math.atan2(math.sin(har), math.cos(har) * math.sin(latr) - math.tan(decr) * math.cos(latr))) + 180
    elev = 90 - zen
    if elev > -0.575:  # standard refraction correction (degrees)
        te = math.tan(math.radians(elev))
        if elev > 5:
            r = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
        elif elev > -0.575:
            r = 1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)))
        else:
            r = -20.774 / te
        elev += r / 3600
    return elev, az % 360


def sun_times(lon, lat, tzname, date):
    """Golden hour (evening, sun at ~7 deg), solar noon, and night (sun ~-18 deg) for a local date."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        tz = timezone(timedelta(hours=round(lon / 15)))
    t0 = datetime(date.year, date.month, date.day, 0, 0, tzinfo=tz)
    samples = [(t0 + timedelta(minutes=m)) for m in range(0, 1440, 2)]
    el = [solar_position(lon, lat, s.astimezone(timezone.utc)) for s in samples]
    i_noon = int(np.argmax([e for e, _ in el]))
    evening = [i for i in range(i_noon, len(samples)) if el[i][0] <= 7.0]
    i_gold = evening[0] if evening else len(samples) - 1
    i_night = next((i for i in range(i_noon, len(samples)) if el[i][0] <= -5), len(samples) - 1)  # blue hour: lamps on, sky not yet black

    def pack(i, label):
        return {"label": label, "local": samples[i].strftime("%Y-%m-%d %H:%M %Z"),
                "utc": samples[i].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                "elevation": round(el[i][0], 2), "azimuth": round(el[i][1], 2)}
    return {"golden": pack(i_gold, "golden hour"), "noon": pack(i_noon, "solar noon"), "night": pack(i_night, "night (deep blue hour)")}


# ------------------------------------------------------------------------------------------------ block readers
def read_points(d, meta):
    parts = [np.frombuffer(base64.b64decode((d / f"points_{i}.txt").read_text()), dtype=PT_DTYPE)
             for i in range(int(meta.get("point_parts", 1)))]
    return np.concatenate(parts)


def read_mesh(d):
    m = json.loads((d / "mesh.txt").read_text())

    def dec(s, dt, w):
        return np.frombuffer(base64.b64decode(s), dtype=dt).reshape(-1, w)
    h, t = m["houses"], m["terrain"]
    houses = {"pos": dec(h["pos"], np.float32, 3), "col": dec(h["col"], np.uint8, 4), "idx": dec(h["idx"], np.uint32, 3)}
    terrain = {"pos": dec(t["pos"], np.float32, 3), "idx": dec(t["idx"], np.uint32, 3)}
    return houses, terrain


def newest_image(d, meta):
    """Newest NAIP year, unless an older NAIP flight (or the USGS orthophoto) is clearly sharper (>= 1.4x Laplacian
    variance at 1024 px): some state NAIP deliveries are resampled and come out blurred (e.g. Texas 2022)."""
    from scipy import ndimage
    yrs = sorted(meta.get("img_years") or [])
    cands = [(d / "img" / f"{y}.jpg", f"USDA NAIP {y}") for y in reversed(yrs) if (d / "img" / f"{y}.jpg").exists()]
    if (d / "aerial.jpg").exists():
        cands.append((d / "aerial.jpg", meta.get("aerial") or "aerial.jpg"))
    if not cands:
        raise FileNotFoundError("no imagery")

    def sharp(p):
        a = np.asarray(Image.open(p).convert("L").resize((1024, 1024)), float)
        return float(ndimage.laplace(a).var())
    sc = [sharp(p) for p, _ in cands]
    best = int(np.argmax(sc))
    if sc[best] >= 1.4 * sc[0]:
        p, src = cands[best]
        return p, f"{src} (sharper than {cands[0][1]})"
    return cands[0]


def sample_img(img, half, x, y):
    H, W = img.shape[:2]
    c = np.clip(((x + half) / (2 * half) * W).astype(int), 0, W - 1)
    r = np.clip(((half - y) / (2 * half) * H).astype(int), 0, H - 1)
    return img[r, c]


# ------------------------------------------------------------------------------------------------ streets.json (streets lane)
def _tris(rings, half):
    """Constrained-Delaunay triangles of open CCW rings, clipped to the LiDAR square. Returns {'xy', 'idx'} or None."""
    import shapely
    from shapely.geometry import Polygon, box
    sq = box(-half + 0.02, -half + 0.02, half - 0.02, half - 0.02)
    XY, IDX, base = [], [], 0
    for r in rings:
        if len(r) < 3:
            continue
        try:
            p = Polygon([(float(a[0]), float(a[1])) for a in r]).buffer(0).intersection(sq)
        except Exception:  # noqa: BLE001
            continue
        if p.is_empty:
            continue
        for g in getattr(p, "geoms", [p]):
            if g.geom_type != "Polygon" or g.area < 0.05:
                continue
            g = shapely.segmentize(g, 1.0)  # dense enough to follow the terrain when draped
            for t in shapely.constrained_delaunay_triangles(g).geoms:
                c = np.array(t.exterior.coords)[:3]
                XY.append(c); IDX.append([base, base + 1, base + 2]); base += 3
    if not XY:
        return None
    return {"xy": np.round(np.vstack(XY), 3).tolist(), "idx": IDX}


def _clip_path(path, half):
    from shapely.geometry import LineString, box
    if len(path) < 2:
        return []
    sq = box(-half + 0.02, -half + 0.02, half - 0.02, half - 0.02)
    g = LineString([(float(a[0]), float(a[1])) for a in path]).intersection(sq)
    return [np.round(np.array(s.coords), 3).tolist() for s in getattr(g, "geoms", [g]) if not s.is_empty and s.length > 0.2]


def parse_streets(d, half, img=None):
    """streets.json (usa/streets.py: OSM ODbL + city planimetrics) -> triangulated surfaces + paint + street furniture,
    clipped to the LiDAR square (the lane also writes up to 80 m beyond it on a DEM; outside the square we have no
    LiDAR, so nothing is drawn there)."""
    f = d / "streets.json"
    if not f.exists():
        return None
    try:
        s = json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return None
    val = (s.get("meta") or {}).get("validation") or {}
    if val.get("house_overlap_m2") != 0:
        # owner rule: road polygons are drawn only once the streets lane has validated them against the houses
        # (meta.validation.house_overlap_m2 == 0); until then the NAIP-textured LiDAR terrain shows the real roads
        print(f"  streets.json not used: meta.validation.house_overlap_m2 = {val.get('house_overlap_m2')!r} (needs 0)")
        return None
    out = {"attribution": (s.get("meta") or {}).get("attribution"), "licence": (s.get("meta") or {}).get("licence"),
           "validation": val}
    roads = [r for r in s.get("roads", []) if r.get("poly")]

    def is_concrete(r):
        sf = str(r.get("surface") or "").lower()
        if sf:
            return sf in ("concrete", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "paved") and sf != "asphalt"
        if img is None:
            return False
        P = np.array(r["poly"])[:, :2]  # untagged surface: bright in the NAIP photo = concrete (alleys mostly)
        c = sample_img(img, half, P[:, 0] * 0.9 + P[:, 0].mean() * 0.1, P[:, 1] * 0.9 + P[:, 1].mean() * 0.1)
        return float(np.median(c.mean(1))) > 140
    conc = [is_concrete(r) for r in roads]
    out["road_tris"] = _tris([r["poly"] for r, c in zip(roads, conc) if not c], half)
    out["road_concrete_tris"] = _tris([r["poly"] for r, c in zip(roads, conc) if c], half)
    # owner rule: only measured geometry. Sidewalks/driveways/curbs/markings are drawn only when their 'source' names a
    # measured dataset (city planimetrics, a marking survey); OSM-tag-derived buffers, lane lines synthesised from
    # lanes=* tags and inferred crosswalks are skipped - the aerial texture already shows the real paint.
    def measured(o):
        src = str(o.get("source") or "").lower()
        return any(w in src for w in ("planimetric", "marking survey", "markings dataset", "pavement marking", "survey")) and "osm" not in src
    out["sidewalk_tris"] = _tris([w["poly"] for w in s.get("sidewalks", []) if w.get("poly") and measured(w)], half)
    out["driveway_tris"] = _tris([w["poly"] for w in s.get("driveways", []) if w.get("poly") and measured(w)], half)
    out["crosswalk_tris"] = _tris([c["poly"] for c in s.get("crosswalks", []) if c.get("poly") and measured(c)], half)
    lines = []
    for ln in s.get("lines", []) + [dict(x, color=x.get("color", "white")) for x in s.get("stop_lines", [])]:
        if not measured(ln):
            continue
        for p in _clip_path(ln.get("path") or [], half):
            lines.append({"path": p, "width": float(ln.get("width_m") or 0.1), "colour": "yellow" if "yel" in str(ln.get("color")) else "white"})
    out["lines"] = lines
    out["curbs"] = [p for c in s.get("curbs", []) if measured(c) for p in _clip_path(c.get("path") or [], half)]

    def _num(v):
        try:
            return float(str(v).split()[0])
        except (TypeError, ValueError, IndexError):
            return None
    # street furniture: only physical objects with exact recorded coordinates and a source id. OSM highway=stop /
    # give_way / traffic_signals nodes are control points on the centreline, not poles -> never drawn. Nothing is moved:
    # an item whose recorded position falls inside a road polygon is skipped, not relocated.
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    try:
        allroad = unary_union([Polygon([(q_[0], q_[1]) for q_ in r["poly"]]).buffer(0) for r in roads])
    except Exception:  # noqa: BLE001
        allroad = None
    items = list(s.get("signals", [])) + list((s.get("furniture") or []))
    fpath = d / "furniture.json"
    if fpath.exists():
        try:
            fj = json.loads(fpath.read_text())
            items += fj if isinstance(fj, list) else (fj.get("items") or fj.get("furniture") or [])
        except Exception:  # noqa: BLE001
            pass
    sig, skipped = [], []
    for g in items:
        kind = str(g.get("kind") or "")
        sid = g.get("osm_node") or g.get("source_id") or g.get("id") or g.get("asset_id")
        src = str(g.get("source") or "")
        if "x" not in g or "y" not in g or abs(g["x"]) >= half or abs(g["y"]) >= half:
            continue
        if not sid or "control point" in src.lower() or ("osm" in src.lower() and kind in ("stop_sign", "give_way", "yield_sign", "traffic_signal")):
            skipped.append(kind or "?"); continue
        if allroad is not None and allroad.contains(Point(g["x"], g["y"])):
            skipped.append(kind + " (inside road)"); continue
        sig.append({"x": float(g["x"]), "y": float(g["y"]), "kind": kind, "heading": g.get("heading_deg"), "source": src, "source_id": sid,
                    "h": _num((g.get("lamp") or {}).get("height")) or _num(g.get("height_m"))})
    out["signals"] = sig
    out["furniture_skipped"] = skipped
    out["lamps"] = [{"xy": [g["x"], g["y"]], "h": g["h"] or 7.5, "heading": g["heading"]} for g in sig if g["kind"] == "street_lamp"]
    out["road_polys"] = [{"ring": [[float(a[0]), float(a[1])] for a in r["poly"]], "kind": r.get("kind"), "name": r.get("name")} for r in roads]
    out["counts"] = {k: (len(v["idx"]) if isinstance(v, dict) and "idx" in v else len(v)) for k, v in out.items()
                     if k.endswith("_tris") and v or k.endswith("_tris") and False or k in ("lines", "curbs", "signals", "lamps")}
    return out


# ------------------------------------------------------------------------------------------------ main street + cameras
def lidar_road_axis(px, py, k, inten, houses_json, half):
    """Asphalt returns little laser light: ground returns in the darkest 35 % of intensity mark pavement. Score every
    straight 7 m-wide band (2 deg x 1 m steps) by dark-ground cells minus bright-ground cells, never through a footprint,
    and return the best band's (centre point, direction) - the street the LiDAR itself sees."""
    g = k == 0
    if g.sum() < 2000:
        return None
    thr = np.percentile(inten[g], 35)
    n = int(2 * half)
    gi = np.clip((px[g] + half).astype(int), 0, n - 1); gj = np.clip((py[g] + half).astype(int), 0, n - 1)
    tot = np.zeros(n * n); dark = np.zeros(n * n)
    np.add.at(tot, gj * n + gi, 1); np.add.at(dark, gj * n + gi, (inten[g] <= thr).astype(float))
    frac = np.where(tot >= 2, dark / np.maximum(tot, 1), np.nan).reshape(n, n)
    road = np.nan_to_num(frac, nan=0.0) > 0.6
    bright = np.nan_to_num(frac, nan=1.0) < 0.3
    blocked = np.zeros((n, n), bool)
    try:
        from shapely import contains_xy
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        U = unary_union([Polygon(h["ring"]).buffer(1.0) for h in houses_json if h.get("ring") and len(h["ring"]) >= 4])
        cx, cy = np.meshgrid(np.arange(n) + 0.5 - half, np.arange(n) + 0.5 - half)
        blocked = contains_xy(U, cx.ravel(), cy.ravel()).reshape(n, n)
    except Exception:  # noqa: BLE001
        pass
    cx, cy = np.meshgrid(np.arange(n) + 0.5 - half, np.arange(n) + 0.5 - half)
    best = None
    for th in np.radians(np.arange(0, 180, 2)):
        u = np.array([math.cos(th), math.sin(th)]); nv = np.array([-u[1], u[0]])
        d = cx * nv[0] + cy * nv[1]
        for off in np.arange(-half + 6, half - 6, 1.0):
            band = np.abs(d - off) < 3.5
            if (band & blocked).sum() > 3:
                continue
            sc = (band & road).sum() - 0.6 * (band & bright).sum()
            if best is None or sc > best[0]:
                best = (sc, off * nv, u)
    if best is None or best[0] < 40:
        return None
    return best[1], best[2]


def main_street(houses_json, streets, half, address=None, lidar_axis=None):
    """(point, unit direction) of the block's main street in local metres."""
    if streets and streets.get("road_polys"):
        from shapely.geometry import Polygon, box
        sq = box(-half, -half, half, half)
        best = None
        addr = (address or "").split(",")[0].split()
        key = addr[1].lower() if len(addr) > 1 else None  # "4116 21ST AVE S" -> "21st"
        for r in streets["road_polys"]:
            if r.get("kind") == "intersection" or str(r.get("kind") or "").startswith("service"):
                continue
            try:
                g = Polygon(r["ring"]).buffer(0).intersection(sq)
            except Exception:  # noqa: BLE001
                continue
            if g.is_empty:
                continue
            score = g.area * (10 if key and str(r.get("name") or "").lower().split()[:1] == [key] else 1)  # the address's own street first
            if best is None or score > best[2]:
                best = (g, r, score)
        if best is not None:
            P = np.array(best[0].convex_hull.exterior.coords)
            mu = P.mean(0); u = np.linalg.svd(P - mu)[2][0]
            return mu, u / np.linalg.norm(u), f"streets.json road polygon ({best[1].get('name') or best[1].get('kind')})", best[0]
    if lidar_axis is not None:
        return lidar_axis[0], lidar_axis[1], "LiDAR: dark-intensity ground band (asphalt)", None
    pts = []
    for h in houses_json:
        f = h.get("facing") or {}
        if f.get("azimuth") is None or not f.get("street_m") or not h.get("ring"):
            continue
        ring = np.array(h["ring"]); c = ring.mean(0)
        a = math.radians(f["azimuth"])
        pts.append((round(f["azimuth"] / 90) % 4, c + (f["street_m"]) * np.array([math.sin(a), math.cos(a)])))
    if pts:
        groups = {}
        for g, p in pts:
            groups.setdefault(g, []).append(p)
        g, P = max(groups.items(), key=lambda kv: len(kv[1]))
        P = np.array(P)
        a = math.radians(g * 90)
        u = np.array([math.cos(a), -math.sin(a)])  # perpendicular to the facing direction = along the street
        mu = np.median(P, 0)
        return mu, u, f"houses.json facing ({len(P)} houses)", None
    return np.zeros(2), np.array([1.0, 0.0]), "block axis (no street data)", None


def fine_terrain(px, py, pz, k, terr_pos, half, step=0.5):
    """0.5 m ground surface straight from the LiDAR ground-class returns (median per cell); cells with no ground return
    (under roofs, dense shrubs) are filled from the nearest measured cell, then a light 3x3 median removes speckle.
    Falls back to the 1 m mesh.txt terrain when the block has too few ground returns."""
    from scipy import ndimage
    n = int(round(2 * half / step)) + 1
    g = k == 0
    if g.sum() < 2000:
        return None
    gi = np.clip(np.round((px[g] + half) / step).astype(int), 0, n - 1)
    gj = np.clip(np.round((py[g] + half) / step).astype(int), 0, n - 1)
    key = gj * n + gi
    order = np.argsort(key, kind="stable")
    ks, zs = key[order], pz[g][order]
    uniq, start, cnt = np.unique(ks, return_index=True, return_counts=True)
    Z = np.full(n * n, np.nan, np.float32)
    Z[uniq] = [float(np.median(zs[a:a + c])) for a, c in zip(start, cnt)]
    Z = Z.reshape(n, n)
    miss = np.isnan(Z)
    _, (iy, ix) = ndimage.distance_transform_edt(miss, return_indices=True)
    Z = ndimage.median_filter(Z[iy, ix], size=3)
    ax = -half + np.arange(n) * step
    TX, TY = np.meshgrid(ax, ax)
    pos = np.column_stack([TX.ravel(), TY.ravel(), Z.ravel()]).astype(np.float32)
    tid = np.arange(n * n).reshape(n, n)
    idx = np.column_stack([tid[:-1, :-1].ravel(), tid[:-1, 1:].ravel(), tid[1:, 1:].ravel(),
                           tid[:-1, :-1].ravel(), tid[1:, 1:].ravel(), tid[1:, :-1].ravel()]).reshape(-1, 3)
    return pos, idx.astype(np.int32), float(miss.mean())


def ground_z(terr_pos, x, y):
    d = (terr_pos[:, 0] - x) ** 2 + (terr_pos[:, 1] - y) ** 2
    return float(terr_pos[np.argmin(d), 2])


def export(slug, date):
    d = BLOCKS / slug
    meta = json.loads((d / "meta.json").read_text())
    houses_json = json.loads((d / "houses.json").read_text()) if (d / "houses.json").exists() else []
    half = float(meta["half_m"]); lon, lat = meta["origin"]
    unit = float(meta.get("point_unit_m", 0.02))
    q = read_points(d, meta)
    hm, tm = read_mesh(d)
    img_path, img_src = newest_image(d, meta)
    img = np.asarray(Image.open(img_path).convert("RGB"))

    px = q["x"].astype(np.float32) * unit; py = q["y"].astype(np.float32) * unit
    pz = q["z"].astype(np.float32) / 100.0
    k = q["k"].astype(np.uint8); hag = q["h"].astype(np.float32) / 8.0
    col = sample_img(img, half, px, py).astype(np.float32) / 255.0
    veg = k >= 2
    if veg.any():
        # under-canopy returns get the canopy's colour from above but darker the lower they sit in the column
        gi = np.clip(((px + half) / 2).astype(int), 0, int(half)); gj = np.clip(((py + half) / 2).astype(int), 0, int(half))
        key = gj * (int(half) + 1) + gi
        top = np.zeros((int(half) + 1) ** 2, np.float32)
        np.maximum.at(top, key[veg], hag[veg])
        rel = np.clip(hag / np.maximum(top[key], 0.5), 0, 1)
        col[veg] *= (0.55 + 0.45 * rel[veg])[:, None]

    # houses: per face kind/id
    hp, hc, hi = hm["pos"], hm["col"], hm["idx"].astype(np.int64)
    fkind = hc[hi[:, 0], 2].astype(np.uint8); fhid = hc[hi[:, 0], 0].astype(np.int32)
    albedo = {}
    try:
        from shapely.geometry import Polygon
        from shapely import contains_xy
        for h in houses_json:
            poly = Polygon(h["ring"]); core = poly.buffer(-0.6)
            if core.is_empty:
                core = poly
            mn = np.array(core.bounds)
            gx, gy = np.meshgrid(np.arange(mn[0], mn[2], 0.25), np.arange(mn[1], mn[3], 0.25))
            m = contains_xy(core, gx.ravel(), gy.ravel())
            if m.sum() < 4:
                continue
            c = sample_img(img, half, gx.ravel()[m], gy.ravel()[m]).astype(np.float32)
            lum = c.mean(1)
            c = c[(lum > np.percentile(lum, 25)) & (lum < np.percentile(lum, 95))] if len(c) > 20 else c  # drop shadowed/specular
            albedo[int(h["id"]) % 256] = (np.median(c, 0) / 255.0).round(4).tolist()
    except Exception as e:  # noqa: BLE001
        print("  roof albedo sampling failed:", e)

    streets = parse_streets(d, half, img)
    wall_types = {}  # assessor exterior-wall class per building (0 unknown -> plain)
    for h in houses_json:
        ew = str(((h.get("assessor") or {}).get("exteriorwall")) or "").lower()
        t = 0
        if any(w in ew for w in ("brick", "masonry")):
            t = 2
        elif any(w in ew for w in ("stucco", "concrete", "block", "eifs", "adobe")):
            t = 3
        elif "stone" in ew:
            t = 4
        elif any(w in ew for w in ("vinyl", "metal", "alum", "wood", "siding", "lap", "shingle", "hardi", "fiber", "frame", "steel", "masonite", "hardboard")):
            t = 1
        if t:
            wall_types[int(h["id"]) % 256] = t
    tp, tix = tm["pos"], tm["idx"].astype(np.int32)
    ft = fine_terrain(px, py, pz, k, tp, half)
    terrain_src = "mesh.txt 1 m ground grid"
    if ft is not None:
        tp, tix, fill = ft
        terrain_src = f"0.5 m median of LiDAR ground returns ({fill * 100:.0f}% of cells filled from nearest)"
    # wall feet: sink the bottom edge 0.3 m into the (finer) ground so no wall hovers on a few-cm terrain mismatch
    hp = hp.copy()
    wv = hc[:, 2] == 2
    if wv.any():
        tn = int(round(math.sqrt(len(tp)))); stp = float(tp[1, 0] - tp[0, 0])
        ii = np.clip(np.round((hp[wv, 0] + half) / stp).astype(int), 0, tn - 1)
        jj = np.clip(np.round((hp[wv, 1] + half) / stp).astype(int), 0, tn - 1)
        gz_ = tp[:, 2].reshape(tn, tn)[jj, ii]
        low = hp[wv, 2] < gz_ + 0.6
        z_ = hp[wv, 2].copy(); z_[low] = np.minimum(z_[low], gz_[low]) - 0.3
        hp[wv, 2] = z_
    sun = sun_times(lon, lat, meta.get("tz") or "UTC", date)

    # cameras ------------------------------------------------------------
    hz = [float(h.get("height_m") or 6) for h in houses_json] or [6.0]
    hmax = float(np.percentile(hz, 90))
    gz = float(np.median(tp[:, 2]))
    sa = math.radians(sun["golden"]["azimuth"])
    cam_az = sa - math.radians(55)  # camera on the sun side, 55 deg off: warm-lit facades, long shadows away from camera
    # real-estate drone framing: ~50 m up, ~39 deg down, 35 mm, aimed at the block's own address (local origin)
    sc_ = max(half / 60.0, 0.8)
    alt, hd_ = 50.0 * sc_, 62.0 * sc_
    hero = {"loc": [hd_ * math.sin(cam_az), hd_ * math.cos(cam_az), gz + alt], "target": [0.0, 0.0, gz + 3.0], "lens": 35, "fstop": 5.6}
    top = {"loc": [0.0, 0.0, gz + 420.0], "target": [0.0, 0.0, gz], "lens": 50, "ortho": 2 * half * 1.0}
    mu, u, street_src, road_poly = main_street(houses_json, streets, half, meta.get("name"), lidar_road_axis(px, py, k, q["i"].astype(np.float32), houses_json, half))
    sv = np.array([math.sin(sa), math.cos(sa)])
    if float(u @ sv) > 0:  # walk away from the low sun: facades ahead are side/front-lit, shadows run forward
        u = -u

    def centre(t):
        """Point on the street at axis position t: middle of the road polygon's cross-section if we have one."""
        p = mu + t * u
        if road_poly is None:
            return p
        from shapely.geometry import LineString
        nrm = np.array([-u[1], u[0]])
        x = road_poly.intersection(LineString([p - 60 * nrm, p + 60 * nrm]))
        if x.is_empty:
            return p
        segs = list(getattr(x, "geoms", [x]))
        seg = min(segs, key=lambda g_: g_.distance(LineString([p, p + 1e-3 * nrm])))
        c_ = np.array(seg.coords).mean(0)
        return c_
    # keep the eye off buildings and out of tree trunks/hedges: the first 30 m of the view must not cross any footprint
    # (+1.2 m) or pass through LiDAR returns 0.5-2.2 m above ground; if the guessed line is blocked, slide sideways
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
    blk = unary_union([Polygon(h["ring"]).buffer(1.2) for h in houses_json if h.get("ring") and len(h["ring"]) >= 4]) if houses_json else None
    low = (k >= 2) & (hag > 0.5) & (hag < 2.2)
    LP = np.column_stack([px[low], py[low]])

    def entry(off):
        q = mu + off * np.array([-u[1], u[0]])
        for t in np.linspace(-2 * half, 2 * half, 801):
            if (np.abs(q + t * u) < half - 4).all():
                return t, q
        return None, q

    def blocked(a_, b_):
        seg = LineString([a_, b_])
        if blk is not None and (seg.intersects(blk) or blk.contains(Point(a_))):
            return True
        if len(LP):
            d_ = b_ - a_; L_ = np.linalg.norm(d_); d_ = d_ / L_
            rel = LP - a_; along = rel @ d_; perp = np.abs(rel @ np.array([-d_[1], d_[0]]))
            return int(((along > 0) & (along < L_) & (perp < 0.6)).sum()) > 3
        return False
    best_ = None
    for off in sorted(np.arange(-8, 8.01, 0.5), key=abs):
        t0, q = entry(off)
        if t0 is None:
            continue
        a_ = centre(t0) if off == 0 else q + t0 * u
        b_ = centre(t0 + 30) if off == 0 else q + (t0 + 30) * u
        if not blocked(a_, b_):
            best_ = (a_, b_, off); break
    if best_ is None and road_poly is None:
        # no street polygon and the guessed line is blocked: search directions +-90 deg and offsets +-25 m for the clear
        # 30 m sight line closest to the houses' street points
        u0 = u
        for ang, off in sorted(((a_d, o_) for a_d in range(-90, 91, 10) for o_ in np.arange(-25, 25.01, 1.0)),
                               key=lambda z: abs(z[1]) + 0.25 * abs(z[0])):
            c_, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            u = np.array([c_ * u0[0] - s_ * u0[1], s_ * u0[0] + c_ * u0[1]])
            t0, q = entry(off)
            if t0 is None:
                continue
            a_, b_ = q + t0 * u, q + (t0 + 30) * u
            if not blocked(a_, b_):
                best_ = (a_, b_, off)
                street_src += f", turned {ang:+d} deg"
                break
        u = u0 if best_ is None else u
    if best_ is None:
        t0, q = entry(0.0)
        t0 = 0.0 if t0 is None else t0
        best_ = (centre(t0), centre(t0 + 30), 0.0)
    p0, look, off_ = best_
    if off_:
        street_src += f", shifted {off_:+.1f} m to a clear line of sight"
    e0 = ground_z(tp, *p0) + 1.7
    el_ = ground_z(tp, *look) + 1.7
    street = {"loc": [float(p0[0]), float(p0[1]), e0], "target": [float(look[0]), float(look[1]), el_ - 0.35], "lens": 35, "fstop": 2.8,
              "source": street_src}
    na = cam_az + math.radians(25)
    night = {"loc": [hd_ * 0.85 * math.sin(na), hd_ * 0.85 * math.cos(na), gz + alt * 0.7], "target": [0.0, 0.0, gz + 2.0], "lens": 35, "fstop": 4.0}

    out = CACHE / slug; out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "scene.npz",
             pts=np.column_stack([px, py, pz]).astype(np.float32), pts_k=k, pts_col=col.astype(np.float32), pts_hag=hag,
             house_pos=hp.astype(np.float32), house_idx=hi.astype(np.int32), house_fkind=fkind, house_fhid=fhid,
             terr_pos=tp.astype(np.float32), terr_idx=tix)
    density = float(meta.get("points_per_m2") or len(px) / (4 * half * half))
    sj = {"slug": slug, "name": meta.get("name"), "city": meta.get("city"), "state": meta.get("state"), "lon": lon, "lat": lat,
          "half_m": half, "z_base": meta.get("z_base"), "points": int(len(px)), "points_per_m2": density,
          "point_radius": round(float(min(0.16, max(0.035, 0.42 / math.sqrt(max(density, 1))))), 3),
          "image": str(img_path), "image_source": img_src, "terrain": terrain_src, "wall_types": wall_types, "roof_albedo": albedo, "streets": streets,
          "sun": sun, "cameras": {"hero": hero, "top": top, "street": street, "night": night},
          "lidar": meta.get("lidar"), "date": str(date)}
    (out / "scene.json").write_text(json.dumps(sj, indent=1))
    print(f"{slug}: {len(px):,} points, {len(hi):,} house tris, {len(tix):,} terrain tris, image {img_src}, "
          f"streets {streets['counts'] if streets else 'no'}, golden {sun['golden']['local']} el {sun['golden']['elevation']}, street cam via {street_src}")
    return out


VIEW_TEXT = {
    "hero": "oblique aerial at golden hour",
    "top": "straight-down orthographic view at solar noon",
    "street": "street level, 1.7 m eye height, looking down the main street at golden hour",
    "night": "oblique at blue hour/night lit only by street lamps and signals from streets.json",
}


def register(slug, max_bytes=1_500_000):
    """Shrink any still over 1.5 MB, then add renders/<view>.jpg to meta.files and describe them in meta.renders."""
    import os
    sys.path.insert(0, str(ROOT))
    from usa.blocklock import block_lock
    d = BLOCKS / slug
    log = json.loads((CACHE / slug / "render_log.json").read_text())
    sj = json.loads((CACHE / slug / "scene.json").read_text())
    renders = {}
    sun = sj.get("sun") or {}
    fallback = {"hero": "golden", "street": "golden", "top": "noon", "night": "night"}
    for view in ("hero", "top", "street", "night"):  # every still on disk, even if its log entry was lost
        if view not in log and (d / "renders" / f"{view}.jpg").exists():
            g = sun.get(fallback[view], {})
            log[view] = {"lighting": f"{g.get('label')} {g.get('local')} (sun {g.get('elevation')} deg up, azimuth {g.get('azimuth')} deg)",
                         "device": "OPTIX", "blender": "5.3.0"}
    for view, info in log.items():
        f = d / "renders" / f"{view}.jpg"
        if not f.exists():
            continue
        q = 88
        while f.stat().st_size > max_bytes and q > 50:
            q -= 6
            im = Image.open(f); im.load()
            tmp = f.with_suffix(".tmp.jpg"); im.save(tmp, quality=q, optimize=True); os.replace(tmp, f)
        cam = sj["cameras"].get(view, {})
        renders[view] = {"file": f"renders/{view}.jpg", "what": VIEW_TEXT.get(view, view), "lighting": info.get("lighting"),
                         "bytes": f.stat().st_size, "size": "1600x900", "engine": f"Blender {info.get('blender', '')} Cycles ({info.get('device')})".replace("  ", " "),
                         "seconds": info.get("seconds")}
        if view == "street":
            renders[view]["street_from"] = cam.get("source")
    if not renders:
        return
    common = {"data": "every LiDAR return drawn (none thinned); roofs/walls = LiDAR-fitted mesh.txt; ground = LiDAR ground returns textured with "
                      f"{sj.get('image_source')}; streets = {'streets.json (OSM ODbL + city data)' if sj.get('streets') else 'none (no streets.json yet; roads are only the NAIP photo)'}; "
                      "sun = NOAA solar position; a neutral dark plinth under the block edge is the only non-data object",
              "sun_date": sj.get("date"), "script": "usa/render_export.py + usa/render_blender.py"}
    with block_lock(d):
        raw = (d / "meta.json").read_text(encoding="utf-8")
        meta = json.loads(raw)
        pretty = raw.lstrip().startswith("{" + chr(10))  # keep whatever layout the file already has
        files = meta.get("files") or []
        for r in renders.values():
            if r["file"] not in files:
                files.append(r["file"])
        meta["files"] = files
        meta["renders"] = {**common, "views": renders}
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1) if pretty else json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, d / "meta.json")
    print(f"{slug}: registered {sorted(renders)} ({', '.join(str(r['bytes'] // 1024) + ' KB' for r in renders.values())})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", action="store_true", help="after Blender: add renders/*.jpg to meta.files + meta.renders")
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--date", default="2026-09-22", help="local date for the sun (default: September equinox)")
    a = ap.parse_args()
    slugs = sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists() and (p / "mesh.txt").exists()) if a.all else a.slugs
    date = datetime.strptime(a.date, "%Y-%m-%d").date()
    for s in slugs:
        try:
            register(s) if a.register else export(s, date)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: FAILED {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

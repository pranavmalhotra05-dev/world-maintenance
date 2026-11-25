"""Street scene for every 3D block, measured only (no prediction): road surfaces from the block's LiDAR paved
corridor, LiDAR curbs, exact OSM control points / crossings / lamps / footways, Minneapolis planimetric sidewalks.
Lane lines, crosswalk stripes and stop lines are emitted only from a licensed marking-line dataset (none found:
meta.markings_source = null). See usa/research/streets.md.

    .venv/Scripts/python -u -W ignore -m usa.streets [slug ...] [--refresh] [--render <slug>]

OSM is read only from Geofabrik state extracts via usa/osm_extract.py (ODbL 1.0, "(c) OpenStreetMap contributors";
streets.json is a Derivative Database -> share-alike). Output web/usa/blocks/<slug>/streets.json in local metres of
meta.origin (houses.json frame), z above meta.z_base, clipped to the LiDAR square; meta.validation.house_overlap_m2
must be 0.
"""
import argparse
import base64
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box as sbox, mapping
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, linemerge, substring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "streets"
CACHE.mkdir(parents=True, exist_ok=True)
PAD = 80.0
UA = "roofprint-usa-streets/0.1 (open-data research; street-scene geometry from OSM for 3D block viewer; python-requests)"
S = requests.Session()
S.headers["User-Agent"] = UA
MPLS_PLAN = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/PW_Planimetric_Lines/FeatureServer/0/query"
DEM = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"

# z lifts above the ground surface so the renderer does not z-fight (documented in meta.z_lift_m)
LIFT = {"road": 0.03, "mark": 0.05, "walk": 0.15, "drive": 0.06, "curb": 0.15}
CAR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
       "service", "road", "busway", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
WALK = {"footway", "path", "pedestrian", "cycleway", "steps", "bridleway"}
RANK = {"motorway": 9, "trunk": 8, "primary": 7, "secondary": 6, "tertiary": 5, "unclassified": 3, "residential": 3,
        "living_street": 2, "road": 3, "busway": 4, "service": 1}
ARTERIAL = {"motorway", "trunk", "primary", "secondary"}
MAJOR = ARTERIAL | {"tertiary", "busway"}


def base_kind(hw):
    return hw[:-5] if hw.endswith("_link") else hw


def lane_width(hw):
    b = base_kind(hw)
    if b in ARTERIAL:
        return 3.6
    if b in ("tertiary", "road", "busway"):
        return 3.3
    return 3.0


def parse_len(v):
    if not v:
        return None
    s = str(v).strip().lower().replace(",", ".")
    m = re.match(r"^([\d.]+)\s*(m|ft|')?", s)
    if not m:
        m2 = re.match(r"^(\d+)'\s*(\d+)\"?", s)
        return (int(m2.group(1)) * 0.3048 + int(m2.group(2)) * 0.0254) if m2 else None
    try:
        x = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) in ("ft", "'"):
        x *= 0.3048
    return x if 0.5 < x < 80 else None


def parse_int(v):
    try:
        return int(str(v).split(";")[0].strip())
    except (TypeError, ValueError):
        return None


# ================================================================ frames + terrain
class Frame:
    def __init__(self, meta):
        self.lon, self.lat = meta["origin"]
        self.half = float(meta["half_m"])
        self.fwd = Transformer.from_crs(4326, 3857, always_xy=True)
        self.inv = Transformer.from_crs(3857, 4326, always_xy=True)
        self.cx, self.cy = self.fwd.transform(self.lon, self.lat)
        self.k = 1 / math.cos(math.radians(self.lat))
        self.R = self.half + PAD

    def local(self, lon, lat):
        mx, my = self.fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return (mx - self.cx) / self.k, (my - self.cy) / self.k

    def ll(self, x, y):
        return self.inv.transform(self.cx + np.asarray(x, float) * self.k, self.cy + np.asarray(y, float) * self.k)

    def bbox_ll(self, extra=40.0):
        r = self.R + extra
        lo, la = self.ll([-r, r], [-r, r])
        return float(lo[0]), float(la[0]), float(lo[1]), float(la[1])  # W, S, E, N


class Terrain:
    """z above meta.z_base: LiDAR terrain mesh inside the block square, 3DEP DEM (offset to the mesh) outside."""

    def __init__(self, bdir, fr, slug):
        mesh = json.loads((bdir / "mesh.txt").read_text())
        pos = np.frombuffer(base64.b64decode(mesh["terrain"]["pos"]), np.float32).reshape(-1, 3)
        tn = int(round(math.sqrt(len(pos))))
        self.Z = pos[:, 2].reshape(tn, tn).astype(float)
        self.x0 = float(pos[0, 0]); self.y0 = float(pos[0, 1]); self.step = float(pos[1, 0] - pos[0, 0]); self.tn = tn
        self.half = fr.half
        self.dem = None; self.dem_src = None; self.dem_offset = None
        try:
            self._load_dem(fr, slug)
        except Exception as e:  # noqa: BLE001
            print("  DEM failed, extrapolating mesh edge:", str(e)[:120])

    def _load_dem(self, fr, slug):
        R = fr.R + 15
        n = int(2 * R)  # ~1 m pixels
        f = CACHE / f"dem_{slug}.npy"
        if f.exists():
            arr = np.load(f)
        else:
            bb = (fr.cx - R * fr.k, fr.cy - R * fr.k, fr.cx + R * fr.k, fr.cy + R * fr.k)
            last = None
            for i in range(4):
                try:
                    r = S.get(DEM, params={"bbox": ",".join(f"{v:.2f}" for v in bb), "bboxSR": 3857, "imageSR": 3857, "size": f"{n},{n}",
                                           "format": "tiff", "pixelType": "F32", "interpolation": "RSP_BilinearInterpolation",
                                           "f": "image"}, timeout=120)
                    if r.ok and r.content[:2] in (b"II", b"MM"):
                        break
                    last = RuntimeError(f"{r.status_code} {r.text[:120]}")
                except Exception as e:  # noqa: BLE001
                    last = e
                time.sleep(5 * (i + 1))
            else:
                raise last
            import rasterio
            with rasterio.MemoryFile(r.content) as mf, mf.open() as ds:
                arr = ds.read(1).astype(np.float32)
            np.save(f, arr)
        arr = np.where(arr < -1000, np.nan, arr)
        self.dem = arr; self.dem_R = fr.R + 15; self.dem_n = arr.shape[0]
        # offset: DEM -> mesh frame (mesh z is LiDAR ground minus z_base; DEM is NAVD88 metres)
        gx = self.x0 + np.arange(self.tn) * self.step
        GX, GY = np.meshgrid(gx, gx)
        d = self.Z.ravel() - self._dem_at(GX.ravel(), GY.ravel())
        self.dem_offset = float(np.nanmedian(d))
        self.dem_src = "USGS 3DEP 1/3 arc-second DEM (3DEPElevation ImageServer)"
        print(f"  DEM {arr.shape} offset to LiDAR mesh {self.dem_offset:+.2f} m (spread {np.nanstd(d):.2f} m)")

    def _dem_at(self, x, y):
        R, n = self.dem_R, self.dem_n
        px = (np.asarray(x) + R) / (2 * R) * n - 0.5
        py = (R - np.asarray(y)) / (2 * R) * n - 0.5  # row 0 = north
        return bilinear(self.dem, px, py)

    def _mesh_at(self, x, y):
        px = (np.asarray(x) - self.x0) / self.step
        py = (np.asarray(y) - self.y0) / self.step
        return bilinear(self.Z, px, py)

    def z(self, x, y):
        x = np.asarray(x, float); y = np.asarray(y, float)
        zm = self._mesh_at(x, y)  # edge-clamped
        if self.dem is None:
            return zm
        zd = self._dem_at(x, y) + self.dem_offset
        out = np.maximum(np.abs(x), np.abs(y)) - (self.half - 4)  # blend over the last 4 m of the square
        w = np.clip(out / 4.0, 0, 1)
        zd = np.where(np.isnan(zd), zm, zd)
        return zm * (1 - w) + zd * w


def bilinear(A, px, py):
    h, w = A.shape
    px = np.clip(px, 0, w - 1.0001); py = np.clip(py, 0, h - 1.0001)
    i = np.floor(px).astype(int); j = np.floor(py).astype(int)
    fx = px - i; fy = py - j
    return (A[j, i] * (1 - fx) * (1 - fy) + A[j, i + 1] * fx * (1 - fy) + A[j + 1, i] * (1 - fx) * fy + A[j + 1, i + 1] * fx * fy)


# ================================================================ data fetch
def osm_elements(fr, slug, meta, refresh):
    """OSM highway ways + tagged nodes for the clip box, read from a Geofabrik US-state .osm.pbf extract
    (usa/osm_extract.py, written by another lane; pyosmium fallback). No public Overpass / QLever: their usage
    terms do not clearly cover commercial use. Returns Overpass-shaped dicts:
      {"type":"way","id","tags","nodes":[ids],"geometry":[{"lon","lat"}]}, {"type":"node","id","lon","lat","tags"}."""
    f = CACHE / f"osmpbf_{slug}.json"
    if f.exists() and not refresh:
        d = json.loads(f.read_text())
        return d["elements"], d["via"]
    bb = fr.bbox_ll()
    els, via = None, None
    try:
        from usa import osm_extract as ox  # noqa: F401
        els, via = _via_helper(ox, bb, meta)
    except ImportError:
        pass
    if els is None:
        els, via = _via_pyosmium(bb, meta)
    f.write_text(json.dumps({"elements": els, "via": via}))
    return els, via


NODE_HW = {"traffic_signals", "stop", "give_way", "street_lamp", "crossing", "turning_circle", "turning_loop", "mini_roundabout"}


def _keep_node(t):
    return t.get("highway") in NODE_HW or "crossing" in t or "traffic_signals" in t


def _cid(lon, lat):
    """Synthetic node id from the 7-dp coordinate (osm_extract returns way vertices, not node refs; OSM ways that
    share a node share the exact coordinate, so junctions are recovered exactly)."""
    return int(round(lon * 1e7)) * 4_000_000_000 + int(round(lat * 1e7))


def _via_helper(ox, bb, meta):
    region = ox.STATES.get(meta.get("state"), meta.get("state"))
    W, So, E, N = bb
    nodes = [("highway", v) for v in sorted(NODE_HW)] + [("crossing", None), ("traffic_signals", None), ("crossing:markings", None)]
    feats = ox.query(region, nodes=nodes, ways=[("highway", None)], bbox=(W, So, E, N))
    els = []
    for f_ in feats:
        if f_["type"] == "n":
            if _keep_node(f_["tags"]):
                els.append({"type": "node", "id": _cid(f_["lon"], f_["lat"]), "osm_id": f_["id"], "lon": f_["lon"], "lat": f_["lat"], "tags": f_["tags"]})
        elif f_["type"] == "w" and len(f_.get("coords") or []) >= 2:
            cs = f_["coords"]
            els.append({"type": "way", "id": f_["id"], "tags": f_["tags"], "nodes": [_cid(x, y) for x, y in cs],
                        "geometry": [{"lon": x, "lat": y} for x, y in cs]})
    src = ox.download(region)
    return els, f"Geofabrik extract {src.name} via usa/osm_extract.py (pyosmium)"


from usa import osm_extract as _ox_states  # noqa: E402
GEOFABRIK_STATE = dict(_ox_states.STATES)  # all states (shared with usa/osm_extract.py)


def _via_pyosmium(bb, meta):
    """Fallback: scan the Geofabrik state extract (usa/_cache/osm/<state>-latest.osm.pbf, downloaded by the
    osm_extract lane, or here if absent) with pyosmium, keeping only what touches the clip bbox."""
    import osmium
    W, So, E, N = bb
    st = GEOFABRIK_STATE[meta["state"]] if len(meta.get("state", "")) == 2 else meta["state"].lower().replace(" ", "-")
    d = ROOT / "usa" / "_cache" / "osm"
    cands = sorted(d.glob(f"{st}*.osm.pbf")) if d.exists() else []
    if not cands:
        d.mkdir(parents=True, exist_ok=True)
        url = f"https://download.geofabrik.de/north-america/us/{st}-latest.osm.pbf"
        print("  downloading", url)
        with S.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            tmp = d / f"{st}-latest.osm.pbf.part"
            with open(tmp, "wb") as fh:
                for ch in r.iter_content(1 << 20):
                    fh.write(ch)
            os.replace(tmp, d / f"{st}-latest.osm.pbf")
        cands = [d / f"{st}-latest.osm.pbf"]
    pbf = cands[-1]
    els = []

    class H(osmium.SimpleHandler):
        def node(self, n):
            if W <= n.location.lon <= E and So <= n.location.lat <= N:
                t = {tg.k: tg.v for tg in n.tags}
                if t and _keep_node(t):
                    els.append({"type": "node", "id": _cid(n.location.lon, n.location.lat), "osm_id": n.id, "lon": n.location.lon, "lat": n.location.lat, "tags": t})

        def way(self, w):
            if "highway" not in w.tags:
                return
            try:
                geo = [(nd.lon, nd.lat) for nd in w.nodes]
            except osmium.InvalidLocationError:
                return
            if not any(W <= x <= E and So <= y <= N for x, y in geo):
                xs = [g[0] for g in geo]; ys = [g[1] for g in geo]
                if max(xs) < W or min(xs) > E or max(ys) < So or min(ys) > N:
                    return
            els.append({"type": "way", "id": w.id, "tags": {tg.k: tg.v for tg in w.tags}, "nodes": [_cid(x, y) for x, y in geo],
                        "geometry": [{"lon": x, "lat": y} for x, y in geo]})

    H().apply_file(str(pbf), locations=True, idx="flex_mem")
    return els, f"Geofabrik extract {pbf.name} (pyosmium)"


def mpls_planimetric(fr, slug, refresh):
    f = CACHE / f"plan_{slug}.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text())
    W, So, E, N = fr.bbox_ll(10)
    feats, off = [], 0
    while True:
        r = S.get(MPLS_PLAN, params={"where": "SYMBOL_NAME IN ('SWLK_EDGE','BLVD_WALK_EDGE','SWLK_EDGE_PVT','DRIVE_EDGE_PVT','CURB_GUTTER','CURB_GUTTER_OBSC','PAVEMENT_EDGE','PARKING_LOT_EDGE','TRAIL_EDGE','ALLEY_EDGE')",
                                     "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
                                     "spatialRel": "esriSpatialRelIntersects", "outFields": "SYMBOL_NAME", "f": "geojson",
                                     "resultOffset": off, "resultRecordCount": 1000, "orderByFields": "OBJECTID"}, timeout=120)
        r.raise_for_status()
        fs = r.json().get("features", [])
        feats += fs
        if len(fs) < 1000:
            break
        off += 1000
    f.write_text(json.dumps(feats))
    return feats


# ================================================================ geometry helpers
def lines_of(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if hasattr(g, "geoms"):
        return [x for p in g.geoms for x in lines_of(p)]
    return []


def polys_of(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if hasattr(g, "geoms"):
        return [x for p in g.geoms for x in polys_of(p)]
    return []


def simple_parts(g, min_area=0.05):
    """Polygons without holes: split through each hole until none is left."""
    out = []
    stack = polys_of(g)
    while stack:
        p = stack.pop()
        if p.area < min_area:
            continue
        if not p.interiors:
            out.append(orient(p, 1.0)); continue
        h = Polygon(p.interiors[0]).representative_point()
        x0, y0, x1, y1 = p.bounds
        if (x1 - x0) >= (y1 - y0):
            parts = [p.intersection(sbox(x0 - 1, y0 - 1, h.x, y1 + 1)), p.intersection(sbox(h.x, y0 - 1, x1 + 1, y1 + 1))]
        else:
            parts = [p.intersection(sbox(x0 - 1, y0 - 1, x1 + 1, h.y)), p.intersection(sbox(x0 - 1, h.y, x1 + 1, y1 + 1))]
        for q in parts:
            stack += polys_of(q)
    return out


def offset(line, d):
    """Offset left (+) / right (-) keeping the line direction."""
    if abs(d) < 1e-6:
        return [line]
    try:
        g = line.offset_curve(d, quad_segs=8, join_style="round")
    except Exception:  # noqa: BLE001
        return []
    return [l_ for l_ in lines_of(g) if l_.length > 0.5]


def strip(line, a, b):
    """Polygon between left offsets a < b (metres, + = left)."""
    a, b = min(a, b), max(a, b)
    if b - a < 0.05:
        return Polygon()
    def side(d):
        if d == 0:
            return Polygon()
        return line.buffer(d, quad_segs=8, single_sided=True, join_style="round")
    if a >= 0:
        return side(b).difference(side(a)) if a > 0 else side(b)
    if b <= 0:
        return side(a).difference(side(b)) if b < 0 else side(a)
    return unary_union([side(a), side(b)])


def point_tangent(line, s):
    """Point + unit tangent at distance s; beyond the end the last tangent is extrapolated (arms of short ways)."""
    L_ = line.length
    sc = min(max(s, 0), L_)
    p = line.interpolate(sc)
    p0 = line.interpolate(max(sc - 4.0, 0)); p1 = line.interpolate(min(sc + 4.0, L_))
    t = np.array([p1.x - p0.x, p1.y - p0.y]); n_ = np.linalg.norm(t)
    t = t / n_ if n_ > 1e-9 else np.array([1.0, 0.0])
    return np.array([p.x, p.y]) + t * max(s - L_, 0), t


def left(t):
    return np.array([-t[1], t[0]])


def bearing(t):
    return (math.degrees(math.atan2(t[0], t[1])) + 360) % 360


def rect(c, t, along0, along1, off0, off1):
    n = left(t)
    pts = [c + t * along0 + n * off0, c + t * along1 + n * off0, c + t * along1 + n * off1, c + t * along0 + n * off1]
    return orient(Polygon(pts), 1.0)


# ================================================================ main per block
class Road:
    pass


def road_from_way(e, fr):
    t = e.get("tags", {})
    hw = t.get("highway")
    if hw not in CAR or t.get("area") == "yes" or not e.get("geometry"):
        return None
    if t.get("service") in ("parking_aisle",) and False:
        return None
    lon = [g["lon"] for g in e["geometry"]]; lat = [g["lat"] for g in e["geometry"]]
    x, y = fr.local(lon, lat)
    nodes = list(e.get("nodes") or [])
    ow = t.get("oneway", "")
    if t.get("junction") in ("roundabout", "circular") and not ow:
        ow = "yes"
    if ow == "-1":
        x, y, nodes = x[::-1], y[::-1], nodes[::-1]; ow = "yes"
    oneway = ow in ("yes", "true", "1") or base_kind(hw) == "motorway" and ow != "no"
    coords = np.column_stack([x, y])
    keep = np.r_[True, np.hypot(*np.diff(coords, axis=0).T) > 0.01]
    coords = coords[keep]; nodes = [nd for nd, k_ in zip(nodes, keep) if k_] if len(nodes) == len(keep) else nodes
    if len(coords) < 2:
        return None
    r = Road()
    r.id = e["id"]; r.tags = t; r.hw = hw; r.kind = base_kind(hw); r.name = t.get("name") or t.get("ref")
    r.coords = coords; r.line = LineString(coords); r.nodes = nodes; r.oneway = oneway
    r.surface = t.get("surface") or ("asphalt" if r.kind != "service" else None)
    svc = t.get("service")
    lw = lane_width(hw)
    n = parse_int(t.get("lanes"))
    if svc == "alley":
        n_def, lw = 1, 4.2
    elif svc in ("driveway", "drive-through"):
        n_def, lw = 1, 3.0
    elif svc == "parking_aisle":
        n_def, lw = 2, 3.0
    elif r.kind == "service":
        n_def = 1 if oneway else 2
    elif oneway:
        n_def = 2 if r.kind in ARTERIAL and not hw.endswith("_link") else 1
    else:
        n_def = 4 if r.kind in ("trunk", "primary", "motorway") else 2
    r.lanes_tagged = n is not None
    n = n if n and 0 < n < 12 else n_def
    width = parse_len(t.get("width")) or parse_len(t.get("est_width"))
    r.width_source = "width tag" if width else f"{n} lanes x {lw} m"
    fw = parse_int(t.get("lanes:forward")); bw_ = parse_int(t.get("lanes:backward")); both = parse_int(t.get("lanes:both_ways")) or 0
    if oneway:
        f, b, both = n, 0, 0
    elif fw is not None and bw_ is not None:
        f, b = fw, bw_
        if f + b + both != n:
            n = f + b + both
    elif fw is not None:
        f, b = fw, max(n - fw - both, 0)
    elif bw_ is not None:
        b, f = bw_, max(n - bw_ - both, 0)
    else:
        if n >= 3 and n % 2 == 1 and not both:
            both = 1  # US: odd two-way lane count = centre two-way left-turn lane
        f = (n - both) / 2; b = (n - both) / 2
    if width and n:
        lw = min(max(width / n, 2.5), 5.0) if width / n < 6 else lw
    r.n, r.f, r.b, r.both, r.lw = n, f, b, both, lw
    r.W = n * lw  # travel lanes
    # extras outside travel lanes: parking + bike lanes (way frame: L = left of way direction)
    def side_val(prefix, s):
        return t.get(f"{prefix}:{s}") or t.get(f"{prefix}:both") or (t.get(prefix) if t.get(prefix) not in (None, "separate", "no", "none") else None)
    pk = {}
    for s in ("left", "right"):
        v = t.get(f"parking:{s}") or t.get("parking:both") or t.get(f"parking:lane:{s}") or t.get("parking:lane:both")
        pk[s] = {"parallel": 2.4, "lane": 2.4, "street_side": 2.4, "half_on_kerb": 1.2, "diagonal": 4.8, "perpendicular": 5.2}.get(
            (t.get(f"parking:{s}:orientation") or t.get("parking:both:orientation") or v or ""), 2.4 if v in ("lane", "street_side", "yes") else 0.0)
        if v in (None, "no", "none", "separate"):
            pk[s] = 0.0
    bk = {}
    for s in ("left", "right"):
        v = side_val("cycleway", s)
        bk[s] = 1.5 if v in ("lane", "exclusive_lane", "buffered_lane") else 0.0
    if t.get("cycleway") == "opposite_lane":
        bk["left"] = 1.5
    r.park, r.bike = pk, bk
    r.eL = r.W / 2 + pk["left"] + bk["left"]
    r.eR = r.W / 2 + pk["right"] + bk["right"]
    if width and width > r.W + 0.5 and not (pk["left"] or pk["right"] or bk["left"] or bk["right"]):
        r.eL = r.eR = width / 2  # width tag wider than lanes: treat the rest as shoulder / parking
    r.dv = -r.W / 2 + f * lw  # divider offset (left +) between forward (right) and backward (left) lanes
    lm = t.get("lane_markings")
    r.marked = lm != "no" and (r.kind in MAJOR or lm == "yes")
    r.curbed = r.kind != "service" and t.get("highway") != "track"
    r.rank = RANK.get(r.kind, 1)
    return r


PT_DTYPE = [("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")]
RES = 0.5          # LiDAR raster cell (m)
MAXHALF = {"service": 5.0, "living_street": 7.0, "residential": 9.0, "unclassified": 9.0, "road": 9.0, "tertiary": 11.0,
           "secondary": 14.0, "primary": 16.0, "trunk": 18.0, "motorway": 18.0, "busway": 9.0}
MARKINGS_SEARCH = [
    {"city": "Austin", "layer": "Markings Short Line / Specialty Line / Specialty Point (ATD, services.arcgis.com/0L95CJ0VTaxqcmED)",
     "verdict": "not used: item licenseInfo is a disclaimer only, no licence granted (not clear-green)"},
    {"city": "Saint Paul", "layer": "Crosswalks (services1.arcgis.com/9meaaHE3uiba0zr8)", "verdict": "not used: no licence stated"},
    {"city": "Dallas", "layer": "Pavement Markings Maintenance Plan FY2023-25 (TrafficControlDevices)",
     "verdict": "not used: a maintenance plan, not the marking lines; disclaimer only"},
    {"city": "Minneapolis, Denver, Boulder, Columbus, Cincinnati, Kansas City, Tulsa, Phoenix, Scottsdale, Birmingham, Huntsville",
     "layer": "ArcGIS Online + Socrata catalogue search 'pavement markings' / 'crosswalk markings' / 'stop bar'",
     "verdict": "no published marking-line layer found"},
]


def load_lidar(bdir, meta):
    parts = []
    for i in range(int(meta.get("point_parts") or 1)):
        f = bdir / f"points_{i}.txt"
        if f.exists():
            parts.append(np.frombuffer(base64.b64decode(f.read_text()), dtype=PT_DTYPE))
    q = np.concatenate(parts)
    return q["x"] * 0.02, q["y"] * 0.02, q["z"] * 0.01, q["k"], q["h"] / 8.0, q["i"].astype(float)


class Lidar:
    """0.5 m rasters of the block's ground returns (z above meta.z_base, like mesh.txt)."""

    def __init__(self, bdir, meta, house_buf):
        from scipy import ndimage
        global RES
        RES = 0.5 if float(meta.get("points_per_m2") or 0) >= 25 else 1.0  # >= ~4 ground returns per cell
        x, y, z, k, h, it = load_lidar(bdir, meta)
        self.half = half = float(meta["half_m"]); n = self.n = int(round(2 * half / RES))
        gi = np.clip(((x + half) / RES).astype(int), 0, n - 1); gj = np.clip(((y + half) / RES).astype(int), 0, n - 1)
        idx = gj * n + gi
        g = k == 0
        cnt = np.bincount(idx[g], minlength=n * n).astype(float)
        zs = np.bincount(idx[g], z[g], n * n); zz = np.bincount(idx[g], z[g] ** 2, n * n); isum = np.bincount(idx[g], it[g], n * n)
        with np.errstate(invalid="ignore", divide="ignore"):
            zm = zs / cnt; zsd = np.sqrt(np.maximum(zz / cnt - zm ** 2, 0)); im = isum / cnt
        self.has = (cnt >= 2).reshape(n, n)
        self.zsd = np.where(self.has, zsd.reshape(n, n), np.nan)
        self.inten = np.where(self.has, im.reshape(n, n), np.nan)
        zr = np.where(cnt > 0, zm, np.nan).reshape(n, n)
        _, (iy, ix) = ndimage.distance_transform_edt(np.isnan(zr), return_indices=True)
        self.dtm = ndimage.median_filter(zr[iy, ix], size=3)
        self.real = cnt.reshape(n, n) > 0
        b = (k == 1)
        self.bld = (np.bincount(idx[b], minlength=n * n) > 0).reshape(n, n)
        # canopy: vegetation / other returns > 1.5 m above ground; ground returns under it are dimmed -> not usable for
        # the intensity test, treated as unknown (bridged, never an edge)
        v = (k >= 2) & (h > 1.5)
        self.canopy = (np.bincount(idx[v], minlength=n * n) > 0).reshape(n, n)
        self.canopy = ndimage.binary_dilation(self.canopy, iterations=1)
        self.has_all = self.has.copy()
        self.has = self.has & ~self.canopy
        cx = -half + (np.arange(n) + 0.5) * RES
        CX, CY = np.meshgrid(cx, cx)
        self.cx, self.CX, self.CY = cx, CX, CY
        if house_buf is not None and not house_buf.is_empty:
            self.bld |= shapely.contains_xy(house_buf, CX, CY)
        # curb step, slope-invariant: change across +-1 cell minus a third of the change across +-3 cells
        # (a ramp/grade cancels, a step of h leaves ~2h/3); both sides real ground
        st = np.zeros((n, n))
        Z = self.dtm
        for dj, di in ((0, 1), (1, 0), (1, 1), (1, -1)):
            a1 = np.roll(Z, (dj, di), (0, 1)); c1 = np.roll(Z, (-dj, -di), (0, 1))
            a3 = np.roll(Z, (3 * dj, 3 * di), (0, 1)); c3 = np.roll(Z, (-3 * dj, -3 * di), (0, 1))
            ok = np.roll(self.real, (dj, di), (0, 1)) & np.roll(self.real, (-dj, -di), (0, 1))
            st = np.maximum(st, np.where(ok, np.abs(a1 - c1) - np.abs(a3 - c3) / 3, 0))
        st[:3] = st[-3:] = 0; st[:, :3] = st[:, -3:] = 0
        self.step = st
        self.curb = (st >= 0.055) & (st <= 0.3)
        self.n_points = len(x)

    def cell(self, x, y):
        i = np.clip(((np.asarray(x) + self.half) / RES).astype(int), 0, self.n - 1)
        j = np.clip(((np.asarray(y) + self.half) / RES).astype(int), 0, self.n - 1)
        return j, i

    def z(self, x, y):
        px = (np.asarray(x, float) + self.half) / RES - 0.5
        py = (np.asarray(y, float) + self.half) / RES - 0.5
        return bilinear(self.dtm, px, py)


def chaikin(pts, it=2):
    pts = np.asarray(pts, float)
    for _ in range(it):
        if len(pts) < 3:
            break
        q = 0.75 * pts[:-1] + 0.25 * pts[1:]; r = 0.25 * pts[:-1] + 0.75 * pts[1:]
        mid = np.empty((2 * len(q), 2)); mid[0::2] = q; mid[1::2] = r
        pts = np.vstack([pts[:1], mid, pts[-1:]])
    return pts


def nanmedian_filter(a, w, frac=0.5):
    out = np.full_like(a, np.nan)
    h = w // 2
    for i in range(len(a)):
        win = a[max(0, i - h): i + h + 1]
        v = win[~np.isnan(win)]
        if len(v) >= max(2, int(frac * len(win))):
            out[i] = np.median(v)
    return out


def measure_way(r, lid, line, way_mask):
    """Perpendicular profiles every 1 m: the paved run through (or nearest to) the OSM centreline, bounded by a curb
    step / intensity change / building. Returns smoothed left/right edge offsets (NaN = no measured corridor)."""
    mh = MAXHALF.get(r.kind, 9.0)
    L_ = line.length
    ss = np.arange(0.5, L_ - 0.49, 1.0) if L_ > 1.5 else np.array([L_ / 2])
    offs = np.arange(-mh - 0.5, mh + 0.5 + 1e-6, 0.25)
    dl = np.full(len(ss), np.nan); dr = np.full(len(ss), np.nan); C = np.zeros((len(ss), 2)); T = np.zeros((len(ss), 2))
    for a, s in enumerate(ss):
        c, t = point_tangent(line, s); nrm = left(t)
        C[a], T[a] = c, t
        px = c[0] + nrm[0] * offs; py = c[1] + nrm[1] * offs
        inside = (np.abs(px) < lid.half) & (np.abs(py) < lid.half)
        j, i = lid.cell(px, py)
        m = way_mask[j, i] & inside
        if not m.any():
            continue
        z0 = int(np.argmin(np.abs(offs)))
        cand = np.where(m)[0]
        k0 = cand[np.argmin(np.abs(cand - z0))]
        if abs(offs[k0]) > 3.0:  # OSM may be off by a few metres, not more
            continue
        lo = k0
        while lo - 1 >= 0 and m[lo - 1]:
            lo -= 1
        hi = k0
        while hi + 1 < len(offs) and m[hi + 1]:
            hi += 1
        # an edge counts only where it is measured: the run stops at a curb step (z is reliable even under canopy), a
        # building, or open lawn/sidewalk ground; trailing canopy cells (no usable intensity) are trimmed back to the
        # last open paved cell and accepted only if that trim is < 1 m
        openp = lid.has[j, i]
        def edge(k_end, k_next, sgn):
            if not (0 <= k_next < len(offs)) or not inside[k_next]:
                return np.nan
            jn, in_ = j[k_next], i[k_next]
            if lid.curb[jn, in_] or lid.bld[jn, in_]:
                return offs[k_end] + sgn * 0.125
            if lid.has[jn, in_]:  # open, not paved (lawn / concrete walk)
                kk = k_end
                while kk != k0 and not openp[kk]:
                    kk -= sgn
                if abs(offs[k_end] - offs[kk]) <= 1.0:
                    return offs[kk] + sgn * 0.125
            return np.nan
        dl[a] = edge(hi, hi + 1, 1)
        dr[a] = edge(lo, lo - 1, -1)
    # driveway aprons / tree gaps: robust along-road median (9 m), then drop raw outliers
    dls = nanmedian_filter(dl, 25, 0.3); drs = nanmedian_filter(dr, 25, 0.3)
    good = ~np.isnan(dls) & ~np.isnan(drs) & ((dls - drs) >= 2.5)
    return ss, C, T, dls, drs, good


def build(slug, refresh=False):
    bdir = BLOCKS / slug
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    fr = Frame(meta)
    half = fr.half
    print(f"== {slug}  LiDAR square ±{half:.0f} m")
    houses = json.loads((bdir / "houses.json").read_text(encoding="utf-8"))
    rings = [Polygon(h_["ring"]) for h_ in houses if len(h_.get("ring") or []) >= 3]
    rings = [p if p.is_valid else p.buffer(0) for p in rings]
    house_u = unary_union(rings) if rings else Polygon()
    house_b = house_u.buffer(0.5)      # hard constraint zone
    house_cut = house_u.buffer(0.55)   # what we subtract (margin for coordinate rounding)
    lid = Lidar(bdir, meta, house_b)
    els, osm_src = osm_elements(fr, slug, meta, refresh)
    sq = sbox(-half, -half, half, half)

    roads, walks, nodes_t, node_xy, node_osm = [], [], {}, {}, {}
    for e in els:
        if e["type"] == "node":
            nodes_t[e["id"]] = e.get("tags", {}); node_xy[e["id"]] = np.array(fr.local(e["lon"], e["lat"]), float)
            node_osm[e["id"]] = e.get("osm_id", e["id"])
    for e in els:
        if e["type"] != "way":
            continue
        r = road_from_way(e, fr)
        if r is not None:
            if r.line.intersects(sq.buffer(20)):
                roads.append(r)
            continue
        t = e.get("tags", {})
        if t.get("highway") in WALK and e.get("geometry"):
            x, y = fr.local([g["lon"] for g in e["geometry"]], [g["lat"] for g in e["geometry"]])
            ln = LineString(np.column_stack([x, y]))
            if ln.length > 0.5 and ln.intersects(sq):
                walks.append((t, ln, e["id"]))
    print(f"  OSM ({osm_src}): {len(roads)} car ways, {len(walks)} foot/cycle ways, {len(nodes_t)} tagged nodes")

    # ---------------------------------------------------------------- LiDAR corridor per way
    from scipy import ndimage
    from shapely.strtree import STRtree  # noqa: F401
    n = lid.n
    cent = np.zeros((n, n), bool)
    for r in roads:
        for s in np.arange(0, r.line.length, 0.25):
            p = r.line.interpolate(s)
            if abs(p.x) < half and abs(p.y) < half:
                j, i = lid.cell(p.x, p.y); cent[j, i] = True
    dist_c = ndimage.distance_transform_edt(~cent) * RES if cent.any() else np.full((n, n), 1e9)
    near = lid.has & (dist_c < 2.0) & ~lid.bld
    far = lid.has & (dist_c > 10.0) & ~lid.bld
    lawn_i = float(np.nanmedian(lid.inten[far])) if far.sum() > 50 else float(np.nanpercentile(lid.inten[lid.has], 75))
    smooth_def = np.where(lid.has, lid.zsd < 0.03, True)
    strips, axes, info, skipped = [], {}, [], []
    for r in roads:
        line = r.line.intersection(sq.buffer(-0.01))
        segs = [l_ for l_ in lines_of(line) if l_.length > 2.0]
        if not segs:
            continue
        # this way's own pavement signature (asphalt dark / concrete bright): median intensity on its centreline
        vals = []
        for l_ in segs:
            for s in np.arange(0, l_.length, 0.5):
                p = l_.interpolate(s); j, i = lid.cell(p.x, p.y)
                if near[j, i]:
                    vals.append(lid.inten[j, i])
        if len(vals) < 6:
            continue
        wi = float(np.median(vals))
        zsd_way = [lid.zsd[lid.cell(p.x, p.y)] for l_ in segs for p in (l_.interpolate(t_) for t_ in np.arange(0, l_.length, 0.5))
                   if lid.has[lid.cell(p.x, p.y)]]
        zthr = max(0.03, 1.3 * float(np.nanmedian(zsd_way))) if zsd_way else 0.03
        smooth = np.where(lid.has, lid.zsd < zthr, True)
        contrast = abs(lawn_i - wi)
        if contrast < 30:  # pavement indistinguishable from lawn in intensity (e.g. bright concrete alley): do not guess
            skipped.append((r.name or r.hw, round(contrast)))
            continue
        tol = max(18.0, 0.45 * contrast)
        paved = lid.has & (np.abs(lid.inten - wi) <= tol) & smooth
        passable = (paved | ~lid.has) & ~lid.bld & ~lid.curb
        # keep only what is connected to this way's own centreline cells
        lab, _ = ndimage.label(passable)
        seeds = set()
        for l_ in segs:
            for s in np.arange(0, l_.length, 0.5):
                p = l_.interpolate(s); j, i = lid.cell(p.x, p.y)
                if paved[j, i] and lab[j, i]:
                    seeds.add(lab[j, i])
        mask = np.isin(lab, list(seeds)) if seeds else np.zeros_like(passable)
        if RES < 1.0:
            mask = ndimage.binary_opening(mask, iterations=1)
        w_all = []
        for l_ in segs:
            ss, C, T, dls, drs, good = measure_way(r, lid, l_, mask)
            # runs of consecutive good samples -> polygons
            runs, cur = [], []
            for a in range(len(ss)):
                if good[a]:
                    cur.append(a)
                elif cur:
                    runs.append(cur); cur = []
            if cur:
                runs.append(cur)
            for run in runs:
                if len(run) < 4:
                    continue
                nl = np.array([left(T[a]) for a in run])
                Lp = C[run] + nl * dls[run][:, None]; Rp = C[run] + nl * drs[run][:, None]
                Ax = C[run] + nl * ((dls[run] + drs[run]) / 2)[:, None]
                Lp, Rp, Ax = chaikin(Lp), chaikin(Rp), chaikin(Ax)
                poly = Polygon(np.vstack([Lp, Rp[::-1]]))
                if not poly.is_valid:
                    poly = poly.buffer(0)
                strips.append((r, poly)); axes.setdefault(r.id, []).append(LineString(Ax))
                w_all += list(dls[run] - drs[run])
        if w_all:
            info.append((r, float(np.median(w_all)), wi, contrast))
    print(f"  lawn intensity {lawn_i:.0f}; measured ways: " + ", ".join(f"{(r_.name or r_.hw)[:18]} {w:.1f} m (int {wi:.0f}, contrast {c:.0f})" for r_, w, wi, c in info))
    width_of = {r_.id: w for r_, w, _, _ in info}
    if skipped:
        print("  not drawn (no intensity contrast to lawn):", skipped)

    # ---------------------------------------------------------------- junction areas from the LiDAR mask
    use = {}
    for ri, r in enumerate(roads):
        for nd in r.nodes:
            use.setdefault(nd, set()).add(ri)
    strip_u = unary_union([p for _, p in strips]) if strips else Polygon()
    patches = []
    allpaved = np.zeros((n, n), bool)
    for r in roads:
        if r.id in width_of:
            pass
    from rasterio import features as rfeat
    from affine import Affine
    tr = Affine(RES, 0, -half, 0, RES, -half)
    meta_res = RES
    junc = []
    for nd, rs in use.items():
        rr = [roads[i] for i in rs if roads[i].id in width_of]
        cnt_arms = sum((1 if roads[i].nodes[0] == nd or roads[i].nodes[-1] == nd else 2) for i in rs)
        if len(rs) < 2 or cnt_arms < 3 or len(rr) < 2:
            continue
        # the node position: the vertex shared by the ways
        r0 = roads[next(iter(rs))]
        xy = r0.coords[r0.nodes.index(nd)]
        if abs(xy[0]) > half or abs(xy[1]) > half:
            continue
        wmax = max(width_of[r_.id] for r_ in rr)
        circ = Point(xy).buffer(wmax / 2 + 7.0, quad_segs=16)
        # LiDAR pavement inside the circle, connected to the measured strips there (any of the ways' signatures)
        local = strip_u.intersection(circ)
        if local.is_empty:
            continue
        sig = [np.median([lid.inten[lid.cell(*p_)] for p_ in np.array(b.exterior.coords)[::3] if lid.has[lid.cell(*p_)]] or [np.nan])
               for b in polys_of(local)]
        wis = [v for v in sig if not np.isnan(v)]
        msk = np.zeros((n, n), bool)
        for wi in wis or [np.nan]:
            if np.isnan(wi):
                continue
            tol = max(18.0, 0.45 * abs(lawn_i - wi))
            msk |= lid.has & (np.abs(lid.inten - wi) <= tol) & smooth_def & ~lid.bld & ~lid.curb
        if RES < 1.0:
            msk = ndimage.binary_opening(msk, iterations=1)
        shp = [shapely.geometry.shape(g_) for g_, v in rfeat.shapes(msk.astype(np.uint8), mask=msk, transform=tr) if v == 1]
        cand = unary_union(shp).intersection(circ) if shp else Polygon()
        cand = cand.buffer(0.35, quad_segs=4).buffer(-0.35, quad_segs=4)
        hull = local.convex_hull  # junction pavement only between the measured arms (corner fillets), never beyond
        pieces = [p.intersection(hull) for p in polys_of(cand) if p.intersects(local.buffer(0.3))]
        patch = unary_union(pieces + [local]).buffer(0.5, quad_segs=6).buffer(-0.5, quad_segs=6).simplify(0.1)
        patch = patch.difference(strip_u)
        if not patch.is_empty and patch.area > 2:
            junc.append((nd, rr, patch))

    # ---------------------------------------------------------------- outputs
    out = {"roads": [], "lines": [], "crosswalks": [], "stop_lines": [], "crossings": [], "sidewalks": [], "paths": [],
           "curbs": [], "driveways": [], "signals": []}

    def P(poly, lift):
        poly = shapely.segmentize(poly, 1.5)
        cs = np.array(poly.exterior.coords)[:-1]
        if len(cs) < 3:
            return None
        z = lid.z(cs[:, 0], cs[:, 1]) + lift
        return [[round(float(a), 2), round(float(b), 2), round(float(c_), 2)] for a, b, c_ in zip(cs[:, 0], cs[:, 1], z)]

    def Lz(line, lift, seg=1.5):
        line = shapely.segmentize(line, seg)
        cs = np.array(line.coords)
        z = lid.z(cs[:, 0], cs[:, 1]) + lift
        return [[round(float(a), 2), round(float(b), 2), round(float(c_), 2)] for a, b, c_ in zip(cs[:, 0], cs[:, 1], z)]

    def Z(x, y):
        return round(float(lid.z(np.array([x]), np.array([y]))[0]), 2)

    lidar_name = f"USGS 3DEP LiDAR {meta.get('ept_project')} ({meta.get('lidar_year')})"

    def attrs(r):
        t = r.tags
        return {"name": r.name, "highway": r.hw, "lanes": parse_int(t.get("lanes")), "maxspeed": t.get("maxspeed"),
                "oneway": t.get("oneway") or ("yes" if r.oneway else None), "surface": t.get("surface"), "osm_id": r.id}

    road_polys = []
    for r, poly in strips:
        g = poly.difference(house_cut).intersection(sq)
        for p in simple_parts(g, 0.5):
            v = P(p, LIFT["road"])
            if v:
                out["roads"].append({"poly": v, "kind": r.hw if r.kind != "service" else f"service:{r.tags.get('service', 'other')}",
                                     **attrs(r), "width_m": round(width_of.get(r.id, float("nan")), 2),
                                     "source": f"surface measured from {lidar_name} ground returns (paved corridor: intensity + smoothness, "
                                               f"bounded by curb steps); attributes from OSM way {r.id}"})
                road_polys.append(p)
    for nd, rr, patch in junc:
        g = patch.difference(house_cut).intersection(sq)
        names = sorted({r_.name for r_ in rr if r_.name})
        for p in simple_parts(g, 0.5):
            v = P(p, LIFT["road"] + 0.005)
            if v:
                out["roads"].append({"poly": v, "kind": "intersection", "name": " & ".join(names) or None, "highway": None, "lanes": None,
                                     "maxspeed": None, "oneway": None, "surface": None, "osm_ways": sorted(r_.id for r_ in rr),
                                     "source": f"intersection paved area measured from {lidar_name}; ways from OSM"})
                road_polys.append(p)
    for rid, ax_ in axes.items():
        pass

    # curbs: road edges where the LiDAR shows a 6-35 cm step from the pavement to the outside
    road_all = unary_union(road_polys) if road_polys else Polygon()
    for ring_ in [g.exterior for g in polys_of(road_all)]:
        ln = shapely.segmentize(LineString(ring_.coords), 0.5)
        cs = np.array(ln.coords)
        if len(cs) < 3:
            continue
        flags, hts = [], []
        for a in range(len(cs)):
            p0 = cs[max(a - 1, 0)]; p1 = cs[min(a + 1, len(cs) - 1)]
            t = p1 - p0; nt = np.linalg.norm(t)
            if nt < 1e-6:
                flags.append(False); hts.append(0); continue
            t /= nt; nn = left(t)
            pi = cs[a] + nn * 0.75; po = cs[a] - nn * 0.75
            if not road_all.contains(Point(pi)):
                pi, po = po, pi
            ok = all(abs(v_) < half - 0.6 for v_ in (*pi, *po))
            if not ok:
                flags.append(False); hts.append(0); continue
            ji, ii = lid.cell(*pi); jo, io_ = lid.cell(*po)
            dz = lid.dtm[jo, io_] - lid.dtm[ji, ii]
            good = lid.real[ji, ii] and lid.real[jo, io_] and 0.06 <= dz <= 0.35
            flags.append(good); hts.append(dz)
        a = 0
        while a < len(cs):
            if flags[a]:
                b_ = a
                while b_ + 1 < len(cs) and (flags[b_ + 1] or (b_ + 2 < len(cs) and flags[b_ + 2])):
                    b_ += 1
                if b_ - a >= 4:  # >= 2 m
                    seg = LineString(cs[a:b_ + 1])
                    out["curbs"].append({"path": Lz(seg, 0.0, 1.0), "height_m": round(float(np.median([h_ for h_, f_ in zip(hts[a:b_ + 1], flags[a:b_ + 1]) if f_])), 3),
                                         "source": f"curb step measured in {lidar_name} along the paved corridor edge"})
                a = b_ + 1
            else:
                a += 1

    # crossings: exact OSM geometry only (no stripes are drawn: no licensed marking layer)
    for nd, tg in nodes_t.items():
        if (tg.get("highway") == "crossing" or "crossing" in tg or "crossing:markings" in tg) and all(abs(v_) < half for v_ in node_xy[nd]):
            x_, y_ = node_xy[nd]
            out["crossings"].append({"x": round(float(x_), 2), "y": round(float(y_), 2), "z": Z(x_, y_), "osm_node": node_osm[nd],
                                     "tags": {k: v for k, v in tg.items() if k.startswith("crossing") or k in ("highway", "tactile_paving", "kerb")},
                                     "source": f"OSM node {node_osm[nd]}"})
    for t, ln, wid in walks:
        g = ln.intersection(sq)
        segs = [s_ for s_ in lines_of(g) if s_.length > 0.3]
        if not segs:
            continue
        if t.get("footway") == "crossing" or t.get("cycleway") == "crossing" or t.get("path") == "crossing":
            for s_ in segs:
                out["crossings"].append({"path": Lz(s_, LIFT["mark"]), "osm_way": wid,
                                         "tags": {k: v for k, v in t.items() if k.startswith("crossing") or k in ("highway", "footway", "surface")},
                                         "source": f"OSM way {wid}"})
        else:
            for s_ in segs:
                out["paths"].append({"path": Lz(s_, LIFT["walk"]), "kind": t.get("footway") and f"footway:{t['footway']}" or t.get("highway"),
                                     "width_m": parse_len(t.get("width")), "surface": t.get("surface"), "osm_way": wid,
                                     "source": f"OSM way {wid} (centreline as mapped)"})

    # control points: exact OSM node positions, heading only from a direction tag
    CARD = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5,
            "SW": 225, "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}
    for nd, tg in nodes_t.items():
        hw = tg.get("highway")
        kind = {"traffic_signals": "signal_control", "stop": "stop_control", "give_way": "yield_control", "street_lamp": "street_lamp"}.get(hw)
        if not kind:
            continue
        x_, y_ = node_xy[nd]
        if abs(x_) > half or abs(y_) > half:
            continue
        dtag = tg.get("direction") or tg.get("traffic_signals:direction")
        hd = None
        if dtag in ("forward", "backward"):
            for r in roads:
                if nd in r.nodes:
                    s = r.line.project(Point(x_, y_)); _, t = point_tangent(r.line, s)
                    hd = round(bearing(t if dtag == "forward" else -t), 1)
                    break
        elif dtag:
            try:
                hd = float(dtag) % 360
            except ValueError:
                hd = CARD.get(dtag.upper())
        out["signals"].append({"x": round(float(x_), 2), "y": round(float(y_), 2), "z": Z(x_, y_), "heading_deg": hd, "kind": kind,
                               "osm_node": node_osm[nd], "direction_tag": dtag,
                               "tags": {k: v for k, v in tg.items() if k in ("stop", "traffic_signals", "direction", "traffic_signals:direction",
                                                                              "lamp_type", "light:method", "support", "ref")},
                               "source": f"OSM node {node_osm[nd]} (exact position; heading_deg = travel bearing of the controlled traffic "
                                         f"from the direction tag, else null)"})

    # sidewalks / private walks / driveways: City of Minneapolis planimetric (measured), clipped to the LiDAR square
    plan = {}
    if meta.get("city", "").lower() == "minneapolis" or slug.startswith("minneapolis"):
        try:
            for f_ in mpls_planimetric(fr, slug, refresh):
                g = f_.get("geometry")
                if not g:
                    continue
                for p in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
                    if len(p) >= 2:
                        x, y = fr.local([c[0] for c in p], [c[1] for c in p])
                        plan.setdefault(f_["properties"]["SYMBOL_NAME"], []).append(LineString(np.column_stack([x, y])))
        except Exception as e:  # noqa: BLE001
            print("  planimetric failed:", str(e)[:150])
    if plan:
        def closing(lines_, rr_):
            return unary_union([l_.buffer(rr_, quad_segs=4) for l_ in lines_]).buffer(-rr_ + 0.02, quad_segs=4)
        sw = closing(plan.get("SWLK_EDGE", []) + plan.get("BLVD_WALK_EDGE", []), 1.4).difference(road_all)
        for kind, geom_, lift in (("sidewalk", sw, LIFT["walk"]),
                                  ("walk_private", closing(plan.get("SWLK_EDGE_PVT", []), 1.0).difference(road_all).difference(sw), LIFT["drive"])):
            for p in simple_parts(geom_.difference(house_cut).intersection(sq), 0.8):
                v = P(p, lift)
                if v:
                    out["sidewalks"].append({"poly": v, "kind": kind, "source": "City of Minneapolis PW_Planimetric_Lines (edge pairs closed into strips)"})
        dv = closing(plan.get("DRIVE_EDGE_PVT", []), 2.0).difference(road_all).difference(sw)
        for p in simple_parts(dv.difference(house_cut).intersection(sq), 2.0):
            v = P(p, LIFT["drive"])
            if v:
                out["driveways"].append({"poly": v, "source": "City of Minneapolis PW_Planimetric_Lines"})

    # ---------------------------------------------------------------- validation (hard: zero overlap with houses + 0.5 m)
    def area_overlap(items, key):
        a = 0.0
        for it_ in items:
            g = Polygon(np.array(it_[key])[:, :2]) if key == "poly" else LineString(np.array(it_[key])[:, :2]).buffer(0.05)
            if not g.is_valid:
                g = g.buffer(0)
            a += g.intersection(house_b).area
        return a
    ov = {"roads_m2": round(area_overlap(out["roads"], "poly"), 4),
          "crosswalks_m2": round(area_overlap(out["crosswalks"], "poly"), 4),
          "lines_m2": round(area_overlap(out["lines"], "path"), 4)}
    ov["total_m2"] = round(sum(ov.values()), 4)
    validation = {"house_overlap_m2": ov["total_m2"], "house_buffer_m": 0.5, "overlap": ov, "houses": len(rings),
                  "controls_inside_road": sum(1 for s in out["signals"] if road_all.contains(Point(s["x"], s["y"])))}
    print(f"  VALIDATION overlap with houses+0.5 m: {ov['total_m2']} m2 " + ("OK" if ov["total_m2"] < 1e-3 else "FAILED"))

    counts = {k: len(v) for k, v in out.items()}
    counts["signals_by_kind"] = {}
    for s in out["signals"]:
        counts["signals_by_kind"][s["kind"]] = counts["signals_by_kind"].get(s["kind"], 0) + 1
    counts["ways_not_measured"] = [f"{a} (contrast {b})" for a, b in skipped]
    counts["ways_measured"] = len(info); counts["ways_in_square"] = sum(1 for r in roads if r.line.intersects(sq))
    counts["road_area_m2"] = round(road_all.area, 1)
    sources = [{"name": lidar_name + " (block points_*.txt, ground class)", "licence": "public domain",
                "used_for": "road surface (paved corridor), widths, curbs, all z values"},
               {"name": "OpenStreetMap", "via": osm_src, "licence": "ODbL 1.0", "url": "https://www.openstreetmap.org/copyright",
                "used_for": "road attributes (name, highway, lanes, maxspeed, oneway, surface) for tooltips; which corridor belongs to which "
                            "way; exact crossing / control-point / lamp / footway positions"}]
    if plan:
        sources.append({"name": "City of Minneapolis PW_Planimetric_Lines", "licence": "copyright waived by the City of Minneapolis (CC0-style)",
                        "url": "https://opendata.minneapolismn.gov", "used_for": "sidewalk / private-walk / driveway polygons"})
    out["meta"] = {"sources": sources,
                   "licence": "ODbL 1.0 (contains a Derivative Database of OpenStreetMap; share-alike: redistribute under ODbL)",
                   "attribution": "© OpenStreetMap contributors, ODbL" + ("; City of Minneapolis Open Data" if plan else "") + "; USGS 3DEP",
                   "markings_source": None, "markings_search": MARKINGS_SEARCH,
                   "markings_note": "lane lines, centre lines, crosswalk stripes and stop lines are emitted only from a licensed marking-line "
                                    "dataset; none exists for this city, so lines / crosswalks / stop_lines are empty",
                   "method": "no prediction: road polygons are the LiDAR paved corridor (0.5 m raster of ground returns; per-way intensity "
                             "signature + smoothness < 3 cm, bounded by 6-35 cm curb steps and buildings; 1 m perpendicular profiles, 9 m "
                             "along-road median, Chaikin-smoothed); OSM ways without a measured corridor are not drawn; everything clipped "
                             "to the LiDAR square; control points / crossings / lamps / footways at their exact OSM positions",
                   "frame": "local metres east/north of meta.origin (Web-Mercator metres / k, as houses.json); z = metres above meta.z_base",
                   "clip_m": half, "z_lift_m": LIFT, "rings": "open (last != first), CCW, no holes",
                   "counts": counts, "validation": validation, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    txt = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    tmp = bdir / "streets.json.tmp"
    tmp.write_text(txt, encoding="utf-8"); os.replace(tmp, bdir / "streets.json")
    summary = {"file": "streets.json", "house_overlap_m2": validation["house_overlap_m2"], "bytes": len(txt.encode("utf-8")), "counts": counts, "validation": validation,
               "markings_source": None, "osm_via": osm_src, "licence": "ODbL 1.0 (© OpenStreetMap contributors)",
               "generated": out["meta"]["generated"]}
    with block_lock(bdir):
        m = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
        files = m.get("files") or []
        if "streets.json" not in files:
            files.append("streets.json")
        m["files"] = files
        m["streets"] = summary
        tmp = bdir / "meta.json.streets.tmp"
        tmp.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, bdir / "meta.json")
    print(f"  wrote streets.json {len(txt) / 1e6:.2f} MB: " + ", ".join(f"{k} {v}" for k, v in counts.items() if not isinstance(v, dict)) + f" {counts['signals_by_kind']}")
    return counts, len(txt)


def render(slug, path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MP
    bdir = BLOCKS / slug
    d = json.loads((bdir / "streets.json").read_text(encoding="utf-8"))
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    houses = json.loads((bdir / "houses.json").read_text(encoding="utf-8"))
    half = meta["half_m"]
    lid = Lidar(bdir, meta, None)
    fig, axs = plt.subplots(1, 2, figsize=(24, 12), dpi=90)
    for ax, fill in zip(axs, (False, True)):
        ax.imshow(lid.inten, origin="lower", extent=[-half, half, -half, half], cmap="gray",
                  vmin=np.nanpercentile(lid.inten, 2), vmax=np.nanpercentile(lid.inten, 98), zorder=0)
        for h_ in houses:
            r_ = np.array(h_["ring"]); ax.fill(r_[:, 0], r_[:, 1], fc=(1, 0, 0, 0.25), ec="red", lw=1, zorder=5)
        for r in d["roads"]:
            p = np.array(r["poly"])[:, :2]
            if fill:
                ax.add_patch(MP(p, closed=True, fc=(0.1, 0.4, 1, 0.45) if r["kind"] != "intersection" else (0.6, 0.2, 1, 0.45), ec="blue", lw=0.6, zorder=2))
            else:
                ax.add_patch(MP(p, closed=True, fc="none", ec="cyan", lw=1.0, zorder=2))
        for c in d["curbs"]:
            p = np.array(c["path"]); ax.plot(p[:, 0], p[:, 1], color="yellow", lw=1.6, zorder=3)
        for s in d["sidewalks"]:
            ax.add_patch(MP(np.array(s["poly"])[:, :2], closed=True, fc=(0.9, 0.85, 0.6, 0.5) if fill else "none", ec="orange", lw=0.4, zorder=2))
        for c in d["crossings"]:
            if "path" in c:
                p = np.array(c["path"]); ax.plot(p[:, 0], p[:, 1], "m-", lw=2, zorder=4)
            else:
                ax.plot(c["x"], c["y"], "mD", ms=5, zorder=4)
        mk = {"signal_control": ("o", "lime"), "stop_control": ("8", "red"), "yield_control": ("v", "orange"), "street_lamp": ("*", "yellow")}
        for s in d["signals"]:
            m_, c_ = mk.get(s["kind"], ("x", "k"))
            ax.plot(s["x"], s["y"], m_, color=c_, ms=10, mec="k", zorder=6)
        ax.set_xlim(-half, half); ax.set_ylim(-half, half); ax.set_aspect("equal")
    c_ = d["meta"]["counts"]
    axs[0].set_title(f"{slug}: LiDAR ground intensity, houses (red), road outlines (cyan), curbs (yellow)", fontsize=9)
    axs[1].set_title(f"filled; ways measured {c_['ways_measured']}/{c_['ways_in_square']}, overlap {d['meta']['validation']['overlap']['total_m2']} m2", fontsize=9)
    path = path or CACHE / f"render_{slug}.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--render", action="append", default=[])
    a = ap.parse_args()
    slugs = a.slugs or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists() and (p / "mesh.txt").exists())
    res = {}
    for s in slugs:
        try:
            res[s] = build(s, a.refresh)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            res[s] = ("FAILED", str(e)[:200])
    for s in a.render:
        print("render ->", render(s))
    print(json.dumps({k: (v[0] if isinstance(v, tuple) and isinstance(v[0], dict) else v) for k, v in res.items()}, default=str)[:4000])

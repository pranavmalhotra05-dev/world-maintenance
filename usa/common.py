"""Shared helpers for usa/build_block.py and usa/enrich_national.py (national, keyless, commercial-use sources only)."""
import base64
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
NAT = ROOT / "data" / "national"
BLOCKS = ROOT / "web" / "usa" / "blocks"
EPT_BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
UA = "roofprint-usa/0.1 (open-data research)"
S = requests.Session()
S.headers["User-Agent"] = UA
PT_DTYPE = [("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")]
# licences we accept for address records (public domain / permissive / share-alike that allows commercial use)
OK_LICENCE = re.compile(r"public[- ]domain|cc0|cc-by(?!-nc)|cdla-permissive|odbl|pddl|odc-by", re.I)


def get(url, tries=3, **kw):
    kw.setdefault("timeout", 120)
    last = None
    for i in range(tries):
        try:
            r = S.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {url}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


# ---------------------------------------------------------------- Census geocoder (public, keyless)
CENSUS = "https://geocoding.geo.census.gov/geocoder"


def _geo_fields(geos):
    one = lambda k: (geos.get(k) or [{}])[0]
    st, co, tr, bl = one("States"), one("Counties"), one("Census Tracts"), one("2020 Census Blocks")
    pl = one("Incorporated Places") or one("County Subdivisions")
    return {"state_fips": st.get("STATE"), "state": st.get("STUSAB"), "state_name": st.get("NAME"),
            "county_fips": co.get("GEOID"), "county": co.get("NAME"),
            "tract": tr.get("GEOID"), "block_geoid": bl.get("GEOID"),
            "place": re.sub(r"\s+(city|town|village|borough|CDP)$", "", pl.get("NAME") or "") or None}


def geocode(address):
    r = get(f"{CENSUS}/geographies/onelineaddress", params={"address": address, "benchmark": "Public_AR_Current",
                                                             "vintage": "Current_Current", "format": "json"}, timeout=90).json()
    m = r["result"]["addressMatches"]
    if not m:
        raise SystemExit(f"Census geocoder found no match for: {address}")
    m = m[0]; ac = m.get("addressComponents", {})
    out = {"lon": float(m["coordinates"]["x"]), "lat": float(m["coordinates"]["y"]), "matched_address": m["matchedAddress"],
           "city": (ac.get("city") or "").title() or None, "zip": ac.get("zip") or None, **_geo_fields(m.get("geographies", {}))}
    out["state"] = out["state"] or ac.get("state")
    return out


def census_point(lon, lat):
    r = get(f"{CENSUS}/geographies/coordinates", params={"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                                                         "vintage": "Current_Current", "format": "json"}, timeout=90).json()
    return {"lon": lon, "lat": lat, "matched_address": None, "city": None, "zip": None, **_geo_fields(r["result"]["geographies"])}


# ---------------------------------------------------------------- USGS 3DEP EPT project choice
def lidar_year(name, wesm=None):
    if wesm is not None:
        row = wesm.get(name)
        if row:
            return int(row[:4]), "WESM collect_end"
    ys = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", name)]
    if ys:
        return max(ys), "project name"
    m = re.search(r"_[A-Z](\d{2})$", name)  # e.g. _B22 = 2022 USGS work unit suffix
    if m:
        return 2000 + int(m.group(1)), "project name suffix"
    return None, None


def pick_ept(lon, lat, box_ll=None):
    """Projects whose boundary contains the point; prefer full box coverage, newest year, highest density."""
    import csv
    from pyproj import Geod
    from shapely.geometry import Point, box, shape
    wesm = {}
    try:
        with open(NAT / "wesm.csv", newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                if r.get("collect_end"):
                    wesm[r["workunit"]] = r["collect_end"]
    except FileNotFoundError:
        pass
    geod = Geod(ellps="WGS84")
    fc = json.load(open(NAT / "ept_boundaries.geojson"))
    pt = Point(lon, lat); bb = box(*box_ll) if box_ll else None
    cands = []
    for f in fc["features"]:
        g = shape(f["geometry"])
        if not g.contains(pt):
            continue
        p = f["properties"]
        area = abs(geod.geometry_area_perimeter(g)[0])
        y, ysrc = lidar_year(p["name"], wesm)
        cov = g.intersection(bb).area / bb.area if bb is not None else 1.0
        cands.append({"name": p["name"], "count": p["count"], "density": round(p["count"] / max(area, 1), 2), "year": y,
                      "year_source": ysrc, "box_coverage": round(cov, 3)})
    if not cands:
        raise SystemExit(f"No USGS 3DEP EPT project covers {lon},{lat}")
    cands.sort(key=lambda c: (c["box_coverage"] >= 0.95, c["year"] or 0, c["density"]), reverse=True)
    return cands[0], cands


def ept_nodes(ept, root, box):
    found, todo = [], ["0-0-0-0"]
    while todo:
        k0 = todo.pop()
        for k, n in get(f"{ept}/ept-hierarchy/{k0}.json", timeout=60).json().items():
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


# ---------------------------------------------------------------- block IO
def block_dir(slug):
    return BLOCKS / slug


def load_json(d, name, default=None):
    p = d / name
    return json.load(open(p, encoding="utf-8")) if p.exists() else default


def dump_json(d, name, obj, indent=None):
    with open(d / name, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=None if indent else (",", ":"), indent=indent, default=_np_default)


def _np_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if math.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def write_files_list(d):
    """meta.json["files"] = every file in the block folder (relative, forward slashes), so the viewer never probes for missing ones."""
    meta = load_json(d, "meta.json", {})
    files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
    meta["files"] = files if "meta.json" in files else files + ["meta.json"]
    dump_json(d, "meta.json", meta)
    return meta["files"]


def load_points(d, meta):
    parts = [np.frombuffer(base64.b64decode((d / f"points_{i}.txt").read_text()), dtype=PT_DTYPE) for i in range(meta["point_parts"])]
    return np.concatenate(parts)


def block_box_3857(lon, lat, half):
    """The block square in Web Mercator (house3d.py convention: local metres = mercator metres / k)."""
    from pyproj import Transformer
    cx, cy = Transformer.from_crs(4326, 3857, always_xy=True).transform(lon, lat)
    k = 1 / math.cos(math.radians(lat))
    return cx, cy, k, (cx - half * k, cy - half * k, cx + half * k, cy + half * k)


def raster_to_block(href, box3857, n, bands=None, margin=0.15, env=None):
    """Read a window of a (cloud) raster and resample it onto the block's Web-Mercator grid.
    Returns float32 array (bands, n, n), row 0 = north (same orientation as aerial.jpg)."""
    import rasterio
    from rasterio.transform import from_bounds as tfb
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds
    x0, y0, x1, y1 = box3857
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    with rasterio.Env(**(env or {})), rasterio.open(href) as src:
        bb = transform_bounds("EPSG:3857", src.crs, x0 - mx, y0 - my, x1 + mx, y1 + my)
        win = from_bounds(*bb, src.transform).round_offsets().round_lengths()
        idx = bands or list(range(1, src.count + 1))
        a = src.read(idx, window=win, boundless=True, fill_value=0).astype(np.float32)
        st = src.window_transform(win); scrs = src.crs
    out = np.zeros((len(idx), n, n), np.float32)
    dt = tfb(x0, y0, x1, y1, n, n)
    for i in range(len(idx)):
        reproject(a[i], out[i], src_transform=st, src_crs=scrs, dst_transform=dt, dst_crs="EPSG:3857", resampling=Resampling.bilinear)
    return out


def quadkey(lon, lat, z):
    s = math.sin(math.radians(lat)); n = 2 ** z
    x = int((lon + 180) / 360 * n); y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    q = ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        q += str((1 if x & m else 0) + (2 if y & m else 0))
    return q

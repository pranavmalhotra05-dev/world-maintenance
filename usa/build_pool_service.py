"""Pool-cleaning service view per block -> web/usa/blocks/<slug>/pool_service.json (lane PS1).

    .venv\\Scripts\\python -u -W ignore usa/build_pool_service.py            # every block with pools
    .venv\\Scripts\\python -u -W ignore usa/build_pool_service.py --slug phoenix-az

Only measured / published data; nothing is invented. Pool depth and volume are in no open dataset and are not output.

Inputs
  houses.json h.pool (usa/pools.py: NAIP water sites), h.flood_zone (FEMA NFHL), meta.weather_triggers (NWS/NCEI),
  meta.cleaning.market (Census CBP/NES, BLS QCEW), meta.affluence (ACS), meta.pools.context_pools
  LiDAR block points (USGS 3DEP, public domain): tree heights, canopy over the pool, no-return outline, sun ray-cast
  web/usa/data/earth_county.json (EPA radon zones, EPA AQS design values)
  web/app/data/gnss.json (EarthScope / NSF GAGE velocities, CC BY 4.0): nearest station vertical velocity
  USDA NRCS Soil Data Access (public domain): linear extensibility (LEP) under each pool point
  NASA POWER climatology API (no key, NASA data CC0): all-sky surface irradiance + UV index (NREL NSRDB needs a key)
  CDC ArboNET county CSV behind the CDC WNV historic-data map (no login, US federal work): county WNV activity
Caches: usa/cache/pools/ (SDA, POWER, CDC CSV). Writes only pool_service.json.
"""
import argparse
import csv
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from scipy import ndimage
from shapely.geometry import MultiPoint, Point, Polygon, box as sbox
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "usa"))
from common import BLOCKS, block_box_3857, load_points  # noqa: E402

DATA = ROOT / "web" / "usa" / "data"
GNSS = ROOT / "web" / "app" / "data" / "gnss.json"
CACHE = ROOT / "usa" / "cache" / "pools"
POOL_IMG_CACHE = ROOT / "usa" / "_cache" / "pools"
SDA = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
POWER = "https://power.larc.nasa.gov/api/temporal/climatology/point"
WNV_URL = "https://www.cdc.gov/wcms/vizdata/live/ncezid_dvbd/WNV/wnv_hist_hum_nonhum_yearly.csv"
UA = {"User-Agent": "roof-intel/0.1 (open-data research)"}
R = 0.5            # analysis grid (m)
VERSION = 1

SOURCES = {
    "pools": {"name": "Pool sites + yearly states (usa/pools.py on USDA NAIP / open orthophotos)", "licence": "public domain (USDA FSA NAIP)",
              "url": "https://planetarycomputer.microsoft.com/dataset/naip"},
    "lidar": {"name": "USGS 3DEP LiDAR block points (trees, canopy, no-return outline, sun ray-cast)", "licence": "public domain",
              "url": "https://registry.opendata.aws/usgs-lidar/"},
    "gnss": {"name": "EarthScope Consortium / NSF GAGE GNSS station velocities", "licence": "CC BY 4.0 (GAGE Facility Data License)",
             "url": "https://www.unavco.org/data/policies_forms/data-policy/data-license.html"},
    "sda": {"name": "USDA NRCS Soil Data Access (SSURGO) linear extensibility", "licence": "public domain (USDA federal work)",
            "url": "https://sdmdataaccess.sc.egov.usda.gov/"},
    "power": {"name": "NASA POWER climatology 2001-2025 (ALLSKY_SFC_SW_DWN, ALLSKY_SFC_UV_INDEX)", "licence": "NASA open data, CC0",
              "url": "https://power.larc.nasa.gov/"},
    "wnv": {"name": "CDC ArboNET West Nile virus activity by county, 1999-2025 (CSV behind the CDC historic-data map)",
            "licence": "public domain (US federal work, CDC)", "url": WNV_URL},
    "weather": {"name": "Dust / wind / hail days (NWS VTEC via IEM, NOAA NCEI Storm Events, GHCN-Daily) - meta.weather_triggers",
                "licence": "public domain", "url": "https://mesonet.agron.iastate.edu/vtec/"},
    "earth": {"name": "EPA radon zones + EPA AQS design values 2023-2025 (earth_county.json)", "licence": "public domain (EPA)",
              "url": "https://edg.epa.gov/EPA_Data_License.html"},
    "market": {"name": "Pool-service market: Census CBP / NES, BLS QCEW (cleaning_market.json)", "licence": "public domain",
               "url": "https://www.bls.gov/cew/"},
    "flood": {"name": "FEMA NFHL flood zone per house", "licence": "public domain", "url": "https://msc.fema.gov/"},
    "affluence": {"name": "ACS 5-year income (meta.affluence)", "licence": "public domain (US Census)", "url": "https://www.census.gov/programs-surveys/acs"},
    "osm": {"name": "OpenStreetMap road names (streets.json), only when a pool house has no address", "licence": "ODbL",
            "url": "https://www.openstreetmap.org/copyright"},
}

GREEN = {"green_algae"}
WATER_STATES = {"clean_blue", "turquoise", "green_algae", "greenish_uncertain"}


def log(*a):
    print(*a, flush=True)


def rnd(v, d=1):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, d) if d else int(round(f))


def load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def http(method, url, tries=3, **kw):
    kw.setdefault("timeout", 90)
    kw.setdefault("headers", UA)
    last = None
    for i in range(tries):
        try:
            r = requests.request(method, url, **kw)
            if r.status_code < 500 and r.status_code != 429:
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise last


# ---------------------------------------------------------------- external, cached
def sda_soil(lon, lat):
    """Linear extensibility (LEP, %) under a point: depth-weighted over 0-150 cm per component, then weighted by
    component share. NRCS shrink-swell classes: low < 3, moderate 3-6, high 6-9, very high >= 9."""
    f = CACHE / f"sda_{lon:.6f}_{lat:.6f}.json"
    rows = load(f)
    if rows is None:
        q = ("SELECT mu.mukey, mu.muname, c.cokey, c.compname, c.comppct_r, c.majcompflag, h.hzdept_r, h.hzdepb_r, h.lep_r "
             "FROM mapunit mu INNER JOIN component c ON c.mukey=mu.mukey LEFT JOIN chorizon h ON h.cokey=c.cokey "
             f"WHERE mu.mukey IN (SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon:.6f} {lat:.6f})'))")
        r = http("POST", SDA, json={"format": "JSON+COLUMNNAME", "query": q})
        if not r.ok:
            raise RuntimeError(f"SDA HTTP {r.status_code}: {r.text[:120]}")
        t = (r.json() or {}).get("Table") or []
        rows = [dict(zip(t[0], x)) for x in t[1:]] if t else []
        CACHE.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(f, "w"))
    if not rows:
        return None
    comps = {}
    for x in rows:
        c = comps.setdefault(x["cokey"], {"name": x["compname"], "pct": float(x["comppct_r"] or 0), "major": (x["majcompflag"] or "").strip() == "Yes",
                                          "num": 0.0, "den": 0.0})
        if x["lep_r"] is None or x["hzdept_r"] is None or x["hzdepb_r"] is None:
            continue
        top, bot = float(x["hzdept_r"]), min(float(x["hzdepb_r"]), 150.0)
        if bot > top:
            c["num"] += float(x["lep_r"]) * (bot - top); c["den"] += bot - top
    have = [c for c in comps.values() if c["den"] > 0 and c["pct"] > 0]
    if not have:
        return {"lep_pct": None, "class": None, "map_unit": rows[0]["muname"], "mukey": rows[0]["mukey"],
                "note": "map unit has no LEP values (e.g. urban land / water)"}
    w = sum(c["pct"] for c in have)
    lep = sum(c["num"] / c["den"] * c["pct"] for c in have) / w
    cls = "low" if lep < 3 else "moderate" if lep < 6 else "high" if lep < 9 else "very high"
    dom = max(have, key=lambda c: c["pct"])
    return {"lep_pct": rnd(lep, 1), "class": cls, "map_unit": rows[0]["muname"], "mukey": rows[0]["mukey"],
            "dominant_component": dom["name"], "components_used_pct": rnd(w, 0),
            "basis": "component-share-weighted, 0-150 cm depth-weighted lep_r (SSURGO via SDA)"}


def nasa_power(lon, lat):
    f = CACHE / f"power_{lon:.2f}_{lat:.2f}.json"
    j = load(f)
    if j is None:
        r = http("GET", POWER, params={"parameters": "ALLSKY_SFC_SW_DWN,ALLSKY_SFC_UV_INDEX", "community": "RE",
                                       "longitude": f"{lon:.4f}", "latitude": f"{lat:.4f}", "format": "JSON"})
        if not r.ok:
            raise RuntimeError(f"POWER HTTP {r.status_code}")
        j = r.json()
        CACHE.mkdir(parents=True, exist_ok=True)
        json.dump(j, open(f, "w"))
    p = j["properties"]["parameter"]
    sw = p.get("ALLSKY_SFC_SW_DWN") or {}
    uv = p.get("ALLSKY_SFC_UV_INDEX") or {}
    ann = sw.get("ANN")
    uvm = {k: v for k, v in uv.items() if k != "ANN" and v is not None and v > -900}
    note = None
    if uvm:
        pk = max(uvm, key=uvm.get)
        note = (f"NASA POWER all-sky UV index (24-h mean, not the noon peak) is highest in {pk.title()} at {uvm[pk]:.1f} "
                f"(annual mean {uv.get('ANN'):.1f}); UV breaks down free chlorine fastest in those months")
    months = {k.title(): rnd(v, 1) for k, v in sw.items() if k != "ANN" and v is not None and v > -900}
    return (rnd(ann, 1) if ann is not None and ann > -900 else None), note, months


_WNV = None


def wnv_county(fips):
    global _WNV
    if _WNV is None:
        f = CACHE / "wnv_hist_hum_nonhum_yearly.csv"
        if not f.exists():
            r = http("GET", WNV_URL, timeout=180)
            if not r.ok:
                raise RuntimeError(f"CDC HTTP {r.status_code}")
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_bytes(r.content)
        _WNV = {}
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                c = str(row.get("County") or "").strip().zfill(5)
                _WNV.setdefault(c, []).append(row)
    rows = _WNV.get(str(fips).zfill(5))
    yrs_all = sorted({int(r["Year"]) for v in _WNV.values() for r in v[:1]} | {1999, 2025})
    period = f"{min(yrs_all)}-{max(yrs_all)}"
    if not rows:
        return {"fips": fips, "period": period, "years_with_activity": 0, "years_with_human_cases": 0, "human_cases_total": 0,
                "neuroinvasive_total": 0, "last_year_activity": None, "last_year_human": None,
                "note": "county never listed in the CDC ArboNET county file (no reported activity)"}

    def n(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    hum = [r for r in rows if n(r.get("Reported human cases")) > 0]
    last = max(rows, key=lambda r: int(r["Year"]))
    return {"fips": fips, "period": period, "years_with_activity": len({r["Year"] for r in rows}),
            "years_with_human_cases": len({r["Year"] for r in hum}),
            "human_cases_total": sum(n(r.get("Reported human cases")) for r in rows),
            "neuroinvasive_total": sum(n(r.get("Neuroinvasive disease cases")) for r in rows),
            "last_year_activity": int(last["Year"]), "last_activity": last.get("Activity"),
            "last_year_human": max((int(r["Year"]) for r in hum), default=None),
            "note": "ArboNET reports by county of residence; mosquito activity means standing water (green pools) matters"}


_GN = None


def nearest_gnss(lon, lat):
    global _GN
    if _GN is None:
        _GN = load(GNSS, {}).get("stations") or []
    best = None
    for s in _GN:
        sid, slon, slat, ve, vn, vu = s[:6]
        if vu is None:
            continue
        p1, p2 = math.radians(lat), math.radians(slat)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(slon - lon) / 2) ** 2
        d = 2 * 6371 * math.asin(math.sqrt(a))
        if best is None or d < best[0]:
            best = (d, sid, vu, s[6] if len(s) > 6 else None)
    if not best:
        return None
    return {"station": best[1], "vu_mm_yr": rnd(best[2], 1), "dist_km": rnd(best[0], 1), "last_epoch": best[3]}


# ---------------------------------------------------------------- LiDAR grids
def grids(d, meta):
    half = float(meta["half_m"])
    n = int(round(2 * half / R))
    pts = load_points(d, meta)
    u = meta.get("point_unit_m", 0.02)
    px, py = pts["x"] * u, pts["y"] * u
    z = pts["z"] / 100.0                 # metres above meta.z_base
    hag = pts["h"] / 8.0
    k = pts["k"]
    ci = np.clip(((px + half) / R).astype(int), 0, n - 1)
    cj = np.clip(((py + half) / R).astype(int), 0, n - 1)
    idx = cj * n + ci
    cnt = np.bincount(idx, minlength=n * n).reshape(n, n).astype(float)
    dsm = np.full(n * n, -np.inf); np.maximum.at(dsm, idx, z); dsm = dsm.reshape(n, n)
    g = k == 0
    gs = np.zeros(n * n); gc = np.zeros(n * n)
    np.add.at(gs, idx[g], z[g]); np.add.at(gc, idx[g], 1)
    ground = np.where(gc > 0, gs / np.maximum(gc, 1), np.nan).reshape(n, n)
    if np.isnan(ground).any() and np.isfinite(ground).any():
        _, (ii, jj) = ndimage.distance_transform_edt(np.isnan(ground), return_indices=True)
        ground = ground[ii, jj]
    empty = ~np.isfinite(dsm)
    dsm[empty] = ground[empty]              # no return (water, glass): the ground level, never invented height
    veg = (k == 2) | ((k == 3) & (hag >= 2.0))   # multi-return vegetation + single-return high non-building points
    chm = np.zeros(n * n); np.maximum.at(chm, idx[veg], hag[veg]); chm = chm.reshape(n, n)
    gx = -half + (np.arange(n) + 0.5) * R
    imax = np.zeros(n * n); np.maximum.at(imax, idx, pts["i"].astype(float)); imax = imax.reshape(n, n)
    lowint = (cnt > 0) & (imax <= 12) & (dsm - ground < 0.4)   # every return in the cell is very dark, at ground level
    return {"n": n, "lowint": lowint, "half": half, "cnt": cnt, "dsm": dsm, "ground": ground, "chm": chm, "gx": gx,
            "nopts": ndimage.uniform_filter(cnt, 2) * 4 < 0.5, "pts_per_m2": meta.get("points_per_m2")}


def cells_polygon(mask, gx):
    """Union of the 0.5 m cells -> polygon; holes filled; simplified at 0.3 m (removes pixel stair-steps only)."""
    mask = ndimage.binary_fill_holes(mask)
    jj, ii = np.nonzero(mask)
    if not len(ii):
        return None
    sq = [sbox(gx[i] - R / 2, gx[j] - R / 2, gx[i] + R / 2, gx[j] + R / 2) for i, j in zip(ii, jj)]
    p = unary_union(sq)
    if p.geom_type != "Polygon":
        p = max(p.geoms, key=lambda q: q.area)
    return Polygon(p.exterior).simplify(0.3, preserve_topology=True)


def component_at(mask, x, y, gx, half, reach=3.0):
    lab, nl = ndimage.label(mask)
    if not nl:
        return None
    i, j = int((x + half) / R), int((y + half) / R)
    r = int(reach / R)
    win = lab[max(0, j - r):j + r + 1, max(0, i - r):i + r + 1]
    ids = [v for v in np.unique(win) if v]
    if not ids:
        return None
    best = max(ids, key=lambda v: (win == v).sum())
    return lab == best


def lot_mask(houses, hid, gx, x, y, reach=25.0, own=True):
    """Cells nearer to this house's footprint than to any other footprint (no open parcels here), within `reach` m of
    the pool point and outside every footprint (+0.5 m)."""
    from shapely import contains_xy
    GX, GY = np.meshgrid(gx, gx)
    n = len(gx)
    lab = np.zeros((n, n), int); foot = np.zeros((n, n), bool)
    win = (np.abs(GX - x) <= reach + 15) & (np.abs(GY - y) <= reach + 15)
    for k, h in enumerate(houses):
        if not h.get("ring") or (h.get("garage") and h.get("id") != hid):
            continue   # garages/sheds do not own yard space (usa/pools.py assigns pools to non-garage houses too)
        try:
            pg = Polygon(h["ring"])
        except Exception:  # noqa: BLE001
            continue
        if pg.distance(Point(x, y)) > reach + 15:
            continue
        ins = win & contains_xy(pg, GX, GY)
        if not ins.any():   # tiny footprint: mark its centroid cell
            c = pg.centroid
            ins = np.zeros((n, n), bool); ins[min(n - 1, max(0, int((c.y - gx[0]) / R))), min(n - 1, max(0, int((c.x - gx[0]) / R)))] = True
        lab[ins] = k + 1
        foot |= win & contains_xy(pg.buffer(0.5), GX, GY)
    me = next((k + 1 for k, h in enumerate(houses) if h.get("id") == hid), None)
    near = np.hypot(GX - x, GY - y) <= reach
    if not own:
        return near & ~foot
    if me is None or not (lab == me).any():
        return near & ~foot
    _, (ii, jj) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
    lot = (lab[ii, jj] == me) & near & ~foot
    pi_, pj_ = int((x - gx[0]) / R + 0.5), int((y - gx[0]) / R + 0.5)
    if 0 <= pi_ < n and 0 <= pj_ < n and not lot[pj_, pi_]:
        # the pool point itself is nearer another footprint (pools.py assigned it to this house by polygon distance):
        # keep the yard around the pool, still outside every footprint
        lot = near & ~foot
    return lot


def shape_ok(mask, a0, lo=0.4, hi=2.5, others=(), gx=None):
    area = mask.sum() * R * R
    for ox, oy in others:   # never swallow a neighbour's pool
        i, j = int((ox - gx[0]) / R + 0.5), int((oy - gx[0]) / R + 0.5)
        if 0 <= i < mask.shape[1] and 0 <= j < mask.shape[0] and mask[j, i]:
            return False, "region reaches another house's pool"
    if not (lo * a0 <= area <= hi * a0) or area > 160 or area < 6:
        return False, f"{area:.0f} m2 vs the {a0:.0f} m2 pool"
    yy, xx = np.nonzero(mask)
    hull = MultiPoint(np.column_stack([xx, yy]).astype(float)).convex_hull.buffer(0.5, join_style=2)
    sol = mask.sum() / max(hull.area, 1)
    if sol < 0.55:
        return False, f"ragged region (solidity {sol:.2f})"
    return True, f"{area:.0f} m2, solidity {sol:.2f}"


def outline_lidar(G, pool, masks, others, lo=0.4, hi=2.5):
    """Water gap in the full LiDAR point set: cells with no laser return (1 m window), else also ground-level cells whose
    returns are all very low intensity (specular water), connected to the pool point. Tried first in the yard (outside
    every footprint, 25 m), then cut to the house's lot (cells nearer this footprint than any other) if it leaks."""
    a0 = float(pool.get("area_m2") or 0)
    why = []
    for tag, m0 in (("no-return", G["nopts"]), ("no-return + low-intensity", G["nopts"] | G["lowint"])):
        for where, lm in masks:
            m = (m0 & lm) if tag == "no-return" else ndimage.binary_opening(m0 & lm, iterations=1) & lm
            comp = component_at(m, pool["x"], pool["y"], G["gx"], G["half"])
            if comp is None:
                why.append(f"no {tag} gap at the pool point ({where})"); continue
            ok, msg = shape_ok(comp, a0, lo=lo, hi=hi, others=others, gx=G["gx"])
            if not ok:
                why.append(f"{tag} gap ({where}) {msg}"); continue
            p = cells_polygon(comp, G["gx"])
            if p is not None and p.area >= 6:
                return p, "lidar_gap", f"LiDAR {tag} cells connected to the pool point ({where}): {msg}"
    return None, None, "; ".join(why)


def outline_naip(slug, meta, pool, houses, hid, others):
    """Water pixels (usa/pools.py rule: NDWI + blue-over-red + not dark) on the cached NAIP flights, newest first; the
    first flight whose water region at the pool point is a clean pool-sized shape inside the lot wins."""
    cdir = POOL_IMG_CACHE / slug
    fs = sorted(cdir.glob("naip_*.npz"), reverse=True) if cdir.exists() else []
    if not fs:
        return None, None, "no cached NAIP snapshots"
    from pools import CTX, features
    half = float(meta["half_m"]); H = half + CTX
    n = int(round(2 * H / R))
    gx = -H + (np.arange(n) + 0.5) * R
    masks = [("yard", lot_mask(houses, hid, gx, float(pool["x"]), float(pool["y"]), own=False)),
             ("lot", lot_mask(houses, hid, gx, float(pool["x"]), float(pool["y"])))]
    lot = masks[1][1]
    loose = []
    a0 = float(pool.get("area_m2") or 0)
    why = []
    count = np.zeros((n, n), int); years = []
    for f in fs:
        z = np.load(f, allow_pickle=True)
        arr = z["arr"].astype(np.float32); arr[arr < 0] = np.nan
        info = json.loads(str(z["info"]))
        if arr.shape[:2] != (n, n):
            continue
        s = {"arr": arr, **info}
        try:
            features(s, np.zeros((n, n), bool))
        except Exception:  # noqa: BLE001
            continue
        count += s["water"]; years.append(int(info["year"]))
        for where, lm in masks:
            w = ndimage.binary_closing(s["water"], iterations=1) & lm
            comp = component_at(w, pool["x"], pool["y"], gx, H)
            if comp is None:
                why.append(f"{info['year']}: no water at the pool point"); break
            ok, msg = shape_ok(comp, a0, lo=0.6, hi=1.6, others=others, gx=gx)
            if not ok:
                ok2, msg2 = shape_ok(comp, a0, others=others, gx=gx)
                if ok2:
                    loose.append((comp, info, where, msg2))
                why.append(f"{info['year']} ({where}): {msg}"); continue
            p = cells_polygon(comp, gx)
            if p is not None and p.area >= 6:
                return p, f"naip_{info['year']}", f"NAIP {info['date']} water pixels ({where}): {msg}"
    # no single flight gives a clean shape (small or shaded pool): pixels that are water on at least half as many
    # flights as the pool's best pixel, connected to the pool point inside the lot
    if years:
        w = (count > 0) & lot
        comp = component_at(w, pool["x"], pool["y"], gx, H)
        if comp is not None:
            core = comp & (count >= max(1, math.ceil(count[comp].max() / 2)))
            core = component_at(core, pool["x"], pool["y"], gx, H) if core.any() else None
            if core is not None:
                ok, msg = shape_ok(core, a0, others=others, gx=gx)
                if ok:
                    p = cells_polygon(core, gx)
                    if p is not None and p.area >= 6:
                        return p, f"naip_{min(years)}-{max(years)}", (f"NAIP water on >= half the best pixel's count across "
                                                                      f"{len(years)} flights {min(years)}-{max(years)}, inside the lot: {msg}")
                why.append(f"consensus: {msg}")
    for comp, info, where, msg in loose:   # newest flight whose shape is clean but outside 0.6-1.6x the pool area
        p = cells_polygon(comp, gx)
        if p is not None and p.area >= 6:
            return p, f"naip_{info['year']}", f"loose: NAIP {info['date']} water pixels ({where}): {msg}"
    return None, None, "NAIP: " + "; ".join(why[:5])


def sun_cast(G, poly, lat, lon, tz, year):
    """Direct sun on the pool surface on Jun 21 and Dec 21: rays from sample points on the water toward the sun (5-min
    steps) over the block's LiDAR surface (max return height per 0.5 m cell). Obstacles outside the block are unknown."""
    import pandas as pd
    import pvlib
    half, n, dsm, gx = G["half"], G["n"], G["dsm"], G["gx"]
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + R / 2, maxx, R); ys = np.arange(miny + R / 2, maxy, R)
    X, Y = np.meshgrid(xs, ys)
    from shapely import contains_xy
    ins = contains_xy(poly, X, Y)
    sx, sy = X[ins], Y[ins]
    if len(sx) == 0:
        c = poly.representative_point(); sx, sy = np.array([c.x]), np.array([c.y])
    if len(sx) > 48:
        sel = np.linspace(0, len(sx) - 1, 48).astype(int); sx, sy = sx[sel], sy[sel]
    ring = poly.buffer(2.5).difference(poly.buffer(0.5))
    rb = ring.bounds
    gi = (gx >= rb[0]) & (gx <= rb[2]); gj = (gx >= rb[1]) & (gx <= rb[3])
    GX, GY = np.meshgrid(gx[gi], gx[gj])
    rm = contains_xy(ring, GX, GY)
    sub = G["ground"][np.ix_(gj, gi)]
    z0 = float(np.nanmedian(sub[rm])) if rm.any() else float(np.nanmedian(G["ground"]))
    zw = z0 + 0.05
    steps = np.arange(1.0, 2 * half * 1.45, R * 0.8)
    out = {}
    for tag, md in (("jun", f"{year}-06-21"), ("dec", f"{year}-12-21")):
        t = pd.date_range(md + " 00:00", md + " 23:55", freq="5min", tz=tz)
        sp = pvlib.solarposition.get_solarposition(t, lat, lon)
        el = sp["apparent_elevation"].values; az = sp["azimuth"].values
        day = el > 0
        lit = np.zeros(len(sx)); nday = int(day.sum())
        for e, a in zip(el[day], az[day]):
            dx, dy = math.sin(math.radians(a)), math.cos(math.radians(a))
            rx = sx[:, None] + dx * steps[None, :]; ry = sy[:, None] + dy * steps[None, :]
            inside = (np.abs(rx) < half) & (np.abs(ry) < half)
            ci = np.clip(((rx + half) / R).astype(int), 0, n - 1); cj = np.clip(((ry + half) / R).astype(int), 0, n - 1)
            hz = zw + steps[None, :] * math.tan(math.radians(e))
            blocked = (inside & (dsm[cj, ci] > hz)).any(1)
            lit += ~blocked
        hrs = lit / 12.0                     # 5-min steps
        out[tag] = {"direct_h": float(hrs.mean()), "daylight_h": nday / 12.0}
    return {"direct_hours_jun21": rnd(out["jun"]["direct_h"], 1), "direct_hours_dec21": rnd(out["dec"]["direct_h"], 1),
            "shade_share_jun": rnd(1 - out["jun"]["direct_h"] / max(out["jun"]["daylight_h"], 1e-6), 2),
            "daylight_hours_jun21": rnd(out["jun"]["daylight_h"], 1), "daylight_hours_dec21": rnd(out["dec"]["daylight_h"], 1),
            "samples": int(len(sx)), "water_z_m": rnd(z0, 2),
            "method": "5-min sun positions (pvlib, meta.tz) ray-cast over the LiDAR max-return surface (0.5 m); "
                      "obstacles outside the block not seen"}


def tree_stats(G, poly, bld):
    half, n, chm, gx = G["half"], G["n"], G["chm"], G["gx"]
    from shapely import contains_xy
    GX, GY = np.meshgrid(gx, gx)
    near = contains_xy(poly.buffer(10.0), GX, GY) & ~bld
    over = contains_xy(poly, GX, GY)
    hmax = float(chm[near].max()) if near.any() else 0.0
    # individual crowns: CHM local maxima > 3 m with >= 2 m between tops (smoothed to merge one crown's peaks)
    sm = ndimage.gaussian_filter(np.where(bld, 0, chm), 1.0)
    peaks = (sm == ndimage.maximum_filter(sm, size=int(4 / R) + 1)) & (chm > 3.0) & near & ~bld
    lab, nt = ndimage.label(peaks)
    share = float((chm[over] >= 2.0).mean()) if over.any() else 0.0
    return {"max_h_m": rnd(hmax, 1), "max_h_capped": bool(hmax >= 31.8), "n_over_3m_within_10m": int(nt), "canopy_over_pool_share": rnd(share, 2),
            "overhang": bool(share >= 0.02),
            "method": "LiDAR vegetation returns (multi-return + single-return >= 2 m non-building) within 10 m of the pool edge; "
                      "crowns = height-model local maxima > 3 m; canopy over pool = pool cells with vegetation >= 2 m"}


def building_mask(G, houses):
    from shapely import contains_xy
    GX, GY = np.meshgrid(G["gx"], G["gx"])
    m = np.zeros((G["n"], G["n"]), bool)
    for h in houses:
        if h.get("ring"):
            try:
                m |= contains_xy(Polygon(h["ring"]).buffer(0.5), GX, GY)
            except Exception:  # noqa: BLE001
                pass
    return m


# ---------------------------------------------------------------- streets / route
def street_of(addr):
    if not addr:
        return None
    s = re.sub(r"^\s*\d+[A-Za-z]?(-\d+)?\s+", "", str(addr)).strip()
    s = re.sub(r"\s+(apt|unit|#).*$", "", s, flags=re.I)
    return s.title() if s else None


def nearest_road(d, x, y):
    s = load(d / "streets.json", {})
    best = None
    for r in s.get("roads") or []:
        if not r.get("name") or not r.get("poly"):
            continue
        try:
            dist = Polygon([(p[0], p[1]) for p in r["poly"]]).exterior.distance(Point(x, y)) if len(r["poly"]) > 2 else 1e9
        except Exception:  # noqa: BLE001
            continue
        if best is None or dist < best[0]:
            best = (dist, r["name"])
    return best


def route(pools):
    """Nearest-neighbour walk, crossing to another street costs +40 m; starts at the pool nearest the block's SW corner."""
    left = list(range(len(pools)))
    if not left:
        return []
    cur = min(left, key=lambda i: pools[i]["x"] + pools[i]["y"])
    order = [cur]; left.remove(cur)
    while left:
        def cost(j):
            dd = math.hypot(pools[j]["x"] - pools[cur]["x"], pools[j]["y"] - pools[cur]["y"])
            return dd + (0 if pools[j]["_street"] == pools[cur]["_street"] else 40)
        cur = min(left, key=cost); order.append(cur); left.remove(cur)
    return [pools[i]["id"] for i in order]


# ---------------------------------------------------------------- rules
def lin(v, lo, hi):
    if v is None:
        return None
    return max(0.0, min(1.0, (float(v) - lo) / (hi - lo)))


def visit_rule(p, clim):
    """Visits per week. Base 1 (the standard weekly residential route). Additions, each citing its measured value."""
    per, why = 1.0, ["base: 1 weekly visit (standard residential route)"]
    t, s = p["trees"], p["sun"]
    if t and ((t["canopy_over_pool_share"] or 0) >= 0.15 or (t["n_over_3m_within_10m"] or 0) >= 4):
        per += 0.5
        why.append(f"+0.5 leaf/debris load: canopy over {t['canopy_over_pool_share']:.0%} of the pool, "
                   f"{t['n_over_3m_within_10m']} tree crowns > 3 m within 10 m (tallest {'>= ' if t.get('max_h_capped') else ''}{t['max_h_m']} m, LiDAR)")
    dd = clim.get("dust_days_yr"); ww = clim.get("high_wind_days_yr")
    if (dd or 0) >= 20 or (ww or 0) >= 30:
        per += 0.5
        why.append(f"+0.5 in storm season: {dd or 0:.0f} dust days and {ww or 0:.0f} high-wind days per year (county, NWS/NCEI)")
    if s and (s["direct_hours_jun21"] or 0) >= 10 and (clim.get("sun_kwh_m2_day") or 0) >= 5.5:
        per += 0.5
        why.append(f"+0.5 in summer: {s['direct_hours_jun21']} h direct sun on the water on Jun 21 (LiDAR ray-cast) and "
                   f"{clim['sun_kwh_m2_day']} kWh/m2/day mean irradiance (NASA POWER) burn off chlorine fast")
    if p["state_now"] in GREEN:
        per += 1.0
        why.append(f"+1 until cleared: pool looked green/algae on the latest image ({p['_latest']})")
    elif p["state_now"] == "covered_or_empty":
        why.append(f"no change: pool covered or empty on the latest image ({p['_latest']}) - start-up visit first")
    return {"per_week": min(round(per * 2) / 2, 3.0), "reasons": why,
            "rule": "1 + 0.5 debris (canopy >= 15% or >= 4 crowns) + 0.5 dust/wind (>= 20 dust or >= 30 wind days) "
                    "+ 0.5 sun (>= 10 h Jun 21 and >= 5.5 kWh/m2/day) + 1 green now; cap 3"}


def lead_rule(p, clim, aff_inc, conf):
    pts, why = {}, []
    hist = p["history"]
    if p["state_now"] in GREEN:
        pts["green_now"] = 30; why.append(f"+30 green/algae on the latest image ({p['_latest']})")
    elif p["state_now"] == "covered_or_empty":
        pts["green_now"] = 12; why.append(f"+12 covered or empty on the latest image ({p['_latest']}) - start-up / refill clean")
    elif p["state_now"] == "turquoise":
        pts["green_now"] = 5; why.append(f"+5 off-blue (turquoise) on the latest image ({p['_latest']})")
    past = sorted({h["year"] for h in hist if h["state"] in GREEN and h["year"] != p["_latest_year"]})
    if past:
        pts["green_past"] = 15 if len(past) >= 2 else 10
        why.append(f"+{pts['green_past']} green in earlier images {', '.join(map(str, past))}")
    t = p["trees"]
    if t:
        tp = round(15 * min(1.0, (t["canopy_over_pool_share"] or 0) / 0.3 * 0.6 + min(t["n_over_3m_within_10m"], 5) / 5 * 0.4))
        if tp:
            pts["trees"] = tp
            why.append(f"+{tp} trees: canopy over {t['canopy_over_pool_share']:.0%} of the pool, {t['n_over_3m_within_10m']} crowns > 3 m within 10 m (LiDAR)")
    dw = lin(clim.get("dust_days_yr"), 0, 30)
    if dw and round(10 * dw):
        pts["dust"] = round(10 * dw)
        why.append(f"+{pts['dust']} {clim['dust_days_yr']:.0f} dust days per year (county)")
    a = lin(aff_inc, 45000, 160000)
    if a:
        pts["affluence"] = round(15 * a)
        why.append(f"+{pts['affluence']} median household income ${aff_inc:,.0f} (ACS)")
    s = p["sun"]
    if s and s["direct_hours_jun21"] is not None and s["direct_hours_jun21"] >= 10:
        pts["sun"] = 5; why.append(f"+5 {s['direct_hours_jun21']} h direct sun on Jun 21 (algae/chlorine demand)")
    if p["risk_wnv"]:
        pts["wnv"] = 5; why.append(f"+5 county had West Nile human cases in {p['risk_wnv']} (CDC ArboNET) - standing green water is a code issue")
    raw = sum(pts.values())
    c = conf if conf is not None else 0.7
    sc = round(raw * (0.5 + 0.5 * c))
    why.append(f"x {0.5 + 0.5 * c:.2f} for pool detection confidence {c:.2f}")
    return {"score": int(sc), "points": pts, "reasons": why,
            "rule": "green now 30 (covered 12, turquoise 5) + past green 10/15 + trees <= 15 + dust <= 10 + income <= 15 "
                    "+ sun 5 + WNV county 5; x (0.5 + 0.5 x confidence)"}


# ---------------------------------------------------------------- block
def build(slug, cov):
    d = BLOCKS / slug
    meta, houses = load(d / "meta.json"), load(d / "houses.json")
    if meta is None or houses is None:
        return None
    hs = houses["houses"] if isinstance(houses, dict) else houses
    ph = [h for h in hs if isinstance(h.get("pool"), dict) and h["pool"].get("present")]
    if not ph:
        return None
    lon0, lat0 = meta["origin"]; fips = str(meta.get("fips") or meta.get("county_fips") or "").zfill(5)
    used = {"pools", "lidar", "weather", "earth", "market", "affluence"}
    # area
    wt = meta.get("weather_triggers") or {}
    wc = wt.get("county") if isinstance(wt.get("county"), dict) else wt
    ec = (load(DATA / "earth_county.json", {}).get("counties") or {}).get(fips) or {}
    try:
        kwh, uvn, monthly = nasa_power(lon0, lat0); used.add("power")
    except Exception as e:  # noqa: BLE001
        log("  POWER failed:", e); kwh, uvn, monthly = None, None, None
    clim = {"dust_days_yr": rnd(wc.get("dust_days_yr"), 1), "high_wind_days_yr": rnd(wc.get("high_wind_days_yr"), 1),
            "dust_then_rain_yr": rnd(wc.get("dust_then_rain_yr"), 1), "hail_days_yr": rnd(wc.get("hail_days_yr"), 1),
            "pm25_dv": rnd(ec.get("pm25_dv"), 1), "o3_dv": rnd(ec.get("o3_dv"), 0), "sun_kwh_m2_day": kwh, "uv_note": uvn,
            "sun_kwh_m2_day_monthly": monthly,
            "sun_source": "NASA POWER 2001-2025 all-sky climatology (NREL NSRDB needs an API key, not used)" if kwh is not None else None}
    try:
        wnv = wnv_county(fips); used.add("wnv")
    except Exception as e:  # noqa: BLE001
        log("  CDC WNV failed:", e); wnv = None
    gn = nearest_gnss(lon0, lat0)
    if gn:
        used.add("gnss")
    risks = {"wnv_county": wnv, "radon_zone": ec.get("radon_zone"),
             "subsidence": {k: gn[k] for k in ("station", "vu_mm_yr", "dist_km")} if gn else None}
    if gn:
        risks["subsidence"]["note"] = "vertical velocity of the nearest permanent GNSS station (NAM14), not measured at the block"
    market = dict((meta.get("cleaning") or {}).get("market") or {}) or None
    aff = meta.get("affluence") or {}
    inc = aff.get("median_hh_income")
    if isinstance(inc, dict):
        bg, moe = inc.get("block_group"), inc.get("block_group_moe")
        inc_v = bg if (bg and moe is not None and moe < 0.5 * bg) else inc.get("tract") or bg
    else:
        inc_v = inc

    # LiDAR
    G = None
    try:
        G = grids(d, meta)
        bld = building_mask(G, [h for h in hs])
    except Exception as e:  # noqa: BLE001
        log("  LiDAR unavailable:", e)
    cx, cy, kk, _ = block_box_3857(lon0, lat0, 1)
    from pyproj import Transformer
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    year = datetime.now().year
    pools = []
    for h in ph:
        P = h["pool"]
        by = sorted([b for b in P.get("by_year") or [] if b.get("year")], key=lambda b: (b["year"], b.get("date") or ""))
        hist = [{"year": b["year"], "state": b["state"]} for b in by]
        dated = [b for b in by if b["state"] != "no_data"]
        latest = dated[-1] if dated else None
        x, y = float(P["x"]), float(P["y"])
        others = [(float(q["pool"]["x"]), float(q["pool"]["y"])) for q in ph if q is not h]
        plon, plat = to_ll.transform(cx + x * kk, cy + y * kk)
        street = street_of(h.get("address")); street_src = "address" if street else None
        if not street:
            nr = nearest_road(d, x, y)
            if nr:
                street, street_src = nr[1], f"nearest OSM road ({nr[0]:.0f} m)"
        poly, osrc, onote = None, None, None
        if G is not None:
            masks = [("yard", lot_mask(hs, h["id"], G["gx"], x, y, own=False)), ("lot", lot_mask(hs, h["id"], G["gx"], x, y))]
            poly, osrc, onote = outline_lidar(G, P, masks, others, lo=0.6, hi=1.6)   # strict: 0.6-1.6x the pool area
        if poly is None:
            try:
                p2, s2, o2 = outline_naip(slug, meta, P, hs, h["id"], others)
            except Exception as e:  # noqa: BLE001
                p2, s2, o2 = None, None, f"NAIP outline failed: {e}"
            if p2 is not None and not o2.startswith("loose"):
                poly, osrc, onote = p2, s2, o2
            elif G is not None:
                p3, s3, o3 = outline_lidar(G, P, masks, others)   # loose LiDAR (0.4-2.5x) before a loose NAIP shape
                if p3 is not None:
                    poly, osrc, onote = p3, s3, o3 + " (outside 0.6-1.6x of the pool area)"
                elif p2 is not None:
                    poly, osrc, onote = p2, s2, o2
                else:
                    onote = f"{onote}; {o2}" if onote else o2
            elif p2 is not None:
                poly, osrc, onote = p2, s2, o2
            else:
                onote = f"{onote}; {o2}" if onote else o2
        # analysis footprint: the measured outline, else a disc of the measured area at the pool point (not output)
        apoly = poly if poly is not None else Point(x, y).buffer(math.sqrt(max(float(P.get("area_m2") or 20), 6) / math.pi))
        trees = sun = None
        if G is not None:
            try:
                trees = tree_stats(G, apoly, bld)
                if poly is None:
                    trees["footprint"] = "disc of the measured pool area at the pool point (no measured outline)"
            except Exception as e:  # noqa: BLE001
                log("  trees failed:", e)
            try:
                sun = sun_cast(G, apoly, plat, plon, meta.get("tz") or "UTC", year)
            except Exception as e:  # noqa: BLE001
                log("  sun failed:", e)
        try:
            soil = sda_soil(plon, plat); used.add("sda")
        except Exception as e:  # noqa: BLE001
            log("  SDA failed:", e); soil = None
        if h.get("flood_zone"):
            used.add("flood")
        if street_src and street_src.startswith("nearest"):
            used.add("osm")
        lidar_year = meta.get("lidar_year")
        notes = []
        if lidar_year and P.get("first_seen_year") and int(P["first_seen_year"]) > int(lidar_year):
            notes.append(f"pool first seen {P['first_seen_year']}, after the {lidar_year} LiDAR: outline/trees describe the site before the pool")
        hum_years = None
        if wnv and wnv.get("last_year_human") and wnv["last_year_human"] >= year - 3:
            hum_years = wnv["last_year_human"]
        p = {"id": f"{slug}:{h['id']}", "house_id": h["id"], "address": h.get("address"), "street": street, "street_source": street_src,
             "x": rnd(x, 1), "y": rnd(y, 1), "lon": rnd(plon, 6), "lat": rnd(plat, 6),
             "area_m2": rnd(P.get("area_m2"), 1), "confidence": rnd(P.get("confidence"), 2), "first_seen_year": P.get("first_seen_year"),
             "outline": [[rnd(a, 1), rnd(b, 1)] for a, b in list(poly.exterior.coords)[:-1]] if poly is not None else None,
             "outline_source": osrc, "outline_note": onote, "outline_area_m2": rnd(poly.area, 1) if poly is not None else None,
             "state_now": latest["state"] if latest else None, "state_now_date": latest.get("date") if latest else None,
             "history": hist, "trees": trees, "sun": sun,
             "ground": {"soil_shrink_swell": (soil or {}).get("class"), "flood_zone": h.get("flood_zone"),
                        "subsidence_vu_mm_yr": gn["vu_mm_yr"] if gn and gn["dist_km"] is not None and gn["dist_km"] <= 50 else None,
                        "soil": soil,
                        "subsidence_note": (f"GNSS {gn['station']} {gn['dist_km']} km away" if gn and gn["dist_km"] <= 50
                                            else "nearest GNSS station > 50 km away: not used per pool") if gn else None},
             "notes": notes,
             "_street": street, "_latest": (latest or {}).get("date") or (latest or {}).get("year"),
             "_latest_year": (latest or {}).get("year"), "risk_wnv": hum_years}
        p["visit"] = visit_rule(p, clim)
        p["lead"] = lead_rule(p, clim, inc_v, P.get("confidence"))
        pools.append(p)
        for key, ok in (("outline", p["outline"] is not None), ("trees", trees is not None), ("sun", sun is not None),
                        ("soil", (soil or {}).get("class") is not None), ("flood", bool(h.get("flood_zone"))),
                        ("subsidence", p["ground"]["subsidence_vu_mm_yr"] is not None), ("history", bool(hist))):
            cov.setdefault(key, [0, 0]); cov[key][0] += ok; cov[key][1] += 1
    streets = {}
    for p in pools:
        streets.setdefault(p["_street"] or "(no street)", []).append(p["id"])
    rt = route(pools)
    for p in pools:
        for k in ("_street", "_latest", "_latest_year", "risk_wnv"):
            p.pop(k, None)
    mp = meta.get("pools") or {}
    out = {"slug": slug, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "version": VERSION,
           "sources": [SOURCES[k] for k in SOURCES if k in used],
           "block": {"n_pools": len(pools), "n_houses": sum(1 for h in hs if not h.get("garage")),
                     "pools_within_100m": mp.get("context_pools") if isinstance(mp.get("context_pools"), int) else None,
                     "pools_within_100m_note": "pool sites found by usa/pools.py in the block plus a 100 m ring",
                     "streets": [{"name": k, "pool_ids": v} for k, v in streets.items()],
                     "route": rt, "route_rule": "nearest-neighbour walk from the pool nearest the SW corner; changing street costs +40 m",
                     "climate": clim, "risks": risks, "market": market,
                     "market_source": (meta.get("cleaning") or {}).get("market_source"),
                     "lidar": meta.get("lidar"), "lidar_year": meta.get("lidar_year"), "tz": meta.get("tz")},
           "pools": pools,
           "not_available": ["pool depth and water volume (in no open dataset)", "how often each pool is actually cleaned"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append")
    a = ap.parse_args()
    slugs = a.slug or sorted(p.parent.name for p in BLOCKS.glob("*/meta.json"))
    cov, rows = {}, []
    for s in slugs:
        try:
            out = build(s, cov)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc(); log(f"{s}: failed {e}"); continue
        f = BLOCKS / s / "pool_service.json"
        if out is None:
            continue
        tmp = f.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, f)
        rows.append((s, out["block"]["n_pools"], out["block"]["pools_within_100m"]))
        log(f"== {s}: {out['block']['n_pools']} pools, route {out['block']['route']}")
        for p in out["pools"]:
            log(f"   {p['id']:<22} {str(p['address'])[:28]:<28} {p['area_m2']} m2 now={p['state_now']} outline={p['outline_source']}/{p['outline_area_m2']} "
                f"trees={p['trees'] and (p['trees']['max_h_m'], p['trees']['n_over_3m_within_10m'], p['trees']['canopy_over_pool_share'])} "
                f"sun={p['sun'] and (p['sun']['direct_hours_jun21'], p['sun']['direct_hours_dec21'], p['sun']['shade_share_jun'])} "
                f"soil={p['ground']['soil_shrink_swell']} visit={p['visit']['per_week']} lead={p['lead']['score']}")
    log("\nCoverage summary")
    log(f"  blocks with pools: {len(rows)}  pools: {sum(r[1] for r in rows)}")
    for s, n, c in rows:
        log(f"   {s:<18} pools {n}  within 100 m {c}")
    for k, (ok, tot) in cov.items():
        log(f"  {k:<11} {ok}/{tot}")


if __name__ == "__main__":
    main()

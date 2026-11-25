"""Swimming pools per house and whether each looks clean (blue) or green (algae) on every open aerial snapshot.

  python usa/pools.py --slug austin-tx [--slug dallas-tx ...] [--all] [--samples] [--no-s2] [--no-oam]

Imagery (free + commercial use allowed only; every snapshot keeps its source, licence and resolution):
  USDA NAIP 0.6-1 m RGB+NIR, every NAIP year       Planetary Computer STAC 'naip'          public domain
  OpenAerialMap drone / aerial COGs                 api.openaerialmap.org                    CC BY 4.0 / CC BY-SA / CC0 only
  state / county orthophotos                        web/usa/data/imagery_sources.json        only entries whose licence is open
  Sentinel-2 L2A 10 m (neighbourhood signal only)   Planetary Computer STAC 'sentinel-2-l2a' Copernicus free licence (commercial OK)
No Google / Esri / Nearmap / Planet / Maxar imagery.

Method (details + limitations: usa/research/pools_method.md):
  1. every snapshot is resampled onto one 0.5 m grid over the block plus a 100 m context ring (row 0 = south, local
     metres = block convention); colour is grey-world balanced on the snapshot's own paved/roof pixels
  2. water pixels: NDWI=(G-NIR)/(G+NIR) high (when NIR exists), blue over red, not dark (shadow), low NDVI, not on a
     building footprint or LiDAR surface > 1.5 m above ground (roofs, trees)
  3. pool sites = compact blobs 12-150 m2 that are water in at least one snapshot; geometry is then FIXED so every
     snapshot is judged on the same pixels (a green or covered pool is still measured once the site is known)
  4. per snapshot: clean_blue / turquoise / green_algae / covered_or_empty / dark / not_present, using absolute
     colour plus colour relative to the other pools in the context window that snapshot (camera/illumination control)
  5. each block-house gets its nearest pool site (largest if several) -> h.pool; block summary -> meta.pools
Writes only h.pool and meta.pools (inside usa.blocklock.block_lock). Imagery cached under usa/_cache/pools/<slug>/.
"""
import argparse
import io
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import MultiPoint, Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BLOCKS, ROOT, _np_default, block_box_3857, get, load_points, raster_to_block  # noqa: E402
from usa.blocklock import block_lock  # noqa: E402

CACHE = ROOT / "usa" / "_cache" / "pools"
CATALOGUE = ROOT / "web" / "usa" / "data" / "imagery_sources.json"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
R = 0.5                      # analysis grid, metres
CTX = 100.0                  # context ring around the block (reference pools), metres
MIN_M2, MAX_M2 = 12.0, 150.0
OPEN_LIC = ("cc-by", "cc by", "cc0", "public domain", "pddl", "cc-by-sa", "cc by-sa", "odc-by", "us government work", "open data commons")

# thresholds (tuned on austin/dallas/tulsa/phoenix NAIP; see pools_method.md)
NDWI_REL = 0.40             # water pixel: NDWI above the snapshot's paved/roof NDWI by this much (NAIP DNs are not
                            # reflectance, so absolute NDWI drifts by +-0.15 between flights)
BLUE_OVER_RED = 1.25        # grey-balanced b / r for water pixels
CHROMA = 0.40               # (b - r) / brightness: pool water is a saturated cyan/blue, asphalt and roofs are not
DARK = 0.55                 # brightness vs the snapshot's reference (paved/roof) brightness; below = shadow


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- imagery providers -> snapshots
def _stac():
    import planetary_computer
    from pystac_client import Client
    return Client.open(STAC, modifier=planetary_computer.sign_inplace)


def naip_snapshots(meta, box, n, cdir):
    """One snapshot per NAIP flight date covering the context window (quarter-quads mosaicked)."""
    from shapely.geometry import box as sbox, shape as sshape
    W, S_, E, N = meta["bounds"]
    items = list(_stac().search(collections=["naip"], bbox=[W, S_, E, N], max_items=300).items())
    by_year = {}
    for it in items:
        y = int(it.properties["datetime"][:4])
        by_year.setdefault(y, []).append(it)
    out = []

    def one(y):
        its = sorted(by_year[y], key=lambda i: i.properties["datetime"])
        f = cdir / f"naip_{y}.npz"
        if f.exists():
            z = np.load(f, allow_pickle=True)
            return {"arr": z["arr"].astype(np.float32), **json.loads(str(z["info"]))}
        arr = None; used = []
        for it in reversed(its):  # latest flight of the year first, fill gaps from the others
            nb = len(it.assets["image"].extra_fields.get("eo:bands", [1, 2, 3, 4])) or 4
            try:
                a = raster_to_block(it.assets["image"].href, box, n, bands=list(range(1, min(nb, 4) + 1)), margin=0.02)
            except Exception as e:  # noqa: BLE001
                log(f"  NAIP {it.id} failed: {str(e)[:100]}"); continue
            if a.shape[0] == 3:
                a = np.concatenate([a, np.full((1, n, n), np.nan, np.float32)])
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
        arr = np.flipud(arr.transpose(1, 2, 0)).copy()  # (n, n, 4), row 0 = south
        it0 = used[0]
        info = {"source": "USDA NAIP", "licence": "public domain (USDA FSA)", "gsd_m": float(it0.properties.get("gsd") or 1.0),
                "date": it0.properties["datetime"][:10], "year": y, "items": [i.id for i in used], "nir": bool(np.isfinite(arr[..., 3]).any())}
        np.savez_compressed(f, arr=np.nan_to_num(arr, nan=-1).astype(np.int16), info=json.dumps(info))
        return {"arr": arr, **info}

    with ThreadPoolExecutor(6) as ex:
        for s in ex.map(one, sorted(by_year)):
            if s is not None:
                a = s["arr"]; a[a < 0] = np.nan; out.append(s)
    return out


def oam_snapshots(meta, box, n, cdir):
    """OpenAerialMap drone/aerial COGs covering the block; open licences only."""
    W, S_, E, N = meta["bounds"]
    r = get("https://api.openaerialmap.org/meta", params={"bbox": f"{W},{S_},{E},{N}", "limit": 50}, timeout=60)
    res = r.json().get("results", []) if r.ok else []
    out = []
    for m in res:
        lic = (m.get("properties", {}) or {}).get("license") or m.get("license") or ""
        if not any(k in lic.lower() for k in OPEN_LIC) or "nc" in lic.lower().replace("cc0", ""):
            log(f"  OAM {m.get('_id')} skipped: licence {lic!r}"); continue
        url = m.get("uuid")
        if not url or not url.lower().endswith((".tif", ".tiff")):
            continue
        d = (m.get("acquisition_end") or m.get("acquisition_start") or "")[:10]
        f = cdir / f"oam_{m['_id']}.npz"
        try:
            if f.exists():
                arr = np.load(f)["arr"].astype(np.float32)
            else:
                a = raster_to_block(url, box, n, bands=[1, 2, 3], margin=0.02)
                arr = np.flipud(a.transpose(1, 2, 0)).copy()
                np.savez_compressed(f, arr=arr.astype(np.uint8))
            if (arr.sum(2) > 0).mean() < 0.05:
                continue
            arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), np.nan, np.float32)], 2)
            out.append({"arr": arr, "source": f"OpenAerialMap {m.get('provider') or ''} '{(m.get('title') or '')[:60]}'".strip(),
                        "licence": lic, "gsd_m": round(float(m.get("gsd") or 0), 3) or None, "date": d, "year": int(d[:4]) if d else None,
                        "items": [m["_id"]], "nir": False})
        except Exception as e:  # noqa: BLE001
            log(f"  OAM {m.get('_id')} failed: {str(e)[:100]}")
    return out


def catalogue_snapshots(meta, box, n, cdir):
    """Open state/county orthophotos catalogued by usa/imagery.py (web/usa/data/imagery_sources.json).
    Used: aerial/drone entries with commercial_ok true whose 'access' is an ArcGIS ImageServer (exportImage, extent
    checked first). NAIP and OpenAerialMap are fetched by their own providers; WMS / S3-tile-index entries are skipped
    (logged) because they need per-source tiling code."""
    import re
    if not CATALOGUE.exists():
        return []
    try:
        cat = json.load(open(CATALOGUE, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log("  imagery catalogue unreadable:", e); return []
    out = []
    for e in cat.get("sources", []) if isinstance(cat, dict) else []:
        name = e.get("name") or "orthophoto"
        if e.get("commercial_ok") is not True or e.get("kind") not in ("aerial", "drone") or name.upper().startswith("NAIP") \
                or "openaerialmap" in name.lower():
            continue
        m = re.search(r"https?://\S+/ImageServer", str(e.get("access") or ""))
        if not m:
            continue
        url = m.group(0)
        year = e.get("newest_year")
        f = cdir / f"cat_{re.sub(r'[^a-z0-9]+', '_', name.lower())[:60]}.npz"
        try:
            if f.exists():
                z = np.load(f, allow_pickle=True)
                if z["arr"].size == 1:
                    continue  # cached 'does not cover this block'
                arr = z["arr"].astype(np.float32)
            else:
                info = get(url, params={"f": "json"}, timeout=60).json()
                ext = info.get("extent") or {}
                wk = (ext.get("spatialReference") or {}).get("latestWkid") or (ext.get("spatialReference") or {}).get("wkid")
                if wk in (3857, 102100) and not (ext["xmin"] <= box[0] and ext["ymin"] <= box[1] and ext["xmax"] >= box[2] and ext["ymax"] >= box[3]):
                    np.savez_compressed(f, arr=np.zeros(1, np.uint8)); continue
                nb = int(info.get("bandCount") or 3)
                r = get(url + "/exportImage", params={"bbox": ",".join(f"{v:.2f}" for v in box), "bboxSR": 3857, "imageSR": 3857,
                                                     "size": f"{n},{n}", "format": "tiff", "pixelType": "U8", "interpolation": "RSP_BilinearInterpolation",
                                                     "bandIds": ",".join(str(i) for i in range(min(nb, 4))), "f": "image"}, timeout=240)
                import rasterio
                with rasterio.MemoryFile(r.content) as mf, mf.open() as src:
                    a_ = src.read().astype(np.float32)
                arr = np.flipud(a_[:4].transpose(1, 2, 0)).copy()
                np.savez_compressed(f, arr=arr.astype(np.uint8))
            if arr.shape[2] == 3:
                arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), np.nan, np.float32)], 2)
            if (np.nansum(arr[..., :3], 2) > 0).mean() < 0.5:
                continue
            out.append({"arr": arr, "source": name, "licence": e.get("licence"), "gsd_m": e.get("resolution_m"),
                        "date": str(year or ""), "year": int(year) if year else None, "items": [url],
                        "nir": bool(np.isfinite(arr[..., 3]).any() and np.nanmax(arr[..., 3]) > 0)})
            log(f"  + {name} ({year}, {e.get('resolution_m')} m, {e.get('licence')})")
        except Exception as ex:  # noqa: BLE001
            log(f"  catalogue {name} failed: {str(ex)[:120]}")
    return out


# ---------------------------------------------------------------- per-snapshot features
def features(s, block_mask):
    a = s["arr"]
    valid = np.nansum(a[..., :3], 2) > 0
    has_nir = s["nir"] and np.isfinite(a[..., 3][valid]).mean() > 0.9
    # dark-object subtraction: haze adds a roughly constant (mostly blue) offset that makes asphalt and shadows look
    # like water; remove each band's 0.5th percentile before any ratio or index
    dos = [float(np.nanpercentile(a[..., i][valid], 0.5)) if (i < 3 or has_nir) else 0.0 for i in range(4)]
    r, g, b, nir = (np.maximum(a[..., i] - dos[i], 0) for i in range(4))
    ndwi = (g - nir) / np.maximum(g + nir, 1) if has_nir else None
    ndvi = (nir - r) / np.maximum(nir + r, 1) if has_nir else None
    # grey-world balance on the snapshot's own non-vegetated, mid-to-bright pixels (roofs, paving, bare ground);
    # pools are < 1 % of the window so they do not bias it
    br = (r + g + b) / 3
    exg = (2 * g - r - b) / np.maximum(r + g + b, 1)
    neutral = valid & ((ndvi < 0.15) if ndvi is not None else (exg < 0.02))
    lo, hi = np.percentile(br[neutral], [40, 95]) if neutral.sum() > 200 else np.percentile(br[valid], [40, 95])
    ref = neutral & (br >= lo) & (br <= hi)
    med = np.array([np.median(c[ref]) for c in (r, g, b)]) if ref.sum() > 100 else np.array([np.median(c[valid]) for c in (r, g, b)])
    k = med.mean() / np.maximum(med, 1)
    rn, gn, bn = r * k[0], g * k[1], b * k[2]
    ref_bright = float(med.mean())
    s["ndwi_abs"] = ndwi
    if has_nir:  # NIR indices relative to the same neutral pixels
        ndwi = ndwi - float(np.median(ndwi[ref])) if ref.sum() > 100 else ndwi - float(np.median(ndwi[valid]))
        ndvi = ndvi - float(np.median(ndvi[ref])) if ref.sum() > 100 else ndvi - float(np.median(ndvi[valid]))
    s.update(rn=rn, gn=gn, bn=bn, ndwi=ndwi, ndvi=ndvi, valid=valid, has_nir=has_nir, ref_bright=ref_bright, balance=k.round(3).tolist(), dos=[round(v, 1) for v in dos])
    brn = (rn + gn + bn) / 3
    water = valid & (bn > BLUE_OVER_RED * rn) & (gn > rn) & (bn >= 0.85 * gn) & (brn > DARK * ref_bright)         & ((bn - rn) > CHROMA * brn)
    if has_nir:
        water &= (ndwi > NDWI_REL) & (ndvi < -0.05)
    else:  # RGB-only snapshot: stricter colour rule
        water &= (bn > 1.5 * rn) & (gn > 1.15 * rn)
    s["water"] = water & ~block_mask
    return s


def hue_deg(r, g, b):
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 1e-6:
        return None
    if mx == r:
        h = ((g - b) / (mx - mn)) % 6
    elif mx == g:
        h = (b - r) / (mx - mn) + 2
    else:
        h = (r - g) / (mx - mn) + 4
    return round(60 * h, 1)


def site_stats(s, m):
    rn, gn, bn = (float(np.median(c[m])) for c in (s["rn"], s["gn"], s["bn"]))
    d = {"rgb": [round(rn), round(gn), round(bn)], "hue": hue_deg(rn, gn, bn),
         "greenness": round((gn - bn) / max(gn + bn, 1), 3), "bright": round((rn + gn + bn) / 3 / max(s["ref_bright"], 1), 2),
         "water_frac": round(float(s["water"][m].mean()), 2)}
    if s["has_nir"]:
        d["ndwi"] = round(float(np.median(s["ndwi"][m])), 3)
        d["ndvi"] = round(float(np.median(s["ndvi"][m])), 3)
    else:
        d["ndwi"] = d["ndvi"] = None
    return d


def classify(d, ref):
    """State of a known pool site on one snapshot. ref = median stats of the clean pools in the context window that snapshot."""
    if d is None:
        return "no_data"
    gr, hue, br = d["greenness"], d["hue"] if d["hue"] is not None else 0, d["bright"]
    ndwi, ndvi = d["ndwi"], d["ndvi"]
    g_rel = gr - ref["greenness"] if ref else None
    # a flight that makes many pools look green (sensor / processing cast) only flags the clear outliers
    g_out = ref is None or ref["n"] < 4 or gr > ref["greenness_q75"] + 0.03
    if (ndvi is not None and ndvi > 0.25 and ndwi < 0.0) or (gr > 0.15 and 60 <= hue <= 150 and (ndwi is None or ndwi < 0.05)):
        return "not_present"              # vegetation (pool not built yet / filled in / overgrown); relative NDVI/NDWI
    if br < DARK and (ndwi is None or ndwi < 0.3):
        return "dark"                     # shadow (tree / house / cloud) or very dark water: colour not judged
    if ndwi is not None and ndvi is not None:   # NDWI/NDVI here are relative to the snapshot's paved/roof pixels
        if ndwi < 0.15 and d["water_frac"] < 0.2:
            if gr > 0.04 and ndwi > -0.05 and ndvi < 0.25 and (g_rel is None or g_rel > 0.07) and g_out:
                return "green_algae"      # greenish, weak water signal: algae raises NIR
            return "covered_or_empty"
    elif d["water_frac"] < 0.15 and not (gr > 0.04 and hue < 170):
        return "covered_or_empty"
    if g_rel is not None and ref["n"] >= 4:   # enough clean pools nearby this snapshot: judge relative to them
        if g_rel > 0.07 and gr > -0.01 and g_out:
            return "green_algae"
        if g_rel > 0.03 or hue < 180:
            return "turquoise"
        return "clean_blue"
    if gr > 0.03 or hue < 165:
        return "green_algae"
    if gr > -0.04 or hue < 188:
        return "turquoise"
    return "clean_blue"


# ---------------------------------------------------------------- one block
def calib_meta(lon, lat, half):
    """A stand-in block (no LiDAR, no footprints) for calibration runs at any spot; never written anywhere."""
    from pyproj import Transformer
    cx, cy, k, box = block_box_3857(lon, lat, half)
    t = Transformer.from_crs(3857, 4326, always_xy=True)
    W, S_ = t.transform(box[0], box[1]); E, N = t.transform(box[2], box[3])
    return {"origin": [lon, lat], "half_m": half, "bounds": [W, S_, E, N], "point_parts": 0}


def run_block(slug, samples=False, use_s2=True, use_oam=True, dry=False, calib=None):
    d = BLOCKS / slug
    if calib:
        meta, houses, dry = calib_meta(*calib), [], True
    else:
        meta = json.load(open(d / "meta.json", encoding="utf-8"))
        houses = json.load(open(d / "houses.json", encoding="utf-8"))
    lon, lat = meta["origin"]; half = float(meta["half_m"])
    H = half + CTX
    n = int(round(2 * H / R))
    cx, cy, k, box = block_box_3857(lon, lat, H)
    cdir = CACHE / slug; cdir.mkdir(parents=True, exist_ok=True)
    gx = -H + (np.arange(n) + 0.5) * R
    GX, GY = np.meshgrid(gx, gx)
    in_block = (np.abs(GX) <= half) & (np.abs(GY) <= half)

    # buildings (footprints) + LiDAR heights inside the block: roofs and trees are never pool pixels
    polys = [Polygon(h["ring"]) for h in houses]
    BLD = np.zeros((n, n), bool)
    for p in polys:
        x0, y0, x1, y1 = p.bounds
        sl = (slice(max(0, int((y0 + H) / R) - 2), int((y1 + H) / R) + 3), slice(max(0, int((x0 + H) / R) - 2), int((x1 + H) / R) + 3))
        BLD[sl] |= contains_xy(p.buffer(0.6), GX[sl], GY[sl])
    HIGH = np.zeros((n, n), bool); nopts = None
    try:
        pts = load_points(d, meta)
        u = meta.get("point_unit_m", 0.02)
        px, py, hag = pts["x"] * u, pts["y"] * u, pts["h"] / 8.0
        ci = np.clip(((px + H) / R).astype(int), 0, n - 1); cj = np.clip(((py + H) / R).astype(int), 0, n - 1)
        mx = np.zeros(n * n); np.maximum.at(mx, cj * n + ci, np.where(pts["k"] == 0, 0, hag))
        mx = ndimage.grey_closing(mx.reshape(n, n), size=3)
        HIGH = (mx > 1.5) & in_block
        # LiDAR returns per 1 m cell (water often returns no laser pulse: supporting evidence only)
        cnt = np.zeros(n * n); np.add.at(cnt, cj * n + ci, 1)
        cnt = cnt.reshape(n, n)
        nopts = ndimage.uniform_filter(cnt, 2) * 4 < 0.5
    except Exception as e:  # noqa: BLE001
        log("  LiDAR points unavailable:", e)
    block_mask = BLD | HIGH

    t0 = time.time()
    snaps = naip_snapshots(meta, box, n, cdir)
    if use_oam:
        try:
            snaps += oam_snapshots(meta, box, n, cdir)
        except Exception as e:  # noqa: BLE001
            log("  OpenAerialMap failed:", e)
    snaps += catalogue_snapshots(meta, box, n, cdir)
    snaps = [s for s in snaps if s.get("date")]
    snaps.sort(key=lambda s: (s["date"], s["source"]))
    log(f"  {len(snaps)} snapshots ({', '.join(s['date'] + ' ' + s['source'].split()[0] for s in snaps)}) in {time.time() - t0:.0f}s")
    for s in snaps:
        features(s, block_mask)

    # ---------------------------------------------------------------- pool sites (fixed geometry across snapshots)
    count = np.zeros((n, n), int)
    for s in snaps:
        w = ndimage.binary_opening(s["water"], iterations=1)
        lab, nl = ndimage.label(w)
        if nl:
            sz = np.bincount(lab.ravel()) * R * R
            okl = (sz >= 0.6 * MIN_M2) & (sz <= 1.5 * MAX_M2); okl[0] = False
            count += okl[lab]
    lab, nl = ndimage.label(ndimage.binary_closing(count > 0, iterations=1))
    sites = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        m = lab[sl] == i
        px_n = int(m.sum()); area = px_n * R * R
        if area < MIN_M2 * 0.8 or area > MAX_M2 * 1.3:
            continue
        yy, xx = np.nonzero(m)
        pts_ = MultiPoint(np.column_stack([gx[xx + sl[1].start], gx[yy + sl[0].start]]))
        hull = pts_.convex_hull.buffer(R / 2, join_style=2)
        rect = hull.minimum_rotated_rectangle
        e_ = np.array(rect.exterior.coords)
        sides = sorted([np.hypot(*(e_[1] - e_[0])), np.hypot(*(e_[2] - e_[1]))])
        aspect = sides[1] / max(sides[0], 0.1)
        fill = area / max(hull.area, 1)
        if aspect > 5 or fill < 0.6 or sides[0] < 2.2:
            continue
        full = np.zeros((n, n), bool); full[sl] = m
        # consensus core: pixels that were water in at least half as many snapshots as the site's best pixel
        # (NAIP flights shift by 1-3 m, so the union is wider than the pool)
        core = full & (count >= max(1, math.ceil(count[full].max() / 2)))
        if core.sum() > 80:
            ce = ndimage.binary_erosion(core)
            core = ce if ce.sum() >= 30 else core
        c = pts_.centroid
        sites.append({"mask": full, "core": core, "x": c.x, "y": c.y, "area_m2": area, "aspect": aspect, "fill": fill,
                      "hits": int(count[full].max()), "in_block": abs(c.x) <= half + 3 and abs(c.y) <= half + 3})
    log(f"  {len(sites)} pool sites in the block + {CTX:.0f} m context ({sum(s_['in_block'] for s_ in sites)} in the block)")

    # per snapshot stats on the fixed core; drop sites seen as water only once (cars, glare, one bad flight) unless that
    # once is the newest snapshot (a new pool)
    for st in sites:
        st["stats"] = []
        for s in snaps:
            m = st["core"] & s["valid"]
            st["stats"].append(site_stats(s, m) if m.sum() >= 0.6 * st["core"].sum() else None)
        st["water_px"] = [int((s["water"] & st["mask"]).sum()) for s in snaps]
        hit = [i for i, x in enumerate(st["stats"]) if x and x["water_frac"] >= 0.4]
        st["hits"] = len(hit)
        st["keep"] = len(hit) >= 2 or (len(hit) == 1 and hit[0] == len(snaps) - 1)
        wp = [st["water_px"][i] for i in hit]
        st["area_m2"] = float(np.median(wp)) * R * R if wp else st["core"].sum() * R * R
    dropped = sum(not st["keep"] for st in sites)
    sites = [st for st in sites if st["keep"] and MIN_M2 <= st["area_m2"] <= MAX_M2]
    log(f"  kept {len(sites)} sites seen as water on >= 2 snapshots (or new on the newest), {dropped} one-off detections dropped "
        f"({sum(s_['in_block'] for s_ in sites)} in the block)")
    # reference = clean-water sites that snapshot (context included)
    refs = []
    for i, s in enumerate(snaps):
        good = [st["stats"][i] for st in sites if st["stats"][i] and st["stats"][i]["water_frac"] > 0.6]
        basis = "clean water this snapshot"
        if len(good) < 3:  # weak-water flight (old 1 m NAIP): the typical look of all known pool sites that snapshot
            good = [st["stats"][i] for st in sites if st["stats"][i] and st["stats"][i]["bright"] >= DARK]
            basis = "all known pool sites this snapshot"
        refs.append({"greenness": float(np.median([x["greenness"] for x in good])), "hue": float(np.median([x["hue"] or 0 for x in good])),
                     "greenness_q75": float(np.percentile([x["greenness"] for x in good], 75)), "n": len(good), "basis": basis} if len(good) >= (3 if basis.startswith("clean") else 4) else None)
    for st in sites:
        st["states"] = [classify(x, ref) for x, ref in zip(st["stats"], refs)]
    # a snapshot where a large share of the neighbourhood's pools all turn 'green' at once is a colour cast of that flight
    # (seen on AZ NAIP 2013), not algae: those verdicts become 'greenish_uncertain' and do not count as green years
    cast = []
    meds = [r_["greenness"] for r_ in refs if r_]
    ref_med = float(np.median(meds)) if len(meds) >= 3 else None
    for i in range(len(snaps)):
        judged = [st["states"][i] for st in sites if st["states"][i] in ("clean_blue", "turquoise", "green_algae")]
        share = sum(z == "green_algae" for z in judged) / max(len(judged), 1)
        # cast = the whole pool distribution of this flight shifted green (median of the window's pools vs the other
        # flights), or more than half the pools green at once; a green minority on a normal flight stays green_algae
        shift = refs[i]["greenness"] - ref_med if refs[i] and ref_med is not None else 0.0
        cast.append(len(judged) >= 6 and ((share > 0.25 and shift > 0.04) or share > 0.5))
        log(f"    {snaps[i]['date']}: pools judged {len(judged)}, green share {share:.0%}, reference greenness shift {shift:+.3f}")
        if cast[-1]:
            for st in sites:
                if st["states"][i] == "green_algae":
                    st["states"][i] = "greenish_uncertain"
            log(f"  {snaps[i]['date']}: {share:.0%} of {len(judged)} pools look green at once -> treated as a colour cast")
    for st in sites:
        wat = [i for i, z in enumerate(st["states"]) if z in ("clean_blue", "turquoise", "green_algae", "greenish_uncertain")]
        st["first_i"] = wat[0] if wat else None
        for i in range(len(snaps)):  # before the pool first holds water: not built yet (unless clearly covered)
            if st["first_i"] is not None and i < st["first_i"] and st["states"][i] in ("covered_or_empty", "not_present", "green_algae") \
                    and (st["stats"][i] or {}).get("water_frac", 0) < 0.1 and (st["stats"][i] or {}).get("ndvi") not in (None,) \
                    and (st["stats"][i] or {}).get("ndvi", 0) > 0.15:
                st["states"][i] = "not_present"
        if st["first_i"] is not None:   # vegetation where a pool held water before: filled in / removed (or overgrown cover)
            for i in range(st["first_i"] + 1, len(snaps)):
                if st["states"][i] == "not_present":
                    st["states"][i] = "filled_or_removed"
        # confidence: repeated clean water, shape, LiDAR water dropout
        nw = len(wat)
        conf = 0.35 + 0.12 * min(nw, 4) + (0.1 if st["fill"] > 0.8 else 0) + (0.05 if st["aspect"] < 3 else 0)
        if nopts is not None and st["in_block"]:
            st["lidar_no_return_share"] = round(float(nopts[st["mask"]].mean()), 2)
            conf += 0.1 if st["lidar_no_return_share"] > 0.3 else 0
        st["confidence"] = round(min(conf, 0.98), 2)

    # ---------------------------------------------------------------- assign sites to houses
    homes = [i for i, h in enumerate(houses) if not h.get("garage")]
    per_house = {}
    for si, st in enumerate(sites):
        if not st["in_block"]:
            continue
        p = Point(st["x"], st["y"])
        ds = sorted((polys[i].distance(p), i) for i in homes)
        st["house_dist_m"] = round(ds[0][0], 1) if ds else None
        if not ds or ds[0][0] > 20:   # farther: likely the yard of a house missing from the footprint list
            continue
        per_house.setdefault(ds[0][1], []).append(si)
    imagery = [{"date": s["date"], "source": s["source"], "licence": s["licence"], "gsd_m": s["gsd_m"], "nir": s["has_nir"],
                "reference_pools": (refs[i] or {}).get("n", 0), "reference_basis": (refs[i] or {}).get("basis"),
                "colour_cast": cast[i]} for i, s in enumerate(snaps)]
    result = {}
    for hi in homes:
        lst = per_house.get(hi, [])
        if not lst:
            result[hi] = {"present": False, "confidence": None, "area_m2": 0, "first_seen_year": None, "by_year": [], "green_years": [],
                          "last_state": None, "note": f"no pool water seen on {len(snaps)} open aerial snapshots ({snaps[0]['date'][:4]}-{snaps[-1]['date'][:4]})" if snaps else "no imagery"}
            continue
        st = sites[min(lst, key=lambda j: sites[j]["house_dist_m"])]
        by = []
        for i, s in enumerate(snaps):
            x = st["stats"][i]
            by.append({"year": int(s["date"][:4]), "date": s["date"], "source": s["source"], "gsd_m": s["gsd_m"], "state": st["states"][i],
                       "greenness": x["greenness"] if x else None, "greenness_rel": round(x["greenness"] - refs[i]["greenness"], 3) if x and refs[i] else None,
                       "hue": x["hue"] if x else None, "ndwi": x["ndwi"] if x else None, "ndvi": x["ndvi"] if x else None,
                       "water_frac": x["water_frac"] if x else None, "rgb": x["rgb"] if x else None})
        seen = [b for b in by if b["state"] in ("clean_blue", "turquoise", "green_algae", "greenish_uncertain")]
        last = next((b for b in reversed(by) if b["state"] != "no_data"), None)
        green = sorted({b["year"] for b in by if b["state"] == "green_algae"})
        note = []
        if st["first_i"] == 0:
            note.append(f"already there on the first snapshot ({snaps[0]['date'][:4]})")
        if green:
            note.append(f"green/algae look on {len(green)} of {len(seen)} water snapshots")
        if len(lst) > 1:
            note.append(f"{len(lst)} water bodies near this house (nearest reported; others may belong to a neighbour missing from the footprints)")
        note.append("state only on capture dates (~every 2 years), not how often it is cleaned")
        result[hi] = {"present": True, "confidence": st["confidence"], "area_m2": round(st["area_m2"], 1),
                      "first_seen_year": seen[0]["year"] if seen else None, "x": round(st["x"], 1), "y": round(st["y"], 1),
                      "by_year": by, "green_years": green, "last_state": last["state"] if last else None,
                      "house_dist_m": st["house_dist_m"], "lidar_no_return_share": st.get("lidar_no_return_share"), "note": "; ".join(note)}

    # Sentinel-2: neighbourhood-level water signal only (10 m pixels are larger than most pools)
    s2 = None
    if use_s2 and sites:
        try:
            s2 = sentinel2_signal(meta, box, n, cdir, sites)
        except Exception as e:  # noqa: BLE001
            log("  Sentinel-2 failed:", str(e)[:120])

    n_pool = sum(1 for v in result.values() if v["present"])
    summary = {"n_pools": n_pool, "houses": len(homes), "share_houses_with_pool": round(n_pool / max(len(homes), 1), 3),
               "green_now": sum(1 for v in result.values() if v["present"] and v["last_state"] == "green_algae"),
               "green_ever": sum(1 for v in result.values() if v["green_years"]),
               "context_pools": len(sites), "context_m": CTX,
               "in_block_sites": sum(st["in_block"] for st in sites),
               "in_block_sites_unassigned": sum(st["in_block"] for st in sites) - sum(len(v) for v in per_house.values()), "imagery_dates": imagery,
               "method": "NAIP/open orthophoto water pixels (NDWI>0.10, blue>red, not dark, not roof/tree via LiDAR+footprints) -> compact 12-150 m2 "
                         "sites fixed across snapshots -> per-snapshot state from grey-world-balanced colour + NDWI/NDVI, relative to the "
                         "context window's clean pools; see usa/research/pools_method.md",
               "licence": "; ".join(sorted({f"{s['source']}: {s['licence']}" if not s['source'].startswith('USDA') else f"USDA NAIP: {s['licence']}" for s in snaps}
                                           | ({"Sentinel-2 L2A: Copernicus Sentinel data, free licence incl. commercial use"} if s2 else set()))),
               "sentinel2_neighbourhood": s2, "limitations": "snapshots every ~1-2 years on capture dates only; covered/shaded pools and "
                                                              "pools under trees can be missed; colour states are indicative, not inspections",
               "generated": datetime.now(timezone.utc).isoformat(timespec="minutes")}

    log(f"  {slug}: {n_pool}/{len(homes)} houses with a pool, green now {summary['green_now']}, green ever {summary['green_ever']}")
    for hi, v in result.items():
        if v["present"]:
            log(f"    house {hi} {houses[hi].get('address') or ''}: {v['area_m2']} m2 {v['house_dist_m']} m away conf {v['confidence']} first {v['first_seen_year']} "
                f"states {' '.join(str(b['year'])[2:] + ':' + b['state'][:5] for b in v['by_year'])}")
    if samples:
        save_samples(slug, snaps, sites, refs, n, H)
    if dry:
        return summary, result
    with block_lock(d):
        H_ = json.load(open(d / "houses.json", encoding="utf-8"))
        M_ = json.load(open(d / "meta.json", encoding="utf-8"))
        by_id = {houses[hi]["id"]: v for hi, v in result.items()}
        for h in H_:
            h["pool"] = by_id.get(h.get("id"))
        M_["pools"] = summary
        for name, obj in (("houses.json", H_), ("meta.json", M_)):
            tmp = d / (name + ".pools.tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(obj, f, separators=(",", ":"), default=_np_default)
                os.replace(tmp, d / name)
            finally:
                tmp.unlink(missing_ok=True)
    return summary, result


def sentinel2_signal(meta, box, n, cdir, sites):
    """Per summer: least-cloudy Sentinel-2 L2A scene; mean NDWI of 10 m pixels touching known pool sites minus the
    neighbourhood's other ground pixels. A neighbourhood-level 'water present' signal, never a per-pool colour."""
    m_all = np.zeros((n, n), bool)
    for st in sites:
        m_all |= st["mask"]
    f = cdir / f"s2_{int(m_all.sum())}.json"  # keyed by the site set
    if f.exists():
        return json.load(open(f))
    W, S_, E, N = meta["bounds"]
    cat = _stac()
    m = np.zeros((n, n), bool)
    for st in sites:
        m |= st["mask"]
    m10 = ndimage.binary_dilation(m, iterations=6)
    out = []
    for y in range(2017, datetime.now().year + 1):
        its = list(cat.search(collections=["sentinel-2-l2a"], bbox=[W, S_, E, N], datetime=f"{y}-06-01/{y}-09-15",
                              query={"eo:cloud_cover": {"lt": 10}}, max_items=40).items())
        if not its:
            continue
        it = min(its, key=lambda i: i.properties["eo:cloud_cover"])
        g = np.flipud(raster_to_block(it.assets["B03"].href, box, n, margin=0.05)[0])
        ni = np.flipud(raster_to_block(it.assets["B08"].href, box, n, margin=0.05)[0])
        nd = (g - ni) / np.maximum(g + ni, 1)
        out.append({"year": y, "date": it.properties["datetime"][:10], "scene": it.id, "cloud_pct": round(it.properties["eo:cloud_cover"], 1),
                    "ndwi_near_pools": round(float(nd[m10].mean()), 3), "ndwi_elsewhere": round(float(nd[~m10].mean()), 3),
                    "contrast": round(float(nd[m10].mean() - nd[~m10].mean()), 3)})
    res = {"years": out, "resolution_m": 10, "use": "neighbourhood-level only: 10 m pixels mix each pool with its yard"}
    json.dump(res, open(f, "w"))
    return res


def save_samples(slug, snaps, sites, refs, n, H, per_kind=3):
    """RGB crop strips (every snapshot of one site, true colour, site outline) for visual checking."""
    from PIL import Image, ImageDraw
    sd = CACHE / "samples"; sd.mkdir(parents=True, exist_ok=True)
    for old in sd.glob(f"{slug}_*.png"):
        old.unlink()
    # overview: newest NIR snapshot, every site outlined (magenta = in block, cyan = context)
    s = next((s for s in reversed(snaps) if s["has_nir"]), snaps[-1])
    a_ = np.clip(np.nan_to_num(s["arr"][..., :3]), 0, 255).astype(np.uint8).copy()
    for st in sites:
        e = st["mask"] & ~ndimage.binary_erosion(st["mask"])
        a_[e] = (255, 0, 255) if st["in_block"] else (0, 255, 255)
    Image.fromarray(np.flipud(a_)).save(sd / f"{slug}_overview_{s['date'][:4]}.png")
    pick = []
    gsites = [st for st in sites if "green_algae" in st["states"] or "greenish_uncertain" in st["states"]]
    bsites = [st for st in sites if "green_algae" not in st["states"]]
    pick += [("green", st) for st in sorted(gsites, key=lambda s: (-s["in_block"], -s["hits"]))[:per_kind]]
    pick += [("blue", st) for st in sorted(bsites, key=lambda s: (-s["in_block"], -s["hits"]))[:per_kind]]
    for kind, st in pick:
        j, i = int((st["y"] + H) / R), int((st["x"] + H) / R)
        w = 30
        tiles = []
        for s, state in zip(snaps, st["states"]):
            rgb = np.stack([s["arr"][..., c] for c in range(3)], -1)
            crop = rgb[max(0, j - w):j + w, max(0, i - w):i + w]
            crop = np.flipud(np.clip(np.nan_to_num(crop), 0, 255)).astype(np.uint8)
            im = Image.fromarray(crop).resize((crop.shape[1] * 3, crop.shape[0] * 3), Image.NEAREST)
            mk = np.flipud(st["mask"][max(0, j - w):j + w, max(0, i - w):i + w])
            edge = mk & ~ndimage.binary_erosion(mk)
            a_ = np.asarray(im).copy()
            ey, ex = np.nonzero(edge)
            for dy in range(3):
                for dx in range(3):
                    a_[ey * 3 + dy, ex * 3 + dx] = (255, 0, 255)
            im = Image.fromarray(a_)
            dr = ImageDraw.Draw(im)
            dr.rectangle([0, 0, im.width, 12], fill=(0, 0, 0))
            dr.text((2, 0), f"{s['date'][:7]} {state}", fill=(255, 255, 0))
            tiles.append(im)
        strip = Image.new("RGB", (sum(t.width for t in tiles), max(t.height for t in tiles)))
        x0 = 0
        for t in tiles:
            strip.paste(t, (x0, 0)); x0 += t.width
        fn = sd / f"{slug}_{kind}_{int(st['x'])}_{int(st['y'])}.png"
        strip.save(fn)
        log(f"  sample {fn.name}: {' '.join(st['states'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--calib", nargs=3, type=float, metavar=("LON", "LAT", "HALF"),
                    help="calibration run at any spot (no LiDAR/footprints); samples only, writes nothing; needs --slug as a cache name")
    ap.add_argument("--samples", action="store_true")
    ap.add_argument("--no-s2", action="store_true")
    ap.add_argument("--no-oam", action="store_true")
    ap.add_argument("--dry", action="store_true", help="detect + samples, write nothing to the block")
    a = ap.parse_args()
    slugs = a.slug or []
    if a.all:
        slugs = sorted(p.parent.name for p in BLOCKS.glob("*/meta.json"))
    for sl in slugs:
        log(f"== {sl}")
        try:
            run_block(sl, samples=a.samples, use_s2=not a.no_s2, use_oam=not a.no_oam, dry=a.dry, calib=a.calib)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            log(f"  {sl} failed: {e}")


if __name__ == "__main__":
    main()

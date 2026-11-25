"""Lane I1 - free, commercial-use-OK imagery for every 3D block (satellite, aerial, drone, post-disaster).

    .venv\\Scripts\\python -u -W ignore -m usa.imagery [slug ...] [--no-series] [--sources-only]

Per block it writes NEW jpgs under web/usa/blocks/<slug>/sat/ and meta.imagery (+ appends sat/* to meta.files) inside
usa.blocklock.block_lock. It never touches img/<year>.jpg or h.* fields. Catalogue -> web/usa/data/imagery_sources.json.
Method + licence evidence -> usa/research/imagery.md. Cache -> usa/_cache/imagery/.

Sources (all keyless):
  * Sentinel-2 L2A, Landsat C2 L2, HLS S30/L30, Sentinel-1 RTC, NAIP  - Microsoft Planetary Computer STAC (anonymous SAS)
  * state orthos: MnGeo WMS (met25, public domain), TxGIO ImageServer (CC0), Colorado DRAPP 2022 on AWS (CC0)
  * NOAA NGS Emergency Response Imagery (public domain; per-layer attribution filter drops MAXAR (CC BY-NC) and CAP)
  * OpenAerialMap catalogue (per-image licence; keep CC BY / CC BY-SA / CC0 / public domain only)
"""
import io
import json
import math
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "imagery"
CACHE.mkdir(parents=True, exist_ok=True)
SOURCES_OUT = ROOT / "web" / "usa" / "data" / "imagery_sources.json"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
UA = "roofprint-usa/0.1 (open-data research)"
S = requests.Session()
S.headers["User-Agent"] = UA
TODAY = datetime.now(timezone.utc).date()
MAX_JPG = 1_450_000

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")

# ------------------------------------------------------------------ licences (quotes < 15 words, verified pages)
LIC = {
    "copernicus": {"licence": "Copernicus Sentinel data legal notice (EU Reg. 1159/2013): free, full and open; "
                              "reproduction/distribution/modification allowed, no commercial restriction; "
                              "credit 'Contains modified Copernicus Sentinel data <year>'",
                   "quote": "users shall have a free, full and open access to Copernicus Sentinel Data",
                   "url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice"},
    "usgs": {"licence": "USGS Landsat: no restrictions (US Government public domain)",
             "quote": "There are no restrictions on Landsat data downloaded from the USGS",
             "url": "https://www.usgs.gov/faqs/are-there-any-restrictions-use-or-redistribution-landsat-data"},
    "hls": {"licence": "CC0 (NASA ESDIS default for NASA-led mission data)",
            "quote": "data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)",
            "url": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance"},
    "s1rtc": {"licence": "CC BY 4.0 (Planetary Computer Sentinel-1 RTC; contains modified Copernicus Sentinel data)",
              "quote": "STAC collection license: CC-BY-4.0",
              "url": "https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc"},
    "naip": {"licence": "Public domain with attribution (USDA FSA NAIP)",
             "quote": "Public Domain with Attribution",
             "url": "https://registry.opendata.aws/naip/"},
    "mngeo": {"licence": "Public domain (Minnesota Government Data Practices Act)",
              "quote": "This dataset is public domain under the Minnesota Government Data Practices Act",
              "url": "https://mnatlas.org/metadata/2025_7_county.html"},
    "txgio": {"licence": "CC0 1.0 (TxGIO DataHub collection licence)",
              "quote": "Creative Commons Zero v1.0 Universal",
              "url": "https://api.tnris.org/api/v1/collections"},
    "drapp": {"licence": "CC0 1.0 (State of Colorado public imagery, AWS Open Data registry)",
              "quote": "License: CC0 1.0",
              "url": "https://registry.opendata.aws/colorado-imagery/"},
    "noaa": {"licence": "US Government public domain (NOAA NGS Emergency Response Imagery)",
             "quote": "open to the public and can be used as desired",
             "url": "https://registry.opendata.aws/noaa-eri/"},
}
# owner rule (stricter, 2026-11-24): only explicit public domain / CC0 / CC BY / CC BY-SA / Copernicus open policy.
# OpenAerialMap: the image itself must carry a CC licence (CC0, CC BY, CC BY-SA); anything else -> rejected.
OAM_OK = re.compile(r"^cc[- ]?(0|zero)\b.*$|^cc[- ]?by([- ]?sa)?([- ]?\d(\.\d)?)?$", re.I)
TERMS_NOT_EXPLICIT = "terms not explicit"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(url, tries=3, **kw):
    kw.setdefault("timeout", 90)
    last = None
    for i in range(tries):
        try:
            r = S.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {url}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def cached_json(name, fn, max_age_h=20):
    p = CACHE / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return json.loads(p.read_text())
    v = fn()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, default=str))
    return v


# ------------------------------------------------------------------ geometry
def buffer_bbox(b, m):
    lon0, lat0, lon1, lat1 = b
    lat = (lat0 + lat1) / 2
    dlat = m / 111_320.0
    dlon = m / (111_320.0 * math.cos(math.radians(lat)))
    return [lon0 - dlon, lat0 - dlat, lon1 + dlon, lat1 + dlat]


def bbox_intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def bbox_contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def save_jpg(img, path, q=88):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = img.convert("RGB")
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() < MAX_JPG or q <= 40:
            break
        q -= 8
    tmp = path.with_suffix(".tmp.jpg")
    tmp.write_bytes(buf.getvalue())
    os.replace(tmp, path)
    return buf.tell()


def outline(img, bbox_img, bbox_blk, colour=(255, 60, 60)):
    """draw the 3D block footprint (bbox_blk) on an image covering bbox_img (both lon/lat)."""
    w, h = img.size
    x0 = (bbox_blk[0] - bbox_img[0]) / (bbox_img[2] - bbox_img[0]) * w
    x1 = (bbox_blk[2] - bbox_img[0]) / (bbox_img[2] - bbox_img[0]) * w
    y0 = (bbox_img[3] - bbox_blk[3]) / (bbox_img[3] - bbox_img[1]) * h
    y1 = (bbox_img[3] - bbox_blk[1]) / (bbox_img[3] - bbox_img[1]) * h
    d = ImageDraw.Draw(img)
    d.rectangle([x0, y0, x1, y1], outline=colour, width=max(1, w // 300))
    return img


def blank(img):
    a = np.asarray(img.convert("L"), dtype=np.float32)
    return a.std() < 3 or (a > 250).mean() > 0.9 or (a < 5).mean() > 0.9


# ------------------------------------------------------------------ Planetary Computer STAC
_pc = None


def pc():
    global _pc
    if _pc is None:
        import planetary_computer
        import pystac_client
        _pc = pystac_client.Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)
    return _pc


def stac_search(col, bbox, dt, query=None, limit=1000):
    s = pc().search(collections=[col], bbox=bbox, datetime=dt, query=query, max_items=limit,
                    sortby=[{"field": "properties.datetime", "direction": "desc"}])
    return list(s.items())


def revisit(dates):
    d = sorted(set(dates))
    if len(d) < 2:
        return None
    gaps = [(d[i + 1] - d[i]).days for i in range(len(d) - 1)]
    return float(np.median(gaps))


def stac_meta(slug, bbox):
    """scene-level metadata for S2 / Landsat / HLS / S1 / NAIP (cached)."""
    def run():
        out = {}
        yr = f"{(TODAY - timedelta(days=365)).isoformat()}/{TODAY.isoformat()}"
        for col in ("sentinel-2-l2a", "landsat-c2-l2", "hls2-s30", "hls2-l30", "sentinel-1-rtc"):
            try:
                its = stac_search(col, bbox, yr)
            except Exception as e:  # noqa: BLE001
                out[col] = {"error": str(e)[:200]}
                continue
            if col == "landsat-c2-l2":
                its = [i for i in its if i.properties.get("platform", "").lower() in ("landsat-8", "landsat-9")]
            ds = [i.datetime.date() for i in its]
            cc = [(i.datetime.date(), i.properties.get("eo:cloud_cover")) for i in its]
            low = [d for d, c in cc if c is not None and c <= 20]
            out[col] = {"scenes_12mo": len(set(ds)), "latest": max(ds).isoformat() if ds else None,
                        "latest_lowcloud_scene": max(low).isoformat() if low else None,
                        "median_revisit_days": revisit(ds),
                        "median_revisit_lowcloud_days": revisit(low),
                        "platforms": sorted({i.properties.get("platform", "") for i in its}),
                        "dates": sorted({d.isoformat() for d in ds})}
        try:
            its = stac_search("naip", bbox, None)
            yrs = {}
            for i in its:
                y = i.datetime.year
                yrs.setdefault(y, {"year": y, "gsd_m": i.properties.get("gsd"), "items": 0, "state": i.properties.get("naip:state")})
                yrs[y]["items"] += 1
            out["naip"] = {"years": [yrs[y] for y in sorted(yrs)]}
        except Exception as e:  # noqa: BLE001
            out["naip"] = {"error": str(e)[:200]}
        return out
    return cached_json(f"{slug}/stac_meta.json", run)


# ------------------------------------------------------------------ Sentinel-2 pixels
S2_BANDS = ("B02", "B03", "B04", "B08", "SCL")
BAD_SCL = {0, 1, 3, 8, 9, 10}


def s2_read(item, aoi):
    """read B02/B03/B04/B08 (10 m) + SCL (20 m->10 m) over aoi; return dict of float32 reflectance + scl."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    out = {}
    pb = item.properties.get("s2:processing_baseline") or "00.00"
    try:
        off = 1000 if float(pb) >= 4.0 else 0
    except ValueError:
        off = 0
    ref = None
    with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES"):
        for b in S2_BANDS:
            href = item.assets[b].href
            with rasterio.open(href) as ds:
                bb = transform_bounds("EPSG:4326", ds.crs, *aoi)
                if ref is None:
                    w = from_bounds(*bb, transform=ds.transform)
                    res = ds.res[0]
                    ref = (bb, max(8, int(round((bb[2] - bb[0]) / 10))), max(8, int(round((bb[3] - bb[1]) / 10))))
                w = from_bounds(*ref[0], transform=ds.transform)
                a = ds.read(1, window=w, out_shape=(ref[2], ref[1]), boundless=True, fill_value=0,
                            resampling=rasterio.enums.Resampling.nearest)
            if b == "SCL":
                out[b] = a.astype(np.uint8)
            else:
                out[b] = np.where(a == 0, np.nan, (a.astype(np.float32) - off) / 10000.0)
    return out


def s2_stats(px):
    scl = px["SCL"]
    good = ~np.isin(scl, list(BAD_SCL)) & np.isfinite(px["B04"])
    frac = float(good.mean())
    if good.sum() < 10:
        return frac, None, None
    r, g, b, n = (px[k][good] for k in ("B04", "B03", "B02", "B08"))
    ndvi = (n - r) / np.maximum(n + r, 1e-4)
    return frac, float(np.nanmean(ndvi)), float(np.nanmean((r + g + b) / 3))


def s2_dedupe(items):
    """one item per acquisition date, keep the lowest cloud tile."""
    best = {}
    for i in items:
        d = i.datetime.date()
        if d not in best or (i.properties.get("eo:cloud_cover") or 100) < (best[d].properties.get("eo:cloud_cover") or 100):
            best[d] = i
    return [best[d] for d in sorted(best, reverse=True)]


def ndvi_rgb(nd):
    stops = np.array([[-0.2, 120, 90, 60], [0.1, 200, 180, 130], [0.3, 230, 220, 90], [0.5, 120, 190, 70], [0.8, 20, 110, 30]])
    v = np.clip(np.nan_to_num(nd, nan=-0.2), -0.2, 0.8)
    rgb = np.stack([np.interp(v, stops[:, 0], stops[:, k]) for k in (1, 2, 3)], -1)
    return Image.fromarray(rgb.astype(np.uint8))


def s2_images(px, aoi, blk, sat, tag):
    r, g, b, n = (np.nan_to_num(px[k], nan=0) for k in ("B04", "B03", "B02", "B08"))
    rgb = np.stack([r, g, b], -1)
    rgb = np.clip((rgb / 0.25) ** (1 / 1.6), 0, 1) * 255
    up = 640
    im = Image.fromarray(rgb.astype(np.uint8)).resize((up, int(up * rgb.shape[0] / rgb.shape[1])), Image.LANCZOS)
    f1 = sat / f"s2_{tag}_rgb.jpg"
    save_jpg(outline(im, aoi, blk), f1)
    nd = (n - r) / np.maximum(n + r, 1e-4)
    im2 = ndvi_rgb(nd).resize(im.size, Image.NEAREST)
    f2 = sat / f"s2_{tag}_ndvi.jpg"
    save_jpg(outline(im2, aoi, blk, (255, 0, 255)), f2)
    return f1, f2


def sentinel2(slug, blk, sat, do_series=True):
    aoi = buffer_bbox(blk, 300)
    start = date(TODAY.year - 2, TODAY.month, 1)
    dt = f"{start.isoformat()}/{TODAY.isoformat()}"

    def search():
        its = stac_search("sentinel-2-l2a", aoi, dt, query={"eo:cloud_cover": {"lt": 70}})
        return [i.to_dict() for i in its]
    import pystac
    import planetary_computer
    raw = cached_json(f"{slug}/s2_items.json", search)
    items = s2_dedupe([planetary_computer.sign(pystac.Item.from_dict(d)) for d in raw])
    res = {"latest_clear": None, "series": [], "scenes_checked": 0}
    # latest clear-sky scene over the block (+300 m), by the SCL mask
    cand = sorted(items, key=lambda i: i.datetime, reverse=True)
    for it in cand[:25]:
        if (it.properties.get("eo:cloud_cover") or 100) > 60:
            continue
        try:
            px = s2_read(it, aoi)
        except Exception as e:  # noqa: BLE001
            log(slug, "s2 read fail", it.id, str(e)[:120])
            continue
        res["scenes_checked"] += 1
        frac, nd, br = s2_stats(px)
        if frac >= 0.97:
            tag = it.datetime.date().isoformat()
            f1, f2 = s2_images(px, aoi, blk, sat, tag)
            res["latest_clear"] = {"date": tag, "item": it.id, "clear_frac": round(frac, 3), "ndvi": round(nd, 3),
                                   "brightness": round(br, 4), "files": [f1, f2],
                                   "scene_cloud_pct": it.properties.get("eo:cloud_cover")}
            break
    if not do_series:
        return res
    # 24-month monthly series (best clear scene per month)
    months = {}
    for it in items:
        months.setdefault(it.datetime.strftime("%Y-%m"), []).append(it)

    def month_val(m):
        cands = sorted(months[m], key=lambda i: i.properties.get("eo:cloud_cover") or 100)[:4]
        best = None
        for it in cands:
            try:
                frac, nd, br = s2_stats(s2_read(it, aoi))
            except Exception:  # noqa: BLE001
                continue
            if nd is None:
                continue
            if best is None or frac > best[0]:
                best = (frac, nd, br, it.datetime.date().isoformat())
            if frac >= 0.9:
                break
        if not best or best[0] < 0.6:
            return {"month": m, "ndvi": None, "brightness": None, "date": best[3] if best else None,
                    "clear_frac": round(best[0], 3) if best else 0}
        return {"month": m, "ndvi": round(best[1], 4), "brightness": round(best[2], 4), "date": best[3],
                "clear_frac": round(best[0], 3)}

    def series():
        allm = []
        y, mo = start.year, start.month
        while (y, mo) <= (TODAY.year, TODAY.month):
            allm.append(f"{y:04d}-{mo:02d}")
            mo += 1
            if mo == 13:
                y, mo = y + 1, 1
        allm = allm[-24:]
        with ThreadPoolExecutor(6) as ex:
            vals = list(ex.map(lambda m: month_val(m) if m in months else
                               {"month": m, "ndvi": None, "brightness": None, "date": None, "clear_frac": 0}, allm))
        return vals
    res["series"] = cached_json(f"{slug}/s2_series.json", series, max_age_h=72)
    return res


def series_trend(series):
    """year-over-year change of matched months (avoids seasonality)."""
    by = {s["month"]: s for s in series if s.get("ndvi") is not None}
    dn, db = [], []
    for m, s in by.items():
        y, mo = m.split("-")
        prev = by.get(f"{int(y) - 1}-{mo}")
        if prev:
            dn.append(s["ndvi"] - prev["ndvi"])
            db.append(s["brightness"] - prev["brightness"])
    if not dn:
        return None
    mn, mb = float(np.mean(dn)), float(np.mean(db))
    return {"pairs": len(dn), "ndvi_yoy": round(mn, 4), "brightness_yoy": round(mb, 4),
            "read": ("greening" if mn > 0.03 else "browning" if mn < -0.03 else "stable vegetation") + ", " +
                    ("brightening (dust/bare soil/new roofs or paving)" if mb > 0.01 else
                     "darkening" if mb < -0.01 else "stable brightness")}


# ------------------------------------------------------------------ XYZ tile mosaics (NOAA ERI, OpenAerialMap)
def lonlat_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tile_mosaic(tmpl, bbox, z, max_tiles=64):
    x0, y1 = lonlat_px(bbox[0], bbox[1], z)
    x1, y0 = lonlat_px(bbox[2], bbox[3], z)
    tx0, tx1, ty0, ty1 = int(x0 // 256), int(x1 // 256), int(y0 // 256), int(y1 // 256)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > max_tiles:
        return None
    canvas = Image.new("RGBA", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    got = 0

    def fetch(t):
        tx, ty = t
        try:
            r = get(tmpl.format(z=z, x=tx, y=ty), tries=2, timeout=40)
            if r.status_code == 200 and len(r.content) > 200 and "clear.png" not in r.url:
                return t, Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception:  # noqa: BLE001
            pass
        return t, None
    with ThreadPoolExecutor(8) as ex:
        for (tx, ty), im in ex.map(fetch, [(a, b) for a in range(tx0, tx1 + 1) for b in range(ty0, ty1 + 1)]):
            if im is not None:
                canvas.paste(im, ((tx - tx0) * 256, (ty - ty0) * 256))
                got += 1
    if not got:
        return None
    crop = canvas.crop((int(x0 - tx0 * 256), int(y0 - ty0 * 256), int(x1 - tx0 * 256), int(y1 - ty0 * 256)))
    alpha = np.asarray(crop)[..., 3]
    if (alpha > 0).mean() < 0.5:
        return None
    bg = Image.new("RGB", crop.size, (255, 255, 255))
    bg.paste(crop, mask=crop.split()[3])
    return bg, float((alpha > 0).mean())


# ------------------------------------------------------------------ NOAA NGS Emergency Response Imagery
def noaa_layers():
    def run():
        html = get("https://storms.ngs.noaa.gov/").text
        evs = sorted(set(re.findall(r"https://storms\.ngs\.noaa\.gov/storms/([A-Za-z0-9_]+)/index\.html", html)))
        layers, events = [], []
        for ev in evs:
            u = f"https://storms.ngs.noaa.gov/storms/{ev}/services/index.json"
            try:
                r = get(u, tries=2, timeout=60)
                d = r.json() if r.status_code == 200 else None
            except Exception:  # noqa: BLE001
                d = None
            legacy = False
            if d is None:  # older viewers (Leaflet app.js) hard-code their layer list, no bounds
                try:
                    page = get(f"https://storms.ngs.noaa.gov/storms/{ev}/index.html", timeout=60).text
                    js = re.findall(r'src="(js/app[^"]*\.js)"', page)
                    src = get(f"https://storms.ngs.noaa.gov/storms/{ev}/{js[0]}", timeout=60).text if js else ""
                    ids = sorted(set(re.findall(r"stormscdn\.ngs\.noaa\.gov/([0-9a-z]+-rgb)/\{z\}", src)))
                    d = [{"name": i.replace("-rgb", "_RGB"), "attribution": "NOAA", "description": ev, "maxzoom": 19,
                          "bounds": None, "legacy": True,
                          "tiles": [f"https://stormscdn.ngs.noaa.gov/{i}/{{z}}/{{x}}/{{y}}"]} for i in ids] or None
                    legacy = True
                except Exception:  # noqa: BLE001
                    d = None
            events.append({"event": ev, "index": u, "ok": d is not None, "legacy_app_js": legacy, "layers": len(d) if d else 0})
            for L in d or []:
                L["event"] = ev
                layers.append(L)
        return {"events": events, "layers": layers}
    return cached_json("noaa_eri_layers.json", run)


_TILE_HIT = {}


def tile_has_data(tmpl, z, x, y):
    k = (tmpl, z, x, y)
    if k not in _TILE_HIT:
        try:
            r = get(tmpl.format(z=z, x=x, y=y), tries=2, timeout=40)
            _TILE_HIT[k] = r.status_code == 200 and len(r.content) > 200 and "clear.png" not in r.url
        except Exception:  # noqa: BLE001
            _TILE_HIT[k] = False
    return _TILE_HIT[k]


def legacy_bounds(L, blk, near):
    """legacy layers have no bounds: probe z8 tiles over the 25 km box, then the block tile at z16."""
    t = L["tiles"][0]
    z = 8
    x0, y1 = (int(v // 256) for v in lonlat_px(near[0], near[1], z))
    x1, y0 = (int(v // 256) for v in lonlat_px(near[2], near[3], z))
    hit = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if tile_has_data(t, z, x, y)]
    if not hit:
        return None
    cx, cy = lonlat_px((blk[0] + blk[2]) / 2, (blk[1] + blk[3]) / 2, 16)
    return blk if tile_has_data(t, 16, int(cx // 256), int(cy // 256)) else near


def noaa_for_block(blk, sat, NO):
    near = buffer_bbox(blk, 25_000)
    out, rejected = [], []
    legacy = [L for L in NO["layers"] if L.get("legacy")]
    with ThreadPoolExecutor(12) as ex:
        lb = dict(zip([L["name"] for L in legacy], ex.map(lambda L: legacy_bounds(L, blk, near), legacy)))
    for L in NO["layers"]:
        b = L.get("bounds") if not L.get("legacy") else lb.get(L["name"])
        if not b or not bbox_intersects(b, near):
            continue
        attr = (L.get("attribution") or "").strip().upper()
        m = re.match(r"(\d{8})", L.get("name", ""))
        d = datetime.strptime(m.group(1), "%Y%m%d").date().isoformat() if m else None
        rec = {"event": L["event"], "layer": L.get("name"), "description": L.get("description") or L["event"],
               "date": d, "attribution": attr, "bounds": None if L.get("legacy") else b,
               "covers_block": (b is blk) if L.get("legacy") else bbox_contains(b, blk),
               "within_25km": True,
               "tiles": (L.get("tiles") or [None])[0]}
        if attr not in ("NOAA", "USGS", ""):
            rec["rejected"] = ("Maxar Open Data = CC BY-NC 4.0 (non-commercial)" if "MAXAR" in attr
                               else f"{TERMS_NOT_EXPLICIT}: attribution {attr}, no licence stated for this layer")
            rejected.append(rec)
            continue
        rec["licence"] = LIC["noaa"]["licence"]
        rec["licence_url"] = LIC["noaa"]["url"]
        if rec["covers_block"] and rec["tiles"]:
            clip = buffer_bbox(blk, 50)
            for z in (19, 18, 17):
                if z > int(L.get("maxzoom", 19)):
                    continue
                try:
                    mo = tile_mosaic(rec["tiles"], clip, z)
                except Exception:  # noqa: BLE001
                    mo = None
                if mo and not blank(mo[0]):
                    im = mo[0]
                    if im.width > 1024:
                        im = im.resize((1024, int(1024 * im.height / im.width)), Image.LANCZOS)
                    f = sat / f"noaa_{rec['layer']}.jpg"
                    save_jpg(outline(im, clip, blk), f)
                    rec["file"] = f
                    rec["zoom"] = z
                    rec["coverage_frac"] = round(mo[1], 3)
                    break
            if "file" not in rec:
                rec["note"] = "bounding box covers block but no imagery tiles at block (strip gap)"
        out.append(rec)
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out, rejected


# ------------------------------------------------------------------ OpenAerialMap
def oam_for_block(slug, blk, sat):
    near = buffer_bbox(blk, 25_000)

    def run():
        res, page = [], 1
        while page < 20:
            r = get("https://api.openaerialmap.org/meta", params={"bbox": ",".join(f"{v:.5f}" for v in near),
                                                                 "limit": 100, "page": page}, timeout=60).json()
            res += r.get("results", [])
            if len(res) >= r.get("meta", {}).get("found", 0) or not r.get("results"):
                break
            page += 1
        return res
    found = cached_json(f"{slug}/oam.json", run)
    keep, rej = [], []
    cx, cy = (blk[0] + blk[2]) / 2, (blk[1] + blk[3]) / 2

    def dist_km(x):
        b = (x.get("geojson") or {}).get("bbox") or x.get("bbox") or [cx, cy, cx, cy]
        dx = max(b[0] - cx, 0, cx - b[2]) * 111.32 * math.cos(math.radians(cy))
        dy = max(b[1] - cy, 0, cy - b[3]) * 111.32
        return math.hypot(dx, dy)
    found = sorted(found, key=lambda x: (dist_km(x), -(int((x.get("acquisition_end") or "0000")[:4]))))
    clips = 0
    for x in found:
        lic = ((x.get("properties") or {}).get("license") or x.get("license") or "").strip()
        b = (x.get("geojson") or {}).get("bbox") or x.get("bbox")
        rec = {"id": x.get("_id"), "title": x.get("title"), "provider": x.get("provider"), "platform": x.get("platform"),
               "date": (x.get("acquisition_end") or x.get("acquisition_start") or "")[:10], "gsd_m": x.get("gsd"),
               "licence": lic or "unknown", "bounds": b, "covers_block": bool(b and bbox_intersects(b, blk)),
               "tms": (x.get("properties") or {}).get("tms"), "distance_km": round(dist_km(x), 2),
               "page": f"https://map.openaerialmap.org/#/{x.get('_id')}" if x.get("_id") else None}
        if not lic or not OAM_OK.match(lic.replace("_", " ").strip()):
            rec["rejected"] = f"{TERMS_NOT_EXPLICIT}: image licence '{lic or 'unknown'}' is not CC0 / CC BY / CC BY-SA"
            rej.append(rec)
            continue
        if rec["covers_block"] and rec["tms"] and clips < 3:
            clips += 1
            clip = buffer_bbox(blk, 50)
            gsd = rec["gsd_m"] or 0.3
            zmax = min(21, int(math.log2(156543 * math.cos(math.radians(blk[1])) / max(gsd, 0.05))))
            for z in (zmax, zmax - 1, zmax - 2):
                try:
                    mo = tile_mosaic(rec["tms"].replace("{z}", "{z}"), clip, z)
                except Exception:  # noqa: BLE001
                    mo = None
                if mo and not blank(mo[0]):
                    im = mo[0]
                    if im.width > 1024:
                        im = im.resize((1024, int(1024 * im.height / im.width)), Image.LANCZOS)
                    f = sat / f"oam_{rec['id']}.jpg"
                    save_jpg(outline(im, clip, blk), f)
                    rec["file"] = f
                    break
        keep.append(rec)
    n_keep, n_rej = len(keep), len(rej)
    keep = [r for r in keep if r["covers_block"]] + [r for r in keep if not r["covers_block"]][:10]
    for r in keep:
        r.pop("bounds", None)
    return keep, rej[:10], {"open_within_25km": n_keep, "rejected_within_25km": n_rej,
                            "covering_block": sum(1 for r in keep if r["covers_block"])}


# ------------------------------------------------------------------ state / regional orthos
STATE_ORTHOS = {
    # slug -> list of services to export (newest first); only commercial_ok == True services are listed here
    "minneapolis-mn": ["mn_met25"], "minneapolis-oz": ["mn_met25"], "saint-paul-mn": ["mn_met25"],
    "austin-tx": ["tx_stratmap20"], "dallas-tx": ["tx_naip22"],
    "denver-co": ["co_drapp22"], "boulder-co": ["co_drapp22"],
}
ORTHO = {
    "mn_met25": {"name": "Metropolitan Council / MnGeo Twin Cities 7-county spring leaf-off orthos", "year": 2025,
                 "date": "2025-04", "resolution_m": 0.3, "lic": "mngeo", "kind": "wms",
                 "url": "https://imageserver.gisdata.mn.gov/cgi-bin/wms", "layer": "met25"},
    "tx_stratmap20": {"name": "TxGIO StratMap 2020 Capital Area leaf-off orthos", "year": 2020, "date": "2020",
                      "resolution_m": 0.076, "lic": "txgio", "kind": "arcgis_image",
                      "url": "https://imagery.geographic.texas.gov/server/rest/services/StratMap/StratMap20_NCCIR_CapArea_McLennan/ImageServer"},
    "tx_naip22": {"name": "NAIP 2022 Texas 60 cm (TxGIO ImageServer)", "year": 2022, "date": "2022",
                  "resolution_m": 0.6, "lic": "txgio", "kind": "arcgis_image",
                  "url": "https://imagery.geographic.texas.gov/server/rest/services/NAIP/NAIP22_NCCIR_60cm/ImageServer"},
    "co_drapp22": {"name": "DRAPP 2022 Denver regional leaf-off orthos (State of Colorado on AWS)", "year": 2022,
                   "date": "2022", "resolution_m": 0.076, "lic": "drapp", "kind": "s3_tiff",
                   "index": "https://gis.colorado.gov/public/rest/services/OIT/Public_Imagery_Layer_Index/MapServer/6/query"},
}


def to3857(lon, lat):
    return lon * 20037508.34 / 180, math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137


def ortho_wms(svc, clip, px=1024):
    p = {"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": svc["layer"], "STYLES": "",
         "SRS": "EPSG:4326", "BBOX": ",".join(f"{v:.7f}" for v in clip), "WIDTH": px, "HEIGHT": px, "FORMAT": "image/jpeg"}
    r = get(svc["url"], params=p, timeout=120)
    if not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(r.text[:200])
    return Image.open(io.BytesIO(r.content))


def ortho_arcgis(svc, clip, px=1024):
    a, b = to3857(clip[0], clip[1]), to3857(clip[2], clip[3])
    p = {"bbox": f"{a[0]},{a[1]},{b[0]},{b[1]}", "bboxSR": 3857, "imageSR": 3857, "size": f"{px},{px}",
         "bandIds": "0,1,2", "format": "jpg", "f": "image"}
    r = get(svc["url"] + "/exportImage", params=p, timeout=120)
    if not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(r.text[:200])
    return Image.open(io.BytesIO(r.content))


class RangeTiff:
    """minimal uncompressed, chunky, strip-organised GeoTIFF reader over HTTP ranges (DRAPP tiles are ~450 MB)."""
    def __init__(self, url):
        self.url = url
        h = self.rng(0, 16)
        assert h[:2] == b"II"
        ifd = struct.unpack("<I", h[4:8])[0]
        n = struct.unpack("<H", self.rng(ifd, 2))[0]
        d = self.rng(ifd + 2, n * 12)
        self.tags = {}
        for i in range(n):
            t, ty, cnt, v = struct.unpack("<HHII", d[i * 12:i * 12 + 12])
            self.tags[t] = (ty, cnt, v, d[i * 12 + 8:i * 12 + 12])
        self.W, self.H = self.val(256), self.val(257)
        self.spp = self.val(277)
        assert self.val(259) in (1, None), "compressed"
        sc = self.arr(33550, "d")
        tp = self.arr(33922, "d")
        self.x0, self.y0, self.dx, self.dy = tp[3] - tp[0] * sc[0], tp[4] + tp[1] * sc[1], sc[0], sc[1]
        self.offs = self.arr(273, "I" if self.tags[273][0] == 4 else "Q")
        self.rps = self.val(278) or 1

    def rng(self, a, n):
        r = get(self.url, headers={"Range": f"bytes={a}-{a + n - 1}"}, timeout=60)
        return r.content

    def val(self, t):
        if t not in self.tags:
            return None
        ty, cnt, v, raw = self.tags[t]
        return struct.unpack("<H", raw[:2])[0] if ty == 3 else v

    def arr(self, t, f):
        ty, cnt, v, raw = self.tags[t]
        sz = struct.calcsize(f)
        data = raw if cnt * sz <= 4 else self.rng(v, cnt * sz)
        return struct.unpack("<" + f * cnt, data[:cnt * sz])

    def window(self, X0, Y0, X1, Y1, step):
        c0, c1 = max(0, int((X0 - self.x0) / self.dx)), min(self.W, int((X1 - self.x0) / self.dx))
        r0, r1 = max(0, int((self.y0 - Y1) / self.dy)), min(self.H, int((self.y0 - Y0) / self.dy))
        if c1 <= c0 or r1 <= r0:
            return None, None
        rows = list(range(r0, r1, step))
        bpp = self.spp

        def row(r):
            s = r // self.rps
            a = self.offs[s] + ((r % self.rps) * self.W + c0) * bpp
            b = self.rng(a, (c1 - c0) * bpp)
            return np.frombuffer(b, np.uint8).reshape(-1, bpp)[::step, :3]
        with ThreadPoolExecutor(16) as ex:
            arr = np.stack(list(ex.map(row, rows)))
        return arr, (c0, r0, c1, r1)


def ortho_drapp(svc, clip, step=3):
    from rasterio.warp import transform
    p = {"geometry": ",".join(f"{v:.6f}" for v in clip), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "false", "f": "json"}
    feats = get(svc["index"], params=p, timeout=60).json().get("features", [])
    urls = []
    for f in feats:
        for v in f["attributes"].values():
            if isinstance(v, str) and v.endswith(".tif") and "DRAPP2022" in v:
                urls.append(v if v.startswith("http") else "https://colorado-public-imagery.s3.us-west-2.amazonaws.com/" + v.lstrip("/"))
    if not urls:
        raise RuntimeError("no DRAPP 2022 tile for block")
    xs, ys = transform("EPSG:4326", "EPSG:6428", [clip[0], clip[2], clip[0], clip[2]], [clip[1], clip[1], clip[3], clip[3]])
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    tif0 = RangeTiff(urls[0])
    sx = tif0.dx * step
    Wt, Ht = int((X1 - X0) / sx), int((Y1 - Y0) / sx)
    canvas = np.full((Ht, Wt, 3), 255, np.uint8)
    for u in dict.fromkeys(urls):
        t = tif0 if u == urls[0] else RangeTiff(u)
        a, win = t.window(X0, Y0, X1, Y1, step)
        if a is None:
            continue
        ox = int(round((t.x0 + win[0] * t.dx - X0) / sx))
        oy = int(round((Y1 - (t.y0 - win[1] * t.dy)) / sx))
        h, w = min(a.shape[0], Ht - oy), min(a.shape[1], Wt - ox)
        if h > 0 and w > 0:
            canvas[max(0, oy):oy + h, max(0, ox):ox + w] = a[max(0, -oy):h, max(0, -ox):w]
    return Image.fromarray(canvas), urls


def state_orthos(slug, blk, sat):
    out = []
    clip = buffer_bbox(blk, 50)
    for key in STATE_ORTHOS.get(slug, []):
        svc = ORTHO[key]
        rec = {"source": svc["name"], "service": svc.get("url") or svc.get("index"), "date": svc["date"],
               "year": svc["year"], "resolution_m": svc["resolution_m"], "licence": LIC[svc["lic"]]["licence"],
               "licence_url": LIC[svc["lic"]]["url"]}
        f = sat / f"{key}.jpg"
        try:
            if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
                rec["file"] = f
            else:
                extra = None
                if svc["kind"] == "wms":
                    im = ortho_wms(svc, clip)
                elif svc["kind"] == "arcgis_image":
                    im = ortho_arcgis(svc, clip)
                else:
                    im, extra = ortho_drapp(svc, clip)
                    rec["tiles"] = extra
                if blank(im):
                    raise RuntimeError("blank image")
                if im.width > 1024:
                    im = im.resize((1024, int(1024 * im.height / im.width)), Image.LANCZOS)
                save_jpg(outline(im, clip, blk), f)
                rec["file"] = f
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:200]
            log(slug, key, "FAILED", rec["error"])
        out.append(rec)
    return out


# ------------------------------------------------------------------ per block
def rel(p, bdir):
    return str(Path(p).relative_to(bdir)).replace("\\", "/")


def run_block(bdir, NO, do_series=True):
    slug = bdir.name
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    blk = meta["bounds"]
    sat = bdir / "sat"
    sat.mkdir(exist_ok=True)
    (CACHE / slug).mkdir(exist_ok=True)
    t0 = time.time()
    sm = stac_meta(slug, buffer_bbox(blk, 300))
    s2 = sentinel2(slug, blk, sat, do_series)
    orthos = state_orthos(slug, blk, sat)
    noaa, noaa_rej = noaa_for_block(blk, sat, NO)
    oam, oam_rej, oam_counts = oam_for_block(slug, blk, sat)

    latest = []
    if s2["latest_clear"]:
        lc = s2["latest_clear"]
        latest.append({"source": "Sentinel-2 L2A true colour (block +300 m, SCL cloud-masked check)", "date": lc["date"],
                       "resolution_m": 10, "file": rel(lc["files"][0], bdir), "licence": LIC["copernicus"]["licence"],
                       "licence_url": LIC["copernicus"]["url"], "clear_frac": lc["clear_frac"]})
        latest.append({"source": "Sentinel-2 L2A NDVI (block +300 m)", "date": lc["date"], "resolution_m": 10,
                       "file": rel(lc["files"][1], bdir), "licence": LIC["copernicus"]["licence"],
                       "licence_url": LIC["copernicus"]["url"], "ndvi_mean": lc["ndvi"]})
    for col, name, res_m, lic in (("hls2-s30", "NASA HLS S30 (Sentinel-2 harmonised)", 30, "hls"),
                                  ("hls2-l30", "NASA HLS L30 (Landsat harmonised)", 30, "hls"),
                                  ("landsat-c2-l2", "Landsat 8/9 Collection 2 L2", 30, "usgs"),
                                  ("sentinel-1-rtc", "Sentinel-1 RTC radar (all-weather)", 10, "s1rtc")):
        m = sm.get(col) or {}
        if m.get("latest"):
            latest.append({"source": name, "date": m["latest"], "latest_lowcloud_scene": m.get("latest_lowcloud_scene"),
                           "resolution_m": res_m, "licence": LIC[lic]["licence"], "licence_url": LIC[lic]["url"],
                           "note": "metadata only (no clip)"})
    for o in orthos:
        e = {"source": o["source"], "date": o["date"], "resolution_m": o["resolution_m"], "licence": o["licence"],
             "licence_url": o["licence_url"], "service": o["service"]}
        if o.get("file"):
            e["file"] = rel(o["file"], bdir)
        if o.get("error"):
            e["error"] = o["error"]
        latest.append(e)
    naip = (sm.get("naip") or {}).get("years") or []
    if naip:
        n = naip[-1]
        latest.append({"source": "NAIP (USDA FSA) via Planetary Computer", "date": str(n["year"]),
                       "resolution_m": n["gsd_m"], "licence": LIC["naip"]["licence"], "licence_url": LIC["naip"]["url"],
                       "note": "catalogue only here; img/<year>.jpg owned by another lane"})
    for r in noaa:
        if r.get("file"):
            latest.append({"source": f"NOAA NGS ERI {r['description']} ({r['layer']})", "date": r["date"],
                           "resolution_m": None, "file": rel(r["file"], bdir), "licence": r["licence"],
                           "licence_url": r["licence_url"]})
    for r in oam:
        if r.get("file"):
            latest.append({"source": f"OpenAerialMap: {r['title']} ({r['provider']})", "date": r["date"],
                           "resolution_m": r["gsd_m"], "file": rel(r["file"], bdir), "licence": r["licence"],
                           "licence_url": r["page"]})
    latest.sort(key=lambda e: str(e.get("date") or ""), reverse=True)

    def rv(col):
        m = sm.get(col) or {}
        return {k: m.get(k) for k in ("scenes_12mo", "median_revisit_days", "median_revisit_lowcloud_days", "latest")}
    opt = set()
    for col in ("sentinel-2-l2a", "landsat-c2-l2", "hls2-s30", "hls2-l30"):
        opt |= set((sm.get(col) or {}).get("dates") or [])
    optd = [date.fromisoformat(d) for d in opt]
    revisit_summary = {
        "sentinel2": rv("sentinel-2-l2a"), "landsat89": rv("landsat-c2-l2"), "hls_s30": rv("hls2-s30"),
        "hls_l30": rv("hls2-l30"), "sentinel1_rtc": rv("sentinel-1-rtc"),
        "optical_combined": {"acquisition_days_12mo": len(opt), "median_revisit_days": revisit(optd)},
        "naip_years": [n["year"] for n in naip],
        "newest_clear_optical": s2["latest_clear"]["date"] if s2["latest_clear"] else None,
        "days_since_clear_optical": (TODAY - date.fromisoformat(s2["latest_clear"]["date"])).days if s2["latest_clear"] else None,
        "newest_sub_metre": max([str(o["year"]) for o in orthos if o.get("file")] +
                                [r["date"] or "" for r in noaa if r.get("file")] +
                                [r["date"] for r in oam if r.get("file")] + [str(naip[-1]["year"]) if naip else ""] or [""]) or None,
        "text": None,
    }
    rs = revisit_summary
    rs["text"] = (f"Optical satellites imaged this block on {len(opt)} days in the last 12 months "
                  f"(median gap {rs['optical_combined']['median_revisit_days']} d); newest clear Sentinel-2 view "
                  f"{rs['newest_clear_optical']} ({rs['days_since_clear_optical']} d ago). Sentinel-1 radar every "
                  f"~{rs['sentinel1_rtc']['median_revisit_days']} d regardless of cloud. Newest sub-metre open aerial: "
                  f"{rs['newest_sub_metre']}.")
    series = [{k: s[k] for k in ("month", "ndvi", "brightness", "date", "clear_frac")} for s in s2.get("series") or []]
    imagery = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "latest": latest,
        "sentinel2_series": series,
        "sentinel2_trend": series_trend(series) if series else None,
        "revisit_summary": revisit_summary,
        "naip_years": naip,
        "disaster_events": [{k: v if k != "file" else rel(v, bdir) for k, v in r.items() if k not in ("tiles",)} for r in noaa],
        "drone_images": [{k: v if k != "file" else rel(v, bdir) for k, v in r.items()} for r in oam],
        "drone_summary": oam_counts,
        "rejected_here": [{"source": f"NOAA ERI {r['layer']} ({r['attribution']})", "reason": r["rejected"]} for r in noaa_rej]
                         + [{"source": f"OpenAerialMap {r['id']} {r['title']}", "reason": r["rejected"]} for r in oam_rej],
        "sources": "data/imagery_sources.json",
    }
    files = sorted({rel(p, bdir) for p in sat.glob("*.jpg")})
    keep = {e["file"] for e in latest if e.get("file")} | {d["file"] for d in imagery["disaster_events"] if d.get("file")} \
        | {d["file"] for d in imagery["drone_images"] if d.get("file")}
    for f in files:  # remove stale clips of earlier runs (only our own sat/ folder)
        if f not in keep:
            (bdir / f).unlink(missing_ok=True)
    files = sorted(keep)
    from usa.blocklock import block_lock
    with block_lock(bdir):
        m = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
        m["imagery"] = imagery
        fl = [f for f in (m.get("files") or []) if not (isinstance(f, str) and f.startswith("sat/") and f not in keep)]
        for f in files:
            if f not in fl:
                fl.append(f)
        m["files"] = fl
        tmp = bdir / "meta.json.imagery.tmp"
        tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, bdir / "meta.json")
    log(slug, f"done in {time.time() - t0:.0f}s: latest clear S2 {rs['newest_clear_optical']}, files {len(files)}, "
              f"noaa {len(noaa)} ({sum(1 for r in noaa if r.get('file'))} clips), oam {len(oam)}")
    return slug, imagery


# ------------------------------------------------------------------ source catalogue
def catalogue(results, NO):
    def tested(cond, note):
        return {"ok": bool(cond), "note": note, "date": TODAY.isoformat()}
    nb = len(results)
    s2ok = sum(1 for im in results.values() if any(e["source"].startswith("Sentinel-2") for e in im["latest"]))
    srcs = [
        {"name": "Sentinel-2 L2A (Copernicus) via Microsoft Planetary Computer", "kind": "satellite", "resolution_m": 10,
         "revisit": "5 d nominal (2A/2B/2C), 2-3 d at overlap latitudes", "coverage": "global land",
         "licence": LIC["copernicus"]["licence"], "licence_quote": LIC["copernicus"]["quote"], "licence_url": LIC["copernicus"]["url"],
         "commercial_ok": True, "access": "STAC https://planetarycomputer.microsoft.com/api/stac/v1 collection sentinel-2-l2a; anonymous SAS signing, no key",
         "used_for": "latest clear true-colour + NDVI clip (block +300 m), 24-month monthly NDVI/brightness series",
         "tested": tested(s2ok, f"clear clips for {s2ok}/{nb} blocks")},
        {"name": "Landsat 8/9 Collection 2 Level-2 via Planetary Computer", "kind": "satellite", "resolution_m": 30,
         "revisit": "8 d combined (16 d each)", "coverage": "global land", "licence": LIC["usgs"]["licence"],
         "licence_quote": LIC["usgs"]["quote"], "licence_url": LIC["usgs"]["url"], "commercial_ok": True,
         "access": "PC STAC collection landsat-c2-l2 (keyless); also USGS M2M/EarthExplorer (free login)",
         "used_for": "revisit + latest date (metadata)", "tested": tested(True, "scene search per block")},
        {"name": "NASA HLS v2 S30 + L30 (Harmonized Landsat Sentinel-2)", "kind": "satellite", "resolution_m": 30,
         "revisit": "2-3 d combined", "coverage": "global land", "licence": LIC["hls"]["licence"], "licence_quote": LIC["hls"]["quote"],
         "licence_url": LIC["hls"]["url"], "commercial_ok": True,
         "access": "PC STAC collections hls2-s30 / hls2-l30 keyless; LP DAAC CMR-STAC needs a free Earthdata login (not used)",
         "used_for": "revisit + latest date (metadata)", "tested": tested(True, "scene search per block via PC (no Earthdata login needed)")},
        {"name": "Sentinel-1 RTC (radiometric terrain corrected) via Planetary Computer", "kind": "satellite", "resolution_m": 10,
         "revisit": "12 d (S1A; S1C from 2025 restores ~6 d)", "coverage": "global land", "licence": LIC["s1rtc"]["licence"],
         "licence_quote": LIC["s1rtc"]["quote"], "licence_url": LIC["s1rtc"]["url"], "commercial_ok": True,
         "access": "PC STAC collection sentinel-1-rtc (keyless)", "used_for": "latest all-weather radar date (metadata)",
         "tested": tested(True, "scene search per block")},
        {"name": "NAIP (USDA FSA) via Planetary Computer", "kind": "aerial", "resolution_m": 0.6,
         "revisit": "2-3 yr per state (0.3-1 m)", "coverage": "CONUS", "licence": LIC["naip"]["licence"],
         "licence_quote": LIC["naip"]["quote"], "licence_url": LIC["naip"]["url"], "commercial_ok": True,
         "access": "PC STAC collection naip (keyless)", "used_for": "catalogue of years (another lane renders img/<year>.jpg)",
         "tested": tested(True, "year list per block")},
    ]
    for key, svc in ORTHO.items():
        used = [s for s, ks in STATE_ORTHOS.items() if key in ks]
        ok = [s for s in used if any(e.get("file", "").endswith(f"{key}.jpg") for e in results.get(s, {}).get("latest", []))]
        srcs.append({"name": svc["name"], "kind": "aerial", "resolution_m": svc["resolution_m"],
                     "revisit": {"mn_met25": "~5 yr (2016, 2020, 2025)", "tx_stratmap20": "StratMap regional rotation (Travis 2019, 2020)",
                                 "tx_naip22": "NAIP 2-yr", "co_drapp22": "2 yr (DRAPP); 2024 not yet in open bucket"}[key],
                     "coverage": ", ".join(used), "licence": LIC[svc["lic"]]["licence"], "licence_quote": LIC[svc["lic"]]["quote"],
                     "licence_url": LIC[svc["lic"]]["url"], "commercial_ok": True,
                     "access": svc.get("url") or ("S3 GeoTIFF https://colorado-public-imagery.s3.us-west-2.amazonaws.com/DRAPP/DRAPP2022/<tile>.tif, tile index " + svc["index"]),
                     "newest_year": svc["year"], "tested": tested(len(ok) == len(used), f"clip exported for {ok}")})
    ne = sum(1 for e in NO["events"] if e["ok"])
    hits = sorted({(r["event"], r["layer"]) for im in results.values() for r in im["disaster_events"]})
    srcs.append({"name": "NOAA NGS Emergency Response Imagery (storms.ngs.noaa.gov)", "kind": "disaster", "resolution_m": 0.25,
                 "revisit": "event-driven, flown 1-5 d after hurricanes/tornadoes/floods", "coverage": "US event footprints",
                 "licence": LIC["noaa"]["licence"], "licence_quote": LIC["noaa"]["quote"], "licence_url": LIC["noaa"]["url"],
                 "commercial_ok": True, "access": "per-event https://storms.ngs.noaa.gov/storms/<event>/services/index.json (bounds + XYZ tiles); raw tiles s3://noaa-eri-pds",
                 "filter": "layers attributed MAXAR (CC BY-NC) or CAP (licence not stated) are excluded",
                 "tested": tested(ne > 0, f"{ne} event indexes read; {len(hits)} layers within 25 km of a block")})
    oam_n = sum((im.get("drone_summary") or {}).get("open_within_25km", len(im["drone_images"])) for im in results.values())
    srcs.append({"name": "OpenAerialMap (HOT OIN catalogue)", "kind": "drone", "resolution_m": None,
                 "revisit": "ad hoc uploads", "coverage": "global, sparse", "licence": "per image; kept only CC BY 4.0 / CC BY-SA / CC0",
                 "licence_url": "https://docs.openaerialmap.org/", "commercial_ok": True,
                 "access": "https://api.openaerialmap.org/meta?bbox=... (keyless); TMS per image",
                 "tested": tested(True, f"{oam_n} open-licence images within 25 km across all blocks")})
    rejected = [
        {"name": "Planet NICFI basemaps", "reason": "NICFI licence is non-commercial; tropics only"},
        {"name": "Maxar Open Data Program (incl. Maxar layers on NOAA ERI viewers)", "reason": "CC BY-NC 4.0 - non-commercial"},
        {"name": "Google satellite / Google Earth", "reason": "proprietary, ToS forbids extraction"},
        {"name": "Esri World Imagery", "reason": "proprietary (Maxar/Vexcel etc.), ToS"},
        {"name": "Bing Maps aerial", "reason": "proprietary"},
        {"name": "Mapbox Satellite", "reason": "proprietary, ToS"},
        {"name": "Nearmap (incl. CAGIS Hamilton Co OH aerials 2021-2026)", "reason": "proprietary - CAGIS service description: 'CAGIS Nearmap Aerial Imagery'"},
        {"name": "EagleView / Pictometry (incl. Maricopa County Assessor Aerials, INCOG Tulsa current imagery)", "reason": "proprietary vendor imagery, sold via county data sales"},
        {"name": "Colorado Statewide Aerial Imagery (Vexcel via OIT/CDOT)", "reason": "vendor licence with acceptable-use policy"},
        {"name": "City of Huntsville AerialImagery2026", "reason": "'copyrighted by the City of Huntsville', repackaging/reselling prohibited"},
        {"name": "Jefferson County AL imagery (jccgis.jccal.org)", "reason": f"{TERMS_NOT_EXPLICIT} (login/token required)"},
        {"name": "Maricopa County GIO AerialComposite", "reason": f"{TERMS_NOT_EXPLICIT} (no licence on service; multi-vendor composite)"},
        {"name": "Ohio OSIP Most Current ImageServer (maps.ohio.gov)", "reason": f"{TERMS_NOT_EXPLICIT}: disclaimer only ('public use at the user's own risk'); works, 0.08-0.3 m, 2017-2024"},
        {"name": "Missouri MSDIS 6-inch statewide 2023/24", "reason": f"{TERMS_NOT_EXPLICIT}: free service, no licence text"},
        {"name": "Hennepin 2022 (hen22) / Ramsey 2020 (rams20) 15 cm on MnGeo WMS", "reason": f"{TERMS_NOT_EXPLICIT} (met25, explicitly public domain, used instead)"},
        {"name": "MnGeo mncomp composite", "reason": f"{TERMS_NOT_EXPLICIT}: mixed sources, per-pixel licence unclear"},
        {"name": "Oklahoma statewide 2021 imagery (OKMaps)", "reason": f"{TERMS_NOT_EXPLICIT} (no public endpoint/licence found)"},
        {"name": "NCTCOG regional orthos (Dallas)", "reason": "member cost-share, no public service"},
        {"name": "DRCOG DRAPP 2024 (gis.drcog.org)", "reason": f"{TERMS_NOT_EXPLICIT}: no imagery service/licence published (CC0 DRAPP 2022 on AWS used)"},
        {"name": "NOAA ERI layers attributed CAP (Civil Air Patrol)", "reason": f"{TERMS_NOT_EXPLICIT}: no licence stated for these layers"},
        {"name": "OpenAerialMap images without a CC0 / CC BY / CC BY-SA licence on the image", "reason": f"{TERMS_NOT_EXPLICIT}"},
    ]
    out = {"meta": {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "lane": "I1 imagery",
                    "script": "usa/imagery.py", "doc": "usa/research/imagery.md",
                    "rule": "permanently free + commercial use OK; licence quote + URL recorded per source and per image",
                    "blocks": sorted(results)},
           "sources": srcs, "rejected": rejected}
    SOURCES_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SOURCES_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SOURCES_OUT)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_series = "--no-series" not in sys.argv
    dirs = [d for d in sorted(BLOCKS.iterdir()) if (d / "meta.json").exists() and (not args or d.name in args)]
    NO = noaa_layers()
    results = {}
    for d in dirs:
        try:
            s, im = run_block(d, NO, do_series)
            results[s] = im
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            log(d.name, "FAILED", e)
    # keep results of blocks not in this run (from their meta) for the catalogue
    for d in sorted(BLOCKS.iterdir()):
        if d.name not in results and (d / "meta.json").exists():
            im = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("imagery")
            if im:
                results[d.name] = im
    catalogue(results, NO)
    log("catalogue ->", SOURCES_OUT)


if __name__ == "__main__":
    main()

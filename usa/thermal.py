"""Measured land-surface temperature (LST) per 3D block - lane TH.

Sources (all free, no key, commercial use allowed):
  * Landsat 8/9 Collection 2 Level-2 Surface Temperature (ST_B10 = STAC asset 'lwir11'), 100 m TIRS resampled to
    30 m by USGS, via Microsoft Planetary Computer STAC 'landsat-c2-l2' (anonymous SAS). USGS: public domain.
  * GOES-19 ABI L2 Land Surface Temperature CONUS (ABI-L2-LSTC, hourly, 2 km) from NOAA NODD on AWS
    (s3://noaa-goes19, anonymous HTTPS). NOAA: public domain. One common clear summer day -> diurnal curve.
  * Air temperature: NWS/FAA ASOS routine (METAR) obs of the nearest ASOS station via the Iowa Environmental
    Mesonet archive (IEM: "public domain and may be used freely by anyone for any lawful purpose").
  * ECOSTRESS LST 70 m (LP DAAC) - SKIPPED: data URLs 302 to urs.earthdata.nasa.gov (Earthdata login required).

Outputs per block (web/usa/blocks/<slug>/thermal/):
  lst_summer.png, lst_winter.png  - grids over meta.bounds, inferno ramp, nearest-neighbour (30 m pixels visible)
  lst_grid.json (summer, hottest clear JJA scene), lst_grid_winter.json (coolest clear DJF scene):
      {bounds, w, h, scale, offset, values:[degC*10 ints, row 0 = north], date, ...}
  meta.thermal, houses[].thermal (written under usa.blocklock.block_lock, re-read inside the lock).

Run:  .venv\\Scripts\\python -u -W ignore -m usa.thermal [slug ...]
"""
import csv
import io
import json
import math
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "thermal"
SCN = CACHE / "scenes"
SAMPLES = CACHE / "samples"
for d in (CACHE, SCN, SAMPLES):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
T0, T1 = "2023-11-24", "2026-11-24"
PAD_M = 2000.0          # context window around the block (urban-heat comparison)
GRID_N = 36             # output grid cells per side over meta.bounds (~3 m cells; true pixel is 30 m)
UA = {"User-Agent": "roof-intel-thermal/1.0"}

LIC = {
    "landsat": {"licence": "US Government public domain (USGS Landsat Collection 2)",
                "quote": "There are no restrictions on the use of Landsat products.",
                "url": "https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature",
                "citation": "EROS (2020) Landsat 8-9 OLI/TIRS Level-2, Collection 2. USGS. doi:10.5066/P9OGBGM6"},
    "goes": {"licence": "US Government public domain (NOAA GOES-R via NOAA Open Data Dissemination on AWS)",
             "url": "https://registry.opendata.aws/noaa-goes/"},
    "iem": {"licence": "Public domain (NWS/FAA ASOS observations via Iowa Environmental Mesonet)",
            "quote": "The materials found on this website are in the public domain and may be used freely by "
                     "anyone for any lawful purpose.",
            "url": "https://mesonet.agron.iastate.edu/disclaimer.php"},
}
PIXEL_NOTE = "30 m pixel covers several houses/yard; not a roof-only reading"

# inferno (perceptually uniform) control points
_INF = np.array([[0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85], [227, 89, 51],
                 [249, 140, 10], [249, 201, 50], [252, 255, 164]], float)


def ramp(t):
    t = np.clip(np.nan_to_num(t, nan=0.0), 0, 1) * (len(_INF) - 1)
    i = np.minimum(t.astype(int), len(_INF) - 2)
    f = (t - i)[..., None]
    return (_INF[i] * (1 - f) + _INF[i + 1] * f).astype(np.uint8)


def get(url, timeout=120, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)


def wjson(p, obj, **kw):
    tmp = Path(str(p) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, **kw)
    os.replace(tmp, p)


# ----------------------------------------------------------------------------------------- Landsat search/read
def search_landsat(slug, bounds):
    cp = CACHE / f"search_{slug}.json"
    if cp.exists():
        return json.loads(cp.read_text())
    import pystac_client
    c = pystac_client.Client.open(PC_STAC)
    out = []
    for it in c.search(collections=["landsat-c2-l2"], bbox=bounds, datetime=f"{T0}/{T1}",
                       query={"platform": {"in": ["landsat-8", "landsat-9"]}}).items():
        p = it.properties
        if not all(k in it.assets for k in ("lwir11", "qa_pixel", "qa")):
            continue   # e.g. SR-only (L2SR) scenes have no thermal band
        out.append({"id": it.id, "dt": it.datetime.isoformat(), "platform": p.get("platform"),
                    "cloud": p.get("eo:cloud_cover"), "tier": p.get("landsat:collection_category"),
                    "lwir": it.assets["lwir11"].href, "qa_pixel": it.assets["qa_pixel"].href,
                    "qa": it.assets["qa"].href})
    cp.write_text(json.dumps(out))
    return out


def _sign(h):
    import planetary_computer
    return planetary_computer.sign(h)


GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".TIF,.tif",
                GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2", VSI_CACHE="TRUE")


def read_scene(slug, it, bounds):
    """read qa_pixel over block+2 km; if the block is clear also read ST + ST_QA. Cached as npz."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    cp = SCN / f"{slug}__{it['id']}.npz"
    if cp.exists():
        z = np.load(cp, allow_pickle=False)
        return {k: z[k] for k in z.files}
    res = {}
    for k in range(3):
        try:
            with rasterio.Env(**GDAL_ENV):
                with rasterio.open(_sign(it["qa_pixel"])) as ds:
                    l, b, r, t = transform_bounds("EPSG:4326", ds.crs, *bounds)
                    win = from_bounds(l - PAD_M, b - PAD_M, r + PAD_M, t + PAD_M, ds.transform)
                    win = win.round_offsets().round_lengths()
                    qp = ds.read(1, window=win, boundless=True, fill_value=1)
                    tr = ds.window_transform(win)
                    crs = ds.crs.to_wkt()
                ok = block_clear(qp, tr, (l, b, r, t))
                res = {"qp": qp, "tr": np.array(tr[:6]), "crs": np.array(crs), "blk": np.array([l, b, r, t]),
                       "ok": np.array(ok)}
                if ok:
                    with rasterio.open(_sign(it["lwir"])) as ds:
                        res["st"] = ds.read(1, window=win, boundless=True, fill_value=0)
                    with rasterio.open(_sign(it["qa"])) as ds:
                        res["sq"] = ds.read(1, window=win, boundless=True, fill_value=-9999)
            break
        except Exception as e:
            if k == 2:
                print(f"  ! read fail {it['id']}: {e}")
                return None
            time.sleep(3)
    np.savez_compressed(cp, **res)
    return res


def clear_mask(qp):
    # bit0 fill, bit1 dilated cloud, bit2 cirrus, bit3 cloud, bit4 cloud shadow, bit5 snow (kept: real surface)
    return (qp & 0b11111) == 0


def block_pixels(tr, shape, blk):
    """boolean mask of pixels that intersect the block rectangle (UTM)."""
    a, b_, c, d, e, f = tr[:6]
    cols = c + a * (np.arange(shape[1]) + 0.5)
    rows = f + e * (np.arange(shape[0]) + 0.5)
    half = abs(a) / 2
    l, b, r, t = blk
    cx = (cols + half > l) & (cols - half < r)
    ry = (rows + half > b) & (rows - half < t)
    return ry[:, None] & cx[None, :]


def block_clear(qp, tr, blk):
    m = block_pixels(tr, qp.shape, blk)
    cm = clear_mask(qp)
    if not cm[m].all():
        return False
    return cm.mean() >= 0.8   # 2 km context mostly clear too (cloud-edge / shadow contamination guard)


def to_grid(st_c, tr, crs_wkt, bounds, n=GRID_N):
    """nearest-neighbour reprojection of the UTM ST grid onto an n x n lon/lat grid over meta.bounds."""
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_bounds as tfb
    from affine import Affine
    dst = np.full((n, n), np.nan, np.float32)
    reproject(st_c.astype(np.float32), dst, src_transform=Affine(*tr), src_crs=crs_wkt, src_nodata=np.nan,
              dst_transform=tfb(*bounds, n, n), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.nearest)
    return dst


# ----------------------------------------------------------------------------------------------- air temperature
_NET = {}


def nearest_station(state, lon, lat):
    cands = []
    for st in {state, *NEIGHBOUR.get(state, [])}:
        if st not in _NET:
            cp = CACHE / f"iem_{st}_ASOS.geojson"
            if not cp.exists():
                cp.write_bytes(get(f"https://mesonet.agron.iastate.edu/geojson/network/{st}_ASOS.geojson"))
            _NET[st] = json.loads(cp.read_text())["features"]
        cands += _NET[st]
    best = None
    for f in cands:
        p = f["properties"]
        if p.get("archive_end") or not p.get("online", True):
            continue
        if p.get("archive_begin") and p["archive_begin"] > T0:
            continue
        x, y = f["geometry"]["coordinates"]
        dkm = math.hypot((x - lon) * 111.32 * math.cos(math.radians(lat)), (y - lat) * 110.57)
        awos = (p.get("attributes") or {}).get("IS_AWOS") == "1"
        score = dkm + (15 if awos else 0)   # prefer a true ASOS when one is within ~15 km extra
        if best is None or score < best[0]:
            best = (score, {"id": f["id"], "name": p.get("sname"), "network": p.get("network"), "km": round(dkm, 1),
                            "lon": x, "lat": y, "awos": awos})
    return best[1] if best else None


NEIGHBOUR = {"AZ": [], "MN": [], "OH": ["KY"], "CO": [], "TX": [], "AL": [], "MO": ["KS"], "OK": []}


def air_series(sid):
    cp = CACHE / f"asos_{sid}.csv"
    if not cp.exists():
        a, b = datetime.fromisoformat(T0), datetime.fromisoformat(T1)
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=tmpc&year1=%d&month1=%d"
               "&day1=%d&year2=%d&month2=%d&day2=%d&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&report_type=3"
               % (sid, a.year, a.month, a.day, b.year, b.month, b.day))
        cp.write_bytes(get(url, timeout=300))
    t, v = [], []
    for row in csv.DictReader(io.StringIO(cp.read_text())):
        if row.get("tmpc") in (None, "", "M"):
            continue
        t.append(datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
        v.append(float(row["tmpc"]))
    return np.array(t), np.array(v)


def air_at(ser, dt):
    t, v = ser
    if not len(t):
        return None
    ts = dt.timestamp()
    i = int(np.argmin(np.abs(t - ts)))
    return round(float(v[i]), 1) if abs(t[i] - ts) <= 5400 else None


# ----------------------------------------------------------------------------------------------- rendering
def save_grid(path, g, bounds, date, extra):
    vals = np.where(np.isfinite(g), np.round(g * 10), -9999).astype(int)
    obj = {"bounds": bounds, "w": int(g.shape[1]), "h": int(g.shape[0]), "scale": 0.1, "offset": 0.0,
           "nodata": -9999, "units": "degC", "row0": "north", "date": date, "values": vals.ravel().tolist()}
    obj.update(extra)
    wjson(path, obj, separators=(",", ":"))


def save_png(path, g, vmin, vmax, up=8):
    rgb = ramp((g - vmin) / max(vmax - vmin, 1e-6))
    a = np.where(np.isfinite(g), 255, 0).astype(np.uint8)[..., None]
    img = Image.fromarray(np.concatenate([rgb, a], -1), "RGBA").resize((g.shape[1] * up, g.shape[0] * up),
                                                                     Image.NEAREST)
    img.save(path, optimize=True)


def save_sample(path, st_c, blkmask, vmin, vmax, up=4):
    rgb = ramp((st_c - vmin) / max(vmax - vmin, 1e-6))
    rgb[~np.isfinite(st_c)] = (90, 90, 90)
    img = Image.fromarray(rgb, "RGB").resize((st_c.shape[1] * up, st_c.shape[0] * up), Image.NEAREST)
    ys, xs = np.where(blkmask)
    from PIL import ImageDraw
    ImageDraw.Draw(img).rectangle([xs.min() * up, ys.min() * up, (xs.max() + 1) * up - 1, (ys.max() + 1) * up - 1],
                                  outline=(0, 255, 255), width=2)
    img.save(path, quality=90)


# ----------------------------------------------------------------------------------------------- GOES diurnal
def goes_diurnal(day, blocks):
    """GOES-19 ABI-L2-LSTC hourly over one UTC day+6h window -> {slug: [{t_utc, lst_c|None}]}"""
    import h5py
    from pyproj import Proj
    out = {s: [] for s, _ in blocks}
    gdir = CACHE / "goes" / day
    gdir.mkdir(parents=True, exist_ok=True)
    d0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(hours=6)
    for h in range(24):
        t = d0 + timedelta(hours=h)
        doy = t.timetuple().tm_yday
        pref = f"ABI-L2-LSTC/{t.year}/{doy:03d}/{t.hour:02d}/"
        fp = gdir / f"{t:%Y%m%d%H}.nc"
        if not fp.exists():
            xml = get(f"https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix={pref}").decode()
            keys = [k.split("</Key>")[0] for k in xml.split("<Key>")[1:]]
            if not keys:
                continue
            fp.write_bytes(get("https://noaa-goes19.s3.amazonaws.com/" + keys[0], timeout=300))
        with h5py.File(fp, "r") as f:
            L = f["LST"]
            raw = L[()]
            sc, of = L.attrs.get("scale_factor", [1])[0], L.attrs.get("add_offset", [0])[0]
            fill = L.attrs.get("_FillValue", [65535])[0]
            dqf = f["DQF"][()]
            x = f["x"][()] * f["x"].attrs["scale_factor"][0] + f["x"].attrs["add_offset"][0]
            y = f["y"][()] * f["y"].attrs["scale_factor"][0] + f["y"].attrs["add_offset"][0]
            pj = f["goes_imager_projection"].attrs
            H = pj["perspective_point_height"][0]
            proj = Proj(proj="geos", h=H, lon_0=pj["longitude_of_projection_origin"][0],
                        sweep=pj["sweep_angle_axis"].decode() if isinstance(pj["sweep_angle_axis"], bytes)
                        else str(pj["sweep_angle_axis"]), a=pj["semi_major_axis"][0], b=pj["semi_minor_axis"][0])
            for slug, (lon, lat) in blocks:
                gx, gy = proj(lon, lat)
                i, j = int(np.argmin(np.abs(y * H - gy))), int(np.argmin(np.abs(x * H - gx)))
                v = raw[i, j]
                ok = v != fill and dqf[i, j] <= 1
                out[slug].append({"t_utc": t.strftime("%Y-%m-%dT%H:%MZ"),
                                  "lst_c": round(float(v) * float(sc) + float(of) - 273.15, 1) if ok else None,
                                  "qf": ["high", "medium", "low", "none"][min(int(dqf[i, j]), 3)]})
    return out


# ----------------------------------------------------------------------------------------------- per block
def process(slug):
    bd = BLOCKS / slug
    meta = json.loads((bd / "meta.json").read_text(encoding="utf-8"))
    houses = json.loads((bd / "houses.json").read_text(encoding="utf-8"))
    bounds = [float(v) for v in meta["bounds"]]
    lon, lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    items = search_landsat(slug, bounds)
    items = [i for i in items if (i["tier"] or "T1") == "T1" and (i["cloud"] is None or i["cloud"] <= 85)]
    # one acquisition per platform+date (adjacent WRS rows overlap)
    best = {}
    for i in items:
        k = (i["platform"], i["dt"][:10])
        if k not in best or (i["cloud"] or 0) < (best[k]["cloud"] or 0):
            best[k] = i
    items = sorted(best.values(), key=lambda i: i["dt"])
    with ThreadPoolExecutor(8) as ex:
        reads = list(ex.map(lambda i: read_scene(slug, i, bounds), items))

    stn = nearest_station(meta.get("state"), lon, lat)
    air = air_series(stn["id"]) if stn else (np.array([]), np.array([]))

    scenes = []
    for it, r in zip(items, reads):
        if r is None or not bool(r["ok"]) or "st" not in r:
            continue
        st = r["st"].astype(np.float32)
        stc = np.where(st > 0, st * 0.00341802 + 149.0 - 273.15, np.nan)
        stc[~clear_mask(r["qp"])] = np.nan
        unc = np.where(r["sq"] > 0, r["sq"] * 0.01, np.nan)
        bm = block_pixels(r["tr"], stc.shape, r["blk"])
        if not np.isfinite(stc[bm]).all():
            continue
        u = float(np.nanmean(unc[bm])) if np.isfinite(unc[bm]).any() else None
        if u is not None and u > 6:
            continue
        g = to_grid(stc, r["tr"], str(r["crs"]), bounds)
        dt = datetime.fromisoformat(it["dt"])
        ctx = stc[~bm]
        scenes.append({"it": it, "dt": dt, "g": g, "stc": stc, "bm": bm, "unc": u,
                       "mean": float(np.nanmean(g)), "max": float(np.nanmax(g)), "med": float(np.nanmedian(g)),
                       "ctx_med": float(np.nanmedian(ctx)), "ctx_p2": float(np.nanpercentile(ctx, 2)),
                       "ctx_p98": float(np.nanpercentile(ctx, 98)), "air": air_at(air, dt)})
    print(f"{slug}: {len(items)} T1 acquisitions, {len(scenes)} clear over block")
    if not scenes:
        return {"slug": slug, "scenes": 0}

    summer = [s for s in scenes if s["dt"].month in (6, 7, 8)] or scenes
    winter = [s for s in scenes if s["dt"].month in (12, 1, 2)] or \
             [s for s in scenes if s["dt"].month in (11, 3)] or scenes
    hot = max(summer, key=lambda s: s["mean"])
    cold = min(winter, key=lambda s: s["mean"])
    hottest = max(scenes, key=lambda s: s["mean"])
    coolest = min(scenes, key=lambda s: s["mean"])

    td = bd / "thermal"
    td.mkdir(exist_ok=True)
    common = {"source": "Landsat C2 L2 ST_B10 (USGS) via Planetary Computer", "pixel_m": 30, "native_tirs_m": 100,
              "resampling": "nearest from UTM 30 m grid; each 30 m pixel spans ~10 x 10 grid cells"}
    for s, name, season in ((hot, "lst_grid.json", "summer"), (cold, "lst_grid_winter.json", "winter")):
        lo, hi = float(np.nanmin(s["g"])), float(np.nanmax(s["g"]))
        if hi - lo < 4:   # block-local stretch, but never exaggerate < 4 C of spread into a full ramp
            c = (hi + lo) / 2
            lo, hi = c - 2, c + 2
        save_grid(td / name, s["g"], bounds, s["dt"].strftime("%Y-%m-%d"),
                  dict(common, season=season, scene_id=s["it"]["id"], time_utc=s["dt"].strftime("%H:%MZ"),
                       png_vmin_c=round(lo, 1), png_vmax_c=round(hi, 1),
                       png_range_basis="block min/max, widened to >= 4 C span",
                       context_p2_c=round(s["ctx_p2"], 1), context_p98_c=round(s["ctx_p98"], 1),
                       context_median_c=round(s["ctx_med"], 1),
                       context_basis="clear pixels within 2 km of the block", air_temp_c=s["air"]))
        save_png(td / f"lst_{season}.png", s["g"], lo, hi)
    save_sample(SAMPLES / f"{slug}_{hot['dt']:%Y%m%d}_context.jpg", hot["stc"], hot["bm"], hot["ctx_p2"],
                hot["ctx_p98"])

    # per-house
    n = GRID_N
    def cell(h):
        if h.get("lon") is None:
            return None
        cx = int((h["lon"] - bounds[0]) / (bounds[2] - bounds[0]) * n)
        cy = int((bounds[3] - h["lat"]) / (bounds[3] - bounds[1]) * n)
        return (min(max(cy, 0), n - 1), min(max(cx, 0), n - 1))
    hv = {}
    for h in houses:
        c = cell(h)
        if c is None:
            continue
        rel = [float(s["g"][c] - s["med"]) for s in scenes if np.isfinite(s["g"][c])]
        rel_s = [float(s["g"][c] - s["med"]) for s in summer if np.isfinite(s["g"][c])]
        hv[h["id"]] = {"summer_lst_c": round(float(hot["g"][c]), 1), "winter_lst_c": round(float(cold["g"][c]), 1),
                       "rel_to_block_c": round(float(np.mean(rel)), 2) if rel else None,
                       "rel_to_block_summer_c": round(float(np.mean(rel_s)), 2) if rel_s else None,
                       "summer_date": hot["dt"].strftime("%Y-%m-%d"), "winter_date": cold["dt"].strftime("%Y-%m-%d"),
                       "pixel_note": PIXEL_NOTE}
    rels = [v["rel_to_block_c"] for v in hv.values() if v["rel_to_block_c"] is not None]
    p90 = float(np.percentile(rels, 90)) if rels else None
    for v in hv.values():
        v["hot_roof_flag"] = bool(v["rel_to_block_c"] is not None and p90 is not None
                                  and v["rel_to_block_c"] >= p90 and v["rel_to_block_c"] > 0)

    # context: modelled sun (h.sun) vs measured relative LST
    pairs = [(hv[h["id"]]["rel_to_block_c"], (h.get("sun") or {}).get("shaded_pct")) for h in houses
             if h["id"] in hv and hv[h["id"]]["rel_to_block_c"] is not None and (h.get("sun") or {}).get("shaded_pct")
             is not None]
    corr = None
    if len(pairs) >= 5:
        a = np.array(pairs, float)
        if a[:, 0].std() > 0 and a[:, 1].std() > 0:
            corr = round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 2)

    uhi_hot = hot["mean"] - hot["ctx_med"]
    uhi_all = float(np.mean([s["mean"] - s["ctx_med"] for s in scenes]))
    series = [{"date": s["dt"].strftime("%Y-%m-%d"), "time_utc": s["dt"].strftime("%H:%MZ"),
               "platform": s["it"]["platform"], "block_mean_c": round(s["mean"], 1), "block_max_c": round(s["max"], 1),
               "context_2km_median_c": round(s["ctx_med"], 1), "air_temp_c": s["air"],
               "st_uncertainty_k": round(s["unc"], 2) if s["unc"] is not None else None} for s in scenes]
    files = ["thermal/lst_summer.png", "thermal/lst_winter.png", "thermal/lst_grid.json",
             "thermal/lst_grid_winter.json"]
    th = {
        "source": "Landsat 8/9 Collection 2 Level-2 Surface Temperature (ST_B10), USGS, via Microsoft Planetary "
                  "Computer STAC 'landsat-c2-l2'",
        "resolution_m": 30, "native_tirs_m": 100,
        "period": f"{T0}/{T1}", "acquisitions_n": len(items), "scenes_n": len(scenes),
        "clear_rule": "QA_PIXEL fill/dilated-cloud/cirrus/cloud/shadow all clear on every block pixel, >=80% clear "
                      "within 2 km, Tier 1 only, ST_QA uncertainty <= 6 K",
        "overpass_local": "~10:30-11:00 local solar time (Landsat); afternoon peak LST is higher",
        "hottest_date": hottest["dt"].strftime("%Y-%m-%d"), "hottest_block_mean_c": round(hottest["mean"], 1),
        "hottest_block_max_c": round(hottest["max"], 1),
        "coolest_date": coolest["dt"].strftime("%Y-%m-%d"), "coolest_block_mean_c": round(coolest["mean"], 1),
        "summer_date": hot["dt"].strftime("%Y-%m-%d"), "winter_date": cold["dt"].strftime("%Y-%m-%d"),
        "summer_scenes_n": len([s for s in scenes if s["dt"].month in (6, 7, 8)]),
        "winter_scenes_n": len([s for s in scenes if s["dt"].month in (12, 1, 2)]),
        "uhi_vs_2km_hottest_c": round(uhi_hot, 1), "uhi_vs_2km_mean_c": round(uhi_all, 1),
        "urban_heat_note": (f"On the hottest clear summer pass ({hot['dt']:%Y-%m-%d}) the block averaged "
                            f"{hot['mean']:.1f} C, {abs(uhi_hot):.1f} C {'warmer' if uhi_hot >= 0 else 'cooler'} than "
                            f"the median of clear pixels within 2 km ({hot['ctx_med']:.1f} C); across all {len(scenes)} "
                            f"clear passes the block is on average {uhi_all:+.1f} C vs its 2 km surroundings. "
                            "Surface (skin) temperature, not air temperature."),
        "house_pixel_note": PIXEL_NOTE,
        "hot_roof_rule": "houses whose mean (pixel - block median) across all clear scenes is in the block's top "
                         "decile and > 0; houses sharing one 30 m pixel share the flag",
        "sun_context": {"corr_rel_lst_vs_modelled_shaded_pct": corr,
                        "note": "h.sun is modelled from LiDAR; measured LST is a 30 m mixed pixel (roof+yard+trees). "
                                "Use together only as context."},
        "air": ({"source": "NWS/FAA ASOS routine obs via Iowa Environmental Mesonet", "station": stn,
                 "match": "nearest routine obs within 90 min of the Landsat pass", "licence": LIC["iem"]["licence"]}
                if stn else None),
        "ecostress": "skipped: LP DAAC ECO_L2T_LSTE v002 granules exist over the block but downloads redirect to "
                     "urs.earthdata.nasa.gov (free Earthdata login required; none configured)",
        "licence": LIC["landsat"]["licence"], "licence_quote": LIC["landsat"]["quote"],
        "licence_url": LIC["landsat"]["url"], "citation": LIC["landsat"]["citation"],
        "files": files, "series": series,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }
    with block_lock(bd):
        meta = json.loads((bd / "meta.json").read_text(encoding="utf-8"))
        houses = json.loads((bd / "houses.json").read_text(encoding="utf-8"))
        old = meta.get("thermal") or {}
        if "goes_diurnal" in old:
            th["goes_diurnal"] = old["goes_diurnal"]
        meta["thermal"] = th
        fl = meta.setdefault("files", [])
        for f in files:
            if f not in fl:
                fl.append(f)
        for h in houses:
            if h.get("id") in hv:
                h["thermal"] = hv[h["id"]]
        wjson(bd / "houses.json", houses, separators=(",", ":"))
        wjson(bd / "meta.json", meta, indent=1)
    return {"slug": slug, "scenes": len(scenes), "acq": len(items), "hottest": th["hottest_date"],
            "hot_mean": th["hottest_block_mean_c"], "hot_max": th["hottest_block_max_c"],
            "summer": th["summer_scenes_n"], "winter": th["winter_scenes_n"], "uhi": th["uhi_vs_2km_hottest_c"],
            "uhi_mean": th["uhi_vs_2km_mean_c"], "station": stn and stn["id"], "station_km": stn and stn["km"],
            "corr": corr, "flags": sum(v["hot_roof_flag"] for v in hv.values()), "houses": len(hv),
            "pass_dates": [s["date"] for s in series]}


def add_goes(results, day=None):
    """pick the JJA date with the most blocks clear in Landsat (else given) and attach a GOES diurnal curve."""
    if day is None:
        from collections import Counter
        c = Counter(d for r in results for d in r.get("pass_dates", []) if d[5:7] in ("06", "07", "08")
                    and d[:4] == "2026")
        day = c.most_common(1)[0][0] if c else "2026-07-15"
    blocks = []
    for r in results:
        m = json.loads((BLOCKS / r["slug"] / "meta.json").read_text(encoding="utf-8"))
        b = m["bounds"]
        blocks.append((r["slug"], ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)))
    print("GOES diurnal day", day)
    cur = goes_diurnal(day, blocks)
    for slug, _ in blocks:
        bd = BLOCKS / slug
        with block_lock(bd):
            meta = json.loads((bd / "meta.json").read_text(encoding="utf-8"))
            th = meta.get("thermal")
            if th is None:
                continue
            pts = cur[slug]
            vals = [p["lst_c"] for p in pts if p["lst_c"] is not None]
            th["goes_diurnal"] = {
                "source": "GOES-19 ABI L2 Land Surface Temperature CONUS (ABI-L2-LSTC), NOAA NODD on AWS",
                "resolution_m": 2000, "day_utc_window": f"{day}T06Z + 24 h", "points": pts,
                "max_c": max(vals) if vals else None, "min_c": min(vals) if vals else None,
                "clear_hours": len(vals),
                "note": "2 km pixel = whole neighbourhood; only DQF high/medium-quality retrievals kept (low/no-retrieval = cloud or degraded); shows diurnal shape, "
                        "not block detail",
                "licence": LIC["goes"]["licence"], "licence_url": LIC["goes"]["url"]}
            wjson(bd / "meta.json", meta, indent=1)
        r = next(x for x in results if x["slug"] == slug)
        r["goes_max"] = th["goes_diurnal"]["max_c"]
        r["goes_hours"] = th["goes_diurnal"]["clear_hours"]
    return day


def main():
    slugs = sys.argv[1:] or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists())
    results = []
    for s in slugs:
        try:
            results.append(process(s))
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append({"slug": s, "error": str(e)})
    ok = [r for r in results if r.get("scenes")]
    try:
        day = add_goes(ok)
    except Exception as e:
        import traceback; traceback.print_exc()
        day = None
    wjson(CACHE / "summary.json", {"goes_day": day, "results": results}, indent=1)
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != "pass_dates"}))


if __name__ == "__main__":
    main()

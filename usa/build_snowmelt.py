"""Snow-melt test: which roofs (and mapped driveways) lose their snow faster than the neighbours'
after the same storm.

Data (all free, no login, commercial use OK):
  * NOAA NCEI GHCN-Daily SNOW / SNWD via the Access Data Service (US public domain) -> storm events
  * Sentinel-2 L2A COGs (Copernicus open licence) via Element84 Earth Search STAC -> B03, B11, B08, SCL
    window reads of the block bbox only.

Per house and per post-storm clear scene: area-weighted NDSI snow fraction of the roof ring at the 20 m
B11 grid, compared with the median of the OTHER roofs in the block on the same scene (melt anomaly).
Across scenes: early_melt_share, sign-test p, median anomaly.  Observations only, never claims about
what is hidden under the roof.

    .venv\\Scripts\\python usa\\build_snowmelt.py boulder-co denver-co ...
    .venv\\Scripts\\python usa\\build_snowmelt.py --snowy        (the default snowy-climate list)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BLOCKS, ROOT, S, UA, block_box_3857  # noqa: E402

CACHE = ROOT / "usa" / "cache" / "snow"
STAC = "https://earth-search.aws.element84.com/v1"
NCEI_DATA = "https://www.ncei.noaa.gov/access/services/data/v1"
NCEI_SEARCH = "https://www.ncei.noaa.gov/access/services/search/v1/data"
START, END = "2017-10-01", dt.date.today().isoformat()
EVENT_MM = 100          # storm total >= 10 cm
FRESH_MM = 5            # new snow above this between storm and scene resets the clock
SCENE_DAYS = (1, 10)
CLEAR_MIN = 0.90        # block must be this cloud-free (SCL) for the scene to count
SNOW_PRESENT = 0.25     # block median snow fraction needed for a contrast to exist
EARLY = 0.10            # anomaly below -0.10 snow fraction = "melting earlier than neighbours"
BAD_SCL = {0, 1, 3, 8, 9, 10}   # nodata, saturated, cloud shadow, cloud med/high, cirrus

SNOWY = ["anchorage-ak", "boulder-co", "denver-co", "detroit-mi", "columbus-oh", "columbus-oz",
         "minneapolis-mn", "minneapolis-oz", "saint-paul-mn", "omaha-ne", "kansas-city-mo",
         "salt-lake-city-ut", "oak-park-il", "spokane-wa", "somerville-ma", "cincinnati-oh",
         "levittown-ny", "philadelphia-pa", "washington-dc"]

SOURCES = [
    {"name": "NOAA NCEI GHCN-Daily (SNOW, SNWD) via Access Data Service",
     "url": "https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries",
     "licence": "US Government work, public domain",
     "licence_url": "https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily"},
    {"name": "Copernicus Sentinel-2 L2A (B03, B08, B11, SCL) COGs via Element84 Earth Search",
     "url": "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a",
     "licence": "Copernicus Sentinel data, free and open incl. commercial use (attribution: 'contains modified Copernicus Sentinel data')",
     "licence_url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice"},
]

METHOD = ("Storms: consecutive GHCN-Daily snowfall days at the nearest reporting station(s) summed; total >= 10 cm. "
          "Scenes: Sentinel-2 L2A passes 1-10 days after the storm's last snow day, with no new snowfall > 0.5 cm "
          "in between, block >= 90% clear by SCL, and block median snow fraction >= 0.25. Only the block bbox (+40 m) "
          "is window-read. NDSI = (B03 - B11)/(B03 + B11) on the 20 m B11 grid (B03 averaged 2x2); snow fraction = "
          "clip(0.06 + 1.21*NDSI, 0, 1) (Salomonson & Appel linear form), forced to 0 where SCL is water. "
          "Each roof ring is intersected exactly with the 20 m pixels (area-weighted mean). Anomaly = roof snow fraction "
          "minus the median of the other roofs in the block on the same scene (needs >= 2 other roofs). "
          "early_melt_share = share of observations with anomaly < -0.10; sign_p = one-sided sign test of anomaly < 0. "
          "Flag 'faster-melting roof (possible heat loss)' needs >= 4 observations, early_melt_share >= 0.6, "
          "median anomaly <= -0.10 and sign_p <= 0.1 (ties within 0.005 excluded). Apron = mapped driveway lines (only where block files hold them) "
          "buffered 1.5 m, compared with the median of open ground pixels (no roof).")

LIMITS = [
    "A 20 m pixel is 400 m2; a typical house roof (100-250 m2) fills a quarter to a half of one pixel, so every roof "
    "value is mixed with yard, street, trees and neighbouring roofs ('mixed': true).",
    "Neighbouring houses often share the same pixels, which pulls their values together and hides real differences.",
    "Faster snow loss also comes from steep or south-facing pitches sliding/sun-melting, dark roofing, roof wind "
    "scour, shovelling, or trees/shadows in the pixel; heat loss is only one possible reason.",
    "Driveways mapped as lines are ~3 m wide: far below one pixel. A clear driveway is most often shovelled or "
    "ploughed, not heated.",
    "Snowfall is a station measurement up to tens of km away; local totals differ.",
    "Few qualifying scenes per winter (clouds follow storms), so observation counts are small.",
]


# ----------------------------------------------------------------------------------------- helpers
def http_json(url, params=None, timeout=120):
    for i in range(4):
        try:
            r = S.get(url, params=params, timeout=timeout, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 404):
                return None
        except Exception as e:  # noqa: BLE001
            err = e
        import time
        time.sleep(3 * (i + 1))
    return None


def cached(path: Path, fn):
    if path.exists():
        return json.loads(path.read_text())
    v = fn()
    if v is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(v))
    return v


def hav_km(lon1, lat1, lon2, lat2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


# ----------------------------------------------------------------------------------------- stations / events
def stations_near(lon, lat, ck: Path):
    def fetch():
        for pad in (0.15, 0.35):
            bbox = f"{lat + pad},{lon - pad},{lat - pad},{lon + pad}"
            j = http_json(NCEI_SEARCH, {"dataset": "daily-summaries", "bbox": bbox, "dataTypes": "SNOW",
                                        "startDate": START + "T00:00:00", "endDate": END + "T00:00:00", "limit": 200})
            out = []
            for r in (j or {}).get("results", []):
                st = r["stations"][0]
                snow = next((d for d in st.get("dataTypes", []) if d["id"] == "SNOW"), None)
                if not snow or snow["endDate"][:10] < "2019-06-01":
                    continue
                c = r["boundingPoints"][0]["coordinates"]
                out.append({"id": st["id"], "name": st.get("name"), "lon": c[0], "lat": c[1],
                            "snow_start": snow["startDate"][:10], "snow_end": snow["endDate"][:10],
                            "km": round(hav_km(lon, lat, c[0], c[1]), 1)})
            if len(out) >= 2:
                return sorted(out, key=lambda s: s["km"])
        return []
    return cached(ck / "stations.json", fetch)


def station_daily(st_ids, ck: Path):
    def fetch():
        j = http_json(NCEI_DATA, {"dataset": "daily-summaries", "stations": ",".join(st_ids), "dataTypes": "SNOW,SNWD",
                                  "startDate": START, "endDate": END, "format": "json", "units": "metric"}, timeout=300)
        return j
    rows = cached(ck / ("daily_" + "_".join(st_ids) + ".json"), fetch) or []
    by = {}
    for r in rows:
        d = by.setdefault(r["STATION"], {}).setdefault(r["DATE"][:10], {})
        for k in ("SNOW", "SNWD"):
            if r.get(k) not in (None, ""):
                try:
                    d[k] = float(r[k])
                except ValueError:
                    pass
    return by


def find_events(stations, by):
    """Merged per-day snowfall: nearest station that reported that day."""
    order = [s["id"] for s in stations if s["id"] in by]
    days = sorted({d for s in order for d in by[s]})
    snow, src, depth = {}, {}, {}
    for d in days:
        for s in order:
            v = by[s].get(d, {})
            if "SNOW" in v:
                snow[d], src[d] = v["SNOW"], s
                if "SNWD" in v:
                    depth[d] = v["SNWD"]
                break
    events, run = [], []
    for d in days:
        if snow.get(d, 0) >= 1:
            if run and (dt.date.fromisoformat(d) - dt.date.fromisoformat(run[-1])).days > 1:
                events.append(run); run = []
            run.append(d)
        elif run:
            events.append(run); run = []
    if run:
        events.append(run)
    out = []
    for r in events:
        tot = sum(snow[d] for d in r)
        if tot >= EVENT_MM:
            last = r[-1]
            out.append({"date": last, "start": r[0], "snow_cm": round(tot / 10, 1), "station": src[last],
                        "depth_cm_after": round(depth[last] / 10, 1) if last in depth else None})
    return out, snow


# ----------------------------------------------------------------------------------------- Sentinel-2
def stac_items(bounds, d0, d1, ck: Path):
    def fetch():
        import pystac_client
        c = pystac_client.Client.open(STAC)
        res = []
        for it in c.search(collections=["sentinel-2-l2a"], bbox=bounds, datetime=f"{d0}/{d1}",
                           query={"eo:cloud_cover": {"lt": 80}}).items():
            a = {}
            for k in ("green", "swir16", "nir", "scl"):
                if k in it.assets:
                    rb = (it.assets[k].extra_fields.get("raster:bands") or [{}])[0]
                    a[k] = {"href": it.assets[k].href, "scale": rb.get("scale"), "offset": rb.get("offset")}
            if len(a) == 4:
                res.append({"id": it.id, "date": it.datetime.date().isoformat(),
                            "cloud": it.properties.get("eo:cloud_cover"),
                            "baseline": it.properties.get("s2:processing_baseline"), "assets": a})
        return res
    return cached(ck / f"stac_{d0}_{d1}.json", fetch)


ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
           GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2")


def read_scene(item, ll_box, ck: Path):
    """Window-read the block bbox. Returns dict with 20 m arrays + transform + crs, cached as npz."""
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    f = ck / "scenes" / f"{item['id']}.npz"
    if f.exists():
        z = np.load(f, allow_pickle=False)
        return {k: z[k] for k in z.files}
    out = {}
    with rasterio.Env(**ENV):
        with rasterio.open(item["assets"]["swir16"]["href"]) as src:
            b = transform_bounds("EPSG:4326", src.crs, *ll_box)
            w = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
            w = w.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
            tr20 = src.window_transform(w)
            out["b11"] = src.read(1, window=w).astype("f4")
            crs = src.crs.to_wkt()
        col0, row0, ncol, nrow = int(w.col_off), int(w.row_off), int(w.width), int(w.height)
        with rasterio.open(item["assets"]["scl"]["href"]) as src:
            out["scl"] = src.read(1, window=w).astype("u1")
        w10 = rasterio.windows.Window(col0 * 2, row0 * 2, ncol * 2, nrow * 2)
        for k, key in (("b03", "green"), ("b08", "nir")):
            with rasterio.open(item["assets"][key]["href"]) as src:
                a = src.read(1, window=w10, boundless=True, fill_value=0).astype("f4")
            a = a.reshape(nrow, 2, ncol, 2)
            nz = (a > 0).all(axis=(1, 3))
            out[k] = np.where(nz, a.mean(axis=(1, 3)), 0).astype("f4")
    out["tr"] = np.array(tr20[:6], "f8")
    out["crs"] = np.array(crs)
    f.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, **out)
    return out


def refl(dn, a):
    sc = a.get("scale") or 1e-4
    off = a.get("offset")
    if off is None:
        off = 0.0
    r = dn * sc + off
    return np.where(dn > 0, r, np.nan)


# ----------------------------------------------------------------------------------------- geometry
def local_to_crs(meta, crs_wkt):
    from pyproj import Transformer
    lon, lat = meta["origin"]
    cx, cy, k, _ = block_box_3857(lon, lat, meta.get("half_m", 50))
    t = Transformer.from_crs(3857, crs_wkt, always_xy=True)

    def f(pts):
        a = np.asarray(pts, float)[:, :2]
        x, y = t.transform(cx + a[:, 0] * k, cy + a[:, 1] * k)
        return np.column_stack([x, y])
    return f


def pixel_weights(geom, tr, shape):
    """Exact area of geom inside each 20 m pixel (m2)."""
    from shapely.geometry import box
    a, b, c, d, e, f = tr
    w = np.zeros(shape, "f8")
    if geom.is_empty:
        return w
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(0, int((minx - c) // a)); c1 = min(shape[1] - 1, int((maxx - c) // a))
    r0 = max(0, int((maxy - f) // e)); r1 = min(shape[0] - 1, int((miny - f) // e))
    for r in range(r0, r1 + 1):
        for cc in range(c0, c1 + 1):
            x0, y1 = c + cc * a, f + r * e
            px = box(x0, y1 + e, x0 + a, y1)
            w[r, cc] = geom.intersection(px).area
    return w


def summarize(ws, pix_area):
    tot = ws.sum()
    return {"area_m2": round(float(tot), 1), "pixels": round(float(tot / pix_area), 2),
            "purity": round(float((ws ** 2).sum() / tot / pix_area), 2) if tot else 0,
            "n_pixels_touched": int((ws > 0.5).sum())}


# ----------------------------------------------------------------------------------------- stats
def sign_p(k, n):
    """One-sided P(X >= k), X ~ Bin(n, 0.5)."""
    if n == 0:
        return None
    return round(sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n, 4)


def judge(anoms, kind, mixed):
    obs = len(anoms)
    if obs == 0:
        return {"obs": 0, "early_melt_share": None, "median_anomaly": None, "sign_p": None, "flag": "insufficient data"}
    a = np.array(anoms)
    early = int((a < -EARLY).sum())
    share = early / obs
    med = float(np.median(a))
    nz = a[np.abs(a) > 0.005]           # sign test ignores ties (e.g. every pixel saturated at 1.0)
    p = sign_p(int((nz < 0).sum()), len(nz))
    if kind == "roof":
        hit = obs >= 4 and share >= 0.6 and med <= -EARLY and p is not None and p <= 0.1
        flag = "faster-melting roof (possible heat loss)" if hit else ("normal" if obs >= 4 else "insufficient data")
    else:
        hit = obs >= 4 and share >= 0.6 and med <= -2 * EARLY and p is not None and p <= 0.1 and not mixed
        flag = ("stays clear while surroundings are white (possibly heated or simply cleared paving)" if hit
                else ("normal" if obs >= 4 and not mixed else "insufficient data"))
    return {"obs": obs, "early_melt_share": round(share, 2), "median_anomaly": round(med, 3),
            "sign_p": p, "flag": flag}


# ----------------------------------------------------------------------------------------- per block
def process(slug):
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    d = BLOCKS / slug
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    houses = json.loads((d / "houses.json").read_text(encoding="utf-8"))
    ck = CACHE / slug
    lon, lat = meta["origin"]
    W, Sb, E, N = meta["bounds"]
    pad_lon, pad_lat = 40 / (111320 * math.cos(math.radians(lat))), 40 / 110540
    ll_box = (W - pad_lon, Sb - pad_lat, E + pad_lon, N + pad_lat)

    stations = stations_near(lon, lat, ck)[:3]
    by = station_daily([s["id"] for s in stations], ck) if stations else {}
    events, daily = find_events(stations, by)
    print(f"[{slug}] stations {[ (s['id'], s['km']) for s in stations]} events {len(events)}", flush=True)

    # driveways (only real mapped geometry)
    drives = []
    try:
        st = json.loads((d / "streets.json").read_text(encoding="utf-8"))
        for dv in st.get("driveways") or []:
            pts = [p[:2] for p in dv.get("poly") or []]
            if len(pts) >= 2:
                drives.append({"pts": pts, "source": dv.get("source")})
    except FileNotFoundError:
        pass

    # scenes per event
    ev_scenes = []
    for ev in events:
        e = dt.date.fromisoformat(ev["date"])
        d0, d1 = e + dt.timedelta(days=SCENE_DAYS[0]), e + dt.timedelta(days=SCENE_DAYS[1])
        if d0 > dt.date.today():
            ev_scenes.append((ev, [])); continue
        items = stac_items(ll_box, d0.isoformat(), d1.isoformat(), ck) or []
        keep = []
        for it in items:
            sd = dt.date.fromisoformat(it["date"])
            fresh = sum(daily.get((e + dt.timedelta(days=i)).isoformat(), 0) for i in range(1, (sd - e).days + 1))
            if fresh > FRESH_MM:
                continue
            keep.append(it)
        ev_scenes.append((ev, keep))

    todo = [it for _, its in ev_scenes for it in its]
    arrays = {}

    def load(it):
        try:
            return it["id"], read_scene(it, ll_box, ck)
        except Exception as ex:  # noqa: BLE001
            print(f"  read fail {it['id']}: {ex}", flush=True)
            return it["id"], None
    with ThreadPoolExecutor(6) as ex:
        for iid, arr in ex.map(load, todo):
            arrays[iid] = arr

    geo = None
    roofs, aprons, wts = {}, {}, {}
    house_ids = [str(h["id"]) for h in houses]
    obs_roof = {h: [] for h in house_ids}
    obs_apron = {h: [] for h in house_ids}
    out_events, rejected = [], {"cloudy": 0, "no_snow_left": 0, "read_fail": 0, "fresh_snow": 0, "duplicate_date": 0}
    seen_dates = set()
    for ev, its in ev_scenes:
        used = []
        # one scene per date: prefer the tile with the most valid pixels
        best = {}
        for it in its:
            arr = arrays.get(it["id"])
            if arr is None:
                rejected["read_fail"] += 1; continue
            valid = float((arr["scl"] > 0).mean()) if arr["scl"].size else 0
            if it["date"] not in best or valid > best[it["date"]][0]:
                if it["date"] in best:
                    rejected["duplicate_date"] += 1
                best[it["date"]] = (valid, it, arr)
            else:
                rejected["duplicate_date"] += 1
        for date in sorted(best):
            if date in seen_dates:
                continue
            _, it, arr = best[date]
            tr = tuple(arr["tr"]); crs = str(arr["crs"]); shape = arr["scl"].shape
            key = (crs, tr[0], tr[2] % 20, tr[5] % 20, shape)
            # geometry weights per crs/grid
            gk = (crs, tr)
            if gk not in wts:
                f = local_to_crs(meta, crs)
                hw, aw = {}, {}
                for h in houses:
                    try:
                        poly = Polygon(f(h["ring"])).buffer(0)
                    except Exception:  # noqa: BLE001
                        continue
                    hw[str(h["id"])] = pixel_weights(poly, tr, shape)
                    roofs[str(h["id"])] = poly
                allroof = unary_union(list(roofs.values())) if roofs else None
                for dv in drives:
                    ln = LineString(f(dv["pts"]))
                    # nearest house within 12 m
                    best_h, best_d = None, 12.0
                    for hid, poly in roofs.items():
                        dd = poly.distance(ln)
                        if dd < best_d:
                            best_h, best_d = hid, dd
                    if best_h is None:
                        continue
                    g = ln.buffer(1.5)
                    if allroof is not None:
                        g = g.difference(allroof)
                    aw[best_h] = aw.get(best_h, 0) + pixel_weights(g, tr, shape)
                # block footprint (block square in this crs) + roof-free ground
                sq = Polygon(f([[-meta["half_m"], -meta["half_m"]], [meta["half_m"], -meta["half_m"]],
                                [meta["half_m"], meta["half_m"]], [-meta["half_m"], meta["half_m"]]]))
                bw = pixel_weights(sq, tr, shape)
                rw = sum(hw.values()) if hw else np.zeros(shape)
                wts[gk] = (hw, aw, bw, rw)
            hw, aw, bw, rw = wts[gk]
            a = it["assets"]
            g3, s11, n8 = refl(arr["b03"], a["green"]), refl(arr["b11"], a["swir16"]), refl(arr["b08"], a["nir"])
            with np.errstate(invalid="ignore", divide="ignore"):
                ndsi = (g3 - s11) / (g3 + s11)
            scl = arr["scl"]
            fsc = np.clip(0.06 + 1.21 * ndsi, 0, 1)
            fsc = np.where(scl == 6, 0, fsc)  # water
            bad = np.isin(scl, list(BAD_SCL)) | ~np.isfinite(fsc)
            inblock = bw > 0.5 * 400
            clear = 1 - (bad & inblock).sum() / max(1, inblock.sum())
            if clear < CLEAR_MIN:
                rejected["cloudy"] += 1; continue
            med_block = float(np.nanmedian(np.where(bad | ~inblock, np.nan, fsc)))
            snow11 = float(((scl == 11) & inblock).sum() / max(1, inblock.sum()))
            if not (med_block >= SNOW_PRESENT or snow11 >= 0.2):
                rejected["no_snow_left"] += 1; continue
            seen_dates.add(date)
            used.append(date)

            def wmean(w):
                ok = (~bad) & (w > 0)
                if w.sum() == 0 or w[ok].sum() < 0.8 * w.sum():
                    return None
                return float((fsc[ok] * w[ok]).sum() / w[ok].sum())
            vals = {hid: wmean(w) for hid, w in hw.items()}
            for hid, v in vals.items():
                if v is None:
                    continue
                others = [vals[o] for o in vals if o != hid and vals[o] is not None]
                if len(others) < 2:
                    continue
                obs_roof[hid].append({"date": date, "snow": round(v, 3), "ref": round(float(np.median(others)), 3),
                                      "anomaly": round(v - float(np.median(others)), 3)})
            # open ground reference for aprons: pixels <10% roof
            ground = (~bad) & inblock & (rw < 40)
            gref = float(np.median(fsc[ground])) if ground.sum() >= 3 else None
            for hid, w in aw.items():
                v = wmean(w)
                if v is None or gref is None:
                    continue
                obs_apron[hid].append({"date": date, "snow": round(v, 3), "ref": round(gref, 3),
                                       "anomaly": round(v - gref, 3)})
        out_events.append({"date": ev["date"], "start": ev["start"], "snow_cm": ev["snow_cm"], "station": ev["station"],
                           "depth_cm_after": ev["depth_cm_after"], "scenes": used,
                           "candidates": len(its)})

    any_w = next(iter(wts.values()), None)
    out_houses = []
    for h in houses:
        hid = str(h["id"])
        notes = []
        rs = summarize(any_w[0][hid], 400) if any_w and hid in any_w[0] else None
        mixed = bool(rs is None or rs["pixels"] < 1 or rs["purity"] < 0.5)
        roof = judge([o["anomaly"] for o in obs_roof[hid]], "roof", mixed)
        roof.update({"pixels": rs["pixels"] if rs else None, "purity": rs["purity"] if rs else None,
                     "pixel_m": 20, "mixed": mixed, "series": obs_roof[hid]})
        if len(houses) < 3:
            notes.append("fewer than 3 houses in the block: no roof-to-roof comparison possible")
        if mixed:
            notes.append(f"roof covers {rs['pixels'] if rs else 0} of a 20 m pixel; value is mixed with surroundings")
        if roof["flag"].startswith("faster"):
            notes.append("observed: this roof's pixel lost snow sooner than the neighbouring roofs' after "
                         f"{roof['obs']} storms/scenes; causes can be heat loss, roof pitch/aspect, dark roofing, "
                         "shovelling or pixel mixing - an inspection lead, not a finding")
        apron = None
        if any_w and hid in any_w[1]:
            asum = summarize(any_w[1][hid], 400)
            amixed = True if asum["pixels"] < 1 else asum["purity"] < 0.5
            apron = judge([o["anomaly"] for o in obs_apron[hid]], "apron", amixed)
            apron.update({"source": "mapped driveway line (" + (drives[0]["source"] or "block streets.json") + "), buffered 1.5 m",
                          "pixels": asum["pixels"], "purity": asum["purity"], "pixel_m": 20, "mixed": amixed,
                          "series": obs_apron[hid]})
            notes.append(f"driveway area {asum['area_m2']} m2 = {asum['pixels']} of a 20 m pixel: too small to resolve")
        out_houses.append({"house_id": hid, "address": h.get("address"), "roof": roof, "apron": apron, "notes": notes})

    res = {"slug": slug, "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00"), "version": 1,
           "sources": SOURCES, "stations": stations,
           "events": out_events,
           "scene_rejects": rejected,
           "houses": out_houses, "method": METHOD, "limits": LIMITS,
           "summary": {"events": len(out_events), "events_with_scenes": sum(1 for e in out_events if e["scenes"]),
                       "scenes_used": len(seen_dates),
                       "houses": len(out_houses),
                       "flagged": sum(1 for x in out_houses if x["roof"]["flag"].startswith("faster")),
                       "aprons": sum(1 for x in out_houses if x["apron"]),
                       "median_roof_pixels": (float(np.median([x["roof"]["pixels"] for x in out_houses if x["roof"]["pixels"] is not None]))
                                              if any(x["roof"]["pixels"] is not None for x in out_houses) else None)}}
    (d / "snowmelt.json").write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
    print(f"[{slug}] {res['summary']} rejects {rejected}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--snowy", action="store_true")
    a = ap.parse_args()
    slugs = a.slugs or (SNOWY if a.snowy else [])
    for s in slugs:
        if not (BLOCKS / s / "houses.json").exists():
            print("skip", s); continue
        try:
            process(s)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"[{s}] FAILED {e}")


if __name__ == "__main__":
    main()

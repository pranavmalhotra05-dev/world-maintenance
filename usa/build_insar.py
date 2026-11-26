"""Build web/app/data/insar.json: measured radar ground motion (InSAR) at 30 m pixels around every
built block (web/usa/blocks/index.json), from NASA/JPL OPERA L3 DISP-S1 (Sentinel-1 surface
displacement, collection OPERA_L3_DISP-S1_V1, CMR C3294057315-ASF, doi:10.5067/SNWG/OPL3DISPS1-V1).

Source / access (no login): the ASF DAAC displacement time-series service that backs the public
ASF Vertex displacement tool (search.asf.alaska.edu), found in its JS bundle as `displacement_api`:
  POST https://d2qmcvu7qty7vn.cloudfront.net/timeseries
       {"wkt":"POINT(lon lat)","bucket":"asf-cumulus-prod-opera-products","polarization":"VV",
        "flightDirection":"ASCENDING"|"DESCENDING"}
It returns, for the 30 m DISP-S1 pixel containing the point, one row per DISP-S1 granule:
short_wavelength_displacement (metres, radar line-of-sight, already chained to the first epoch of
the frame), reference/secondary datetimes, pixel UTM x/y, is_masked, and the source granule.
(The .nc granules themselves need an Earthdata Login and are NOT used. POLYGON queries fail
server-side, so only point queries are made.)

Sampling: a 3x3 lattice per block, centre and +-STEP deg (default 0.005 deg ~ 500 m) in lon/lat,
for both flight directions. Each lattice point resolves to one real 30 m pixel; every returned
epoch of that pixel is kept (masked epochs kept and flagged). Nothing is interpolated or smoothed.
The only derived number is `rate_mm_yr`, an ordinary least-squares slope through the kept,
unmasked epochs of that pixel (labelled as computed here, not a product field).

Licence: Earthdata data use guidance: "Unless the content is marked with a use restriction or
license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."
The Earthdata catalog entry says the dataset "is openly shared, without restriction". The product
"contains modified Copernicus Sentinel data" (Copernicus terms: free, full and open, incl. commercial).
Responses are cached in usa/cache/insar/ so re-runs resume. Runtime cap: --minutes (default 30).

Usage: .venv\\Scripts\\python usa\\build_insar.py [--minutes 30] [--step 0.005] [--workers 6]
"""
import argparse
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "cache", "insar")
BLOCKS = os.path.join(ROOT, "web", "usa", "blocks", "index.json")
OUT = os.path.join(ROOT, "web", "app", "data", "insar.json")
API = "https://d2qmcvu7qty7vn.cloudfront.net/timeseries"
BUCKET = "asf-cumulus-prod-opera-products"
UA = {"User-Agent": "roof-intel-builder/1.0", "Content-Type": "application/json"}
DIRS = ("ASCENDING", "DESCENDING")
SKIP = {"slc-ut-test"}


def post_json(body, timeout=120, tries=3):
    err = None
    data = json.dumps(body).encode("utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=data, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")[:300]
            if 400 <= e.code < 500 or e.code == 500:  # service answers "no data here" with 4xx/500
                return {"_error": e.code, "detail": txt}
            err = e
        except Exception as e:
            err = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"timeseries {body['wkt']} {body['flightDirection']}: {err}")


def cached(name, body):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    j = post_json(body)
    if isinstance(j, dict) and j.get("_error", 0) >= 500:
        return j  # not cached: may be transient
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, separators=(",", ":"))
    os.replace(tmp, path)
    return j


_tf = {}


def pixel_lonlat(x, y, lon, lat):
    """UTM pixel centre -> lon/lat. The frame's UTM zone is not returned, so try the point's zone
    and its neighbours and keep the one that lands within 60 m of the queried point."""
    z0 = int((lon + 180) // 6) + 1
    best = None
    for z in (z0, z0 - 1, z0 + 1):
        epsg = (32600 if lat >= 0 else 32700) + z
        if epsg not in _tf:
            _tf[epsg] = Transformer.from_crs(epsg, 4326, always_xy=True)
        plon, plat = _tf[epsg].transform(x, y)
        d = ((plon - lon) * 111320 * max(0.1, abs(__import__("math").cos(lat * 3.14159265 / 180)))) ** 2 + ((plat - lat) * 110540) ** 2
        if best is None or d < best[0]:
            best = (d, plon, plat, epsg)
    return best[1], best[2], best[3], best[0] ** 0.5


def ols_rate(ts, vs):
    n = len(ts)
    if n < 3:
        return None
    mt = sum(ts) / n
    mv = sum(vs) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in zip(ts, vs)) / sxx


def to_years(iso):
    t = time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    return t.tm_year + (t.tm_yday - 1 + t.tm_hour / 24) / 365.25


def parse(resp, lon, lat):
    """-> (frame, epsg, pixel lon/lat, ref date, rows[(date, mm, masked, granule)]) or None."""
    if not isinstance(resp, dict) or "_error" in resp or "detail" in resp:
        return None
    rows, frame, x, y, ref = [], None, None, None, None
    for k, v in resp.items():
        if not isinstance(v, dict) or v.get("secondary_datetime") is None:
            continue  # the service's own "mean" summary row is skipped
        frame = k.split("_")[4]
        x, y, ref = v["x"], v["y"], v["reference_datetime"]
        d = v["short_wavelength_displacement"]
        rows.append((v["secondary_datetime"], None if d is None else d * 1000.0, bool(v["is_masked"]), k))
    if not rows:
        return None
    rows.sort()
    plon, plat, epsg, off = pixel_lonlat(x, y, lon, lat)
    return frame, epsg, x, y, plon, plat, off, ref, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--step", type=float, default=0.005)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    with open(BLOCKS, encoding="utf-8") as f:
        idx = json.load(f)
    blocks = [b for b in (idx if isinstance(idx, list) else idx.get("blocks", [])) if b["slug"] not in SKIP]

    jobs = []
    for b in blocks:
        for j in (-1, 0, 1):
            for i in (-1, 0, 1):
                lon = round(b["lon"] + i * a.step, 6)
                lat = round(b["lat"] + j * a.step, 6)
                for d in DIRS:
                    jobs.append((b["slug"], i, j, lon, lat, d))
    # centre points first so a time-capped run still covers every block
    jobs.sort(key=lambda t: (abs(t[1]) + abs(t[2]), t[0]))

    t0 = time.time()
    deadline = t0 + a.minutes * 60
    stop = threading.Event()
    res, lock, done = {}, threading.Lock(), [0]

    def fetch(job):
        slug, i, j, lon, lat, d = job
        name = f"{slug}_{i:+d}{j:+d}_{d[0]}.json"
        if stop.is_set() or (time.time() > deadline and not os.path.exists(os.path.join(CACHE, name))):
            return job, "skipped"
        body = {"wkt": f"POINT({lon} {lat})", "bucket": BUCKET, "polarization": "VV", "flightDirection": d}
        return job, cached(name, body)

    def run(batch):
      with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(fetch, jb) for jb in batch]
        for fu in as_completed(futs):
            try:
                job, r = fu.result()
            except Exception as e:
                print("ERR", e)
                continue
            with lock:
                res[job] = r
                done[0] += 1
                if done[0] % 25 == 0:
                    print(f"{done[0]}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)

    # phase 1: block centres; phase 2: the lattice, only for directions whose centre pixel had data
    # (a "no data" answer at the centre means no DISP-S1 frame in that direction covers the block)
    centre = [jb for jb in jobs if jb[1] == 0 and jb[2] == 0]
    run(centre)
    has = {(jb[0], jb[5]) for jb in centre if isinstance(res.get(jb), dict) and "_error" not in res[jb]}
    ring = [jb for jb in jobs if not (jb[1] == 0 and jb[2] == 0)]
    run([jb for jb in ring if (jb[0], jb[5]) in has])
    for jb in ring:
        if (jb[0], jb[5]) not in has:
            res[jb] = {"_error": 400, "detail": "not queried: centre pixel had no data in this direction"}

    out_blocks, counts = {}, {}
    frames = {}
    for b in blocks:
        slug = b["slug"]
        pts, nskip, nnone = [], 0, 0
        for job in [jb for jb in jobs if jb[0] == slug]:
            r = res.get(job)
            if r is None or r == "skipped":
                nskip += 1
                continue
            p = parse(r, job[3], job[4])
            if p is None:
                nnone += 1
                continue
            frame, epsg, x, y, plon, plat, off, ref, rows = p
            fk = f"{frame}_{job[5][0]}"
            fr = frames.setdefault(fk, {"frame": frame, "flightDirection": job[5], "epsg": epsg, "reference": ref[:19]})
            dates = [rw[0][:10].replace("-", "") for rw in rows]
            mm = [None if rw[1] is None else round(rw[1], 2) for rw in rows]
            masked = [k for k, rw in enumerate(rows) if rw[2]]
            good = [(to_years(rw[0]), rw[1]) for rw in rows if not rw[2] and rw[1] is not None]
            rate = ols_rate([g[0] for g in good], [g[1] for g in good])
            pts.append([round(plon, 6), round(plat, 6), fk, round(x, 1), round(y, 1),
                        None if rate is None else round(rate, 2), len(rows), dates[0], dates[-1],
                        dates, mm, masked])
        if pts:
            out_blocks[slug] = pts
        counts[slug] = {"series": len(pts), "noData": nnone, "notFetched": nskip,
                        "epochs": sum(p[6] for p in pts)}

    out = {
        "source": "NASA/JPL OPERA L3 DISP-S1 v1 (Sentinel-1 surface displacement, 30 m), via the ASF DAAC "
                  "displacement time-series service (no login)",
        "product": "OPERA_L3_DISP-S1_V1 (CMR C3294057315-ASF), doi:10.5067/SNWG/OPL3DISPS1-V1",
        "api": API,
        "measure": "short_wavelength_displacement, radar line-of-sight (LOS), millimetres, relative to the "
                   "frame's first epoch (frames[].reference); positive = toward the satellite. "
                   "Not vertical: ascending and descending LOS differ.",
        "sampling": f"3x3 lattice per block at +-{a.step} deg, both flight directions; each point is one real "
                    "30 m pixel (lon/lat = that pixel's centre); every epoch kept, masked epochs flagged",
        "fields": ["pixel_lon", "pixel_lat", "frameKey", "utm_x", "utm_y",
                   "rate_mm_yr|null (OLS slope of unmasked epochs, computed here)", "n_epochs",
                   "first_date", "last_date", "dates[YYYYMMDD]", "los_mm[]", "masked_epoch_indices[]"],
        "uncertainty": "not provided by the time-series service (per-granule quality layers are only in the "
                       "login-gated .nc files)",
        "frames": frames,
        "licence": "CC0 (NASA-led mission data, no use restriction marked; contains modified Copernicus Sentinel data)",
        "licenceUrl": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
        "attribution": "OPERA DISP-S1: NASA/JPL/OPERA, ASF DAAC (doi:10.5067/SNWG/OPL3DISPS1-V1); "
                       "contains modified Copernicus Sentinel data",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": counts,
        "blocks": out_blocks,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes;", sum(c["series"] for c in counts.values()), "series")
    for s, c in counts.items():
        print(f"  {s:22s} {c}")


if __name__ == "__main__":
    main()

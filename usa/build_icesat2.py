"""Build web/app/data/icesat2.json: ICESat-2 ATL08 ground (h_te_best_fit) and canopy heights
in a ~+-0.05 deg box around every built block (web/usa/blocks/index.json).

Source: NSIDC OpenAltimetry API (no login), queried server-side.
  tracks : /data/api/icesat2/getTracks?minx&miny&maxx&maxy           (RGTs crossing the box)
  dates  : /data/api/v2/icesat2/collections/atl08/tracks/{rgt}/dates (every date that RGT has data)
  data   : /data/api/icesat2/atl08?date&minx&miny&maxx&maxy&trackId&outputFormat=json
Every returned 100 m segment is kept. The API repeats some beams verbatim (e.g. gt1l == gt3r),
so exact duplicate rows (same date, lat, lon, height) are collapsed; nothing else is dropped.
Canopy fill 3.4028235e38 -> null. Canopy is ATL08 h_canopy (height above ground, m).

Licence: Earthdata data use guidance: "Unless the content is marked with a use restriction or
license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."
OpenAltimetry adds only a citation request (https://nsidc.org/openaltimetry/cite).
Responses are cached in usa/cache/icesat2/ so re-runs resume. Runtime cap: --minutes (default 20).

Usage: .venv\\Scripts\\python usa\\build_icesat2.py [--minutes 20] [--pad 0.05]
"""
import argparse
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "cache", "icesat2")
BLOCKS = os.path.join(ROOT, "web", "usa", "blocks", "index.json")
OUT = os.path.join(ROOT, "web", "app", "data", "icesat2.json")
API = "https://openaltimetry.earthdatacloud.nasa.gov/data/api"
UA = {"User-Agent": "roof-intel-builder/1.0"}
FILL = 1e30


def get_json(url, timeout=180, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def cached(name, url):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    j = get_json(url)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, separators=(",", ":"))
    os.replace(tmp, path)
    return j


def bbox(b, pad):
    return (round(b["lon"] - pad, 4), round(b["lat"] - pad, 4), round(b["lon"] + pad, 4), round(b["lat"] + pad, 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--pad", type=float, default=0.05)
    ap.add_argument("--discover-pad", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    for d in ("tracks", "dates", "atl08"):
        os.makedirs(os.path.join(CACHE, d), exist_ok=True)
    blocks = json.load(open(BLOCKS, encoding="utf-8"))
    t0 = time.time()

    # 1. tracks per block, 2. dates per track
    jobs = {}  # slug -> list of (date, rgt)
    boxes = {}
    for b in blocks:
        box = boxes[b["slug"]] = bbox(b, a.pad)
        q = "minx={}&miny={}&maxx={}&maxy={}".format(*box)
        tr = cached(f"tracks/{b['slug']}.json", f"{API}/icesat2/getTracks?{q}&outputFormat=json")
        rgts = tr.get("output", {}).get("track", []) or []
        if not rgts:
            # getTracks tests the nominal RGT line only; real beams are off-pointed by several km,
            # so look for RGTs in a wider box but still request data for the small box.
            wb = bbox(b, a.discover_pad)
            wq = "minx={}&miny={}&maxx={}&maxy={}".format(*wb)
            tr = cached(f"tracks/{b['slug']}_wide.json", f"{API}/icesat2/getTracks?{wq}&outputFormat=json")
            rgts = tr.get("output", {}).get("track", []) or []
        lst = []
        for rgt in rgts:
            ds = cached(f"dates/{rgt}.json", f"{API}/v2/icesat2/collections/atl08/tracks/{rgt}/dates")
            lst += [(d, rgt) for d in ds.get("dates", [])]
        lst.sort(reverse=True)  # newest first
        jobs[b["slug"]] = lst
        print(f"  {b['slug']}: tracks {rgts}, {len(lst)} track-dates", flush=True)

    # round-robin across blocks so coverage stays even if the time cap bites
    order, i = [], 0
    while any(i < len(v) for v in jobs.values()):
        for slug, v in jobs.items():
            if i < len(v):
                order.append((slug, *v[i]))
        i += 1
    print(f"{len(order)} atl08 requests planned ({time.time() - t0:.0f}s so far)", flush=True)

    deadline = t0 + a.minutes * 60
    stop = threading.Event()

    def fetch(slug, date, rgt):
        name = f"atl08/{slug}_{rgt}_{date}.json"
        if os.path.exists(os.path.join(CACHE, name)):
            return slug, date, rgt, cached(name, None)
        if stop.is_set() or time.time() > deadline:
            stop.set()
            return slug, date, rgt, None
        minx, miny, maxx, maxy = boxes[slug]
        url = (f"{API}/icesat2/atl08?date={date}&minx={minx}&miny={miny}&maxx={maxx}&maxy={maxy}"
               f"&trackId={rgt}&outputFormat=json")
        try:
            return slug, date, rgt, cached(name, url)
        except Exception as e:
            print("   fail", name, e, flush=True)
            return slug, date, rgt, None

    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(fetch, *o) for o in order]
        for f in as_completed(futs):
            slug, date, rgt, j = f.result()
            if j is not None:
                results[(slug, date, rgt)] = j
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(order)} done, {len(results)} fetched, {time.time() - t0:.0f}s", flush=True)

    # 3. assemble
    pts, seen = [], set()
    cov = {b["slug"]: {"requests": len(jobs[b["slug"]]), "fetched": 0, "withData": 0, "points": 0} for b in blocks}
    for (slug, date, rgt), j in sorted(results.items()):
        c = cov[slug]
        c["fetched"] += 1
        n0 = len(pts)
        for s in j.get("series", []) or []:
            beam = s.get("beam", "")
            for row in s.get("lat_lon_elev_canopy", []) or []:
                if len(row) < 3 or row[2] is None or abs(row[2]) > FILL:
                    continue
                lat, lon, h = round(row[0], 6), round(row[1], 6), round(row[2], 1)
                key = (date, rgt, lat, lon, h)
                if key in seen:
                    continue
                seen.add(key)
                hc = row[3] if len(row) > 3 else None
                hc = None if hc is None or abs(hc) > FILL else round(hc, 1)
                pts.append([lon, lat, h, hc, date, rgt, beam])
        if len(pts) > n0:
            c["withData"] += 1
            c["points"] += len(pts) - n0
    # heights to NAVD88 (GEOID18), the datum of USGS 3DEP LiDAR, so laser points sit on the same ground
    # (PROJ fetches the NOAA GEOID18 grid, US public domain, from cdn.proj.org on first use)
    from pyproj import Transformer, network
    network.set_network_enabled(True)
    tr = Transformer.from_crs("EPSG:7912", "EPSG:6318+5703", always_xy=True)
    if pts:
        _, _, hz = tr.transform([p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts])
        bad = 0
        for p, h in zip(pts, hz):
            if h == h and abs(h) < 1e5: p[2] = round(float(h), 1)
            else: bad += 1
        print(f"NAVD88 conversion: {len(pts) - bad} converted, {bad} outside the GEOID18 grid (left as ellipsoid height)")
    doc = {
        "source": "NASA ICESat-2 ATL08 (land and vegetation height) via NSIDC OpenAltimetry",
        "fields": ["lon", "lat", "h_te_best_fit_m", "h_canopy_m_above_ground|null", "date", "rgt", "beam"],
        "heights": "metres, NAVD88 orthometric (GEOID18) converted from the ITRF2014/WGS84 ellipsoid h_te; canopy = height above terrain",
        "licence": "CC0 (NASA-led mission data; no restrictions on use, citation requested)",
        "licenceUrl": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
        "attribution": "ICESat-2 ATL08: NASA / NSIDC DAAC, accessed via OpenAltimetry "
                       "(Khalsa et al. 2020, Earth Sci Inform, https://nsidc.org/openaltimetry/cite)",
        "boxPadDeg": a.pad,
        "built": time.strftime("%Y-%m-%d"),
        "coverage": cov,
        "points": pts,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    planned = len(order)
    print(f"wrote {OUT}: {len(pts)} points, {len(results)}/{planned} requests fetched, "
          f"{os.path.getsize(OUT) / 1e6:.1f} MB, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

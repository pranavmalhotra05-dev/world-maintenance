"""Build web/app/data/gnss.json: GNSS station velocities (US + neighbours) in NAM14.

Source: EarthScope Consortium / NSF GAGE Facility no-login web services.
  station list : https://web-services.unavco.org/gps/metadata/sites/v1?<bbox>
  velocity     : https://web-services.unavco.org/gps/data/velocity/{STA}/beta
                 ?analysisCenter=cwu&referenceFrame=nam14&product=final
(The bulk .vel files on data.earthscope.org need a login, so we call per station,
 at most 4 at a time, and cache every response under usa/cache/gnss/.)

Licence: GAGE Facility Data License (since 2025-06-12) = CC BY 4.0, commercial use allowed.
  https://www.unavco.org/data/policies_forms/data-policy/data-license.html

Usage: .venv\\Scripts\\python usa\\build_gnss.py
"""
import csv
import io
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "cache", "gnss")
OUT = os.path.join(ROOT, "web", "app", "data", "gnss.json")
BBOX = dict(minlatitude=14, maxlatitude=75, minlongitude=-180, maxlongitude=-50)
META = "https://web-services.unavco.org/gps/metadata/sites/v1?" + "&".join(f"{k}={v}" for k, v in BBOX.items())
VEL = ("https://web-services.unavco.org/gps/data/velocity/{sta}/beta"
       "?analysisCenter=cwu&referenceFrame=nam14&product=final")
UA = {"User-Agent": "roof-intel-builder/1.0"}


def get(url, timeout=60, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 4xx, or a 500 that persists, means "no velocity for this station"
            if e.code in (400, 404, 422) or (e.code == 500 and i >= 1):
                return e.code, body
            err = e
        except Exception as e:  # network hiccup
            err = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def station_ids():
    path = os.path.join(CACHE, "sites_meta.csv")
    if not os.path.exists(path):
        _, txt = get(META, timeout=300)
        open(path, "w", encoding="utf-8").write(txt)
    txt = open(path, encoding="utf-8").read()
    lines = [l for l in txt.splitlines() if l and not l.startswith("#")]
    ids = sorted({l.split(",", 1)[0].strip() for l in lines if l.split(",", 1)[0].strip()})
    return ids


def fetch_vel(sta):
    path = os.path.join(CACHE, "vel", f"{sta}.txt")
    if os.path.exists(path):
        return sta, open(path, encoding="utf-8").read()
    code, txt = get(VEL.format(sta=sta))
    if code != 200:
        txt = f"#NONE {code} {txt[:200]}"
    open(path, "w", encoding="utf-8").write(txt)
    return sta, txt


def parse(sta, txt):
    if txt.startswith("#NONE") or txt.lstrip().startswith("{"):
        return None
    rows = [l for l in txt.splitlines() if l and not l.startswith("#")]
    if len(rows) < 2:
        return None
    rd = csv.reader(io.StringIO("\n".join(rows[1:])))
    for r in rd:
        try:
            lat, lon = float(r[2]), float(r[3])
            vn, ve, vu = float(r[5]), float(r[6]), float(r[7])
        except (ValueError, IndexError):
            continue
        if lon > 180:
            lon -= 360
        last = (r[9] if len(r) > 9 else "")[:10]
        return [r[0].strip() or sta, round(lon, 6), round(lat, 6),
                round(ve * 1000, 1), round(vn * 1000, 1), round(vu * 1000, 1), last]
    return None


def main():
    os.makedirs(os.path.join(CACHE, "vel"), exist_ok=True)
    ids = station_ids()
    print(f"{len(ids)} stations in bbox", flush=True)
    out, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (sta, txt) in enumerate(ex.map(fetch_vel, ids)):
            rec = parse(sta, txt)
            if rec:
                out.append(rec)
            if i % 200 == 0:
                print(f"  {i}/{len(ids)}  with velocity: {len(out)}  {time.time() - t0:.0f}s", flush=True)
    out.sort(key=lambda r: r[0])
    doc = {
        "source": "EarthScope Consortium / NSF GAGE Facility, CWU analysis centre, final solution",
        "frame": "NAM14",
        "units": "mm/yr",
        "fields": ["id", "lon", "lat", "ve", "vn", "vu", "lastEpoch"],
        "licence": "CC BY 4.0 (GAGE Facility Data License)",
        "licenceUrl": "https://www.unavco.org/data/policies_forms/data-policy/data-license.html",
        "attribution": "GNSS velocities: EarthScope Consortium, NSF GAGE Facility (CC BY 4.0). "
                       "Converted to mm/yr and rounded to 0.1 mm/yr.",
        "built": time.strftime("%Y-%m-%d"),
        "stations": out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(out)} stations, {os.path.getsize(OUT) / 1e3:.0f} kB")


if __name__ == "__main__":
    sys.exit(main())

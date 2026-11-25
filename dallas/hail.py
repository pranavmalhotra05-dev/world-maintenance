"""NOAA MRMS hail (MESH = Maximum Estimated Size of Hail) for Dallas County.

Source: s3://noaa-mrms-pds (public, no account, NOAA open data - attribution requested).
Product: MESH_Max_1440min = largest hail estimated by radar at each ~1 km cell in the past 24 h,
refreshed every 30 min. One file at 12:00 UTC captures the previous local afternoon/evening storms.

  python hail.py backfill            # every day since the archive began (Oct 2020), skips days already done
  python hail.py latest              # newest 24 h file (used by the live watcher)

Each day with hail >= 0.75 in over the county is saved as a small cropped grid in data/dallas/hail/.
"""
import gzip
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import eccodes
import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
HAIL = ROOT / "data" / "dallas" / "hail"
HAIL.mkdir(parents=True, exist_ok=True)
BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "CONUS/MESH_Max_1440min_00.50"
# Dallas County plus a small margin (lon/lat)
W, E, S, N = -97.06, -96.50, 32.52, 33.03
MIN_MM = 19.0  # 0.75 inch: below this, shingle damage is unlikely
session = requests.Session()
_DECODE = threading.Lock()  # ecCodes is not thread-safe: downloads run in parallel, decoding one at a time


def decode(buf: bytes):
    """GRIB2 bytes -> (lats, lons, values) cropped to the county box."""
    raw = gzip.decompress(buf)
    with _DECODE:
        return _decode_locked(raw)


def _decode_locked(raw: bytes):
    h = eccodes.codes_new_from_message(raw)
    try:
        nx, ny = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        lat0 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
        lon0 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        dlat = eccodes.codes_get(h, "jDirectionIncrementInDegrees")
        dlon = eccodes.codes_get(h, "iDirectionIncrementInDegrees")
        vals = eccodes.codes_get_values(h).reshape(ny, nx)
    finally:
        eccodes.codes_release(h)
    lon0 = lon0 - 360 if lon0 > 180 else lon0
    lats = lat0 - np.arange(ny) * dlat  # rows run north -> south
    lons = lon0 + np.arange(nx) * dlon
    r = np.where((lats >= S) & (lats <= N))[0]
    c = np.where((lons >= W) & (lons <= E))[0]
    sub = vals[r[0]:r[-1] + 1, c[0]:c[-1] + 1].astype(np.float32)
    sub[sub < 0] = 0  # -1 / -3 = no data / no hail
    return lats[r[0]:r[-1] + 1], lons[c[0]:c[-1] + 1], sub


def key_for(day: date, hhmmss="120000"):
    d = day.strftime("%Y%m%d")
    return f"{PRODUCT}/{d}/MRMS_MESH_Max_1440min_00.50_{d}-{hhmmss}.grib2.gz"


def fetch_day(day: date):
    """Storm day = the local day before the 12:00 UTC file."""
    storm_day = day - timedelta(days=1)
    out = HAIL / f"{storm_day.isoformat()}.npz"
    done = HAIL / f"{storm_day.isoformat()}.none"
    if out.exists() or done.exists():
        return storm_day, None
    r = session.get(f"{BUCKET}/{key_for(day)}", timeout=120)
    if r.status_code == 404:  # occasional gap: take the nearest file that day
        lst = session.get(BUCKET, params={"list-type": "2", "prefix": f"{PRODUCT}/{day:%Y%m%d}/"}, timeout=60).text
        keys = [k.split("</Key>")[0] for k in lst.split("<Key>")[1:]]
        if not keys:
            return storm_day, "missing"
        k = min(keys, key=lambda k: abs(int(k[-15:-9]) - 120000))  # ..._YYYYMMDD-HHMMSS.grib2.gz
        r = session.get(f"{BUCKET}/{k}", timeout=120)
    r.raise_for_status()
    lats, lons, v = decode(r.content)
    mx = float(v.max())
    if mx < MIN_MM:
        done.write_text(f"{mx:.1f}")
        return storm_day, mx
    np.savez_compressed(out, lats=lats, lons=lons, mesh=v.astype(np.float16))
    return storm_day, mx


def backfill(start=date(2020, 10, 15), end=None):
    end = end or datetime.now(timezone.utc).date()
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    hits = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, (sd, mx) in enumerate(ex.map(fetch_day, days), 1):
            if isinstance(mx, float) and mx >= MIN_MM:
                hits += 1
                print(f"  hail day {sd}: max {mx / 25.4:.2f} in", flush=True)
            if i % 200 == 0:
                print(f"  {i}/{len(days)} days checked", flush=True)
    print(f"done: {hits} new hail days")


def latest():
    """Most recent 24 h max, whatever its time. Returns (valid_time, lats, lons, mesh)."""
    today = datetime.now(timezone.utc).date()
    for d in (today, today - timedelta(days=1)):
        lst = session.get(BUCKET, params={"list-type": "2", "prefix": f"{PRODUCT}/{d:%Y%m%d}/"}, timeout=60).text
        keys = sorted(k.split("</Key>")[0] for k in lst.split("<Key>")[1:])
        if keys:
            k = keys[-1]
            r = session.get(f"{BUCKET}/{k}", timeout=120)
            r.raise_for_status()
            stamp = datetime.strptime(k[-24:-9], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
            return (stamp, *decode(r.content))
    raise RuntimeError("no MRMS files found for today or yesterday")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    if cmd == "backfill":
        backfill()
    elif cmd == "latest":
        t, la, lo, v = latest()
        print(json.dumps({"valid": t.isoformat(), "max_in": round(float(v.max()) / 25.4, 2)}))

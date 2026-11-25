"""REAL sky / cloud conditions per 3D block, so the 3D sky can draw actual clouds.

  .venv\\Scripts\\python -u -W ignore usa/sky_weather.py                    # all blocks
  .venv\\Scripts\\python -u -W ignore usa/sky_weather.py austin-tx tulsa-ok  # some blocks
  .venv\\Scripts\\python -u -W ignore usa/sky_weather.py --no-clim          # skip the IEM climatology refresh

Re-runnable and fast (~10-20 s): one GOES file per product per run (cached by S3 key), NWS points/stations cached,
IEM climatology cached per station and only refreshed weekly.  Suitable for a scheduled task (e.g. every 30-60 min).

Sources (all US federal / public domain; quotes in usa/research/sky_weather.md):
  1 NWS API api.weather.gov  /points -> nearest observation station latest METAR (cloud layers, text) and the
    gridpoint forecast skyCover (hourly, next 7 days).
  2 NOAA GOES-East (GOES-19) ABI L2 Clear Sky Mask CONUS (ABI-L2-ACMC, 2 km) and Cloud Top Height (ABI-L2-ACHAC,
    10 km) from the NOAA NODD bucket s3://noaa-goes19 (anonymous HTTPS).
  3 Iowa Environmental Mesonet ASOS archive (asos.py): routine hourly METAR sky cover for the last ~3 years at the
    same station -> typical cloudiness by local hour and by month.

Output web/usa/blocks/<slug>/sky_weather.json; meta.files += sky_weather.json and meta.sky_weather (short summary),
written under usa.blocklock.block_lock with re-read + tmp/os.replace.
"""
import io
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import h5py
import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "sky_weather"
CACHE.mkdir(parents=True, exist_ok=True)
UA = "roof-intel-sky-weather/0.1 (open-data research script; python-requests)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/geo+json, application/json"})

GOES_BUCKET = "https://noaa-goes19.s3.amazonaws.com/"
GOES_RADIUS_KM = 5.0          # clear-sky fraction radius
ACHA_RADIUS_KM = 15.0         # 10 km product: need a wider ring to get any pixel
CLIM_YEARS = 3
CLIM_MAX_AGE_D = 7
OBS_MAX_AGE_H = 3

# METAR sky-cover amount -> percent of sky (okta midpoints: FEW 1-2/8, SCT 3-4/8, BKN 5-7/8, OVC 8/8)
AMOUNT_PCT = {"SKC": 0.0, "CLR": 0.0, "NCD": 0.0, "NSC": 0.0, "FEW": 18.75, "SCT": 43.75, "BKN": 75.0,
              "OVC": 100.0, "VV": 100.0}

LICENCE = {
    "summary": "All inputs are US federal works or public domain: NWS/NOAA (17 U.S.C. 105), NOAA NODD GOES, "
               "IEM (public domain). Attribution given; no endorsement implied.",
    "nws": "The information on National Weather Service (NWS) Web pages are in the public domain, unless "
           "specifically noted otherwise, and may be used without charge for any lawful purpose ... "
           "(weather.gov/disclaimer)",
    "goes": "NOAA data disseminated through NODD are open to the public and can be used as desired. "
            "(registry.opendata.aws/noaa-goes)",
    "iem": "The materials found on this website are in the public domain and may be used freely by anyone for "
           "any lawful purpose. (mesonet.agron.iastate.edu/disclaimer.php)",
    "attribution": "NOAA National Weather Service; NOAA/NESDIS GOES-19 ABI L2 via NOAA Open Data Dissemination; "
                   "Iowa Environmental Mesonet, Iowa State University.",
}


def get(url, params=None, timeout=30, tries=3, as_json=True):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json() if as_json else r
            last = f"HTTP {r.status_code}"
            if r.status_code in (400, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def write_json(path, obj, indent=None):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, separators=None if indent else (",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def hav_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------------------- NWS
def nws_point(lat, lon):
    """/points + /stations for a location, cached forever (grid/stations change very rarely)."""
    cf = CACHE / f"point_{lat:.4f}_{lon:.4f}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    p = get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")["properties"]
    st = get(p["observationStations"], params={"limit": 10})["features"]
    stations = []
    for f in st:
        lo, la = f["geometry"]["coordinates"][:2]
        stations.append({"id": f["properties"]["stationIdentifier"], "name": f["properties"].get("name"),
                         "lat": la, "lon": lo, "km": round(float(hav_km(lat, lon, la, lo)), 1)})
    stations.sort(key=lambda s: s["km"])
    out = {"grid": p["forecastGridData"], "office": p.get("gridId"), "stations": stations}
    write_json(cf, out, indent=1)
    return out


def layer_pct(amount):
    return AMOUNT_PCT.get((amount or "").upper())


def nws_current(stations, now):
    tried = []
    for s in stations[:6]:
        try:
            o = get(f"https://api.weather.gov/stations/{s['id']}/observations/latest",
                    params={"require_qc": "false"})["properties"]
        except Exception as e:  # noqa: BLE001
            tried.append(f"{s['id']}: {e}"); continue
        ts = o.get("timestamp")
        layers_raw = o.get("cloudLayers") or []
        age_h = (now - parse_iso(ts)).total_seconds() / 3600 if ts else 99
        if age_h > OBS_MAX_AGE_H or (not layers_raw and not o.get("textDescription")):
            tried.append(f"{s['id']}: stale/empty ({age_h:.1f} h)"); continue
        layers = []
        for L in layers_raw:
            b = (L.get("base") or {}).get("value")
            layers.append({"amount": L.get("amount"), "base_m": None if b is None else round(float(b))})
        pcts = [layer_pct(L["amount"]) for L in layers if layer_pct(L["amount"]) is not None]
        cover = max(pcts) if pcts else None
        bases = [L["base_m"] for L in layers if L["base_m"] is not None and (layer_pct(L["amount"]) or 0) > 0]
        ceil = [L["base_m"] for L in layers if L["base_m"] is not None and L["amount"] in ("BKN", "OVC", "VV")]
        vis = (o.get("visibility") or {}).get("value")
        return {
            "cloud_cover_pct": cover,
            "layers": layers,
            "cloud_base_m": min(bases) if bases else None,
            "ceiling_m": min(ceil) if ceil else None,
            "base_reference": "above ground level at the station (METAR)",
            "condition": o.get("textDescription") or None,
            "visibility_m": None if vis is None else round(float(vis)),
            "observed_at": ts,
            "age_min": round(age_h * 60),
            "station": {"id": s["id"], "name": s["name"], "km": s["km"]},
            "raw_metar": o.get("rawMessage") or None,
            "source": "NWS API api.weather.gov /stations/{id}/observations/latest",
            "skipped": tried or None,
        }
    return {"cloud_cover_pct": None, "layers": [], "cloud_base_m": None, "condition": None, "observed_at": None,
            "station": None, "source": "NWS API (no usable observation)", "skipped": tried}


def iso_dur_h(d):
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", d)
    if not m:
        return 1
    return int(m.group(1) or 0) * 24 + int(m.group(2) or 0) + (1 if int(m.group(3) or 0) else 0)


def nws_hourly(grid_url, now):
    g = get(grid_url, timeout=45, tries=4)["properties"]
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    hours = {}
    for v in g.get("skyCover", {}).get("values", []):
        t0s, dur = v["validTime"].split("/")
        t0 = parse_iso(t0s)
        for h in range(max(1, iso_dur_h(dur))):
            t = t0 + timedelta(hours=h)
            if start <= t < end and v["value"] is not None:
                hours[t] = v["value"]
    return [{"time": t.strftime("%Y-%m-%dT%H:00Z"), "cloud_cover_pct": int(round(hours[t]))} for t in sorted(hours)], \
        g.get("updateTime")


# ---------------------------------------------------------------------------------------------------- GOES
def goes_latest_key(product, now):
    for back in range(0, 4):
        t = now - timedelta(hours=back)
        pre = f"{product}/{t:%Y}/{t.timetuple().tm_yday:03d}/{t:%H}/"
        x = get(GOES_BUCKET, params={"list-type": 2, "prefix": pre}, as_json=False).text
        keys = sorted(re.findall(r"<Key>([^<]+\.nc)</Key>", x))
        if keys:
            return keys[-1]
    return None


def goes_file(key):
    cf = CACHE / ("goes_" + key.rsplit("/", 1)[-1])
    if not cf.exists():
        for old in CACHE.glob("goes_OR_" + key.rsplit("/", 1)[-1].split("_")[1] + "_*.nc"):
            old.unlink(missing_ok=True)          # keep only the latest scan per product
        b = get(GOES_BUCKET + key, timeout=90, as_json=False).content
        tmp = Path(str(cf) + ".tmp"); tmp.write_bytes(b); os.replace(tmp, cf)
    return cf


def _scaled(v):
    a = v[()].astype(np.float64)
    return a * float(v.attrs.get("scale_factor", [1])[0]) + float(v.attrs.get("add_offset", [0])[0])


class GoesGrid:
    def __init__(self, path, var):
        with h5py.File(path, "r") as f:
            pj = f["goes_imager_projection"].attrs
            self.a = float(pj["semi_major_axis"][0]); self.b = float(pj["semi_minor_axis"][0])
            self.H = float(pj["perspective_point_height"][0]) + self.a
            self.lon0 = math.radians(float(pj["longitude_of_projection_origin"][0]))
            self.x = _scaled(f["x"]); self.y = _scaled(f["y"])
            v = f[var]
            self.raw = v[()]
            self.fill = int(v.attrs["_FillValue"][0]) if "_FillValue" in v.attrs else None
            self.scale = float(v.attrs.get("scale_factor", [1])[0]); self.offset = float(v.attrs.get("add_offset", [0])[0])
            self.dqf = f["DQF"][()] if "DQF" in f else None
            self.t0 = f.attrs["time_coverage_start"].decode() if "time_coverage_start" in f.attrs else None
            self.t1 = f.attrs["time_coverage_end"].decode() if "time_coverage_end" in f.attrs else None
        self.dx = abs(self.x[1] - self.x[0])

    def to_xy(self, lat, lon):
        """Geodetic lat/lon (deg) -> ABI fixed-grid scan angles (rad), GOES-R PUG Vol 4 sec 4.2.8."""
        a, b, H = self.a, self.b, self.H
        e2 = 1 - (b * b) / (a * a)
        phi = math.radians(lat); lam = math.radians(lon)
        phic = math.atan((b * b) / (a * a) * math.tan(phi))
        rc = b / math.sqrt(1 - e2 * math.cos(phic) ** 2)
        sx = H - rc * math.cos(phic) * math.cos(lam - self.lon0)
        sy = -rc * math.cos(phic) * math.sin(lam - self.lon0)
        sz = rc * math.sin(phic)
        return math.asin(-sy / math.sqrt(sx * sx + sy * sy + sz * sz)), math.atan(sz / sx)

    def to_latlon(self, X, Y):
        a, b, H = self.a, self.b, self.H
        A = np.sin(X) ** 2 + np.cos(X) ** 2 * (np.cos(Y) ** 2 + (a * a) / (b * b) * np.sin(Y) ** 2)
        B = -2 * H * np.cos(X) * np.cos(Y)
        C = H * H - a * a
        rs = (-B - np.sqrt(B * B - 4 * A * C)) / (2 * A)
        sx = rs * np.cos(X) * np.cos(Y); sy = -rs * np.sin(X); sz = rs * np.cos(X) * np.sin(Y)
        lat = np.degrees(np.arctan((a * a) / (b * b) * sz / np.sqrt((H - sx) ** 2 + sy ** 2)))
        lon = np.degrees(self.lon0 - np.arctan(sy / (H - sx)))
        return lat, lon

    def window(self, lat, lon, radius_km, nominal_km):
        x0, y0 = self.to_xy(lat, lon)
        i = int(np.argmin(np.abs(self.y - y0))); j = int(np.argmin(np.abs(self.x - x0)))
        r = int(math.ceil(radius_km / nominal_km)) + 3
        i0, i1 = max(0, i - r), min(len(self.y), i + r + 1)
        j0, j1 = max(0, j - r), min(len(self.x), j + r + 1)
        X, Y = np.meshgrid(self.x[j0:j1], self.y[i0:i1])
        la, lo = self.to_latlon(X, Y)
        d = hav_km(lat, lon, la, lo)
        m = np.isfinite(d) & (d <= radius_km)
        sub = self.raw[i0:i1, j0:j1]
        dq = None if self.dqf is None else self.dqf[i0:i1, j0:j1]
        return sub[m], (None if dq is None else dq[m]), float(np.nanmin(d))


def goes_scenes(now):
    out = {}
    for prod, var in (("ABI-L2-ACMC", "BCM"), ("ABI-L2-ACHAC", "HT")):
        try:
            key = goes_latest_key(prod, now)
            out[prod] = (key, GoesGrid(goes_file(key), var)) if key else (None, None)
        except Exception as e:  # noqa: BLE001
            print(f"  GOES {prod} failed: {e}")
            out[prod] = (None, None)
    return out


def goes_block(scenes, lat, lon):
    key, g = scenes.get("ABI-L2-ACMC", (None, None))
    res = {"clear_sky_fraction": None, "cloud_fraction": None, "pixels": 0, "radius_km": GOES_RADIUS_KM,
           "cloud_top_m": None, "cloud_top_pixels": 0, "time": None, "cloud_top_time": None,
           "satellite": "GOES-19 (GOES-East) ABI", "source": None}
    if g is not None:
        v, dq, dmin = g.window(lat, lon, GOES_RADIUS_KM, 2.0)
        ok = (v != g.fill) & (v <= 1)
        if dq is not None:
            ok &= dq != 2                 # space pixels
        n = int(ok.sum())
        if n:
            clear = int((v[ok] == 0).sum())
            res.update(clear_sky_fraction=round(clear / n, 3), cloud_fraction=round(1 - clear / n, 3), pixels=n)
        res["time"] = g.t0
        res["source"] = f"s3://noaa-goes19/{key} (BCM: 0 clear/probably clear, 1 cloudy/probably cloudy)"
    key2, h = scenes.get("ABI-L2-ACHAC", (None, None))
    if h is not None:
        v, dq, _ = h.window(lat, lon, ACHA_RADIUS_KM, 10.0)
        ok = v != h.fill
        if dq is not None:
            ok &= dq <= 1                 # good or marginal retrievals
        if ok.any():
            ht = v[ok].astype(np.float64) * h.scale + h.offset
            res.update(cloud_top_m=round(float(np.median(ht))), cloud_top_pixels=int(ok.sum()),
                       cloud_top_min_m=round(float(ht.min())), cloud_top_max_m=round(float(ht.max())))
        res["cloud_top_time"] = h.t0
        res["cloud_top_radius_km"] = ACHA_RADIUS_KM
        res["cloud_top_source"] = f"s3://noaa-goes19/{key2} (HT, DQF<=1)"
    return res


# ---------------------------------------------------------------------------------------------------- IEM climatology
def iem_id(station_id):
    s = station_id.upper()
    return s[1:] if len(s) == 4 and s[0] == "K" else s


def climatology(station_id, tz, now, refresh=True):
    sid = iem_id(station_id)
    cf = CACHE / f"clim_{sid}.json"
    if cf.exists():
        c = json.loads(cf.read_text())
        age_d = (now - parse_iso(c["built"])).total_seconds() / 86400
        if age_d < CLIM_MAX_AGE_D or not refresh:
            return c
    elif not refresh:
        return None
    t1 = now.date(); t0 = t1.replace(year=t1.year - CLIM_YEARS)
    params = [("station", sid), ("data", "skyc1"), ("data", "skyc2"), ("data", "skyc3"), ("data", "skyc4"),
              ("year1", t0.year), ("month1", t0.month), ("day1", t0.day),
              ("year2", t1.year), ("month2", t1.month), ("day2", t1.day),
              ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"),
              ("trace", "T"), ("direct", "no"), ("report_type", "3")]
    txt = get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=params, timeout=180,
              as_json=False).text
    zi = ZoneInfo(tz)
    by_m, by_h = defaultdict(list), defaultdict(list)
    n = 0
    lines = txt.splitlines()
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) < 6:
            continue
        pcts = [AMOUNT_PCT[a] for a in p[2:6] if a in AMOUNT_PCT]
        if not pcts:
            continue
        try:
            t = datetime.strptime(p[1], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).astimezone(zi)
        except ValueError:
            continue
        c = max(pcts); n += 1
        by_m[t.month - 1].append(c); by_h[t.hour].append(c)
    if n < 500:
        out = None
    else:
        out = {"by_month_pct": [round(float(np.mean(by_m[i])), 1) if len(by_m[i]) >= 30 else None for i in range(12)],
               "by_hour_pct": [round(float(np.mean(by_h[i])), 1) if len(by_h[i]) >= 30 else None for i in range(24)],
               "clear_frac_by_month": [round(float(np.mean(np.array(by_m[i]) == 0)), 3) if len(by_m[i]) >= 30
                                       else None for i in range(12)],
               "hour_basis": f"local time {tz}", "n_obs": n, "period": [str(t0), str(t1)],
               "station": sid, "method": "routine hourly METARs; cover = max layer amount (FEW 18.75, SCT 43.75, "
                                          "BKN 75, OVC/VV 100, CLR 0); mean per bucket",
               "source": "Iowa Environmental Mesonet ASOS archive mesonet.agron.iastate.edu/cgi-bin/request/asos.py"}
    c = {"built": now.isoformat(), "clim": out}
    write_json(cf, c, indent=1)
    time.sleep(1.0)                       # be polite to IEM between stations
    return c


# ---------------------------------------------------------------------------------------------------- block
def block_inputs(slug):
    d = BLOCKS / slug
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    lon, lat = meta["origin"]
    return d, lat, lon, meta.get("tz") or "UTC", meta.get("city"), meta.get("state")


def build_nws(slug, now):
    d, lat, lon, tz, city, state = block_inputs(slug)
    pt = nws_point(lat, lon)
    cur = nws_current(pt["stations"], now)
    try:
        hourly, upd = nws_hourly(pt["grid"], now)
        herr = None
    except Exception as e:  # noqa: BLE001
        hourly, upd, herr = [], None, str(e)
    return {"slug": slug, "dir": d, "lat": lat, "lon": lon, "tz": tz, "city": city, "state": state, "pt": pt,
            "current": cur, "hourly": hourly, "hourly_meta": {"grid": pt["grid"], "updated": upd, "error": herr}}


def fuse(cur, goes):
    """Best single sky state for the renderer. ASOS ceilometers only see to ~3.7 km (12,000 ft), so an automated
    'CLR' means 'no cloud below 12,000 ft'; GOES sees everything from the top. Cover = max(station, satellite)."""
    st = cur.get("cloud_cover_pct")
    sat = None if goes.get("cloud_fraction") is None else round(goes["cloud_fraction"] * 100, 1)
    vals = [v for v in (st, sat) if v is not None]
    cover = max(vals) if vals else None
    high_only = bool(st == 0 and sat is not None and sat >= 50 and (goes.get("cloud_top_m") or 0) > 3600)
    if st is not None and sat is not None:
        basis = "max(station METAR, GOES clear-sky mask)"
    else:
        basis = "station METAR" if st is not None else ("GOES clear-sky mask" if sat is not None else None)
    return {"cloud_cover_pct": cover, "station_cover_pct": st, "satellite_cover_pct": sat,
            "low_cloud_base_m": cur.get("cloud_base_m"), "cloud_top_m": goes.get("cloud_top_m"),
            "high_cloud_above_ceilometer": high_only, "condition": cur.get("condition"), "basis": basis,
            "note": "ASOS/AWOS ceilometer limit ~3,660 m (12,000 ft): station CLR does not exclude higher cloud"
            if high_only else None}


def save_block(r, goes, clim, now):
    d = r["dir"]
    out = {
        "sky": fuse(r["current"], goes),
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": {"lat": r["lat"], "lon": r["lon"], "tz": r["tz"], "city": r["city"], "state": r["state"]},
        "current": r["current"],
        "hourly": r["hourly"],
        "hourly_source": {"source": "NWS API gridpoint forecast skyCover (percent of sky)", **r["hourly_meta"]},
        "goes": goes,
        "climatology": clim,
        "licence": LICENCE,
    }
    write_json(d / "sky_weather.json", out, indent=None)
    with block_lock(d):
        mp = d / "meta.json"
        meta = json.loads(mp.read_text(encoding="utf-8"))
        files = meta.setdefault("files", [])
        if "sky_weather.json" not in files:
            files.append("sky_weather.json")
        c = r["current"]
        meta["sky_weather"] = {"updated": out["generated"], "station": (c.get("station") or {}).get("id"),
                               "cloud_cover_pct": c.get("cloud_cover_pct"), "condition": c.get("condition"),
                               "goes_clear_sky_fraction": goes.get("clear_sky_fraction"),
                               "best_cloud_cover_pct": out["sky"]["cloud_cover_pct"],
                               "source": "NWS API + NOAA GOES-19 ABI L2 + IEM ASOS (public domain)"}
        tmp = mp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        os.replace(tmp, mp)


def main(argv):
    no_clim = "--no-clim" in argv
    slugs = [a for a in argv if not a.startswith("--")] or sorted(
        p.name for p in BLOCKS.iterdir() if p.is_dir() and (p / "meta.json").exists())
    now = datetime.now(timezone.utc)
    t0 = time.time()
    print(f"sky_weather: {len(slugs)} blocks @ {now:%Y-%m-%d %H:%MZ}")
    with ThreadPoolExecutor(6) as ex:
        fut_goes = ex.submit(goes_scenes, now)
        results = {}
        for slug, res in zip(slugs, ex.map(lambda s: _safe(build_nws, s, now), slugs)):
            results[slug] = res
        scenes = fut_goes.result()
    print(f"  NWS + GOES fetched in {time.time() - t0:.1f}s")
    for slug in slugs:
        r = results[slug]
        if isinstance(r, Exception):
            print(f"  {slug:16s} FAILED: {r}"); continue
        goes = goes_block(scenes, r["lat"], r["lon"])
        clim = None
        st = r["current"].get("station") or (r["pt"]["stations"][0] if r["pt"]["stations"] else None)
        if st:
            try:
                c = climatology(st["id"], r["tz"], now, refresh=not no_clim)
                clim = c["clim"] if c else None
            except Exception as e:  # noqa: BLE001
                print(f"  {slug}: climatology failed: {e}")
        save_block(r, goes, clim, now)
        c = r["current"]
        lay = " ".join(f"{L['amount']}{'' if L['base_m'] is None else L['base_m']}" for L in c["layers"]) or "-"
        print(f"  {slug:16s} {((c.get('station') or {}).get('id') or '?'):5s} {str(c.get('cloud_cover_pct')):>6s}% "
              f"{(c.get('condition') or '-')[:24]:24s} layers[{lay}] goes_clear={goes['clear_sky_fraction']} "
              f"top={goes['cloud_top_m']} best={fuse(c, goes)['cloud_cover_pct']}% fc_next={r['hourly'][0]['cloud_cover_pct'] if r['hourly'] else None}% "
              f"clim={'y' if clim else 'n'}")
    print(f"done in {time.time() - t0:.1f}s")


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        return e


if __name__ == "__main__":
    main(sys.argv[1:])

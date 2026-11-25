"""Rough-weather CLEANING TRIGGERS per county and per block, 2015-2026.

  .venv\\Scripts\\python -u -W ignore usa/weather_triggers.py            # build national json + all blocks
  .venv\\Scripts\\python -u -W ignore usa/weather_triggers.py --blocks-only scottsdale-az phoenix-az

Triggers (days): dust (haboob / blowing dust / PM10 spike), dust followed by rain within 72 h ('mud rain'),
high wind (>= 50 mph gust, debris), hail.  All sources are US federal works (17 U.S.C. 105) or IEM (public domain):
  1 NOAA NCEI Storm Events Database bulk CSVs (details files)          www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/
  2 NWS VTEC warnings DS/DU/HW via Iowa Environmental Mesonet archive   mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py
    + historical NWS county/zone (UGC) polygons for zone->county       mesonet.agron.iastate.edu/api/1/nws/ugcs.geojson
  3 EPA AQS pre-generated daily PM10 (81102) files                     aqs.epa.gov/aqsweb/airdata/daily_81102_YYYY.zip
  4 NOAA GHCN-Daily PRCP via NCEI Access Data Service                  www.ncei.noaa.gov/access/services/data/v1
Outputs
  web/usa/data/weather_triggers.json   per-county yearly/monthly aggregates (no national daily rows)
  web/usa/blocks/<slug>/meta.json      meta.weather_triggers (fetch first; then block_lock, re-read, set key, tmp+replace)
Cache: usa/_cache/weather/
"""
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
OUT = ROOT / "web" / "usa" / "data" / "weather_triggers.json"
CACHE = ROOT / "usa" / "_cache" / "weather"
CACHE.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "roofprint-usa/0.1 (open-data research)"

Y0, Y1 = 2015, 2026
FULL_YEARS = list(range(2015, 2026))          # averages use complete years 2015-2025
TODAY = date.today()
KT_50MPH = 43.4                               # 50 mph in knots (Storm Events magnitudes are knots)
PM10_DUST = 150.0                             # ug/m3 24-h mean (= the PM10 24-h NAAQS level)
RAIN_IN = 0.1
BLOCK_KM = 25.0
DUST, WIND, HAIL, RAIN = 1, 2, 4, 8

ST = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11",
      "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
      "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30",
      "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
      "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
      "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56", "PR": "72"}
FIPS2ST = {v: k for k, v in ST.items()}

SOURCES = [
    {"id": "ncei_storm_events", "name": "NOAA NCEI Storm Events Database (bulk details CSVs)",
     "url": "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/",
     "licence": "US federal government work, public domain (17 U.S.C. 105); NOAA/NWS: 'The information on National "
                "Weather Service (NWS) Web pages are in the public domain, unless specifically noted otherwise'",
     "licence_url": "https://www.weather.gov/disclaimer", "commercial_ok": "yes",
     "used": "Dust Storm, Dust Devil, High Wind, Strong Wind, Thunderstorm Wind, Hail, Heavy Rain, Flash Flood"},
    {"id": "nws_vtec_iem", "name": "NWS VTEC warnings archive (Dust Storm Warning/Advisory, Blowing Dust Warning/"
                                   "Advisory, High Wind Warning) + historical UGC polygons, via Iowa Environmental Mesonet",
     "url": "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py",
     "licence": "IEM: 'The materials found on this website are in the public domain and may be used freely by anyone "
                "for any lawful purpose.' (attribution appreciated); underlying NWS products are public domain",
     "licence_url": "https://mesonet.agron.iastate.edu/disclaimer.php", "commercial_ok": "yes"},
    {"id": "epa_aqs_pm10", "name": "EPA AQS AirData pre-generated daily PM10 (parameter 81102) files",
     "url": "https://aqs.epa.gov/aqsweb/airdata/download_files.html",
     "licence": "EPA Standard Open Data License: 'all data produced by the U.S EPA is by default in the public domain "
                "and is not subject to domestic copyright protection under 17 U.S.C. 105'",
     "licence_url": "https://edg.epa.gov/EPA_Data_License.html", "commercial_ok": "yes"},
    {"id": "ghcn_daily", "name": "NOAA NCEI GHCN-Daily precipitation (PRCP) via NCEI Access Data Service",
     "url": "https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries",
     "licence": "US federal government work (NOAA NCEI), public domain (17 U.S.C. 105)",
     "licence_url": "https://www.weather.gov/disclaimer", "commercial_ok": "yes"},
]
ATTRIB = ("Weather triggers derived from NOAA NCEI Storm Events Database, NOAA GHCN-Daily, EPA AQS PM10 and NWS "
          "warnings archived by the Iowa Environmental Mesonet (Iowa State University). Public domain.")


def get(url, tries=4, **kw):
    kw.setdefault("timeout", 300)
    last = None
    for i in range(tries):
        try:
            r = S.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {url}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise last


def cached(path, url, params=None, binary=True, max_age_days=None):
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        if max_age_days is None or time.time() - path.stat().st_mtime < max_age_days * 86400:
            return path
    r = get(url, params=params)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(r.content)
    os.replace(tmp, path)
    return path


def hav_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


# =============================================================== UGC geometry (zone -> county)
SNAPS = {2015: "2015-06-01", 2018: "2018-06-01", 2021: "2021-06-01", 2024: "2024-06-01", 2026: "2026-11-01"}


def snap_for(year):
    return min(SNAPS, key=lambda s: (abs(s - year), -s))


def load_ugc():
    """Returns zone2cty[snap][ugc]=[fips..], counties{fips:{name,st,lat,lon,geom}}, zones_now [(ugc, geom)]."""
    pkl = CACHE / "ugc_map.json"
    zgeo_now = []
    counties = {}
    zone2cty = {s: {} for s in SNAPS}
    zcent = {}

    def fetch(args):
        st, s = args
        p = CACHE / "ugc" / f"{st}_{SNAPS[s]}.geojson"
        try:
            cached(p, "https://mesonet.agron.iastate.edu/api/1/nws/ugcs.geojson", params={"state": st, "valid": SNAPS[s]})
        except Exception as e:  # noqa: BLE001
            print("  ugc fail", st, s, e)
            return None
        return st, s, p
    jobs = [(st, s) for st in ST for s in SNAPS]
    with ThreadPoolExecutor(6) as ex:
        res = [r for r in ex.map(fetch, jobs) if r]
    for st, s, p in sorted(res, key=lambda t: (t[0], t[1])):
        try:
            fc = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        cgeo, zgeo = {}, {}
        for f in fc.get("features", []):
            u = f["properties"]["ugc"]
            if not f.get("geometry"):
                continue
            try:
                g = shape(f["geometry"])
                if not g.is_valid:
                    g = g.buffer(0)
            except Exception:  # noqa: BLE001
                continue
            if u[2] == "C":
                fips = ST[st] + u[3:]
                cgeo[fips] = g
                if s == 2026 or fips not in counties:
                    rp = g.representative_point()
                    counties[fips] = {"name": f["properties"].get("name"), "st": st, "lat": rp.y, "lon": rp.x, "geom": g}
            elif u[2] == "Z":
                zgeo[u] = g
        if not cgeo:
            continue
        cf = list(cgeo)
        tree = STRtree([cgeo[k] for k in cf])
        for u, g in zgeo.items():
            if s == 2026:
                zgeo_now.append((u, g))
            ga = g.area or 1e-12
            hits = []
            for i in tree.query(g):
                try:
                    a = g.intersection(cgeo[cf[i]]).area
                except Exception:  # noqa: BLE001
                    continue
                if a > 0:
                    hits.append((a, cf[i]))
            hits.sort(reverse=True)
            keep = [f for a, f in hits if a >= 0.05 * ga] or [f for a, f in hits[:1]]
            zone2cty[s][u] = keep
            zcent.setdefault(u, (g.representative_point().y, g.representative_point().x))
        # county UGCs map to themselves
        for fips in cgeo:
            zone2cty[s][st + "C" + fips[2:]] = [fips]
            zcent.setdefault(st + "C" + fips[2:], (counties[fips]["lat"], counties[fips]["lon"]))
    return zone2cty, counties, zgeo_now, zcent


def ugc_counties(zone2cty, ugc, year):
    order = sorted(SNAPS, key=lambda s: (abs(s - year), -s))
    for s in order:
        v = zone2cty[s].get(ugc)
        if v:
            return v
    return []


# =============================================================== 1 Storm Events
SE_TYPES = {"Dust Storm": "dust", "Dust Devil": "dust", "High Wind": "wind", "Strong Wind": "wind",
            "Thunderstorm Wind": "wind", "Hail": "hail", "Heavy Rain": "rain", "Flash Flood": "rain"}


def storm_files():
    r = get("https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/", timeout=120)
    names = re.findall(r'href="(StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d{8})\.csv\.gz)"', r.text)
    best = {}
    for n, y, c in names:
        y = int(y)
        if Y0 <= y <= Y1 and (y not in best or c > best[y][1]):
            best[y] = (n, c)
    return {y: v[0] for y, v in sorted(best.items())}


def load_storm_events(zone2cty):
    """Returns list of dict(date, fips[list], kind, lat, lon, src_type, mag) and coverage notes."""
    files = storm_files()
    print("  storm events files:", files)
    rows = []
    unmapped = Counter()
    for y, n in files.items():
        p = CACHE / "storm" / n
        if not p.exists():
            for old in (CACHE / "storm").glob(f"StormEvents_details-ftp_v1.0_d{y}_c*.csv.gz"):
                old.unlink()
        cached(p, "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/" + n)
        df = pd.read_csv(p, compression="gzip", low_memory=False,
                         usecols=["BEGIN_YEARMONTH", "BEGIN_DAY", "END_YEARMONTH", "END_DAY", "STATE_FIPS", "CZ_TYPE",
                                  "CZ_FIPS", "EVENT_TYPE", "MAGNITUDE", "MAGNITUDE_TYPE", "BEGIN_LAT", "BEGIN_LON",
                                  "EVENT_ID"])
        df = df[df.EVENT_TYPE.isin(SE_TYPES)]
        for t in df.itertuples(index=False):
            kind = SE_TYPES[t.EVENT_TYPE]
            mag = t.MAGNITUDE if pd.notna(t.MAGNITUDE) else None
            if t.EVENT_TYPE in ("Strong Wind", "Thunderstorm Wind") and (mag is None or mag < KT_50MPH):
                continue
            try:
                d0 = date(int(t.BEGIN_YEARMONTH) // 100, int(t.BEGIN_YEARMONTH) % 100, int(t.BEGIN_DAY))
                d1 = date(int(t.END_YEARMONTH) // 100, int(t.END_YEARMONTH) % 100, int(t.END_DAY))
            except Exception:  # noqa: BLE001
                continue
            sf = f"{int(t.STATE_FIPS):02d}" if pd.notna(t.STATE_FIPS) else None
            st = FIPS2ST.get(sf)
            if not st:
                continue
            cz = int(t.CZ_FIPS)
            if t.CZ_TYPE == "C":
                fl = [sf + f"{cz:03d}"]
            else:
                fl = ugc_counties(zone2cty, f"{st}Z{cz:03d}", y)
                if not fl:
                    unmapped[f"{st}Z{cz:03d}"] += 1
                    continue
            lat = float(t.BEGIN_LAT) if pd.notna(t.BEGIN_LAT) else None
            lon = float(t.BEGIN_LON) if pd.notna(t.BEGIN_LON) else None
            ndays = max(0, min((d1 - d0).days, 4))
            for k in range(ndays + 1):
                rows.append({"date": d0 + timedelta(k), "fips": fl, "kind": kind, "lat": lat, "lon": lon,
                             "ugc": f"{st}{t.CZ_TYPE}{cz:03d}", "src": "NCEI Storm Events: " + t.EVENT_TYPE,
                             "mag": mag})
        print(f"  storm {y}: kept so far {len(rows)}")
    return rows, unmapped


# =============================================================== 2 NWS warnings (IEM)
WARN = [("DS", "W", "dust", "NWS Dust Storm Warning"), ("DS", "Y", "dust", "NWS Dust Advisory"),
        ("DU", "W", "dust", "NWS Blowing Dust Warning"), ("DU", "Y", "dust", "NWS Blowing Dust Advisory"),
        ("HW", "W", "wind", "NWS High Wind Warning")]


def vtec_year(y):
    """One IEM watchwarn CSV per year (the service returns every phenomenon for county/zone rows, so we download
    once and keep only DS/DU/HW county/zone rows in a compact cache file)."""
    keep = CACHE / "iem" / f"vtec_dsduhw_{y}.csv"
    fresh = keep.exists() and (y < TODAY.year or time.time() - keep.stat().st_mtime < 3 * 86400)
    if fresh:
        return keep
    raw = CACHE / "iem" / f"_raw_{y}.csv"
    ets = f"{y + 1}-01-01T00:00Z" if y < TODAY.year else (TODAY.isoformat() + "T00:00Z")
    # reuse a full-year file left by an earlier (per-phenomenon) run if present
    old = CACHE / "iem" / f"HWW_{y}.csv"
    if old.exists() and y < TODAY.year:
        raw = old
    else:
        cached(raw, "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py",
               params={"sts": f"{y}-01-01T00:00Z", "ets": ets, "accept": "csv"}, max_age_days=0)
    phs = {(p, s) for p, s, _, _ in WARN}
    tmp = keep.with_suffix(".tmp")
    with open(raw, encoding="utf-8", errors="replace") as f, open(tmp, "w", encoding="utf-8", newline="") as fo:
        rd = csv.DictReader(f)
        wr = csv.DictWriter(fo, fieldnames=["phenomena", "significance", "gtype", "ugc", "utc_issue", "utc_expire"],
                            extrasaction="ignore")
        wr.writeheader()
        for r in rd:
            if r.get("gtype") == "C" and (r.get("phenomena"), r.get("significance")) in phs:
                wr.writerow(r)
    os.replace(tmp, keep)
    for p in (CACHE / "iem").glob(f"*_{y}.csv"):
        if p != keep:
            p.unlink()
    return keep


def load_warnings(zone2cty, zcent):
    rows, unmapped = [], Counter()
    for y in range(Y0, Y1 + 1):
        try:
            p = vtec_year(y)
        except Exception as e:  # noqa: BLE001
            print("  warn fetch fail", y, e)
            continue
        for ph, sig, kind, label in WARN:
            seen = set()
            with open(p, encoding="utf-8", errors="replace") as f:
                for r in csv.DictReader(f):
                    if r.get("gtype") != "C" or r.get("phenomena") != ph or r.get("significance") != sig:
                        continue
                    u = r["ugc"]
                    if len(u) != 6:
                        continue
                    try:
                        t0 = datetime.strptime(r["utc_issue"], "%Y-%m-%d %H:%M")
                        t1 = datetime.strptime(r["utc_expire"], "%Y-%m-%d %H:%M")
                    except Exception:  # noqa: BLE001
                        continue
                    lon = zcent.get(u, (None, -100))[1]
                    off = timedelta(hours=round(lon / 15))
                    d0, d1 = (t0 + off).date(), (t1 + off).date()
                    fl = ugc_counties(zone2cty, u, y)
                    if not fl:
                        unmapped[u] += 1
                        continue
                    for k in range(max(0, min((d1 - d0).days, 2)) + 1):
                        key = (u, d0 + timedelta(k))
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append({"date": d0 + timedelta(k), "fips": fl, "kind": kind, "lat": None, "lon": None,
                                     "ugc": u, "src": label, "mag": None})
        print(f"  warnings {y}: total {len(rows)}")
    return rows, unmapped


# =============================================================== 3 EPA AQS PM10
FIREWORKS = {(1, 1), (7, 4), (7, 5)}


def load_pm10():
    """Returns monitors{site:{lat,lon,name,fips,first,last}}, site_dust{site:set(dates)}"""
    mons, site_max = {}, defaultdict(dict)
    for y in range(Y0, Y1 + 1):
        p = CACHE / "aqs" / f"daily_81102_{y}.zip"
        try:
            cached(p, f"https://aqs.epa.gov/aqsweb/airdata/daily_81102_{y}.zip",
                   max_age_days=None if y < TODAY.year - 1 else 20)
        except Exception as e:  # noqa: BLE001
            print("  aqs fail", y, e)
            continue
        with zipfile.ZipFile(p) as z:
            nm = [n for n in z.namelist() if n.endswith(".csv")][0]
            df = pd.read_csv(z.open(nm), low_memory=False,
                             usecols=["State Code", "County Code", "Site Num", "Latitude", "Longitude", "Date Local",
                                      "Arithmetic Mean", "Local Site Name"], dtype={"State Code": str, "County Code": str,
                                                                                     "Site Num": str})
        df = df[df["State Code"].str.fullmatch(r"\d\d")]
        df["site"] = df["State Code"] + df["County Code"].str.zfill(3) + df["Site Num"].str.zfill(4)
        g = df.groupby(["site", "Date Local"])["Arithmetic Mean"].max().reset_index()
        for site, sub in df.groupby("site"):
            r0 = sub.iloc[0]
            m = mons.setdefault(site, {"lat": float(r0.Latitude), "lon": float(r0.Longitude),
                                       "name": str(r0["Local Site Name"]) if pd.notna(r0["Local Site Name"]) else None,
                                       "fips": site[:5], "first": y, "last": y, "days": 0})
            m["last"] = y
        cnt = g.groupby("site").size()
        for s, n in cnt.items():
            mons[s]["days"] += int(n)
        hi = g[g["Arithmetic Mean"] > PM10_DUST]
        for t in hi.itertuples(index=False):
            d = date.fromisoformat(t[1])
            site_max[t[0]][d] = float(t[2])
        print(f"  pm10 {y}: monitors {len(mons)}, dust site-days {sum(len(v) for v in site_max.values())}")
    return mons, site_max


# =============================================================== 4 GHCN-Daily PRCP
def ghcn_inventory():
    inv = cached(CACHE / "ghcnd-inventory.txt", "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt",
                 max_age_days=60)
    stn = cached(CACHE / "ghcnd-stations.txt", "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt",
                 max_age_days=60)
    names = {}
    for line in open(stn, encoding="utf-8", errors="replace"):
        names[line[:11]] = line[41:71].strip()
    out = {}
    for line in open(inv, encoding="utf-8", errors="replace"):
        if line[31:35] != "PRCP" or not line.startswith("US"):
            continue
        sid = line[:11]
        first, last = int(line[36:40]), int(line[41:45])
        if first <= 2016 and last >= TODAY.year:
            out[sid] = {"lat": float(line[12:20]), "lon": float(line[21:30]), "name": names.get(sid), "first": first}
    return out


def fetch_prcp(sids):
    """Per-station cached daily PRCP (inches) {sid:{date:val}} fetched in batches from the NCEI access service."""
    res, todo = {}, []
    d = CACHE / "ghcn"
    d.mkdir(exist_ok=True)
    for s in sids:
        p = d / f"{s}.csv"
        if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
            res[s] = p
        else:
            todo.append(s)
    batches = [todo[i:i + 10] for i in range(0, len(todo), 10)]

    def one(b):
        try:
            r = get("https://www.ncei.noaa.gov/access/services/data/v1",
                    params={"dataset": "daily-summaries", "stations": ",".join(b), "dataTypes": "PRCP",
                            "startDate": f"{Y0}-01-01", "endDate": TODAY.isoformat(), "format": "csv",
                            "units": "standard"}, timeout=600)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print("  ghcn batch fail", b[0], e)
            return
        per = defaultdict(list)
        for row in csv.reader(io.StringIO(r.text)):
            if len(row) >= 3 and row[0] != "STATION":
                per[row[0]].append(f"{row[1]},{row[2]}")
        for s in b:
            (d / f"{s}.csv").write_text("\n".join(per.get(s, [])), encoding="utf-8")
    if batches:
        print(f"  ghcn: fetching {len(todo)} stations in {len(batches)} batches")
        with ThreadPoolExecutor(4) as ex:
            for i, _ in enumerate(ex.map(one, batches)):
                if i % 20 == 0:
                    print(f"    batch {i}/{len(batches)}")
    out = {}
    for s in sids:
        p = d / f"{s}.csv"
        if not p.exists():
            continue
        dd = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            a, _, v = line.partition(",")
            try:
                dd[date.fromisoformat(a)] = float(v)
            except Exception:  # noqa: BLE001
                pass
        out[s] = dd
    return out


# =============================================================== aggregation
def build():
    t0 = time.time()
    print("[1] UGC polygons")
    zone2cty, counties, zones_now, zcent = load_ugc()
    print(f"  counties {len(counties)}, zone snapshots {[len(v) for v in zone2cty.values()]}")
    print("[2] Storm Events")
    se_rows, se_un = load_storm_events(zone2cty)
    print("[3] NWS warnings")
    w_rows, w_un = load_warnings(zone2cty, zcent)
    print("[4] PM10")
    mons, site_dust = load_pm10()

    day = defaultdict(lambda: defaultdict(int))          # fips -> date -> bitmask
    srcs = defaultdict(lambda: defaultdict(set))         # fips -> date -> kinds (for last events)
    bit = {"dust": DUST, "wind": WIND, "hail": HAIL, "rain": RAIN}
    for r in se_rows + w_rows:
        if not (Y0 <= r["date"].year <= Y1):
            continue
        for f in r["fips"]:
            day[f][r["date"]] |= bit[r["kind"]]
    pm10_county = defaultdict(set)
    for site, dd in site_dust.items():
        f = mons[site]["fips"]
        for d in dd:
            if (d.month, d.day) in FIREWORKS:
                continue
            day[f][d] |= DUST
            pm10_county[f].add(d)

    print("[5] GHCN rain for dust counties + block areas")
    inv = ghcn_inventory()
    sids = list(inv)
    slat = np.array([inv[s]["lat"] for s in sids]); slon = np.array([inv[s]["lon"] for s in sids])
    pts = [Point(lo, la) for la, lo in zip(slat, slon)]
    stree = STRtree(pts)
    dust_cty = [f for f, dd in day.items() if any(v & DUST for v in dd.values())]
    blocks = block_list()
    cty_st = {}
    for f in set(dust_cty) | {b["fips"] for b in blocks if b.get("fips")}:
        c = counties.get(f)
        if not c:
            continue
        inside = [sids[i] for i in stree.query(c["geom"], predicate="intersects")]
        dist = lambda s: hav_km(c["lat"], c["lon"], inv[s]["lat"], inv[s]["lon"])  # noqa: E731
        inside.sort(key=lambda s: (inv[s]["first"] > 2015, s.startswith("US1"), dist(s)))
        pick = inside[:4]
        if not pick:
            dd = np.hypot((slat - c["lat"]) * 111, (slon - c["lon"]) * 111 * math.cos(math.radians(c["lat"])))
            pick = [sids[i] for i in np.argsort(dd)[:2] if dd[i] < 40]
        cty_st[f] = pick
    bst = {}
    for b in blocks:
        dd = np.hypot((slat - b["lat"]) * 111, (slon - b["lon"]) * 111 * math.cos(math.radians(b["lat"])))
        order = [i for i in np.argsort(dd)[:6] if dd[i] < BLOCK_KM]
        bst[b["slug"]] = [(sids[i], float(dd[i])) for i in order]
    need = sorted({s for v in cty_st.values() for s in v} | {s for v in bst.values() for s, _ in v})
    prcp = fetch_prcp(need)
    for f, ss in cty_st.items():
        for s in ss:
            for d, v in prcp.get(s, {}).items():
                if v >= RAIN_IN:
                    day[f][d] |= RAIN

    print("[6] county aggregates")
    out_c = {}
    for f, dd in day.items():
        if f not in counties and len(f) != 5:
            continue
        years = {y: {"dust": 0, "dust_rain": 0, "wind": 0, "hail": 0} for y in range(Y0, Y1 + 1)}
        month = np.zeros(12)
        ev = []
        for d in sorted(dd):
            if not (Y0 <= d.year <= Y1):
                continue
            v = dd[d]
            types = []
            if v & DUST:
                types.append("dust")
                if any(dd.get(d + timedelta(k), 0) & RAIN for k in range(4)):
                    types.append("dust_rain")
            if v & WIND:
                types.append("wind")
            if v & HAIL:
                types.append("hail")
            if not types:
                continue
            for t in types:
                years[d.year][t] += 1
            if d.year in FULL_YEARS:
                month[d.month - 1] += 1
            ev.append((d, types))
        if not ev:
            continue
        ny = len(FULL_YEARS)
        avg = lambda k: round(sum(years[y][k] for y in FULL_YEARS) / ny, 2)  # noqa: E731
        last = []
        for d, types in reversed(ev):
            for t in ("dust_rain", "dust", "hail", "wind"):
                if t in types:
                    last.append({"date": d.isoformat(), "type": t})
                    break
            if len(last) >= 10:
                break
        c = counties.get(f, {})
        out_c[f] = {"name": c.get("name"), "st": c.get("st") or FIPS2ST.get(f[:2]),
                    "dust_days_yr": avg("dust"), "dust_then_rain_yr": avg("dust_rain"),
                    "high_wind_days_yr": avg("wind"), "hail_days_yr": avg("hail"),
                    "trigger_days_yr": round(month.sum() / ny, 2),
                    "monthly_trigger_profile": [round(x / ny, 2) for x in month],
                    "last_events": last,
                    "years": {str(y): v for y, v in years.items()},
                    "pm10_monitors": sum(1 for m in mons.values() if m["fips"] == f and m["last"] >= 2015),
                    "rain_stations": len(cty_st.get(f, []))}
    meta = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "period": f"{Y0}-01-01..{TODAY.isoformat()} (averages over full years {FULL_YEARS[0]}-{FULL_YEARS[-1]})",
            "sources": SOURCES, "attribution": ATTRIB,
            "definitions": {
                "dust": "day with a Storm Events Dust Storm/Dust Devil, an NWS Dust Storm Warning/Dust Advisory/Blowing "
                        "Dust Warning/Advisory covering a zone overlapping the county (zone->county by historical UGC "
                        "polygon overlap >= 5% of zone area), or any EPA AQS PM10 monitor in the county with 24-h mean "
                        f"> {PM10_DUST:g} ug/m3 (Jan 1 and Jul 4-5 excluded for PM10-only: fireworks)",
                "dust_rain": f"dust day with rain (>= {RAIN_IN} in at any of up to 4 GHCN-Daily stations in the county, or "
                             "a Storm Events Heavy Rain/Flash Flood) on the same day or within the next 3 days (72 h)",
                "wind": "Storm Events High Wind (any), Strong Wind or Thunderstorm Wind with magnitude >= 43.4 kt (50 mph), "
                        "or an NWS High Wind Warning",
                "hail": "Storm Events Hail report (county)",
                "trigger_days_yr": "mean days/yr with any of dust, dust_rain, wind, hail (a day counts once)",
                "monthly_trigger_profile": "mean trigger days per calendar month (Jan..Dec), full years only",
                "*_yr": f"mean per year over {FULL_YEARS[0]}-{FULL_YEARS[-1]}; years{{}} also holds partial {Y1}",
                "caveat": "Zone-based events/warnings are assigned to every county the zone overlaps; PM10 > 150 "
                          "can also be wildfire smoke or local construction; warnings are forecasts, not observations"},
            "coverage": {"counties": len(out_c),
                         "counties_with_dust": sum(1 for v in out_c.values() if v["dust_days_yr"] > 0),
                         "counties_with_pm10_monitor": sum(1 for v in out_c.values() if v["pm10_monitors"]),
                         "counties_with_rain_stations": sum(1 for v in out_c.values() if v["rain_stations"]),
                         "pm10_monitors": len(mons), "ghcn_stations_used": len(need),
                         "storm_event_rows": len(se_rows), "warning_rows": len(w_rows),
                         "unmapped_zone_events": sum(se_un.values()) + sum(w_un.values()),
                         "unmapped_examples": [k for k, _ in (se_un + w_un).most_common(12)]}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fo:
        json.dump({"meta": meta, "counties": dict(sorted(out_c.items()))}, fo, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"  wrote {OUT} {OUT.stat().st_size / 1e6:.2f} MB, {len(out_c)} counties ({time.time() - t0:.0f}s)")
    # keep things needed for blocks
    return {"zone2cty": zone2cty, "counties": counties, "zones_now": zones_now, "se": se_rows, "warn": w_rows,
            "mons": mons, "site_dust": site_dust, "inv": inv, "prcp": prcp, "bst": bst, "out_c": out_c, "meta": meta,
            "stree": stree, "sids": sids, "slat": slat, "slon": slon}


# =============================================================== blocks
def block_list(slugs=None):
    out = []
    for d in sorted(BLOCKS.iterdir()) if BLOCKS.exists() else []:
        m = d / "meta.json"
        if not d.is_dir() or not m.exists() or (slugs and d.name not in slugs):
            continue
        try:
            meta = json.load(open(m, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        o = meta.get("origin") or [None, None]
        if o[0] is None:
            continue
        out.append({"slug": d.name, "dir": d, "lon": float(o[0]), "lat": float(o[1]),
                    "fips": meta.get("county_fips") or meta.get("fips")})
    return out


def season(profile):
    pk = max(profile) if profile else 0
    if pk <= 0:
        return []
    return [i + 1 for i, v in enumerate(profile) if v >= 0.5 * pk]


MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def block_payload(b, ctx):
    lat, lon, fips = b["lat"], b["lon"], b["fips"]
    pt = Point(lon, lat)
    zone = next((u for u, g in ctx["zones_now"] if g.contains(pt)), None)
    if fips is None:
        fips = next((f for f, c in ctx["counties"].items() if c["geom"].contains(pt)), None)
    cugc = (FIPS2ST.get(fips[:2], "") + "C" + fips[2:]) if fips else None
    since = date(2020, 1, 1)
    ev = {}

    def add(d, t, src, km=None):
        if d < since:
            return
        k = (d, t)
        e = ev.get(k)
        if e is None or (km is not None and (e.get("km") is None or km < e["km"])):
            ev[k] = {"date": d.isoformat(), "type": t, "source": src, **({"km": round(km, 1)} if km is not None else {})}
    for r in ctx["se"]:
        if r["date"] < since:
            continue
        if r["lat"] is not None:
            km = hav_km(lat, lon, r["lat"], r["lon"])
            if km <= BLOCK_KM:
                add(r["date"], r["kind"], r["src"], km)
        elif r["ugc"] in (zone, cugc):
            add(r["date"], r["kind"], r["src"] + (" (zone)" if r["ugc"] == zone else " (county)"))
    for r in ctx["warn"]:
        if r["date"] >= since and r["ugc"] in (zone, cugc):
            add(r["date"], r["kind"], r["src"] + f" ({r['ugc']})")
    mons = ctx["mons"]
    near = sorted(((hav_km(lat, lon, m["lat"], m["lon"]), s) for s, m in mons.items()), key=lambda t: t[0])
    nm = None
    if near:
        km, s = near[0]
        m = mons[s]
        nm = {"site": s, "name": m["name"], "lat": m["lat"], "lon": m["lon"], "km": round(km, 1),
              "years": f"{m['first']}-{m['last']}"}
    for km, s in near:
        if km > BLOCK_KM:
            break
        for d, v in ctx["site_dust"].get(s, {}).items():
            if (d.month, d.day) not in FIREWORKS:
                add(d, "dust", f"EPA AQS PM10 {s} 24-h mean {v:.0f} ug/m3", km)
    # rain at nearest stations -> dust_rain
    st = ctx["bst"].get(b["slug"], [])
    rain_days = set()
    for s, _ in st[:3]:
        rain_days |= {d for d, v in ctx["prcp"].get(s, {}).items() if v >= RAIN_IN}
    rain_days |= {date.fromisoformat(e["date"]) for (d, t), e in ev.items() if t == "rain"}
    for (d, t), e in list(ev.items()):
        if t == "dust" and any(d + timedelta(k) in rain_days for k in range(4)):
            add(d, "dust_rain", "dust event + rain within 72 h (GHCN-Daily" +
                (f" {st[0][0]}" if st else "") + ")")
    evl = sorted((e for e in ev.values() if e["type"] != "rain"), key=lambda e: (e["date"], e["type"]), reverse=True)
    counts = Counter(e["type"] for e in evl)
    cs = ctx["out_c"].get(fips, {}) if fips else {}
    ses = season(cs.get("monthly_trigger_profile", []))
    rs = None
    if st:
        s, km = st[0]
        rs = {"id": s, "name": ctx["inv"][s]["name"], "km": round(km, 1)}
    return {"county_fips": fips, "nws_zone": zone,
            "county": {k: cs.get(k) for k in ("name", "dust_days_yr", "dust_then_rain_yr", "high_wind_days_yr",
                                              "hail_days_yr", "trigger_days_yr", "monthly_trigger_profile")} if cs else None,
            "nearest_pm10_monitor": nm, "nearest_rain_station": rs,
            "events_since_2020_within_25km": evl[:250],
            "event_days_since_2020": dict(counts),
            "events_truncated": len(evl) > 250,
            "season_months": ses, "season": ", ".join(MON[m - 1] for m in ses),
            "radius_km": BLOCK_KM,
            "definitions": "see web/usa/data/weather_triggers.json meta.definitions; zone/county events listed without km",
            "sources": [s["id"] for s in SOURCES], "attribution": ATTRIB,
            "generated": ctx["meta"]["generated"]}


def write_block(b, payload):
    B = b["dir"]
    with block_lock(B):
        meta = json.load(open(B / "meta.json", encoding="utf-8"))
        meta["weather_triggers"] = payload
        tmp = B / "meta.json.wt.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp, B / "meta.json")


def do_blocks(ctx, slugs=None):
    bl = block_list(slugs)
    # stations for blocks not seen at build time
    miss = [b for b in bl if b["slug"] not in ctx["bst"]]
    if miss:
        slat, slon, sids = ctx["slat"], ctx["slon"], ctx["sids"]
        for b in miss:
            dd = np.hypot((slat - b["lat"]) * 111, (slon - b["lon"]) * 111 * math.cos(math.radians(b["lat"])))
            ctx["bst"][b["slug"]] = [(sids[i], float(dd[i])) for i in np.argsort(dd)[:6] if dd[i] < BLOCK_KM]
        need = sorted({s for b in miss for s, _ in ctx["bst"][b["slug"]]} - set(ctx["prcp"]))
        ctx["prcp"].update(fetch_prcp(need))
    for b in bl:
        p = block_payload(b, ctx)
        write_block(b, p)
        c = p["county"] or {}
        print(f"  {b['slug']}: fips {p['county_fips']} zone {p['nws_zone']} dust/yr {c.get('dust_days_yr')} "
              f"dust_rain/yr {c.get('dust_then_rain_yr')} wind/yr {c.get('high_wind_days_yr')} hail/yr "
              f"{c.get('hail_days_yr')} | 25km events {p['event_days_since_2020']} | season {p['season']} | "
              f"pm10 {p['nearest_pm10_monitor'] and p['nearest_pm10_monitor']['km']} km")
    return bl


if __name__ == "__main__":
    args = sys.argv[1:]
    slugs = None
    if "--blocks-only" in args:
        slugs = [a for a in args if not a.startswith("--")] or None
    ctx = build()
    do_blocks(ctx, slugs)
    top = sorted(ctx["out_c"].items(), key=lambda kv: -kv[1]["dust_then_rain_yr"])[:20]
    print("TOP 20 dust_then_rain_yr")
    for f, v in top:
        print(f"  {f} {v['name']}, {v['st']}: dust_rain {v['dust_then_rain_yr']} dust {v['dust_days_yr']} "
              f"wind {v['high_wind_days_yr']} hail {v['hail_days_yr']} pm10 {v['pm10_monitors']} rain_st {v['rain_stations']}")
    print("coverage", json.dumps(ctx["meta"]["coverage"]))

"""STR demand index from AGGREGATE visitor-flow open data (US federal, public domain).

Sources (all US federal works, public domain, commercial use allowed):
  * BTS T-100 Segment (All Carriers)             -> monthly arriving passengers per airport
  * BTS DB1B Coupon (10% ticket sample)          -> destination (trip-break) vs connecting share
  * BTS Reporting Carrier On-Time Performance    -> arrival local-hour histogram
  * BTS Master Coordinate                        -> airport lon/lat
  * DOC ITA/NTTO I-94 arrivals by state of first intended address
  * NPS IRMA Visitor Use Statistics + NPS unit locations

Never individuals: every number is an aggregate count of passengers / visits.

Usage:  .venv\\Scripts\\python -u -W ignore usa\\tourism_air.py [download|build|all]
Output: web/usa/data/tourism_air.json
"""
import csv
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "_cache", "tourism")
OUT = os.path.join(ROOT, "web", "usa", "data", "tourism_air.json")
COUNTIES = os.path.join(ROOT, "web", "usa", "data", "counties.json")
os.makedirs(CACHE, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (open-data research script)"}
TT = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ={code}&QO_fu146_anzr={q}"
TT_Q = {"FMG": "Nv4%20Pn44vr45", "FLM": "N8vn6v10%20f722146%20gnoyr5", "FLL": "N8vn6v10%20f722146%20gnoyr5"}
PREZIP = "https://transtats.bts.gov/PREZIP/"

AIRPORT_KM = 60.0  # county centroid -> airport radius
NPS_KM = 80.0      # county centroid -> park unit radius


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ----------------------------------------------------------------- downloads
def tt_download(code, fields, out, year=None, period=None, tries=3):
    """POST the TranStats 'Download' form (ASP.NET) and save the returned zip."""
    path = os.path.join(CACHE, out)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    url = TT.format(code=code, q=TT_Q.get(code, ""))
    for t in range(tries):
        try:
            s = requests.Session()
            s.headers.update(UA)
            h = s.get(url, timeout=120).text
            d = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', h))
            d.update({"cboGeography": "All", "btnDownload": "Download"})
            if year is not None:
                d["cboYear"] = str(year)
            if period is not None:
                d["cboPeriod"] = str(period)
            for f in fields:
                d[f] = "on"
            t0 = time.time()
            r = s.post(url, data=d, timeout=1800, stream=True)
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            with open(tmp, "rb") as fh:
                magic = fh.read(2)
            if magic != b"PK":
                raise RuntimeError("not a zip (%s)" % r.headers.get("content-type"))
            os.replace(tmp, path)
            log("  got", out, os.path.getsize(path) // 1024, "KB in %.0fs" % (time.time() - t0))
            return path
        except Exception as e:  # noqa
            log("  retry", out, e)
            time.sleep(10 * (t + 1))
    raise RuntimeError("failed " + out)


def http_get(url, out, tries=3):
    path = os.path.join(CACHE, out)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    for t in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=1800, stream=True)
            r.raise_for_status()
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            os.replace(tmp, path)
            log("  got", out, os.path.getsize(path) // 1024, "KB")
            return path
        except Exception as e:  # noqa
            log("  retry", out, e)
            time.sleep(10 * (t + 1))
    raise RuntimeError("failed " + url)


def latest_months(n, last_y, last_m):
    out = []
    y, m = last_y, last_m
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def tt_latest(code):
    h = requests.get(TT.format(code=code, q=TT_Q.get(code, "")), headers=UA, timeout=120).text
    m = re.search(r"Latest Available Data: ([A-Za-z]+) (\d{4})", h)
    mon = datetime.strptime(m.group(1), "%B").month
    return int(m.group(2)), mon


SEG_FIELDS = ["YEAR", "MONTH", "ORIGIN", "DEST", "DEST_COUNTRY", "ORIGIN_COUNTRY", "PASSENGERS", "CLASS"]
COUPON_FIELDS = ["ITIN_ID", "SEQ_NUM", "ORIGIN", "DEST", "TRIP_BREAK", "PASSENGERS"]
COORD_FIELDS = ["AIRPORT", "DISPLAY_AIRPORT_NAME", "DISPLAY_AIRPORT_CITY_NAME_FULL", "AIRPORT_STATE_CODE",
                "AIRPORT_COUNTRY_CODE_ISO", "LATITUDE", "LONGITUDE", "AIRPORT_IS_LATEST", "AIRPORT_IS_CLOSED",
                "UTC_LOCAL_TIME_VARIATION"]


def plan():
    seg_y, seg_m = tt_latest("FMG")
    db_y, db_m = tt_latest("FLM")
    seg_months = latest_months(24, seg_y, seg_m)
    db_q = []
    y, q = db_y, (db_m - 1) // 3 + 1
    for _ in range(4):
        db_q.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    # on-time: newest month present in PREZIP listing
    idx = requests.get(PREZIP, headers=UA, timeout=120).text
    otp = sorted({(int(a), int(b)) for a, b in re.findall(
        r"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_(\d{4})_(\d{1,2})\.zip", idx)})
    otp_months = otp[-12:]
    return {"seg_months": seg_months, "db_quarters": db_q[::-1], "otp_months": otp_months}


def download(p):
    log("download: coordinates")
    tt_download("FLL", COORD_FIELDS, "master_coord.zip")
    for y in sorted({y for y, _ in p["seg_months"]}):
        log("download: T-100 segment", y)
        tt_download("FMG", SEG_FIELDS, "t100seg_%d.zip" % y, year=y, period="All")
    for y, q in p["db_quarters"]:
        log("download: DB1B coupon", y, q)
        try:
            tt_download("FLM", COUPON_FIELDS, "db1bcoupon_%d_q%d.zip" % (y, q), year=y, period=q, tries=1)
        except Exception:  # noqa  form timed out -> full pre-zipped quarter (same data, all columns)
            http_get(PREZIP + "Origin_and_Destination_Survey_DB1BCoupon_%d_%d.zip" % (y, q),
                     "db1bcoupon_full_%d_q%d.zip" % (y, q))
    for y, m in p["otp_months"]:
        f = "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_%d_%d.zip" % (y, m)
        log("download: on-time", y, m)
        http_get(PREZIP + f, "otp_%d_%02d.zip" % (y, m))


I94_PAGE = "https://www.trade.gov/i-94-arrivals-historical-data"
NPS_STATS = "https://irmaservices.nps.gov/v3/rest/stats/visitation"
NPS_BOUND = ("https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/"
             "NPS_Land_Resources_Division_Boundary_and_Tract_Data_Service/FeatureServer/2/query")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def i94_download(n=12):
    """Monthly FINAL 'Summary & Analysis' workbooks (Table G = first intended address)."""
    try:
        h = requests.get(I94_PAGE, headers=UA, timeout=120).text
    except Exception:  # noqa
        h = ""
    opts = re.findall(r'<option value="(/?sites/default/files/[^"]+\.xlsx)"[^>]*>\s*([A-Za-z]+) (\d{4})', h)
    if not opts:  # trade.gov sometimes serves a bot challenge -> use cached monthly files
        fs = sorted(f for f in os.listdir(CACHE) if re.fullmatch(r"i94_\d{4}_\d{2}\.xlsx", f))[-n:]
        log("  I-94 page unavailable; using cached", len(fs), "files")
        return [(int(f[4:8]), int(f[9:11]), f) for f in fs]
    got = []
    for href, mon, yr in opts[:n]:
        m = MONTHS.index(mon) + 1
        f = "i94_%s_%02d.xlsx" % (yr, m)
        http_get("https://www.trade.gov/" + href.lstrip("/"), f)
        got.append((int(yr), m, f))
    return got


def nps_download(p):
    # latest 12 months that IRMA has published
    y, m = p["seg_months"][-1]
    for _ in range(12):
        r = requests.get(NPS_STATS, params=dict(startMonth=m, startYear=y, endMonth=m, endYear=y),
                         headers=dict(UA, Accept="application/json"), timeout=120).json()
        if r:
            break
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months = latest_months(12, y, m)
    path = os.path.join(CACHE, "nps_visits_%d_%02d.json" % (y, m))
    if not os.path.exists(path):
        (y0, m0), (y1, m1) = months[0], months[-1]
        r = requests.get(NPS_STATS, params=dict(startMonth=m0, startYear=y0, endMonth=m1, endYear=y1),
                         headers=dict(UA, Accept="application/json"), timeout=600).json()
        json.dump(r, open(path, "w"))
        log("  got NPS visitation rows", len(r))
    bpath = os.path.join(CACHE, "nps_boundary.json")
    if not os.path.exists(bpath):
        feats, off = [], 0
        while True:
            r = requests.get(NPS_BOUND, params=dict(where="1=1", outFields="UNIT_CODE,UNIT_NAME,UNIT_TYPE,STATE",
                                                    returnGeometry="true", outSR=4326, maxAllowableOffset=0.01,
                                                    resultOffset=off, resultRecordCount=200, f="json"),
                             headers=UA, timeout=600).json()
            feats += r.get("features", [])
            if not r.get("exceededTransferLimit") and len(r.get("features", [])) < 200:
                break
            off += 200
        json.dump(feats, open(bpath, "w"))
        log("  got NPS boundaries", len(feats))
    return path, bpath, months


# ----------------------------------------------------------------- build
def read_zip_csv(path, usecols=None, chunks=False):
    import pandas as pd
    z = zipfile.ZipFile(path)
    name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    kw = dict(usecols=usecols, low_memory=False, encoding="latin-1")
    if chunks:
        return pd.read_csv(z.open(name), chunksize=2_000_000, **kw)
    return pd.read_csv(z.open(name), **kw)


def hav_km(lon1, lat1, lon2, lat2):
    import numpy as np
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def build_airports(p):
    import pandas as pd
    coord = read_zip_csv(os.path.join(CACHE, "master_coord.zip"))
    coord = coord[(coord.AIRPORT_IS_LATEST == 1)].drop_duplicates("AIRPORT")
    coord = coord.set_index("AIRPORT")

    # ---- T-100 segment: arriving passengers at US airports (scheduled F + charter L)
    frames = []
    for y in sorted({y for y, _ in p["seg_months"]}):
        df = read_zip_csv(os.path.join(CACHE, "t100seg_%d.zip" % y))
        df = df[(df.DEST_COUNTRY == "US") & (df.CLASS.isin(["F", "L"])) & (df.PASSENGERS > 0)]
        df["intl"] = df.ORIGIN_COUNTRY != "US"
        frames.append(df.groupby(["DEST", "YEAR", "MONTH", "intl"]).PASSENGERS.sum().reset_index())
    seg = pd.concat(frames)
    seg["ym"] = seg.YEAR * 100 + seg.MONTH
    months = [y * 100 + m for y, m in p["seg_months"]]
    last12, prev12 = months[12:], months[:12]
    tot = seg.groupby(["DEST", "ym"]).PASSENGERS.sum().unstack(fill_value=0).reindex(columns=months, fill_value=0)
    intl = seg[seg.intl].groupby("DEST").apply(lambda g: g[g.ym.isin(last12)].PASSENGERS.sum())
    pax12 = tot[last12].sum(axis=1)
    paxp = tot[prev12].sum(axis=1)

    # ---- DB1B coupon: destination (trip break) vs connection
    od = defaultdict(lambda: [0.0, 0.0, 0.0])  # dest -> [all coupon pax, trip-break pax, trip-break & non-resident]
    for y, q in p["db_quarters"]:
        f = os.path.join(CACHE, "db1bcoupon_%d_q%d.zip" % (y, q))
        ff = os.path.join(CACHE, "db1bcoupon_full_%d_q%d.zip" % (y, q))
        if os.path.exists(f):
            df = read_zip_csv(f, usecols=COUPON_FIELDS)
        elif os.path.exists(ff):
            cam = dict(zip(["ItinID", "SeqNum", "Origin", "Dest", "Break", "Passengers"], COUPON_FIELDS))
            df = read_zip_csv(ff, usecols=lambda c: c in cam).rename(columns=cam)
        else:
            log("  DB1B missing", y, q, "- skipped")
            continue
        p.setdefault("db_used", []).append((y, q))
        first = df[df.SEQ_NUM == 1].set_index("ITIN_ID").ORIGIN
        df["home"] = df.ITIN_ID.map(first)
        df["brk"] = df.TRIP_BREAK.astype(str).str.upper().eq("X")
        df["vis"] = df.brk & (df.home != df.DEST)
        g = df.groupby("DEST").agg(a=("PASSENGERS", "sum"))
        g["b"] = df[df.brk].groupby("DEST").PASSENGERS.sum()
        g["v"] = df[df.vis].groupby("DEST").PASSENGERS.sum()
        g = g.fillna(0)
        for k, r in g.iterrows():
            od[k][0] += r.a
            od[k][1] += r.b
            od[k][2] += r.v
        log("  DB1B", y, q, "rows", len(df))
        del df

    # ---- On-time: arrival local-hour histogram
    hours = defaultdict(lambda: [0] * 24)
    for y, m in p["otp_months"]:
        f = os.path.join(CACHE, "otp_%d_%02d.zip" % (y, m))
        df = read_zip_csv(f, usecols=["Dest", "ArrTime", "CRSArrTime", "Cancelled", "Diverted"])
        t = df.ArrTime.where(df.ArrTime.notna() & (df.Cancelled == 0) & (df.Diverted == 0), df.CRSArrTime)
        hr = (t.fillna(0).astype(int) // 100) % 24
        c = pd.crosstab(df.Dest, hr)
        for a, row in c.iterrows():
            h = hours[a]
            for k, v in row.items():
                h[int(k)] += int(v)
    otp_flights = {a: sum(v) for a, v in hours.items()}

    airports = {}
    for a, n12 in pax12.items():
        if n12 < 10000 or a not in coord.index:   # commercial-service threshold (~FAA 10k)
            continue
        c = coord.loc[a]
        if pd.isna(c.LATITUDE):
            continue
        mon = tot.loc[a, last12].values
        # month-of-year share (index 0 = Jan)
        moy = [0.0] * 12
        for ym, v in zip(last12, mon):
            moy[ym % 100 - 1] += v
        s = sum(moy) or 1
        moy = [round(v / s, 4) for v in moy]
        peak = [i + 1 for i in sorted(range(12), key=lambda i: -moy[i])[:3]]
        o = od.get(a)
        od_share = round(o[1] / o[0], 3) if o and o[0] >= 50 else None
        vis_share = round(o[2] / o[0], 3) if o and o[0] >= 50 else None
        hh = hours.get(a)
        hs = None
        if hh and sum(hh) >= 100:
            tt = sum(hh)
            hs = [round(v / tt, 4) for v in hh]
        airports[a] = {
            "name": str(c.DISPLAY_AIRPORT_NAME), "city": str(c.DISPLAY_AIRPORT_CITY_NAME_FULL),
            "lon": round(float(c.LONGITUDE), 4), "lat": round(float(c.LATITUDE), 4),
            "pax_12m": int(round(n12, -1)),
            "intl_pax_12m": int(round(float(intl.get(a, 0)), -1)),
            "pax_prev_12m": int(round(paxp.get(a, 0), -1)),
            "pax_yoy": round(n12 / paxp[a] - 1, 3) if paxp.get(a, 0) > 0 else None,
            "monthly_pax": [int(round(v, -1)) for v in tot.loc[a, months].values],
            "seasonality": moy, "peak_months": peak,
            "od_share": od_share, "visitor_share": vis_share,
            "db1b_sample_pax": int(o[0]) if o else 0,
            "arrival_hour_share": hs, "otp_flights_12m": otp_flights.get(a, 0),
            "checkin_window": checkin_window(hs),
        }
    log("airports", len(airports))
    return airports, months


def checkin_window(hs):
    """Shortest contiguous (circular) hour span holding >= 50% of arrivals."""
    if not hs:
        return None
    best = None
    for L in range(1, 25):
        for s in range(24):
            if sum(hs[(s + i) % 24] for i in range(L)) >= 0.5:
                best = (s, (s + L) % 24)
                break
        if best:
            break
    peak = max(range(24), key=lambda i: hs[i])
    return {"start_hour": best[0], "end_hour": best[1], "peak_hour": peak}


def build_i94():
    import openpyxl
    files = i94_download(12)
    state, monthly = defaultdict(int), defaultdict(lambda: [0] * 12)
    for y, m, f in files:
        wb = openpyxl.load_workbook(os.path.join(CACHE, f), read_only=True, data_only=True)
        ws = wb["Table G"]
        hdr = None
        for r in ws.iter_rows(values_only=True):
            if r[0] and "State/Territory" in str(r[0]):
                hdr = [str(x).replace("\n", " ") if x else "" for x in r]
                io_, im = hdr.index("Total Overseas"), next(i for i, x in enumerate(hdr) if x.startswith("Mexico"))
                continue
            if hdr and r[0] and isinstance(r[io_], (int, float)):
                nm = str(r[0]).replace("\xa0", " ").strip()
                if nm.lower().startswith(("total", "grand total", "not r", "*", "unknown")):
                    continue
                v = int(r[io_] or 0) + int(r[im] or 0)
                state[nm] += v
                monthly[nm][m - 1] += v
    months = sorted((y, m) for y, m, _ in files)
    return dict(state), {k: v for k, v in monthly.items()}, months


def build_nps(p):
    import numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    vpath, bpath, months = nps_download(p)
    visits, vmon = defaultdict(int), defaultdict(lambda: [0] * 12)
    names = {}
    for r in json.load(open(vpath)):
        visits[r["UnitCode"]] += int(r["RecreationVisitors"] or 0)
        vmon[r["UnitCode"]][int(r["Month"]) - 1] += int(r["RecreationVisitors"] or 0)
        names[r["UnitCode"]] = r["UnitName"]
    units = {}
    for f in json.load(open(bpath)):
        a = f["attributes"]
        code = a["UNIT_CODE"]
        rings = f.get("geometry", {}).get("rings") or []
        if not rings:
            continue
        geom = unary_union([Polygon(r).buffer(0) for r in rings if len(r) >= 4])
        if geom.is_empty:
            continue
        if code in units:
            geom = units[code]["geom"].union(geom)
        c = geom.representative_point()
        units[code] = {"geom": geom, "name": names.get(code, a["UNIT_NAME"]), "lon": c.x, "lat": c.y}
    return units, visits, vmon, months


def county_rows():
    d = json.load(open(COUNTIES))
    c = d["cols"]
    i_f, i_x, i_y, i_s = c.index("fips"), c.index("lon"), c.index("lat"), c.index("STUSPS")
    i_sn = c.index("STATE_NAME")
    return [(r[i_f], r[i_x], r[i_y], r[i_s], r[i_sn]) for r in d["rows"] if r[i_x] is not None]


def build(p):
    import numpy as np
    from shapely.geometry import Point
    airports, months = build_airports(p)
    i94, i94_mon, i94_months = build_i94()
    units, visits, vmon, nps_months = build_nps(p)
    rows = county_rows()

    codes = list(airports)
    alon = np.array([airports[a]["lon"] for a in codes])
    alat = np.array([airports[a]["lat"] for a in codes])
    med_vis = float(np.median([v["visitor_share"] for v in airports.values() if v["visitor_share"] is not None]))
    med_od = float(np.median([v["od_share"] for v in airports.values() if v["od_share"] is not None]))

    # NPS: km from county centroid to nearest point of unit boundary (0 if inside)
    ucodes = [u for u in units if visits.get(u, 0) > 0]
    ulon = np.array([units[u]["lon"] for u in ucodes])
    ulat = np.array([units[u]["lat"] for u in ucodes])

    counties = {}
    for fips, lon, lat, st, stname in rows:
        dk = hav_km(lon, lat, alon, alat)
        near = sorted([(float(dk[i]), codes[i]) for i in np.where(dk <= AIRPORT_KM)[0]])
        vp, totp, seas = 0.0, 0.0, np.zeros(12)
        for km, a in near:
            ap = airports[a]
            share = ap["visitor_share"] if ap["visitor_share"] is not None else med_vis
            vp += ap["pax_12m"] * share
            totp += ap["pax_12m"]
            seas += np.array(ap["seasonality"]) * ap["pax_12m"] * share
        # NPS within 80 km: prefilter on representative point (parks < ~400 km across)
        du = hav_km(lon, lat, ulon, ulat)
        parks, nv, nseas = [], 0, np.zeros(12)
        for i in np.where(du <= 400)[0]:
            u = ucodes[i]
            g = units[u]["geom"]
            pt = Point(lon, lat)
            if g.contains(pt):
                km = 0.0
            else:
                from shapely.ops import nearest_points
                q = nearest_points(g, pt)[0]
                km = float(hav_km(lon, lat, q.x, q.y))
            if km <= NPS_KM:
                parks.append((round(km, 1), u, visits[u]))
                nv += visits[u]
                nseas += np.array(vmon[u])
        parks.sort(key=lambda t: -t[2])
        s = seas.sum()
        ns = nseas.sum()
        counties[fips] = {
            "airports": [{"iata": a, "km": round(km, 1)} for km, a in near],
            "pax_12m_within_60km": int(round(totp, -2)),
            "visitor_pax_12m_within_60km": int(round(vp, -2)),
            "intl_arrivals_state": i94.get(stname),
            "nps_visits_80km": int(round(nv, -2)),
            "nps_units": [{"code": u, "km": km, "visits": int(round(v, -2))} for km, u, v in parks[:5]],
            "seasonality": [round(float(x / s), 3) for x in seas] if s > 0 else None,
            "nps_seasonality": [round(float(x / ns), 3) for x in nseas] if ns > 0 else None,
        }

    nps_out = {u: {"name": units[u]["name"], "lon": round(units[u]["lon"], 4), "lat": round(units[u]["lat"], 4),
                   "visits_12m": int(visits[u])} for u in ucodes}
    for a in airports.values():
        a.pop("db1b_sample_pax", None)
    ym = lambda t: "%d-%02d" % t  # noqa
    meta = {
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "aggregate_only": "All values are aggregate counts of passengers/visits; no individual-level data.",
        "periods": {
            "t100_last12": [ym(p["seg_months"][12]), ym(p["seg_months"][-1])],
            "t100_prev12": [ym(p["seg_months"][0]), ym(p["seg_months"][11])],
            "db1b_quarters": ["%dQ%d" % tuple(t) for t in p.get("db_used", p["db_quarters"])],
            "ontime_months": [ym(p["otp_months"][0]), ym(p["otp_months"][-1])],
            "i94_months": [ym(i94_months[0]), ym(i94_months[-1])],
            "nps_months": [ym(nps_months[0]), ym(nps_months[-1])],
        },
        "fields": {
            "pax_12m": "T-100 segment passengers arriving (deplaned) at airport, all carriers, scheduled+charter, domestic+international origins, last 12 months",
            "pax_yoy": "pax_12m / previous 12 months - 1",
            "monthly_pax": "24 monthly arrivals, oldest first (periods.t100_prev12[0] .. t100_last12[1])",
            "seasonality": "share of last-12-month arrivals by calendar month, index 0 = January",
            "peak_months": "top-3 calendar months (1=Jan) by arrivals",
            "od_share": "DB1B coupon pax arriving here whose trip breaks here (destination/stopover) / all coupon pax arriving here; 1-od_share = connecting",
            "visitor_share": "as od_share but also excluding itineraries that started at this airport (residents returning home) = inbound visitors",
            "arrival_hour_share": "share of reporting-carrier arrivals by LOCAL hour 0-23 (actual arrival time; scheduled if cancelled/diverted)",
            "checkin_window": "shortest contiguous local-hour span holding >=50% of arrivals (end exclusive) + peak hour",
            "county.visitor_pax_12m_within_60km": "sum over airports within 60 km (straight line from county centroid) of pax_12m * visitor_share (median share if missing); airports are counted for every county in range (not apportioned)",
            "county.intl_arrivals_state": "NTTO I-94 overseas + Mexico (air/sea) visitor arrivals with first intended address in county's state, 12 months (Canada not included)",
            "county.nps_visits_80km": "NPS recreation visits, 12 months, to park units whose boundary lies within 80 km of county centroid",
            "county.seasonality": "visitor-pax-weighted month-of-year share of the county's airports (index 0 = Jan)",
        },
        "sources": SOURCES,
        "attribution": ("Data: U.S. DOT Bureau of Transportation Statistics (T-100, DB1B, On-Time, Master Coordinate); "
                        "U.S. Dept. of Commerce ITA National Travel and Tourism Office (I-94 arrivals); "
                        "U.S. National Park Service (IRMA Visitor Use Statistics, Land Resources Division boundaries). "
                        "U.S. Government works, public domain (17 U.S.C. 105)."),
        "rejected": REJECTED,
    }
    out = {"meta": meta, "airports": airports, "nps_units": nps_out,
           "i94_states": {k: {"arrivals_12m": v, "monthly": i94_mon[k]} for k, v in i94.items()},
           "counties": counties}
    with open(OUT, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    log("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
    return out


SOURCES = [
    {"name": "BTS T-100 Segment (All Carriers)", "url": "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45",
     "licence": "Public Domain U.S. Government (BTS open-data catalog label, data.bts.gov e.g. https://data.bts.gov/d/bu82-4pwz); US federal work, 17 U.S.C. 105"},
    {"name": "BTS DB1B Origin & Destination Survey - Coupon (10% ticket sample)", "url": "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FLM&QO_fu146_anzr=N8vn6v10%20f722146%20gnoyr5",
     "licence": "US federal work (BTS), public domain, 17 U.S.C. 105"},
    {"name": "BTS Reporting Carrier On-Time Performance (1987-present)", "url": "https://transtats.bts.gov/PREZIP/",
     "licence": "Public Domain U.S. Government (data.bts.gov label, e.g. https://data.bts.gov/d/rns4-gpqn); 17 U.S.C. 105"},
    {"name": "BTS Aviation Support Tables - Master Coordinate", "url": "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FLL&QO_fu146_anzr=N8vn6v10%20f722146%20gnoyr5",
     "licence": "US federal work (BTS), public domain, 17 U.S.C. 105"},
    {"name": "ITA/NTTO I-94 Arrivals - monthly Summary & Analysis, Table G (first intended address)", "url": I94_PAGE,
     "licence": "US federal statistical product (Dept. of Commerce ITA/NTTO from DHS/CBP ADIS I-94); public domain, 17 U.S.C. 105. Table G uses overseas + Mexico (I-94) columns only - the Statistics Canada component is not used."},
    {"name": "NPS IRMA Visitor Use Statistics REST (v3)", "url": NPS_STATS,
     "licence": "NPS: 'material created by the National Park Service ... is generally considered in the public domain' https://www.nps.gov/aboutus/disclaimer.htm (commercial republication must credit the U.S. Government source)"},
    {"name": "NPS Land Resources Division unit boundaries (ArcGIS feature service)", "url": NPS_BOUND.rsplit("/query", 1)[0],
     "licence": "NPS public-domain data (same NPS disclaimer)"},
    {"name": "County centroids", "url": "web/usa/data/counties.json (project file, US Census derived)", "licence": "US Census Bureau, public domain"},
]

REJECTED = [
    {"name": "AirDNA", "reason": "commercial/proprietary STR data, paid licence, no redistribution"},
    {"name": "OpenSky Network", "reason": "terms restrict use to non-commercial research"},
    {"name": "ADS-B Exchange", "reason": "commercial data licensing required for business use; not permanently free"},
    {"name": "FlightAware / Flightradar24", "reason": "proprietary, paid commercial APIs, ToS forbid redistribution"},
    {"name": "SafeGraph / Advan / Placer.ai", "reason": "proprietary device-level-derived mobility data, paid licences"},
    {"name": "Google Community Mobility Reports", "reason": "discontinued (Oct 2022); Google ToS, not an open licence"},
    {"name": "Statistics Canada component of NTTO arrivals", "reason": "not needed (state-level table is I-94 overseas+Mexico only); StatCan Open Licence would also require separate attribution"},
]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    pp = os.path.join(CACHE, "plan.json")
    if mode == "build" and os.path.exists(pp):
        p = json.load(open(pp))
        p = {k: [tuple(x) for x in v] for k, v in p.items()}
    else:
        p = plan()
        json.dump(p, open(pp, "w"))
    log("plan", {k: (v[0], v[-1]) for k, v in p.items()})
    if mode in ("download", "all"):
        download(p)
    if mode in ("build", "all"):
        build(p)

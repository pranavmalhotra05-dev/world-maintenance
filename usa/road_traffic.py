"""Road traffic volumes (aggregate AADT / continuous-count volumes only; never individual vehicles or people).

  .venv\\Scripts\\python -u -W ignore usa/road_traffic.py                 # all stages
  .venv\\Scripts\\python -u -W ignore usa/road_traffic.py counties tmas  # national file only
  .venv\\Scripts\\python -u -W ignore usa/road_traffic.py blocks [<slug> ...]

Stages
  counties : FHWA HPMS public sections (geo.dot.gov Hosted/HPMS_FULL_<ST>_<YEAR>), server-side grouped statistics per
             county: daily VMT = sum(AADT x section miles) on mainline roadways (facility_type 1/2), busiest section AADT,
             length-weighted mean AADT on arterials (f_system 3-4), truck share (single-unit + combination AADT, sections
             that report both), YoY VMT change latest vs previous HPMS year.
  tmas     : NTAD Travel Monitoring Analysis System (TMAS) 2025 continuous-count stations + daily volume table
             (BTS / FHWA). Station monthly ADT -> per-state and per-county seasonal index (month ADT / annual mean).
  blocks   : for every web/usa/blocks/<slug>/meta.json: HPMS sections (+ CDOT traffic counts in CO) within 300 m
             of the block -> meta.traffic; per non-garage house h.traffic (nearest road with an AADT, its distance,
             busiest AADT within 150 m). MnDOT AADT is not used (no explicit open licence; HPMS covers MN).
Output: web/usa/data/road_traffic.json ; meta.traffic + h.traffic in each block (written under usa.blocklock).
Licences: see usa/research/road_traffic.md. All fetching happens before the block lock is taken.
"""
import json
import math
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, MultiLineString, Point

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
OUT = ROOT / "web" / "usa" / "data" / "road_traffic.json"
CACHE = ROOT / "usa" / "_cache" / "traffic"
CACHE.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) roofprint-usa/0.1 (open-data research)"

HPMS = "https://geo.dot.gov/server/rest/services/Hosted"
CDOT = "https://dtdapps.codot.gov/server/rest/services/Webapps/open_data_sde/FeatureServer/13"
TMAS_ST_ITEM = "ac3f8dd9c4454c20b8d3b7ac5247c8f4"
TMAS_VOL_ITEM = "5a9462b519854ec6a2334b3c0bdfc3c1"
AGO = "https://www.arcgis.com/sharing/rest/content/items/"
RADIUS_M, HOUSE_R_M, FALLBACK_M = 300, 150, 1000

FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12",
        "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23",
        "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
        "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
        "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55",
        "WY": "56", "PR": "72"}
ABBR = {v: k for k, v in FIPS.items()}

SOURCES = {
    "hpms": {"name": "FHWA Highway Performance Monitoring System (HPMS) public sections, via USDOT geo.dot.gov",
             "url": "https://geo.dot.gov/server/rest/services/Hosted (HPMS_FULL_<ST>_<YEAR>)",
             "licence": "US federal government work (17 U.S.C. 105), public domain. HPMS public release data is published "
                        "by FHWA for unrestricted public use; NTAD notice: \"This NTAD dataset is a work of the United States "
                        "government as defined in 17 U.S.C. 101 and as such are not protected by any U.S. copyrights. This "
                        "work is available for unrestricted public use.\"",
             "licence_url": "https://www.fhwa.dot.gov/policyinformation/hpms.cfm"},
    "tmas": {"name": "NTAD Travel Monitoring Analysis System (TMAS) 2025 stations + volume tables (FHWA via BTS)",
             "url": AGO + TMAS_VOL_ITEM + " ; " + AGO + TMAS_ST_ITEM,
             "licence": "\"This NTAD dataset is a work of the United States government as defined in 17 U.S.C. 101 and as "
                        "such are not protected by any U.S. copyrights. This work is available for unrestricted public use.\"",
             "licence_url": "https://www.arcgis.com/home/item.html?id=" + TMAS_VOL_ITEM},
    "tvt": {"name": "FHWA Traffic Volume Trends (monthly), Table 5 state VMT and % change from same month previous year",
            "url": "https://highways.dot.gov/policy/policy-information/resources/tvt",
            "licence": "US federal government work (17 U.S.C. 105), public domain; FHWA monthly publication, no use restriction.",
            "licence_url": "https://www.transportation.gov/web-policies"},
    "cdot": {"name": "CDOT Highways: Traffic Counts (Colorado DOT open data)",
             "url": CDOT,
             "licence": "\"This work is licensed under a Creative Commons Attribution-ShareAlike 4.0 License\" "
                        "(attribution: Colorado Department of Transportation; share-alike applies to derived datasets)",
             "licence_url": "https://www.arcgis.com/home/item.html?id=723375d00be646b0bf87313d4c474a25"},
}


REJECTED = {"INRIX / TomTom / HERE / StreetLight / Google traffic": "commercial licensed probe data",
            "FHWA NPMRDS": "licensed (INRIX-derived) - restricted to public agencies",
            "TxDOT AADT services": "licence: 'may not be used for commercial purposes or resold'",
            "ODOT Oklahoma AADT_Network": "licence: 'Authorized reference use only'",
            "MnDOT AADT segments": "item states only 'Use limitations: None' - no explicit open licence (grey); MN roads "
                                   "are covered by HPMS (MnDOT's own federal submission)",
            "ODOT Ohio / MoDOT / ALDOT": "no official agency-hosted AADT service with a stated open licence "
                                         "found (only third-party copies); covered via HPMS (state submissions)"}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00")


def get_json(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=180)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(str(d["error"])[:300])
            return d
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


# ----------------------------------------------------------------------------------------------------- HPMS services
def hpms_services():
    p = CACHE / "hpms_services.json"
    if p.exists() and time.time() - p.stat().st_mtime < 7 * 86400:
        return json.load(open(p))
    d = get_json(HPMS, {"f": "json"})
    svc = {}
    for s in d["services"]:
        n = s["name"].split("/")[-1]
        if s["type"] != "FeatureServer" or not n.upper().startswith("HPMS_FULL_"):
            continue
        parts = n.split("_")
        st, yr = parts[2].upper(), parts[-1]
        st = st[:2]  # e.g. GA3 -> GA
        if st in FIPS and yr.isdigit():
            svc.setdefault(st, {})[yr] = n
    json.dump(svc, open(p, "w"), indent=0)
    return svc


def stat_query(svc, where, stats, group="county_id"):
    url = f"{HPMS}/{svc}/FeatureServer/0/query"
    d = get_json(url, {"where": where, "groupByFieldsForStatistics": group, "outStatistics": json.dumps(stats), "f": "json"})
    return [f["attributes"] for f in d.get("features", [])]


def county_stats(st, svc):
    cp = CACHE / "hpms_county" / f"{svc}.json"
    cp.parent.mkdir(exist_ok=True)
    if cp.exists():
        return json.load(open(cp))
    flds = [f["name"].lower() for f in get_json(f"{HPMS}/{svc}/FeatureServer/0", {"f": "json"})["fields"]]
    L = "section_length" if "section_length" in flds else "sectionlength"   # 2025 services renamed the field
    main = "aadt > 0 AND facility_type IN (1,2)"
    out = {}
    a = stat_query(svc, main, [
        {"statisticType": "sum", "onStatisticField": "aadt*" + L, "outStatisticFieldName": "vmt"},
        {"statisticType": "max", "onStatisticField": "aadt", "outStatisticFieldName": "mx"},
        {"statisticType": "sum", "onStatisticField": L, "outStatisticFieldName": "mi"}])
    for r in a:
        out[str(r["county_id"])] = {"vmt": r["vmt"], "max": r["mx"], "mi": r["mi"]}
    b = stat_query(svc, main + " AND f_system IN (3,4)", [
        {"statisticType": "sum", "onStatisticField": "aadt*" + L, "outStatisticFieldName": "v"},
        {"statisticType": "sum", "onStatisticField": L, "outStatisticFieldName": "l"}])
    for r in b:
        out.setdefault(str(r["county_id"]), {})["art"] = (r["v"] / r["l"]) if r["l"] else None
    c = stat_query(svc, main + " AND aadt_single_unit IS NOT NULL AND aadt_combination IS NOT NULL", [
        {"statisticType": "sum", "onStatisticField": "(aadt_single_unit+aadt_combination)*" + L, "outStatisticFieldName": "t"},
        {"statisticType": "sum", "onStatisticField": "aadt*" + L, "outStatisticFieldName": "v"}])
    for r in c:
        out.setdefault(str(r["county_id"]), {})["truck"] = (r["t"] / r["v"]) if r["v"] else None
    json.dump(out, open(cp, "w"))
    return out


def stage_counties():
    svc = hpms_services()
    counties, states, status = {}, {}, {}
    from concurrent.futures import ThreadPoolExecutor
    jobs = []
    for st in svc:
        yrs = sorted(svc[st], reverse=True)
        jobs += [(st, svc[st][y]) for y in yrs[:1] + [y for y in yrs[1:2] if int(y) == int(yrs[0]) - 1]]

    def warm(j):
        try:
            county_stats(*j)
        except Exception as e:
            print("warm fail", j[1], str(e)[:120])
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(warm, jobs))
    for st in sorted(svc):
        yrs = sorted(svc[st], reverse=True)
        latest = yrs[0]
        prev = str(int(latest) - 1)
        try:
            cur = county_stats(st, svc[st][latest])
        except Exception as e:
            status[st] = f"FAIL {latest}: {e}"[:200]; print("FAIL", st, status[st]); continue
        old = None
        if prev in svc[st]:
            try:
                old = county_stats(st, svc[st][prev])
            except Exception as e:
                status[st] = f"prev {prev} failed: {e}"[:200]
        tv = sum(v.get("vmt") or 0 for v in cur.values())
        state_yoy = None
        if old:
            tv0 = sum(v.get("vmt") or 0 for v in old.values())
            state_yoy = round(tv / tv0 - 1, 4) if tv0 else None
            # statewide VMT never moves >8 %/yr in practice (TVT shows +-3 %); a bigger jump means the two HPMS
            # submissions are not comparable (re-segmentation / partial upload) -> suppress county YoY for that state.
            if state_yoy is None or abs(state_yoy) > 0.08:
                status[st] = f"yoy suppressed: statewide HPMS VMT {latest} vs {prev} = {state_yoy:+.1%}" if state_yoy is not None else "yoy suppressed"
                old = None
        states[FIPS[st]] = {"abbr": st, "hpms_year": int(latest), "yoy_vs": int(prev) if old else None,
                            "state_yoy": state_yoy, "vmt_daily": round(tv), "counties": len(cur)}
        for cid, v in cur.items():
            if not cid.isdigit() or int(cid) == 0 or v.get("vmt") is None:
                continue
            f = FIPS[st] + f"{int(cid):03d}"
            yoy = None
            if old and (old.get(cid) or {}).get("vmt"):
                yoy = round(v["vmt"] / old[cid]["vmt"] - 1, 4)
            counties[f] = {"vmt_daily": round(v["vmt"]), "max_aadt": v.get("max"),
                           "mean_aadt_arterial": round(v["art"]) if v.get("art") else None,
                           "truck_pct": round(100 * v["truck"], 1) if v.get("truck") is not None else None,
                           "yoy": yoy, "road_mi": round(v.get("mi") or 0, 1), "year": int(latest)}
        status.setdefault(st, "ok")
        print(f"ok   {st} {latest} counties={len(cur)} vmt={tv/1e6:.1f}M prev={'yes' if old else 'no'}")
    return counties, states, status


# ----------------------------------------------------------------------------------------------------- TMAS
def fetch_item(item, name):
    d = CACHE / "dl"
    d.mkdir(exist_ok=True)
    z = d / name
    if not z.exists():
        with S.get(AGO + item + "/data", stream=True, timeout=600) as r:
            r.raise_for_status()
            tmp = z.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for ch in r.iter_content(1 << 20):
                    fh.write(ch)
            os.replace(tmp, z)
    x = d / "x"
    with zipfile.ZipFile(z) as zf:
        gdb = zf.namelist()[0].split("/")[0]
        if not (x / gdb).exists():
            zf.extractall(x)
    return x / gdb


def stage_tmas():
    import pyogrio
    agg = CACHE / "tmas_monthly.parquet"
    st_gdb = fetch_item(TMAS_ST_ITEM, "TMAS_Stations_Table.zip")
    stations = pyogrio.read_dataframe(st_gdb, read_geometry=False)
    stations["key"] = stations["state_code"].str.zfill(2) + ":" + stations["station_id"].astype(str)
    if not agg.exists():
        vol_gdb = fetch_item(TMAS_VOL_ITEM, "TMAS_Volume_Table.zip")
        layer = pyogrio.list_layers(vol_gdb)[0][0]
        n = pyogrio.read_info(vol_gdb, layer=layer)["features"]
        hrs = [f"hour_{h:02d}" for h in range(24)]
        cols = ["state_code", "station_id", "travel_dir", "travel_lane", "month_record", "day_record"] + hrs
        parts, step = [], 1_000_000
        for off in range(0, n, step):
            df = pyogrio.read_dataframe(vol_gdb, layer=layer, columns=cols, read_geometry=False,
                                        skip_features=off, max_features=step, use_arrow=True)
            h = df[hrs].apply(pd.to_numeric, errors="coerce")
            ok = h.notna().all(axis=1) & (h >= 0).all(axis=1)
            df = df.loc[ok, cols[:6]].copy()
            df["vol"] = h.loc[ok].sum(axis=1).astype("int64")
            df["key"] = df["state_code"].astype(str).str.zfill(2) + ":" + df["station_id"].astype(str)
            df["travel_dir"] = pd.to_numeric(df["travel_dir"], errors="coerce").fillna(-1).astype(int)
            df["travel_lane"] = pd.to_numeric(df["travel_lane"], errors="coerce").fillna(-1).astype(int)
            parts.append(df[["key", "travel_dir", "travel_lane", "month_record", "day_record", "vol"]])
            print(f"  tmas rows {off + len(h):,}/{n:,}")
        v = pd.concat(parts, ignore_index=True)
        v["month_record"] = pd.to_numeric(v["month_record"]).astype(int)
        v["day_record"] = pd.to_numeric(v["day_record"]).astype(int)
        # lane 0 = all lanes of a direction combined; direction 9 = both directions combined (TMG ch.6). Never double count.
        has0 = v.groupby(["key", "travel_dir", "month_record", "day_record"])["travel_lane"].transform(lambda s: (s == 0).any())
        v = v[~has0 | (v["travel_lane"] == 0)]
        dd = v.groupby(["key", "travel_dir", "month_record", "day_record"], as_index=False)["vol"].sum()
        has9 = dd.groupby(["key", "month_record", "day_record"])["travel_dir"].transform(lambda s: (s == 9).any())
        dd = dd[~has9 | (dd["travel_dir"] == 9)]
        day = dd.groupby(["key", "month_record", "day_record"], as_index=False)["vol"].sum()
        m = day.groupby(["key", "month_record"]).agg(madt=("vol", "mean"), days=("vol", "size")).reset_index()
        m.to_parquet(agg)
    m = pd.read_parquet(agg)
    m = m[m["days"] >= 7]
    first = stations.drop_duplicates("key").set_index("key")
    st_out, by_state, by_county = [], {}, {}
    for key, g in m.groupby("key"):
        if key not in first.index:
            continue
        s = first.loc[key]
        sf = key.split(":")[0]
        cc = str(s.get("county_code") or "").strip()
        cf = sf + cc.zfill(3) if cc.isdigit() and int(cc) > 0 else None
        g = g.sort_values("month_record")
        last = g.iloc[-1]
        lat, lon = s.get("latitude"), s.get("longitude")
        annual = float(g["madt"].mean())
        st_out.append([sf, key.split(":")[1], round(float(lat), 5) if pd.notna(lat) else None,
                       round(float(lon), 5) if pd.notna(lon) else None, str(s.get("f_system") or ""), cf,
                       int(last["month_record"]), int(round(last["madt"])), int(round(annual)), int(len(g))])
        if len(g) >= 10 and annual > 0:
            idx = {int(r.month_record): r.madt / annual for r in g.itertuples()}
            by_state.setdefault(sf, []).append(idx)
            if cf:
                by_county.setdefault(cf, []).append(idx)

    def season(lst):
        return [round(float(np.median([d[mo] for d in lst if mo in d])), 3) if any(mo in d for d in lst) else None
                for mo in range(1, 13)]
    states = {sf: {"stations": len(l), "seasonal_index": season(l)} for sf, l in by_state.items()}
    counties = {cf: {"ccs_n": len(l), "seasonal_index": season(l)} for cf, l in by_county.items()}
    print(f"ok   tmas stations={len(st_out)} states={len(states)} counties={len(counties)}")
    return st_out, states, counties


# ----------------------------------------------------------------------------------------------------- TVT
STATE_NAMES = {"Alabama": "01", "Alaska": "02", "Arizona": "04", "Arkansas": "05", "California": "06", "Colorado": "08",
               "Connecticut": "09", "Delaware": "10", "District of Columbia": "11", "Florida": "12", "Georgia": "13",
               "Hawaii": "15", "Idaho": "16", "Illinois": "17", "Indiana": "18", "Iowa": "19", "Kansas": "20",
               "Kentucky": "21", "Louisiana": "22", "Maine": "23", "Maryland": "24", "Massachusetts": "25",
               "Michigan": "26", "Minnesota": "27", "Mississippi": "28", "Missouri": "29", "Montana": "30",
               "Nebraska": "31", "Nevada": "32", "New Hampshire": "33", "New Jersey": "34", "New Mexico": "35",
               "New York": "36", "North Carolina": "37", "North Dakota": "38", "Ohio": "39", "Oklahoma": "40",
               "Oregon": "41", "Pennsylvania": "42", "Rhode Island": "44", "South Carolina": "45", "South Dakota": "46",
               "Tennessee": "47", "Texas": "48", "Utah": "49", "Vermont": "50", "Virginia": "51", "Washington": "53",
               "West Virginia": "54", "Wisconsin": "55", "Wyoming": "56"}


def stage_tvt():
    """FHWA Traffic Volume Trends, Table 5 (state VMT, month vs same month previous year).
    highways.dot.gov refuses scripted HTTP clients (403), so the table is read once in the built-in browser and kept
    in usa/_cache/traffic/tvt_<yyyy>_<mm>_table5.json; this stage only reshapes the newest cached table."""
    fs = sorted(CACHE.glob("tvt_*_table5.json"))
    if not fs:
        return None
    t = json.load(open(fs[-1], encoding="utf-8"))
    out = {}
    for nm, v in t["states"].items():
        f = STATE_NAMES.get(nm)
        if f:
            out[f] = {"stations": v[0], "vmt_m_latest": v[1], "vmt_m_prev_year": v[2], "yoy_pct": v[3],
                      "prior_month_stations": v[4], "prior_month_vmt_m": v[5], "prior_month_yoy_pct": v[7]}
    return {"report": t["report"], "url": t["url"], "page": t["page"], "latest_month": "2026-09 (preliminary)",
            "prior_month": "2026-08 (revised)", "states": out}


# ----------------------------------------------------------------------------------------------------- blocks
def geo_q(url, box, fields="*", extra=None):
    w, s, e, n = box
    p = {"where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects", "outSR": 4326, "outFields": fields, "f": "json",
         "resultRecordCount": 2000}
    if extra:
        p.update(extra)
    return get_json(url + "/query", p).get("features", [])


def lines_of(g):
    return [p for p in (g or {}).get("paths", []) if len(p) >= 2]


SIGN = {2: "I-", 3: "US ", 4: "SR "}
FSYS = {1: "Interstate", 2: "Freeway/expressway", 3: "Principal arterial", 4: "Minor arterial", 5: "Major collector",
        6: "Minor collector", 7: "Local road"}


def hpms_name(a):
    nm = a.get("route_name") or a.get("routename")
    num = a.get("route_number") if a.get("route_number") is not None else a.get("routenumber")
    sg = a.get("route_signing") if a.get("route_signing") is not None else a.get("routesigning")
    if num not in (None, 0, "0", "") and sg in SIGN:
        lab = f"{SIGN[sg]}{int(num) if str(num).isdigit() else num}"
        return f"{lab} ({nm})" if nm and str(num) not in str(nm) else lab
    if nm:
        return nm
    fs = FSYS.get(a.get("f_system"), "road")
    return f"{fs} (HPMS route {a.get('route_id')})" if a.get("route_id") else fs


def fetch_block_segments(meta, svc):
    lon0, lat0 = meta["origin"]
    W, So, E, N = meta["bounds"]
    mx = 111320 * math.cos(math.radians(lat0)); my = 110540
    box = (W - FALLBACK_M / mx, So - FALLBACK_M / my, E + FALLBACK_M / mx, N + FALLBACK_M / my)
    segs, status = [], {}
    st = meta.get("state")
    yrs = sorted((svc.get(st) or {}), reverse=True)
    if yrs:
        name = svc[st][yrs[0]]
        try:
            fs = geo_q(f"{HPMS}/{name}/FeatureServer/0", box)
            for f in fs:
                a = f["attributes"]
                if not a.get("aadt") or a.get("facility_type") not in (1, 2, 4):
                    continue
                trucks = None
                if a.get("aadt_single_unit") is not None and a.get("aadt_combination") is not None:
                    trucks = int(a["aadt_single_unit"] + a["aadt_combination"])
                segs.append({"name": hpms_name(a), "aadt": int(a["aadt"]), "year": a.get("data_year"),
                             "trucks": trucks, "f_system": a.get("f_system"), "ramp": a.get("facility_type") == 4,
                             "source": "hpms", "paths": lines_of(f.get("geometry"))})
            status["hpms"] = f"ok {name} ({len(fs)} sections in box)"
        except Exception as e:
            status["hpms"] = f"{type(e).__name__}: {e}"[:200]
    else:
        status["hpms"] = "no HPMS service for state"
    if st == "CO":
        try:
            fs = geo_q(CDOT, box, fields="ROUTE,AADT,AADTYR,AADTTRUCKS,ISRAMP")
            for f in fs:
                a = f["attributes"]
                if not a.get("AADT"):
                    continue
                r = (a.get("ROUTE") or "").lstrip("0")
                segs.append({"name": f"CO route {r}" if r else None, "aadt": int(a["AADT"]),
                             "year": int(a["AADTYR"]) if str(a.get("AADTYR") or "").isdigit() else None,
                             "trucks": a.get("AADTTRUCKS"), "source": "cdot", "paths": lines_of(f.get("geometry"))})
            status["cdot"] = f"ok ({len(fs)} segments)"
        except Exception as e:
            status["cdot"] = f"{type(e).__name__}: {e}"[:200]
    return segs, status


def local_geom(paths, lon0, lat0):
    mx = 111320 * math.cos(math.radians(lat0)); my = 110540
    ls = [LineString([((x - lon0) * mx, (y - lat0) * my) for x, y, *_ in p]) for p in paths]
    return MultiLineString(ls) if len(ls) > 1 else (ls[0] if ls else None)


def build_block(meta, houses, segs):
    lon0, lat0 = meta["origin"]
    mx = 111320 * math.cos(math.radians(lat0)); my = 110540
    W, So, E, N = meta["bounds"]
    half_w = (E - W) * mx / 2; half_h = (N - So) * my / 2
    for s in segs:
        s["geom"] = local_geom(s["paths"], lon0, lat0)
    segs = [s for s in segs if s["geom"] is not None]
    # distance to the block footprint (rectangle) for the 300 m filter; km_from_center from origin
    from shapely.geometry import box as sbox
    blk = sbox(-half_w, -half_h, half_w, half_h)
    near = [s for s in segs if s["geom"].distance(blk) <= RADIUS_M]
    best = {}
    for s in near:
        d = s["geom"].distance(Point(0, 0))
        k = (s["name"], s["aadt"], s["source"])
        if k not in best or d < best[k][0]:
            best[k] = (d, s)
    # the state DOT layer and HPMS usually describe the same section (HPMS is the state's own submission): merge
    # records with the same AADT whose nearest points to the centre are within 40 m, keeping both source tags.
    merged = []
    for d, s in sorted(best.values(), key=lambda t: (-t[1]["aadt"], t[1]["source"] != "hpms", t[0])):
        twin = next((m for m in merged if m["aadt"] == s["aadt"] and abs(m["_d"] - d) <= 40), None)
        if twin:
            if s["source"] not in twin["source"].split("+"):
                twin["source"] += "+" + s["source"]
            if (not twin["name"] or "HPMS route" in twin["name"]) and s["name"]:
                twin["name"] = s["name"]
            continue
        merged.append({"name": s["name"], "aadt": s["aadt"], "year": s["year"], "km_from_center": round(d / 1000, 3),
                       "source": s["source"], "_d": d, **({"trucks": s["trucks"]} if s.get("trucks") is not None else {})})
    out_segs = [{k: v for k, v in m.items() if k != "_d"} for m in sorted(merged, key=lambda m: (-m["aadt"], m["_d"]))[:10]]
    per_house = {}
    for h in houses:
        if h.get("garage") or h.get("lon") is None or h.get("lat") is None:
            continue
        p = Point((h["lon"] - lon0) * mx, (h["lat"] - lat0) * my)
        ds = [(s["geom"].distance(p), s) for s in near]
        if not ds:
            per_house[h["id"]] = {"nearest_road": None, "nearest_aadt": None, "distance_m": None,
                                  "busiest_within_150m_aadt": None}
            continue
        d0, s0 = min(ds, key=lambda t: t[0])
        within = [s["aadt"] for d, s in ds if d <= HOUSE_R_M]
        per_house[h["id"]] = {"nearest_road": s0["name"], "nearest_aadt": s0["aadt"], "distance_m": round(d0, 1),
                              "nearest_source": s0["source"],
                              "busiest_within_150m_aadt": max(within) if within else None}
    fallback = None
    if not out_segs:   # nothing counted within 300 m (typical inside residential grids): report nearest within 1 km
        far = [(s["geom"].distance(blk), s) for s in segs]
        far = [t for t in far if t[0] <= FALLBACK_M]
        if far:
            d, s = min(far, key=lambda t: t[0])
            fallback = {"name": s["name"], "aadt": s["aadt"], "year": s["year"], "m_from_block": round(d),
                        "km_from_center": round(s["geom"].distance(Point(0, 0)) / 1000, 3), "source": s["source"]}
    return out_segs, per_house, len(near), fallback


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def stage_blocks(slugs, national):
    svc = hpms_services()
    dirs = [d for d in sorted(BLOCKS.iterdir()) if (d / "meta.json").exists() and (not slugs or d.name in slugs)]
    summary = {}
    for d in dirs:
        meta0 = json.load(open(d / "meta.json", encoding="utf-8"))
        segs, status = fetch_block_segments(meta0, svc)            # network: outside the lock
        cf = meta0.get("county_fips") or meta0.get("fips")
        cty = (national.get("counties") or {}).get(cf)
        sf = (cf or "")[:2]
        seas = (national.get("tmas", {}).get("counties") or {}).get(cf) or (national.get("tmas", {}).get("states") or {}).get(sf)
        with block_lock(d):
            meta = json.load(open(d / "meta.json", encoding="utf-8"))
            houses = json.load(open(d / "houses.json", encoding="utf-8"))
            out_segs, per_house, n_near, fallback = build_block(meta, houses, [dict(s) for s in segs])
            used = sorted({x for s in out_segs for x in s["source"].split("+")} | {"hpms"})
            meta["traffic"] = {
                "segments": out_segs, "radius_m": RADIUS_M, "segments_within_radius": n_near,
                "nearest_outside_radius": fallback,
                "county": cty, "county_fips": cf,
                "seasonal_index": (seas or {}).get("seasonal_index"),
                "state_tvt": ((national.get("tvt") or {}).get("states") or {}).get(sf),
                "state_tvt_month": (national.get("tvt") or {}).get("latest_month"),
                "seasonal_scope": ("county" if (national.get("tmas", {}).get("counties") or {}).get(cf) else "state") if seas else None,
                "status": status,
                "note": "Aggregate annual average daily traffic (vehicles/day) on public road sections; no individual "
                        "vehicle or person data. Road noise is covered separately by the BTS National Transportation "
                        "Noise Map already used elsewhere in this project.",
                "sources": {k: {"licence": SOURCES[k]["licence"], "url": SOURCES[k]["url"]}
                            for k in used + (["tmas"] if seas else []) + (["tvt"] if national.get("tvt") else [])},
                "generated": now()}
            for h in houses:
                if h.get("id") in per_house:
                    h["traffic"] = per_house[h["id"]]
            write_json(d / "houses.json", houses)
            write_json(d / "meta.json", meta)
        top = out_segs[0] if out_segs else None
        hs = [v for v in per_house.values() if v["distance_m"] is not None]
        summary[d.name] = {"segments": len(out_segs), "top": f"{top['name']} {top['aadt']:,} ({top['source']})" if top else None,
                           "houses": len(per_house),
                           "median_nearest_m": round(float(np.median([v["distance_m"] for v in hs])), 1) if hs else None,
                           "max_busiest_150m": max([v["busiest_within_150m_aadt"] for v in hs if v["busiest_within_150m_aadt"]], default=None),
                           "fallback": f"{fallback['name']} {fallback['aadt']:,} at {fallback['m_from_block']} m" if fallback else None,
                           "status": status}
        print("ok  ", d.name, json.dumps(summary[d.name]))
    json.dump(summary, open(CACHE / "block_summary.json", "w"), indent=1)
    return summary


def main():
    args = sys.argv[1:]
    stages = [a for a in args if a in ("counties", "tmas", "blocks")] or ["counties", "tmas", "blocks"]
    slugs = [a for a in args if a not in ("counties", "tmas", "blocks")]
    national = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    if "counties" in stages:
        counties, states, status = stage_counties()
        national["counties"] = counties
        national["states"] = states
        national.setdefault("meta", {})["hpms_status"] = status
    if "tmas" in stages:
        stations, tst, tco = stage_tmas()
        national["tmas"] = {"year": 2025, "stations_fields": ["state_fips", "station_id", "lat", "lon", "f_system",
                                                              "county_fips", "latest_month", "latest_month_adt",
                                                              "annual_adt", "months_reported"],
                            "stations": stations, "states": tst, "counties": tco,
                            "yoy": None, "yoy_reason": "NTAD publishes only the 2025 TMAS volume table (no prior year), so station "
                                                       "YoY is not computed; state-level YoY comes from FHWA TVT (see tvt)."}
        for cf, v in tco.items():
            if cf in national.get("counties", {}):
                national["counties"][cf]["ccs_n"] = v["ccs_n"]
    if "tmas" in stages:
        tvt = stage_tvt()
        if tvt:
            national["tvt"] = tvt
            print(f"ok   tvt states={len(tvt['states'])}")
    if "counties" in stages or "tmas" in stages:
        m = national.setdefault("meta", {})
        m.update({"generated": now(), "units": {"vmt_daily": "vehicle-miles per day (sum AADT x section miles, mainline "
                                                               "one-/two-way sections)", "max_aadt": "vehicles/day",
                                                 "mean_aadt_arterial": "length-weighted AADT, f_system 3-4",
                                                 "truck_pct": "% of VMT by single-unit + combination trucks (sections reporting both)",
                                                 "yoy": "fractional change in county VMT vs previous HPMS year (null if not published)"},
                  "sources": SOURCES,
                  "rejected": REJECTED,
                  "privacy": "aggregate traffic volumes only"})
        write_json(OUT, national)
        print("wrote", OUT, f"{OUT.stat().st_size/1e6:.2f} MB")
    elif national.get("meta"):   # blocks-only run: keep the national file's source / rejected lists current
        national["meta"]["sources"] = SOURCES
        national["meta"]["rejected"] = REJECTED
        write_json(OUT, national)
    if "blocks" in stages:
        stage_blocks(slugs, national)


if __name__ == "__main__":
    main()

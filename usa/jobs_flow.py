"""County job-market and people-flow signals (aggregates only) -> web/usa/data/jobs_flow.json.

Sources (all permanently free, commercial use allowed - US federal works, public domain;
licence quotes + URLs in usa/research/jobs_flow.md and in meta.sources):
  1. BLS QCEW open-data CSV slices (data.bls.gov/cew/data/api)        - employment, wages, supersectors
  2. BLS LAUS flat file la.data.0.CurrentU25-29 (download.bls.gov)    - unemployment, labour force
  3. Census LEHD LODES 8 OD main+aux JT00 (lehd.ces.census.gov)       - jobs here vs employed residents, commuters
  4. IRS SOI county-to-county migration 2023-2024 (irs.gov/pub/irs-soi) - gross inflow, net domestic, top origins
  5. Census Population Estimates Vintage 2025 co-est2025-alldata.csv - components of change
Building permits by county already live in the per-county blocks (Census BPS) - referenced only.
Usage: python -u usa/jobs_flow.py [stage ...]   stages: qcew laus lodes irs pop build (default: all)
"""
import csv
import gzip
import io
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "jobs"
OUT = ROOT / "web" / "usa" / "data" / "jobs_flow.json"
COUNTIES = ROOT / "web" / "usa" / "data" / "counties.json"
UA = "roofintel-research/0.1 (open-data county labour aggregates; python-requests)"
S = requests.Session()
S.headers["User-Agent"] = UA
CACHE.mkdir(parents=True, exist_ok=True)

# QCEW: latest 4 released quarters on 2026-11-24 are 2025Q2..2026Q1 (2026Q2 releases in December)
QTRS = [(2025, 2), (2025, 3), (2025, 4), (2026, 1)]
PRIOR = [(2024, 2), (2024, 3), (2024, 4), (2025, 1)]
FIVE = [(2020, 2), (2020, 3), (2020, 4), (2021, 1)]
SUPERSECTORS = {
    "1011": "Natural resources & mining", "1012": "Construction", "1013": "Manufacturing",
    "1021": "Trade, transportation & utilities", "1022": "Information", "1023": "Financial activities",
    "1024": "Professional & business services", "1025": "Education & health services",
    "1026": "Leisure & hospitality", "1027": "Other services", "1028": "Public administration",
}
LODES_YEAR = 2024
IRS_YR = "2324"
LAUS_FILE = "la.data.0.CurrentU25-29"

SOURCES = [
    {"id": "qcew", "name": "BLS Quarterly Census of Employment and Wages (QCEW) open data CSV slices",
     "url": "https://data.bls.gov/cew/data/api/", "vintage": "2020Q2-2021Q1, 2024Q2-2026Q1",
     "licence": "US federal government work - public domain (17 U.S.C. 105)",
     "licence_url": "https://www.bls.gov/opub/copyright-information.htm",
     "quote": "everything that we publish, both in hard copy and electronically, is in the public domain ... we do ask that you cite the Bureau of Labor Statistics as the source."},
    {"id": "laus", "name": "BLS Local Area Unemployment Statistics (LAUS) flat file " + LAUS_FILE,
     "url": "https://download.bls.gov/pub/time.series/la/", "vintage": "monthly, not seasonally adjusted",
     "licence": "US federal government work - public domain",
     "licence_url": "https://www.bls.gov/opub/copyright-information.htm",
     "quote": "everything that we publish, both in hard copy and electronically, is in the public domain ... we do ask that you cite the Bureau of Labor Statistics as the source."},
    {"id": "lodes", "name": "Census LEHD LODES 8 Origin-Destination (main + aux, JT00 all jobs)",
     "url": "https://lehd.ces.census.gov/data/lodes/LODES8/", "vintage": str(LODES_YEAR) + " (latest year per state)",
     "licence": "US federal government work - public domain; LEHD data are public-use",
     "licence_url": "https://www.copyright.gov/title17/92chap1.html#105",
     "quote": "\"Copyright protection under this title is not available for any work of the United States Government\" (17 U.S.C. 105)"},
    {"id": "irs_soi", "name": "IRS Statistics of Income county-to-county migration data",
     "url": "https://www.irs.gov/statistics/soi-tax-stats-migration-data", "vintage": "filing years 2023-2024",
     "licence": "US federal government work - public domain",
     "licence_url": "https://www.copyright.gov/title17/92chap1.html#105",
     "quote": "\"Copyright protection under this title is not available for any work of the United States Government\" (17 U.S.C. 105)"},
    {"id": "popest", "name": "Census Population Estimates Vintage 2025, county components of change (co-est2025-alldata.csv)",
     "url": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv",
     "vintage": "Vintage 2025 (July 2024 - July 2025)",
     "licence": "US federal government work - public domain",
     "licence_url": "https://www.copyright.gov/title17/92chap1.html#105",
     "quote": "\"Copyright protection under this title is not available for any work of the United States Government\" (17 U.S.C. 105)"},
    {"id": "bps_ref", "name": "Census Building Permits Survey by county (already in per-county blocks; referenced, not re-ingested)",
     "url": "https://www.census.gov/construction/bps/", "licence": "US federal government work - public domain"},
]
REJECTED = [
    {"name": "Lightcast (Burning Glass) job postings", "reason": "paid, proprietary licence"},
    {"name": "LinkedIn Economic Graph / workforce data", "reason": "proprietary; user agreement forbids scraping/commercial reuse"},
    {"name": "Indeed Hiring Lab postings data", "reason": "terms restrict redistribution/commercial reuse; not county-level open bulk"},
    {"name": "Revelio Labs", "reason": "paid, proprietary"},
    {"name": "FRED (St. Louis Fed) API", "reason": "terms of use restrict commercial use; third-party series copyrighted - use the primary BLS/Census files instead"},
    {"name": "Census Data API (ACS county-to-county via api.census.gov)", "reason": "now requires an API key; bulk flat files used instead (IRS SOI + Pop Estimates cover migration)"},
    {"name": "USAJOBS Search API", "reason": "requires a registered API key (free) and a user-agent email; skipped per lane instructions"},
    {"name": "Zillow/Redfin/Apartment List migration & search-flow data", "reason": "terms restrict commercial redistribution"},
]


def get(url, tries=5, **kw):
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


def fetch(name, url):
    p = CACHE / name
    if p.exists() and p.stat().st_size > 0:
        return p
    r = get(url, stream=True)
    if r.status_code != 200:
        return None
    tmp = p.with_suffix(p.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(p)
    return p


def county_list():
    d = json.loads(COUNTIES.read_text(encoding="utf-8"))
    i = d["cols"].index("fips")
    return [r[i] for r in d["rows"]]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- QCEW
def qcew_file(ind, y, q):
    return fetch(f"qcew_{y}q{q}_{ind}.csv", f"https://data.bls.gov/cew/data/api/{y}/{q}/industry/{ind}.csv")


def stage_qcew():
    jobs = [("10", y, q) for y, q in QTRS + PRIOR + FIVE] + [(s, y, q) for s in SUPERSECTORS for y, q in QTRS + PRIOR]
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda a: qcew_file(*a), jobs))
    print(f"qcew files: {sum(1 for r in res if r)}/{len(jobs)}")
    tot = defaultdict(dict)   # fips -> {(y,q): (emp, wage)}
    sec = defaultdict(lambda: defaultdict(dict))  # fips -> sector -> {(y,q): emp}
    for (ind, y, q), p in zip(jobs, res):
        if not p:
            continue
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                a = row["area_fips"]
                if not a.isdigit() or a.endswith("000") or a[:2] == "00":
                    continue
                if ind == "10":
                    if row["own_code"] != "0" or row["agglvl_code"] != "70":
                        continue
                else:
                    if row["own_code"] != "5" or row["agglvl_code"] != "73":
                        continue
                if row["disclosure_code"] == "N":
                    continue
                emp = (int(row["month1_emplvl"]) + int(row["month2_emplvl"]) + int(row["month3_emplvl"])) / 3
                if ind == "10":
                    tot[a][(y, q)] = (emp, int(row["avg_wkly_wage"]), int(row["total_qtrly_wages"]))
                else:
                    sec[a][ind][(y, q)] = emp
    out = {}
    for a, d in tot.items():
        def avg(qs, k):
            v = [d[x][k] for x in qs if x in d]
            return sum(v) / len(v) if len(v) == len(qs) else None
        e, ep, e5 = avg(QTRS, 0), avg(PRIOR, 0), avg(FIVE, 0)
        if e is None:
            continue
        wq = sum(d[x][2] for x in QTRS)
        w = wq / (e * 52) if e else None   # annualised wages / avg employment / 52
        wp = (sum(d[x][2] for x in PRIOR) / (ep * 52)) if ep else None
        r = {"emp": round(e), "emp_yoy": pct(e, ep), "emp_5y": pct(e, e5),
             "wage_wk": round(w) if w else None, "wage_yoy": pct(w, wp), "emp_latest_q": f"{QTRS[-1][0]}Q{QTRS[-1][1]}"}
        gains = []
        for s, sd in sec.get(a, {}).items():
            if all(x in sd for x in QTRS + PRIOR):
                g = sum(sd[x] for x in QTRS) / 4 - sum(sd[x] for x in PRIOR) / 4
                gains.append((g, s))
        gains.sort(reverse=True)
        r["top_growth_sectors"] = [{"sector": SUPERSECTORS[s], "job_gain": round(g)} for g, s in gains[:3] if g > 0]
        out[a] = r
    (CACHE / "qcew_county.json").write_text(json.dumps(out), encoding="utf-8")
    print("qcew counties:", len(out))


def pct(a, b):
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 2)


# ---------------------------------------------------------------- LAUS
def stage_laus():
    p = fetch(LAUS_FILE, "https://download.bls.gov/pub/time.series/la/" + LAUS_FILE)
    data = defaultdict(dict)  # (fips, measure) -> {(y, m): v}
    with open(p, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.split("\t")
            sid = parts[0].strip()
            if not sid.startswith("LAUCN"):
                continue
            per = parts[2].strip()
            if per == "M13":
                continue
            v = num(parts[3].strip())
            if v is None:
                continue
            data[(sid[5:10], sid[-2:])][(int(parts[1]), int(per[1:]))] = v
    cnt = defaultdict(int)
    for (f_, m), d in data.items():
        if m == "03":
            for k in d:
                cnt[k] += 1
    nser = sum(1 for (f_, m) in data if m == "03")
    latest = max(k for k, v in cnt.items() if v >= 0.9 * nser)  # latest month with near-full county coverage
    prev = (latest[0] - 1, latest[1])
    out = {}
    for (fips, m), d in data.items():
        if m != "03":
            continue
        if latest not in d:
            continue
        r = {"unemp": d[latest], "unemp_chg": round(d[latest] - d[prev], 1) if prev in d else None}
        lf = data.get((fips, "06"), {})
        r["lf_yoy"] = pct(lf.get(latest), lf.get(prev))
        r["labor_force"] = int(lf[latest]) if latest in lf else None
        out[fips] = r
    out["_month"] = f"{latest[0]}-{latest[1]:02d}"
    (CACHE / "laus_county.json").write_text(json.dumps(out), encoding="utf-8")
    print("laus counties:", len(out) - 1, "month", out["_month"])


# ---------------------------------------------------------------- LODES
def lodes_states():
    html = get("https://lehd.ces.census.gov/data/lodes/LODES8/").text
    return [s for s in re.findall(r'href="([a-z]{2})/"', html) if s != "us"]


def lodes_year(st):
    html = get(f"https://lehd.ces.census.gov/data/lodes/LODES8/{st}/od/").text
    ys = sorted(int(y) for y in re.findall(rf"{st}_od_main_JT00_(\d{{4}})\.csv\.gz", html))
    return max(ys) if ys else None


def ct_crosswalk():
    """2020 CT blocks carry legacy county codes; map block -> planning region via the LODES geography crosswalk."""
    p = fetch("ct_xwalk.csv.gz", "https://lehd.ces.census.gov/data/lodes/LODES8/ct/ct_xwalk.csv.gz")
    m = {}
    with gzip.open(p, "rt", encoding="latin-1") as f:
        rd = csv.DictReader(f)
        for row in rd:
            m[row["tabblk2020"]] = row["cty"]
    return m


def stage_lodes():
    import pandas as pd
    states = lodes_states()
    with ThreadPoolExecutor(8) as ex:
        years = dict(zip(states, ex.map(lodes_year, states)))
    print("lodes years:", {y: [s for s in states if years[s] == y] for y in set(years.values())})
    jobs = []
    for st in states:
        y = years[st]
        if y:
            for part in ("main", "aux"):
                n = f"{st}_od_{part}_JT00_{y}.csv.gz"
                jobs.append((n, f"https://lehd.ces.census.gov/data/lodes/LODES8/{st}/od/{n}"))
    with ThreadPoolExecutor(6) as ex:
        paths = list(ex.map(lambda a: fetch(*a), jobs))
    print("lodes files:", sum(1 for p in paths if p), "/", len(jobs))
    ctx = ct_crosswalk()
    ct_blk = {k: v for k, v in ctx.items()}
    work = defaultdict(int)
    home = defaultdict(int)
    same = defaultdict(int)
    for (n, _), p in zip(jobs, paths):
        if not p:
            continue
        for ch in pd.read_csv(p, usecols=["w_geocode", "h_geocode", "S000"], dtype={"w_geocode": str, "h_geocode": str},
                              chunksize=2_000_000):
            wc = ch["w_geocode"].str[:5]
            hc = ch["h_geocode"].str[:5]
            if (wc.str[:2] == "09").any() or (hc.str[:2] == "09").any():
                wm = wc.str[:2] == "09"
                wc = wc.where(~wm, ch["w_geocode"].map(ct_blk).fillna(wc))
                hm = hc.str[:2] == "09"
                hc = hc.where(~hm, ch["h_geocode"].map(ct_blk).fillna(hc))
            df = pd.DataFrame({"w": wc, "h": hc, "n": ch["S000"]})
            for k, v in df.groupby("w")["n"].sum().items():
                work[k] += int(v)
            for k, v in df.groupby("h")["n"].sum().items():
                home[k] += int(v)
            sm = df[df["w"] == df["h"]]
            for k, v in sm.groupby("w")["n"].sum().items():
                same[k] += int(v)
        print("  done", n, flush=True)
    out = {}
    for c in set(work) | set(home):
        jh, re_ = work.get(c, 0), home.get(c, 0)
        s = same.get(c, 0)
        out[c] = {"jobs_here": jh, "residents_employed": re_, "in_commuters": jh - s, "out_commuters": re_ - s,
                  "live_work_same": s, "inflow_ratio": round(jh / re_, 3) if re_ else None,
                  "in_commute_share": round((jh - s) / jh, 3) if jh else None}
    out["_years"] = years
    (CACHE / "lodes_county.json").write_text(json.dumps(out), encoding="utf-8")
    print("lodes counties:", len(out) - 1)


# ---------------------------------------------------------------- IRS SOI
def read_irs(kind):
    p = fetch(f"county{kind}{IRS_YR}.csv", f"https://www.irs.gov/pub/irs-soi/county{kind}{IRS_YR}.csv")
    with open(p, encoding="latin-1", newline="") as f:
        return [{k.lower().replace("ï»¿", "").lstrip("﻿"): v for k, v in r.items()} for r in csv.DictReader(f)]


def stage_irs():
    inflow, outflow = read_irs("inflow"), read_irs("outflow")
    out = defaultdict(lambda: {"origins": []})
    for r in inflow:
        dest = r["y2_statefips"].zfill(2) + r["y2_countyfips"].zfill(3)
        os_, oc = int(r["y1_statefips"]), int(r["y1_countyfips"])
        n2 = int(r["n2"])
        n1 = int(r["n1"])
        if os_ == 97 and oc == 0:
            out[dest]["gross_inflow"] = n2 if n2 >= 0 else None
            out[dest]["inflow_returns"] = n1 if n1 >= 0 else None
        elif os_ == 98 and oc == 0:
            out[dest]["foreign_inflow"] = n2 if n2 >= 0 else None
        elif os_ <= 56 or os_ == 72:
            orig = f"{os_:02d}{oc:03d}"
            if orig == dest or n2 < 0:
                continue
            out[dest]["origins"].append((n2, orig, f"{r['y1_countyname'].strip()}, {r['y1_state'].strip()}"))
    for r in outflow:
        o = r["y1_statefips"].zfill(2) + r["y1_countyfips"].zfill(3)
        ds, dc = int(r["y2_statefips"]), int(r["y2_countyfips"])
        if ds == 97 and dc == 0:
            n2 = int(r["n2"])
            out[o]["gross_outflow"] = n2 if n2 >= 0 else None
    res = {}
    for c, d in out.items():
        d["origins"].sort(reverse=True)
        gi, go = d.get("gross_inflow"), d.get("gross_outflow")
        res[c] = {"gross_inflow": gi, "gross_outflow": go,
                  "net_domestic_mig_irs": (gi - go) if gi is not None and go is not None else None,
                  "top_origins": [{"fips": f, "name": n, "people": k} for k, f, n in d["origins"][:5]]}
    (CACHE / "irs_county.json").write_text(json.dumps(res), encoding="utf-8")
    print("irs counties:", len(res))


# ---------------------------------------------------------------- Pop Estimates
def stage_pop():
    p = fetch("co-est2025-alldata.csv",
              "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv")
    out = {}
    with open(p, encoding="latin-1", newline="") as f:
        for r in csv.DictReader(f):
            if r["SUMLEV"] != "050":
                continue
            c = r["STATE"] + r["COUNTY"]
            p24, p25, p20 = num(r["POPESTIMATE2024"]), num(r["POPESTIMATE2025"]), num(r["ESTIMATESBASE2020"])
            dom5 = sum(int(r[f"DOMESTICMIG{y}"]) for y in range(2021, 2026))
            out[c] = {"pop": int(p25), "pop_change_pct": pct(p25, p24), "pop_change_5y_pct": pct(p25, p20),
                      "net_domestic_mig": int(r["DOMESTICMIG2025"]), "net_intl_mig": int(r["INTERNATIONALMIG2025"]),
                      "net_mig_rate": num(r["RNETMIG2025"]), "net_domestic_mig_2021_25": dom5}
    (CACHE / "pop_county.json").write_text(json.dumps(out), encoding="utf-8")
    print("pop counties:", len(out))


# ---------------------------------------------------------------- build
WEIGHTS = {"emp_yoy": 0.25, "wage_yoy": 0.15, "mig_rate": 0.25, "inflow_ratio": 0.15, "low_unemp": 0.20}


def pctile(vals):
    """fips->value  ->  fips->percentile 0..100 (average rank, ties shared)."""
    items = sorted((v, k) for k, v in vals.items() if v is not None)
    n = len(items)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][0] == items[i][0]:
            j += 1
        p = 100.0 * ((i + j) / 2) / (n - 1) if n > 1 else 50.0
        for k in range(i, j + 1):
            out[items[k][1]] = p
        i = j + 1
    return out


def stage_build():
    ld = lambda n: json.loads((CACHE / n).read_text(encoding="utf-8"))  # noqa: E731
    q, la, lo, irs, pop = ld("qcew_county.json"), ld("laus_county.json"), ld("lodes_county.json"), ld("irs_county.json"), ld("pop_county.json")
    fips = county_list()
    rows = {}
    for c in fips:
        r = {}
        r.update(q.get(c, {}))
        r.update(la.get(c, {}))
        L = lo.get(c, {})
        for k in ("jobs_here", "residents_employed", "in_commuters", "out_commuters", "inflow_ratio", "in_commute_share"):
            if k in L:
                r[k] = L[k]
        P = pop.get(c, {})
        r.update(P)
        I = irs.get(c, {})
        for k in ("gross_inflow", "gross_outflow", "net_domestic_mig_irs", "top_origins"):
            if k in I:
                r[k] = I[k]
        rows[c] = r
    # housing_demand_pressure: percentile blend
    mig_rate = {c: (r["net_domestic_mig"] / r["pop"] * 1000 if r.get("pop") and r.get("net_domestic_mig") is not None else None)
                for c, r in rows.items()}
    comps = {
        "emp_yoy": pctile({c: r.get("emp_yoy") for c, r in rows.items()}),
        "wage_yoy": pctile({c: r.get("wage_yoy") for c, r in rows.items()}),
        "mig_rate": pctile(mig_rate),
        "inflow_ratio": pctile({c: r.get("inflow_ratio") for c, r in rows.items()}),
        "low_unemp": pctile({c: (-r["unemp"] if r.get("unemp") is not None else None) for c, r in rows.items()}),
    }
    for c, r in rows.items():
        num_, den = 0.0, 0.0
        for k, w in WEIGHTS.items():
            v = comps[k].get(c)
            if v is not None:
                num_ += w * v
                den += w
        if den >= 0.6:
            r["housing_demand_pressure"] = round(num_ / den, 1)
        r["dom_mig_per_1k"] = round(mig_rate[c], 2) if mig_rate.get(c) is not None else None
    # final percentile rank of the blend
    hp = pctile({c: r.get("housing_demand_pressure") for c, r in rows.items()})
    for c, r in rows.items():
        if c in hp:
            r["hdp_pctile"] = round(hp[c], 1)
    for r in rows.values():
        for k in list(r):
            if r[k] is None:
                del r[k]
    meta = {
        "generated": date.today().isoformat(), "n": len(rows),
        "periods": {"qcew": "avg 2025Q2-2026Q1 vs 2024Q2-2025Q1 (yoy) and 2020Q2-2021Q1 (5y, pandemic-depressed base)",
                    "laus_month": la.get("_month"), "lodes_years": lo.get("_years"), "irs": "2023-2024 filing years",
                    "popest": "Vintage 2025 (Jul 2024-Jul 2025)"},
        "fields": {
            "emp": "QCEW covered employment, avg of latest 4 quarters", "emp_yoy": "% change vs prior 4 quarters",
            "emp_5y": "% change vs same 4 quarters 5 years earlier", "wage_wk": "avg weekly wage, latest 4 quarters",
            "wage_yoy": "% change avg weekly wage", "top_growth_sectors": "top 3 private supersectors by job gain (yoy, 4-qtr avg)",
            "unemp": "LAUS unemployment rate % (latest month, NSA)", "unemp_chg": "pp change vs same month a year earlier",
            "lf_yoy": "labour force % change yoy (same month)", "jobs_here": "LODES jobs located in county (JT00)",
            "residents_employed": "LODES jobs held by county residents", "in_commuters": "jobs here held by residents of other counties",
            "out_commuters": "employed residents working in other counties", "inflow_ratio": "jobs_here / residents_employed (>1 = net daily inflow)",
            "net_domestic_mig": "Census PEP net domestic migration Jul 2024-Jul 2025", "net_intl_mig": "Census PEP net international migration",
            "pop_change_pct": "Census PEP population % change 2024-2025", "gross_inflow": "IRS SOI people (exemptions/n2) moving in from other US counties 2023-24",
            "gross_outflow": "IRS SOI people moving out to other US counties", "net_domestic_mig_irs": "IRS gross_inflow - gross_outflow",
            "top_origins": "IRS SOI top 5 origin counties by people (suppressed small flows excluded)",
            "housing_demand_pressure": "0-100 weighted percentile blend (see weights)", "hdp_pctile": "percentile rank of the blend",
            "building_permits": "see per-county blocks (Census BPS) - not duplicated here"},
        "weights": WEIGHTS,
        "sources": SOURCES, "rejected": REJECTED,
        "attribution": "Sources: U.S. Bureau of Labor Statistics (QCEW, LAUS); U.S. Census Bureau (LEHD LODES 8, Population Estimates V2025); IRS Statistics of Income migration data. Public domain US federal data; derived aggregates by roof-intel.",
    }
    OUT.write_text(json.dumps({"meta": meta, "counties": rows}, separators=(",", ":")), encoding="utf-8")
    print("wrote", OUT, round(OUT.stat().st_size / 1e6, 2), "MB")
    cov = {k: sum(1 for r in rows.values() if k in r) for k in ("emp", "emp_yoy", "emp_5y", "top_growth_sectors", "unemp", "jobs_here", "gross_inflow", "top_origins", "net_domestic_mig", "housing_demand_pressure")}
    print("coverage of", len(rows), cov)
    top = sorted(((r["housing_demand_pressure"], c) for c, r in rows.items() if "housing_demand_pressure" in r), reverse=True)[:20]
    names = {}
    d = json.loads(COUNTIES.read_text(encoding="utf-8"))
    for row in d["rows"]:
        names[row[0]] = f"{row[1]}, {row[2]}"
    for s, c in top:
        r = rows[c]
        print(f"{s:5.1f} {c} {names.get(c)}  emp_yoy={r.get('emp_yoy')} wage_yoy={r.get('wage_yoy')} mig/1k={r.get('dom_mig_per_1k')} inflow={r.get('inflow_ratio')} unemp={r.get('unemp')}")


STAGES = {"qcew": stage_qcew, "laus": stage_laus, "lodes": stage_lodes, "irs": stage_irs, "pop": stage_pop, "build": stage_build}

if __name__ == "__main__":
    for s in (sys.argv[1:] or list(STAGES)):
        t = time.time()
        STAGES[s]()
        print(f"[{s}] {time.time() - t:.0f}s", flush=True)

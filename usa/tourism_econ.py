"""County tourism-economy and short-term-rental supply signals (aggregates only) -> web/usa/data/tourism_econ.json.

Sources (all permanently free, commercial use allowed; see usa/research/tourism_econ.md for licence quotes):
  1. BLS QCEW open CSV slices (data.bls.gov/cew/data/api)           - US public domain
  2. Census County Business Patterns county flat file (www2.census.gov) - US public domain
  3. Census population estimates (co-est2025-alldata.csv)             - US public domain (sanity only)
  4. Texas Comptroller local HOT reporting (data.texas.gov er34-v24h) - Public Domain
  5. Texas Comptroller Hotel Tax Permits (data.texas.gov hdzd-884n)   - Public Domain
  6. Austin STR Locations (datahub.austintexas.gov 2fah-4p7e)         - Public Domain
  7. Denver STR licences (data.colorado.gov f3vc-vat3)                - Public Domain
  8. Wikidata county seats (query.wikidata.org)                       - CC0
  9. Wikimedia Pageviews REST API                                     - CC0
Usage: python -u usa/tourism_econ.py [stage ...]   stages: qcew cbp pop hot str wiki build (default: all)
"""
import csv
import io
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "tourism"
OUT = ROOT / "web" / "usa" / "data" / "tourism_econ.json"
COUNTIES = ROOT / "web" / "usa" / "data" / "counties.json"
UA = "roofintel-research/0.1 (open-data county tourism aggregates)"
S = requests.Session()
S.headers["User-Agent"] = UA
CACHE.mkdir(parents=True, exist_ok=True)

# latest 4 QCEW quarters = 2025Q2..2026Q1 (2026Q2 not yet released on 2026-11-24); prior 4 for YoY
QTRS = [(2025, 2), (2025, 3), (2025, 4), (2026, 1)]
PRIOR = [(2024, 2), (2024, 3), (2024, 4), (2025, 1)]
INDUSTRIES = {"721": QTRS + PRIOR, "7211": QTRS, "7213": QTRS, "71": QTRS, "722": QTRS, "1026": QTRS, "10": QTRS}
CBP_YEAR = 2023


def get(url, tries=4, **kw):
    kw.setdefault("timeout", 180)
    last = None
    for i in range(tries):
        try:
            r = S.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {url}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise last


def cached(name, url, binary=False, **kw):
    p = CACHE / name
    if not p.exists():
        r = get(url, **kw)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p.read_bytes() if binary else p.read_text(encoding="utf-8", errors="replace")


def county_fips():
    d = json.loads(COUNTIES.read_text(encoding="utf-8"))
    i = {c: k for k, c in enumerate(d["cols"])}
    return {r[i["fips"]]: r for r in d["rows"]}, i


# ---------------------------------------------------------------- 1. QCEW
def qcew():
    out = {}  # (ind, y, q) -> {fips: (estabs, emp_avg, wages)}  None if suppressed
    for ind, qs in INDUSTRIES.items():
        for y, q in qs:
            txt = cached(f"qcew_{y}q{q}_{ind}.csv", f"https://data.bls.gov/cew/data/api/{y}/{q}/industry/{ind}.csv")
            m = {}
            for r in csv.DictReader(io.StringIO(txt)):
                a = r["area_fips"]
                if not re.fullmatch(r"\d{5}", a) or a.endswith("000") or a.endswith("999"):
                    continue
                if r["own_code"] != "5":
                    continue
                if r["disclosure_code"] == "N":  # employment/wages suppressed; establishment counts still published
                    m[a] = (int(r["qtrly_estabs"]), None, None)
                    continue
                emp = (int(r["month1_emplvl"]) + int(r["month2_emplvl"]) + int(r["month3_emplvl"])) / 3
                m[a] = (int(r["qtrly_estabs"]), emp, int(r["total_qtrly_wages"]))
            out[f"{ind}|{y}|{q}"] = m
            print(f"qcew {ind} {y}Q{q}: {len(m)} counties ({sum(v[1] is None for v in m.values())} suppressed)")
    return out


def _agg(Q, ind, qs, f):
    vals = [Q[f"{ind}|{y}|{q}"].get(f) for y, q in qs]
    if any(v is None or v[1] is None for v in vals):
        return None
    return vals


# ---------------------------------------------------------------- 2. CBP
def cbp():
    raw = cached(f"cbp{CBP_YEAR % 100}co.zip", f"https://www2.census.gov/programs-surveys/cbp/datasets/{CBP_YEAR}/cbp{CBP_YEAR % 100}co.zip", binary=True)
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = [n for n in z.namelist() if n.endswith(".txt")][0]
    out = {}
    with z.open(name) as fh:
        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1")):
            r = {k.lower(): v for k, v in r.items()}
            if r["naics"] in ("721///", "7211//"):
                f = r["fipstate"].zfill(2) + r["fipscty"].zfill(3)
                out.setdefault(f, {})[r["naics"].strip("/")] = int(r["est"])
    print(f"cbp {CBP_YEAR}: {len(out)} counties with NAICS 721")
    return out


# ---------------------------------------------------------------- population (sanity)
def pop():
    txt = cached("co-est2025-alldata.csv", "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv")
    out = {}
    for r in csv.DictReader(io.StringIO(txt)):
        if r["SUMLEV"] == "050":
            out[r["STATE"] + r["COUNTY"]] = int(r["POPESTIMATE2025"])
    print(f"pop: {len(out)}")
    return out


# ---------------------------------------------------------------- Texas helpers
def tx_fips(code):  # Comptroller county code 1..254 (alphabetical) -> FIPS 48001..48507
    return f"48{2 * int(code) - 1:03d}"


def soda(domain, rid, name, where=None, select=None):
    p = CACHE / name
    if not p.exists():
        rows, off = [], 0
        while True:
            params = {"$limit": 50000, "$offset": off}
            if not select:
                params["$order"] = ":id"
            if where:
                params["$where"] = where
            if select:
                params["$select"] = select
            r = get(f"https://{domain}/resource/{rid}.json", params=params)
            r.raise_for_status()
            b = r.json()
            rows += b
            if len(b) < 50000:
                break
            off += 50000
        p.write_text(json.dumps(rows), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))


def norm_city(s):
    s = (s or "").upper()
    s = re.sub(r"^(CITY|TOWN|VILLAGE) OF\s+", "", s)
    s = re.sub(r"^(CITY|TOWN|VILLAGE) OF\s+", "", s)
    s = re.sub(r"\s+(CITY|TOWN|VILLAGE)$", "", s)
    s = s.replace("ST.", "SAINT").replace("FT.", "FORT").replace("MT.", "MOUNT")
    return re.sub(r"[^A-Z ]", "", s).strip()


def hot():
    """Texas local HOT revenue (Ch. 351 city + Ch. 352 county), FY2025, by county; cities mapped by modal permit county."""
    permits = soda("data.texas.gov", "hdzd-884n", "tx_hotel_permits.json")
    city_cty = defaultdict(lambda: defaultdict(int))
    cty_names = {}
    for p in permits:
        if p.get("loc_state") == "TX" and p.get("loc_county", "").isdigit() and 1 <= int(p["loc_county"]) <= 254:
            city_cty[norm_city(p.get("loc_city"))][tx_fips(p["loc_county"])] += 1
    # county name -> fips from counties.json
    cf, i = county_fips()
    for f, r in cf.items():
        if f.startswith("48"):
            cty_names[norm_city(r[i["NAME"]])] = f
    rep = soda("data.texas.gov", "er34-v24h", "tx_local_hot_2025.json")
    out, unmatched = defaultdict(float), []
    for r in rep:
        rev = float(r.get("total_revenue_351") or 0) + float(r.get("total_revenue_352") or 0)
        if rev <= 0:
            continue
        nm = norm_city(r.get("name"))
        nm = re.sub(r"\s+COUNTY$", "", nm)
        if (r.get("type") or "").lower() == "county":
            f = cty_names.get(nm)
        else:
            c = city_cty.get(nm)
            f = max(c, key=c.get) if c else None
        if f:
            out[f] += rev
        else:
            unmatched.append(r.get("name"))
    print(f"hot: {len(out)} TX counties, {len(unmatched)} unmatched reporting entities e.g. {unmatched[:8]}")
    return {k: round(v) for k, v in out.items()}, len(rep), len(unmatched)


# ---------------------------------------------------------------- 4. STR supply
def str_supply():
    res = {}
    permits = soda("data.texas.gov", "hdzd-884n", "tx_hotel_permits.json")
    tx = defaultdict(int)
    for p in permits:
        if p.get("out_of_business_date"):
            continue
        if p.get("naics") in ("531110", "531311") and p.get("loc_county", "").isdigit() and 1 <= int(p["loc_county"]) <= 254:
            tx[tx_fips(p["loc_county"])] += 1
    res["tx_permits"] = dict(tx)
    aus = soda("datahub.austintexas.gov", "2fah-4p7e", "austin_str.json", select="count(*) as n")
    res["austin"] = int(aus[0]["n"])
    den = soda("data.colorado.gov", "f3vc-vat3", "denver_str.json", select="license_status, count(*) as n GROUP BY license_status")
    res["denver_by_status"] = {d.get("license_status"): int(d["n"]) for d in den}
    print("str:", len(tx), "TX counties;", "austin", res["austin"], "denver", res["denver_by_status"])
    return res


# ---------------------------------------------------------------- 5. Wikipedia pageviews
SPARQL = """SELECT ?fips ?article WHERE {
  ?county wdt:P882 ?fips . FILTER(STRLEN(?fips)=5)
  ?county wdt:P36 ?seat .
  ?article schema:about ?seat ; schema:isPartOf <https://en.wikipedia.org/> .
}"""


def seats():
    p = CACHE / "wikidata_seats.json"
    if not p.exists():
        r = get("https://query.wikidata.org/sparql", params={"query": SPARQL, "format": "json"},
                headers={"Accept": "application/sparql-results+json"})
        r.raise_for_status()
        p.write_text(r.text, encoding="utf-8")
    b = json.loads(p.read_text(encoding="utf-8"))["results"]["bindings"]
    out = defaultdict(list)
    for x in b:
        t = x["article"]["value"].rsplit("/wiki/", 1)[1]
        if t not in out[x["fips"]["value"]]:
            out[x["fips"]["value"]].append(t)
    return out


WSTART, WEND = "2024110100", "2026103100"  # 24 full months Nov 2024 - Oct 2026


WIKI_TOP_TI = 200
WIKI_TOP = 500  # anonymous Wikimedia API clients are rate-limited (~10 req/min observed); top counties by population


def wiki(limit=None, fetch=True):
    st = seats()
    cf, _ = county_fips()
    P = pop()
    pdir = CACHE / "pageviews"
    pdir.mkdir(exist_ok=True)
    order = sorted(cf, key=lambda f: -(P.get(f) or 0))
    top = set(order[:WIKI_TOP])
    if OUT.exists():  # plus the most tourism-intensive counties (resort/park counties are small, so pop rank misses them)
        prev = json.loads(OUT.read_text(encoding="utf-8"))["counties"]
        ti = sorted((v["tourism_intensity"], f) for f, v in prev.items() if v.get("tourism_intensity") is not None)
        top |= {f for _, f in ti[-WIKI_TOP_TI:]}
    out, n = {}, 0
    for f in order:
        arts = st.get(f, [])[:1]
        fps = [pdir / (re.sub(r"[^A-Za-z0-9_.-]", "_", a) + ".json") for a in arts]
        if f not in top and not all(fp.exists() for fp in fps):
            arts = [a for a, fp in zip(arts, fps) if fp.exists()]
        if not arts:
            continue
        series = defaultdict(int)
        for a in arts:
            fp = pdir / (re.sub(r"[^A-Za-z0-9_.-]", "_", a) + ".json")
            if not fp.exists() and fetch:
                url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
                       f"{quote(a, safe='')}/monthly/{WSTART}/{WEND}")
                for k in range(8):
                    r = S.get(url, timeout=60)
                    if r.status_code != 429 and r.status_code < 500:
                        break
                    time.sleep((int(r.headers.get("retry-after") or 0) or 20 * (k + 1)) + 1)
                if r.status_code in (200, 404):
                    fp.write_text(r.text if r.status_code == 200 else '{"items":[]}', encoding="utf-8")
                time.sleep(0.35)
                n += 1
            if not fp.exists():
                continue
            for it in json.loads(fp.read_text(encoding="utf-8")).get("items", []):
                series[it["timestamp"][:6]] += it["views"]
        if series:
            out[f] = {"articles": [requests.utils.unquote(a) for a in arts], "monthly": dict(sorted(series.items()))}
        if limit and len(out) >= limit:
            break
    print(f"wiki: {len(out)} counties ({n} new requests)")
    return out


# ---------------------------------------------------------------- build
def r1(x, d=1):
    return None if x is None else round(x, d)


def build():
    cf, ci = county_fips()
    Q = qcew()
    C = cbp()
    P = pop()
    H, hot_n, hot_unm = hot()
    STR = str_supply()
    W = wiki(fetch=False)
    counties = {}
    cov = defaultdict(int)
    for f in sorted(cf):
        o = {}
        acc = _agg(Q, "721", QTRS, f)
        accp = _agg(Q, "721", PRIOR, f)
        tot = _agg(Q, "10", QTRS, f)
        last = Q["721|2026|1"].get(f)
        o["acc_estabs"] = last[0] if last else None
        o["acc_suppressed"] = bool(last) and acc is None
        o["acc_emp"] = r1(sum(v[1] for v in acc) / 4) if acc else None
        o["acc_wages"] = sum(v[2] for v in acc) if acc else None
        o["acc_emp_yoy"] = r1((sum(v[1] for v in acc) / sum(v[1] for v in accp) - 1) * 100) if acc and accp and sum(v[1] for v in accp) > 0 else None
        for ind, key in (("7211", "trav_emp"), ("7213", "rv_emp"), ("71", "arts_emp"), ("722", "food_emp")):
            v = _agg(Q, ind, QTRS, f)
            o[key] = r1(sum(x[1] for x in v) / 4) if v else None
        o["leisure_emp"] = r1(o["arts_emp"] + o["food_emp"]) if o["arts_emp"] is not None and o["food_emp"] is not None else None
        lh = _agg(Q, "1026", QTRS, f)
        o["leis_hosp_emp"] = r1(sum(x[1] for x in lh) / 4) if lh else None
        totemp = sum(v[1] for v in tot) / 4 if tot else None
        o["priv_emp"] = r1(totemp, 0) if totemp else None
        o["tourism_intensity"] = r1(o["acc_emp"] / totemp * 100, 2) if o["acc_emp"] is not None and totemp else None
        o["lh_share"] = r1(o["leis_hosp_emp"] / totemp * 100, 2) if o["leis_hosp_emp"] is not None and totemp else None
        # seasonality index by calendar quarter Q1..Q4 (1.0 = 4-quarter mean); accommodation, fallback leisure&hosp
        src = acc if acc and sum(v[1] for v in acc) > 0 else (lh if lh and sum(v[1] for v in lh) > 0 else None)
        if src:
            m = sum(v[1] for v in src) / 4
            byq = {q: v[1] for (y, q), v in zip(QTRS, src)}
            o["seasonality_q"] = [r1(byq[q] / m, 3) for q in (1, 2, 3, 4)]
            o["season_src"] = "721" if src is acc else "1026"
        else:
            o["seasonality_q"] = None
            o["season_src"] = None
        cb = C.get(f, {})
        o["cbp_acc_estabs"] = cb.get("721")
        o["pop"] = P.get(f)
        o["hot_receipts_12m"] = H.get(f)
        s = STR["tx_permits"].get(f) if f.startswith("48") else None
        o["str_registered"] = s
        o["str_src"] = "tx_hot_permits_531110" if s else None
        if f == "48453":
            o["str_city_registry"] = {"austin": STR["austin"]}
            o["str_tx_permits"] = s
            o["str_registered"] = max(s or 0, STR["austin"])
            o["str_src"] = "austin_str_locations (max of city registry and TX permits)"
        if f == "08031":
            o["str_registered"] = sum(v for k, v in STR["denver_by_status"].items() if k in ("License Issued - Active", "About to Expire"))
            o["str_src"] = "denver_str_licences"
        w = W.get(f)
        if w:
            vals = list(w["monthly"].values())
            months = list(w["monthly"].keys())
            last12 = vals[-12:]
            o["wiki_views_12m"] = sum(last12)
            o["wiki_articles"] = w["articles"]
            # peak months: calendar months whose 2-year mean exceeds 1.15x overall monthly mean
            bym = defaultdict(list)
            for mo, v in zip(months, vals):
                bym[int(mo[4:6])].append(v)
            mean = sum(vals) / len(vals) if vals else 0
            idx = {k: sum(v) / len(v) / mean for k, v in bym.items()} if mean else {}
            o["wiki_season_idx"] = [r1(idx.get(k), 2) for k in range(1, 13)] if idx else None
            pk = sorted([k for k, v in idx.items() if v >= 1.15], key=lambda k: -idx[k])
            o["wiki_peak_months"] = pk or ([max(idx, key=idx.get)] if idx else None)
            o["wiki_yoy"] = r1((sum(last12) / sum(vals[-24:-12]) - 1) * 100) if len(vals) >= 24 and sum(vals[-24:-12]) else None
        else:
            o["wiki_views_12m"] = None
            o["wiki_peak_months"] = None
        o = {k: v for k, v in o.items() if v is not None or k in ("acc_estabs", "acc_emp", "acc_wages", "acc_emp_yoy", "tourism_intensity",
                                                                    "leisure_emp", "seasonality_q", "hot_receipts_12m", "str_registered",
                                                                    "wiki_views_12m", "wiki_peak_months")}
        for k in ("acc_emp", "tourism_intensity", "acc_emp_yoy", "leisure_emp", "seasonality_q", "cbp_acc_estabs", "hot_receipts_12m",
                  "str_registered", "wiki_views_12m"):
            if o.get(k) is not None:
                cov[k] += 1
        counties[f] = o
    meta = {"generated": date.today().isoformat(),
            "qcew_quarters": [f"{y}Q{q}" for y, q in QTRS], "qcew_prior": [f"{y}Q{q}" for y, q in PRIOR],
            "cbp_year": CBP_YEAR, "wiki_window": [WSTART[:6], WEND[:6]], "coverage": dict(cov), "n": len(counties),
            "fields": FIELDS, "sources": SOURCES, "rejected": REJECTED,
            "attribution": ("U.S. Bureau of Labor Statistics QCEW; U.S. Census Bureau County Business Patterns and Population Estimates; "
                            "Texas Comptroller of Public Accounts (data.texas.gov); City of Austin; City and County of Denver via Colorado "
                            "Information Marketplace; Wikidata (CC0); Wikimedia Foundation pageviews (CC0). Aggregates only."),
            "tx_hot_entities": hot_n, "tx_hot_unmatched": hot_unm}
    OUT.write_text(json.dumps({"meta": meta, "counties": counties}, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} {OUT.stat().st_size / 1e6:.2f} MB coverage {dict(cov)}")
    rows = [(f, o) for f, o in counties.items() if o.get("tourism_intensity") is not None]
    rows.sort(key=lambda x: -x[1]["tourism_intensity"])
    for f, o in rows[:25]:
        r = cf[f]
        print(f, r[ci["NAME"]], r[ci["STUSPS"]], o["tourism_intensity"], o["acc_emp"], o.get("priv_emp"), o.get("pop"), o.get("seasonality_q"),
              o.get("wiki_peak_months"))


FIELDS = {
    "acc_estabs": "QCEW NAICS 721 private establishments, 2026Q1",
    "acc_emp": "QCEW NAICS 721 private employment, mean of 12 months 2025Q2-2026Q1",
    "acc_wages": "QCEW NAICS 721 total wages USD, sum of 4 quarters",
    "acc_emp_yoy": "% change in 721 mean employment vs 2024Q2-2025Q1",
    "trav_emp": "QCEW 7211 traveler accommodation employment (4q mean)", "rv_emp": "QCEW 7213 RV parks/camps employment (4q mean)",
    "arts_emp": "QCEW 71 arts/entertainment/recreation employment (4q mean)", "food_emp": "QCEW 722 food services employment (4q mean)",
    "leisure_emp": "arts_emp + food_emp", "leis_hosp_emp": "QCEW supersector 1026 leisure & hospitality employment (4q mean)",
    "priv_emp": "QCEW total private employment (industry 10, own 5, 4q mean)",
    "tourism_intensity": "acc_emp / priv_emp * 100 (percent)", "lh_share": "leis_hosp_emp / priv_emp * 100",
    "seasonality_q": "employment index by calendar quarter [Q1,Q2,Q3,Q4], 1.0 = 4q mean (source in season_src: 721 or 1026)",
    "cbp_acc_estabs": "Census CBP 2023 NAICS 721 establishments (cross-check)", "pop": "Census population estimate July 2025",
    "hot_receipts_12m": "Texas only: local hotel occupancy tax revenue FY2025 (Ch.351 city + Ch.352 county), USD, cities assigned to modal county",
    "str_registered": "registered STR supply: TX = active state hotel-tax permits with NAICS 531110/531311 (owner-registered STRs; platform-collected STRs not included); Denver = STR licences",
    "str_city_registry": "city registry counts inside the county (Austin STR Locations)",
    "wiki_views_12m": "English Wikipedia user pageviews of county-seat article(s), Nov 2025-Oct 2026",
    "wiki_season_idx": "calendar-month index (Jan..Dec) of seat-article views over 24 months, 1.0 = mean",
    "wiki_peak_months": "calendar months with index >= 1.15 (else the single max month)", "wiki_yoy": "% change last 12 vs prior 12 months",
}
SOURCES = [
    {"name": "BLS Quarterly Census of Employment and Wages (open data CSV slices)", "url": "https://data.bls.gov/cew/data/api/{year}/{qtr}/industry/{naics}.csv",
     "licence": "US federal public domain", "licence_url": "https://www.bls.gov/opub/copyright-information.htm",
     "quote": "see usa/research/tourism_econ.md"},
    {"name": "Census County Business Patterns 2023 county file", "url": "https://www2.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip",
     "licence": "US federal public domain", "licence_url": "https://www.census.gov/about/policies/open-gov/open-data.html"},
    {"name": "Census county population estimates Vintage 2025", "url": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv",
     "licence": "US federal public domain", "licence_url": "https://www.census.gov/about/policies/open-gov/open-data.html"},
    {"name": "Texas Comptroller Local Hotel Occupancy Tax Reporting 2025", "url": "https://data.texas.gov/resource/er34-v24h", "licence": "Public Domain (dataset metadata)"},
    {"name": "Texas Comptroller Hotel Tax Permits", "url": "https://data.texas.gov/resource/hdzd-884n", "licence": "Public Domain (dataset metadata)"},
    {"name": "City of Austin STR Locations", "url": "https://datahub.austintexas.gov/resource/2fah-4p7e", "licence": "Public Domain (dataset metadata)"},
    {"name": "City of Denver Short Term Rental Licenses (Colorado Information Marketplace)", "url": "https://data.colorado.gov/resource/f3vc-vat3", "licence": "Public Domain (dataset metadata)"},
    {"name": "Wikidata county seat (P36) by FIPS (P882)", "url": "https://query.wikidata.org/sparql", "licence": "CC0 1.0", "licence_url": "https://www.wikidata.org/wiki/Wikidata:Licensing"},
    {"name": "Wikimedia Pageviews REST API (per-article monthly, user agent)", "url": "https://wikimedia.org/api/rest_v1/metrics/pageviews/",
     "licence": "CC0 1.0", "licence_url": "https://dumps.wikimedia.org/legal.html"},
]
REJECTED = [
    {"name": "AirDNA", "reason": "commercial subscription; terms bar redistribution"},
    {"name": "Inside Airbnb", "reason": "CC BY 4.0 per site but scraped from Airbnb (platform terms forbid scraping); project excludes scraped STR data"},
    {"name": "Mashvisor / AllTheRooms / other STR analytics", "reason": "paid, proprietary, scraped listings"},
    {"name": "STR scrapers (Airbnb/Vrbo listings)", "reason": "platform terms prohibit scraping; not open data"},
    {"name": "Google Trends", "reason": "Google Terms of Service; no open licence for redistribution or bulk automated access"},
    {"name": "STR/CoStar hotel occupancy & ADR", "reason": "proprietary subscription data"},
    {"name": "Colorado DOR Taxes by County and Industry (data.colorado.gov ax6t-kdim)", "reason": "licence OK (Public Domain) but stale: last month 2016-02"},
    {"name": "Chicago Shared Housing Registrations", "reason": "City of Chicago Data Terms of Use allow the City to require users to terminate any use for any reason (revocable, not permanently free); contains host names"},
    {"name": "Nashville Residential STR Permits", "reason": "licenceInfo only cites Metro Executive Order 18, which permits licence requirements or restrictions on use; no open licence; contains owner names/phones"},
    {"name": "Monthly Texas HOT receipts by hotel", "reason": "no longer published as open data; annual FY2025 local HOT reports used instead"},
]

if __name__ == "__main__":
    stages = sys.argv[1:] or ["build"]
    for s in stages:
        {"qcew": qcew, "cbp": cbp, "pop": pop, "hot": hot, "str": str_supply, "wiki": wiki, "build": build, "seats": lambda: print(len(seats()))}[s]()

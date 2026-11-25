"""Area-level cleaning-service market + affluence (aggregates only - never households).

Outputs
  web/usa/data/cleaning_market.json          county table (pool/janitorial/landscaping supply, seasonality, income)
  web/usa/data/cleaning_market_tracts.json   tract lookup {tract: [median_hh_income, share_hh_150k, share_hh_200k, households]}
  web/usa/blocks/<slug>/meta.json            adds only meta.affluence (under usa.blocklock.block_lock)

Sources (all U.S. federal statistical products = public domain, free, commercial use allowed; bulk flat files, no key):
  BLS QCEW open-data industry slices (data.bls.gov/cew/data/api/<yr>/<q>/industry/<naics>.csv)
  Census Nonemployer Statistics 2024 county file, County Business Patterns 2023 county file
  ACS 2020-2024 5-year table-based summary file B19001 + B19013 (county, tract, block group, place)
  IRS SOI ZIP-code individual income tax statistics, tax year 2023 (23zpallagi.csv)
  Census 2020 ZCTA-to-tract relationship file (tract -> dominant ZCTA when a block has no ZIP)
  EIA RECS 2020 public-use microdata (SWIMPOOL) -> weighted state share of homes with a swimming pool

Usage:  .venv/Scripts/python -u -W ignore usa/cleaning_market.py [--blocks-only] [--slugs a,b]
"""
import argparse
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usa.common import S, get  # noqa: E402
from usa.blocklock import block_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "cleaning"
CACHE.mkdir(parents=True, exist_ok=True)
BLOCKS = ROOT / "web" / "usa" / "blocks"
OUT = ROOT / "web" / "usa" / "data" / "cleaning_market.json"
OUT_TR = ROOT / "web" / "usa" / "data" / "cleaning_market_tracts.json"

QCEW_YEARS = [2023, 2024, 2025]
QCEW_LATEST = 2025
QCEW_NAICS = {"561790": "pool_svc", "561720": "janitorial", "561730": "landscaping",
              "459999": "misc_retail", "423910": "sporting_wholesale"}
ACS = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
NES_URL = "https://www2.census.gov/programs-surveys/nonemployer-statistics/datasets/2024/historical-datasets/nonemp24co.zip"
CBP_URL = "https://www2.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip"
IRS_URL = "https://www.irs.gov/pub/irs-soi/23zpallagi.csv"
REL_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_tract20_natl.txt"
RECS_URL = "https://www.eia.gov/consumption/residential/data/2020/csv/recs2020_public_v7.csv"
PD = "Public domain (U.S. federal government work, 17 U.S.C. 105) - free, commercial use allowed, no key"


def fetch(url, name=None, optional=False):
    p = CACHE / (name or url.rstrip("/").rsplit("/", 1)[1])
    if not p.exists() or p.stat().st_size == 0:
        print("  download", url, flush=True)
        r = get(url, timeout=600)
        if r.status_code == 404 and optional:
            print("    (404 - not published)", flush=True); return None
        r.raise_for_status()
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(r.content); os.replace(tmp, p)
    return p


def num(s):
    v = pd.to_numeric(s, errors="coerce")
    return v.where(v >= 0)  # ACS negative jam codes -> NaN


# ---------------------------------------------------------------- ACS income (county / tract / block group / place)
def acs_income():
    b01 = pd.read_csv(fetch(ACS + "acsdt5y2024-b19001.dat"), sep="|", dtype=str,
                      usecols=["GEO_ID", "B19001_E001", "B19001_E016", "B19001_E017"])
    b13 = pd.read_csv(fetch(ACS + "acsdt5y2024-b19013.dat"), sep="|", dtype=str,
                      usecols=["GEO_ID", "B19013_E001", "B19013_M001"])
    df = b01.merge(b13, on="GEO_ID", how="outer")
    df["lvl"] = df["GEO_ID"].str[:3]
    df = df[df["lvl"].isin(["050", "140", "150", "160"])].copy()
    df["geoid"] = df["GEO_ID"].str.split("US").str[1]
    hh = num(df["B19001_E001"])
    df["households"] = hh
    df["share_hh_200k"] = (num(df["B19001_E017"]) / hh.where(hh > 0)).round(4)
    df["share_hh_150k"] = ((num(df["B19001_E016"]) + num(df["B19001_E017"])) / hh.where(hh > 0)).round(4)
    df["mhi"] = num(df["B19013_E001"])
    df["mhi_moe"] = num(df["B19013_M001"])
    return df[["lvl", "geoid", "households", "share_hh_150k", "share_hh_200k", "mhi", "mhi_moe"]]


# ---------------------------------------------------------------- BLS QCEW
def qcew():
    frames = []
    for code in QCEW_NAICS:
        for yr in QCEW_YEARS:
            for q in (1, 2, 3, 4):
                p = fetch(f"https://data.bls.gov/cew/data/api/{yr}/{q}/industry/{code}.csv", f"qcew_{yr}q{q}_{code}.csv", optional=True)
                if p is None:
                    continue
                d = pd.read_csv(p, dtype={"area_fips": str, "own_code": str, "agglvl_code": str, "disclosure_code": str},
                                usecols=["area_fips", "own_code", "agglvl_code", "size_code", "year", "qtr",
                                         "disclosure_code", "qtrly_estabs", "month1_emplvl", "month2_emplvl",
                                         "month3_emplvl", "total_qtrly_wages"])
                d = d[(d["own_code"] == "5") & (d["size_code"] == 0) & d["agglvl_code"].isin(["58", "78"])]
                d["naics"] = code
                frames.append(d)
    q = pd.concat(frames, ignore_index=True)
    q["disc"] = q["disclosure_code"].fillna("").str.strip() != "N"
    q["emp"] = q[["month1_emplvl", "month2_emplvl", "month3_emplvl"]].mean(axis=1)
    q["lvl"] = np.where(q["agglvl_code"] == "78", "county", "state")
    return q


def seasonality(q, code):
    """Per area: Q1..Q4 employment index (mean of 4 = 1), averaged over years with all 4 quarters disclosed."""
    d = q[(q["naics"] == code) & q["disc"] & (q["emp"] > 0)]
    g = d.pivot_table(index=["area_fips", "year"], columns="qtr", values="emp", aggfunc="first").dropna()
    if g.empty:
        return {}
    idx = g.div(g.mean(axis=1), axis=0)
    out = {}
    for area, sub in idx.groupby(level=0):
        out[area] = {"q": [round(float(x), 3) for x in sub.mean().values], "years": [int(y) for y in sub.index.get_level_values(1)]}
    return out


def annual(q, code, yr=QCEW_LATEST):
    d = q[(q["naics"] == code) & (q["year"] == yr)]
    rows = {}
    for area, sub in d.groupby("area_fips"):
        r = {"estabs": int(round(sub["qtrly_estabs"].mean())) if len(sub) else None}
        if len(sub) == 4 and sub["disc"].all():
            emp = float(sub["emp"].mean())
            r["emp"] = int(round(emp))
            r["wkly_wage"] = int(round(sub["total_qtrly_wages"].sum() / (emp * 52))) if emp > 0 else None
        else:
            r["emp"] = None; r["wkly_wage"] = None
        rows[area] = r
    return rows


# ---------------------------------------------------------------- NES / CBP
def nes():
    z = zipfile.ZipFile(fetch(NES_URL))
    d = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
    d = d[d["NAICS"].isin(["56179", "56172", "56173"])]
    d["fips"] = d["ST"] + d["CTY"]
    d["ESTAB"] = pd.to_numeric(d["ESTAB"], errors="coerce")
    d["RCPTOT"] = pd.to_numeric(d["RCPTOT"], errors="coerce")  # $1,000
    return {(r.fips, r.NAICS): (r.ESTAB, r.RCPTOT) for r in d.itertuples()}


def cbp():
    z = zipfile.ZipFile(fetch(CBP_URL))
    d = pd.read_csv(z.open(z.namelist()[0]), dtype=str, usecols=["fipstate", "fipscty", "naics", "emp", "est"])
    d = d[d["naics"].isin(["561790", "561720", "561730", "238990", "459999", "423910"])]
    d["fips"] = d["fipstate"] + d["fipscty"]
    d["est"] = pd.to_numeric(d["est"], errors="coerce")
    d["emp"] = pd.to_numeric(d["emp"], errors="coerce")
    return {(r.fips, r.naics): (r.est, r.emp) for r in d.itertuples()}


# ---------------------------------------------------------------- IRS SOI ZIP, ZCTA crosswalk, RECS pools
def irs_zip():
    d = pd.read_csv(fetch(IRS_URL), dtype={"zipcode": str}, usecols=["STATEFIPS", "zipcode", "agi_stub", "N1", "A00100"])
    d = d[~d["zipcode"].isin(["00000", "99999"])]
    g = d.groupby("zipcode")
    tot_n = g["N1"].sum(); tot_a = g["A00100"].sum()
    n200 = d[d["agi_stub"] == 6].groupby("zipcode")["N1"].sum().reindex(tot_n.index).fillna(0)
    out = pd.DataFrame({"returns": tot_n, "agi_per_return": (tot_a * 1000 / tot_n).round(0),
                        "share_returns_200k": (n200 / tot_n).round(4)})
    return out


def tract_to_zcta():
    d = pd.read_csv(fetch(REL_URL), sep="|", dtype=str, encoding="utf-8-sig",
                    usecols=["GEOID_ZCTA5_20", "GEOID_TRACT_20", "AREALAND_PART"])
    d = d.dropna(subset=["GEOID_ZCTA5_20"])
    d["AREALAND_PART"] = pd.to_numeric(d["AREALAND_PART"], errors="coerce")
    d = d.sort_values("AREALAND_PART", ascending=False).drop_duplicates("GEOID_TRACT_20")
    return dict(zip(d["GEOID_TRACT_20"], d["GEOID_ZCTA5_20"]))


def recs_pools():
    d = pd.read_csv(fetch(RECS_URL), usecols=["STATE_FIPS", "state_postal", "SWIMPOOL", "NWEIGHT"], dtype={"STATE_FIPS": str})
    d["pool"] = (d["SWIMPOOL"] == 1).astype(float)
    out = {}
    for st, sub in d.groupby("STATE_FIPS"):
        out[st.zfill(2)] = {"share": round(float((sub["pool"] * sub["NWEIGHT"]).sum() / sub["NWEIGHT"].sum()), 4),
                            "homes_with_pool": int(round((sub["pool"] * sub["NWEIGHT"]).sum())), "n": int(len(sub))}
    return out


# ---------------------------------------------------------------- county table
def iv(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else int(x)


def build_counties(inc):
    print("QCEW ...", flush=True)
    q = qcew()
    seas = {c: seasonality(q, c) for c in ("561790", "561730")}
    ann = {c: annual(q, c) for c in QCEW_NAICS}
    print("NES / CBP / RECS ...", flush=True)
    ne, cb, recs = nes(), cbp(), recs_pools()
    co = inc[inc["lvl"] == "050"].set_index("geoid")
    counties = {}
    for fips, r in co.iterrows():
        st = fips[:2] + "000"
        a = {c: ann[c].get(fips, {}) for c in QCEW_NAICS}
        s = seas["561790"].get(fips); src = "county"
        if not s:
            s = seas["561790"].get(st); src = "state" if s else None
        ls = seas["561730"].get(fips) or seas["561730"].get(st)
        hh = r["households"]
        pool_est = a["561790"].get("estabs")
        pool_emp = a["561790"].get("emp")
        cbp_pool = cb.get((fips, "561790"), (None, None))
        nes_pool = ne.get((fips, "56179"), (None, None))
        est_for_rate = pool_est if pool_est is not None else cbp_pool[0]
        supply = (est_for_rate or 0) + (nes_pool[0] or 0)
        emp_for_rate = pool_emp if pool_emp is not None else (cbp_pool[1] if cbp_pool[1] and cbp_pool[1] > 0 else None)
        rc = recs.get(fips[:2], {})
        counties[fips] = {
            "pool_svc_estabs": iv(pool_est), "pool_svc_emp": iv(pool_emp), "pool_svc_wkly_wage": iv(a["561790"].get("wkly_wage")),
            "pool_svc_nonemployers": iv(nes_pool[0]), "pool_svc_nonemployer_receipts_k": iv(nes_pool[1]),
            "pool_svc_cbp_estabs": iv(cbp_pool[0]), "pool_svc_cbp_emp": iv(cbp_pool[1]),
            "pool_svc_seasonality_q": s["q"] if s else None, "pool_svc_seasonality_src": src,
            "pool_svc_seasonality_years": s["years"] if s else None,
            "landscaping_seasonality_q": ls["q"] if ls else None,
            "window_janitorial_estabs": iv(a["561720"].get("estabs")), "window_janitorial_emp": iv(a["561720"].get("emp")),
            "nonemployer_janitorial": iv(ne.get((fips, "56172"), (None,))[0]),
            "landscaping_estabs": iv(a["561730"].get("estabs")), "landscaping_emp": iv(a["561730"].get("emp")),
            "nonemployer_landscaping": iv(ne.get((fips, "56173"), (None,))[0]),
            "specialty_trade_238990_estabs": iv(cb.get((fips, "238990"), (None,))[0]),  # QCEW does not publish a 238990 slice -> CBP 2023
            "misc_retail_459999_estabs": iv(a["459999"].get("estabs")),
            "pool_wholesale_423910_estabs": iv(a["423910"].get("estabs")),
            "households": iv(hh), "median_hh_income": iv(r["mhi"]),
            "share_hh_150k": None if pd.isna(r["share_hh_150k"]) else float(r["share_hh_150k"]),
            "share_hh_200k": None if pd.isna(r["share_hh_200k"]) else float(r["share_hh_200k"]),
            "svc_per_10k_households": round(supply / hh * 1e4, 2) if hh and hh > 0 and supply else None,
            "pool_svc_emp_per_10k_households": round(emp_for_rate / hh * 1e4, 2) if hh and hh > 0 and emp_for_rate else None,
            "pool_svc_emp_src": "qcew" if pool_emp is not None else ("cbp" if emp_for_rate else None),
            "state_pool_share_recs2020": rc.get("share"),
        }
    return counties, recs


def write_json(p, obj):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- per-block affluence
def label(pct):
    if pct is None:
        return None
    return "very high" if pct >= 90 else "high" if pct >= 70 else "middle" if pct >= 30 else "lower"


def block_affluence(inc, irs, t2z, slugs=None):
    tr = inc[inc["lvl"] == "140"].set_index("geoid")
    bg = inc[inc["lvl"] == "150"].set_index("geoid")
    tr_pct_nat = tr["mhi"].rank(pct=True)
    bg_county = bg.index.str[:5]
    tr_county = tr.index.str[:5]
    done = []
    for d in sorted(BLOCKS.iterdir()):
        mp = d / "meta.json"
        if not mp.exists() or (slugs and d.name not in slugs):
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        tract = m.get("tract"); bgid = (m.get("block_geoid") or "")[:12] or None
        if not tract and bgid:
            tract = bgid[:11]
        if not tract:
            print("  skip (no tract):", d.name); continue
        cty = tract[:5]
        t = tr.loc[tract] if tract in tr.index else None
        b = bg.loc[bgid] if bgid and bgid in bg.index else None
        ct_tr = tr[tr_county == cty]["mhi"].dropna()
        ct_bg = bg[bg_county == cty]["mhi"].dropna()
        pct_tr = round(float((ct_tr <= t["mhi"]).mean() * 100), 1) if t is not None and pd.notna(t["mhi"]) and len(ct_tr) else None
        pct_bg = round(float((ct_bg <= b["mhi"]).mean() * 100), 1) if b is not None and pd.notna(b["mhi"]) and len(ct_bg) else None
        zip_ = m.get("zip"); zsrc = "meta.zip" if zip_ else None
        if not zip_ or zip_ not in irs.index:
            z2 = t2z.get(tract)
            if z2:
                zip_, zsrc = z2, "dominant 2020 ZCTA of tract (Census relationship file)"
        zr = irs.loc[zip_] if zip_ and zip_ in irs.index else None
        f = lambda v: None if v is None or pd.isna(v) else (int(v) if float(v).is_integer() and abs(v) > 1 else float(v))
        pct = pct_bg if pct_bg is not None else pct_tr
        aff = {
            "tract": tract, "block_group": bgid,
            "median_hh_income": {"tract": f(t["mhi"]) if t is not None else None,
                                 "block_group": f(b["mhi"]) if b is not None else None,
                                 "block_group_moe": f(b["mhi_moe"]) if b is not None else None},
            "share_hh_200k": {"tract": f(t["share_hh_200k"]) if t is not None else None,
                              "block_group": f(b["share_hh_200k"]) if b is not None else None},
            "share_hh_150k": {"tract": f(t["share_hh_150k"]) if t is not None else None,
                              "block_group": f(b["share_hh_150k"]) if b is not None else None},
            "households": {"tract": f(t["households"]) if t is not None else None,
                           "block_group": f(b["households"]) if b is not None else None},
            "zip": zip_, "zip_source": zsrc,
            "zip_agi_per_return": f(zr["agi_per_return"]) if zr is not None else None,
            "zip_share_returns_200k": f(zr["share_returns_200k"]) if zr is not None else None,
            "county_percentile": {"block_group": pct_bg, "tract": pct_tr,
                                  "basis": "median household income vs all block groups / tracts in the county (ACS 2020-2024)"},
            "national_tract_percentile": round(float(tr_pct_nat.get(tract) * 100), 1) if tract in tr_pct_nat.index and pd.notna(tr_pct_nat.get(tract)) else None,
            "label": label(pct),
            "label_rule": "county percentile of block-group median HH income (tract if BG missing): >=90 very high, >=70 high, >=30 middle, else lower",
            "sources": ["ACS 2020-2024 5-year B19001/B19013 (Census, public domain)",
                        "IRS SOI ZIP-code data tax year 2023 (public domain)"],
            "note": "Area aggregates only; no household-level income is used or inferred.",
        }
        with block_lock(d):
            mm = json.loads(mp.read_text(encoding="utf-8"))  # re-read inside lock
            mm["affluence"] = aff
            tmp = mp.with_suffix(".json.p4tmp")
            tmp.write_text(json.dumps(mm, indent=1), encoding="utf-8")
            os.replace(tmp, mp)
        done.append((d.name, aff))
        print(f"  {d.name:16s} tract={tract} bgMHI={aff['median_hh_income']['block_group']} trMHI={aff['median_hh_income']['tract']} "
              f"bg200k={aff['share_hh_200k']['block_group']} zip={zip_} agi/ret={aff['zip_agi_per_return']} pct={pct} -> {aff['label']}")
    return done


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks-only", action="store_true")
    ap.add_argument("--slugs", default="")
    a = ap.parse_args()
    print("ACS income ...", flush=True)
    inc = acs_income()
    print("  levels:", inc["lvl"].value_counts().to_dict())
    irs = irs_zip(); t2z = tract_to_zcta()
    print("  IRS ZIPs:", len(irs), " tract->ZCTA:", len(t2z))
    if not a.blocks_only:
        counties, recs = build_counties(inc)
        tr = inc[inc["lvl"] == "140"]
        trl = {r.geoid: [iv(r.mhi), None if pd.isna(r.share_hh_150k) else float(r.share_hh_150k),
                         None if pd.isna(r.share_hh_200k) else float(r.share_hh_200k), iv(r.households)]
               for r in tr.itertuples()}
        write_json(OUT_TR, {"cols": ["median_hh_income", "share_hh_150k", "share_hh_200k", "households"],
                            "source": "ACS 2020-2024 5-year B19001/B19013 (U.S. Census Bureau, public domain)", "tracts": trl})
        meta = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [
                {"name": "BLS QCEW open data, NAICS 561790/561720/561730/459999/423910, private ownership, county + state, "
                         f"{QCEW_YEARS[0]}Q1-{QCEW_YEARS[-1]}Q4", "url": "https://data.bls.gov/cew/data/api/{yr}/{q}/industry/{naics}.csv", "licence": PD},
                {"name": "Census Nonemployer Statistics 2024, county (NAICS 56179/56172/56173)", "url": NES_URL, "licence": PD},
                {"name": "Census County Business Patterns 2023, county", "url": CBP_URL, "licence": PD},
                {"name": "ACS 2020-2024 5-year table-based summary file B19001, B19013 (county/tract/block group)", "url": ACS, "licence": PD},
                {"name": "IRS SOI ZIP-code individual income tax statistics, tax year 2023", "url": IRS_URL, "licence": PD},
                {"name": "Census 2020 ZCTA5-tract relationship file", "url": REL_URL, "licence": PD},
                {"name": "EIA Residential Energy Consumption Survey 2020 public-use microdata (SWIMPOOL, NWEIGHT)", "url": RECS_URL, "licence": PD},
            ],
            "rejected": [
                {"name": "IBISWorld industry reports (pool & spa maintenance, janitorial)", "reason": "paid, proprietary licence"},
                {"name": "Statista market-size statistics", "reason": "paid subscription, redistribution prohibited"},
                {"name": "Angi / HomeAdvisor cost guides", "reason": "proprietary website content; terms forbid commercial reuse/scraping"},
                {"name": "Thumbtack cost / pro-count data", "reason": "proprietary; no open licence"},
                {"name": "Census Data API (api.census.gov)", "reason": "needs a key for volume use; identical data taken from public bulk flat files instead"},
                {"name": "Pool & Hot Tub Alliance / industry association pool counts", "reason": "member/paid publications, no open licence"},
            ],
            "definitions": {
                "pool_svc_*": "NAICS 561790 'Other Services to Buildings and Dwellings' - includes swimming-pool cleaning & maintenance but also "
                              "chimney, gutter, duct, power-washing, parking-lot cleaning. No pool-only code exists; treat as an upper-bound proxy.",
                "pool_svc_estabs/emp/wkly_wage": f"QCEW {QCEW_LATEST} private-sector annual average (mean of 4 quarters / 12 months); emp & wage null when any "
                                                 "quarter is BLS-suppressed (disclosure 'N'); establishments are always published.",
                "pool_svc_cbp_estabs/emp": "CBP 2023 (March employment); fallback where QCEW is suppressed; CBP emp 0 = noise-suppressed.",
                "pool_svc_nonemployers": "Nonemployer Statistics 2024 NAICS 56179 sole proprietors/no-payroll firms (most solo pool cleaners); receipts in $1,000.",
                "pool_svc_seasonality_q": f"Q1..Q4 employment index (mean=1.0) from QCEW monthly employment, averaged over the {QCEW_YEARS[0]}-{QCEW_YEARS[-1]} years "
                                          "with all 4 quarters disclosed; src 'state' = state-level 561790 profile used when the county is suppressed.",
                "landscaping_seasonality_q": "same as above for NAICS 561730 (county, else state).",
                "window_janitorial_*": "NAICS 561720 Janitorial Services (includes window cleaning, residential & commercial).",
                "landscaping_*": "NAICS 561730 Landscaping Services.",
                "specialty_trade_238990_estabs": "NAICS 238990 All Other Specialty Trade Contractors - includes outdoor swimming-pool construction (mixed code); CBP 2023 because QCEW publishes no 238990 slice.",
                "misc_retail_459999_estabs": "NAICS 459999 All Other Miscellaneous Retailers - includes swimming-pool (above-ground) and supply stores (mixed code).",
                "pool_wholesale_423910_estabs": "NAICS 423910 Sporting & Recreational Goods wholesale - includes swimming pools & equipment (mixed code).",
                "median_hh_income / share_hh_150k / share_hh_200k / households": "ACS 2020-2024 5-year B19013 / (B19001_016+017)/B19001_001 / B19001_017/B19001_001 / B19001_001.",
                "svc_per_10k_households": "(QCEW 561790 establishments, CBP if missing) + NES 56179 nonemployers, per 10,000 households.",
                "pool_svc_emp_per_10k_households": "561790 paid employment (QCEW, CBP if suppressed) per 10,000 households.",
                "state_pool_share_recs2020": "EIA RECS 2020 weighted share of occupied primary homes with a swimming pool (SWIMPOOL=1), state level; "
                                             "ACS and AHS have no county/tract pool variable.",
                "pool_counts_note": "No federal source publishes pool counts below state level. ACS has no pool item; AHS has no published county pool counts. "
                                    "Parcel-level pool flags (e.g. Maricopa assessor) are handled by another lane.",
            },
            "recs_state_pools": recs,
            "coverage": {},
        }
        vals = list(counties.values())
        cov = lambda k: sum(1 for v in vals if v.get(k) is not None)
        meta["coverage"] = {"counties": len(vals), **{k: cov(k) for k in [
            "pool_svc_estabs", "pool_svc_emp", "pool_svc_nonemployers", "pool_svc_cbp_estabs", "pool_svc_seasonality_q",
            "window_janitorial_estabs", "nonemployer_janitorial", "landscaping_estabs", "median_hh_income", "share_hh_200k",
            "svc_per_10k_households", "pool_svc_emp_per_10k_households"]},
            "pool_svc_seasonality_county_level": sum(1 for v in vals if v["pool_svc_seasonality_src"] == "county")}
        write_json(OUT, {"meta": meta, "counties": counties})
        print("wrote", OUT, OUT.stat().st_size, "bytes;", OUT_TR, OUT_TR.stat().st_size, "bytes")
        print("coverage:", meta["coverage"])
    slugs = set(filter(None, a.slugs.split(","))) or None
    print("blocks ...", flush=True)
    block_affluence(inc, irs, t2z, slugs)


if __name__ == "__main__":
    main()

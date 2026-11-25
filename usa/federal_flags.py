"""Federal incentive flags, rents, house-price index and permits for built 3D blocks.

  .venv\\Scripts\\python -u -W ignore usa/federal_flags.py <slug> [<slug> ...]
  .venv\\Scripts\\python -u -W ignore usa/federal_flags.py --all

Only sources marked commercial_ok "yes" in usa/research/federal_realestate.json (US federal works, 17 U.S.C. 105).
Adds (never removes or changes other keys):
  houses.json  h.incentives = {qct, dda, nmtc, usda_rural_eligible, hubzone, lihtc_within_400m, ..., sources}
  meta.json    meta.rent, meta.fhfa_hpi, meta.permits_bps, meta.federal_flags
Sources
  QCT 2026, DDA 2026, NMTC (2016-20 ACS), LIHTC, FMR, SAFMR : HUD eGIS ArcGIS mirrors (services.arcgis.com/VTyQ9soqVukalItT)
  USDA RD single-family ineligible areas : rdgdwe.sc.egov.usda.gov Eligibility MapServer layer 4 (RHS SFH MFH)
  FHFA annual HPI by tract (2010 tracts) and ZIP5 : www.fhfa.gov/hpi/download/annual/
  Census Building Permits Survey annual county + place files : www2.census.gov/econ/bps/
  SBA HUBZone: no documented public query service -> null with reason.
All network fetching happens before the per-block lock (web/usa/blocks/<slug>/.lock) is taken; inside the lock both
files are re-read from disk, only our keys are set, both are written, and the lock is removed.
A failing source is recorded as an error string in meta.federal_flags.status and never stops the run.
"""
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "federal"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) roofprint-usa/0.1 (open-data research)"
S = requests.Session()
S.headers["User-Agent"] = UA

HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services/"
L_QCT = HUD + "QUALIFIED_CENSUS_TRACTS_2026/FeatureServer/0"
L_DDA = HUD + "Difficult_Development_Areas_2026/FeatureServer/0"
L_NMTC = HUD + "NMTC20/FeatureServer/0"
L_LIHTC = HUD + "LIHTC/FeatureServer/0"
L_FMR = HUD + "Fair_Market_Rents/FeatureServer/0"
L_ZCTA = HUD + "HUD_PDR_Small_Area_Fair_Market_Rents/FeatureServer/0"
L_SAFMR_T = HUD + "HUD_PDR_Small_Area_Fair_Market_Rents/FeatureServer/1"
L_SAFMR_DESIG = HUD + "SAFMR_Year/FeatureServer/2"
L_USDA = "https://rdgdwe.sc.egov.usda.gov/arcgis/rest/services/Eligibility/Eligibility/MapServer/4"
FHFA_TRACT = "https://www.fhfa.gov/hpi/download/annual/hpi_at_tract.csv"
FHFA_ZIP5 = "https://www.fhfa.gov/hpi/download/annual/hpi_at_zip5.xlsx"
BPS = "https://www2.census.gov/econ/bps/"
BPS_REGION = {"Northeast Region": "ne", "Midwest Region": "mw", "South Region": "so", "West Region": "we"}
STATE_REGION = {}
for _r, _sts in {"Northeast Region": "09 23 25 33 34 36 42 44 50",
                 "Midwest Region": "17 18 19 20 26 27 29 31 38 39 46 55",
                 "South Region": "01 05 10 11 12 13 21 22 24 28 37 40 45 47 48 51 54",
                 "West Region": "02 04 06 08 15 16 30 32 35 41 49 53 56"}.items():
    for _s in _sts.split():
        STATE_REGION[_s] = _r

LIHTC_RADIUS_M = 400
LIHTC_FIELDS = "HUD_ID,PROJECT,PROJ_ADD,PROJ_CTY,PROJ_ST,PROJ_ZIP,N_UNITS,LI_UNITS,YR_PIS,YR_ALLOC,CREDIT,NON_PROF,QCT,DDA"
LOCK_WAIT_S, LOCK_STALE_S = 600, 900

HUD_ATTR = ("Source: U.S. Department of Housing and Urban Development (HUD) Office of Policy Development and Research, "
            "HUD eGIS open data (hudgis-hud.opendata.arcgis.com); layer credit: \"U.S. Department of Housing and Urban "
            "Development, U.S. Census Bureau\". Not endorsed by HUD.")
ATTRIBUTION = {
    "qct": HUD_ATTR + " Layer: Qualified Census Tracts 2026.",
    "dda": HUD_ATTR + " Layer: Difficult Development Areas 2026.",
    "nmtc": "Source: U.S. Department of the Treasury, CDFI Fund, New Markets Tax Credit low-income community "
            "eligibility (2016-2020 ACS), via HUD eGIS layer NMTC20.",
    "lihtc": HUD_ATTR + " Layer: Low-Income Housing Tax Credit Properties (HUD National LIHTC Database). "
             "Locations are general property locations, not individual buildings.",
    "usda_rd": "Source: USDA Rural Development GIS Group, Rural Designations (Ineligible Areas), layer RHS SFH MFH. "
               "Indicative only; confirm at https://eligibility.sc.egov.usda.gov/.",
    "fmr": HUD_ATTR + " Layer: Fair Market Rents.",
    "safmr": HUD_ATTR + " Layers: Small Area Fair Market Rents (SAFMR_table) and SAFMR_Year (designated areas).",
    "fhfa_hpi": "Source: Federal Housing Finance Agency (FHFA) House Price Index, annual all-transactions indexes "
                "for census tracts and five-digit ZIP codes (developmental indexes).",
    "bps": "Source: U.S. Census Bureau, Building Permits Survey (annual county and place files).",
    "hubzone": "U.S. Small Business Administration HUBZone program (not queried).",
}
LICENCE = "US federal government works, not subject to copyright (17 U.S.C. 105): https://www.usa.gov/government-copyright"
HUBZONE_REASON = ("no documented public query service: SBA publishes HUBZones only through the map app "
                  "maps.certify.sba.gov/hubzone/map (its internal search endpoint is not a public API; the research file "
                  "notes the bulk shapefile URL is not pinned)")


# ---------------------------------------------------------------- http helpers
def _get(url, params=None, timeout=90, tries=4, stream=False):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=timeout, stream=stream)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def arc_query(layer, **p):
    p.setdefault("f", "json")
    j = _get(layer + "/query", params=p).json()
    if "error" in j:
        raise RuntimeError(f"ArcGIS error {j['error'].get('code')}: {j['error'].get('message')}")
    return j


def env_params(w, s, e, n):
    return dict(geometry=f"{w},{s},{e},{n}", geometryType="esriGeometryEnvelope", inSR=4326,
                spatialRel="esriSpatialRelIntersects")


def pt_params(lon, lat):
    return dict(geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", inSR=4326,
                spatialRel="esriSpatialRelIntersects")


def polygons(layer, bbox, fields):
    """Features (shapely geometry, attributes) of a polygon layer that intersect the bbox, in EPSG:4326."""
    j = arc_query(layer, f="geojson", outFields=fields, outSR=4326, returnGeometry="true", **env_params(*bbox))
    out = []
    for f in j.get("features", []):
        if f.get("geometry"):
            out.append((shape(f["geometry"]), f.get("properties") or {}))
    return out


def layer_edit_date(layer):
    try:
        j = _get(layer, params={"f": "json"}).json()
        ms = (j.get("editingInfo") or {}).get("dataLastEditDate")
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else None
    except Exception:  # noqa: BLE001
        return None


def cached(url, name, max_age_days=30):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists() or p.stat().st_size < 1000 or time.time() - p.stat().st_mtime > max_age_days * 86400:
        print(f"  downloading {url}")
        r = _get(url, timeout=900)
        p.write_bytes(r.content)
    return p


def _yr(v):
    """HUD LIHTC uses 8888/9999 for unknown years."""
    v = _num(v) if v is not None else None
    return v if v and 1980 <= v <= 2100 else None


def hav_m(lon1, lat1, lon2, lat2):
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- national tables (loaded once per run)
_TABLES = {}


def fhfa_tract():
    if "tract" not in _TABLES:
        t = pd.read_csv(cached(FHFA_TRACT, "hpi_at_tract.csv"), dtype={"tract": str})
        _TABLES["tract"] = {k: g.sort_values("year") for k, g in t.groupby("tract")}
    return _TABLES["tract"]


def fhfa_zip5():
    if "zip5" not in _TABLES:
        z = pd.read_excel(cached(FHFA_ZIP5, "hpi_at_zip5.xlsx"), header=None, skiprows=6, dtype=str)
        z = z.iloc[:, :6]
        z.columns = ["zip", "year", "annual_change", "hpi", "hpi1990", "hpi2000"]
        z = z.dropna(subset=["zip", "year"])
        z["year"] = pd.to_numeric(z.year, errors="coerce")
        for c in ("annual_change", "hpi", "hpi1990", "hpi2000"):
            z[c] = pd.to_numeric(z[c], errors="coerce")
        _TABLES["zip5"] = {k: g.sort_values("year") for k, g in z.groupby("zip")}
        _TABLES["zip5_updated"] = None
        try:
            head = pd.read_excel(CACHE / "hpi_at_zip5.xlsx", header=None, nrows=5, dtype=str)
            for v in head[0].dropna():
                m = re.search(r"Last updated:\s*(.+?)\.?$", v)
                if m:
                    _TABLES["zip5_updated"] = m.group(1)
        except Exception:  # noqa: BLE001
            pass
    return _TABLES["zip5"]


def hpi_summary(g):
    g = g.dropna(subset=["hpi"])
    if g.empty:
        return None
    last = g.iloc[-1]
    y = int(last.year)
    def at(yr):
        r = g[g.year == yr]
        return float(r.hpi.iloc[0]) if len(r) else None
    h1, h5, h10 = at(y - 1), at(y - 5), at(y - 10)
    pct = lambda a: round((float(last.hpi) / a - 1) * 100, 2) if a else None  # noqa: E731
    return {"year": y, "hpi": round(float(last.hpi), 2),
            "hpi_base2000": None if pd.isna(last.hpi2000) else round(float(last.hpi2000), 2),
            "change_1yr_pct": None if pd.isna(last.annual_change) else round(float(last.annual_change), 2),
            "change_5yr_pct": pct(h5), "change_10yr_pct": pct(h10),
            "change_1yr_pct_from_index": pct(h1)}


def _bps_latest(dir_url, prefix):
    idx = _get(dir_url).text
    yrs = sorted(set(re.findall(rf'href="{prefix}(20\d\d)a\.txt"', idx)))
    if not yrs:
        raise RuntimeError(f"no annual files under {dir_url}")
    return yrs[-1]


def _num(x):
    try:
        return int(float(str(x).strip()))
    except Exception:  # noqa: BLE001
        return None


def _struct(cells):
    """cells = 12 values: (bldgs, units, value) x (1-unit, 2-units, 3-4 units, 5+ units)."""
    keys = ["units_1", "units_2", "units_3_4", "units_5plus"]
    out = {}
    for i, k in enumerate(keys):
        b, u, v = (_num(c) for c in cells[3 * i:3 * i + 3])
        out[k] = {"bldgs": b, "units": u, "value_usd": v}
    out["total_units"] = sum((out[k]["units"] or 0) for k in keys)
    out["total_value_usd"] = sum((out[k]["value_usd"] or 0) for k in keys)
    return out


def bps_county():
    if "bps_co" not in _TABLES:
        y = _bps_latest(BPS + "County/", "co")
        url = f"{BPS}County/co{y}a.txt"
        txt = cached(url, f"bps_co{y}a.txt", 365).read_text(encoding="latin-1")
        rows = {}
        for line in txt.splitlines()[3:]:
            c = [x.strip() for x in line.split(",")]
            if len(c) < 18 or not c[1].isdigit():
                continue
            rows[c[1].zfill(2) + c[2].zfill(3)] = {"name": c[5], "cells": c[6:18]}
        _TABLES["bps_co"] = (int(y), url, rows)
    return _TABLES["bps_co"]


def bps_place(state_fips):
    reg = STATE_REGION.get(state_fips)
    if not reg:
        raise RuntimeError(f"no Census region for state {state_fips}")
    key = "bps_pl_" + reg
    if key not in _TABLES:
        d = BPS + "Place/" + reg.replace(" ", "%20") + "/"
        y = _bps_latest(d, BPS_REGION[reg])
        url = f"{d}{BPS_REGION[reg]}{y}a.txt"
        txt = cached(url, f"bps_{BPS_REGION[reg]}{y}a.txt", 365).read_text(encoding="latin-1")
        rows = []
        for line in txt.splitlines()[3:]:
            c = [x.strip() for x in line.split(",")]
            if len(c) < 29 or not c[1].isdigit():
                continue
            rows.append({"state": c[1].zfill(2), "county": c[3], "place_fips": c[5], "pop": _num(c[7]),
                         "name": c[16], "months_rep": _num(c[15]), "cells": c[17:29]})
        _TABLES[key] = (int(y), url, rows)
    return _TABLES[key]


def _norm_place(n):
    n = re.sub(r"\s*\(.*?\)", "", str(n or "")).strip().lower()
    return re.sub(r"\s+(city|town|village|borough|municipality|cdp|city and borough|metropolitan government)$", "", n)


# ---------------------------------------------------------------- per-block fetch (no lock held)
def fetch_block(meta, houses):
    st, res = {}, {}
    w, s, e, n = meta["bounds"]
    pts = [(h.get("lon"), h.get("lat")) for h in houses]
    good = [(x, y) for x, y in pts if x is not None and y is not None]
    lons = [p[0] for p in good] + [w, e]
    lats = [p[1] for p in good] + [s, n]
    bb = (min(lons) - 0.0005, min(lats) - 0.0005, max(lons) + 0.0005, max(lats) + 0.0005)
    lon0, lat0 = meta["origin"]

    def run(name, fn):
        try:
            res[name] = fn()
            st[name] = "ok"
        except Exception as ex:  # noqa: BLE001
            st[name] = f"error: {type(ex).__name__}: {str(ex)[:200]}"
            res[name] = None

    run("qct", lambda: polygons(L_QCT, bb, "GEOID,NAME"))
    run("dda", lambda: polygons(L_DDA, bb, "ZCTA5,DDA_CODE,DDA_NAME,DDA_TYPE"))
    run("nmtc", lambda: polygons(L_NMTC, bb, "GEOID,METRO,NMTC_LIC_INC,POV_RATE_16_20_ACS,POV_RATE_QUALIFY,"
                                             "MFI_BENCH_16_20_ACS,MFI_BENCH_QUALIFY,UNEMPLOY_RATE_16_20_ACS,"
                                             "SEVERE_DISTRESS,DEEP_DISTRESS"))

    def usda():
        # ineligible-area polygons are county-sized: avoid downloading geometry, query points instead
        hit = arc_query(L_USDA, outFields="OBJECTID", returnGeometry="false", **env_params(*bb)).get("features", [])
        if not hit:
            return {"mode": "bbox clear", "inel": {i: False for i in range(len(pts))}}
        within = dict(env_params(*bb)); within["spatialRel"] = "esriSpatialRelWithin"
        allin = arc_query(L_USDA, outFields="OBJECTID", returnGeometry="false", **within).get("features", [])
        if allin:
            return {"mode": "bbox inside ineligible area", "inel": {i: True for i in range(len(pts))}}
        inel = {}
        for i, (x, y) in enumerate(pts):
            if x is None or y is None:
                continue
            inel[i] = bool(arc_query(L_USDA, outFields="OBJECTID", returnGeometry="false",
                                     **pt_params(x, y)).get("features"))
        return {"mode": "per-house point query", "inel": inel}
    run("usda_rd", usda)

    def lihtc():
        dlat = (LIHTC_RADIUS_M + 100) / 110540
        dlon = (LIHTC_RADIUS_M + 100) / (111320 * math.cos(math.radians(lat0)))
        j = arc_query(L_LIHTC, outFields=LIHTC_FIELDS, outSR=4326, returnGeometry="true",
                      **env_params(bb[0] - dlon, bb[1] - dlat, bb[2] + dlon, bb[3] + dlat))
        out = []
        for f in j.get("features", []):
            g = f.get("geometry") or {}
            if g.get("x") is None:
                continue
            a = f["attributes"]
            out.append({"hud_id": a.get("HUD_ID"), "project": a.get("PROJECT"), "address": a.get("PROJ_ADD"),
                        "city": a.get("PROJ_CTY"), "state": a.get("PROJ_ST"), "zip": a.get("PROJ_ZIP"),
                        "units": a.get("N_UNITS"), "li_units": a.get("LI_UNITS"),
                        "year_placed_in_service": _yr(a.get("YR_PIS")), "year_allocated": _yr(a.get("YR_ALLOC")),
                        "credit": a.get("CREDIT"), "lon": round(g["x"], 6), "lat": round(g["y"], 6)})
        return out
    run("lihtc", lihtc)

    def fmr():
        f = arc_query(L_FMR, outFields="FMR_CODE,FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR",
                      returnGeometry="false", **pt_params(lon0, lat0)).get("features", [])
        if not f:
            raise RuntimeError("no FMR area contains the block origin")
        a = f[0]["attributes"]
        return {"code": a["FMR_CODE"], "name": a["FMR_AREANAME"],
                **{f"br{i}": a.get(f"FMR_{i}BDR") for i in range(5)}}
    run("fmr", fmr)

    def zcta():
        f = arc_query(L_ZCTA, outFields="GEOID", returnGeometry="false", **pt_params(lon0, lat0)).get("features", [])
        return f[0]["attributes"]["GEOID"] if f else None
    if meta.get("zip"):
        res["zip"], res["zip_basis"] = str(meta["zip"]), "meta.zip"
    else:
        run("zcta", zcta)
        res["zip"] = res.get("zcta")
        res["zip_basis"] = "ZCTA (2020) containing the block origin, HUD eGIS SAFMR ZCTA layer" if res["zip"] else None
    zips = sorted({str(z) for z in [res["zip"]] + [h.get("zip") for h in houses] if z})

    def safmr():
        if not zips:
            raise RuntimeError("block has no ZIP")
        where = "ID IN (" + ",".join(f"'{z}'" for z in zips) + ")"
        rows = arc_query(L_SAFMR_T, where=where, outFields="HUD_CODE,FMR_NAME,ID,SAFMR_0BR,SAFMR_1BR,SAFMR_2BR,"
                                                           "SAFMR_3BR,SAFMR_4BR", returnGeometry="false").get("features", [])
        des = arc_query(L_SAFMR_DESIG, where="ZCTA5 IN (" + ",".join(f"'{z}'" for z in zips) + ")",
                        outFields="ZCTA5,Year_Designated,HUD_CODE,Metro_Name,SAFMR_0BR,SAFMR_1BR,SAFMR_2BR,SAFMR_3BR,SAFMR_4BR",
                        returnGeometry="false").get("features", [])
        return {"rows": [r["attributes"] for r in rows], "designated": [d["attributes"] for d in des]}
    run("safmr", safmr)

    def hpi_tract():
        tr = meta.get("tract")
        t = fhfa_tract()
        if not tr:
            raise RuntimeError("block has no tract")
        if tr not in t:
            return {"missing": f"tract {tr} not in FHFA file (FHFA tract HPI uses 2010 tract boundaries; "
                               f"2020 tract may have been renumbered or lack enough repeat sales)"}
        return hpi_summary(t[tr])
    run("fhfa_hpi_tract", hpi_tract)

    def hpi_zip():
        z = fhfa_zip5()
        out = {}
        for zz in zips:
            out[zz] = hpi_summary(z[zz]) if zz in z else None
        return out
    run("fhfa_hpi_zip5", hpi_zip)

    def bpsc():
        y, url, rows = bps_county()
        cf = (meta.get("state_fips") or meta.get("fips", "")[:2]) + (meta.get("county_fips") or meta.get("fips", "")[2:])
        cf = meta.get("fips") or cf
        r = rows.get(cf)
        if not r:
            raise RuntimeError(f"county {cf} not in {url}")
        return {"year": y, "fips": cf, "name": r["name"], **_struct(r["cells"]), "url": url}
    run("bps_county", bpsc)

    def bpsp():
        stf = (meta.get("state_fips") or (meta.get("fips") or "")[:2]).zfill(2)
        city = meta.get("place") or meta.get("city")
        if not city:
            raise RuntimeError("block has no city/place name")
        y, url, rows = bps_place(stf)
        want = {str(city).strip().lower(), _norm_place(city)}
        if meta.get("state"):
            want |= {f"{w} {str(meta['state']).lower()}" for w in list(want)}   # e.g. "Kansas City MO"
        cand = [r for r in rows if r["state"] == stf and
                ({str(r["name"]).strip().lower(), _norm_place(r["name"])} & want)]
        exact = [r for r in cand if str(r["name"]).strip().lower() in
                 {str(city).strip().lower(), f"{str(city).strip().lower()} {str(meta.get('state') or '').lower()}"}]
        cand = exact or cand
        if not cand:
            return {"missing": f"no BPS place named {city!r} in state {stf} ({url})"}
        cand.sort(key=lambda r: -(r["pop"] or 0))
        r = cand[0]
        return {"year": y, "name": r["name"], "place_fips": r["place_fips"], "population": r["pop"],
                "months_reported": r["months_rep"], **_struct(r["cells"]), "url": url,
                **({"note": f"{len(cand)} BPS rows matched; largest population used"} if len(cand) > 1 else {})}
    run("bps_place", bpsp)
    st["hubzone"] = "not available: " + HUBZONE_REASON
    return st, res


# ---------------------------------------------------------------- assemble keys
def _hit(polys, x, y, pred=lambda a: True):
    p = Point(x, y)
    for g, a in polys or []:
        if g.covers(p) and pred(a):
            return a
    return None


def build(meta, houses, st, res, vint):
    src = {"qct": "HUD QCT 2026 (HUD eGIS QUALIFIED_CENSUS_TRACTS_2026)",
           "dda": "HUD DDA 2026 (HUD eGIS Difficult_Development_Areas_2026)",
           "nmtc": "CDFI Fund NMTC LIC tracts 2016-20 ACS (HUD eGIS NMTC20)",
           "usda_rural_eligible": "USDA RD Eligibility MapServer layer 4 (RHS SFH MFH ineligible areas)",
           "hubzone": "SBA HUBZone - not queried",
           "lihtc_within_400m": "HUD LIHTC database (HUD eGIS LIHTC)"}
    inc_list = []
    lih = res.get("lihtc")
    for i, h in enumerate(houses):
        x, y = h.get("lon"), h.get("lat")
        has = x is not None and y is not None
        inc = {}
        if not has:
            inc = {"qct": None, "dda": None, "nmtc": None, "usda_rural_eligible": None, "hubzone": None,
                   "lihtc_within_400m": None, "note": "house has no lon/lat"}
        else:
            q = _hit(res.get("qct"), x, y) if st["qct"] == "ok" else None
            inc["qct"] = (q is not None) if st["qct"] == "ok" else None
            inc["qct_tract"] = q.get("GEOID") if q else None
            d = _hit(res.get("dda"), x, y) if st["dda"] == "ok" else None
            inc["dda"] = (d is not None) if st["dda"] == "ok" else None
            inc["dda_area"] = {"code": d.get("DDA_CODE"), "name": d.get("DDA_NAME"), "type": d.get("DDA_TYPE"),
                               "zcta5": d.get("ZCTA5")} if d else None
            if st["nmtc"] == "ok":
                t = _hit(res["nmtc"], x, y)
                inc["nmtc"] = bool(t and t.get("NMTC_LIC_INC") == 1)
                inc["nmtc_tract"] = {"geoid": t.get("GEOID"), "metro": t.get("METRO"),
                                     "poverty_rate": t.get("POV_RATE_16_20_ACS"),
                                     "mfi_ratio": None if t.get("MFI_BENCH_16_20_ACS") is None
                                     else round(t["MFI_BENCH_16_20_ACS"], 3),
                                     "unemployment_rate": t.get("UNEMPLOY_RATE_16_20_ACS"),
                                     "severe_distress": t.get("SEVERE_DISTRESS") == 1,
                                     "deep_distress": t.get("DEEP_DISTRESS") == 1} if t else None
            else:
                inc["nmtc"] = None
            if st["usda_rd"] == "ok" and i in res["usda_rd"]["inel"]:
                inc["usda_rural_eligible"] = not res["usda_rd"]["inel"][i]
            else:
                inc["usda_rural_eligible"] = None
            inc["hubzone"] = None
            if st["lihtc"] == "ok":
                ds = sorted((hav_m(x, y, p["lon"], p["lat"]), p["hud_id"]) for p in lih)
                near = [(dd, hid) for dd, hid in ds if dd <= LIHTC_RADIUS_M]
                inc["lihtc_within_400m"] = len(near)
                inc["lihtc_ids_400m"] = [hid for _, hid in near]
                inc["lihtc_nearest_m"] = round(ds[0][0]) if ds else None
            else:
                inc["lihtc_within_400m"] = None
        inc["sources"] = src
        inc_list.append(inc)

    def cnt(k):
        return sum(1 for v in inc_list if v.get(k) is True)
    near_ids = {hid for v in inc_list for hid in (v.get("lihtc_ids_400m") or [])}

    # rent
    rent = None
    fm = res.get("fmr")
    sf = res.get("safmr")
    if fm or sf:
        z = res.get("zip")
        rows = (sf or {}).get("rows", [])
        def pick(zz):
            rr = [r for r in rows if r.get("ID") == zz]
            if fm:
                m = [r for r in rr if r.get("HUD_CODE") == fm["code"]]
                rr = m or rr
            return rr[0] if rr else None
        r0 = pick(z) if z else None
        des = {d["ZCTA5"]: d for d in (sf or {}).get("designated", [])}
        rent = {"zip": z, "zip_basis": res.get("zip_basis"), "year": vint["fy"], "year_basis": vint["fy_basis"],
                "safmr": ({f"br{i}": (int(r0[f"SAFMR_{i}BR"]) if r0.get(f"SAFMR_{i}BR") is not None else None)
                           for i in range(5)} if r0 else None),
                "safmr_area": {"hud_code": r0["HUD_CODE"], "name": r0["FMR_NAME"]} if r0 else None,
                "safmr_mandatory": bool(z in des),
                "safmr_mandatory_since": des[z]["Year_Designated"] if z in des else None,
                "safmr_designation_layer": ({f"br{i}": des[z].get(f"SAFMR_{i}BR") for i in range(5)}
                                            if z in des else None),
                "safmr_designation_layer_note": "values carried by HUD eGIS SAFMR_Year (mandatory-area layer); "
                                                "vintage not stated and may differ from the SAFMR table, which is "
                                                "used as the primary figure",
                "fmr_area": fm,
                "units": "USD per month, gross rent (rent + tenant-paid utilities), 40th percentile",
                "source": "HUD Small Area FMR (HUD eGIS HUD_PDR_Small_Area_Fair_Market_Rents table 1, SAFMR_Year "
                          "layer 2) and FMR (HUD eGIS Fair_Market_Rents layer 0)",
                "layer_updated": {"safmr": vint.get("safmr_edit"), "fmr": vint.get("fmr_edit")}}
        others = [zz for zz in sorted({str(h.get("zip")) for h in houses if h.get("zip")}) if zz != z]
        if others:
            rent["safmr_other_zips"] = {zz: ({f"br{i}": pick(zz).get(f"SAFMR_{i}BR") for i in range(5)}
                                             if pick(zz) else None) for zz in others}
        if not r0:
            rent["safmr_note"] = f"ZIP {z} has no SAFMR row; use fmr_area"

    hpi = None
    if res.get("fhfa_hpi_tract") is not None or res.get("fhfa_hpi_zip5") is not None:
        zz = res.get("fhfa_hpi_zip5") or {}
        z0 = res.get("zip")
        hpi = {"tract": meta.get("tract"), "tract_hpi": res.get("fhfa_hpi_tract"),
               "zip": z0, "zip_basis": res.get("zip_basis"),
               "zip5_hpi": zz.get(z0) if z0 else None,
               **({"zip5_other": {k: v for k, v in zz.items() if k != z0}} if len(zz) > 1 else {}),
               "index": "FHFA annual all-transactions HPI (developmental), not seasonally adjusted",
               "source": {"tract": FHFA_TRACT, "zip5": FHFA_ZIP5},
               "zip5_last_updated": _TABLES.get("zip5_updated")}

    bps = None
    if res.get("bps_county") or res.get("bps_place"):
        bps = {"county": res.get("bps_county"), "place": res.get("bps_place"),
               "basis": "new privately-owned residential units authorized, annual, imputed totals "
                        "(includes estimates for non-reporting permit offices)",
               "source": "Census Bureau Building Permits Survey"}

    lihtc_meta = None
    if lih is not None:
        lihtc_meta = [dict(p, within_400m_of_house=p["hud_id"] in near_ids) for p in lih]

    ff = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "status": st,
          "counts": {"houses": len(houses), "qct": cnt("qct"), "dda": cnt("dda"), "nmtc": cnt("nmtc"),
                     "usda_rural_eligible": cnt("usda_rural_eligible"),
                     "usda_ineligible": sum(1 for v in inc_list if v.get("usda_rural_eligible") is False),
                     "with_lihtc_within_400m": sum(1 for v in inc_list if (v.get("lihtc_within_400m") or 0) > 0)},
          "usda_method": (res.get("usda_rd") or {}).get("mode"),
          "hubzone": {"value": None, "reason": HUBZONE_REASON},
          "lihtc_nearby": lihtc_meta,
          "lihtc_search": f"properties within {LIHTC_RADIUS_M + 100} m of the block bbox; per-house count uses "
                          f"great-circle distance <= {LIHTC_RADIUS_M} m",
          "vintages": vint["notes"],
          "method": "point-in-polygon of house lon/lat (shapely covers) against layer polygons fetched for the "
                    "block bbox; USDA via server-side point/bbox queries",
          "attribution": ATTRIBUTION, "licence": LICENCE,
          "research": "usa/research/federal_realestate.json (commercial_ok yes: hud_qct, hud_dda, hud_nmtc, "
                      "hud_lihtc, hud_fmr, hud_safmr, usda_elig_svc, fhfa_hpi_tract, fhfa_hpi_zip5, census_bps)"}
    return inc_list, rent, hpi, bps, ff


# ---------------------------------------------------------------- locking + write
def _lock(B):
    p = B / ".lock"
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"federal_flags.py pid {os.getpid()} {time.time()}".encode())
            os.close(fd)
            return p
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > LOCK_STALE_S:
                    print(f"  removing stale lock {p}")
                    p.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > LOCK_WAIT_S:
                raise TimeoutError(f"lock {p} held for > {LOCK_WAIT_S}s")
            time.sleep(1)


def write_block(B, inc_by_id, inc_by_idx, rent, hpi, bps, ff):
    lk = _lock(B)
    try:
        meta = json.load(open(B / "meta.json", encoding="utf-8"))
        houses = json.load(open(B / "houses.json", encoding="utf-8"))
        for i, h in enumerate(houses):
            v = inc_by_id.get(h.get("id")) if h.get("id") is not None else None
            if v is None and i < len(inc_by_idx):
                v = inc_by_idx[i]
            if v is not None:
                h["incentives"] = v
        if rent is not None:
            meta["rent"] = rent
        if hpi is not None:
            meta["fhfa_hpi"] = hpi
        if bps is not None:
            meta["permits_bps"] = bps
        meta["federal_flags"] = ff
        tmp_h, tmp_m = B / "houses.json.ff.tmp", B / "meta.json.ff.tmp"
        with open(tmp_h, "w", encoding="utf-8") as f:
            json.dump(houses, f, separators=(",", ":"))
        with open(tmp_m, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp_h, B / "houses.json")
        os.replace(tmp_m, B / "meta.json")
    finally:
        try:
            lk.unlink()
        except FileNotFoundError:
            pass


_VINT = {}


def vintages():
    if not _VINT:
        ed = {k: layer_edit_date(u) for k, u in [("qct", L_QCT), ("dda", L_DDA), ("nmtc", L_NMTC), ("lihtc", L_LIHTC),
                                                 ("fmr", L_FMR), ("safmr", HUD + "HUD_PDR_Small_Area_Fair_Market_Rents/FeatureServer/0")]}
        fy, basis = None, "unknown"
        if ed.get("fmr"):
            y, m = int(ed["fmr"][:4]), int(ed["fmr"][5:7])
            fy = y + 1 if m >= 9 else y
            basis = (f"inferred: HUD FMR layer data last edited {ed['fmr']}; FMRs take effect Oct 1 for the next "
                     f"federal fiscal year (layer description does not state the FY)")
        _VINT.update(fy=fy, fy_basis=basis, safmr_edit=ed.get("safmr"), fmr_edit=ed.get("fmr"), notes={
            "qct": f"service QUALIFIED_CENSUS_TRACTS_2026 (data edited {ed.get('qct')}; layer description text still says FY2024)",
            "dda": f"service Difficult_Development_Areas_2026 (data edited {ed.get('dda')}; metro DDAs by ZCTA, non-metro by county)",
            "nmtc": f"2016-2020 ACS eligibility, 2010 tracts (data edited {ed.get('nmtc')})",
            "lihtc": f"HUD LIHTC database (data edited {ed.get('lihtc')})",
            "fmr": f"FY{fy} (inferred, data edited {ed.get('fmr')})",
            "safmr": f"FY{fy} values (inferred, data edited {ed.get('safmr')}); mandatory-area list from SAFMR_Year",
            "usda_rd": "current ineligible areas (USDA: 'updated often')",
            "fhfa_hpi": "annual; tract file uses 2010 census tracts",
            "bps": "latest annual file listed on www2.census.gov/econ/bps"})
    return _VINT


def process(slug):
    B = BLOCKS / slug
    if not (B / "meta.json").exists() or not (B / "houses.json").exists():
        print(f"{slug}: SKIP (no meta.json/houses.json)")
        return None
    meta = json.load(open(B / "meta.json", encoding="utf-8"))
    if not meta.get("files"):
        print(f"{slug}: SKIP (meta.json has no 'files' yet - block still building)")
        return None
    houses = json.load(open(B / "houses.json", encoding="utf-8"))
    vint = vintages()
    st, res = fetch_block(meta, houses)
    inc, rent, hpi, bps, ff = build(meta, houses, st, res, vint)
    by_id = {h.get("id"): v for h, v in zip(houses, inc) if h.get("id") is not None}
    write_block(B, by_id, inc, rent, hpi, bps, ff)
    print(f"== {slug}: {len(houses)} houses")
    for k, v in st.items():
        tag = "ok  " if v == "ok" else ("n/a " if v.startswith("not available") else "FAIL")
        print(f"  {tag} {k}" + ("" if v == "ok" else f"  {v[:140]}"))
    c = ff["counts"]
    print(f"  qct {c['qct']}  dda {c['dda']}  nmtc {c['nmtc']}  usda-eligible {c['usda_rural_eligible']} "
          f"(ineligible {c['usda_ineligible']})  houses w/ LIHTC<=400m {c['with_lihtc_within_400m']}  "
          f"LIHTC nearby {len(ff['lihtc_nearby'] or [])}")
    if rent:
        print(f"  rent zip {rent['zip']} FY{rent['year']} safmr {rent['safmr']} fmr 2BR "
              f"{(rent['fmr_area'] or {}).get('br2')} ({(rent['fmr_area'] or {}).get('name')})")
    if hpi:
        th, zh = hpi.get("tract_hpi") or {}, hpi.get("zip5_hpi") or {}
        print(f"  HPI tract {th.get('year')} {th.get('hpi')} 1y {th.get('change_1yr_pct')} 5y {th.get('change_5yr_pct')}"
              f"{'  ' + th['missing'][:60] if th.get('missing') else ''} | zip {zh.get('year')} {zh.get('hpi')} "
              f"1y {zh.get('change_1yr_pct')} 5y {zh.get('change_5yr_pct')}")
    if bps:
        co, pl = bps.get("county") or {}, bps.get("place") or {}
        print(f"  BPS county {co.get('year')} {co.get('name')} units {co.get('total_units')} | place "
              f"{pl.get('name') or pl.get('missing')} units {pl.get('total_units')}")
    return st


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    slugs = sorted(p.name for p in BLOCKS.iterdir() if p.is_dir() and not p.name.startswith("_")
                   and (p / "meta.json").exists()) if argv == ["--all"] else argv
    summary = {}
    for s in slugs:
        try:
            summary[s] = process(s)
        except Exception as ex:  # noqa: BLE001
            print(f"{s}: FAIL {type(ex).__name__}: {ex}")
            summary[s] = {"_block": f"error: {ex}"}
    print("\nsummary:")
    for s, st in summary.items():
        if st is None:
            print(f"  {s}: skipped")
        else:
            bad = [k for k, v in st.items() if v != "ok" and k != "hubzone"]
            print(f"  {s}: " + ("all ok" if not bad else "FAIL " + ", ".join(bad)) + " (hubzone n/a)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

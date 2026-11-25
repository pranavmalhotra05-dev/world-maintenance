"""Rental-assistance flags (Housing Choice Voucher readiness) for built 3D blocks.

  .venv\\Scripts\\python -u -W ignore usa/assistance_flags.py <slug> [<slug> ...]
  .venv\\Scripts\\python -u -W ignore usa/assistance_flags.py --all

Re-runnable: every run recomputes and overwrites only h.assistance (houses.json) and meta.assistance (meta.json).
Garages get no h.assistance (a stale one is removed). No tenant data is read or written anywhere.

Sources (US federal works, 17 U.S.C. 105; see usa/research/rental_assistance_federal.json):
  meta.rent (SAFMR / FMR dollars + SAFMR-mandatory flag) written by usa/federal_flags.py
  HUD eGIS: Estimated Housing Authority Service Areas (layer 15), Public Housing Authorities (inventory),
            Housing Choice Vouchers by Tract, Continuum of Care Grantee Areas, Multifamily Properties - Assisted
  HUD Office of Multifamily Housing: Multifamily Assistance & Section 8 Contracts xlsx (property-level contracts)
  HUD PD&R: Picture of Subsidized Households 2025 (tract files, 2020 census geography)
  DoD DTMO: BAH ASCII rate file (ZIP -> Military Housing Area -> monthly BAH)
Optional: usa/research/rental_assistance_local.json (another lane) -> per-city PHA payment standards and landlord
incentive / guarantee programs. Missing file -> skipped; run again once it exists.

All network fetching happens before the per-block lock (usa.blocklock); inside the lock both files are re-read,
only our keys are set, and both are written via tmp + os.replace.
"""
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "assistance"
LOCAL_JSON = ROOT / "usa" / "research" / "rental_assistance_local.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) roofprint-usa/0.1 (open-data research)"
S = requests.Session()
S.headers["User-Agent"] = UA

HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services/"
L_PHA_SA = HUD + "Proposed_Housing_Authority_Service_Areas/FeatureServer/15"
L_PHA = HUD + "Public_Housing_Authorities/FeatureServer/0"
L_HCV = HUD + "Housing_Choice_Vouchers_by_Tract/FeatureServer/0"
L_COC = HUD + "Continuum_of_Care_Grantee_Areas/FeatureServer/0"
L_MF = HUD + "MULTIFAMILY_PROPERTIES_ASSISTED/FeatureServer/0"
L_SAFMR_DESIG = HUD + "SAFMR_Year/FeatureServer/2"
MF_CONTRACTS = "https://www.hud.gov/sites/dfiles/Housing/documents/MF-Assistance-Sec8-Contracts1.xlsx"
MF_PAGE = "https://www.hud.gov/hud-partners/multifamily-assist-section8-database"
PIC_BASE = "https://www.huduser.gov/portal/datasets/pictures/files/"
PIC_FILES = ["TRACT_AK_MN_2025_2020census.xlsx", "TRACT_MO_WY_2025_2020census.xlsx"]
PIC_PAGE = "https://www.huduser.gov/portal/datasets/assthsg.html"
BAH_YEAR = 2026
BAH_URL = (f"https://www.travel.dod.mil/Portals/119/Documents/BAH/BAH_Rates_All_Locations_All_Pay_Grades/ASCII/"
           f"BAH-ASCII-{BAH_YEAR}.zip")
BAH_PAGE = "https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Lookup/"
MF_RADIUS_M = 800

R = {  # rule URLs
    "ps": "https://www.ecfr.gov/current/title-24/part-982/section-982.503",
    "unit_size": "https://www.ecfr.gov/current/title-24/part-982/section-982.402",
    "rent_reason": "https://www.ecfr.gov/current/title-24/part-982/section-982.507",
    "nspire": "https://www.ecfr.gov/current/title-24/part-5/section-5.703",
    "hqs": "https://www.ecfr.gov/current/title-24/part-982/section-982.401",
    "lead": "https://www.ecfr.gov/current/title-24/part-35/subpart-M",
    "nspire_delay": "https://www.federalregister.gov/documents/2025/09/30/2025-19070/economic-growth-regulatory-relief-and-consumer-protection-act-implementation-of-national-standards",
    "budget2026": "https://www.hud.gov/sites/default/files/PIH/documents/2026-Budget-Management-Letter.pdf",
    "ehv": "https://www.hud.gov/sites/default/files/hudclips/documents/PIH-2026-25.pdf",
    "incentives": "https://www.hud.gov/sites/dfiles/OCHCO/documents/2022-18pihn.pdf",
    "fema_dl": "https://www.fema.gov/blog/direct-lease-and-multi-family-lease-and-repair-programs",
    "nfip": "https://www.fema.gov/flood-insurance",
    "fmr": "https://www.huduser.gov/portal/datasets/fmr.html",
}
HUD_ATTR = ("Source: U.S. Department of Housing and Urban Development (HUD), HUD eGIS open data "
            "(hudgis-hud.opendata.arcgis.com). Not endorsed by HUD.")
ATTRIBUTION = {
    "pha": HUD_ATTR + " Layers: Estimated Housing Authority Service Areas; Public Housing Authorities.",
    "hcv_tract": HUD_ATTR + " Layer: Housing Choice Vouchers by Tract.",
    "picture_2025": "Source: HUD Office of Policy Development and Research, Picture of Subsidized Households 2025 "
                    "(2020 census geography), " + PIC_PAGE,
    "coc": HUD_ATTR + " Layer: Continuum of Care Grantee Areas.",
    "hud_multifamily": "Source: HUD Office of Multifamily Housing, Multifamily Assistance & Section 8 Contracts "
                       "database (" + MF_PAGE + ", 'furnished for the convenience of interested parties', no "
                       "warranty) joined to HUD eGIS Multifamily Properties - Assisted for location.",
    "fmr_safmr": "HUD FMR / Small Area FMR via meta.rent (usa/federal_flags.py, HUD eGIS layers).",
    "bah": "Source: U.S. Department of Defense, Defense Travel Management Office, BAH rate files (" + BAH_PAGE + ").",
    "rules": "Code of Federal Regulations via eCFR (ecfr.gov); HUD notices and letters (hud.gov).",
}
LICENCE = "US federal government works, not subject to copyright (17 U.S.C. 105): https://www.usa.gov/government-copyright"
POLICY_NOTES = [
    {"id": "hud_2026_budget_letter", "date": "2026-02/03",
     "note": "HUD 2026 Budget Management Letter: vouchers are funding-constrained. PHAs asked to stop issuing new "
             "vouchers (except HUD-VASH and FYI), pause new project-based voucher commitments, and cut payment "
             "standards, ending exception payment standards and staying inside the 90-110% basic range. Expect "
             "payment standards toward the low end of the range.", "url": R["budget2026"]},
    {"id": "nspire_delay", "date": "2025-09-30",
     "note": "NSPIRE inspections for vouchers delayed to 2027-02-01 (third extension). Until then PHAs may still "
             "inspect under the old HQS (24 CFR 982.401). The checklist here aims at both.", "url": R["nspire_delay"]},
    {"id": "ehv_sunset", "date": "2026",
     "note": "Emergency Housing Vouchers are ending: families under EHV HAP contracts on 2026-09-30 move to Tenant "
             "Protection Vouchers by 2026-12-31 (PIH 2026-25); closeout from early 2027. For a landlord the lease "
             "continues as an ordinary voucher.", "url": R["ehv"]},
    {"id": "landlord_incentives", "date": "2022",
     "note": "PHAs may use HCV admin fees for landlord signing/retention bonuses, security and utility deposits and "
             "holding fees (PIH 2022-18). Optional and per PHA; 2026 budget pressure makes them scarcer.",
     "url": R["incentives"]},
]


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


def pt_params(lon, lat, **extra):
    return dict(geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", inSR=4326,
                spatialRel="esriSpatialRelIntersects", returnGeometry="false", **extra)


def layer_edit_date(layer):
    try:
        j = _get(layer, params={"f": "json"}).json()
        ms = (j.get("editingInfo") or {}).get("dataLastEditDate")
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else None
    except Exception:  # noqa: BLE001
        return None


def cached(url, name, max_age_days=30, min_bytes=1000):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists() or p.stat().st_size < min_bytes or time.time() - p.stat().st_mtime > max_age_days * 86400:
        print(f"  downloading {url}")
        r = _get(url, timeout=900, stream=True)
        tmp = p.with_suffix(p.suffix + ".part")
        with open(tmp, "wb") as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
        if tmp.stat().st_size < min_bytes:
            raise RuntimeError(f"download too small ({tmp.stat().st_size} bytes): {url}")
        os.replace(tmp, p)
    return p


def hav_m(lon1, lat1, lon2, lat2):
    Rm = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * Rm * math.asin(math.sqrt(a))


def _num(x):
    try:
        v = float(str(x).strip())
        return None if math.isnan(v) else v
    except Exception:  # noqa: BLE001
        return None


def _int(x):
    v = _num(x)
    return None if v is None else int(round(v))


# ---------------------------------------------------------------- national tables (loaded once per run)
_T = {}


def mf_contracts():
    """property_id -> [contract rows] from HUD's monthly MF Assistance & Section 8 Contracts xlsx."""
    if "mf" not in _T:
        import pandas as pd
        p = cached(MF_CONTRACTS, "MF-Assistance-Sec8-Contracts1.xlsx", 30)
        raw = pd.read_excel(p, dtype=str, header=None)
        hdr = next(i for i in range(min(20, len(raw))) if str(raw.iat[i, 0]).strip() == "contract_number")
        d = raw.iloc[hdr + 1:].copy()
        d.columns = [str(c).strip() for c in raw.iloc[hdr]]
        d = d.dropna(subset=["contract_number"])
        out = {}
        for r in d.to_dict("records"):
            pid = str(r.get("property_id") or "").strip()
            if pid.endswith(".0"):
                pid = pid[:-2]
            out.setdefault(pid, []).append(r)
        _T["mf"] = out
        _T["mf_file_date"] = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    return _T["mf"]


PIC_KEEP = ["total_units", "total_occupied", "pct_occupied", "people_per_unit", "people_total", "rent_per_month",
            "spending_per_month", "months_waiting", "months_from_movein", "pct_utility_allow", "ave_util_allow",
            "pct_bed1", "pct_bed2", "pct_bed3", "pct_overhoused", "Quarter"]


def picture_tracts():
    """tract GEOID -> {program_label: {fields}} (sub_program 'N/A' rows only), slim CSV cached after first parse."""
    if "pic" in _T:
        return _T["pic"]
    import csv
    slim = CACHE / "picture2025_tract_programs.csv"
    if not slim.exists():
        import openpyxl
        rows = []
        for fn in PIC_FILES:
            p = cached(PIC_BASE + fn, fn, 365, min_bytes=10_000_000)
            print(f"  parsing {fn} (one-time, slow)")
            wb = openpyxl.load_workbook(p, read_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            hdr = list(next(it))
            ix = {h: i for i, h in enumerate(hdr)}
            for row in it:
                if row[ix["sub_program"]] != "N/A":
                    continue
                rows.append([row[ix["code"]], row[ix["program_label"]]] + [row[ix[k]] for k in PIC_KEEP])
            wb.close()
        tmp = slim.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["code", "program_label"] + PIC_KEEP)
            w.writerows(rows)
        os.replace(tmp, slim)
    out = {}
    with open(slim, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["code"], {})[r["program_label"]] = r
    _T["pic"] = out
    return out


def bah_tables():
    if "bah" not in _T:
        p = cached(BAH_URL, f"BAH-ASCII-{BAH_YEAR}.zip", 365, min_bytes=100_000)
        with open(p, "rb") as f:
            z = zipfile.ZipFile(io.BytesIO(f.read()))   # read in memory only, nothing extracted
        yy = str(BAH_YEAR)[2:]
        txt = lambda n: z.read(n).decode("latin-1").splitlines()  # noqa: E731
        zm = {}
        for line in txt(f"sorted_zipmha{yy}.txt"):
            a = line.split()
            if len(a) >= 2:
                zm[a[0]] = a[1]
        names = dict(l.split(";", 1) for l in txt(f"mhanames{yy}.txt") if ";" in l)
        def rates(n):
            o = {}
            for l in txt(n):
                c = l.strip().split(",")
                if len(c) >= 28:
                    o[c[0]] = [_num(v) for v in c[1:28]]
            return o
        _T["bah"] = {"zip": zm, "names": names, "with": rates(f"bahw{yy}.txt"), "without": rates(f"bahwo{yy}.txt")}
    return _T["bah"]


# ---------------------------------------------------------------- optional local file (other lane)
STATE_NAMES = {"MN": "minnesota", "TX": "texas", "CO": "colorado", "OH": "ohio", "OK": "oklahoma", "MO": "missouri",
               "AL": "alabama"}


def _norm_city(c):
    c = str(c or "").lower().strip()
    c = re.sub(r"^st\.?\s+", "saint ", c)
    c = re.sub(r",?\s+(mn|oh|tx|co|al|mo|ok)$", "", c)
    c = re.sub(r"\s+(city|town|village)$", "", c)
    return re.sub(r"[^a-z ]", "", c).strip()


def _year_of(*txt):
    for t in txt:
        m = re.search(r"(20\d\d)", str(t or ""))
        if m:
            return int(m.group(1))
    return None


def _brmap(d):
    """{'0': 1139, '1': ..., 'SRO': ...} -> {'br0': 1139, ...} (non-numeric keys dropped)."""
    out = {}
    for k, v in (d or {}).items():
        if str(k).strip().isdigit() and _num(v):
            out[f"br{int(k)}"] = int(round(_num(v)))
    return out or None


def load_local():
    """usa/research/rental_assistance_local.json (other lane). Missing/unreadable -> status string, nothing merged."""
    if "local" in _T:
        return _T["local"]
    if not LOCAL_JSON.exists():
        _T["local"] = {"status": f"skipped: {LOCAL_JSON.relative_to(ROOT).as_posix()} not found (re-run when it exists)"}
        return _T["local"]
    try:
        j = json.load(open(LOCAL_JSON, encoding="utf-8"))
        _T["local"] = {"status": "ok", "json": j, "checked": (j.get("meta") or {}).get("checked"),
                       "legend": (j.get("meta") or {}).get("verified_legend")}
    except Exception as e:  # noqa: BLE001
        _T["local"] = {"status": f"error: could not parse {LOCAL_JSON.name} (may still be being written): {e}"}
    return _T["local"]


def _soi_protected(e):
    t = str(e.get("status") or "").strip().upper()
    return True if t.startswith("YES") else False if t.startswith("NO") else "unclear"


def local_for_block(meta):
    """-> (status, payment-standard entry or None, [programs], source_of_income or None)."""
    L = load_local()
    if L["status"] != "ok":
        return L["status"], None, [], None
    j = L["json"]
    city, st = _norm_city(meta.get("city")), str(meta.get("state") or "").upper()
    stname = STATE_NAMES.get(st, st.lower())
    county = str(meta.get("county") or "").lower().replace(" county", "").strip()

    ps = next((e for e in j.get("payment_standards", []) if _norm_city(e.get("city")) == city), None)

    progs = []
    for e in j.get("programs", []):
        cs = str(e.get("city_state") or "")
        low = cs.lower()
        scope = None
        if _norm_city(cs) == city:
            scope = "city"
        elif "statewide" in low and stname in low:
            scope = "state"
        elif county and low.startswith(county + " county") and low.rstrip().endswith(" " + st.lower()):
            scope = "county"
        if not scope:
            continue
        gets = e.get("landlord_gets")
        amounts = sorted({m.replace(",", "").replace(" ", "") for m in re.findall(r"\$\s?[\d,]+(?:\.\d+)?", str(gets or ""))},
                         key=lambda x: float(re.sub(r"[^\d.]", "", x) or 0))
        progs.append({"name": e.get("program"), "jurisdiction": cs, "scope": scope,
                      "administrator": e.get("administrator"), "landlord_gets": gets, "amounts": amounts,
                      "unit_eligibility": e.get("unit_eligibility"), "how_to_list": e.get("how_to_list"),
                      "claim_url": e.get("claim_url"), "verified": e.get("verified"),
                      "source_url": e.get("source_url"), "checked": e.get("checked")})

    ents = j.get("source_of_income_law", [])

    def names(e):
        return {_norm_city(re.sub(r"\(.*?\)", "", p)) for p in str(e.get("jurisdiction") or "").split("/")}
    city_e = next((e for e in ents if city in names(e)), None)
    state_e = (next((e for e in ents if stname in names(e) and "(state)" in str(e.get("jurisdiction")).lower()), None)
               or next((e for e in ents if stname in names(e)), None))
    main_e = city_e or state_e
    # a city entry that is "unknown" defers to a definite statewide protection
    if city_e and _soi_protected(city_e) == "unclear" and state_e and _soi_protected(state_e) is True:
        main_e = state_e
    soi = None
    if main_e:
        soi = {"law": str(main_e.get("status") or "")[:400], "protected": _soi_protected(main_e),
               "jurisdiction": main_e.get("jurisdiction"), "verified": main_e.get("verified"),
               "source_url": main_e.get("source_url"),
               "meaning": ("protected = a landlord may not refuse a tenant because rent is paid with a voucher; "
                           "false = no protection or state preemption, so accepting vouchers is voluntary")}
        other = state_e if main_e is city_e else city_e
        if other is not None and other is not main_e:
            soi["also"] = {"jurisdiction": other.get("jurisdiction"), "law": str(other.get("status") or "")[:300],
                           "protected": _soi_protected(other), "verified": other.get("verified"),
                           "source_url": other.get("source_url")}
    return "ok", ps, progs, soi


def pha_standard_for(ps, br, zipc, rent, safmr_t):
    """Per-house PHA payment standard from a local entry: ZIP sample > flat schedule > derived % of SAFMR."""
    if not ps:
        return None
    out = {"pha": ps.get("administrator"), "schedule": ps.get("program"), "basis": ps.get("basis"),
           "effective": ps.get("effective"), "year": _year_of(ps.get("effective"), ps.get("program")),
           "by_zip": ps.get("by_zip"), "verified": ps.get("verified"), "source": ps.get("source_url"),
           "source_doc": ps.get("source_doc"), "checked": ps.get("checked"), "notes": ps.get("notes"),
           "from": "usa/research/rental_assistance_local.json", "bedrooms": br, "zip": zipc}
    if br is None:
        out.update(value=None, value_basis="bedroom count unknown")
        return out
    key = str(br)
    for k, v in (ps.get("ps_samples") or {}).items():          # 1) ZIP-specific value
        if zipc and zipc in re.findall(r"\d{5}", str(k)) and isinstance(v, dict):
            val = _num(v.get(key))
            if val is None and br > 4 and _num(v.get("4")):
                val = _num(v["4"]) * (1 + 0.15 * (br - 4))
            if val is not None:
                out.update(value=int(round(val)), value_basis=f"PHA schedule, ZIP {k}", by_bedroom=_brmap(v))
                return out
    flat = _brmap(ps.get("ps"))                                  # 2) flat schedule
    if flat:
        v, basis = flat.get(f"br{br}"), "PHA schedule (single schedule)"
        if v is None and br > 4 and flat.get("br4"):
            v, basis = int(round(flat["br4"] * (1 + 0.15 * (br - 4)))), "PHA 4BR x (1 + 0.15 per extra bedroom)"
        if ps.get("by_zip") == "partial":
            basis += "; ZIP not among the PHA's SAFMR ZIPs captured, so the metro schedule is used (confirm)"
        out.update(value=v, value_basis=basis, by_bedroom=flat)
        return out
    m = re.search(r"(\d{2,3})%\s*(?:of\s*)?(?:FY\d{4}\s*)?SAFMR", f"{ps.get('basis')} {ps.get('program')}", re.I)
    if m and safmr_t:                                            # 3) derived from stated % of SAFMR
        pct = int(m.group(1)) / 100
        base, _ = ceiling_for(br, safmr_t)
        if base:
            out.update(value=int(round(base * pct)), derived=True,
                       value_basis=(f"derived: {int(pct * 100)}% x SAFMR {br}BR ${int(round(base))} for ZIP {zipc} "
                                    f"(SAFMR from meta.rent, FY{(rent or {}).get('year')}); the PHA's own ZIP table "
                                    "was not captured, confirm against the schedule"))
            return out
    s = (ps.get("ps_summary") or {}).get(key)
    out.update(value=None, value_basis=("ZIP not in captured samples; PHA range for this bedroom count " +
                                        json.dumps(s)) if s else "no value captured for this ZIP / bedroom count")
    return out


# ---------------------------------------------------------------- per-block fetch (no lock held)
def fetch_block(meta):
    st, res = {}, {}
    lon0, lat0 = meta["origin"]

    def run(name, fn):
        try:
            res[name] = fn()
            st[name] = "ok"
        except Exception as ex:  # noqa: BLE001
            st[name] = f"error: {type(ex).__name__}: {str(ex)[:200]}"
            res[name] = None

    def phas():
        sa = arc_query(L_PHA_SA, outFields="PARTICIPANT_CODE,FORMAL_PARTICIPANT_NAME,PCT_SHARE,SUMLEV,STUSAB",
                       **pt_params(lon0, lat0)).get("features", [])
        areas = [f["attributes"] for f in sa]
        codes = sorted({a["PARTICIPANT_CODE"] for a in areas if a.get("PARTICIPANT_CODE")})
        inv = {}
        if codes:
            where = "PARTICIPANT_CODE IN (" + ",".join(f"'{c}'" for c in codes) + ")"
            fl = ("PARTICIPANT_CODE,FORMAL_PARTICIPANT_NAME,HA_PHN_NUM,STD_ADDR,STD_CITY,STD_ST,STD_ZIP5,"
                  "SECTION8_UNITS_CNT,HA_SECTION_8_SIZE_CATEGORY,HA_PROGRAM_TYPE,TOTAL_DWELLING_UNITS,"
                  "SECTION8_OCCUPIED,PHAS_DESIGNATION,SPENDING_PER_MONTH,LAST_UPDT_DTTM")
            for f in arc_query(L_PHA, where=where, outFields=fl, returnGeometry="false").get("features", []):
                inv[f["attributes"]["PARTICIPANT_CODE"]] = f["attributes"]
        return {"areas": areas, "inventory": inv}
    run("pha", phas)

    def hcv():
        f = arc_query(L_HCV, outFields="GEOID,HCV_PUBLIC,HCV_PUBLIC_PCT", **pt_params(lon0, lat0)).get("features", [])
        return f[0]["attributes"] if f else None
    run("hcv_tract", hcv)

    def pic():
        tr = meta.get("tract")
        if not tr:
            raise RuntimeError("block has no tract")
        return {"tract": tr, "programs": picture_tracts().get(str(tr))}
    run("picture_2025", pic)

    def coc():
        f = arc_query(L_COC, outFields="COCNAME,COCNUM,STATE_NAME,RRH_CN_HWAC_TOT,RRH_CN_HWOA,RRH_CN_HWOC,RRH_CN_VET,"
                                       "PSH_CN_HWAC_TOT,PSH_CN_HWOA,PSH_CN_HWOC,PSH_CN_VET,PSH_U_HWOA,PSH_U_HWAC_TOT",
                      **pt_params(lon0, lat0)).get("features", [])
        return f[0]["attributes"] if f else None
    run("coc", coc)

    def mf():
        rad = MF_RADIUS_M + float(meta.get("half_m") or 60)
        fl = ("PROPERTY_ID,PROPERTY_NAME_TEXT,ADDRESS_LINE1_TEXT,PLACED_BASE_CITY_NAME_TEXT,TOTAL_UNIT_COUNT,"
              "TOTAL_ASSISTED_UNIT_COUNT,CLIENT_GROUP_TYPE,PROPERTY_CATEGORY_NAME,REAC_LAST_INSPECTION_SCORE,"
              "REAC_LAST_INSPECTION_DATE,LAT,LON")
        feats = arc_query(L_MF, outFields=fl, distance=rad, units="esriSRUnit_Meter",
                          **pt_params(lon0, lat0)).get("features", [])
        con = mf_contracts()
        out = []
        for f in feats:
            a = f["attributes"]
            if a.get("LAT") is None or a.get("LON") is None:
                continue
            d = hav_m(lon0, lat0, a["LON"], a["LAT"])
            if d > MF_RADIUS_M:
                continue
            pid = str(a.get("PROPERTY_ID") or "").strip()
            cs = []
            for c in con.get(pid, []):
                exp = str(c.get("tracs_overall_expiration_date") or "")[:10] or None
                cs.append({"contract": str(c.get("contract_number") or "").strip(),
                           "program": str(c.get("program_type_name") or "").strip(),
                           "status": str(c.get("tracs_status_name") or "").strip(),
                           "assisted_units": _int(c.get("assisted_units_count")),
                           "expiration": exp,
                           "rent_to_fmr_pct": None if _num(c.get("rent_to_FMR_ratio")) is None
                           else round(_num(c["rent_to_FMR_ratio"]), 1),
                           "bedroom_mix": {k.replace("_count", "").replace("BR", "br").replace("5plusbr", "br5plus"):
                                           _int(c.get(k)) for k in ("0BR_count", "1BR_count", "2BR_count", "3BR_count",
                                                                    "4BR_count", "5plusBR_count")}})
            exps = sorted(c["expiration"] for c in cs if c["expiration"])
            out.append({"property_id": pid, "name": str(a.get("PROPERTY_NAME_TEXT") or "").strip(),
                        "address": str(a.get("ADDRESS_LINE1_TEXT") or "").strip() or None,
                        "city": a.get("PLACED_BASE_CITY_NAME_TEXT"),
                        "units": a.get("TOTAL_UNIT_COUNT"),
                        "assisted_units": (sum(c["assisted_units"] or 0 for c in cs) if cs
                                           else a.get("TOTAL_ASSISTED_UNIT_COUNT")),
                        "client_group": a.get("CLIENT_GROUP_TYPE"), "category": a.get("PROPERTY_CATEGORY_NAME"),
                        "reac_score": a.get("REAC_LAST_INSPECTION_SCORE"),
                        "contract_expiration": exps[0] if exps else None,
                        "contracts": cs,
                        "in_contracts_file": bool(cs),
                        "distance_m": round(d), "lon": round(a["LON"], 6), "lat": round(a["LAT"], 6)})
        out.sort(key=lambda r: r["distance_m"])
        return out
    run("hud_multifamily", mf)

    def bah():
        return bah_tables()
    run("bah", bah)

    rent = meta.get("rent")
    if rent and rent.get("safmr_mandatory") is not None:
        res["safmr_mandatory"] = {"value": bool(rent["safmr_mandatory"]), "since": rent.get("safmr_mandatory_since"),
                                  "basis": "meta.rent (federal_flags.py: HUD eGIS SAFMR_Year layer by ZIP)"}
        st["safmr_mandatory"] = "ok"
    else:
        def sm():
            z = (rent or {}).get("zip") or meta.get("zip")
            if not z:
                raise RuntimeError("no ZIP for SAFMR_Year lookup")
            f = arc_query(L_SAFMR_DESIG, where=f"ZCTA5='{z}'", outFields="ZCTA5,Year_Designated",
                          returnGeometry="false").get("features", [])
            return {"value": bool(f), "since": f[0]["attributes"].get("Year_Designated") if f else None,
                    "basis": f"HUD eGIS SAFMR_Year layer, ZIP {z}"}
        run("safmr_mandatory", sm)
    st["fmr_safmr"] = "ok" if rent else "error: meta.rent missing (run usa/federal_flags.py first)"
    return st, res


# ---------------------------------------------------------------- per-house logic
def stories_of(h):
    sc = h.get("stories_check") or {}
    a = h.get("assessor") or {}
    for v, b in ((sc.get("assessor"), "assessor stories"), (a.get("stories"), "assessor stories"),
                 ((h.get("overture") or {}).get("num_floors"), "Overture num_floors"),
                 (sc.get("lidar_estimate"), "LiDAR height estimate")):
        v = _num(v)
        if v and v > 0:
            return v, b
    return None, None


def bedrooms_of(h):
    """(bedrooms, basis, note). Assessor count preferred; else a conservative LiDAR-footprint estimate."""
    a = h.get("assessor") or {}
    b = _int(a.get("totalbedrooms"))
    if b is not None and b > 0:
        return b, "county assessor", None
    note = "assessor reports 0 bedrooms (treated as missing)" if b == 0 else None
    area = _num(h.get("area_m2"))
    stories, sb = stories_of(h)
    if not area or not stories:
        return None, None, (note + "; " if note else "") + "no footprint area or story count to estimate from"
    if stories >= 4 or area > 400 or area * stories > 600:
        return None, None, (f"footprint {area:.0f} m2 x {stories:g} stories looks multi-unit or non-residential; "
                            f"per-unit bedrooms unknown")
    living = area * min(stories, 3) * 0.8           # 0.8: walls, stairs, unfinished half-storey
    # deliberately low thresholds per US size norms (1BR ~ 55-80 m2, 3BR ~ 110-150 m2), cap 4 when estimated
    est = 1 if living < 60 else 2 if living < 95 else 3 if living < 150 else 4
    if living < 35:
        est = 0
    return est, "estimated from LiDAR footprint & stories", (
        (note + "; " if note else "") +
        f"footprint {area:.0f} m2 x {stories:g} stories ({sb}) x 0.8 = ~{living:.0f} m2 living area; "
        f"conservative, capped at 4")


def ceiling_for(br, table):
    """HUD FMR/SAFMR for a bedroom count: br0..br4 from the table; 5+ = 4BR x (1 + 0.15 per extra bedroom)."""
    if br is None or not table:
        return None, None
    if br <= 4:
        v = table.get(f"br{br}")
        return (_num(v), None) if v is not None else (None, None)
    b4 = _num(table.get("br4"))
    if b4 is None:
        return None, None
    f = 1 + 0.15 * (br - 4)
    return b4 * f, f"{br}BR = 4BR value x {f:.2f} (HUD rule: +15% of the 4BR FMR per bedroom above 4)"


def voucher_ceiling(h, br, rent, safmr_mand, local_ps, local_status):
    if not rent:
        return None
    fy = rent.get("year")
    hz = str(h.get("zip")) if h.get("zip") else None
    z = rent.get("zip")
    saf = rent.get("safmr")
    if hz and hz != z and (rent.get("safmr_other_zips") or {}).get(hz):
        saf, z = rent["safmr_other_zips"][hz], hz
    fmr = rent.get("fmr_area") or {}
    fmr_t = {f"br{i}": fmr.get(f"br{i}") for i in range(5)} if fmr else None
    mand = bool(safmr_mand and safmr_mand.get("value"))
    use_saf = mand and saf
    base, rule5 = ceiling_for(br, saf if use_saf else fmr_t)
    out = {"bedrooms_used": br,
           "what_this_is": ("the range a PHA's voucher payment standard can be set to (90-110% of the "
                            f"{'Small Area FMR for this ZIP' if use_saf else 'metro/county FMR'}). It is a ceiling on "
                            "gross rent (rent + tenant-paid utilities), not a promised rent: the PHA also caps rent at "
                            "local comparable unassisted rents (rent reasonableness) and subtracts tenant-paid "
                            "utilities. The voucher pays for the smaller of the family's voucher size and the unit's "
                            "bedrooms, so extra bedrooms earn nothing."),
           "policy_2026": ("HUD's 2026 Budget Management Letter pushes PHAs to cut payment standards toward the lower "
                           "end of the range and to end exception payment standards; plan on the low figure."),
           "units": "USD per month, gross rent",
           "year": f"FY{fy}" if fy else None,
           "year_note": ("HUD FMR/SAFMR values as stored in meta.rent (HUD eGIS layer vintage). FY2027 values took "
                         "effect 2026-10-01 and may differ: confirm at " + R["fmr"]),
           "rule_url": R["ps"]}
    if base is None:
        out.update({"low": None, "high": None,
                    "basis": "no bedroom count" if br is None else "no FMR/SAFMR value for this bedroom count"})
    else:
        out.update({"low": int(round(base * 0.9)), "mid_100pct": int(round(base)), "high": int(round(base * 1.1)),
                    "basis": (f"{'SAFMR' if use_saf else 'FMR'} {br}BR ${int(round(base))}"
                              f"{' (ZIP ' + str(z) + ', SAFMR mandatory since ' + str(safmr_mand.get('since')) + ')' if use_saf else ' (' + str(fmr.get('name')) + ')'}"
                              " x 0.90 / 1.10"),
                    "base_source": "SAFMR (ZIP)" if use_saf else "FMR (metro/county)"})
        if rule5:
            out["five_plus_rule"] = rule5
        if not use_saf and saf:
            sb, _ = ceiling_for(br, saf)
            if sb:
                out["if_pha_uses_safmr"] = {
                    "low": int(round(sb * 0.9)), "high": int(round(sb * 1.1)), "zip": z,
                    "note": "only if the PHA opted in to Small Area FMRs, or (non-SAFMR PHAs) set a ZIP exception "
                            "standard of up to 110% of SAFMR under 982.503(d)(2); this ZIP is not SAFMR-mandatory"}
    if local_ps:
        out["pha_payment_standard"] = pha_standard_for(local_ps, br, z, rent, saf)
        if out["pha_payment_standard"] and out["pha_payment_standard"].get("value") is None:
            out["pha_payment_standard_note"] = out["pha_payment_standard"].get("value_basis")
    else:
        out["pha_payment_standard"] = None
        out["pha_payment_standard_note"] = (local_status if local_status != "ok" else
                                            "no published PHA payment standard captured for this city yet")
    return out


def flood_item(fz):
    if not fz:
        return "unknown", "no FEMA flood zone on record for this house"
    z = str(fz).strip().upper()
    if re.match(r"^(A|V)", z):
        return "check", (f"FEMA zone {fz} is a Special Flood Hazard Area: flood insurance is required for federally "
                         "backed mortgages, voucher inspection still needs a safe, dry unit, and FEMA Direct Lease "
                         "(disaster housing) will not lease homes inside the SFHA")
    if "0.2" in z:
        return "likely ok", (f"FEMA zone {fz}: outside the SFHA but moderate (500-year) flood hazard; flood insurance "
                             "optional but worth pricing")
    return "likely ok", f"FEMA zone {fz}: outside the Special Flood Hazard Area"


def condition_item(h):
    hints, good = [], []
    rd = h.get("roof_dev") or {}
    sp, p95 = _num(rd.get("spread_cm")), _num(rd.get("p95_cm"))
    if sp is not None:
        if sp > 17.5:
            hints.append(f"LiDAR roof surface spread {sp:g} cm (p95 {p95:g} cm) is in the roughest 10% of our blocks: "
                         "possible sagging/uneven roof, or dormers/trees; look before listing")
        else:
            good.append(f"roof surface spread {sp:g} cm")
    loc = h.get("local") or {}
    for k, label in (("vacant_or_condemned", "city vacant/condemned registry"),
                     ("dangerous_building", "city dangerous-building list")):
        if loc.get(k):
            hints.append(f"listed on {label}: {json.dumps(loc[k], default=str)[:160]}")
    cc = loc.get("code_cases_5y")
    if isinstance(cc, (int, float)) and cc > 0:
        hints.append(f"{int(cc)} city code-enforcement case(s) in the last 5 years")
    elif cc == 0:
        good.append("no city code cases in 5 years")
    n311 = sum(1 for c in (h.get("cases311") or []) if c.get("house") == h.get("id")
               and re.search(r"code|housing|property|vacant|unsafe|rodent|trash", str(c.get("title") or c.get("type")), re.I))
    if n311:
        hints.append(f"{n311} code/housing-related 311 request(s) attached to this house")
    if h.get("roof_permit"):
        good.append(f"roof permit {h['roof_permit']}")
    if hints:
        return "check", "; ".join(hints)
    if cc == 0 or loc.get("rental_license"):
        return "likely ok", ("no exterior or city-record warning signs (" + ", ".join(good) +
                             "); interior condition still needs the PHA inspection")
    return "unknown", ("no warning signs in the exterior data we have (" + (", ".join(good) or "none") +
                       "), but no city code-case data for this house; interior condition unknown")


def house_assistance(h, rent, safmr_mand, local, bah, bah_ok):
    br, basis, br_note = bedrooms_of(h)
    a = h.get("assessor") or {}
    yb = _int(a.get("yearbuilt")) or _int(h.get("year_built"))
    yb_src = "county assessor" if _int(a.get("yearbuilt")) else ("h.year_built" if yb else None)
    lead = None if not yb else yb < 1978
    stories, _ = stories_of(h)
    vc = voucher_ceiling(h, br, rent, safmr_mand, local[1], local[0])
    money = h.get("money") or {}
    val = _num(money.get("est_value_now") or h.get("value"))

    ck = []
    if br is None:
        ck.append({"item": "Bedrooms", "status": "unknown", "why": br_note or "bedroom count unknown",
                   "rule_url": R["unit_size"]})
    else:
        ck.append({"item": "Bedrooms", "status": "likely ok" if basis == "county assessor" else "check",
                   "why": (f"{br} bedroom(s) ({basis}). PHAs size vouchers at about 2 persons per bedroom; a "
                           "bedroom must be a sleeping room with a window (HQS) and usually an openable egress "
                           "window and a door. Extra bedrooms beyond the voucher size earn no extra subsidy." +
                           (f" {br_note}." if br_note and basis != "county assessor" else "")),
                   "rule_url": R["unit_size"]})
    if vc and vc.get("low"):
        ctx = ""
        if val:
            ctx = (f" Context only: at the low ceiling, 12 months of gross rent = {vc['low'] * 12 / val * 100:.1f}% "
                   f"of the ${int(val):,} estimated value ({money.get('value_basis') or money.get('basis') or 'h.money'}).")
        ck.append({"item": "Rent vs voucher ceiling", "status": "check",
                   "why": (f"Gross rent (rent + tenant-paid utilities) must be at or below the PHA payment standard, "
                           f"about ${vc['low']:,}-${vc['high']:,}/month here for {br}BR, and no higher than comparable "
                           f"unassisted rents." +
                           (f" Published PHA standard: ${vc['pha_payment_standard']['value']:,} "
                            f"({vc['pha_payment_standard'].get('pha')}, {vc['pha_payment_standard'].get('value_basis')})."
                            if (vc.get("pha_payment_standard") or {}).get("value") else "") +
                           " We do not estimate a rent for this house." + ctx),
                   "rule_url": R["rent_reason"]})
    else:
        ck.append({"item": "Rent vs voucher ceiling", "status": "unknown",
                   "why": "no ceiling computed (bedrooms or FMR missing); compare gross rent with the PHA schedule",
                   "rule_url": R["ps"]})
    if lead is None:
        ck.append({"item": "Year built / lead paint", "status": "unknown",
                   "why": "year built unknown; if before 1978 and a child under 6 will live there the Lead-Safe "
                          "Housing Rule applies", "rule_url": R["lead"]})
    elif lead:
        ck.append({"item": "Year built / lead paint", "status": "check",
                   "why": (f"built {yb} ({yb_src}), before 1978: if a child under 6 will live there the PHA does a "
                           "visual assessment of painted surfaces; deteriorated paint must be stabilised and pass "
                           "clearance before move-in (24 CFR 35.1215). 0-bedroom and elderly-only units are exempt "
                           "unless a child under 6 lives there."), "rule_url": R["lead"]})
    else:
        ck.append({"item": "Year built / lead paint", "status": "likely ok",
                   "why": f"built {yb} ({yb_src}), 1978 or later: Lead-Safe Housing Rule does not apply",
                   "rule_url": R["lead"]})
    ck.append({"item": "Smoke and CO alarms", "status": "check",
               "why": ("NSPIRE: smoke alarm on every level, inside each bedroom and within 21 ft of each bedroom "
                       "door; carbon-monoxide alarms where there are fuel-burning appliances or an attached garage. "
                       "Not visible in any dataset."), "rule_url": R["nspire"]})
    ck.append({"item": "Heat", "status": "check",
               "why": ("a permanently installed, working heat source (no unvented space heaters) is required in "
                       "HUD-designated climate zones; also hot and cold running water. Not visible in our data."),
               "rule_url": R["nspire"]})
    ck.append({"item": "Egress and windows", "status": "check",
               "why": ("safe path from the unit door to the street; living room and each sleeping room need a "
                       "window (HQS), and most PHAs want bedroom windows that open for escape" +
                       ("; upper-floor bedrooms: check window size and guardrails on drops of 30 in or more"
                        if stories and stories >= 1.5 else "") + "."), "rule_url": R["hqs"]})
    fs, fw = flood_item(h.get("flood_zone"))
    ck.append({"item": "Flood zone", "status": fs, "why": fw, "rule_url": R["fema_dl"] if fs != "check" else R["nfip"]})
    cs, cw = condition_item(h)
    ck.append({"item": "Condition hints", "status": cs, "why": cw, "rule_url": R["nspire"]})

    bah_b = None
    if bah_ok:
        z = str(h.get("zip") or (rent or {}).get("zip") or "")
        mha = bah["zip"].get(z)
        if mha and not mha.startswith("XX") and mha in bah["with"]:
            w, wo = bah["with"][mha], bah["without"].get(mha) or [None] * 27
            bah_b = {"zip": z, "mha": mha, "mha_name": bah["names"].get(mha), "year": BAH_YEAR,
                     "e5_with_dependents": w[4], "e5_without_dependents": wo[4],
                     "o3_with_dependents": w[19], "units": "USD per month",
                     "note": ("BAH is paid to the service member, not the landlord: a rent benchmark for military "
                              "tenants, not a guarantee. E-5 with dependents is the common benchmark."),
                     "source": BAH_URL}
        else:
            bah_b = None
    return {
        "bedrooms": br, "bedrooms_basis": basis, **({"bedrooms_note": br_note} if br_note else {}),
        "voucher_rent_ceiling": vc,
        "lead_rule_applies_if_child_under_6": lead,
        "year_built": yb, "year_built_basis": yb_src,
        "occupancy_by_bedrooms": (f"up to {max(2, 2 * br)} persons at 2 per bedroom (typical PHA subsidy standard)"
                                  if br is not None else "unknown (bedroom count unknown)"),
        "checklist": ck,
        "bah_benchmark": bah_b,
        **({} if bah_b or not bah_ok else {"bah_note": "no DoD Military Housing Area for this ZIP"}),
        "sources": {"bedrooms": "county assessor (h.assessor.totalbedrooms) or LiDAR footprint (h.area_m2) x stories "
                                "(h.stories_check / h.overture.num_floors)",
                    "ceiling": "meta.rent HUD FMR/SAFMR (federal_flags.py) x 0.9/1.1, 24 CFR 982.503",
                    "pha_payment_standard": "usa/research/rental_assistance_local.json" if local[1] else None,
                    "source_of_income": "usa/research/rental_assistance_local.json (meta.assistance.source_of_income)" if local[3] else None,
                    "lead": "24 CFR 35 subpart M; year built from assessor",
                    "flood": "h.flood_zone (FEMA NFHL)",
                    "condition": "h.roof_dev (LiDAR), h.local (city records), h.cases311",
                    "bah": "DoD DTMO BAH 2026 ASCII" if bah_b else None,
                    "licence": LICENCE},
    }


# ---------------------------------------------------------------- block meta
def _pic_prog(p):
    if not p:
        return None
    def v(k):
        x = _num(p.get(k))
        return None if x is None or x < 0 else (int(x) if float(x).is_integer() else round(x, 1))
    return {"units": v("total_units"), "pct_occupied": v("pct_occupied"), "people_total": v("people_total"),
            "avg_hud_spending_per_unit_month": v("spending_per_month"),
            "avg_household_rent_contribution_month": v("rent_per_month"),
            "avg_utility_allowance": v("ave_util_allow"), "months_waiting": v("months_waiting"),
            "months_since_move_in": v("months_from_movein"),
            "pct_units_1br": v("pct_bed1"), "pct_units_2br": v("pct_bed2"), "pct_units_3br_plus": v("pct_bed3"),
            "as_of": p.get("Quarter")}


def _schedule_summary(ps):
    if not ps:
        return None
    return {k: ps.get(k) for k in ("administrator", "program", "basis", "effective", "by_zip", "ps", "ps_summary",
                                   "ps_samples", "prior_year", "notes", "verified", "checked", "source_url",
                                   "source_doc") if ps.get(k) is not None} | {
        "year": _year_of(ps.get("effective"), ps.get("program")), "from": "usa/research/rental_assistance_local.json"}


def _local_policy_notes(ps):
    if not ps:
        return []
    txt = f"{ps.get('program')} {ps.get('basis')} {ps.get('effective')}"
    if re.search(r"cut|reduc|lower|below", f"{txt} {ps.get('notes')}", re.I):
        return [{"id": "pha_payment_standard_cut", "date": ps.get("effective"),
                 "note": f"{ps.get('administrator')}: {ps.get('program')} ({ps.get('basis')}; effective "
                         f"{ps.get('effective')})." + (f" {ps.get('notes')}" if ps.get("notes") else ""),
                 "verified": ps.get("verified"), "url": ps.get("source_url")}]
    return []


def build_meta(meta, st, res, local, vint):
    phas = []
    if res.get("pha"):
        inv = res["pha"]["inventory"]
        for a in res["pha"]["areas"]:
            c = a.get("PARTICIPANT_CODE")
            i = inv.get(c) or {}
            phas.append({"name": (i.get("FORMAL_PARTICIPANT_NAME") or a.get("FORMAL_PARTICIPANT_NAME") or "").strip(),
                         "code": c, "phone": i.get("HA_PHN_NUM"),
                         "office_address": ", ".join(str(x).strip() for x in (i.get("STD_ADDR"), i.get("STD_CITY"),
                                                                             i.get("STD_ST"), i.get("STD_ZIP5")) if x) or None,
                         "website": None,
                         "section8_units": i.get("SECTION8_UNITS_CNT"),
                         "section8_occupied": i.get("SECTION8_OCCUPIED"),
                         "public_housing_units": i.get("TOTAL_DWELLING_UNITS"),
                         "program_type": i.get("HA_PROGRAM_TYPE"),
                         "section8_size": i.get("HA_SECTION_8_SIZE_CATEGORY"),
                         "phas_designation": i.get("PHAS_DESIGNATION"),
                         "service_area_level": {"160": "place", "050": "county", "040": "state"}.get(
                             str(a.get("SUMLEV")), a.get("SUMLEV")),
                         "share_of_pha_units_in_area": None if a.get("PCT_SHARE") is None else round(a["PCT_SHARE"], 3)})
        phas.sort(key=lambda p: -(p["section8_units"] or 0))
    tv = None
    if res.get("hcv_tract") or (res.get("picture_2025") or {}).get("programs"):
        e = res.get("hcv_tract") or {}
        pp = (res.get("picture_2025") or {}).get("programs") or {}
        tv = {"tract": e.get("GEOID") or meta.get("tract"),
              "voucher_households": e.get("HCV_PUBLIC"),
              "pct_of_renter_units": None if e.get("HCV_PUBLIC_PCT") is None else round(e["HCV_PUBLIC_PCT"], 1),
              "egis_basis": "HUD eGIS Housing Choice Vouchers by Tract (HCV_PUBLIC, HCV_PUBLIC_PCT), point at block origin",
              **({"suppressed_note": "HUD leaves the tract count blank when it is small (privacy suppression), so "
                                     "null here means few or no voucher households, not missing data"}
                 if e and e.get("HCV_PUBLIC") is None else {}),
              "picture_2025": {"tract": (res.get("picture_2025") or {}).get("tract"),
                               "housing_choice_vouchers": _pic_prog(pp.get("Housing Choice Vouchers")),
                               "project_based_section8": _pic_prog(pp.get("Project Based Section 8")),
                               "public_housing": _pic_prog(pp.get("Public Housing")),
                               "all_hud_programs": _pic_prog(pp.get("Summary of All HUD Programs")),
                               "note": ("aggregate tract statistics only (no household records); -1/-4 suppressed "
                                        "values shown as null. 'avg_hud_spending_per_unit_month' approximates the "
                                        "typical monthly HAP paid to owners.")} if pp else None}
    coc = None
    if res.get("coc"):
        c = res["coc"]
        coc = {"name": c.get("COCNAME"), "code": c.get("COCNUM"), "state": c.get("STATE_NAME"),
               "beds": {k.lower(): c.get(k) for k in ("RRH_CN_HWAC_TOT", "RRH_CN_HWOA", "RRH_CN_HWOC", "RRH_CN_VET",
                                                       "PSH_CN_HWAC_TOT", "PSH_CN_HWOA", "PSH_CN_HWOC", "PSH_CN_VET")
                        if c.get(k) is not None} or None,
               "why": ("the CoC's rapid re-housing / permanent supportive housing providers lease private units and "
                       "pay owners directly (24 CFR 578.51; ESG rent must not exceed FMR, 576.106)")}
    mf = res.get("hud_multifamily")
    if mf is not None:
        soon = (date.today().replace(year=date.today().year + 2)).isoformat()
        for p in mf:
            p["expires_within_2y"] = bool(p.get("contract_expiration") and p["contract_expiration"] <= soon)
    lst, lps, progs, soi = local
    sm = res.get("safmr_mandatory")
    return {
        "phas": phas,
        "phas_note": ("PHAs whose estimated service area contains the block (HUD calls these estimates, not legal "
                      "boundaries); each sets its own payment standard. Office phone/address from the HUD PHA "
                      "inventory; the layer has no website field; personal names and emails deliberately omitted."),
        "safmr_mandatory": None if sm is None else sm["value"],
        "safmr_mandatory_since": None if sm is None else sm.get("since"),
        "tract_vouchers": tv,
        "coc": coc,
        "hud_multifamily_nearby": mf,
        "hud_multifamily_note": (f"HUD-assisted multifamily properties within {MF_RADIUS_M} m of the block centre "
                                 "(property-level, no tenant data). Contracts joined by property_id from the "
                                 f"monthly contracts file (cached {_T.get('mf_file_date')}). Expiring contracts can "
                                 "mean an owner opt-out and Tenant Protection Vouchers entering the area."),
        "policy_notes": POLICY_NOTES + _local_policy_notes(lps),
        "local_programs": progs,
        "local_programs_status": lst,
        "source_of_income": soi,
        "pha_payment_standard_schedule": _schedule_summary(lps),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": {**st, "local_json": lst},
        "vintages": vint,
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "privacy": "no tenant or household-level data used; PHA office contacts only; HUD aggregate tract statistics",
        "research": "usa/research/rental_assistance_federal.json",
    }


_VINT = {}


def vintages():
    if not _VINT:
        _VINT.update({k: layer_edit_date(u) for k, u in (("pha_service_areas", L_PHA_SA), ("pha_inventory", L_PHA),
                                                         ("hcv_tract", L_HCV), ("coc", L_COC), ("multifamily", L_MF))})
        _VINT["picture"] = "Picture of Subsidized Households 2025 (as of 31DEC2025 quarter)"
        _VINT["bah"] = f"BAH {BAH_YEAR}"
    return dict(_VINT)


# ---------------------------------------------------------------- write
def write_block(B, per_id, per_idx, ameta):
    with block_lock(B):
        meta = json.load(open(B / "meta.json", encoding="utf-8"))
        houses = json.load(open(B / "houses.json", encoding="utf-8"))
        for i, h in enumerate(houses):
            if h.get("garage"):
                h.pop("assistance", None)
                continue
            v = per_id.get(h.get("id")) if h.get("id") is not None else None
            if v is None and i < len(per_idx):
                v = per_idx[i]
            if v is not None:
                h["assistance"] = v
        meta["assistance"] = ameta
        tmp_h, tmp_m = B / "houses.json.af.tmp", B / "meta.json.af.tmp"
        with open(tmp_h, "w", encoding="utf-8") as f:
            json.dump(houses, f, separators=(",", ":"))
        with open(tmp_m, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp_h, B / "houses.json")
        os.replace(tmp_m, B / "meta.json")


def process(slug):
    B = BLOCKS / slug
    if not (B / "meta.json").exists() or not (B / "houses.json").exists():
        print(f"{slug}: SKIP (no meta.json/houses.json)")
        return None
    meta = json.load(open(B / "meta.json", encoding="utf-8"))
    if not meta.get("files"):
        print(f"{slug}: SKIP (block still building)")
        return None
    houses = json.load(open(B / "houses.json", encoding="utf-8"))
    vint = vintages()
    st, res = fetch_block(meta)
    local = local_for_block(meta)
    rent = meta.get("rent")
    per = [None if h.get("garage") else
           house_assistance(h, rent, res.get("safmr_mandatory"), local, res.get("bah"), st.get("bah") == "ok")
           for h in houses]
    ameta = build_meta(meta, st, res, local, vint)
    by_id = {h.get("id"): v for h, v in zip(houses, per) if h.get("id") is not None and v is not None}
    write_block(B, by_id, per, ameta)

    hs = [p for p in per if p]
    nb = sum(1 for p in hs if p["bedrooms_basis"] == "county assessor")
    ne = sum(1 for p in hs if p["bedrooms_basis"] and p["bedrooms_basis"] != "county assessor")
    nl = sum(1 for p in hs if p["lead_rule_applies_if_child_under_6"])
    ceil = [p["voucher_rent_ceiling"]["low"] for p in hs if (p["voucher_rent_ceiling"] or {}).get("low")]
    print(f"== {slug}: {len(houses)} buildings, {len(hs)} houses (bedrooms assessor {nb}, estimated {ne}, "
          f"unknown {len(hs) - nb - ne}); pre-1978 {nl}; ceiling low ${min(ceil) if ceil else '-'}-"
          f"${max(ceil) if ceil else '-'}")
    for k, v in ameta["status"].items():
        print(f"  {'ok  ' if v == 'ok' else 'NOTE' if str(v).startswith('skipped') else 'FAIL'} {k}"
              + ("" if v == "ok" else f"  {str(v)[:140]}"))
    tv = ameta["tract_vouchers"] or {}
    hcvp = ((tv.get("picture_2025") or {}).get("housing_choice_vouchers") or {})
    print(f"  SAFMR mandatory {ameta['safmr_mandatory']}  PHAs {[p['code'] for p in ameta['phas']]}  "
          f"CoC {(ameta['coc'] or {}).get('code')}  tract HCV {tv.get('voucher_households')} "
          f"({tv.get('pct_of_renter_units')}% renters; Picture units {hcvp.get('units')}, HUD $/unit "
          f"{hcvp.get('avg_hud_spending_per_unit_month')})  MF<=800m {len(ameta['hud_multifamily_nearby'] or [])}  "
          f"local programs {len(ameta['local_programs'])}")
    return ameta["status"]


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
            import traceback
            traceback.print_exc()
            print(f"{s}: FAIL {type(ex).__name__}: {ex}")
            summary[s] = {"_block": f"error: {ex}"}
    print("\nsummary:")
    for s, st in summary.items():
        if st is None:
            print(f"  {s}: skipped")
        else:
            bad = [k for k, v in st.items() if v != "ok" and not str(v).startswith("skipped")]
            print(f"  {s}: " + ("all ok" if not bad else "FAIL " + ", ".join(bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

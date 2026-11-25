"""Pricing intelligence per house (no personal data): last sale, tax, estimated value today,
20%-down monthly payments for 10/15/30-year loans at the current APOR (average prime offer rate), plus ZIP context.

  value today  = county market value 2026 (assessment date Jan 2, 2026) x FHFA ZIP5 annual HPI change 2025 -> latest year
  rates        = FFIEC/CFPB Average Prime Offer Rates (APOR), weekly fixed-rate table, 10-, 15- and 30-yr terms
                 (federal work: CFPB "Information created by the CFPB is in the public domain")
  payment      = principal + interest; plus tax/12 from the county; insurance shown as an estimate range
Owner/taxpayer names in the county layer are never requested.
Rejected (not clear-green): Freddie Mac PMMS, Zillow ZHVI, Realtor.com inventory (see usa/research/licence_registry_national.md).
"""
import io
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json")); W, So, E, N = meta["bounds"]
houses = json.load(open(OUT / "houses.json"))
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1 (open-data research)"


def get(url, **kw):
    for i in range(4):
        try:
            r = S.get(url, timeout=120, **kw); r.raise_for_status(); return r
        except Exception:
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed: {url}")


# ---- county parcels: sale, tax, market value (names NOT requested)
r = get("https://gis.hennepin.us/arcgis/rest/services/HennepinData/LAND_PROPERTY/MapServer/1/query",
        params={"where": "1=1", "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                "outFields": "HOUSE_NO,STREET_NM,SALE_DATE,SALE_PRICE,SALE_CODE_NAME,TAX_TOT,MKT_VAL_TOT,LAND_MV1,BLDG_MV1",
                "returnGeometry": "false", "f": "json", "resultRecordCount": 2000}).json()
rows = {f"{a['HOUSE_NO']} {a['STREET_NM'].strip().title()}": a for a in (f["attributes"] for f in r["features"]) if a.get("HOUSE_NO")}

# ---- rates: FFIEC/CFPB Average Prime Offer Rates (APOR)
APOR_URL = "https://files.ffiec.cfpb.gov/apor/YieldTableFixed.txt"
APOR_LICENCE = ("public domain (US federal work). CFPB Website Privacy Policy and Legal Notices "
                "(https://www.consumerfinance.gov/privacy/website-privacy-policy/): \"Information created by the CFPB is in the public "
                "domain and you may reproduce, publish, or otherwise use it without the Bureau's permission.\"")


def apor_rates(get_):
    """Latest weekly APOR for fixed-rate loans (10-, 15-, 30-yr terms) from the FFIEC/CFPB rate-spread tables.
    Pipe-delimited: 'Term of Loan in Years|1|2|...|50', one row per week (MM/DD/YYYY, effective date)."""
    t = get_(APOR_URL).text.strip().splitlines()
    hdr = [h.strip() for h in t[0].split("|")]
    for line in reversed(t[1:]):
        v = [x.strip() for x in line.split("|")]
        try:
            row = dict(zip(hdr, v)); mo, dd, yy = v[0].split("/")
            return {"date": f"{yy}-{int(mo):02d}-{int(dd):02d}", "r30": float(row["30"]), "r15": float(row["15"]), "r10": float(row["10"]),
                    "label": "APOR (average prime offer rate, used by federal regulators)",
                    "source": "FFIEC / CFPB Average Prime Offer Rates, fixed-rate table (weekly)", "url": APOR_URL, "licence": APOR_LICENCE,
                    "note": "APOR is the benchmark regulators use for HMDA rate spreads and QM/HPML tests; it is an average prime "
                            "offer, not a quote. Computed by the CFPB from a commercial rate survey; only the published APOR values are used."}
        except (KeyError, ValueError):
            continue
    return {}

rates = apor_rates(get)
rates_h = {k: rates[k] for k in ("date", "r10", "r15", "r30", "label", "source") if k in rates}   # compact copy per house

# ---- ZIP value index: FHFA annual ZIP5 HPI (public domain), 2025 -> latest annual figure
FHFA_LICENCE = ("public domain (FHFA). FHFA Copyright and Trademark Notice (https://www.fhfa.gov/about/fhfa-policies/website-privacy-policy): "
                "\"Generally, information and materials produced by Federal agencies, including FHFA, are in the public domain and may be "
                "copied and distributed without permission.\"")


def fhfa_zip(zip5):
    try:
        ex = json.load(open(OUT / "extras.json", encoding="utf-8"))
        d = (ex.get("fhfa_hpi") or {}).get("data") or {}
        if d.get("zip") == zip5 and d.get("series"):
            return d
    except Exception:  # noqa: BLE001
        pass
    import pandas as pd
    raw = pd.read_excel(io.BytesIO(get("https://www.fhfa.gov/hpi/download/annual/hpi_at_zip5.xlsx").content), header=None, dtype=str)
    hr = next(i for i in range(30) if any(str(v).strip().lower() == "year" for v in raw.iloc[i]))
    df = raw.iloc[hr + 1:].copy(); df.columns = [str(c).strip() for c in raw.iloc[hr]]
    zc = next(c for c in df.columns if "zip" in c.lower()); yc = next(c for c in df.columns if c.lower() == "year")
    hc = "HPI" if "HPI" in df.columns else next(c for c in df.columns if c.upper().startswith("HPI"))
    z = df[df[zc].astype(str).str.strip().str.split(".").str[0].str.zfill(5) == zip5]
    ser = sorted([int(float(y)), float(v)] for y, v in zip(z[yc], z[hc]) if str(v) not in ("nan", ".", ""))
    return {"zip": zip5, "series": ser, "latest_year": ser[-1][0] if ser else None}


ZIP = "55407"
idx, index_basis, hpi = 1.0, None, None
try:
    hpi = fhfa_zip(ZIP); ser = dict((int(y), v) for y, v in hpi["series"]); ly = hpi["latest_year"]
    if 2025 in ser and ly in ser:
        idx = ser[ly] / ser[2025]
        index_basis = (f"FHFA House Price Index (annual, ZIP {ZIP}) {2025}->{ly}"
                       + (" (no newer annual figure yet; the county value is dated Jan 2, 2026)" if ly == 2025 else ""))
except Exception as e:  # noqa: BLE001
    print("FHFA HPI unavailable:", e)


def pmt(principal, rate_pct, years):
    i = rate_pct / 100 / 12; n = years * 12
    return principal * i / (1 - (1 + i) ** -n)


n_ok = 0
for h in houses:
    a = rows.get(h.get("address") or "")
    if not a:
        continue
    n_ok += 1
    sd = str(a.get("SALE_DATE") or "").strip()
    mv = a.get("MKT_VAL_TOT") or (h.get("assessor") or {}).get("totalvalue")
    est = round(mv * idx, -3) if mv else None
    h["money"] = {
        "last_sale": {"date": f"{sd[:4]}-{sd[4:6]}" if len(sd) >= 6 else None, "price": a.get("SALE_PRICE") or None,
                      "type": (a.get("SALE_CODE_NAME") or "").strip().title() or None},
        "county_value_2026": mv, "land_value": a.get("LAND_MV1"), "building_value": a.get("BLDG_MV1"),
        "est_value_now": est, "index_adj": round(idx, 3), "index_basis": index_basis, "tax_yr": a.get("TAX_TOT")}
    if est and rates:
        loan = est * 0.8; tax_m = (a.get("TAX_TOT") or 0) / 12
        h["money"]["payments_20pct_down"] = {
            "down": round(est * 0.2), "loan": round(loan), "rates": rates_h,
            "y10": round(pmt(loan, rates["r10"], 10) + tax_m), "y15": round(pmt(loan, rates["r15"], 15) + tax_m),
            "y30": round(pmt(loan, rates["r30"], 30) + tax_m), "includes": "principal + interest + county tax; insurance not included",
            "y10_rate_note": "10-yr uses the 10-yr fixed APOR"}
json.dump(houses, open(OUT / "houses.json", "w"), separators=(",", ":"))
json.dump({"rates": rates, "rates_label": "APOR (average prime offer rate, used by federal regulators)",
           "index_adj": round(idx, 3), "index_basis": index_basis, "index_licence": FHFA_LICENCE,
           "fhfa_zip_hpi": {"zip": ZIP, "latest_year": (hpi or {}).get("latest_year"), "series": (hpi or {}).get("series", [])[-12:]} if hpi else None,
           "rejected_sources_note": "Not used (licence not clear-green): Freddie Mac PMMS, Zillow ZHVI, Realtor.com inventory."},
          open(OUT / "market.json", "w"), indent=1)
print(f"money for {n_ok}/{len(houses)} houses; rates {rates.get('date')} 10y {rates.get('r10')} 15y {rates.get('r15')} 30y {rates.get('r30')}; "
      f"index {idx:.3f} ({index_basis})")

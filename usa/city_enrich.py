"""Attach city open data (assessor, permits, 311) to a built block, using usa/adapters.

  python usa/city_enrich.py <slug>

Reads web/usa/blocks/<slug>/meta.json + houses.json, picks the adapter whose city covers the block,
fetches records in the block's bounding box and joins them to houses:
  assessor  -> by normalised street address, else nearest footprint within 15 m
  permits   -> nearest footprint within 25 m (or address match when it has no coordinates)
  311       -> nearest footprint within 20 m
Adds h.assessor (viewer key names), h.permits, h.cases311, h.roof_permit, h.solar_permit, h.value;
writes layers.json permits/cases311 for the map and meta.city_data (adapter, licences, status, counts).
Cities without an adapter are left untouched. No personal names are ever stored (adapters whitelist keys).
Sources whose licence is not clear-green are removed in the adapters (status "removed: licence not explicit - ...");
for those cities houses get no h.assessor and money falls back to the census-tract median ("not this house"),
and anything an earlier run copied from the old assessor onto the house (address, year built, zoning, value) is cleared.
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.adapters import for_point  # noqa: E402
from usa.adapters._common import REMOVED  # noqa: E402
from usa.blocklock import block_lock  # noqa: E402

SUFFIX = {"AVENUE": "AVE", "AV": "AVE", "STREET": "ST", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
          "BOULEVARD": "BLVD", "TERRACE": "TER", "PARKWAY": "PKWY", "CIRCLE": "CIR", "SOUTH": "S", "NORTH": "N",
          "EAST": "E", "WEST": "W", "HIGHWAY": "HWY", "TRAIL": "TRL", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
          "NORTHEAST": "NE", "NORTHWEST": "NW", "WAY": "WAY", "SQUARE": "SQ"}
CITY_WORDS = set()   # filled per run with the adapter's city name, stripped from the end of assessor addresses


def norm(a):
    if not a:
        return None
    a = re.sub(r"[^A-Z0-9 ]", " ", str(a).upper().split(",")[0])
    w = [SUFFIX.get(x, x) for x in a.split()]
    while len(w) > 2 and w[-1] in CITY_WORDS:          # "455 S 41ST ST BOULDER" -> drop trailing city
        w.pop()
    w = [re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", x) for x in w]   # 33RD -> 33, 41ST -> 41
    return " ".join(w) or None


def main(slug):
    with block_lock(ROOT / "web" / "usa" / "blocks" / slug):   # whole run under the shared block lock
        _main(slug)


def _main(slug):
    B = ROOT / "web" / "usa" / "blocks" / slug
    meta = json.load(open(B / "meta.json", encoding="utf-8"))
    houses = json.load(open(B / "houses.json", encoding="utf-8"))
    lon0, lat0 = meta["origin"]
    ad = for_point(lon0, lat0)
    if ad is None:
        print("no city adapter covers this block; nothing to do"); return
    MX = 111320 * math.cos(math.radians(lat0)); MY = 110540
    hx = np.array([np.mean([p[0] for p in h["ring"]]) for h in houses]); hy = np.array([np.mean([p[1] for p in h["ring"]]) for h in houses])
    by_addr = {norm(h.get("address")): i for i, h in enumerate(houses) if h.get("address")}

    def near(r, maxd):
        if r.get("lon") is None:
            return by_addr.get(norm(r.get("address")))
        lx, ly = (r["lon"] - lon0) * MX, (r["lat"] - lat0) * MY
        d = np.hypot(hx - lx, hy - ly); i = int(np.argmin(d))
        return i if d[i] <= maxd else None

    def local(r):
        if r.get("lon") is None:
            return None, None
        return round((r["lon"] - lon0) * MX, 1), round((r["lat"] - lat0) * MY, 1)

    CITY_WORDS.clear(); CITY_WORDS.update(ad.NAME.upper().replace(".", "").split()); CITY_WORDS.add(ad.STATE.upper())
    by_addr.clear(); by_addr.update({norm(h.get("address")): i for i, h in enumerate(houses) if h.get("address")})
    print("adapter:", ad.NAME, ad.STATE)
    w, s_, e, n = meta["bounds"]; pad = 0.0005  # ~50 m: parcels whose centroid sits just outside the block
    data = ad.fetch((w - pad, s_ - pad, e + pad, n + pad))
    st = data.get("status", {})
    for k, v in st.items():
        print("ok  " if v in ("ok", "not open") else ("gone" if str(v).startswith(REMOVED) else "FAIL"), k, v)

    # Undo what an earlier run copied from the assessor onto the house itself (address, year built, zoning, value),
    # so nothing from a source that has since been removed (licence not clear-green) survives a re-run.
    # The current adapter's assessor (if any) fills these again below.
    n_strip = 0
    for h in houses:
        a = h.get("assessor") or {}
        if str(h.get("address_source") or "").startswith("county assessor"):
            h.pop("address", None); h.pop("address_source", None); n_strip += 1
        if a.get("yearbuilt") is not None and h.get("year_built") == a.get("yearbuilt"):
            h.pop("year_built", None)
        if a.get("zoning") and h.get("zoning") == a.get("zoning"):
            h.pop("zoning", None)
        h.pop("value", None)
        h["permits"], h["cases311"], h["assessor"] = [], [], None
    if n_strip:
        print(f"cleared {n_strip} assessor-derived addresses from an earlier run")
    by_addr.clear(); by_addr.update({norm(h.get("address")): i for i, h in enumerate(houses) if h.get("address")})
    removed = {k: v for k, v in st.items() if str(v).startswith(REMOVED)}
    n_ass = 0
    for r in data.get("assessor", []):
        i = by_addr.get(norm(r.get("address")))
        if i is None:
            i = near(r, 15)
            if i is not None and (houses[i].get("garage") or houses[i].get("address")):
                i = None   # nearest-footprint fallback only for unaddressed main buildings, never garages
        if i is None or houses[i].get("assessor"):
            continue
        houses[i]["assessor"] = {
            "address": r.get("address"), "yearbuilt": r.get("year_built"), "stories": r.get("stories"),
            "abovegroundarea": r.get("living_area_sqft"), "roof": r.get("roof"), "exteriorwall": r.get("exterior_wall"),
            "garage": r.get("garage"), "totalbedrooms": r.get("bedrooms"), "totalbathrooms": r.get("baths"),
            "landvalue": r.get("land_value"), "buildingvalue": r.get("building_value"), "totalvalue": r.get("total_value"),
            "sale_date": r.get("sale_date"), "sale_price": r.get("sale_price"), "zoning": r.get("zoning"),
            "propertytype": r.get("property_type"),
            # optional extras some adapters provide (tax year, lot size)
            **{k: r[k] for k in ("tax_year", "lot_sqft") if r.get(k) is not None}}
        if r.get("address") and not houses[i].get("address"):   # no open address point: use the assessor's situs address
            houses[i]["address"] = " ".join(w.capitalize() if not w[:1].isdigit() else w.lower() for w in str(r["address"]).split(",")[0].split())
            houses[i]["address_source"] = f"county assessor ({ad.NAME})"
        if r.get("year_built") and not houses[i].get("year_built"):
            houses[i]["year_built"] = r["year_built"]
        if r.get("total_value"):
            houses[i]["value"] = r["total_value"]
        if r.get("zoning") and not houses[i].get("zoning"):
            houses[i]["zoning"] = r["zoning"]
        n_ass += 1

    # Per-house value from the county assessor replaces the census-tract median that enrich_national.py
    # had to fall back on (which was identical for every house). Some states publish ASSESSED value, a fixed
    # fraction of market value: convert those back and say so.
    ratio = {"kansas_city": 0.19}.get(ad.__name__.rsplit(".", 1)[-1], 1.0)
    # mortgage rates: the APOR (FFIEC/CFPB average prime offer rate) written by enrich_national.py money -> market.json
    mk_rates = (json.load(open(B / "market.json", encoding="utf-8")).get("rates") or {}) if (B / "market.json").exists() else {}
    apor = {k: mk_rates[k] for k in ("date", "r10", "r15", "r30", "label", "source") if k in mk_rates} if mk_rates.get("r10") else None
    n_val = 0
    for h in houses:
        a = h.get("assessor") or {}
        v = a.get("totalvalue")
        old = h.get("money") or {}
        if h.get("garage"):
            h.pop("money", None); h.pop("value", None); continue
        if not old:
            continue
        pay_old = old.get("payments_20pct_down") or {}
        rates = apor or (pay_old.get("rates") if (pay_old.get("rates") or {}).get("r10") else None)   # APOR only (no PMMS leftovers)
        if "neighbourhood_median" in old:   # re-run: keep the original tract figures
            tract_val, tract_tax = old.get("neighbourhood_median"), old.get("neighbourhood_tax")
        else:
            tract_val, tract_tax = old.get("est_value_now"), old.get("tax_yr")
        if v and v > 0:
            mk = round(v / ratio); own = True
        elif tract_val:
            mk = round(tract_val); own = False
        else:
            continue
        tax = (round(tract_tax * mk / tract_val) if own else tract_tax) if tract_tax and tract_val else None
        m = {"est_value_now": mk, "value_level": "this house" if own else "neighbourhood median (census tract) - not this house",
             "value_basis": (f"{ad.NAME} county assessor {'market' if ratio == 1 else 'assessed'} value"
                             + ("" if ratio == 1 else f" / {ratio:.2f} assessment ratio")) if own else
                            (f"no open-licence assessor data for {ad.NAME}; ACS census-tract median home value"
                             if "assessor" in removed else
                             "no assessor record matched this building; ACS census-tract median home value"),
             "basis": "county assessor value" if own else "census tract median",
             "last_sale": {"date": a.get("sale_date"), "price": a.get("sale_price")} if a.get("sale_price") else None,
             "neighbourhood_median": tract_val, "neighbourhood_tax": tract_tax, "tax_yr": tax,
             "tax_basis": ("tract median tax scaled by this home's value" if own else "tract median tax") if tax else None}
        if rates:
            loan = mk * 0.8
            def pmt(r, n):
                i = r / 100 / 12
                return loan * i / (1 - (1 + i) ** -n)
            mt = (tax or 0) / 12
            m["payments_20pct_down"] = {"down": round(mk * 0.2), "loan": round(loan), "rates": rates,
                                        "y10": round(pmt(rates["r10"], 120) + mt), "y15": round(pmt(rates["r15"], 180) + mt),
                                        "y30": round(pmt(rates["r30"], 360) + mt),
                                        "includes": "principal + interest" + (" + estimated property tax" if tax else "") + "; insurance not included",
                                        "y10_rate_note": "10-yr uses the 10-yr fixed APOR"}
        h["money"] = m
        if own:
            h["value"] = mk; n_val += 1
        else:
            h.pop("value", None)
    print(f"per-house values from assessor: {n_val}")

    P, C = [], []
    for r in data.get("permits", []):
        i = near(r, 25); x, y = local(r)
        rec = {"house": i, "x": x, "y": y, **{k: r.get(k) for k in ("type", "work", "status", "value", "comments", "date")}}
        if i is not None:
            houses[i]["permits"].append(rec); x, y = (x, y) if x is not None else (round(float(hx[i]), 1), round(float(hy[i]), 1))
        if x is not None:
            P.append({**rec, "x": x, "y": y})
    for r in data.get("cases311", []):
        i = near(r, 20); x, y = local(r)
        rec = {"house": i, "x": x, "y": y, **{k: r.get(k) for k in ("type", "title", "status", "date")}}
        if i is not None:
            houses[i]["cases311"].append(rec)
        if x is not None:
            C.append(rec)

    txt = lambda q: ((q.get("work") or "") + " " + (q.get("type") or "") + " " + (q.get("comments") or "")).lower()
    roofy = lambda q: any(w in txt(q) for w in ("reroof", "re-roof", "re roof", "tear off", "shingle", "roof replace", "new roof", "roofing")) and "solar" not in txt(q)
    for h in houses:
        h["cases311"].sort(key=lambda c: str(c.get("date") or ""), reverse=True)
        h["roof_permit"] = max((str(q["date"]) for q in h["permits"] if q.get("date") and roofy(q)), default=None)
        h["solar_permit"] = max((str(q["date"]) for q in h["permits"] if q.get("date") and "solar" in txt(q)), default=None)

    lp = B / "layers.json"
    layers = json.load(open(lp, encoding="utf-8")) if lp.exists() else {}
    layers["permits"], layers["cases311"] = P, C
    json.dump(layers, open(lp, "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(houses, open(B / "houses.json", "w", encoding="utf-8"), separators=(",", ":"))
    meta["city_data"] = {"adapter": ad.NAME, "licences": ad.LICENCES, "status": st,
                         "counts": {"assessor_matched": n_ass, "permits": len(P), "cases311": len(C)}}
    if removed:
        meta["city_data"]["removed"] = removed
    if n_ass:
        meta["assessor_label"] = f"Assessor ({ad.NAME})"
    else:
        meta.pop("assessor_label", None)
    files = set(meta.get("files") or []) | {p.name for p in B.iterdir() if p.is_file()}
    meta["files"] = sorted(files)
    json.dump(meta, open(B / "meta.json", "w", encoding="utf-8"), indent=1)
    print(f"assessor matched {n_ass}/{len(houses)} houses | permits {len(P)} | 311 {len(C)}")


if __name__ == "__main__":
    main(sys.argv[1])

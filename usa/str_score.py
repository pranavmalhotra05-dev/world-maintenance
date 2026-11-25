"""County short-term-rental (STR) demand / cost / legal score + per-block STR context.

    .venv\\Scripts\\python -u -W ignore -m usa.str_score            # county file only
    .venv\\Scripts\\python -u -W ignore -m usa.str_score --all      # + every block
    .venv\\Scripts\\python -u -W ignore -m usa.str_score --blocks austin-tx,dallas-tx

Inputs (all optional except counties.json; anything missing is treated as null and the blend re-weights):
  web/usa/data/tourism_air.json, tourism_econ.json, tourism_events.json, counties.json, rules.json
Outputs:
  web/usa/data/str_score.json          counties{fips:{demand,cost,score,legal,peak_months,parts}}
  web/usa/blocks/<slug>/meta.json      meta.str   (added/replaced; nothing else touched)
  web/usa/blocks/<slug>/houses.json    h.str      (added/replaced per non-garage house)
No nightly rates or occupancy are produced: there is no licence-clean public source for them.
Method write-up: usa/research/str_score_method.md
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

DATA = ROOT / "web" / "usa" / "data"
BLOCKS = ROOT / "web" / "usa" / "blocks"
OUT = DATA / "str_score.json"

NOTE = "Estimate from aggregate public data; no booking or occupancy data is used"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Demand blend. 'zero_if_absent': when the source file exists but the county is not listed, the true value is ~0
# (no airport within 60 km, no NPS unit within 80 km, no big venue) rather than unknown.
DEMAND_INPUTS = {
    "air_visitor_pax_60km": dict(w=0.25, src="tourism_air", zero_if_absent=True,
                                 label="Visitor (O&D-weighted) air passengers, airports within 60 km, 12 mo"),
    "tourism_intensity": dict(w=0.20, src="tourism_econ", zero_if_absent=False,
                              label="Accommodation employment share (tourism intensity)"),
    "leisure_emp_per_cap": dict(w=0.15, src="tourism_econ", zero_if_absent=False,
                                label="Leisure & hospitality employment per resident (or per household proxy)"),
    "nps_visits_80km": dict(w=0.10, src="tourism_air", zero_if_absent=True,
                            label="National Park Service recreation visits within 80 km"),
    "venue_capacity": dict(w=0.10, src="tourism_events", zero_if_absent=True,
                           label="Big-venue capacity (stadiums/arenas/amphitheatres) in county"),
    "wiki_views_12m": dict(w=0.10, src="tourism_econ", zero_if_absent=False,
                           label="Wikipedia/Wikivoyage page views, 12 mo (travel interest)"),
    "hot_growth": dict(w=0.10, src="tourism_econ", zero_if_absent=False,
                       label="Hotel-occupancy-tax receipts growth (or level where no growth series)"),
    # small extra "flow" signal (people physically arriving); weights are relative, the blend re-normalises
    "in_commuters": dict(w=0.05, src="jobs_flow", zero_if_absent=False,
                         label="Workers commuting into the county (LEHD/ACS flows)"),
    "vmt_daily": dict(w=0.05, src="road_traffic", zero_if_absent=False,
                      label="Daily vehicle-miles travelled on county roads"),
    # visitors.json (county counts; absent county = 0 where the series is national and exhaustive)
    "federal_land_visits": dict(w=0.05, src="visitors", zero_if_absent=True,
                                label="Federal land (NPS/USFS/BLM/USACE/FWS) recreation visits in county"),
    "state_park_visits": dict(w=0.04, src="visitors", zero_if_absent=False,
                              label="State park visits in county (state reporting is patchy)"),
    "cruise_pax": dict(w=0.03, src="visitors", zero_if_absent=True, label="Cruise passengers at county ports"),
    "amtrak_riders": dict(w=0.03, src="visitors", zero_if_absent=True, label="Amtrak boardings+alightings at county stations"),
    "border_crossings_12m": dict(w=0.03, src="visitors", zero_if_absent=True,
                                 label="Inbound land-border crossings (persons), 12 mo"),
    "state_overseas_visits": dict(w=0.03, src="visitors", zero_if_absent=False,
                                  label="State overseas visits (weak state-level prior, same value for every county in the state)"),
}
# HOUSING demand (buy / long-term rent). sign=+1 higher is more demand, -1 lower is more demand. signed=True: may be negative.
HOUSING_INPUTS = {
    "emp_yoy": dict(w=0.20, sign=1, label="Employment growth, 1 yr (%)"),
    "emp_5y": dict(w=0.10, sign=1, label="Employment growth, 5 yr (%)"),
    "wage_yoy": dict(w=0.15, sign=1, label="Average weekly wage growth, 1 yr (%)"),
    "net_mig_rate": dict(w=0.20, sign=1, label="Net domestic migration per resident worker (or pop if given)"),
    "inflow_ratio": dict(w=0.10, sign=1, label="Commuter inflow ratio (jobs here / residents employed)"),
    "unemp": dict(w=0.10, sign=-1, label="Unemployment rate (lower = more demand)"),
    "unemp_chg": dict(w=0.05, sign=-1, label="Change in unemployment rate (falling = more demand)"),
    "permits": dict(w=0.10, sign=1, label="Residential building permits per 1,000 (builders following demand)"),
}
TOTAL_W = sum(v["w"] for v in DEMAND_INPUTS.values())
PRIOR_W = 0.15  # pseudo-weight of a median (50) prior blended into every demand score
MIN_W = 0.10    # below this much input weight a county gets no demand score
OWNER_SHARE = 0.65         # households ~= owner-occupied units / 0.65 when renter counts are absent
CRED_K = 4.0               # credibility w = n/(n+K), n = (log10(hh)-2)^2: 1k hh 0.20, 10k 0.50, 100k 0.69, 1M 0.80
INTENSITY_MIN_JOBS = 200   # tourism-intensity weight scaled by acc_emp/200 below this many accommodation jobs
SCORE_FORMULA = "score = demand * (0.6 + 0.4 * cost/100)"
COST_WEIGHTS = {"home_value_vs_national": 0.75, "fhfa_trend": 0.25}


def _load(name):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception as e:  # half-written by another lane: treat as missing this run
        print(f"  ! could not read {name}: {e}")
        return None


def _num(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _r(x, n=1):
    return None if x is None else round(x, n)


def _sig(x, n=3):
    """compact raw value: integers for |x| >= 100, else n significant figures."""
    if x is None:
        return None
    if abs(x) >= 100:
        return int(round(x))
    return float(f"{x:.{n}g}")


def _p(x):
    return None if x is None else int(round(x))


def pct_rank(values):
    """{key: value} -> {key: percentile 0-100}. Values <= 0 get 0 (zero-heavy inputs); ties share the average rank."""
    pos = sorted((v, k) for k, v in values.items() if v is not None and v > 0)
    out = {k: 0.0 for k, v in values.items() if v is not None and v <= 0}
    n_all = sum(1 for v in values.values() if v is not None)
    nz = len(out)
    i = 0
    while i < len(pos):
        j = i
        while j + 1 < len(pos) and pos[j + 1][0] == pos[i][0]:
            j += 1
        rank = (i + j) / 2 + 1  # 1-based among positives
        p = 100.0 * (nz + rank) / n_all if n_all else None
        for m in range(i, j + 1):
            out[pos[m][1]] = p
        i = j + 1
    return out


def hav_km(lon1, lat1, lon2, lat2):
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(min(1.0, a)))


def _profile(vals, n):
    v = [_num(x) for x in (vals or [])]
    if len(v) != n or any(x is None for x in v) or sum(v) <= 0:
        return None
    m = sum(v) / n
    return [x / m for x in v]


def hot_growth(x):
    """hot_receipts_12m may be a number (level), {yoy|growth|growth_pct|cur/prev}, or a list (oldest..newest)."""
    if isinstance(x, dict):
        for k in ("yoy", "growth", "growth_pct", "yoy_pct", "chg_pct"):
            g = _num(x.get(k))
            if g is not None:
                return g if abs(g) > 2 else g * 100, "growth"
        cur = _num(x.get("cur") or x.get("last_12m") or x.get("value") or x.get("now"))
        prev = _num(x.get("prev") or x.get("prior_12m") or x.get("prior"))
        if cur is not None and prev:
            return 100.0 * (cur / prev - 1), "growth"
        if cur is not None:
            return cur, "level"
        return None, None
    if isinstance(x, list):
        v = [_num(a) for a in x if _num(a) is not None]
        if len(v) >= 2 and v[0] > 0:
            return 100.0 * (v[-1] / v[0] - 1), "growth"
        return (v[0], "level") if v else (None, None)
    v = _num(x)
    return (v, "level") if v is not None else (None, None)


# ---------------------------------------------------------------- legal (rules.json)
def city_key(name):
    k = (name if isinstance(name, str) else "").strip().lower().replace(".", "").replace("-", "_").replace(" ", "_")
    return "saint_" + k[3:] if k.startswith("st_") else ("fort_" + k[3:] if k.startswith("ft_") else k)


def classify_city(fields):
    """Short status + reasons from the rules.json STR group. Keyword rules on our own summaries; see method doc."""
    g = lambda k: ((fields.get(k) or {}).get("value") or "")
    permit, pr, zones, caps, pending = g("permit"), g("primary_residence"), g("zones"), g("caps"), g("pending")
    low = lambda s: s.lower()
    reasons, status = [], "permitted with licence"
    if any(w in low(permit) for w in ("injunction", "enjoin")):
        status = "in litigation"
        reasons.append("City STR ordinance is enjoined / in court: " + permit)
    elif low(permit).startswith("none in force") or "draft" in low(permit):
        status = "ordinance pending"
        reasons.append("No city STR permit in force yet; draft ordinance pending: " + permit)
    elif low(permit).startswith("yes"):
        reasons.append("City permit/licence required: " + permit)
    if low(pr).startswith("yes") or low(pr).startswith("only"):
        status = "primary residence only"
        reasons.append("Primary-residence rule (no investor whole-home STR): " + pr)
    zones_open = low(zones).startswith("allowed wherever") or "bars cities from banning" in low(zones)
    if zones_open:
        reasons.append("Zoning: " + zones)
    elif "banned" in low(zones) or "not allowed" in low(zones):
        if status == "permitted with licence":
            status = "zoning restricted"
        reasons.append("Zoning limits: " + zones)
    elif "motel" in low(zones) or "allowed by right in" in low(zones) or "no single-family" in low(zones):
        if status == "permitted with licence":
            status = "zoning restricted"
        reasons.append("Allowed only in some zoning districts: " + zones)
    if caps and not low(caps).startswith("none") and "zoning restriction only" not in low(caps):
        reasons.append("Caps/density: " + caps)
    pl = low(pending)
    if pending and not (pl.startswith("none") or pl.startswith("no pending")):
        reasons.append("Pending: " + pending)
    return status, reasons


def build_legal(rules, blocks_index):
    """-> (city_legal{citykey: {...}}, county_legal{fips: {...}}, state_pre{ST: {...}})"""
    city_legal, county_legal, state_pre = {}, {}, {}
    if not rules:
        return city_legal, county_legal, state_pre
    for st, s in (rules.get("states") or {}).items():
        f = (s.get("fields") or {}).get("str_preemption")
        if f:
            state_pre[st] = {"value": f.get("value"), "verified": f.get("verified"), "source_url": f.get("source_url"),
                             "checked": f.get("checked")}
    for ck, c in (rules.get("cities") or {}).items():
        fields = c.get("fields") or {}
        status, reasons = classify_city(fields)
        detail = {}
        for k in ("permit", "primary_residence", "zones", "caps", "max_occupancy", "taxes", "listing_id", "pending"):
            f = fields.get(k)
            if f:
                detail[k] = {"value": f.get("value"), "verified": f.get("verified"), "source_url": f.get("source_url"),
                             "checked": f.get("checked")}
        ver = [d["verified"] for d in detail.values()]
        city_legal[ck] = {"city": c.get("name"), "state": c.get("state"), "status": status, "reasons": reasons,
                          "verified": True if ver and all(v is True for v in ver) else ("partial" if any(v for v in ver) else False),
                          "fields": detail, "state_preemption": state_pre.get(c.get("state"))}
    # county <- city via the block index (city, fips)
    for b in blocks_index or []:
        ck = city_key(b.get("city_data") or b.get("city"))
        fips = b.get("fips")
        if ck in city_legal and fips:
            cl = city_legal[ck]
            e = county_legal.setdefault(fips, {"status": cl["status"], "scope": "city", "cities": []})
            if cl["city"] not in e["cities"]:
                e["cities"].append(cl["city"])
                if e["status"] != cl["status"]:
                    e["status"] = "mixed: " + "; ".join(f"{city_legal[city_key(x)]['city']}={city_legal[city_key(x)]['status']}" for x in e["cities"])
    for e in county_legal.values():
        e["note"] = ("City rules for " + ", ".join(e["cities"]) + " apply inside city limits only; "
                     "other cities and unincorporated areas in the county are not checked.")
    return city_legal, county_legal, state_pre


# ---------------------------------------------------------------- county scores
def _signed_rank(vals):
    vv = {f: v for f, v in vals.items() if v is not None}
    if not vv:
        return {}
    mn = min(vv.values())
    return pct_rank({f: v - mn + 1e-9 for f, v in vv.items()})


def _origin_names(origins, by):
    """top_origins may be [fips], [{fips,n}], [[fips,n]] or [name]; return [{fips,name,n}] (max 5)."""
    out = []
    for o in (origins or [])[:5]:
        f, n, nm = None, None, None
        if isinstance(o, dict):
            f = o.get("fips") or o.get("geoid"); n = o.get("n") or o.get("people") or o.get("count") or o.get("movers"); nm = o.get("name")
        elif isinstance(o, (list, tuple)) and o:
            f = o[0]; n = o[1] if len(o) > 1 else None
        else:
            f = o
        f = str(f) if f is not None else None
        if f and f.isdigit() and len(f) <= 5:
            f = f.zfill(5)
            r = by.get(f)
            nm = nm or (f"{r['NAME']}, {r['STUSPS']}" if r else None)
        elif f and not nm:
            nm, f = f, None
        out.append({"fips": f, "name": nm, "n": n})
    return out


def housing_scores(by, jobs):
    jc = (jobs or {}).get("counties") or {}
    raw = {k: {} for k in HOUSING_INPUTS}
    for f in by:
        j = jc.get(f)
        if not j:
            continue
        for k in ("emp_yoy", "emp_5y", "wage_yoy", "inflow_ratio", "unemp", "unemp_chg"):
            raw[k][f] = _num(j.get(k))
        nm = _num(j.get("net_domestic_mig"))
        pop = _num(j.get("pop") or j.get("population"))
        base = pop or _num(j.get("residents_employed"))
        raw["net_mig_rate"][f] = nm / base if nm is not None and base else None
        raw["permits"][f] = _num(j.get("permits_per_1k") or j.get("permits_per_1000") or j.get("permits"))
    pct = {k: _signed_rank(v) for k, v in raw.items()}
    out = {}
    for f in by:
        j = jc.get(f)
        if not j:
            out[f] = None
            continue
        parts, ws, acc = [], 0.0, 0.0
        for k, s in HOUSING_INPUTS.items():
            v, p = raw[k].get(f), pct[k].get(f)
            parts.append([_sig(v), _p(p)] if v is not None or p is not None else None)
            if p is not None:
                ws += s["w"]; acc += s["w"] * (p if s["sign"] > 0 else 100 - p)
        out[f] = {"score": _r(acc / ws) if ws else None, "coverage": _r(ws, 2), "parts": parts,
                  "pressure_from_jobs_lane": j.get("housing_demand_pressure"),
                  "pop_change_pct": j.get("pop_change_pct"),
                  "top_origins": [[o["fips"] or o["name"], o["n"]] for o in _origin_names(j.get("top_origins"), by)]}
    cov = {k: sum(1 for v in pct[k].values() if v is not None) for k in HOUSING_INPUTS}
    return out, cov


def county_scores(counties, src, county_legal, state_pre):
    air, econ, events = src.get("tourism_air"), src.get("tourism_econ"), src.get("tourism_events")
    jobs, road, vis = src.get("jobs_flow"), src.get("road_traffic"), src.get("visitors")
    cols = counties["cols"]
    rows = [dict(zip(cols, r)) for r in counties["rows"]]
    by = {r["fips"]: r for r in rows}
    air_c = (air or {}).get("counties") or {}
    air_a = (air or {}).get("airports") or {}
    econ_c = (econ or {}).get("counties") or {}
    ev_c = (events or {}).get("counties") or {}
    jobs_c = (jobs or {}).get("counties") or {}
    road_c = (road or {}).get("counties") or {}
    vis_c = (vis or {}).get("counties") or {}
    vis_s = (vis or {}).get("states") or {}
    present = {k: src.get(k) is not None for k in
               ("tourism_air", "tourism_econ", "tourism_events", "jobs_flow", "road_traffic", "visitors")}

    raw = {k: {} for k in DEMAND_INPUTS}
    hot_kind = {}
    for f, r in by.items():
        a, e, v = air_c.get(f), econ_c.get(f), ev_c.get(f)
        jb, rd, vs = jobs_c.get(f) or {}, road_c.get(f) or {}, vis_c.get(f)
        raw["in_commuters"][f] = _num(jb.get("in_commuters"))
        raw["vmt_daily"][f] = _num(rd.get("vmt_daily"))
        for k in ("federal_land_visits", "state_park_visits", "cruise_pax", "amtrak_riders", "border_crossings_12m"):
            x = _num(vs.get(k)) if vs else None
            raw[k][f] = x if x is not None else (0.0 if vis_c and DEMAND_INPUTS[k]["zero_if_absent"] else None)
        raw["state_overseas_visits"][f] = _num((vis_s.get(r.get("STUSPS")) or {}).get("overseas_visits"))
        # air: prefer the lane's pre-computed figure; else sum airport pax * O&D share with distance decay
        val = None
        if a is not None:
            val = _num(a.get("visitor_pax_12m_within_60km"))
            if val is None and a.get("airports"):
                s = 0.0
                for ap in a["airports"]:
                    info = air_a.get(ap.get("iata")) or {}
                    pax, km = _num(info.get("pax_12m")), _num(ap.get("km"))
                    od = _num(info.get("od_share"))
                    if pax and km is not None and km <= 60:
                        s += pax * (od if od is not None else 1.0) * max(0.0, 1 - km / 60)
                val = s
        raw["air_visitor_pax_60km"][f] = val if val is not None else (0.0 if present["tourism_air"] and air_c else None)
        nps = _num(a.get("nps_visits_80km")) if a else None
        raw["nps_visits_80km"][f] = nps if nps is not None else (0.0 if present["tourism_air"] and air_c else None)
        if e:
            raw["tourism_intensity"][f] = _num(e.get("tourism_intensity"))
            le = _num(e.get("leisure_emp_per_capita")) or _num(e.get("leisure_share"))
            lemp = _num(e.get("leis_hosp_emp")) or _num(e.get("leisure_emp"))  # supersector 1026 has wider coverage
            if le is None and lemp is not None:
                pop = _num(e.get("pop") or e.get("population"))
                hh = _num(r.get("owner_occ"))
                le = lemp / pop if pop else (lemp / hh if hh else None)
            raw["leisure_emp_per_cap"][f] = le
            raw["wiki_views_12m"][f] = _num(e.get("wiki_views_12m"))
            g, kind = hot_growth(e.get("hot_receipts_12m"))
            if kind == "level":  # dollar level is size-biased: use receipts per resident
                pop = _num(e.get("pop") or e.get("population"))
                g = g / pop if pop else None
            raw["hot_growth"][f] = g
            if kind:
                hot_kind[f] = kind
        cap = _num(v.get("big_venue_capacity_sum")) if v else None
        raw["venue_capacity"][f] = cap if cap is not None else (0.0 if present["tourism_events"] and ev_c else None)

    # HOT: never mix growth % with dollar levels in one ranking -> rank growth where most counties have growth
    kinds = [k for k in hot_kind.values()]
    if kinds:
        keep = "growth" if kinds.count("growth") >= kinds.count("level") else "level"
        raw["hot_growth"] = {f: (v if hot_kind.get(f) == keep else None) for f, v in raw["hot_growth"].items()}
        DEMAND_INPUTS["hot_growth"]["basis"] = keep
    # growth can be negative: shift so ranking is ordinal over all values (no zero-floor)
    pct = {k: (_signed_rank(vals) if k == "hot_growth" else pct_rank(vals)) for k, vals in raw.items()}
    housing, housing_cov = housing_scores(by, jobs)

    # cost
    hv = {f: _num(r.get("home_value")) for f, r in by.items()}
    hv_ok = sorted(v for v in hv.values() if v)
    nat_med = hv_ok[len(hv_ok) // 2] if hv_ok else None
    hv_pct = pct_rank(hv)
    trend_col = next((c for c in ("fhfa_hpi_5y", "hpi_5y", "fhfa_5y", "hpi_chg_5y", "fhfa_hpi_1y", "hpi_1y") if c in cols), None)
    tr = {f: _num(r.get(trend_col)) for f, r in by.items()} if trend_col else {}
    tr_pct = _signed_rank(tr) if tr else {}

    # ---- market depth: households (log size) + absolute visitor volume
    renter_col = next((c for c in ("renter_occ", "renters", "renter_occupied") if c in cols), None)
    hh, hh_basis = {}, {}
    for f, r in by.items():
        oo, sf = _num(r.get("owner_occ")), _num(r.get("sf_detached"))
        ro = _num(r.get(renter_col)) if renter_col else None
        if oo and ro is not None:
            hh[f], hh_basis[f] = oo + ro, "owner+renter"
        elif oo:
            hh[f], hh_basis[f] = oo / OWNER_SHARE, "owner_occ/0.65"
        elif sf:
            hh[f], hh_basis[f] = sf, "sf_detached"
        else:
            hh[f] = None
    acc_emp = {f: _num((econ_c.get(f) or {}).get("acc_emp")) for f in by}
    vol_raw = {"air_pax": raw["air_visitor_pax_60km"], "venue_cap": raw["venue_capacity"],
               "nps_visits": raw["nps_visits_80km"], "federal_land": raw["federal_land_visits"],
               "cruise": raw["cruise_pax"], "amtrak": raw["amtrak_riders"], "acc_emp": acc_emp}
    vol_pct = {k: pct_rank(v) for k, v in vol_raw.items() if any(x is not None for x in v.values())}
    hh_pct = pct_rank(hh)
    depth, depth_parts = {}, {}
    for f in by:
        vp = [vol_pct[k][f] for k in vol_pct if vol_pct[k].get(f) is not None]
        vol = sum(vp) / len(vp) if vp else None
        hp = hh_pct.get(f)
        ok = [x for x in (hp, vol) if x is not None]
        depth[f] = sum(ok) / len(ok) if ok else None
        depth_parts[f] = [_sig(hh.get(f)), _p(hp), _p(vol), len(vp)]
    depth_pct = pct_rank(depth)

    out = {}
    cover_n = {k: 0 for k in DEMAND_INPUTS}
    for f, r in by.items():
        parts, wsum, acc = [], 0.0, 0.0
        for k, spec in DEMAND_INPUTS.items():
            v, p = raw[k].get(f), pct[k].get(f)
            parts.append([_sig(v), _p(p)] if v is not None or p is not None else None)
            if p is not None:
                w = spec["w"]
                ae = acc_emp.get(f)
                if k == "tourism_intensity" and ae is not None and ae < INTENSITY_MIN_JOBS:
                    w *= max(0.0, ae) / INTENSITY_MIN_JOBS  # tiny hotel sectors give noisy shares
                wsum += w; acc += w * p; cover_n[k] += 1
        # (a) coverage prior: few inputs -> pulled toward 50
        demand_raw = (acc + 50 * PRIOR_W) / (wsum + PRIOR_W) if wsum >= MIN_W else None
        # (b) credibility: small markets -> pulled toward 50. n = (log10(households) - 2)^2, w = n / (n + K)
        h = hh.get(f)
        n_c = max(0.0, math.log10(h) - 2) ** 2 if h and h > 0 else 0.0
        cred = n_c / (n_c + CRED_K) if n_c > 0 else 0.0
        demand = _r(50 + cred * (demand_raw - 50)) if demand_raw is not None else None
        # cost: cheaper entry = higher
        cparts, cw, cacc = {}, 0.0, 0.0
        if hv_pct.get(f) is not None and hv.get(f):
            c1 = 100 - hv_pct[f]
            cparts["home_value"] = [int(hv[f]), _r(hv_pct[f]), _r(hv[f] / nat_med, 2) if nat_med else None]
            cw += COST_WEIGHTS["home_value_vs_national"]; cacc += COST_WEIGHTS["home_value_vs_national"] * c1
        if tr_pct.get(f) is not None:
            cparts["fhfa_trend"] = [_r(tr[f], 2), _r(tr_pct[f])]
            cw += COST_WEIGHTS["fhfa_trend"]; cacc += COST_WEIGHTS["fhfa_trend"] * (100 - tr_pct[f])
        cost = _r(cacc / cw) if cw else None
        score = _r(demand * (0.6 + 0.4 * cost / 100)) if demand is not None and cost is not None else None
        score_raw = _r(demand_raw * (0.6 + 0.4 * cost / 100)) if demand_raw is not None and cost is not None else None
        dp = depth_pct.get(f)
        score_inv = _r(score * min(1.0, dp / 50)) if score is not None and dp is not None else None
        # seasonality: blend whatever monthly/quarterly profiles exist
        profs = []
        a, e = air_c.get(f) or {}, econ_c.get(f) or {}
        p12 = _profile(a.get("seasonality"), 12)
        if p12:
            profs.append(p12)
        pq = _profile(e.get("seasonality_q"), 4)
        if pq:
            profs.append([pq[m // 3] for m in range(12)])
        pw = _profile(e.get("wiki_season_idx"), 12)
        if pw:
            profs.append(pw)
        season = [round(sum(p[m] for p in profs) / len(profs), 2) for m in range(12)] if profs else None
        if season:
            top = sorted(range(12), key=lambda m: -season[m])
            peak = sorted(m for m in top[:3] if season[m] >= 1.05) or [top[0]]
            peak_months = [MONTHS[m] for m in peak]
        else:
            wp = e.get("wiki_peak_months")
            peak_months = [MONTHS[int(m) - 1] if isinstance(m, (int, float)) and 1 <= m <= 12 else m for m in wp] if wp else None
        st = r.get("STUSPS")
        if f in county_legal:
            legal = dict(county_legal[f])
        elif st in state_pre:
            legal = {"status": "unknown", "scope": "state"}
        else:
            legal = {"status": "unknown", "scope": None}
        out[f] = {"name": r.get("NAME"), "st": st, "demand": demand, "demand_raw": _r(demand_raw),
                  "credibility": _r(cred, 2), "cost": cost, "score": score, "score_raw": score_raw,
                  "score_invest": score_inv, "market_depth": _p(dp), "depth_parts": depth_parts[f], "legal": legal,
                  "peak_months": peak_months, "seasonality": season,
                  "coverage": _r(wsum / TOTAL_W, 2), "parts": {"demand": parts, "cost": cparts},
                  "housing": housing.get(f)}
        sr = _num(e.get("str_registered"))
        if sr is not None:  # known registered STR supply: context only, never in the score
            pop = _num(e.get("pop"))
            out[f]["str_supply"] = {"registered": int(sr), "per_1k_pop": _r(1000 * sr / pop, 2) if pop else None,
                                    "src": e.get("str_src"), "city_registry": e.get("str_city_registry")}
    n = len(by)
    coverage = {k: {"counties": cover_n[k], "share": _r(cover_n[k] / n, 3), "weight": DEMAND_INPUTS[k]["w"],
                    "source_file_present": present[DEMAND_INPUTS[k]["src"]]} for k in DEMAND_INPUTS}
    coverage["housing"] = {k: {"counties": v, "share": _r(v / n, 3), "weight": HOUSING_INPUTS[k]["w"]}
                           for k, v in housing_cov.items()}
    coverage["housing"]["_source_file_present"] = present["jobs_flow"]
    coverage["home_value"] = {"counties": len(hv_ok), "share": _r(len(hv_ok) / n, 3)}
    coverage["fhfa_trend"] = {"counties": len([v for v in tr.values() if v is not None]), "column": trend_col}
    return out, coverage, nat_med, air_a


# ---------------------------------------------------------------- blocks
def _peak_window(share):
    s = [_num(x) or 0 for x in (share or [])]
    if len(s) != 24 or sum(s) <= 0:
        return None
    tot = sum(s)
    s = [x / tot for x in s]
    h = max(range(24), key=lambda i: s[i])
    w = max(range(24), key=lambda i: s[i] + s[(i + 1) % 24] + s[(i + 2) % 24])
    ci = (w + 1) % 24
    return {"peak_arrival_hour": h, "busiest_3h_window": f"{w:02d}:00-{(w + 3) % 24:02d}:00",
            "checkin_window": f"{ci:02d}:00-{(ci + 3) % 24:02d}:00",
            "share_in_window": round(s[w] + s[(w + 1) % 24] + s[(w + 2) % 24], 3),
            "note": "Scheduled arrival hours (local time as given by the air lane); check-in window assumes ~1 h from gate to door."}


def block_str(meta, cty, city_legal, src):
    air, events, vis = src.get("tourism_air"), src.get("tourism_events"), src.get("visitors")
    air_a = (air or {}).get("airports") or {}
    fips = meta.get("fips") or meta.get("county_fips")
    lon, lat = (meta.get("origin") or [None, None])[:2]
    c = cty.get(fips) or {}
    m = {"county": {k: c.get(k) for k in ("name", "st", "demand", "demand_raw", "credibility", "cost", "score",
                                          "score_raw", "score_invest", "market_depth", "peak_months",
                                          "coverage")},
         "market_depth_parts": (dict(zip(("households", "households_pct", "visitor_volume_pct", "n_volume_inputs"),
                                         c["depth_parts"])) if c.get("depth_parts") else None),
         "county_parts": ({"demand": dict(zip(DEMAND_INPUTS, c["parts"]["demand"])), "cost": c["parts"]["cost"]}
                          if c.get("parts") else None),
         "county_legal": c.get("legal"), "note": NOTE}
    hd = c.get("housing")
    m["housing_demand"] = ({"score": hd["score"], "coverage": hd["coverage"], "parts": dict(zip(HOUSING_INPUTS, hd["parts"])),
                            "top_origins": [{"fips": k if k in cty else None,
                                             "name": f"{cty[k]['name']}, {cty[k]['st']}" if k in cty else k, "people": n}
                                            for k, n in hd["top_origins"]],
                            "pop_change_pct": hd["pop_change_pct"],
                            "note": "County-level buy/long-term-rent demand from jobs, wages, migration and commuting."}
                           if hd else None)
    sites = []
    for s in (vis or {}).get("sites") or []:
        slon, slat = _num(s.get("lon")), _num(s.get("lat"))
        if slon is None or slat is None or lon is None:
            continue
        d = hav_km(lon, lat, slon, slat)
        if d <= 25:
            sites.append({"name": s.get("name"), "kind": s.get("kind"), "visits": s.get("visits"), "km": round(d, 1)})
    sites.sort(key=lambda x: -(_num(x["visits"]) or 0))
    m["visitor_sites_25km"] = sites[:5] if vis else None
    cl = city_legal.get(city_key(meta.get("city")))
    m["city_legal"] = ({k: cl[k] for k in ("city", "state", "status", "reasons", "verified", "fields", "state_preemption")}
                       if cl else None)
    # nearest airport: from coordinates if the airports table has them, else the county list
    # main gateway = busiest airport (visitor pax) within 60 km; else nearest with >= 100k pax/yr (skips GA fields)
    best, nearest, near60 = None, None, []
    for code, info in (air_a or {}).items():
        alon, alat = _num(info.get("lon")), _num(info.get("lat"))
        pax = _num(info.get("pax_12m")) or 0
        if alon is None or alat is None or lon is None or pax < 100000:
            continue
        d = hav_km(lon, lat, alon, alat)
        vis = pax * (_num(info.get("od_share")) or 1) * (_num(info.get("visitor_share")) or 1)
        if d <= 60:
            near60.append((vis, code, d))
        if nearest is None or d < nearest[1]:
            nearest = (code, d, "block")
    if near60:
        vis, code, d = max(near60)
        best = (code, d, "block")
    elif nearest:
        best = nearest
    m["airports_60km"] = [{"iata": c, "km": round(d, 1)} for _, c, d in sorted(near60, key=lambda x: x[2])] or None
    m["nearest_airport"] = {"iata": nearest[0], "km": round(nearest[1], 1)} if nearest else None
    if best is None:
        aps = (((air or {}).get("counties") or {}).get(fips) or {}).get("airports") or []
        aps = [a for a in aps if _num(a.get("km")) is not None]
        if aps:
            a = min(aps, key=lambda a: a["km"])
            best = (a.get("iata"), a["km"], "county centroid")
    if best:
        info = (air_a or {}).get(best[0]) or {}
        m["airport"] = {"iata": best[0], "km": round(best[1], 1), "km_from": best[2], "name": info.get("name"),
                        "pax_12m": info.get("pax_12m"), "od_share": info.get("od_share"),
                        "visitor_share": info.get("visitor_share"), "pax_yoy": info.get("pax_yoy"),
                        "peak_months": info.get("peak_months"),
                        "arrivals": _peak_window(info.get("arrival_hour_share")),
                        "checkin_window_air_lane": info.get("checkin_window"),
                        "basis": "busiest airport by visitor passengers within 60 km (>=100k pax/yr)"}
    else:
        m["airport"] = None
    # venues within 5 km
    vs = []
    for v in (events or {}).get("venues") or []:
        vlon, vlat = _num(v.get("lon")), _num(v.get("lat"))
        if vlon is None or vlat is None or lon is None:
            continue
        d = hav_km(lon, lat, vlon, vlat)
        if d <= 5:
            vs.append({"name": v.get("n"), "kind": v.get("k"), "cap": v.get("cap"), "km": round(d, 2), "src": v.get("f")})
    vs.sort(key=lambda x: -(_num(x["cap"]) or 0))
    m["venues_5km"] = vs[:5] if events else None
    m["venues_5km_total"] = len(vs) if events else None
    ev_c = ((events or {}).get("counties") or {}).get(fips) or {}
    am = None
    for s in (events or {}).get("amtrak") or (events or {}).get("stations") or []:
        slon, slat = _num(s.get("lon")), _num(s.get("lat"))
        if slon is not None and slat is not None and lon is not None:
            d = hav_km(lon, lat, slon, slat)
            if am is None or d < am[0]:
                am = (d, s.get("n") or s.get("name") or s.get("code"))
    if am:
        m["amtrak"] = {"km": round(am[0], 1), "station": am[1], "km_from": "block"}
    elif _num(ev_c.get("nearest_amtrak_km")) is not None:
        m["amtrak"] = {"km": round(_num(ev_c["nearest_amtrak_km"]), 1), "station": None, "km_from": "county centroid"}
    else:
        m["amtrak"] = None
    m["transit_trips_per_capita"] = ev_c.get("transit_trips_per_capita")
    m["walkability_median"] = ev_c.get("walkability_median")
    m["generated"] = dt.date.today().isoformat()
    return m


def _bedrooms(h):
    a = h.get("assessor") or {}
    for k in ("totalbedrooms", "bedrooms", "beds", "bedroom_count", "num_bedrooms"):
        v = _num(a.get(k))
        if v is not None:
            return int(v)
    return None


def house_str(h, mstr):
    loc = h.get("local") or {}
    reasons = []
    cl = mstr.get("city_legal")
    if cl:
        for t in cl["reasons"]:
            reasons.append(t)
    else:
        reasons.append("City STR rules not checked for this location: check the city and county code.")
    hoa = loc.get("hoa")
    if hoa:
        nm = hoa.get("name") if isinstance(hoa, dict) else str(hoa)
        conf = hoa.get("confidence") if isinstance(hoa, dict) else None
        reasons.append(f"HOA/covenant area ({nm}{', confidence ' + conf if conf else ''}): many HOAs restrict STRs - check CC&Rs.")
    pay = ((h.get("money") or {}).get("payments_20pct_down") or {})
    county = mstr.get("county") or {}
    return {"legal_status": cl["status"] if cl else "unknown",
            "legal_risk": reasons,
            "str_license_on_record": loc.get("str_license"),
            "bedrooms": _bedrooms(h),
            "est_monthly_cost": pay.get("y30"),
            "est_monthly_cost_basis": pay.get("includes") and ("30-yr, 20% down: " + pay["includes"]),
            "demand_score": county.get("demand"),
            "county_score": county.get("score"),
            "county_score_invest": county.get("score_invest"),
            "housing_demand": ({"score": mstr["housing_demand"]["score"],
                                "top_origins": [o["name"] for o in mstr["housing_demand"]["top_origins"] if o.get("name")]}
                               if mstr.get("housing_demand") else None),
            "note": NOTE}


def _write_json(path, obj, indent):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=None if indent else (",", ":"), indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def do_block(d, cty, city_legal, src):
    hp, mp = d / "houses.json", d / "meta.json"
    if not (hp.exists() and mp.exists()):
        return None
    with block_lock(d):
        meta = json.load(open(mp, encoding="utf-8"))
        houses = json.load(open(hp, encoding="utf-8"))
        mstr = block_str(meta, cty, city_legal, src)
        n = 0
        hs = houses["houses"] if isinstance(houses, dict) and "houses" in houses else houses
        for h in hs:
            if h.get("garage"):
                h.pop("str", None)
                continue
            h["str"] = house_str(h, mstr)
            n += 1
        meta["str"] = mstr
        _write_json(hp, houses, None)
        _write_json(mp, meta, 1)
    return mstr, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="annotate every block under web/usa/blocks")
    ap.add_argument("--blocks", default="", help="comma-separated block slugs")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    counties = _load("counties.json")
    if not counties:
        sys.exit("counties.json missing")
    src = {k: _load(k + ".json") for k in
           ("tourism_air", "tourism_econ", "tourism_events", "jobs_flow", "road_traffic", "visitors")}
    rules = _load("rules.json")
    idx = json.load(open(BLOCKS / "index.json", encoding="utf-8")) if (BLOCKS / "index.json").exists() else []
    present = {k: v is not None for k, v in src.items()}
    present["rules"] = rules is not None
    print("inputs:", present)

    city_legal, county_legal, state_pre = build_legal(rules, idx)
    cty, coverage, nat_med, _ = county_scores(counties, src, county_legal, state_pre)

    caveats = [
        NOTE + ". No nightly rates, ADR, RevPAR or occupancy are estimated: we have no permanently free, commercial-use public source for them.",
        "Demand is a relative percentile index across US counties (0-100), not a forecast of bookings or income.",
        "Inputs missing for a county are dropped and the remaining weights re-normalised; 'coverage' = share of total weight present.",
        "Legal status is never folded into the score; a county with a ban can still score high on demand. Read 'legal' first.",
        "Legal entries cover only the 12 cities in rules.json (inside city limits) plus state-preemption notes; everything else is 'unknown'.",
        "Cost uses ACS 5-year median home value (lags the market by 2-3 years); FHFA trend used only if a trend column exists.",
    ]
    caveats.append("Housing demand (counties[fips].housing) is a separate 0-100 index for buying / long-term renting; "
                   "it does not feed the STR score.")
    if not any(src[k] for k in ("tourism_air", "tourism_econ", "tourism_events", "visitors")):
        caveats.insert(0, "No tourism input files were present at this run: demand and score are null until the tourism lanes finish.")
    meta = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "method": "usa/research/str_score_method.md",
            "formula": {"demand": f"(sum w_i*pct_i + 50*{PRIOR_W}) / (sum w_i + {PRIOR_W}) over available inputs; null if sum w_i < {MIN_W}",
                        "cost": "100 - percentile(ACS median home value) [x0.75] + 100 - percentile(FHFA trend) [x0.25], re-weighted if trend missing",
                        "score": SCORE_FORMULA + " (demand after credibility shrinkage); score_raw uses demand_raw",
                        "credibility": f"demand = 50 + w*(demand_raw-50), w = n/(n+{CRED_K}), n = max(0, log10(households)-2)^2",
                        "market_depth": "percentile of mean(pct(households), mean pct of absolute visitor-volume inputs)",
                        "score_invest": "score * min(1, market_depth/50)",
                        "intensity_cap": f"tourism_intensity weight x acc_emp/{INTENSITY_MIN_JOBS} when acc_emp < {INTENSITY_MIN_JOBS}",
                        "housing": "weighted mean of input percentiles (sign -1 inputs inverted), re-weighted over available inputs"},
            "weights": {"demand": {k: v["w"] for k, v in DEMAND_INPUTS.items()}, "cost": COST_WEIGHTS,
                        "housing": {k: v["w"] * v["sign"] for k, v in HOUSING_INPUTS.items()}},
            "labels": {k: v["label"] for k, v in DEMAND_INPUTS.items()},
            "housing_labels": {k: v["label"] for k, v in HOUSING_INPUTS.items()},
            "hot_basis": DEMAND_INPUTS["hot_growth"].get("basis"),
            "parts_format": {"demand": "list of [value, percentile] (null = no data) in meta.part_keys.demand order",
                             "housing.top_origins": "[[origin fips (or name), people], ...] top 5 in-migration origins",
                             "housing.parts": "list of [value, percentile] in meta.part_keys.housing order (raw percentile; sign -1 inverted in score)", "cost.home_value": "[usd, percentile, ratio to national county median]",
                             "cost.fhfa_trend": "[value, percentile]"},
            "depth_parts_format": "[households, households_pct, visitor_volume_pct, n_volume_inputs]; households = "
                                  "owner+renter units if a renter column exists, else owner_occ/0.65, else sf_detached",
            "part_keys": {"demand": list(DEMAND_INPUTS), "housing": list(HOUSING_INPUTS)},
            "national_median_home_value": nat_med,
            "state_preemption": state_pre,
            "legal_scope": {"city": "status from rules.json for the listed cities (inside city limits only)",
                            "state": "no city rules checked; state STR-preemption note in meta.state_preemption[st]",
                            "null": "local and state STR rules not checked"},
            "inputs_present": present,
            "inputs_coverage": coverage, "caveats": caveats,
            "sources": ["tourism_air.json", "tourism_econ.json", "tourism_events.json", "visitors.json",
                        "jobs_flow.json", "road_traffic.json",
                        "counties.json: US Census ACS 5-year (public domain)", "rules.json (project legal research; verified flags per field)"]}
    doc = {"meta": meta, "counties": cty}
    _write_json(OUT, doc, None)
    mb = OUT.stat().st_size / 1e6
    # size guard (< 3 MB): drop 12-month profiles first, then housing/depth parts (blocks keep full detail)
    for drop, why in ((("seasonality",), "seasonality profiles omitted for size; peak_months kept"),
                      (("seasonality", "depth_parts", "housing_parts"), "housing parts and depth parts omitted for size")):
        if mb <= 3:
            break
        slim = {}
        for f, c in cty.items():
            d = {k: v for k, v in c.items() if k not in drop}
            if "housing_parts" in drop and d.get("housing"):
                d["housing"] = {k: v for k, v in d["housing"].items() if k != "parts"}
            slim[f] = d
        meta["caveats"].append(why)
        _write_json(OUT, {"meta": meta, "counties": slim}, None)
        mb = OUT.stat().st_size / 1e6
    print(f"wrote {OUT}  {mb:.2f} MB  counties={len(cty)}" + ("  !! over 3 MB budget" if mb > 3 else ""))
    for k, c in coverage.items():
        print(f"  coverage {k}: {c}")

    def line(f, c):
        return (f"  {f} {c['name']}, {c['st']}: inv {c['score_invest']} score {c['score']} raw {c['score_raw']} "
                f"demand {c['demand']} cost {c['cost']} depth {c['market_depth']} cov {c['coverage']} "
                f"peak {c['peak_months']} legal {c['legal']['status']}")
    ranked = sorted((c for c in cty.items() if c[1]["score_invest"] is not None), key=lambda x: -x[1]["score_invest"])
    if ranked:
        print(f"top {a.top} counties by investment-grade score:")
        for f, c in ranked[:a.top]:
            print(line(f, c))
        print(f"top {a.top} excluding Puerto Rico:")
        for f, c in [x for x in ranked if x[1]["st"] != "PR"][:a.top]:
            print(line(f, c))
        print("block counties:")
        for f in sorted({b.get("fips") for b in idx if b.get("fips") in cty}, key=lambda x: -(cty[x]["score_invest"] or 0)):
            print(line(f, cty[f]))
    else:
        print("no county scores yet (no demand inputs)")

    slugs = [p.name for p in sorted(BLOCKS.iterdir()) if p.is_dir()] if a.all else [s for s in a.blocks.split(",") if s]
    for s in slugs:
        r = do_block(BLOCKS / s, cty, city_legal, src)
        if r:
            m, n = r
            print(f"  block {s}: houses {n}, legal {m['city_legal']['status'] if m['city_legal'] else 'unknown'}, "
                  f"airport {m['airport'] and m['airport']['iata']}, venues5km {m['venues_5km_total']}, demand {m['county']['demand']}")


if __name__ == "__main__":
    main()

"""Lane P5 - cleaning lead engine (pool cleaning + window cleaning).

Reads what other lanes leave in each block and writes ONLY:
    houses.json  -> h["cleaning"]      (per house)
    meta.json    -> meta["cleaning"]   (block summary)

Inputs (all optional; the engine degrades gracefully and records what was present):
    h.pool                {present, area_m2, by_year[{year, state, greenness}], last_state, green_years}  (usa/pools.py)
    h.assessor.pool / pool_area                                                                         (assessor adapters)
    h.trees_near, trees.json, h.meta_canopy                                                             (LiDAR trees)
    h.ring / height_m / eave_m / area_m2 / stories_check / facade_sun                                   (LiDAR)
    h.money.est_value_now, h.local.vacant_or_condemned
    meta.pools, meta.weather_triggers, meta.affluence
    web/usa/data/cleaning_market.json counties[fips]

Method and weights: usa/research/cleaning_leads_method.md

    .venv\\Scripts\\python -u -W ignore -m usa.cleaning_leads                       # default test blocks, once
    .venv\\Scripts\\python -u -W ignore -m usa.cleaning_leads --all                 # every block
    .venv\\Scripts\\python -u -W ignore -m usa.cleaning_leads --wait-min 75 --poll-s 300   # re-run as inputs appear
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
DATA = ROOT / "web" / "usa" / "data"
MARKET = DATA / "cleaning_market.json"
DEFAULT_BLOCKS = ["scottsdale-az", "phoenix-az", "austin-tx", "dallas-tx", "tulsa-ok"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
VERSION = 4

# ---------------------------------------------------------------- weights (max points; each block sums to 100)
POOL_W = {"green_now": 30, "green_past": 15, "trees": 15, "dust_wind": 15, "affluence": 15, "season": 10}
WINDOW_W = {"glass": 35, "dust_rain": 20, "affluence": 20, "stories": 15, "recent_dust": 10}
# area-level parts with no data score this fraction of their weight (neutral), house-level unknowns score 0
NEUTRAL = 0.5
AREA_PARTS = {"dust_wind", "affluence", "season", "dust_rain", "recent_dust"}

GLAZING_RATIO = 0.15          # glass share of opaque wall area (typical US detached house)
STOREY_M = 3.0                # storey height used when no assessor/LiDAR storey count
# state vocabulary of usa/pools.py: clean_blue, turquoise, green_algae, covered_or_empty, not_present, no_data
GREEN_STATES = {"green_algae", "green", "algae", "algal", "murky", "dark", "neglected", "swamp"}
ODD_STATES = {"covered_or_empty", "empty", "drained", "covered", "construction"}
# partial credit for the latest-image state (green states = 1.0)
STATE_PART = {"covered_or_empty": 0.35, "empty": 0.35, "drained": 0.35, "covered": 0.35, "construction": 0.35,
              "not_present": 0.2, "turquoise": 0.15, "clean_blue": 0.0, "clear": 0.0}
NO_DATA_STATES = {"no_data", "", None}

# fixed anchors -> 0..1 (linear, clipped). Chosen so a typical Sun Belt suburb lands mid-range; see method doc.
ANCH = {
    "dust_days_yr": (0, 30), "high_wind_days_yr": (0, 40), "dust_then_rain_yr": (0, 12),
    "median_hh_income": (45000, 160000), "share_hh_200k": (0.03, 0.35), "est_value_now": (200000, 1200000),
    "glass_m2": (8, 60),
}

CAVEAT_POOL = ("imagery snapshots every ~2 yrs; cleaning frequency per house is not observable from open data; "
               "area seasonality from QCEW")
CAVEAT_WINDOW = ("glass area is an estimate: LiDAR footprint perimeter x wall height x 15% typical glazing ratio; "
                 "actual window count, film and cleaning history are not observable from open data")


def lin(v, key):
    lo, hi = ANCH[key]
    if v is None:
        return None
    try:
        return max(0.0, min(1.0, (float(v) - lo) / (hi - lo)))
    except (TypeError, ValueError):
        return None


def num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj, indent):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=None if indent else (",", ":"), indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def house_list(houses):
    return houses["houses"] if isinstance(houses, dict) and "houses" in houses else houses


def parse_date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- area context (block level)
def area_context(meta, market, today):
    wt = meta.get("weather_triggers") or {}
    if not isinstance(wt, dict):
        wt = {}
    stats = wt.get("county") if isinstance(wt.get("county"), dict) else wt
    aff = meta.get("affluence") if isinstance(meta.get("affluence"), dict) else {}
    fips = str(meta.get("fips") or meta.get("county_fips") or "").zfill(5) if (meta.get("fips") or meta.get("county_fips")) else None
    mrow = None
    if market and fips:
        mrow = (market.get("counties") or {}).get(fips)

    prof = stats.get("monthly_trigger_profile") or wt.get("monthly_trigger_profile")
    prof = [num(x) or 0.0 for x in prof] if isinstance(prof, list) and len(prof) == 12 else None
    q = (mrow or {}).get("pool_svc_seasonality_q")
    q = [num(x) or 0.0 for x in q] if isinstance(q, list) and len(q) == 4 else None

    # 12-month calendar: trigger profile blended with pool-service quarterly employment, each min-max scaled to
    # 0..1 over the year (0 = the area's quietest month, 1 = its peak) so a flat series does not look "always hot"
    def mm(series, flat):
        if not series:
            return None
        lo, hi = min(series), max(series)
        if hi <= 0 or (hi - lo) <= flat * hi:
            return [0.5] * len(series)
        return [(v - lo) / (hi - lo) for v in series]
    pn, qn = mm(prof, 0.02), mm(q, 0.02)
    cal = []
    for i in range(12):
        t = pn[i] if pn else None
        s = qn[i // 3] if qn else None
        if t is not None and s is not None:
            c = 0.6 * t + 0.4 * s
        else:
            c = t if t is not None else s
        cal.append({"month": MONTHS[i], "trigger": round(t, 2) if t is not None else None,
                    "service": round(s, 2) if s is not None else None,
                    "combined": round(c, 2) if c is not None else None})

    ev_key = next((k for k in ("events_since_2020_within_25km", "events") if k in wt), None)
    events = wt.get(ev_key) if ev_key else (stats.get("events") or [])
    dust_events = []
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict):
            continue
        typ = str(e.get("type") or e.get("event_type") or e.get("kind") or "").lower()
        d = parse_date(e.get("date") or e.get("begin_date") or e.get("start"))
        km = num(e.get("km") or e.get("dist_km") or e.get("distance_km"))
        if d and ("dust" in typ or "haboob" in typ) and (km is None or km <= 25):
            dust_events.append(d)
    dust_events = sorted(set(dust_events))  # one dust day may appear as both 'dust' and 'dust_rain'

    # reference date for "recent dust": the newest imagery / sky snapshot date we can find, else today
    ref, ref_src = None, None
    for k in ("sky_date", "image_date", "imagery_date"):
        if parse_date(meta.get(k)):
            ref, ref_src = parse_date(meta.get(k)), f"meta.{k}"
            break
    if ref is None and isinstance(meta.get("sky"), dict) and parse_date(meta["sky"].get("date")):
        ref, ref_src = parse_date(meta["sky"]["date"]), "meta.sky.date"
    if ref is None:
        ref, ref_src = today, "run date (no imagery date in meta)"
    recent = [d for d in dust_events if 0 <= (ref - d).days <= 30]

    ctx = {
        "fips": fips,
        "dust_days_yr": num(stats.get("dust_days_yr")),
        "high_wind_days_yr": num(stats.get("high_wind_days_yr")),
        "dust_then_rain_yr": num(stats.get("dust_then_rain_yr")),
        "median_hh_income": aff_val(aff, "median_hh_income"),
        "share_hh_200k": aff_val(aff, "share_hh_200k"),
        "affluence_geo": aff_geo(aff),
        "affluence_label": aff.get("label"),
        "calendar": cal, "has_calendar": any(c["combined"] is not None for c in cal),
        "dust_events_25km": len(dust_events), "recent_dust": [d.isoformat() for d in sorted(recent)],
        "recent_ref": ref.isoformat(), "recent_ref_src": ref_src, "has_events": isinstance(events, list) and (bool(events) or ev_key is not None),
        "last_dust": max(dust_events).isoformat() if dust_events else None,
        "market": mrow,
        "present": {"weather_triggers": bool(wt), "affluence": bool(aff), "market_row": mrow is not None,
                    "pools_meta": bool(meta.get("pools"))},
    }
    # area parts (0..1 or None)
    dd, ww = lin(ctx["dust_days_yr"], "dust_days_yr"), lin(ctx["high_wind_days_yr"], "high_wind_days_yr")
    ctx["dust_wind"] = (0.6 * dd + 0.4 * ww) if dd is not None and ww is not None else (dd if dd is not None else ww)
    ctx["dust_rain"] = lin(ctx["dust_then_rain_yr"], "dust_then_rain_yr")
    inc, sh = lin(ctx["median_hh_income"], "median_hh_income"), lin(ctx["share_hh_200k"], "share_hh_200k")
    ctx["affluence_area"] = (0.5 * inc + 0.5 * sh) if inc is not None and sh is not None else (inc if inc is not None else sh)
    cm = cal[today.month - 1]["combined"]
    ctx["season_now"] = cm
    ctx["recent_dust_part"] = (1.0 if recent else 0.0) if ctx["has_events"] else None
    ctx["next_season"] = next_season(cal, today)
    return ctx


def aff_geo(aff):
    """Which geography to read from lane P4's affluence: block group when its income MOE is < 50% of the value,
    else tract (ACS block-group estimates are often too noisy)."""
    inc = aff.get("median_hh_income")
    if not isinstance(inc, dict):
        return None
    bg, moe = num(inc.get("block_group")), num(inc.get("block_group_moe"))
    if bg and moe is not None and moe < 0.5 * bg:
        return "block_group"
    return "tract" if num(inc.get("tract")) is not None else ("block_group" if bg else None)


def aff_val(aff, key):
    v = aff.get(key)
    if isinstance(v, dict):
        g = aff_geo(aff) or "tract"
        return num(v.get(g)) if num(v.get(g)) is not None else num(v.get("tract") or v.get("block_group"))
    return num(v)


def next_season(cal, today):
    vals = [c["combined"] for c in cal]
    if all(v is None for v in vals):
        return None
    thr = 0.7
    hot = [v is not None and v >= thr for v in vals]
    if not any(hot):
        return None
    if all(hot):
        return {"status": "now", "months": MONTHS[:], "label": "year-round",
                "threshold": "months at >= 70% of the area's annual range of the combined trigger/service index"}
    cur = today.month - 1
    if hot[cur]:
        # we are inside a peak window: report it, running from now to its end
        start = cur
        while hot[(start - 1) % 12] and (start - 1) % 12 != cur:
            start = (start - 1) % 12
        status = "now"
    else:
        start = next(((cur + k) % 12 for k in range(1, 13) if hot[(cur + k) % 12]), None)
        status = "upcoming"
        if start is None:
            return None
    months, i = [], start
    while hot[i] and len(months) < 12:
        months.append(MONTHS[i])
        i = (i + 1) % 12
    return {"status": status, "months": months, "label": f"{months[0]}-{months[-1]}" if len(months) > 1 else months[0],
            "threshold": "months at >= 70% of the area's annual range of the combined trigger/service index"}


# ---------------------------------------------------------------- house helpers
def pool_info(h):
    p = h.get("pool") if isinstance(h.get("pool"), dict) else None
    a = h.get("assessor") if isinstance(h.get("assessor"), dict) else {}
    a_pool = a.get("pool")
    a_flag = None
    if a_pool is not None:
        a_flag = a_pool if isinstance(a_pool, bool) else str(a_pool).strip().lower() not in ("", "0", "n", "no", "none", "false")
    img_present = bool(p and p.get("present"))
    present = img_present or bool(a_flag)
    src = [s for s, ok in (("imagery", img_present), ("assessor", bool(a_flag))) if ok]
    area = num(p.get("area_m2")) if p else None
    if area is None and num(a.get("pool_area")) is not None:
        area = num(a.get("pool_area")) * (0.092903 if num(a.get("pool_area")) > 120 else 1)  # sq ft -> m2 if large
    by = sorted([b for b in (p or {}).get("by_year") or [] if isinstance(b, dict) and b.get("year")],
                key=lambda b: b["year"])
    dated = [b for b in by if b.get("state") not in NO_DATA_STATES]
    latest = dated[-1] if dated else None
    last_state = str((p or {}).get("last_state") or (latest or {}).get("state") or "").lower() or None
    green_years = [int(y) for y in (p or {}).get("green_years") or [] if num(y) is not None]
    if not green_years:
        green_years = [b["year"] for b in by if str(b.get("state") or "").lower() in GREEN_STATES]
    latest_year = latest["year"] if latest else None
    return {"present": present, "sources": src, "area_m2": area, "last_state": last_state,
            "latest_year": latest_year, "latest_date": (latest or {}).get("date"),
            "confidence": num((p or {}).get("confidence")),
            "green_years": sorted(set(green_years)), "years_seen": [b["year"] for b in by],
            "assessor_only": present and not img_present, "xy": pool_xy(p)}


def pool_xy(p):
    if not p:
        return None
    for k in ("xy", "centroid", "center"):
        v = p.get(k)
        if isinstance(v, (list, tuple)) and len(v) >= 2 and num(v[0]) is not None:
            return float(v[0]), float(v[1])
    if num(p.get("x")) is not None and num(p.get("y")) is not None:
        return float(p["x"]), float(p["y"])
    r = p.get("ring") or p.get("polygon")
    if isinstance(r, list) and r and isinstance(r[0], (list, tuple)):
        try:
            return sum(q[0] for q in r) / len(r), sum(q[1] for q in r) / len(r)
        except Exception:
            return None
    return None


def tree_part(h, pinfo, trees):
    """0..1 debris pressure on the pool + description."""
    if pinfo["xy"] and trees:
        px, py = pinfo["xy"]
        pr = math.sqrt((pinfo["area_m2"] or 30) / math.pi)
        over = [t for t in trees if num(t.get("x")) is not None and
                math.hypot(t["x"] - px, t["y"] - py) <= (num(t.get("r")) or 3) + pr + 2 and (num(t.get("h")) or 0) >= 4]
        n = len(over)
        return min(1.0, 0.6 * n) if n < 2 else 1.0, f"{n} tree crown(s) within 2 m of the pool edge (LiDAR)" if n else None, "pool position"
    near = [t for t in h.get("trees_near") or [] if isinstance(t, dict) and (num(t.get("gap_m")) is not None)
            and num(t["gap_m"]) <= 2 and (num(t.get("h")) or 0) >= 4]
    if near or h.get("trees_near") is not None:
        n = len(near)
        tall = max([num(t.get("h")) or 0 for t in near] or [0])
        part = 0.0 if n == 0 else min(1.0, 0.4 + 0.25 * n + (0.15 if tall >= 10 else 0))
        # house-proximity proxy is weaker than a pool-position test
        part *= 0.8
        return part, (f"{n} tree(s) >= 4 m overhanging/within 2 m of the house (tallest {tall:.0f} m) - leaf litter likely"
                      if n else None), "house proximity proxy"
    cov = num((h.get("meta_canopy") or {}).get("cover_pct"))
    if cov is not None:
        return 0.6 * min(1.0, cov / 60), (f"{cov:.0f}% canopy cover around the house" if cov >= 30 else None), "canopy cover proxy"
    return None, None, None


def storeys(h):
    # storeys from LiDAR heights or Overture num_floors only - never from assessor records (licence policy)
    s = num((h.get("stories_check") or {}).get("lidar_estimate"))
    if s:
        return s, "LiDAR estimate"
    ov = h.get("overture") if isinstance(h.get("overture"), dict) else {}
    s = num(ov.get("num_floors"))
    if s:
        return s, "Overture num_floors"
    e = num(h.get("eave_m")) or num(h.get("height_m"))
    if e:
        return max(1.0, round(e / STOREY_M)), "eave height / 3 m"
    return None, None


def perimeter(ring):
    try:
        return sum(math.hypot(ring[i + 1][0] - ring[i][0], ring[i + 1][1] - ring[i][1]) for i in range(len(ring) - 1)) + (
            0 if ring[0] == ring[-1] else math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1]))
    except Exception:
        return None


def glass_estimate(h):
    per = perimeter(h.get("ring")) if h.get("ring") else None
    src = "LiDAR footprint perimeter"
    if not per and num(h.get("area_m2")):
        per = 4 * math.sqrt(num(h["area_m2"]))
        src = "square-footprint perimeter from area"
    wall = num(h.get("eave_m")) or (num(h.get("height_m")) * 0.8 if num(h.get("height_m")) else None)
    if not per or not wall:
        return None
    wall = max(2.4, wall)
    return {"perimeter_m": round(per, 1), "wall_h_m": round(wall, 1), "glass_m2": round(per * wall * GLAZING_RATIO, 1),
            "basis": f"{src} x eave height x {int(GLAZING_RATIO * 100)}% glazing"}


def affluence_part(h, ctx):
    mon = h.get("money") or {}
    lvl = str(mon.get("value_level") or "").lower()
    # a tract median is not this house: only a house-level value counts as the house part
    v = lin(num(mon.get("est_value_now")), "est_value_now") if lvl == "this house" else None
    a = ctx["affluence_area"]
    if v is not None and a is not None:
        return 0.5 * v + 0.5 * a, "home value + area income"
    if v is not None:
        return v, "home value"
    return a, ("area income" if a is not None else None)


def score(parts, weights):
    pts, have, reasons_pts = 0.0, 0, {}
    for k, w in weights.items():
        v = parts.get(k)
        if v is None:
            v = NEUTRAL if k in AREA_PARTS else 0.0
        else:
            have += 1
        reasons_pts[k] = round(w * v, 1)
        pts += w * v
    return round(pts), reasons_pts, round(have / len(weights), 2)


# ---------------------------------------------------------------- per-house leads
def pool_lead(h, ctx, trees):
    pi = pool_info(h)
    if not pi["present"]:
        return None
    reasons, parts = [], {}
    st = pi["last_state"]
    when = pi["latest_date"] or pi["latest_year"] or "latest"
    if st in GREEN_STATES:
        parts["green_now"] = 1.0
        reasons.append(f"pool looks green/algae in the latest image ({when})")
    elif st in ODD_STATES:
        parts["green_now"] = STATE_PART.get(st, 0.35)
        reasons.append(f"pool covered or empty in the latest image ({when}) - refill / start-up clean opportunity")
    elif st == "not_present":
        parts["green_now"] = STATE_PART["not_present"]
        reasons.append(f"pool seen earlier but no water visible in the latest image ({when}) - covered, drained or filled in")
    elif st == "turquoise":
        parts["green_now"] = STATE_PART["turquoise"]
        reasons.append(f"pool colour turquoise (off-blue) in the latest image ({when})")
    elif st:
        parts["green_now"] = STATE_PART.get(st, 0.0)
    else:
        parts["green_now"] = None
    past = [y for y in pi["green_years"] if y != pi["latest_year"]]
    if pi["years_seen"] or pi["green_years"]:
        parts["green_past"] = 0.0 if not past else (0.67 if len(past) == 1 else 1.0)
        if past:
            reasons.append(f"green in past image(s) {', '.join(map(str, past))} - history of neglect")
    else:
        parts["green_past"] = None
    tp, tr, tsrc = tree_part(h, pi, trees)
    parts["trees"] = tp
    if tr:
        reasons.append(tr)
    parts["dust_wind"] = ctx["dust_wind"]
    if ctx["dust_wind"] is not None and ctx["dust_wind"] >= 0.5:
        reasons.append(f"dusty/windy area: {ctx['dust_days_yr'] or 0:.0f} dust days, {ctx['high_wind_days_yr'] or 0:.0f} high-wind days / yr")
    ap, asrc = affluence_part(h, ctx)
    parts["affluence"] = ap
    if ap is not None and ap >= 0.6:
        reasons.append("high spend capacity (" + asrc + ")")
    parts["season"] = ctx["season_now"]
    ns = ctx["next_season"]
    if ns and ns["status"] == "now":
        reasons.append(f"in peak trigger season now ({ns['label']})")
    s, pts, cov = score(parts, POOL_W)
    if pi["assessor_only"]:
        reasons.append("pool known from assessor record only (no imagery state)")
    elif pi["confidence"] is not None and pi["confidence"] < 0.7:
        reasons.append(f"pool detection confidence only {pi['confidence']:.2f} - verify on imagery")
    vac = (h.get("local") or {}).get("vacant_or_condemned")
    if vac:
        reasons.append("listed vacant/condemned - contact owner of record; neglected pool may be a code-enforcement matter")
    order = sorted(pts, key=lambda k: -pts[k])
    return {"score": s, "points": pts, "coverage": cov, "reasons": reasons,
            "main": [k for k in order if pts[k] > 0][:3],
            "pool": {"sources": pi["sources"], "area_m2": pi["area_m2"], "last_state": st,
                     "latest_year": pi["latest_year"], "latest_date": pi["latest_date"],
                     "confidence": pi["confidence"], "green_years": pi["green_years"]},
            "tree_basis": tsrc, "next_trigger_season": ns["label"] if ns else None,
            "next_trigger_status": ns["status"] if ns else None, "note": CAVEAT_POOL}


def window_lead(h, ctx):
    ge = glass_estimate(h)
    reasons, parts = [], {}
    parts["glass"] = lin(ge["glass_m2"], "glass_m2") if ge else None
    if ge:
        reasons.append(f"~{ge['glass_m2']:.0f} m2 of glass estimated ({ge['perimeter_m']:.0f} m perimeter x {ge['wall_h_m']} m walls)")
    parts["dust_rain"] = ctx["dust_rain"]
    if ctx["dust_rain"] is not None and ctx["dust_rain"] >= 0.4:
        reasons.append(f"{ctx['dust_then_rain_yr']:.0f} dust-then-rain episodes / yr (spotting)")
    ap, asrc = affluence_part(h, ctx)
    parts["affluence"] = ap
    if ap is not None and ap >= 0.6:
        reasons.append("high spend capacity (" + asrc + ")")
    s_n, s_src = storeys(h)
    parts["stories"] = None if s_n is None else (0.2 if s_n < 1.5 else 0.7 if s_n < 2.5 else 1.0)
    if s_n and s_n >= 1.5:
        reasons.append(f"{s_n:.0f} storeys ({s_src}) - ladder/pole work, homeowners less likely to DIY")
    parts["recent_dust"] = ctx["recent_dust_part"]
    if ctx["recent_dust"]:
        reasons.append(f"dust event within 30 days of {ctx['recent_ref']} ({', '.join(ctx['recent_dust'][-3:])})")
    fs = h.get("facade_sun") or {}
    front = num((fs.get("front") or {}).get("sun_hours_yr"))
    if front and front >= 1500:
        reasons.append(f"front facade gets {front:.0f} sun-h/yr - streaks and hard-water spots show")
    s, pts, cov = score(parts, WINDOW_W)
    vac = (h.get("local") or {}).get("vacant_or_condemned")
    if vac:
        reasons.append("listed vacant/condemned - low likelihood of a paying occupant")
    order = sorted(pts, key=lambda k: -pts[k])
    return {"score": s, "points": pts, "coverage": cov, "reasons": reasons,
            "main": [k for k in order if pts[k] > 0][:3],
            "glass": ge, "stories": s_n, "stories_source": s_src, "note": CAVEAT_WINDOW}


# ---------------------------------------------------------------- block
def block_summary(houses, ctx, meta, today):
    pools = [h for h in houses if (h.get("cleaning") or {}).get("pool")]
    green = [h for h in pools if (h["cleaning"]["pool"]["pool"]["last_state"] or "") in GREEN_STATES]
    def top(kind):
        rows = sorted((h for h in houses if (h.get("cleaning") or {}).get(kind)),
                      key=lambda h: -h["cleaning"][kind]["score"])[:5]
        return [{"id": h.get("id"), "address": h.get("address"), "score": h["cleaning"][kind]["score"],
                 "reasons": h["cleaning"][kind]["reasons"][:3]} for h in rows]
    m = ctx["market"] or {}
    mp = meta.get("pools") if isinstance(meta.get("pools"), dict) else {}
    caveats = [CAVEAT_POOL, CAVEAT_WINDOW,
               "scores rank houses within an area for outreach; they are not a probability that a household buys service"]
    missing = [k for k, v in ctx["present"].items() if not v]
    if missing:
        caveats.append("inputs missing at this run (area parts scored neutral 50%): " + ", ".join(missing))
    return {
        "version": VERSION, "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "method": "usa/research/cleaning_leads_method.md",
        "houses_scored": sum(1 for h in houses if h.get("cleaning")),
        "houses_with_pools": len(pools), "green_pools_now": len(green),
        "pool_detection": {k: mp.get(k) for k in list(mp)[:8]} if mp else None,
        "top_pool_leads": top("pool"), "top_window_leads": top("window"),
        "trigger_calendar": ctx["calendar"], "next_trigger_season": ctx["next_season"],
        "area": {"dust_days_yr": ctx["dust_days_yr"], "high_wind_days_yr": ctx["high_wind_days_yr"],
                 "dust_then_rain_yr": ctx["dust_then_rain_yr"], "dust_events_25km_since_2020": ctx["dust_events_25km"], "last_dust_event": ctx["last_dust"],
                 "recent_dust_ref": ctx["recent_ref"], "recent_dust_ref_source": ctx["recent_ref_src"],
                 "median_hh_income": ctx["median_hh_income"], "share_hh_200k": ctx["share_hh_200k"],
                 "affluence_label": ctx["affluence_label"], "affluence_geo": ctx["affluence_geo"]},
        "market": {k: m.get(k) for k in ("pool_svc_estabs", "pool_svc_emp", "pool_svc_nonemployers",
                                          "window_janitorial_estabs", "svc_per_10k_households")} if m else None,
        "market_source": "web/usa/data/cleaning_market.json (Census CBP / NES, BLS QCEW)" if m else None,
        "weights": {"pool": POOL_W, "window": WINDOW_W, "neutral_for_missing_area_parts": NEUTRAL},
        "inputs_present": ctx["present"], "caveats": caveats,
    }


def input_signature(d, market):
    meta, houses = load(d / "meta.json"), load(d / "houses.json")
    if meta is None or houses is None:
        return None
    hs = house_list(houses)
    blob = {"m": {k: meta.get(k) for k in ("pools", "weather_triggers", "affluence", "fips")},
            "h": [[h.get("id"), h.get("pool"), (h.get("assessor") or {}).get("pool"), h.get("trees_near") is not None,
                   (h.get("money") or {}).get("est_value_now"), h.get("eave_m"), (h.get("local") or {}).get("vacant_or_condemned")]
                  for h in hs],
            "mk": (market or {}).get("counties", {}).get(str(meta.get("fips") or "").zfill(5)), "v": VERSION}
    return hashlib.sha1(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


def do_block(d, market, today):
    hp, mp = d / "houses.json", d / "meta.json"
    if not (hp.exists() and mp.exists()):
        return None
    trees = load(d / "trees.json")
    trees = trees if isinstance(trees, list) else None
    with block_lock(d):
        meta = json.load(open(mp, encoding="utf-8"))
        houses = json.load(open(hp, encoding="utf-8"))
        hs = house_list(houses)
        ctx = area_context(meta, market, today)
        for h in hs:
            if h.get("garage"):
                h.pop("cleaning", None)
                continue
            h["cleaning"] = {"pool": pool_lead(h, ctx, trees), "window": window_lead(h, ctx)}
        meta["cleaning"] = block_summary(hs, ctx, meta, today)
        write_json(hp, houses, None)
        write_json(mp, meta, 1)
    return meta["cleaning"]


def report(slug, c):
    print(f"== {slug}: scored {c['houses_scored']}, pools {c['houses_with_pools']}, green now {c['green_pools_now']}, "
          f"next season {(c['next_trigger_season'] or {}).get('label')}, inputs {c['inputs_present']}")
    for kind in ("top_pool_leads", "top_window_leads"):
        for r in c[kind][:5]:
            print(f"   {kind.split('_')[1]:<6} {r['score']:>3}  {r['address'] or ('house #' + str(r['id']))}  | {'; '.join(r['reasons'][:2])}")


def run_once(slugs, today, sigs):
    market = load(MARKET)
    changed = 0
    for s in slugs:
        d = BLOCKS / s
        sig = input_signature(d, market) if d.exists() else None
        if sig is None:
            if sigs.get(s) != "missing":
                print(f"-- {s}: block not built yet")
            sigs[s] = "missing"
            continue
        if sigs.get(s) == sig:
            continue
        try:
            c = do_block(d, market, today)
        except TimeoutError as e:
            print(f"-- {s}: {e}")
            continue
        sigs[s] = sig
        changed += 1
        if c:
            report(s, c)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--blocks", default=",".join(DEFAULT_BLOCKS))
    ap.add_argument("--wait-min", type=float, default=0, help="keep polling for new inputs for this many minutes")
    ap.add_argument("--poll-s", type=float, default=300)
    ap.add_argument("--today", default=None, help="override run date (YYYY-MM-DD) for seasonality")
    a = ap.parse_args()
    today = parse_date(a.today) or dt.date.today()
    slugs = ([p.name for p in sorted(BLOCKS.iterdir()) if (p / "houses.json").exists()] if a.all
             else [s for s in a.blocks.split(",") if s])
    sigs = {}
    t_end = time.time() + a.wait_min * 60
    while True:
        n = run_once(slugs, today, sigs)
        print(f"[{dt.datetime.now():%H:%M}] pass done, {n} block(s) (re)scored; market file {'present' if MARKET.exists() else 'missing'}")
        if time.time() >= t_end:
            break
        time.sleep(min(a.poll_s, max(1, t_end - time.time())))


if __name__ == "__main__":
    main()

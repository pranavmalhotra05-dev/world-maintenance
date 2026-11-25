"""Per-house local-government flags for built blocks: rental / STR licences, vacant / condemned / dangerous
buildings, HOA hints, code cases (5 years), open sale history and tax abatements.

  python usa/local_flags.py <slug> [<slug> ...]
  python usa/local_flags.py --all

Endpoints, licences and the per-city "not open" notes live in usa/local_sources.json. Green-only: a dataset is
used only when its licence explicitly allows commercial use and redistribution (commercial_ok "yes", checked in
usa/research/licence_registry_local.json); anything else is skipped and named only in the "not open" notes. Every reader below whitelists its fields:
owner / host / agent / grantor / grantee names, phones and emails are never requested or stored.
Neighbourhood associations, district councils, RNOs etc. are never flagged as HOAs.

Matching: normalised street address first; else point-in-footprint / nearest main building within 15 m
(never a garage); parcel / area polygons match buildings whose centroid lies inside. (Houses carry no parcel
id in houses.json, so the parcel-id step is used only where a source row itself has no address or point.)

Writes, per house, h.local = {rental_license, str_license, vacant_or_condemned, dangerous_building, hoa,
code_cases_5y, sales, tax_abatement} and meta.local_flags = {generated, city, status, attribution, counts}.
Both files are re-read and written under web/usa/blocks/<slug>/.lock (other lanes write the same files).
"""
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point, Polygon, box, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.adapters import for_point  # noqa: E402
from usa.adapters._common import _get, date, num  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CFG = json.loads((Path(__file__).with_name("local_sources.json")).read_text(encoding="utf-8"))
TODAY = datetime.now(timezone.utc).date()
SINCE5 = (TODAY - timedelta(days=5 * 365 + 1)).isoformat()
FIELDS = ["rental_license", "str_license", "vacant_or_condemned", "dangerous_building", "hoa", "code_cases_5y", "sales",
          "tax_abatement"]

# ------------------------------------------------------------------ addresses
SUFFIX = {"AVENUE": "AVE", "AV": "AVE", "STREET": "ST", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT",
          "PLACE": "PL", "BOULEVARD": "BLVD", "TERRACE": "TER", "TERR": "TER", "PARKWAY": "PKWY", "CIRCLE": "CIR",
          "SOUTH": "S", "NORTH": "N", "EAST": "E", "WEST": "W", "HIGHWAY": "HWY", "TRAIL": "TRL", "SQUARE": "SQ",
          "WAY": "WAY", "POINT": "PT", "COVE": "CV", "BEND": "BND", "CROSSING": "XING", "PATH": "PATH"}
NOISE = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"} | set(SUFFIX.values())


def norm(a):
    """'1515 Madison Avenue, Austin' -> '1515 MADISON AVE'; drops unit / apartment parts."""
    if not a:
        return None
    s = str(a).upper().split(",")[0]
    s = re.sub(r"\s(#|UNIT|APT|STE|SUITE|BLDG)\s?\S*.*$", "", s)
    s = re.sub(r"#\S*", "", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = " ".join(SUFFIX.get(w, w) for w in s.split())
    return s or None


def split_addr(n):
    m = re.match(r"^(\d+)[A-Z]?\s+(.+)$", n or "")
    return (int(m.group(1)), m.group(2)) if m else (None, n)


def street_key(street):
    """Longest distinctive token of a street name, for a LIKE pre-filter."""
    toks = [t for t in (street or "").split() if t not in NOISE]
    return max(toks, key=len) if toks else None


# ------------------------------------------------------------------ fetch helpers
def arc(url, bbox=None, where="1=1", fields="*", geom=True, limit=20000, page=1000):
    """ArcGIS query -> [(props, shapely geometry in lon/lat or None)]."""
    out, off = [], 0
    while True:
        p = {"where": where, "outFields": fields, "returnGeometry": "true" if geom else "false", "outSR": 4326,
             "f": "geojson" if geom else "json", "resultOffset": off, "resultRecordCount": page}
        if bbox:
            p.update({"geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                      "spatialRel": "esriSpatialRelIntersects"})
        j = _get(url.rstrip("/") + "/query", p, post=True)
        feats = j.get("features", [])
        if geom:
            batch = [((f.get("properties") or {}), shape(f["geometry"]) if f.get("geometry") else None) for f in feats]
        else:
            batch = [((f.get("attributes") or {}), None) for f in feats]
        out += batch
        more = j.get("exceededTransferLimit") or (j.get("properties") or {}).get("exceededTransferLimit")
        if not batch or len(out) >= limit or not (more or len(batch) == page):
            return out
        off += len(batch)


def soc(ds, where=None, select=None, limit=50000):
    p = {"$limit": limit}
    if where:
        p["$where"] = where
    if select:
        p["$select"] = select
    return _get(f"https://{ds['domain']}/resource/{ds['dataset']}.json", p)


def like_any(field, keys, upper="UPPER"):
    keys = sorted({k.replace("'", "''") for k in keys if k})
    return "(" + " OR ".join(f"{upper}({field}) LIKE '%{k}%'" for k in keys) + ")" if keys else "1=0"


def pt(lon, lat):
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        return None
    return Point(lon, lat) if -180 < lon < 0 and 0 < lat < 90 else None


def iso(v):
    d = date(v)
    return d if d and re.match(r"^\d{4}-\d{2}-\d{2}$", d) else None


def clean(s):
    return re.sub(r"\s+", " ", str(s)).strip() or None if s not in (None, "") else None


def R(addr=None, geom=None, out=None, case=None, when=None, kind=None):
    """One source row: address and/or geometry, the whitelisted payload, optional case id / date."""
    return {"addr": norm(addr), "geom": geom, "out": out, "case": case, "date": when, "kind": kind}


# ------------------------------------------------------------------ dataset readers (field whitelists)
def rd_mpls_sales(ds, ctx):
    w = like_any(ds["addr_field"], ctx["street_keys"])
    rows = arc(ds["url"], where=w, geom=False, fields="FORMATTED_ADDRESS,SALE_DATE,GROSS_SALE_PRICE,NUM_PARCELS_IN_SALE")
    out = []
    for p, _ in rows:
        price = num(p.get("GROSS_SALE_PRICE"))
        if not price or price < 1000:
            continue
        o = {"date": iso(p.get("SALE_DATE")), "price": price}
        if (num(p.get("NUM_PARCELS_IN_SALE")) or 1) > 1:
            o["parcels_in_sale"] = int(num(p["NUM_PARCELS_IN_SALE"]))
        out.append(R(p.get("FORMATTED_ADDRESS"), None, o))
    return out


def _status_by_date(exp):
    return None if not exp else ("expired" if exp < TODAY.isoformat() else "active")


def rd_mpls_rental(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="Address,Tier,IssueDate,ExpirationDate")
    return [R(p.get("Address"), g, {"status": _status_by_date(iso(p.get("ExpirationDate"))), "type": clean(p.get("Tier")),
                                     "issued": iso(p.get("IssueDate")), "expires": iso(p.get("ExpirationDate")),
                                     "as_of": "2016 snapshot"}) for p, g in rows]


def rd_mpls_cases(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="Display,Violation_Case_Number,Scheduled_Date,Completed_Date,Start_Date")
    out = []
    for p, g in rows:
        d = iso(p.get("Completed_Date")) or iso(p.get("Scheduled_Date")) or iso(p.get("Start_Date"))
        if d and d >= SINCE5:
            out.append(R(p.get("Display"), g, None, case=p.get("Violation_Case_Number"), when=d))
    return out


def rd_stp_vacant(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="ADDRESS,VACANT_AS_OF,DWELLING_TYPE,VB_CATEGORY")
    return [R(p.get("ADDRESS"), g, {"status": f"vacant building, category {clean(p.get('VB_CATEGORY'))}",
                                     "since": iso(p.get("VACANT_AS_OF")), "dwelling": clean(p.get("DWELLING_TYPE"))})
            for p, g in rows]


def rd_aus_rop(ds, ctx):
    rows = soc(ds, select="foldersubtype,status,issuedate,propertyname,location_1")
    out = []
    for p in rows:
        loc = p.get("location_1") or {}
        out.append(R(p.get("propertyname"), pt(loc.get("longitude"), loc.get("latitude")),
                     {"status": clean(p.get("status")), "type": clean(p.get("foldersubtype")) or "Repeat Offender Program",
                      "issued": iso(p.get("issuedate")), "expires": None}))
    return out


def rd_aus_str(ds, ctx):
    rows = soc(ds, where=like_any("prop_address", ctx["street_keys"], "upper"), select="prop_address,str_type")
    out = []
    for p in rows:
        m = re.match(r"^BLOCK OF (\d+)\s+(.+)$", (p.get("prop_address") or "").upper().strip())
        if m:
            out.append({"hundred": (int(m.group(1)) // 100 * 100, norm(m.group(2))), "type": clean(p.get("str_type"))})
    return out


def _addr_cases(ds, ctx, select, idf):
    w = like_any(ds["addr_field"], ctx["street_keys"], "upper") + f" AND {ds['date_field']} >= '{SINCE5}T00:00:00'"
    rows = soc(ds, where=w, select=f"{ds['addr_field']},{idf},{ds['date_field']}")
    return [R(p.get(ds["addr_field"]), None, None, case=p.get(idf), when=iso(p.get(ds["date_field"]))) for p in rows]


def rd_aus_code(ds, ctx):
    return _addr_cases(ds, ctx, None, "case_id")


def _den(ds, ctx, rental):
    rows = arc(ds["url"], where=like_any(ds["addr_field"], ctx["street_keys"]), geom=False,
               fields="ADDRESS,LICENSE_TYPE,LICENSE_STATUS,EXPIRATION_DATE")
    out = []
    for p, _ in rows:
        o = {"status": clean(p.get("LICENSE_STATUS")), "type": clean(p.get("LICENSE_TYPE"))}
        o["expires"] = iso(p.get("EXPIRATION_DATE"))
        out.append(R(p.get("ADDRESS"), None, o))
    return out


def rd_den_rental(ds, ctx):
    return _den(ds, ctx, True)


def rd_den_str(ds, ctx):
    return _den(ds, ctx, False)


def rd_bou_rental(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="MAINADDRESS,LICENSESTATUS,EXPIRATIONDATE,RENTALTYPE,BUILDINGTYPE,ENERGYCOMPLIANT,DWELLINGUNITSONCASE")
    out = []
    for p, g in rows:
        t = clean(p.get("RENTALTYPE")) or ""
        o = {"status": clean(p.get("LICENSESTATUS")), "type": t or None, "expires": iso(p.get("EXPIRATIONDATE")),
             "building": clean(p.get("BUILDINGTYPE")), "units": num(p.get("DWELLINGUNITSONCASE")),
             "energy_compliant": clean(p.get("ENERGYCOMPLIANT"))}
        out.append(R(p.get("MAINADDRESS"), g, o, kind="str" if "SHORT TERM" in t.upper() else "rental"))
    return out


def rd_bou_hoa(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="NAME_ALL,HoaArcRef,TYPE")
    return [R(None, g, {"name": clean(p.get("NAME_ALL")), "kind": "HOA", "confidence": "medium",
                        "note": "plat / approval references an HOA (HoaArcRef = Yes)"})
            for p, g in rows if (p.get("HoaArcRef") or "").strip().upper() == "YES"]


def rd_col_code(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="OBJECTID,B1_ALT_ID,B1_FILE_DD,SITE_ADDRESS")
    out = []
    for p, g in rows:
        d = iso(p.get("B1_FILE_DD"))
        if d and d >= SINCE5:
            cid = p.get("B1_ALT_ID") or p.get("OBJECTID")
            out.append(R(p.get("SITE_ADDRESS"), g, None, case=cid, when=d))
    return out


def rd_col_subdiv(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="PLATNAME")
    return [R(None, g, {"name": clean(p.get("PLATNAME")), "kind": "subdivision (possible covenants)", "confidence": "low"})
            for p, g in rows]


def rd_col_tif(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="NAME,ORC,PASSAGE,DTE_STATUS")
    return [R(None, g, {"type": "TIF district", "name": clean(p.get("NAME")), "statute": clean(p.get("ORC")),
                        "status": clean(p.get("DTE_STATUS")), "since": iso(p.get("PASSAGE")),
                        "note": "area incentive; check the parcel's own exemption with the county auditor"})
            for p, g in rows]


def _box_rows(ds, ctx, select):
    w, s, e, n = ctx["bbox"]
    try:
        return soc(ds, where=f"latitude::number between {s} and {n} and longitude::number between {w} and {e}", select=select)
    except Exception:  # fall back to the full (small) table and filter locally
        return [p for p in soc(ds, select=select) if pt(p.get("longitude"), p.get("latitude")) is not None
                and box(*ctx["bbox"]).contains(pt(p.get("longitude"), p.get("latitude")))]


def rd_cin_vacant(ds, ctx):
    rows = _box_rows(ds, ctx, "number_key,comp_type_desc,sub_type_desc,entered_date,data_status_display,status_class,latitude,longitude")
    out = []
    for p in rows:
        open_ = (p.get("status_class") or "").upper() != "CLOS"
        out.append(R(None, pt(p.get("longitude"), p.get("latitude")),
                     {"status": (clean(p.get("sub_type_desc")) or "vacant/foreclosed") + " - " + (clean(p.get("data_status_display")) or ""),
                      "since": iso(p.get("entered_date")), "open": open_}, case=p.get("number_key")))
    return out


def rd_cin_code(ds, ctx):
    return _addr_cases(ds, ctx, None, "number_key")


def rd_cin_abate(ds, ctx):
    rows = _box_rows(ds, ctx, "cra_id,address,application_date,construction_type,ownership_type,latitude,longitude")
    return [R(p.get("address"), pt(p.get("longitude"), p.get("latitude")),
              {"type": "CRA residential tax abatement" + (f" ({clean(p.get('construction_type')).lower()})" if clean(p.get("construction_type")) else ""),
               "since": iso(p.get("application_date")), "occupancy": clean(p.get("ownership_type")),
               "note": "Cincinnati CRA abatements run 10-15 years from completion"}) for p in rows]


def rd_cin_subdiv(ds, ctx):
    rows = arc(ds["url"], ctx["bbox"], fields="NAME,CONVEYANCETYPE,HISTORICAL,LEGALENDDATE")
    out = []
    for p, g in rows:
        if p.get("HISTORICAL") == 1 or p.get("LEGALENDDATE"):
            continue
        t = (p.get("CONVEYANCETYPE") or "").upper()
        if "CONDO" in t:
            out.append(R(None, g, {"name": clean(p.get("NAME")), "kind": "condo", "confidence": "medium"}))
        elif "SUBDIV" in t:
            out.append(R(None, g, {"name": clean(p.get("NAME")), "kind": "subdivision (possible covenants)", "confidence": "low"}))
    return out


def rd_kc_danger(ds, ctx):
    rows = soc(ds, select="casenumber,address,statusofcase,case_opened,case_location")
    out = []
    for p in rows:
        c = (p.get("case_location") or {}).get("coordinates") or [None, None]
        out.append(R(p.get("address"), pt(c[0], c[1]), {"status": clean(p.get("statusofcase")) or "dangerous building case",
                                                          "since": iso(p.get("case_opened"))}, case=p.get("casenumber")))
    return out


def rd_kc_viol(ds, ctx):
    return _addr_cases(ds, ctx, None, "casenumber")


READERS = {k[3:]: v for k, v in globals().items() if k.startswith("rd_")}


# ------------------------------------------------------------------ locking
def lock(B, wait_s=600, stale_s=900):
    p = B / ".lock"
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"local_flags {os.getpid()} {time.time()}".encode()); os.close(fd)
            return p
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > stale_s:
                    p.unlink(missing_ok=True); continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > wait_s:
                raise TimeoutError(f"lock busy: {p}")
            time.sleep(1)


# ------------------------------------------------------------------ main
CONF = {"high": 3, "medium": 2, "low": 1}


def src(ds):
    return f"{ds['name']} ({ds['licence']})"


def run(slug):
    B = BLOCKS / slug
    meta = json.load(open(B / "meta.json", encoding="utf-8"))
    if not meta.get("files"):
        print(f"{slug}: meta.json has no 'files' (block not finished) - skipped"); return None
    houses = json.load(open(B / "houses.json", encoding="utf-8"))
    lon0, lat0 = meta["origin"]
    ad = for_point(lon0, lat0)
    city = ad.__name__.rsplit(".", 1)[-1] if ad else None
    if city not in CFG["cities"]:
        print(f"{slug}: not in one of our cities - skipped"); return None
    cc = CFG["cities"][city]
    MX = 111320 * math.cos(math.radians(lat0)); MY = 110540
    w, s, e, n = meta["bounds"]; pad = 0.0005
    bbox = (w - pad, s - pad, e + pad, n + pad)
    block_box = box(w, s, e, n)

    main_idx = [i for i, h in enumerate(houses) if not h.get("garage")]
    foot = {i: Polygon(houses[i]["ring"]) for i in main_idx if len(houses[i].get("ring") or []) >= 3}
    cen = {i: Point((houses[i]["lon"] - lon0) * MX, (houses[i]["lat"] - lat0) * MY) if houses[i].get("lon") is not None
           else foot[i].centroid for i in foot}
    cen_ll = {i: Point(lon0 + c.x / MX, lat0 + c.y / MY) for i, c in cen.items()}
    by_addr, street_nums = {}, {}
    for i in main_idx:
        a = norm(houses[i].get("address"))
        if a:
            by_addr.setdefault(a, i)
            num_, st = split_addr(a)
            if num_ is not None:
                street_nums.setdefault(st, []).append(num_)
    ctx = {"bbox": bbox, "street_keys": sorted({street_key(st) for st in street_nums if street_key(st)})}

    def on_block_street(a):
        num_, st = split_addr(a or "")
        ns = street_nums.get(st)
        return bool(ns and num_ is not None and min(ns) <= num_ <= max(ns))

    def to_local(g):
        return Point((g.x - lon0) * MX, (g.y - lat0) * MY)

    def match(r):
        """-> list of house indices (main buildings only)."""
        if r["addr"] and r["addr"] in by_addr:
            return [by_addr[r["addr"]]]
        g = r["geom"]
        if g is None:
            return []
        if g.geom_type in ("Polygon", "MultiPolygon"):
            return [i for i, c in cen_ll.items() if g.contains(c)]
        q = to_local(g if g.geom_type == "Point" else g.representative_point())
        hit = [i for i, f in foot.items() if f.contains(q)]
        if hit:
            return hit[:1]
        if not cen:
            return []
        i = min(cen, key=lambda k: cen[k].distance(q))
        return [i] if cen[i].distance(q) <= 15 else []

    def in_block(r):
        g = r["geom"]
        if g is not None:
            return g.intersects(block_box)
        return on_block_street(r["addr"])

    loc = {i: {"rental_license": None, "str_license": None, "vacant_or_condemned": None, "dangerous_building": None,
               "hoa": None, "code_cases_5y": None, "sales": [], "tax_abatement": None} for i in range(len(houses))}
    cases = {i: set() for i in range(len(houses))}
    status, attribution, by_ds = {}, {}, {}
    counts = {"rental_licences_in_block": 0, "str_licences_in_block": 0, "vacant_or_condemned_in_block": 0,
              "dangerous_buildings_in_block": 0, "code_cases_5y_in_block": 0, "sales_in_block": 0,
              "hoa_areas_touching_block": 0, "tax_abatements_in_block": 0}
    code_ok = False

    def better(old, new, f):
        if old is None:
            return True
        if f == "hoa":
            return CONF.get(new.get("confidence"), 0) > CONF.get(old.get("confidence"), 0)
        act = lambda o: str(o.get("status") or "").lower() not in ("expired", "inactive", "closed", "revoked", "delinquent") and o.get("open", True)
        if act(new) != act(old):
            return act(new)
        return str(new.get("expires") or new.get("since") or "") > str(old.get("expires") or old.get("since") or "")

    for ds in cc["datasets"]:
        did, f = ds["id"], ds["field"]
        if ds.get("commercial_ok") != "yes":   # green-only rule: never read a grey / red source
            status[did] = "skipped: licence not green"; continue
        attribution[did] = {"name": ds["name"], "licence": ds["licence"], "commercial_ok": ds["commercial_ok"],
                            "url": ds.get("url") or f"https://{ds['domain']}/resource/{ds['dataset']}"}
        try:
            rows = READERS[did](ds, ctx)
            status[did] = "ok"
        except Exception as ex:
            status[did] = f"error: {type(ex).__name__}: {str(ex)[:160]}"
            print(f"  FAIL {did}: {status[did]}"); continue
        if did == "aus_str":   # hundred-block only: block-level count, never a per-house flag
            hb = {(num_ // 100 * 100, st) for st, ns in street_nums.items() for num_ in ns}
            k = sum(1 for r in rows if r["hundred"] in hb)
            counts["str_licences_on_block_hundred_blocks"] = k
            by_ds[did] = {"rows_on_block_streets": len(rows), "on_block_hundred_blocks": k}
            continue
        nin = sum(1 for r in rows if in_block(r))
        nm = 0
        for r in rows:
            hs = match(r) if ds["match"] != "count" else []
            if f == "code_cases_5y":
                for i in hs:
                    cases[i].add((did, r["case"] or id(r)))
                continue
            for i in hs:
                nm += 1
                o = dict(r["out"] or {}); o["source"] = src(ds)
                tgt = "str_license" if r.get("kind") == "str" else f
                if tgt == "sales":
                    loc[i]["sales"].append(o)
                elif tgt == "vacant_or_condemned" and o.get("open") is False:
                    continue
                elif tgt in loc[i] and better(loc[i][tgt], o, tgt):
                    o.pop("open", None)
                    loc[i][tgt] = o
            if r.get("kind") == "str" and in_block(r):
                counts["str_licences_in_block"] += 1
        if f == "code_cases_5y":
            code_ok = True
            k = len({r["case"] or id(r) for r in rows if in_block(r)})
            counts["code_cases_5y_in_block"] += k
            by_ds[did] = {"cases_in_block": k, "rows_fetched": len(rows)}
            continue
        key = {"rental_license": "rental_licences_in_block", "str_license": "str_licences_in_block",
               "vacant_or_condemned": "vacant_or_condemned_in_block", "dangerous_building": "dangerous_buildings_in_block",
               "sales": "sales_in_block", "hoa": "hoa_areas_touching_block", "tax_abatement": "tax_abatements_in_block"}[f]
        if f == "vacant_or_condemned":
            nin = sum(1 for r in rows if in_block(r) and (r["out"] or {}).get("open", True))
        if f == "rental_license" and did == "bou_rental":
            nin = sum(1 for r in rows if in_block(r) and r.get("kind") != "str")
        counts[key] = counts.get(key, 0) + nin
        by_ds[did] = {"in_block": nin, "house_matches": nm, "rows_fetched": len(rows)}
    for fld, why in cc.get("not_open", {}).items():
        status[f"{fld} (no dataset)"] = f"not open: {why}"

    for i, L in loc.items():
        if code_ok and not houses[i].get("garage"):
            L["code_cases_5y"] = len(cases[i])
        seen, ss = set(), []
        for o in sorted(L["sales"], key=lambda o: str(o.get("date") or ""), reverse=True):
            k = (o.get("date"), o.get("price"))
            if k not in seen:
                seen.add(k); ss.append(o)
        L["sales"] = ss
    hw = {fld: sum(1 for i in main_idx if (loc[i][fld] if fld != "sales" else loc[i]["sales"])) for fld in FIELDS if fld != "code_cases_5y"}
    hw["code_cases_5y"] = sum(1 for i in main_idx if loc[i]["code_cases_5y"])
    counts["houses"] = len(main_idx)
    counts["houses_with"] = hw
    lf = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00"), "city": city, "status": status,
          "attribution": attribution, "counts": counts, "by_dataset": by_ds,
          "matching": "address, else point-in-footprint / nearest main building <= 15 m (never garages); polygons by building centroid",
          "privacy": "field whitelists only; no owner, host, agent, grantor/grantee names, phones or emails"}

    # ---- write under the shared lock: re-read fresh, add only our keys, write both
    lk = lock(B)
    try:
        meta2 = json.load(open(B / "meta.json", encoding="utf-8"))
        houses2 = json.load(open(B / "houses.json", encoding="utf-8"))
        ids = {h.get("id", j): j for j, h in enumerate(houses)}
        for h in houses2:
            j = ids.get(h.get("id"))
            if j is not None:
                h["local"] = loc[j]
        meta2["local_flags"] = lf
        tmp_h, tmp_m = B / "houses.json.lf.tmp", B / "meta.json.lf.tmp"
        json.dump(houses2, open(tmp_h, "w", encoding="utf-8"), separators=(",", ":"))
        json.dump(meta2, open(tmp_m, "w", encoding="utf-8"), indent=1)
        os.replace(tmp_h, B / "houses.json"); os.replace(tmp_m, B / "meta.json")
    finally:
        lk.unlink(missing_ok=True)
    print(f"{slug} ({city}): " + ", ".join(f"{k}={v}" for k, v in status.items() if not k.endswith("(no dataset)")))
    print(f"   counts: {json.dumps({k: v for k, v in counts.items() if v})}")
    return lf


def main(args):
    if not args:
        print(__doc__); return
    slugs = sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists()) if args == ["--all"] else args
    for s in slugs:
        try:
            run(s)
        except Exception as ex:
            print(f"{s}: FAILED {type(ex).__name__}: {ex}")


if __name__ == "__main__":
    main(sys.argv[1:])

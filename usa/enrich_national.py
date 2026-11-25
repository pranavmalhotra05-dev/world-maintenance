"""National per-house and block layers for a block built by usa/build_block.py (copies the mpls/ logic, national sources only).

  python usa/enrich_national.py <slug> [--hail] [--only name1,name2]

Every source is wrapped: a failure prints FAIL and never stops the run; status lands in extras.json["status"].
Licence rule: clear green only (explicit commercial use + redistribution; usa/research/licence_registry_national.md).
Rejected and removed: Zillow ZHVI, Freddie Mac PMMS, Realtor.com, Esri-hosted copies of federal layers.
No owner/applicant names; nothing tracks people or vehicles.

  osm             OpenStreetMap (local Geofabrik state extract via usa/osm_extract.py, ODbL): street furniture, streets -> layers.pavement/alleys
  flood           FEMA NFHL flood zone per house
  spc_hail        NOAA SPC hail reports within 10 km since 2015 (data/national/hail.zip + SPC 2025 + daily 2026)
  mrms_hail       (--hail) NOAA MRMS MESH at each house, last 24 months, only days with SPC hail within 150 km (--hail-spc-km)
  trees           LiDAR tree tops (derive.py) -> trees.json, trees_near
  sun             pvlib year of sun traced over the LiDAR surface per roof (derive.py)
  orientation     facing / roof_aspect / facade_sun (orient.py) + sky.json
  stories         LiDAR storey estimate (no assessor nationally)
  naip_lawn       NAIP NDVI around each house; roof colour time series from img/<year>.jpg (NAIP)
  canopy_height   Meta/WRI 1 m canopy height (CC-BY 4.0)
  goes, fires     NASA GIBS GOES GeoColor image, NASA FIRMS VIIRS 24 h
  infra           national infrastructure (NTAD, NCES, USGS structures, EIA-860M power plants, EIA pipelines, OSM power lines,
                  USACE NID, EPA) -> infra.json, nearby, utility
  acs_tract, hmda, walkability, jobs_lodes, fhfa_hpi
  noise           BTS National Transportation Noise Map 2022/2023 (local BTS raster if present, else BTS's own NTAD tile cache)
  feeds           NWS forecast + alerts, USGS quakes, FEMA NRI, SSURGO soil, FEMA declarations -> feeds.json
  money           FFIEC/CFPB APOR (average prime offer rate) 10/15/30-yr + payment math on an ACS tract median x FHFA ZIP HPI -> money, market.json
"""
import argparse
import csv
import gzip
import io
import json
import math
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (NAT, ROOT, S, block_box_3857, block_dir, dump_json, get, load_json, load_points, quadkey,  # noqa: E402
                    raster_to_block, write_files_list)

ap = argparse.ArgumentParser()
ap.add_argument("slug")
ap.add_argument("--hail", action="store_true", help="NOAA MRMS radar hail per house (slow: downloads GRIBs)")
ap.add_argument("--hail-months", type=int, default=24)
ap.add_argument("--hail-spc-km", type=float, default=150, help="only check MRMS on days with an SPC hail report this close")
ap.add_argument("--only", default="", help="comma list of source names to (re)run")
A = ap.parse_args()
ONLY = [x.strip() for x in A.only.split(",") if x.strip()]
OUT = block_dir(A.slug)
meta = load_json(OUT, "meta.json")
if not meta:
    raise SystemExit(f"no block at {OUT}; run usa/build_block.py first")
houses = load_json(OUT, "houses.json")
layers = load_json(OUT, "layers.json", {})
_SNAP_H = json.dumps(houses, sort_keys=True, default=str); _SNAP_L = json.dumps(layers, sort_keys=True, default=str)  # for the locked merge at write
sources = load_json(OUT, "sources.json", [])
old_ex = load_json(OUT, "extras.json", {}) or {}
LON, LAT = meta["origin"]; HALF = meta["half_m"]; W, So, E, N = meta["bounds"]
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
loc = lambda lon, lat: (round((lon - LON) * MX, 2), round((lat - LAT) * MY, 2))
NOW = datetime.now(timezone.utc).isoformat(timespec="minutes")
TZ = meta.get("tz") or ("America/New_York" if LON > -87.5 else "America/Chicago" if LON > -101 else "America/Denver" if LON > -114 else "America/Los_Angeles")
TRACT = meta.get("tract"); FIPS = meta.get("county_fips"); ST = meta.get("state")
ZIP = meta.get("zip") or next((h.get("zip") for h in houses if h.get("zip")), None)
polys = [Polygon(h["ring"]) for h in houses]
hx = np.array([p.centroid.x for p in polys]); hy = np.array([p.centroid.y for p in polys])
HAILDIR = ROOT / "data" / "usa" / "hail"
ex = {k: v for k, v in old_ex.items() if k not in ("generated", "status")}
status = dict(old_ex.get("status", {}))
feeds = (load_json(OUT, "feeds.json", {}) or {}).get("feeds", [])


def safe(name, licence, updates, store=True):
    def wrap(fn):
        if ONLY and name not in ONLY:
            return fn
        t0 = time.time()
        try:
            d = fn()
            if store:
                ex[name] = {"data": d, "licence": licence, "updates": updates, "fetched": NOW}
            status[name] = "ok"; print(f"ok    {name} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            status[name] = f"{type(e).__name__}: {e}"[:240]; print(f"FAIL  {name}: {status[name]}", flush=True)
        return fn
    return wrap


def add_source(name, detail, url):
    global sources
    sources = [s for s in sources if s["name"] != name] + [{"name": name, "detail": detail, "url": url}]


def feed(name, provider, updates, licence):
    def wrap(fn):
        if ONLY and "feeds" not in ONLY:
            return fn
        global feeds
        e = {"name": name, "provider": provider, "updates": updates, "licence": licence, "fetched": NOW}
        try:
            e["data"] = fn(); e["ok"] = True; print(f"ok    feed {name}", flush=True)
        except Exception as err:  # noqa: BLE001
            e["ok"] = False; e["error"] = f"{type(err).__name__}: {err}"[:300]; print(f"FAIL  feed {name}: {e['error']}", flush=True)
        feeds = [f for f in feeds if f["name"] != name] + [e]
        status["feed: " + name] = "ok" if e["ok"] else e["error"]
        return fn
    return wrap


def km(lon, lat):
    return math.hypot((lon - LON) * MX, (lat - LAT) * MY) / 1000


# ---------------------------------------------------------------- the LiDAR surface (decoded once, lazily)
_SURF = {}


def surf():
    if _SURF:
        return _SURF
    p = load_points(OUT, meta); u = meta.get("point_unit_m", 0.02)
    px, py, pz = p["x"] * u, p["y"] * u, p["z"] / 100.0
    kind, hag = p["k"], p["h"] / 8.0
    R = 0.5; n = int(2 * HALF / R) + 1
    ci = np.clip(((px + HALF) / R).astype(int), 0, n - 1); cj = np.clip(((py + HALF) / R).astype(int), 0, n - 1)
    DSM = np.full(n * n, -np.inf); np.maximum.at(DSM, cj * n + ci, pz); DSM = DSM.reshape(n, n)
    _, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DSM), return_indices=True); DSM = DSM[iy, ix]
    g = kind == 0
    DTM = np.full(n * n, np.inf); np.minimum.at(DTM, (cj * n + ci)[g], pz[g]); DTM = DTM.reshape(n, n)
    _, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DTM), return_indices=True); DTM = ndimage.median_filter(DTM[iy, ix], 5)
    VEG = np.zeros(n * n); np.maximum.at(VEG, (cj * n + ci)[kind == 2], hag[kind == 2]); VEG = VEG.reshape(n, n)
    gx = -HALF + (np.arange(n) + 0.5) * R
    GX, GY = np.meshgrid(gx, gx)
    BLD = np.zeros((n, n), bool)
    for poly in polys:
        BLD |= contains_xy(poly, GX, GY)
    _SURF.update(px=px, py=py, pz=pz, kind=kind, hag=hag, R=R, n=n, DSM=DSM, DTM=DTM, VEG=VEG, gx=gx, GX=GX, GY=GY, BLD=BLD)
    print(f"      decoded {len(p):,} points", flush=True)
    return _SURF


def march(x0, y0, z0, dx, dy, tan_el, steps, thr):
    """Vectorised version of derive.py lit() / orient.py sunlit(): True where the sun is visible.
    x0,y0,z0: (m,) start points; dx,dy,tan_el: (k,) unit horizontal sun direction + tan(elevation). Returns (m, k) bool."""
    s_ = surf(); DSM, R, n = s_["DSM"], s_["R"], s_["n"]
    x0 = np.asarray(x0)[:, None]; y0 = np.asarray(y0)[:, None]; z0 = np.asarray(z0)[:, None]
    blocked = np.zeros((x0.shape[0], len(dx)), bool)
    for s in steps:
        x1 = x0 + dx[None] * s; y1 = y0 + dy[None] * s
        inb = (np.abs(x1) < HALF) & (np.abs(y1) < HALF)
        c = np.clip(((x1 + HALF) / R).astype(int), 0, n - 1); r = np.clip(((y1 + HALF) / R).astype(int), 0, n - 1)
        blocked |= inb & (DSM[r, c] > z0 + tan_el[None] * s + thr)
    return ~blocked


GEOFABRIK_US = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado", "CT": "connecticut",
                "DE": "delaware", "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
                "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
                "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
                "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
                "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
                "PR": "puerto-rico", "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee", "TX": "texas",
                "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming"}
OSM_NODE_TAGS = [("highway", v) for v in ("traffic_signals", "stop", "give_way", "crossing", "street_lamp", "bus_stop")] + \
    [("emergency", "fire_hydrant"), ("man_made", "surveillance"), ("power", None), ("natural", "tree")] + \
    [("amenity", v) for v in ("bench", "waste_basket", "bicycle_parking", "parking", "post_box", "drinking_water")]
OSM_STREET_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service")
OSM_WAY_TAGS = [("power", "line"), ("power", "minor_line"), ("power", "cable"), ("amenity", "parking")] + [("highway", v) for v in OSM_STREET_CLASSES]


def osm_region():
    """Geofabrik region name for this block's state (usa/osm_extract.py naming: north-america/us/<region>-latest.osm.pbf)."""
    if not ST or ST not in GEOFABRIK_US:
        raise RuntimeError(f"no Geofabrik US state extract for state {ST!r}")
    return GEOFABRIK_US[ST]


def osm_furniture_match(kind, t):
    """Public cameras only: drop man_made=surveillance with surveillance=outdoor/indoor (private)."""
    return not (t.get("man_made") == "surveillance" and t.get("surveillance") in ("outdoor", "indoor") and "power" not in t
                and not any(t.get(k) == v for k, v in OSM_NODE_TAGS if k != "man_made" and v is not None))


# ================================================================ OSM street furniture + streets
@safe("osm", "ODbL 1.0 (share-alike; keep separate) -- (c) OpenStreetMap contributors, read from the Geofabrik state extract "
             "(download.geofabrik.de, free download service, ODbL 1.0)", "daily (Geofabrik extract), refreshed when the cached extract is re-downloaded")
def osm():
    sys.path.insert(0, str(ROOT))
    from usa import osm_extract as ox
    reg = osm_region()
    d_ = 60 / MY; dl = 60 / MX
    bw = [round(W - dl, 6), round(So - d_, 6), round(E + dl, 6), round(N + d_, 6)]
    inb = lambda lon, lat: W <= lon <= E and So <= lat <= N
    feats = ox.query(reg, nodes=OSM_NODE_TAGS, ways=OSM_WAY_TAGS, bbox=bw, match=osm_furniture_match)
    d = []
    for f_ in feats:  # one pass over the +60 m box; everything but streets is then clipped back to the block itself (as before)
        t = f_["tags"]
        if f_["type"] == "n":
            if not inb(f_["lon"], f_["lat"]):
                continue
            if t.get("man_made") == "surveillance" and t.get("surveillance") in ("outdoor", "indoor"):
                continue  # private camera (only reachable here when the node also carries another wanted tag)
            d.append({"type": "node", "tags": t, "lon": f_["lon"], "lat": f_["lat"]})
        elif f_["type"] == "w":
            geom = [{"lon": a, "lat": b} for a, b in f_.get("coords") or []]
            if not geom:
                continue
            street = t.get("highway") in OSM_STREET_CLASSES
            if not street and not any(inb(g["lon"], g["lat"]) for g in geom):
                continue
            if street or t.get("power") in ("line", "minor_line", "cable") or t.get("amenity") == "parking":
                d.append({"type": "way", "tags": t, "geometry": geom})
    pts, lines, pave, alleys = [], [], [], []
    for e in d:
        t = e.get("tags", {})
        if e["type"] == "way" and t.get("highway") and e.get("geometry"):
            path = [list(loc(g["lon"], g["lat"])) for g in e["geometry"]]
            if t["highway"] == "service":
                if t.get("service") == "alley":
                    alleys.append({"path": path, "PAVED": t.get("surface"), "name": t.get("name")})
                continue
            pave.append({"path": path, "STREET_O_NAME_1": t.get("name") or t["highway"], "highway": t["highway"], "surface": t.get("surface"),
                         "lanes": t.get("lanes"), "maxspeed": t.get("maxspeed"), "source": "OpenStreetMap"})
            continue
        kind = t.get("highway") or ("fire_hydrant" if t.get("emergency") else None) or ("camera" if t.get("man_made") == "surveillance" else None) \
            or (("power_" + t["power"]) if t.get("power") else None) or t.get("amenity") or ("tree" if t.get("natural") == "tree" else "other")
        if e["type"] == "node":
            x, y = loc(e["lon"], e["lat"])
            pts.append({"x": x, "y": y, "kind": kind, "tags": {k: v for k, v in t.items() if k in ("name", "crossing", "species", "ref", "voltage", "operator", "surveillance:type", "fire_hydrant:type")}})
        elif e.get("geometry"):
            lines.append({"kind": kind, "path": [list(loc(g["lon"], g["lat"])) for g in e["geometry"]], "tags": {k: v for k, v in t.items() if k in ("voltage", "operator", "name", "capacity")}})
    counts = {}
    for p_ in pts + lines:
        counts[p_["kind"]] = counts.get(p_["kind"], 0) + 1
    layers["pavement"] = pave; layers["alleys"] = alleys
    global sources
    sources = [s_ for s_ in sources if s_["name"] != "OpenStreetMap (Overpass)"]
    src = ox.CACHE / f"{reg}-latest.osm.pbf"
    okf = ox.CACHE / (src.name + ".ok")
    fetched = (json.loads(okf.read_text()) if okf.exists() else {}).get("fetched")
    add_source("OpenStreetMap (Geofabrik extract)", f"{len(pave)} street centrelines, {len(pts)} street-furniture points; © OpenStreetMap contributors, ODbL "
               f"(Geofabrik {src.name}{', fetched ' + fetched if fetched else ''})", "openstreetmap.org/copyright")
    return {"points": pts, "lines": lines, "counts": counts, "streets": len(pave), "alleys": len(alleys),
            "attribution": "© OpenStreetMap contributors, ODbL", "source": {"provider": "Geofabrik GmbH", "url": ox._url(reg)[0], "file": src.name,
            "extract_fetched": fetched, "licence": ox.LICENCE, "terms": ox.GEOFABRIK_TERMS["url"], "verdict": ox.GEOFABRIK_TERMS["verdict"]}}


# ================================================================ FEMA flood zone
@safe("flood", "public domain (FEMA NFHL)", "as maps are revised")
def flood():
    fz = get("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query", params={
        "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326, "where": "1=1",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF", "f": "geojson"}, timeout=180).json()
    if "error" in fz:
        raise RuntimeError(str(fz["error"])[:200])
    zones = []
    for f in fz.get("features", []):
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        from shapely.ops import transform as stf
        zones.append((stf(lambda x, y, z=None: ((np.asarray(x) - LON) * MX, (np.asarray(y) - LAT) * MY), g), f["properties"]))
    for i, h in enumerate(houses):
        z = next((pp for poly, pp in zones if poly.contains(Point(hx[i], hy[i]))), None)
        h["flood_zone"] = (z["FLD_ZONE"] + (f" ({z['ZONE_SUBTY'].strip().lower()})" if z.get("ZONE_SUBTY") and z["FLD_ZONE"] == "X" else "")) if z else \
            ("X (minimal, outside mapped hazard)" if zones else None)
    add_source("FEMA National Flood Hazard Layer", "flood zone per house", "hazards.fema.gov")
    return {"polygons": len(zones), "zones": sorted({h["flood_zone"] for h in houses if h.get("flood_zone")})}


# ================================================================ SPC hail reports
_SPC = []


def spc_reports():
    """All SPC hail reports since 2015: (date, local hhmm, size_in, lon, lat, convective-day MRMS file date)."""
    if _SPC:
        return _SPC
    HAILDIR.mkdir(parents=True, exist_ok=True); (HAILDIR / "spc").mkdir(exist_ok=True)
    rows = []

    def from_wcm(df):
        for r in df.itertuples():
            try:
                d = date(int(r.yr), int(r.mo), int(r.dy)); hh = int(str(r.time).replace(":", "")[:4].ljust(4, "0"))
            except Exception:  # noqa: BLE001
                continue
            # wcm times are local standard time; MRMS 12Z file of day D+1 covers ~06:00 CST D to 06:00 CST D+1
            fday = d + timedelta(days=1) if hh >= 600 else d
            rows.append((d.isoformat(), float(r.mag), float(r.slon), float(r.slat), fday))
    with zipfile.ZipFile(NAT / "hail.zip") as zf:
        df = pd.read_csv(zf.open([n for n in zf.namelist() if n.endswith(".csv") and "MACOSX" not in n][0]), encoding="latin-1")
    from_wcm(df[(df.yr >= 2015) & (df.slat > 0)])
    last_year = int(df.yr.max())
    y = last_year + 1
    while y <= date.today().year:
        f = HAILDIR / "spc" / f"{y}_hail.csv"
        if not f.exists():
            r = get(f"https://www.spc.noaa.gov/wcm/data/{y}_hail.csv", timeout=120)
            if r.ok and r.text.startswith("om,"):
                f.write_bytes(r.content)
        if f.exists():
            from_wcm(pd.read_csv(f, encoding="latin-1")); y += 1
        else:
            break
    # remaining days: SPC daily (preliminary) reports, convective day 12Z D -> 12Z D+1
    d0 = date(y, 1, 1); days = [d0 + timedelta(i) for i in range((date.today() - d0).days)]

    def daily(d):
        f = HAILDIR / "spc" / "daily" / f"{d:%y%m%d}.csv"
        if f.exists():
            return d, f.read_bytes().decode("utf-8", "replace")
        r = get(f"https://www.spc.noaa.gov/climo/reports/{d:%y%m%d}_rpts_hail.csv", timeout=60)
        if not r.ok:
            return d, ""
        if (date.today() - d).days > 7:
            f.parent.mkdir(exist_ok=True); f.write_bytes(r.content)
        return d, r.content.decode("utf-8", "replace")
    with ThreadPoolExecutor(12) as tp:
        for d, txt in tp.map(daily, days):
            for r in csv.DictReader(io.StringIO(txt)):
                try:
                    rows.append((d.isoformat(), float(r["Size"]) / 100, float(r["Lon"]), float(r["Lat"]), d + timedelta(days=1)))
                except (KeyError, ValueError, TypeError):
                    pass
    _SPC.extend(rows)
    return _SPC


@safe("spc_hail", "public domain (NOAA SPC)", "daily (preliminary) / yearly (final)")
def spc_hail():
    out = []
    for d, sz, lo, la, _ in spc_reports():
        k_ = km(lo, la)
        if k_ <= 10:
            out.append({"date": d, "size_in": sz, "km": round(k_, 1)})
    out.sort(key=lambda r: r["date"])
    layers["spc_hail"] = out
    add_source("NOAA SPC storm reports", f"{len(out)} hail reports within 10 km since 2015", "spc.noaa.gov/wcm")
    return {"within_10km": len(out), "max_in": max((r["size_in"] for r in out), default=None), "last": out[-1] if out else None}


# ================================================================ MRMS radar hail at each house (optional, slow)
if A.hail or "mrms_hail" in ONLY:
    @safe("mrms_hail", "public domain (NOAA MRMS, attribution requested)", "daily")
    def mrms_hail():
        sys.path.insert(0, str(ROOT / "dallas"))
        import hail as dh  # dallas/hail.py: GRIB decode + MRMS key naming
        dh.W, dh.E, dh.S, dh.N = W - 0.05, E + 0.05, So - 0.05, N + 0.05
        start = date.today() - timedelta(days=int(A.hail_months * 30.44))
        fdays = sorted({fd for d, sz, lo, la, fd in spc_reports() if fd >= start and km(lo, la) <= A.hail_spc_km and fd <= date.today()})
        cache = HAILDIR / "mrms"; cache.mkdir(parents=True, exist_ok=True)
        lon_h = np.array([h["lon"] for h in houses]); lat_h = np.array([h["lat"] for h in houses])

        def one(fd):
            f = cache / f"{fd:%Y%m%d}-120000.grib2.gz"
            if not f.exists():
                r = dh.session.get(f"{dh.BUCKET}/{dh.key_for(fd)}", timeout=120)
                if r.status_code == 404:
                    lst = dh.session.get(dh.BUCKET, params={"list-type": "2", "prefix": f"{dh.PRODUCT}/{fd:%Y%m%d}/"}, timeout=60).text
                    keys = [k.split("</Key>")[0] for k in lst.split("<Key>")[1:]]
                    if not keys:
                        return fd, None
                    kk = min(keys, key=lambda k: abs(int(k[-15:-9]) - 120000))
                    r = dh.session.get(f"{dh.BUCKET}/{kk}", timeout=120)
                r.raise_for_status(); f.write_bytes(r.content)
            la, lo, m = dh.decode(f.read_bytes())
            rr = np.clip(np.round((la[0] - lat_h) / (la[0] - la[1])).astype(int), 0, len(la) - 1)
            cc = np.clip(np.round((lon_h - lo[0]) / (lo[1] - lo[0])).astype(int), 0, len(lo) - 1)
            return fd, np.minimum(m[rr, cc] / 25.4, 4.5)
        with ThreadPoolExecutor(6) as tp:
            res = [r for r in tp.map(one, fdays) if r[1] is not None]
        days = []
        for fd, v in res:
            storm = (fd - timedelta(days=1)).isoformat()
            days.append({"date": storm, "block_max_in": round(float(v.max()), 2)})
            for i, h in enumerate(houses):
                h.setdefault("_hh", [])
                if v[i] >= 0.5:
                    h["_hh"].append([storm, round(float(v[i]), 2)])
        for h in houses:
            hh = sorted(h.pop("_hh", []))
            h["hail_history"] = hh; h["hail_max_in"] = max((v for _, v in hh), default=0)
            h["hail_checked"] = {"from": start.isoformat(), "to": date.today().isoformat(), "days": len(res), "basis": f"days with SPC hail within {A.hail_spc_km:g} km"}
        layers["hail_days"] = sorted(d["date"] for d in days if d["block_max_in"] >= 0.5)
        add_source("NOAA MRMS radar hail (MESH)", f"{len(res)} storm days checked since {start} (SPC hail within {A.hail_spc_km:g} km)", "registry.opendata.aws/noaa-mrms-pds")
        return {"from": start.isoformat(), "days_checked": len(res), "days": days, "hail_days_ge_0_5in": layers["hail_days"]}


# ================================================================ trees from the LiDAR canopy (derive.py, no species nationally)
trees = []


@safe("trees", "derived from USGS 3DEP (public domain)", "with the LiDAR")
def trees_():
    s_ = surf(); VEG, BLD, gx, R = s_["VEG"], s_["BLD"], s_["gx"], s_["R"]
    veg_h = ndimage.gaussian_filter(np.where(VEG > 2.5, VEG, 0), 1.0)
    peaks = (veg_h == ndimage.maximum_filter(veg_h, size=9)) & (veg_h > 4.0) & ~BLD
    lab, _ = ndimage.label(VEG > 2.5)
    for y_, x_ in zip(*np.nonzero(peaks)):
        h_ = float(VEG[y_, x_]); crown = lab == lab[y_, x_]
        yy, xx = np.nonzero(crown[max(0, y_ - 24):y_ + 25, max(0, x_ - 24):x_ + 25])
        d = np.hypot(yy + max(0, y_ - 24) - y_, xx + max(0, x_ - 24) - x_) * R
        rad = float(np.percentile(d[d < 0.6 * h_ + 2], 70)) if (d < 0.6 * h_ + 2).any() else 2.0
        trees.append({"x": round(float(gx[x_]), 1), "y": round(float(gx[y_]), 1), "h": round(h_, 1), "r": round(min(max(rad, 1.5), 12), 1), "genus": None})
    for hi, h in enumerate(houses):
        td = [(t, polys[hi].distance(Point(t["x"], t["y"]))) for t in trees]
        close = [(t, d) for t, d in td if d < t["r"] + 3]
        h["trees_near"] = [{"h": t["h"], "crown_r": t["r"], "genus": None, "gap_m": round(d - t["r"], 1)} for t, d in sorted(close, key=lambda z: z[1])[:6]]
    dump_json(OUT, "trees.json", trees)
    return {"trees": len(trees)}


# ================================================================ sun on each roof (derive.py, vectorised)
@safe("sun", "computed (pvlib + USGS 3DEP)", "static")
def sun():
    import pvlib
    s_ = surf(); px, py, pz, kind, DSM, R, n = s_["px"], s_["py"], s_["pz"], s_["kind"], s_["DSM"], s_["R"], s_["n"]
    times = pd.date_range("2025-01-01", "2025-12-31 23:00", freq="1h", tz=TZ)
    lc = pvlib.location.Location(LAT, LON, tz=TZ, altitude=float(meta.get("z_base", 200)))
    sp = lc.get_solarposition(times); cs = lc.get_clearsky(times, model="ineichen")
    up = sp.apparent_elevation > 3; sp, cs = sp[up], cs[up]
    step = 3; sel = np.arange(0, len(sp), step)
    az = np.radians(sp.azimuth.values[sel]); el = np.radians(sp.apparent_elevation.values[sel])
    sx, sy, sz = np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)
    dx, dy = np.sin(az), np.cos(az); tan_el = np.tan(el)
    dni, dhi = cs.dni.values[sel], cs.dhi.values[sel]
    dzy, dzx = np.gradient(DSM, R)
    rng = np.random.default_rng(1)
    house_pts = np.flatnonzero(kind == 1)
    for hi, h in enumerate(houses):
        poly = polys[hi]; inner = poly.buffer(-0.4)
        inside = house_pts[contains_xy(inner if not inner.is_empty else poly, px[house_pts], py[house_pts])]
        if len(inside) < 10:
            h["sun"] = None; continue
        smp = rng.choice(inside, min(30, len(inside)), replace=False)
        c = np.clip(((px[smp] + HALF) / R).astype(int), 0, n - 1); r_ = np.clip(((py[smp] + HALF) / R).astype(int), 0, n - 1)
        nv = np.column_stack([-dzx[r_, c], -dzy[r_, c], np.ones(len(smp))]); nv /= np.linalg.norm(nv, axis=1, keepdims=True)
        cosi = nv[:, :1] * sx[None] + nv[:, 1:2] * sy[None] + nv[:, 2:3] * sz[None]  # (m, k)
        lit = march(px[smp], py[smp], pz[smp] + 0.2, dx, dy, tan_el, np.arange(1.0, 120.0, 1.0), 0.3)
        poss = cosi > 0
        diff = dhi[None] * (1 + nv[:, 2:3]) / 2
        e_sum = np.where(poss, np.where(lit, dni[None] * cosi + diff, diff), 0).sum(1)
        hours = (poss & lit).sum(1) * step
        shaded = (poss & ~lit).sum(1) / np.maximum(poss.sum(1), 1)
        kwh = e_sum * step / 1000
        roof_m2 = len(inside) / meta["points_per_m2"]
        good = kwh > 1000
        h["sun"] = {"clear_sky_kwh_m2_yr": round(float(np.median(kwh))), "direct_sun_hours_yr": int(np.median(hours)),
                    "shaded_pct": round(float(np.mean(shaded)) * 100, 1), "roof_m2": round(roof_m2, 1),
                    "good_solar_m2": round(roof_m2 * float(good.mean()), 1), "solar_kw": round(roof_m2 * float(good.mean()) * 0.6 * 0.2, 1)}
    ok = [h["sun"]["solar_kw"] for h in houses if h.get("sun")]
    return {"houses": len(ok), "solar_kw_median": float(np.median(ok)) if ok else None, "tz": TZ}


# ================================================================ orientation + facade sun + sky (orient.py)
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
compass = lambda a_: COMPASS[int(((a_ % 360) + 22.5) // 45) % 8]


@safe("orientation", "computed (OSM streets + LiDAR + pvlib)", "static")
def orientation():
    import base64
    import pvlib
    s_ = surf(); DTM, R, n = s_["DTM"], s_["R"], s_["n"]
    streets = [np.array(s["path"]) for s in layers.get("pavement", []) if len(s["path"]) >= 2]
    times = pd.date_range("2025-01-01 00:30", "2025-12-31 23:30", freq="1h", tz=TZ)
    lc = pvlib.location.Location(LAT, LON, tz=TZ, altitude=float(meta.get("z_base", 200)))
    sp = lc.get_solarposition(times); up = (sp.apparent_elevation > 2).values
    az = np.radians(sp.azimuth.values[up]); el = np.radians(sp.apparent_elevation.values[up])
    noon_side = sp.azimuth.values[up] < 180
    dx, dy, tan_el = np.sin(az), np.cos(az), np.tan(el)

    def nearest_street(x, y):
        best, bp = 1e9, None
        for pth in streets:
            a_, b_ = pth[:-1], pth[1:]; ab = b_ - a_
            t = np.clip(((x - a_[:, 0]) * ab[:, 0] + (y - a_[:, 1]) * ab[:, 1]) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
            q = a_ + ab * t[:, None]; d = np.hypot(q[:, 0] - x, q[:, 1] - y); i = int(np.argmin(d))
            if d[i] < best:
                best, bp = d[i], q[i]
        return best, bp
    mesh = json.load(open(OUT / "mesh.txt"))["houses"]
    P = np.frombuffer(base64.b64decode(mesh["pos"]), np.float32).reshape(-1, 3)
    C = np.frombuffer(base64.b64decode(mesh["col"]), np.uint8).reshape(-1, 4)
    I = np.frombuffer(base64.b64decode(mesh["idx"]), np.uint32).reshape(-1, 3)
    a, b, c = P[I[:, 0]], P[I[:, 1]], P[I[:, 2]]
    nrm = np.cross(b - a, c - a); area = np.linalg.norm(nrm, axis=1) / 2
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9); nrm[nrm[:, 2] < 0] *= -1
    tri_h = C[I[:, 0], 0].astype(int); tri_roof = C[I[:, 0], 2] == 1
    # house id per triangle: colour channel holds id % 256, so also require the triangle to sit inside that footprint
    cen = (a + b + c) / 3
    slope = np.degrees(np.arccos(np.clip(nrm[:, 2], -1, 1))); aspect = (np.degrees(np.arctan2(nrm[:, 0], nrm[:, 1])) + 360) % 360
    nf = 0
    for hi, h in enumerate(houses):
        m = (tri_h == hi % 256) & tri_roof
        if len(houses) > 256 and m.any():
            m[m] = contains_xy(polys[hi].buffer(0.5), cen[m, 0], cen[m, 1])
        if m.any():
            A_ = area[m]; flat = slope[m] < 10; asp = aspect[m]
            share = lambda lo, hi_: float(A_[(~flat) & (((asp - lo) % 360) < (hi_ - lo) % 360)].sum() / A_.sum())
            h["roof_aspect"] = {"N": round(share(315, 45) * 100), "E": round(share(45, 135) * 100), "S": round(share(135, 225) * 100),
                                "W": round(share(225, 315) * 100), "flat": round(float(A_[flat].sum() / A_.sum()) * 100),
                                "main_pitch_deg": round(float(np.median(slope[m][~flat])) if (~flat).any() else 0, 1)}
        if not streets:
            continue
        ring = np.array(h["ring"]); cx_, cy_ = ring[:-1].mean(0)
        d, sp_ = nearest_street(cx_, cy_)
        if sp_ is None:
            continue
        face_az = (math.degrees(math.atan2(sp_[0] - cx_, sp_[1] - cy_)) + 360) % 360
        best = None
        for p0, p1 in zip(ring[:-1], ring[1:]):
            e = p1 - p0; L = np.hypot(*e)
            if L < 2:
                continue
            nx, ny = e[1] / L, -e[0] / L; mid = (p0 + p1) / 2
            if (mid[0] + nx - cx_) * nx + (mid[1] + ny - cy_) * ny < 0:
                nx, ny = -nx, -ny
            ang = (math.degrees(math.atan2(nx, ny)) + 360) % 360
            diff = abs((ang - face_az + 180) % 360 - 180)
            if best is None or diff < best[0]:
                best = (diff, ang, mid, nx, ny)
        face = best[1] if best and best[0] < 50 else face_az
        h["facing"] = {"azimuth": round(face), "compass": compass(face), "street_m": round(float(d), 1)}
        nf += 1
        if best:
            _, ang, mid, nx, ny = best
            out = {}
            for side, (sx, sy, fx, fy) in {"front": (mid[0] + nx * 0.4, mid[1] + ny * 0.4, nx, ny),
                                           "back": (cx_ - (mid[0] - cx_) - nx * 0.4, cy_ - (mid[1] - cy_) - ny * 0.4, -nx, -ny)}.items():
                r_, c_ = min(n - 1, max(0, int((sy + HALF) / R))), min(n - 1, max(0, int((sx + HALF) / R)))
                z0 = DTM[r_, c_] + 2.0
                ks = np.flatnonzero((dx * fx + dy * fy) > 0.05)[::2]  # every 2nd hour, then x2 (orient.py)
                lit = march([sx], [sy], [z0], dx[ks], dy[ks], tan_el[ks], np.arange(1.0, 140.0, 1.0), 0.25)[0]
                mo = noon_side[ks]
                out[side] = {"compass": compass((math.degrees(math.atan2(fx, fy)) + 360) % 360),
                             "sun_hours_yr": int(lit.sum() * 2), "morning_h": int((lit & mo).sum() * 2), "afternoon_h": int((lit & ~mo).sum() * 2)}
            h["facade_sun"] = out

    def noon_el(day):
        t = pd.date_range(f"{day} 09:00", f"{day} 16:00", freq="1min", tz=TZ)
        s = lc.get_solarposition(t); i = int(s.apparent_elevation.values.argmax())
        return round(float(s.apparent_elevation.values[i]), 1), t[i].strftime("%H:%M")
    sky = {"lat": LAT, "lon": LON, "tz": TZ,
           "noon_elevation": {k_: noon_el(v) for k_, v in {"winter solstice": "2025-12-21", "spring equinox": "2025-03-20",
                                                         "summer solstice": "2025-06-21", "fall equinox": "2025-09-22"}.items()},
           "note": f"Elevation in degrees above the horizon at solar noon, with local clock time ({TZ})."}
    dump_json(OUT, "sky.json", sky, indent=1)
    return {"facing": nf, "streets": len(streets)}


@safe("stories", "derived from USGS 3DEP", "with the LiDAR")
def stories():
    for h in houses:
        est = max(1.0, round((h["eave_m"] - 0.6) / 2.7 * 2) / 2)
        h["stories_check"] = {"assessor": None, "lidar_estimate": est, "agrees": None,
                              **({"overture_floors": h["overture"]["num_floors"]} if (h.get("overture") or {}).get("num_floors") else {})}
    return {"houses": len(houses)}


# ================================================================ NAIP lawn health + roof colour over the NAIP years
@safe("naip_lawn", "public domain (USDA NAIP)", "every 2-3 years")
def naip():
    import planetary_computer
    from pystac_client import Client
    cx, cy, k, box = block_box_3857(LON, LAT, HALF)
    items = [i for i in Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
             .search(collections=["naip"], bbox=[W, So, E, N], sortby=[{"field": "properties.datetime", "direction": "desc"}], max_items=6).items()]
    it = next(i for i in items if len(i.assets["image"].extra_fields.get("eo:bands", [1, 2, 3, 4])) >= 4)
    n = 400
    a = raster_to_block(it.assets["image"].href, box, n, bands=[1, 2, 3, 4])
    ndvi = np.flipud((a[3] - a[0]) / np.maximum(a[3] + a[0], 1))  # row 0 = south
    gx = -HALF + (np.arange(n) + 0.5) * (2 * HALF / n); GX, GY = np.meshgrid(gx, gx)
    for h in houses:
        ring = np.array(h["ring"])
        m = contains_xy(Polygon(ring).buffer(12), GX, GY) & ~contains_xy(Polygon(ring), GX, GY)
        v = ndvi[m]
        if len(v):
            med = float(np.median(v))
            h["yard"] = {**(h.get("yard") or {}), "naip_ndvi": round(med, 3), "naip_green_pct": round(float((v > 0.3).mean()) * 100, 1),
                         "lawn_health": "lush" if med > 0.45 else "healthy" if med > 0.3 else "patchy / dry" if med > 0.15 else "little vegetation",
                         "zone": "house + 12 m (no national parcels)", "greenness_source": f"NAIP {it.properties['datetime'][:10]} NDVI"}
    # roof colour per NAIP year + likely re-roof year (osint.py logic, relative to the block's typical roof each year)
    from PIL import Image
    yrs = sorted(layers.get("img_years") or [])
    roof_rgb = {}
    for y in yrs:
        f = OUT / "img" / f"{y}.jpg"
        if not f.exists():
            continue
        im = np.asarray(Image.open(f).convert("RGB")).astype(float); SZ = im.shape[0]
        pxl = (np.arange(SZ) + 0.5) / SZ * 2 * HALF - HALF; PX, PY = np.meshgrid(pxl, -pxl)
        for i, p in enumerate(polys):
            inner = p.buffer(-0.8)
            if inner.is_empty:
                continue
            b = inner.bounds
            c0, c1 = int((b[0] + HALF) / (2 * HALF) * SZ), int((b[2] + HALF) / (2 * HALF) * SZ) + 1
            r0, r1 = int((HALF - b[3]) / (2 * HALF) * SZ), int((HALF - b[1]) / (2 * HALF) * SZ) + 1
            sub = contains_xy(inner, PX[r0:r1, c0:c1], PY[r0:r1, c0:c1])
            if sub.sum() < 20:
                continue
            roof_rgb.setdefault(i, {})[y] = np.median(im[r0:r1, c0:c1][sub], axis=0).round(1).tolist()
    block_med = {y: np.median([np.array(v[y]) for v in roof_rgb.values() if y in v], axis=0) for y in yrs if any(y in v for v in roof_rgb.values())}
    nchg = 0
    for i, h in enumerate(houses):
        ts = roof_rgb.get(i, {}); ys = sorted(ts)
        h["roof_color"] = {str(y): ts[y] for y in ys}
        rel = {y: np.array(ts[y]) / block_med[y] for y in ys}
        best, best_y = 0.0, None
        for t in range(2, len(ys) - 1):
            before = np.mean([rel[y] for y in ys[:t]], axis=0); after = np.mean([rel[y] for y in ys[t:]], axis=0)
            spread = np.mean([np.linalg.norm(rel[y] - before) for y in ys[:t]] + [np.linalg.norm(rel[y] - after) for y in ys[t:]])
            step = float(np.linalg.norm(after - before)) / max(spread, 0.05)
            if step > best:
                best, best_y = step, ys[t]
        h["roof_color_change"] = {"year": best_y, "strength": round(best, 2), "source": "NAIP"} if best_y and best > 3.0 else None
        nchg += bool(h["roof_color_change"])
    return {"date": it.properties["datetime"][:10], "gsd_m": it.properties.get("gsd"), "block_ndvi": round(float(np.nanmean(ndvi)), 3),
            "roof_color_years": yrs, "roof_color_changes": nchg}


@safe("canopy_height", "CC-BY 4.0 (Meta & World Resources Institute)", "one-off (2018-2020 imagery)")
def canopy():
    cx, cy, k, box = block_box_3857(LON, LAT, HALF)
    url = f"/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{quadkey(LON, LAT, 9)}.tif"
    n = 200
    a = np.flipud(raster_to_block(url, box, n, bands=[1], env={"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"})[0])
    gx = -HALF + (np.arange(n) + 0.5) * (2 * HALF / n); GX, GY = np.meshgrid(gx, gx)
    for h in houses:
        ring = np.array(h["ring"])
        m = contains_xy(Polygon(ring).buffer(15), GX, GY) & ~contains_xy(Polygon(ring), GX, GY)
        v = a[m]
        if len(v):
            h["meta_canopy"] = {"mean_m": round(float(v.mean()), 1), "max_m": round(float(v.max()), 1), "cover_pct": round(float((v > 3).mean()) * 100)}
    return {"block_mean_m": round(float(a.mean()), 1), "block_cover_pct": round(float((a > 3).mean()) * 100), "tile": quadkey(LON, LAT, 9)}


@safe("goes", "public domain (NOAA GOES via NASA GIBS)", "every 10 min")
def goes():
    from PIL import Image
    wms = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
    layer = "GOES-West_ABI_GeoColor" if LON < -110 else "GOES-East_ABI_GeoColor"
    cap = get(wms, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"}, timeout=180).text
    blk = cap[cap.index(f"<Name>{layer}</Name>"):]
    dim = re.search(r'<Dimension name="time"[^>]*>([^<]+)</Dimension>', blk).group(1)
    last = datetime.strptime(dim.strip().split(",")[-1].split("/")[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    bb = [round(LAT - 4), round(LON - 5), round(LAT + 4), round(LON + 5)]
    p = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": layer, "CRS": "EPSG:4326",
         "BBOX": ",".join(map(str, bb)), "WIDTH": 1250, "HEIGHT": 1000, "FORMAT": "image/jpeg"}
    for k_ in range(18):
        t = (last - timedelta(minutes=10 * k_)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = S.get(wms, params={**p, "TIME": t}, timeout=120)
        if r.ok and r.headers.get("content-type", "").startswith("image") and np.asarray(Image.open(io.BytesIO(r.content))).mean() > 3:
            (OUT / "goes.jpg").write_bytes(r.content)
            return {"source": f"NASA GIBS WMS {layer}", "time": t, "file": "goes.jpg", "bbox": [bb[1], bb[0], bb[3], bb[2]], "bytes": len(r.content)}
    raise ValueError("no non-empty GOES GeoColor frame in the last 3 h")


@safe("fires", "open (NASA FIRMS)", "~3 hours")
def fires():
    t = get("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_USA_contiguous_and_Hawaii_24h.csv", timeout=120).text
    out = []
    for r in csv.DictReader(io.StringIO(t)):
        d = km(float(r["longitude"]), float(r["latitude"]))
        if d <= 300:
            out.append({"km": round(d), "date": r["acq_date"], "time": r["acq_time"], "frp": r.get("frp"), "confidence": r.get("confidence")})
    return sorted(out, key=lambda f: f["km"])


# ---------------------------------------------------------------- infra helpers: EIA's own files + OSM power lines
EIA_LICENCE = ("public domain (US federal work). EIA Copyrights and Reuse (https://www.eia.gov/about/copyrights_reuse.php): \"U.S. government "
               "publications are in the public domain and are not subject to copyright protection. You may use and/or distribute any of our "
               "data, files, databases, reports, graphs, charts, and other information products that are on our website\"")
EIADIR = ROOT / "data" / "national" / "eia"
INFRA_SOURCES = {}


def _kv(v):
    try:
        return round(max(float(x) for x in str(v).split(";") if x.strip()) / 1000, 1) if v else None
    except ValueError:
        return None


def _eia_860m():
    """Latest EIA-860M 'Inventory of Operating Generators' workbook (eia.gov, monthly), cached for 30 days."""
    EIADIR.mkdir(parents=True, exist_ok=True)
    have = sorted(EIADIR.glob("*_generator*.xlsx"), key=lambda f: f.stat().st_mtime)
    if have and time.time() - have[-1].stat().st_mtime < 30 * 86400:
        return have[-1]
    page = get("https://www.eia.gov/electricity/data/eia860m/", timeout=120).text
    for href in re.findall(r'href="([^"]*_generator\d{4}\.xlsx)"', page):
        url = "https://www.eia.gov" + href if href.startswith("/") else href
        r = S.get(url, timeout=300)
        if r.ok and r.content[:2] == b"PK":   # some listed links redirect to an HTML page; take the first real workbook
            f = EIADIR / url.rsplit("/", 1)[1]; f.write_bytes(r.content)
            (EIADIR / (f.name + ".url")).write_text(url)
            return f
    if have:
        return have[-1]
    raise RuntimeError("no EIA-860M workbook found")


def eia_power_plants(m, lc):
    f = _eia_860m()
    df = pd.read_excel(f, "Operating", header=2)
    df = df[pd.to_numeric(df["Latitude"], errors="coerce").notna() & pd.to_numeric(df["Longitude"], errors="coerce").notna()]
    lat_, lon_ = df["Latitude"].astype(float), df["Longitude"].astype(float)
    df = df[(lon_ > W - m / MX) & (lon_ < E + m / MX) & (lat_ > So - m / MY) & (lat_ < N + m / MY)]
    out = []
    for pid, g in df.groupby("Plant ID"):
        mw = pd.to_numeric(g["Nameplate Capacity (MW)"], errors="coerce").fillna(0)
        tech = g.loc[mw.idxmax(), "Technology"] if len(g) else None
        out.append({"xy": lc(float(g["Longitude"].iloc[0]), float(g["Latitude"].iloc[0])), "name": str(g["Plant Name"].iloc[0]).strip(),
                    "plant_id": int(pid), "mw": round(float(mw.sum()), 1), "technology": tech, "generators": int(len(g))})
    uf = EIADIR / (f.name + ".url")
    url = uf.read_text() if uf.exists() else "https://www.eia.gov/electricity/data/eia860m/"
    INFRA_SOURCES["power_plants"] = {"source": f"EIA-860M Monthly Update to the Annual Electric Generator Report ({f.stem})", "url": url,
                                     "licence": EIA_LICENCE, "citation": f"Source: U.S. Energy Information Administration, Form EIA-860M ({f.stem})",
                                     "note": "operating generators >= 1 MW, grouped by plant"}
    return out


def eia_pipelines(m, lc):
    """EIA petroleum product pipelines shapefile, downloaded from eia.gov itself (EIA's Energy Atlas no longer serves it publicly)."""
    EIADIR.mkdir(parents=True, exist_ok=True)
    url = "https://www.eia.gov/maps/map_data/PetroleumProduct_Pipelines_US_EIA.zip"
    z = EIADIR / "PetroleumProduct_Pipelines_US_EIA.zip"
    if not z.exists():
        r = get(url, timeout=300); r.raise_for_status(); z.write_bytes(r.content)
    import pyogrio
    shp = next(n for n in zipfile.ZipFile(z).namelist() if n.endswith(".shp"))
    gdf = pyogrio.read_dataframe(f"/vsizip/{z.as_posix()}/{shp}").to_crs(4326)
    box_ = Polygon([(W - m / MX, So - m / MY), (E + m / MX, So - m / MY), (E + m / MX, N + m / MY), (W - m / MX, N + m / MY)])
    out = []
    for _, r_ in gdf[gdf.intersects(box_)].iterrows():
        g = r_.geometry
        for part in (g.geoms if g.geom_type == "MultiLineString" else [g]):
            out.append({"path": [lc(x, y) for x, y in part.coords],
                        "name": next((str(r_[k]) for k in ("Opername", "OPERNAME", "Pipename", "PIPENAME", "NAME") if k in r_ and r_[k]), None)})
    INFRA_SOURCES["product_pipelines"] = {"source": f"EIA Petroleum Product Pipelines ({shp.rsplit('/', 1)[-1][:-4]}, generalized national layer)",
                                          "url": url, "licence": EIA_LICENCE, "citation": "Source: U.S. Energy Information Administration (Jan 2020)"}
    return out


def osm_power_lines(m, lc):
    """OSM power=line/minor_line/cable with voltage. Uses the local Geofabrik state extract (usa/osm_extract.py) when it is
    already cached; otherwise the power lines inside the block from this run's OSM layer (radius then limited to the block)."""
    sys.path.insert(0, str(ROOT))
    out, how = [], None
    try:
        from usa import osm_extract as ox
        reg = GEOFABRIK_US.get(ST or "")
        pbf = ox.CACHE / f"{reg}-latest.osm.pbf" if reg else None
        if pbf and pbf.exists() and (ox.CACHE / (pbf.name + ".ok")).exists():
            bb = [round(W - m / MX, 4), round(So - m / MY, 4), round(E + m / MX, 4), round(N + m / MY, 4)]
            for f_ in ox.query(reg, ways=[("power", "line"), ("power", "minor_line"), ("power", "cable")], bbox=bb):
                if len(f_.get("coords") or []) >= 2:
                    t = f_["tags"]
                    out.append({"path": [lc(a, b) for a, b in f_["coords"]], "name": t.get("name") or t.get("operator"), "VOLTAGE": _kv(t.get("voltage")),
                                "OWNER": t.get("operator"), "power": t.get("power")})
            how = f"Geofabrik extract {pbf.name}, {m / 1000:g} km around the block"
    except Exception as e:  # noqa: BLE001
        print(f"        osm extract unavailable: {type(e).__name__}: {e}", flush=True)
    if how is None:
        for l_ in ((ex.get("osm") or {}).get("data") or {}).get("lines", []):
            if l_.get("kind") in ("power_line", "power_minor_line", "power_cable") and len(l_["path"]) >= 2:
                t = l_.get("tags") or {}
                out.append({"path": [[round(a, 1), round(b, 1)] for a, b in l_["path"]], "name": t.get("name") or t.get("operator"),
                            "VOLTAGE": _kv(t.get("voltage")), "OWNER": t.get("operator"), "power": l_["kind"][6:]})
        how = "OpenStreetMap lines inside the block only (no local state extract cached yet: run usa/osm_extract.py prefetch)"
    INFRA_SOURCES["transmission"] = {"source": "OpenStreetMap power=line / minor_line / cable (" + how + ")", "url": "https://www.openstreetmap.org/copyright",
                                     "licence": "ODbL 1.0", "attribution": "(c) OpenStreetMap contributors"}
    return out


# ================================================================ national infrastructure (infra.py minus city/Met Council layers)
@safe("infra", "public domain (US DOT NTAD, NCES, USGS, EIA, USACE, EPA) + OpenStreetMap power lines (ODbL 1.0, (c) OpenStreetMap contributors)", "monthly (EIA-860M) / yearly")
def infra():
    NTAD = "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    USGS = "https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer/"
    lc = lambda lon, lat: [round((lon - LON) * MX, 1), round((lat - LAT) * MY, 1)]

    def q(url, m=0, point=False):
        geom = f"{LON},{LAT}" if point else ",".join(map(str, (W - m / MX, So - m / MY, E + m / MX, N + m / MY)))
        r = get(url + "/query", params={"where": "1=1", "geometry": geom, "geometryType": "esriGeometryPoint" if point else "esriGeometryEnvelope",
                                        "inSR": 4326, "outSR": 4326, "outFields": "*", "f": "geojson", "resultRecordCount": 4000}, timeout=120)
        r.raise_for_status(); j = r.json()
        if "error" in j:
            raise RuntimeError(str(j["error"])[:160])
        return j.get("features", [])

    def name_of(p):
        for k_ in p:
            if k_.upper() in ("NAME", "SCHOOL_NAME", "SCH_NAME", "STATION_NAME", "FACILITY_NAME", "PWS_NAME", "PWSNAME", "NAME1", "FAC_NAME", "PLANT_NAME"):
                if p[k_]:
                    return str(p[k_]).strip()
        return None

    def as_points(feats, keep=()):
        out = []
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            c = shape(g).centroid if g["type"] != "Point" else Point(g["coordinates"][:2])
            out.append({"xy": lc(c.x, c.y), "name": name_of(f["properties"]), **{k_: f["properties"].get(k_) for k_ in keep if k_ in f["properties"]}})
        return out

    def as_lines(feats, keep=()):
        out = []
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]] if g["type"] == "LineString" else []
            for part in parts:
                out.append({"path": [lc(c[0], c[1]) for c in part], "name": name_of(f["properties"]), **{k_: f["properties"].get(k_) for k_ in keep if k_ in f["properties"]}})
        return out
    L, st = {}, {}

    def one(name, fn):
        try:
            L[name] = fn(); st[name] = f"ok ({len(L[name])})"; print(f"        ok   {name} {st[name]}", flush=True)
        except Exception as e:  # noqa: BLE001
            st[name] = f"{type(e).__name__}: {e}"[:200]; print(f"        FAIL {name} {st[name]}", flush=True)
    one("crossings", lambda: as_points(q(NTAD + "NTAD_Railroad_Grade_Crossings/FeatureServer/0", 2500)))
    one("transit_stops", lambda: as_points(q(NTAD + "NTAD_National_Transit_Map_Stops/FeatureServer/0", 1200)))
    one("bridges", lambda: as_points(q(NTAD + "NTAD_National_Bridge_Inventory/FeatureServer/0", 2500), keep=("YEAR_BUILT_027", "ADT_029", "DECK_COND_058", "SUPERSTRUCTURE_COND_059", "SUBSTRUCTURE_COND_060", "FACILITY_CARRIED_007", "FEATURES_DESC_006A")))
    one("tunnels", lambda: as_points(q(NTAD + "NTAD_National_Tunnel_Inventory/FeatureServer/0", 4000)))
    one("schools_public", lambda: as_points(q("https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0", 2500)))
    one("schools_private", lambda: as_points(q("https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PRIVATESCH_2122/MapServer/0", 2500)))
    one("hospitals", lambda: as_points(q(USGS + "14", 6000)))
    one("fire_ems", lambda: as_points(q(USGS + "16", 4000)))
    one("police", lambda: as_points(q(USGS + "18", 5000)))
    one("post_offices", lambda: as_points(q(USGS + "3", 3000)))
    one("wastewater_plants", lambda: as_points(q("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/FRS_Wastewater/FeatureServer/0", 25000)))
    one("power_plants", lambda: eia_power_plants(25000, lc))
    one("dams", lambda: as_points(q("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/National_Inventory_of_Dams_Public_Service/FeatureServer/0", 15000)))
    one("airports", lambda: as_points(q(NTAD + "NTAD_Aviation_Facilities/FeatureServer/0", 15000)))
    one("rail", lambda: as_lines(q(NTAD + "NTAD_North_American_Rail_Network_Lines/FeatureServer/0", 2500)))
    one("transmission", lambda: osm_power_lines(8000, lc))
    one("product_pipelines", lambda: eia_pipelines(25000, lc))
    one("water_system", lambda: [{k_: v for k_, v in f["properties"].items() if isinstance(v, (str, int, float))} for f in
                                 q("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Water_System_Boundaries/FeatureServer/0", point=True)][:1])
    hyd = [{"xy": [p["x"], p["y"]], "name": None} for p in (ex.get("osm", {}).get("data", {}) or {}).get("points", []) if p["kind"] == "fire_hydrant"]
    if hyd:
        L["hydrants"] = hyd; st["hydrants"] = f"ok ({len(hyd)}, OpenStreetMap)"

    def nearest_pt(layer, x, y):
        pts = L.get(layer) or []
        if not pts:
            return None
        d = [math.hypot(p["xy"][0] - x, p["xy"][1] - y) for p in pts]; i = int(np.argmin(d))
        return {"m": round(d[i]), "name": pts[i].get("name")}

    def nearest_line(layer, x, y):
        ls = [l_ for l_ in (L.get(layer) or []) if len(l_["path"]) >= 2]
        if not ls:
            return None
        pt = Point(x, y); best = min(ls, key=lambda l_: LineString(l_["path"]).distance(pt))
        return {"m": round(LineString(best["path"]).distance(pt)), "name": best.get("name"), **({"kv": best.get("VOLTAGE")} if "VOLTAGE" in best else {})}
    ws = (L.get("water_system") or [{}])[0]
    utility = {"water_system": name_of(ws) or ws.get("PWS_Name") or ws.get("PWSID"), "pwsid": ws.get("PWSID") or ws.get("PWS_ID")}
    for h in houses:
        r = np.array(h["ring"]); x, y = r[:-1].mean(0)
        nb = {k_: nearest_pt(k_, x, y) for k_ in ("hydrants", "crossings", "transit_stops", "schools_public", "schools_private", "fire_ems",
                                                   "police", "hospitals", "post_offices", "bridges") if k_ in L}
        nb.update({k_: nearest_line(k_, x, y) for k_ in ("rail", "transmission") if k_ in L})
        h["nearby"] = nb
        if utility["water_system"] or utility["pwsid"]:
            h["utility"] = utility
    dump_json(OUT, "infra.json", {"layers": L, "status": st, "utility": utility, "generated": NOW,
                                  "not_national": ["catch basins, parks, sewer mains/interceptors, transitways (city/regional data only)",
                                                   "Pipeline detail (PHMSA NPMS: viewer only)", "Electric substations and distribution lines (no open federal layer)"],
                                  "sources": INFRA_SOURCES})
    add_source("National infrastructure", "US DOT NTAD, NCES, USGS structures, EIA-860M + EIA pipelines, USACE NID, EPA (public domain); "
               "power lines (c) OpenStreetMap contributors, ODbL 1.0", "geodata.bts.gov, eia.gov, openstreetmap.org")
    return {"status": st, "utility": utility}


# ================================================================ Census / economy
@safe("acs_tract", "public domain (US Census ACS)", "yearly")
def acs():
    if not TRACT:
        raise ValueError("no tract in meta.json")
    var = {"B25077_001E": "median_home_value", "B25064_001E": "median_gross_rent", "B19013_001E": "median_household_income",
           "B25035_001E": "median_year_built", "B25003_001E": "occupied_units", "B25003_002E": "owner_occupied", "B25003_003E": "renter_occupied",
           "B25024_001E": "housing_units", "B25024_002E": "units_1_detached", "B25103_001E": "median_real_estate_tax", "B01003_001E": "population"}
    import os

    def finish(out):
        if out.get("occupied_units"):
            out["owner_pct"] = round(out["owner_occupied"] / out["occupied_units"] * 100, 1)
        if out.get("housing_units"):
            out["single_family_detached_pct"] = round(out["units_1_detached"] / out["housing_units"] * 100, 1)
        add_source("US Census ACS 5-year", f"tract {TRACT} ({out['survey']})", out["via"])
        return out
    key = os.environ.get("CENSUS_API_KEY")  # optional free key; the API now refuses keyless calls
    for yr in ((2024, 2023) if key else ()):
        r = S.get(f"https://api.census.gov/data/{yr}/acs/acs5", params={"get": "NAME," + ",".join(var), "for": f"tract:{TRACT[5:]}",
                                                                         "in": f"state:{TRACT[:2]} county:{TRACT[2:5]}", "key": key}, timeout=60)
        if r.ok and r.text.startswith("["):
            hdr, row = r.json()[:2]
            out = {"year": yr, "survey": f"ACS 5-year {yr - 4}-{yr}", "tract": TRACT, "name": row[0], "via": "api.census.gov"}
            for k_, v in zip(hdr, row):
                if k_ in var:
                    try:
                        fv = float(v); out[var[k_]] = None if fv < -1e6 else fv
                    except (TypeError, ValueError):
                        out[var[k_]] = None
            return finish(out)
    # keyless: the ACS table-based summary files (one national file per table), streamed and filtered to the tract
    geo = f"1400000US{TRACT}"
    tables = sorted({k_.split("_")[0] for k_ in var})
    for yr in (2024, 2023):
        def table(t):
            url = f"https://www2.census.gov/programs-surveys/acs/summary_file/{yr}/table-based-SF/data/5YRData/acsdt5y{yr}-{t.lower()}.dat"
            with S.get(url, stream=True, timeout=300) as r:
                if not r.ok:
                    return t, None
                r.encoding = "utf-8"
                it_ = r.iter_lines(decode_unicode=True)
                hdr = next(it_).split("|")
                for line in it_:
                    if line.startswith(geo + "|"):
                        return t, dict(zip(hdr, line.split("|")))
            return t, {}
        with ThreadPoolExecutor(6) as tp:
            got = dict(tp.map(table, tables))
        if any(v is None for v in got.values()):
            continue
        out = {"year": yr, "survey": f"ACS 5-year {yr - 4}-{yr}", "tract": TRACT, "via": "www2.census.gov ACS summary file"}
        for k_, name in var.items():
            t = k_.split("_")[0]
            col = f"{t}_E{k_.split('_')[1][:3]}"  # B25077_001E -> B25077_E001
            v = (got.get(t) or {}).get(col)
            try:
                fv = float(v); out[name] = None if fv < -1e6 else fv
            except (TypeError, ValueError):
                out[name] = None
        return finish(out)
    raise RuntimeError("ACS summary files unavailable")


@safe("hmda", "public (CFPB HMDA)", "yearly")
def hmda():
    # the data-browser API no longer filters aggregations by tract (it requires exactly one of states/counties/msamds/leis),
    # so this is the COUNTY total, labelled as such
    out = {}
    for y in (2025, 2024, 2023):
        r = S.get("https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations", params={"years": y, "counties": FIPS, "actions_taken": 1}, timeout=60)
        if r.ok:
            ag = r.json().get("aggregations", [])
            if ag:
                out[y] = {"loans": ag[0].get("count"), "total_usd": ag[0].get("sum")}
    if not out:
        raise ValueError("no HMDA aggregations for county")
    return {"level": "county", "county_fips": FIPS, "county": meta.get("county"), "originations": out}


@safe("walkability", "public domain (EPA)", "static (2021)")
def sld():
    d = get("https://geodata.epa.gov/arcgis/rest/services/OA/SmartLocationDatabase/MapServer/14/query",
            params={"geometry": f"{LON},{LAT}", "geometryType": "esriGeometryPoint", "inSR": 4326, "outFields": "*", "returnGeometry": "false", "f": "json"}, timeout=60).json()
    a = d["features"][0]["attributes"]
    return {k_: a.get(k_) for k_ in ("GEOID20", "NatWalkInd", "D3B", "D4A", "D2A_EPHHM", "Pct_AO0", "AutoOwn0", "TotPop", "CountHU", "HH")}


@safe("jobs_lodes", "public domain (Census LEHD)", "yearly")
def lodes():
    st = (ST or "").lower()
    for yr in (2023, 2022, 2021):
        r = S.get(f"https://lehd.ces.census.gov/data/lodes/LODES8/{st}/wac/{st}_wac_S000_JT00_{yr}.csv.gz", timeout=300)
        if r.ok:
            rows = [x for x in csv.DictReader(io.StringIO(gzip.decompress(r.content).decode())) if x["w_geocode"].startswith(TRACT)]
            return {"year": yr, "tract": TRACT, "jobs_in_tract": sum(int(x["C000"]) for x in rows), "blocks_with_jobs": len(rows)}
    raise ValueError("no LODES file")


NOISE_LICENCE = ("public domain (US DOT BTS federal work, 17 U.S.C. 105; the noise map is part of NTAD: \"This NTAD dataset is a work of the "
                 "United States government as defined in 17 U.S.C. 101 and as such are not protected by any U.S. copyrights. This work is "
                 "available for unrestricted public use.\")")
NOISE_CLASSES = [((255, 193, 7), 45, 50), ((255, 128, 0), 50, 55), ((255, 0, 0), 55, 60), ((255, 51, 153), 60, 70),
                 ((163, 0, 204), 70, 80), ((82, 0, 204), 80, 90), ((0, 0, 255), 90, None)]   # BTS legend, LAeq 24 h dBA
NOISE_DIR = ROOT / "data" / "national" / "noise"


@safe("noise", NOISE_LICENCE, "BTS releases every ~2 years (2022/2023 current)")
def noise():
    caveat = ("BTS: developed for national-level analysis with simplified modelling (no shielding by barriers or terrain); "
              "intended for tracking trends, not for evaluating noise at an individual location.")
    page = "https://www.bts.gov/geospatial/national-transportation-noise-map"
    # 1. a local copy of the BTS raster download (exact dB), if someone has fetched it from the BTS page
    tifs = sorted(NOISE_DIR.glob("**/*rail_road_and_aviation*.tif")) if NOISE_DIR.exists() else []
    if tifs:
        import rasterio
        from rasterio.warp import transform as wtf
        with rasterio.open(tifs[-1]) as src:
            xs, ys = wtf("EPSG:4326", src.crs, [LON], [LAT]); r_, c_ = src.index(xs[0], ys[0])
            win = src.read(1, window=((r_ - 3, r_ + 4), (c_ - 3, c_ + 4)), boundless=True, fill_value=src.nodata or 0).astype(float)
            if src.nodata is not None:
                win[win == src.nodata] = np.nan
            v = win[3, 3]
        return {"db_laeq_24h": None if np.isnan(v) else round(float(v), 1),
                "max_within_100m_db": None if np.all(np.isnan(win)) else round(float(np.nanmax(win)), 1),
                "source": f"US DOT BTS National Transportation Noise Map (road + rail + aviation, LAeq 24 h), local raster {tifs[-1].name}",
                "url": page, "floor_db": 45, "caveat": caveat}
    # 2. BTS's own published tile cache of the same map (owner USDOT_BTS); pixel colour -> BTS legend class
    from PIL import Image
    yr = "2022"
    reg = "Alaska" if ST == "AK" else "Hawaii" if ST == "HI" else "CONUS"
    base = f"https://tiles.arcgis.com/tiles/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Noise_{yr}_{reg}_aviation_rail_road/MapServer"
    z = 12; n2 = 2 ** z
    fx = (LON + 180) / 360 * n2; fy = (1 - math.log(math.tan(math.radians(LAT)) + 1 / math.cos(math.radians(LAT))) / math.pi) / 2 * n2
    t = get(f"{base}/tile/{z}/{int(fy)}/{int(fx)}", timeout=60); t.raise_for_status()
    a = np.asarray(Image.open(io.BytesIO(t.content)).convert("RGBA")).astype(int)
    px, py = int((fx % 1) * 256), int((fy % 1) * 256)

    def cls(rgba):
        if rgba[3] < 128:
            return None
        return min(NOISE_CLASSES, key=lambda c: sum((rgba[i] - c[0][i]) ** 2 for i in range(3)))
    here = cls(a[py, px])
    res_m = 40075016.686 * math.cos(math.radians(LAT)) / n2 / 256
    k = max(1, int(round(100 / res_m)))
    win = [cls(a[j, i]) for j in range(max(py - k, 0), min(py + k + 1, 256)) for i in range(max(px - k, 0), min(px + k + 1, 256))]
    top = max((c for c in win if c), key=lambda c: c[1], default=None)
    rng = lambda c: f"{c[1]}-{c[2] - 0.1:g}" if c[2] else f">{c[1]}"
    return {"db_laeq_24h_class": rng(here) if here else "<45 (below the map floor)", "db_laeq_24h": None,
            "db_min": here[1] if here else None, "db_max": here[2] if here else 45,
            "max_within_100m_db_class": rng(top) if top else "<45",
            "source": f"US DOT BTS National Transportation Noise Map {yr}/2023 (road + rail + aviation, LAeq 24 h), BTS NTAD tile service "
                      f"NTAD_Noise_{yr}_{reg}_aviation_rail_road (owner USDOT_BTS), legend class at ~{res_m:.0f} m/pixel",
            "url": page, "service": base, "floor_db": 45, "caveat": caveat,
            "exact_values": f"exact dB: download the BTS raster from {page} into {NOISE_DIR.relative_to(ROOT).as_posix()}/ and re-run --only noise"}


FHFA_LICENCE = ("public domain (FHFA). FHFA Copyright and Trademark Notice (https://www.fhfa.gov/about/fhfa-policies/website-privacy-policy): "
                "\"Generally, information and materials produced by Federal agencies, including FHFA, are in the public domain and may be "
                "copied and distributed without permission.\" (FHFA House Price Index is a registered mark: cited, not branded.)")


@safe("fhfa_hpi", FHFA_LICENCE, "yearly")
def fhfa():
    r = get("https://www.fhfa.gov/hpi/download/annual/hpi_at_zip5.xlsx", timeout=300); r.raise_for_status()
    raw = pd.read_excel(io.BytesIO(r.content), header=None, dtype=str)
    hr = next(i for i in range(30) if any(str(v).strip().lower() == "year" for v in raw.iloc[i]))
    df = raw.iloc[hr + 1:].copy(); df.columns = [str(c).strip() for c in raw.iloc[hr]]
    zc = next(c for c in df.columns if "zip" in c.lower()); yc = next(c for c in df.columns if c.lower() == "year")
    hc = "HPI" if "HPI" in df.columns else next(c for c in df.columns if c.upper().startswith("HPI"))
    z = df[df[zc].astype(str).str.strip().str.split(".").str[0].str.zfill(5) == ZIP]
    ser = []
    for y, v in zip(z[yc], z[hc]):
        try:
            ser.append([int(float(y)), round(float(v), 2)])
        except (TypeError, ValueError):
            pass
    ser = [s_ for s_ in sorted(ser) if not math.isnan(s_[1])]
    if not ser:
        raise ValueError(f"ZIP {ZIP} not in FHFA file")
    by = dict(ser); y0 = ser[-1][0] - 10
    return {"zip": ZIP, "series": ser[-30:], "change_10y_pct": round((ser[-1][1] / by[y0] - 1) * 100, 1) if y0 in by else None,
            "base": hc, "latest_year": ser[-1][0]}


# ================================================================ feeds.json (national subset of mpls/feeds.py)
@feed("Weather forecast (hourly, 48 h) + active alerts", "NOAA National Weather Service", "hourly / live", "public domain")
def nws():
    pt = get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}", timeout=60).json()["properties"]
    hourly = get(pt["forecastHourly"], timeout=60).json()["properties"]["periods"][:48]
    alerts = get(f"https://api.weather.gov/alerts/active?point={LAT:.4f},{LON:.4f}", timeout=60).json().get("features", [])
    return {"office": pt.get("cwa"), "hourly": [{"t": h["startTime"][:16], "temp_f": h["temperature"], "wind": h["windSpeed"], "dir": h["windDirection"],
                                                  "pop": (h.get("probabilityOfPrecipitation") or {}).get("value"), "sky": h["shortForecast"]} for h in hourly],
            "alerts": [{"event": a["properties"]["event"], "severity": a["properties"]["severity"], "headline": a["properties"]["headline"],
                        "ends": a["properties"].get("ends")} for a in alerts]}


@feed("Earthquakes, last 30 days, M2.5+ within 1,500 km", "USGS Earthquake Hazards Program", "every minute", "public domain")
def quakes():
    d = get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_month.geojson", timeout=60).json()["features"]
    out = [{"mag": f["properties"]["mag"], "place": f["properties"]["place"], "km": round(km(*f["geometry"]["coordinates"][:2])),
            "time": datetime.fromtimestamp(f["properties"]["time"] / 1000, timezone.utc).isoformat(timespec="minutes")}
           for f in d if km(*f["geometry"]["coordinates"][:2]) <= 1500]
    return sorted(out, key=lambda q: q["km"])


@feed("National Risk Index for this census tract (18 hazards)", "FEMA National Risk Index", "yearly (static)", "public domain")
def nri():
    url = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0/query"
    a = get(url, params={"geometry": f"{LON},{LAT}", "geometryType": "esriGeometryPoint", "inSR": 4326, "outFields": "*", "returnGeometry": "false", "f": "json"}, timeout=120).json()["features"][0]["attributes"]
    keys = {"RISK_RATNG": "overall risk", "SOVI_RATNG": "social vulnerability", "RESL_RATNG": "community resilience", "HAIL_RISKR": "hail", "TRND_RISKR": "tornado",
            "SWND_RISKR": "strong wind", "WNTW_RISKR": "winter weather", "CWAV_RISKR": "cold wave", "HWAV_RISKR": "heat wave", "IFLD_RISKR": "inland flood",
            "CFLD_RISKR": "coastal flood", "HRCN_RISKR": "hurricane", "ERQK_RISKR": "earthquake", "LTNG_RISKR": "lightning", "WFIR_RISKR": "wildfire",
            "DRGT_RISKR": "drought", "ICES_RISKR": "ice storm", "LNDS_RISKR": "landslide"}
    return {"tract": a.get("TRACTFIPS"), "ratings": {v: a.get(k_) for k_, v in keys.items() if a.get(k_)}}


@feed("Soil under the block (drainage, hydrologic group, texture)", "USDA NRCS Soil Data Access (SSURGO)", "yearly", "public domain")
def soil():
    q = (f"SELECT mu.muname, c.compname, c.comppct_r, c.drainagecl, c.hydgrp, c.taxorder FROM mapunit mu "
         f"INNER JOIN component c ON c.mukey = mu.mukey WHERE mu.mukey IN (SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({LON} {LAT})')) "
         f"ORDER BY c.comppct_r DESC")
    t = S.post("https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest", data={"query": q, "format": "JSON+COLUMNNAME"}, timeout=120).json()["Table"]
    return [dict(zip(t[0], r)) for r in t[1:6]]


@feed(f"Disaster declarations for {meta.get('county') or 'this county'}", "OpenFEMA", "as declared (daily)", "public domain")
def declarations():
    d = get("https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries", params={"$filter": f"state eq '{ST}' and fipsCountyCode eq '{FIPS[2:]}'",
            "$orderby": "declarationDate desc", "$top": 25, "$select": "declarationDate,incidentType,declarationTitle,disasterNumber"}, timeout=120).json()["DisasterDeclarationsSummaries"]
    return [{"date": x["declarationDate"][:10], "type": x["incidentType"], "title": x["declarationTitle"], "number": x["disasterNumber"]} for x in d]


# ---- clear-green clean-up: drop rejected sources (Zillow ZHVI, Freddie Mac PMMS, Realtor.com) left by earlier runs
ex.pop("zhvi", None); status.pop("zhvi", None)
feeds = [f for f in feeds if not f["name"].startswith("Home values")]
for k_ in [k_ for k_ in status if k_.startswith("feed: Home values")]:
    status.pop(k_)
sources = [s_ for s_ in sources if not re.search(r"zillow|realtor|freddie|pmms|\bFRED\b", " ".join(str(s_.get(k_, "")) for k_ in ("name", "detail", "url")), re.I)]
if "fhfa_hpi" in ex:
    ex["fhfa_hpi"]["licence"] = FHFA_LICENCE

# ---- FEMA terms: required not-endorsed notices + citation with access date on the NRI / OpenFEMA feed entries
FEMA_NOTICE = {
    "National Risk Index": ("This product uses the Federal Emergency Management Agency's National Risk Index dataset API or downloadable datasets "
                            "but is not endorsed by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these "
                            "data after the data have been retrieved from the Agency's website(s).",
                            "FEMA National Risk Index, census tracts (National_Risk_Index_Census_Tracts FeatureServer, FEMA ArcGIS Online)",
                            "https://hazards.fema.gov/nri/"),
    "Disaster declarations": ("This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed by FEMA. The Federal "
                              "Government or FEMA cannot vouch for the data or analyses derived from these data after the data have been "
                              "retrieved from the Agency's website(s).",
                              "OpenFEMA Dataset: Disaster Declarations Summaries - v2, https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries",
                              "https://www.fema.gov/about/openfema/terms-conditions"),
}


def fema_notices():
    for f in feeds:
        for key, (notice, ds, url) in FEMA_NOTICE.items():
            if f["name"].startswith(key):
                f["licence"] = "public domain (FEMA); FEMA terms: not-endorsed notice + citation with access date required"
                f["notice"] = notice
                f["citation"] = f"{ds}. Federal Emergency Management Agency. Accessed {str(f.get('fetched') or NOW)[:16].replace('T', ' ')} UTC. {url}"


# ================================================================ money (money.py, no assessor nationally)
APOR_URL = "https://files.ffiec.cfpb.gov/apor/YieldTableFixed.txt"
APOR_LABEL = "APOR (average prime offer rate, used by federal regulators)"
APOR_LICENCE = ("public domain (US federal work). CFPB Website Privacy Policy and Legal Notices (https://www.consumerfinance.gov/privacy/website-privacy-policy/): "
                "\"Information created by the CFPB is in the public domain and you may reproduce, publish, or otherwise use it without the Bureau's permission.\"")


def apor_rates(get_):
    """Latest weekly Average Prime Offer Rates for fixed-rate loans (10-, 15-, 30-yr terms) from the FFIEC/CFPB rate-spread tables
    (https://ffiec.cfpb.gov/tools/rate-spread). Pipe-delimited: 'Term of Loan in Years|1|...|50', one row per week (MM/DD/YYYY)."""
    t = get_(APOR_URL, timeout=120).text.strip().splitlines()
    hdr = [h.strip() for h in t[0].split("|")]
    for line in reversed(t[1:]):
        v = [x.strip() for x in line.split("|")]
        try:
            row = dict(zip(hdr, v)); mo, dd, yy = v[0].split("/")
            return {"date": f"{yy}-{int(mo):02d}-{int(dd):02d}", "r30": float(row["30"]), "r15": float(row["15"]), "r10": float(row["10"]),
                    "label": APOR_LABEL, "source": "FFIEC / CFPB Average Prime Offer Rates, fixed-rate table (weekly)"}
        except (KeyError, ValueError):
            continue
    return {}


@safe("money", "public domain (FFIEC/CFPB APOR + US Census ACS + FHFA HPI)", "weekly rates")
def money():
    rates = apor_rates(get)
    acs_d = (ex.get("acs_tract") or {}).get("data") or {}
    fh = (ex.get("fhfa_hpi") or {}).get("data") or {}
    base = acs_d.get("median_home_value"); idx, basis = 1.0, None
    if base and fh.get("series"):
        ser = dict(fh["series"]); y0 = acs_d["year"]
        if y0 in ser:
            idx = ser[fh["latest_year"]] / ser[y0]; basis = f"FHFA ZIP {ZIP} HPI {y0}->{fh['latest_year']}"
    pmt = lambda P_, rp, yrs: P_ * (rp / 1200) / (1 - (1 + rp / 1200) ** (-yrs * 12))
    tax = acs_d.get("median_real_estate_tax")
    n_ = 0
    if base:
        est = round(base * idx, -3)
        for h in houses:
            if h.get("garage"):
                continue
            m = {"est_value_now": est, "value_level": "census tract median (not this house: no national assessor data)",
                 "value_basis": f"ACS {acs_d.get('survey')} tract median value" + (f" x {basis}" if basis else " (no index adjustment)"),
                 "index_adj": round(idx, 3), "tax_yr": tax, "tax_basis": "ACS tract median real-estate tax" if tax else None}
            if rates:
                loan = est * 0.8; tm = (tax or 0) / 12
                m["payments_20pct_down"] = {"down": round(est * 0.2), "loan": round(loan), "rates": rates,
                                            "y10": round(pmt(loan, rates["r10"], 10) + tm), "y15": round(pmt(loan, rates["r15"], 15) + tm),
                                            "y30": round(pmt(loan, rates["r30"], 30) + tm),
                                            "includes": "principal + interest" + (" + tract median tax" if tax else "") + "; insurance not included",
                                            "y10_rate_note": "10-yr uses the 10-yr fixed APOR"}
            h["money"] = m; n_ += 1
    dump_json(OUT, "market.json", {"rates": {**rates, "url": APOR_URL, "licence": APOR_LICENCE,
                                             "note": "APOR is the benchmark regulators use for HMDA rate spreads and QM/HPML tests; an average prime "
                                                     "offer, not a quote. Computed by the CFPB from a commercial rate survey; only the published APOR values are used."},
                                   "rates_label": APOR_LABEL, "tract_median_value": base, "index_adj": round(idx, 3), "index_basis": basis,
                                   "index_licence": FHFA_LICENCE if basis else None,
                                   "rejected_sources_note": "Not used (licence not clear-green): Freddie Mac PMMS, Zillow ZHVI, Realtor.com inventory."}, indent=1)
    add_source("Mortgage rates: APOR (FFIEC/CFPB average prime offer rate)", f"{rates.get('date')}: 10-yr {rates.get('r10')}%, 15-yr {rates.get('r15')}%, "
               f"30-yr {rates.get('r30')}% (public domain)", "ffiec.cfpb.gov/tools/rate-spread")
    return {"houses": n_, "rates": rates, "value": round(base * idx, -3) if base else None}


# ================================================================ write
fema_notices()
ex_out = {"generated": NOW, "status": status, **ex}


def _merge(mine, snap, fresh):
    """Apply only what this run changed (vs its start snapshot) onto the file as it is now, so concurrent lanes' edits survive."""
    if isinstance(mine, dict) and isinstance(snap, dict) and isinstance(fresh, dict):
        out = dict(fresh)
        for k_ in set(mine) | set(snap):
            if k_ not in mine:
                out.pop(k_, None)
            elif k_ not in snap or json.dumps(mine[k_], sort_keys=True, default=str) != json.dumps(snap[k_], sort_keys=True, default=str):
                out[k_] = mine[k_]
        return out
    return mine


sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402
from common import _np_default as _jd  # noqa: E402
with block_lock(OUT):
    snap_h, snap_l = json.loads(_SNAP_H), json.loads(_SNAP_L)
    fresh_h = load_json(OUT, "houses.json") or []
    if len(fresh_h) == len(houses) == len(snap_h):
        houses = [_merge(json.loads(json.dumps(h, default=_jd)), s0, f0) for h, s0, f0 in zip(houses, snap_h, fresh_h)]
    else:
        print("WARN houses.json changed length during the run; writing this run's copy", flush=True)
    layers = _merge(json.loads(json.dumps(layers, default=_jd)), snap_l, load_json(OUT, "layers.json", {}) or {})
    dump_json(OUT, "houses.json", houses)
    dump_json(OUT, "extras.json", ex_out)
    dump_json(OUT, "layers.json", layers)
    dump_json(OUT, "sources.json", sources, indent=1)
    dump_json(OUT, "feeds.json", {"generated": NOW, "feeds": feeds})
    _m = load_json(OUT, "meta.json", {})   # = common.write_files_list, minus the lock file we are holding
    _fl = sorted(q_.relative_to(OUT).as_posix() for q_ in OUT.rglob("*") if q_.is_file() and q_.name != ".lock")
    _m["files"] = _fl if "meta.json" in _fl else _fl + ["meta.json"]
    dump_json(OUT, "meta.json", _m)
okn = sum(1 for v in status.values() if v == "ok" or str(v).startswith("ok"))
print(f"\n{okn}/{len(status)} sources ok")
for k_, v in status.items():
    if v != "ok":
        print(f"  FAIL {k_}: {v}")
tot = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
big = max(OUT.rglob("*"), key=lambda f: f.stat().st_size if f.is_file() else 0)
print(f"block total {tot / 1e6:.1f} MB; largest {big.name} {big.stat().st_size / 1e6:.1f} MB")

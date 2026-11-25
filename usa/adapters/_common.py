"""Shared helpers for the city open-data adapters (ArcGIS REST, Socrata SoQL, record shaping).

Every adapter returns plain dicts with a fixed key set (see ASSESSOR_KEYS / PERMIT_KEYS / CASE_KEYS).
Personal data (owner, applicant, contractor names, phone, email) is never copied: the shapers below
only take the whitelisted keys.
"""
import csv
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

S = requests.Session()
S.headers["User-Agent"] = "roof-intel-open-data/1.0"

ASSESSOR_KEYS = ["lon", "lat", "address", "year_built", "stories", "living_area_sqft", "roof", "exterior_wall", "garage",
                 "bedrooms", "baths", "land_value", "building_value", "total_value", "sale_date", "sale_price", "zoning",
                 "property_type"]
PERMIT_KEYS = ["lon", "lat", "address", "date", "type", "work", "status", "value", "comments"]
CASE_KEYS = ["lon", "lat", "date", "type", "title", "status"]
NOT_OPEN = "not open"
REMOVED = "removed: licence not explicit"
CACHE = Path(__file__).resolve().parents[1] / "cache"


def removed(reason):
    """Status string for a source dropped under the clear-green licence rule (see usa/research/licence_registry_local.json).
    Pass it in run() parts instead of a fetch function: the dataset then returns [] with this status."""
    return f"{REMOVED} - {reason}"


def _shape(keys, kw):
    out = {k: kw.get(k) for k in keys}
    for k, v in out.items():
        if isinstance(v, str):
            v = re.sub(r"\s+", " ", v).strip()
            out[k] = v or None
    if out.get("lon") is not None:
        out["lon"], out["lat"] = round(float(out["lon"]), 6), round(float(out["lat"]), 6)
    return out


def assessor_rec(**kw):
    return _shape(ASSESSOR_KEYS, kw)


def permit_rec(**kw):
    r = _shape(PERMIT_KEYS, kw)
    if r.get("comments"):
        r["comments"] = r["comments"][:140]
    return r


def case_rec(**kw):
    return _shape(CASE_KEYS, kw)


# ------------------------------------------------------------------ value helpers
def num(v):
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def pos(v):
    """Numeric, None when zero/negative (assessor tables use 0 for unknown)."""
    n = num(v)
    return n if n and n > 0 else None


def year(v):
    n = num(v)
    return int(n) if n and 1700 < n < 2100 else None


def date(v):
    """ArcGIS epoch-ms, ISO string, or m/d/Y string -> YYYY-MM-DD."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.split(" ")[0] if fmt != "%b %d, %Y" else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s[:10]


def addr_join(*parts):
    s = " ".join(str(p).strip() for p in parts if p not in (None, "") and str(p).strip() not in ("", "0", "None"))
    return re.sub(r"\s+", " ", s).strip() or None


def baths(full, half=None):
    f, h = num(full) or 0, num(half) or 0
    t = f + 0.5 * h
    return t if t > 0 else None


# ------------------------------------------------------------------ geometry
def _centroid(g):
    if not g:
        return None, None
    t, c = g.get("type"), g.get("coordinates")
    if not c:
        return None, None
    if t == "Point":
        return c[0], c[1]
    if t == "MultiPoint":
        return c[0][0], c[0][1]
    if t in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        from shapely.geometry import shape
        p = shape(g)
        if p.is_empty:
            return None, None
        q = p.representative_point() if t.endswith("Polygon") else p.centroid
        return q.x, q.y
    return None, None


def in_bbox(lon, lat, bbox):
    return lon is not None and lat is not None and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


# ------------------------------------------------------------------ ArcGIS REST
def _get(url, params, tries=3, post=False):
    for i in range(tries):
        try:
            r = (S.post(url, data=params, timeout=90) if post else S.get(url, params=params, timeout=90))
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and "error" in j:
                raise RuntimeError(f"{j['error'].get('code')}: {j['error'].get('message')}")
            return j
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def arcgis(url, bbox=None, where="1=1", fields="*", geom=True, limit=5000, order=None, post=False, page=1000):
    """Query an ArcGIS FeatureServer/MapServer layer. Returns [(attrs, lon, lat)].

    bbox filters spatially (envelope, WGS84). Geometry comes back as WGS84 centroid / representative point.
    """
    out, offset = [], 0
    while True:
        p = {"where": where, "outFields": fields, "returnGeometry": str(bool(geom)).lower(), "outSR": 4326,
             "f": "geojson" if geom else "json", "resultOffset": offset, "resultRecordCount": min(page, limit - len(out))}
        if bbox:
            p.update({"geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                      "spatialRel": "esriSpatialRelIntersects"})
        if order:
            p["orderByFields"] = order
        j = _get(url.rstrip("/") + "/query", p, post=post)
        if geom:
            feats = j.get("features", [])
            batch = [((f.get("properties") or {}), *_centroid(f.get("geometry"))) for f in feats]
        else:
            feats = j.get("features", [])
            batch = [(f.get("attributes") or {}, None, None) for f in feats]
        out += batch
        more = j.get("exceededTransferLimit") or (j.get("properties") or {}).get("exceededTransferLimit")
        if not batch or len(out) >= limit or not (more or len(batch) == p["resultRecordCount"]):
            return out
        offset += len(batch)


def in_list(field, values, quote=True):
    vals = sorted({str(v).replace("'", "''") for v in values if v not in (None, "")})
    if not vals:
        return "1=0"
    return f"{field} IN (" + ",".join(f"'{v}'" if quote else v for v in vals) + ")"


def chunked_in(url, field, values, fields="*", quote=True, size=100, extra=None):
    vals = sorted({v for v in values if v not in (None, "")})
    rows = []
    for i in range(0, len(vals), size):
        w = in_list(field, vals[i:i + size], quote)
        if extra:
            w = f"({w}) AND ({extra})"
        rows += arcgis(url, where=w, fields=fields, geom=False, post=True)
    return [r[0] for r in rows]


def latlon_where(lonf, latf, bbox):
    w, s, e, n = bbox
    return f"{latf} >= {s} AND {latf} <= {n} AND {lonf} >= {w} AND {lonf} <= {e}"


# ------------------------------------------------------------------ Socrata
def socrata(domain, dataset, where=None, select=None, limit=5000, order=None):
    p = {"$limit": limit}
    if where:
        p["$where"] = where
    if select:
        p["$select"] = select
    if order:
        p["$order"] = order
    return _get(f"https://{domain}/resource/{dataset}.json", p)


def soql_box(field, bbox):
    w, s, e, n = bbox
    return f"within_box({field}, {n}, {w}, {s}, {e})"


# ------------------------------------------------------------------ US Census (public domain, federal works)
TIGER_ROADS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/"


def tiger_streets(bbox):
    """Upper-case full street names ('N 37TH WAY') of TIGERweb local + secondary roads crossing the box."""
    names = set()
    for layer in (8, 6, 2):
        try:
            for a, _, _ in arcgis(TIGER_ROADS + str(layer), bbox, fields="NAME", geom=False):
                if a.get("NAME"):
                    names.add(re.sub(r"\s+", " ", a["NAME"]).strip().upper())
        except Exception:
            pass
    return names


GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_GEO_CACHE = CACHE / "census_geocode.json"
_geo = None


def census_geocode(addrs):
    """Batch-geocode with the US Census Geocoder (public domain). addrs: [(street, city, state, zip)].
    Returns {(street, city, state, zip): (lon, lat) | None}. Results are cached in usa/cache/census_geocode.json."""
    global _geo
    if _geo is None:
        try:
            _geo = json.loads(_GEO_CACHE.read_text(encoding="utf-8"))
        except Exception:
            _geo = {}
    key = lambda a: "|".join(str(x or "") for x in a)
    todo = sorted({tuple(a) for a in addrs if key(a) not in _geo})
    for i in range(0, len(todo), 2000):
        chunk = todo[i:i + 2000]
        buf = io.StringIO()
        w = csv.writer(buf)
        for j, a in enumerate(chunk):
            w.writerow([j, *[x or "" for x in a]])
        r = None
        for t in range(3):
            try:
                r = S.post(GEOCODER, data={"benchmark": "Public_AR_Current"},
                           files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")}, timeout=600)
                r.raise_for_status()
                break
            except Exception:
                if t == 2:
                    raise
                time.sleep(3 * (t + 1))
        got = {}
        for row in csv.reader(io.StringIO(r.text)):
            if len(row) >= 6 and row[2] == "Match" and row[5]:
                lon, lat = map(float, row[5].split(","))
                got[int(row[0])] = [lon, lat]
        for j, a in enumerate(chunk):
            _geo[key(a)] = got.get(j)
        CACHE.mkdir(parents=True, exist_ok=True)
        _GEO_CACHE.write_text(json.dumps(_geo), encoding="utf-8")
    return {tuple(a): (tuple(_geo[key(a)]) if _geo.get(key(a)) else None) for a in addrs}


# ------------------------------------------------------------------ runner
def run(parts, bbox):
    """parts: {"assessor": fn|None, "permits": fn|None, "311": fn|None}; fn(bbox) -> list of records."""
    res = {"assessor": [], "permits": [], "cases311": [], "status": {}}
    key = {"assessor": "assessor", "permits": "permits", "311": "cases311"}
    for name, fn in parts.items():
        if fn is None:
            res["status"][name] = NOT_OPEN
            continue
        if isinstance(fn, str):          # removed(...) marker: licence not clear-green, nothing is fetched
            res["status"][name] = fn
            continue
        try:
            recs = fn(bbox)
            res[key[name]] = [r for r in recs if r.get("lon") is None or in_bbox(r["lon"], r["lat"], bbox)]
            res["status"][name] = "ok"
        except Exception as ex:  # keep the other datasets
            res["status"][name] = f"error: {type(ex).__name__}: {str(ex)[:200]}"
    return res


def test_bbox(lon, lat, d=0.001):
    return (lon - d, lat - d, lon + d, lat + d)

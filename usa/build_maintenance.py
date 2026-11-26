"""World Maintenance: per-house, per-trade signals -> web/usa/blocks/<slug>/maintenance.json (lane MS).

    .venv\\Scripts\\python -u -W ignore usa/build_maintenance.py                 # every block in index.json
    .venv\\Scripts\\python -u -W ignore usa/build_maintenance.py --slug boulder-co

Only measured / published values. Every signal carries value, unit and source; every reason cites a value.
Nothing hidden is stated as fact (e.g. a fast-melting roof is "possible heat loss", never "missing insulation").
LiDAR points are read in full (no thinning) from points_*.txt.

Inputs already in the block (other lanes): houses.json, meta.json, trees.json, streets.json,
pool_service.json (optional, copied for the pool trade), snowmelt.json (optional, roof/apron melt flags).
Area files: web/usa/data/earth_county.json (EPA radon zones, USGS NURE, EPA AQS), web/usa/data/weather_triggers.json
(NOAA NCEI / NWS / EPA), web/app/data/gnss.json (EarthScope / NSF GAGE, CC BY 4.0).
Fetched (cached under usa/cache/maint/, no keys, no login, public domain):
  NOAA NCEI 1991-2020 Climate Normals (annual/seasonal) via NCEI Search + Access Data Service: HDD, CDD, snowfall
  USDA NRCS Soil Data Access (SSURGO): linear extensibility percent (shrink-swell) at each house point
  USFWS National Wetlands Inventory (USGS-hosted ArcGIS REST): wetland polygons within 300 m of each house

Re-run after snowmelt.json / pool_service.json land: just run this script again (it reads them when present).
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import requests
import shapely
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely import affinity

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usa"))
from common import BLOCKS, load_points  # noqa: E402

DATA = ROOT / "web" / "usa" / "data"
GNSS = ROOT / "web" / "app" / "data" / "gnss.json"
CACHE = ROOT / "usa" / "cache" / "maint"
UA = {"User-Agent": "roof-intel/0.1 (open-data research)"}
SDA = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
NWI = "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query"
NCEI_SEARCH = "https://www.ncei.noaa.gov/access/services/search/v1/data"
NCEI_DATA = "https://www.ncei.noaa.gov/access/services/data/v1"
VERSION = 1
TODAY = date.today()
R = 0.5   # analysis grid (m)

SOURCES = {
    "lidar": {"name": "USGS 3DEP LiDAR block points (every return; vegetation, ground, roof heights)", "licence": "public domain",
              "url": "https://registry.opendata.aws/usgs-lidar/"},
    "houses": {"name": "Block houses.json (Overture footprints, LiDAR roof fit, NAIP roof colour, FEMA NFHL flood zone, SPC/NCEI hail)",
               "licence": "per-field sources in houses.json (public domain / ODbL / CDLA)", "url": "https://overturemaps.org"},
    "permits": {"name": "City open-data building permits (roof_permit, solar_permit, assessor year built where open)",
                "licence": "city open-data licence recorded in meta.city_data.licences", "url": ""},
    "normals": {"name": "NOAA NCEI U.S. Climate Normals 1991-2020 (annual/seasonal), nearest station", "licence": "public domain (US federal work)",
                "url": "https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals"},
    "weather": {"name": "Weather triggers: NOAA NCEI Storm Events, NWS warnings (IEM), EPA AQS PM10 (weather_triggers.json)",
                "licence": "public domain", "url": "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"},
    "earth": {"name": "earth_county.json: EPA Map of Radon Zones, USGS NURE gamma (eU), EPA AQS design values (PM2.5, O3)",
              "licence": "public domain", "url": "https://www.epa.gov/radon/epa-map-radon-zones"},
    "gnss": {"name": "EarthScope Consortium / NSF GAGE GNSS station velocities (nearest station vertical)", "licence": "CC BY 4.0 (GAGE Facility Data License)",
             "url": "https://www.unavco.org/data/policies_forms/data-policy/data-license.html"},
    "sda": {"name": "USDA NRCS Soil Data Access (SSURGO) linear extensibility (shrink-swell)", "licence": "public domain (USDA federal work)",
            "url": "https://sdmdataaccess.sc.egov.usda.gov/"},
    "nwi": {"name": "USFWS National Wetlands Inventory (Wetlands MapServer)", "licence": "public domain (US federal work)",
            "url": "https://www.fws.gov/program/national-wetlands-inventory"},
    "pools": {"name": "Pool sites + yearly states (usa/pools.py on USDA NAIP) and pool_service.json", "licence": "public domain (USDA FSA NAIP)",
              "url": "https://planetarycomputer.microsoft.com/dataset/naip"},
    "snowmelt": {"name": "snowmelt.json: Sentinel-2 NDSI roof melt vs neighbours after NOAA GHCN-Daily storms",
                 "licence": "Copernicus open licence + public domain", "url": "https://earth-search.aws.element84.com/v1"},
    "streets": {"name": "Block streets.json road surfaces (LiDAR ground + OpenStreetMap)", "licence": "public domain + ODbL 1.0",
                "url": "https://www.openstreetmap.org/copyright"},
}
S_LIDAR, S_HOUSE, S_PERMIT, S_NORM, S_WX = "USGS 3DEP LiDAR", "houses.json", "city permits", "NOAA Climate Normals 1991-2020", "NOAA NCEI / NWS (weather_triggers)"

TRADES = [
    ("roof", "Roofing", "Hail history, roof age clues, roof surface deviation from fitted planes, height, sun exposure, snow-melt test."),
    ("gutters", "Gutters", "LiDAR vegetation above the eave within 3 m of the roof edge, and roof-edge length to clean."),
    ("trees", "Tree care", "LiDAR canopy over the roof, tallest tree within 10 m, trees tall enough to reach the house if they fell."),
    ("sidewalk_leaves", "Leaf clean-up (frontage)", "LiDAR canopy over the strip between the house and the street."),
    ("snow_ice", "Snow and ice", "Station snowfall normals, ground in the house's winter noon shadow, snow-melt apron flag."),
    ("heating", "Heating", "Heating degree-days, home age, faster-melting roof flag."),
    ("cooling", "Cooling", "Cooling degree-days, facade and roof sun hours, summer surface temperature, home age."),
    ("solar_clean", "Solar panel cleaning", "Solar permit on record, dust days, PM2.5, canopy over the roof."),
    ("pressure_wash", "Pressure washing", "Shaded facades (low sun hours), canopy beside the house, dust days, PM2.5."),
    ("foundation", "Foundation", "Nearest GNSS vertical velocity, soil shrink-swell (SSURGO LEP), FEMA flood zone."),
    ("flood_water", "Drainage and flood", "FEMA flood zone and the house's ground height versus the lowest ground nearby (LiDAR)."),
    ("radon", "Radon", "EPA county radon zone and USGS NURE equivalent uranium."),
    ("pests", "Pest control", "NWI wetlands within 300 m, green-pool history in the block, canopy around the house."),
    ("wildfire", "Defensible space", "LiDAR vegetation cover in the 0-1.5 m, 1.5-9 m and 9-30 m zones around the house."),
    ("pool", "Pool service", "Copied from pool_service.json (lane PS1) when present."),
]
NEXT = {
    "roof": "Roof inspection (hail / age check)", "gutters": "Gutter clean before leaf drop", "trees": "Arborist visit: prune limbs over the roof",
    "sidewalk_leaves": "Leaf and walk clean-up in autumn", "snow_ice": "Offer a snow-clearing / de-icing contract before first snow",
    "heating": "Furnace / boiler tune-up before winter", "cooling": "AC service before summer", "solar_clean": "Panel wash after dusty season",
    "pressure_wash": "Wash shaded siding (mildew) in spring", "foundation": "Foundation / slab check", "flood_water": "Grading, gutter-extension or sump check",
    "radon": "Radon test kit / measurement", "pests": "Mosquito and pest treatment in the warm season", "wildfire": "Clear vegetation within 1.5 m of the house",
    "pool": "Pool service visit",
}


# ---------------------------------------------------------------- helpers
def num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def rnd(v, n=1):
    v = num(v)
    return None if v is None else round(v, n)


def lin(v, lo, hi):
    v = num(v)
    if v is None:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def sig(label, value, unit, source):
    return {"label": label, "value": value, "unit": unit, "source": source}


def hav_km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1.0, a)))


def cached(name, fn):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.md5(name.encode()).hexdigest()[:16] + ".json")
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    v = fn()
    if v is not None:
        f.write_text(json.dumps(v), encoding="utf-8")
    return v


def http(method, url, tries=3, **kw):
    for i in range(tries):
        try:
            r = requests.request(method, url, headers=UA, timeout=90, **kw)
            if r.ok:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


# ---------------------------------------------------------------- remote data
NORM_TYPES = ["ANN-HTDD-NORMAL", "ANN-CLDD-NORMAL", "ANN-SNOW-NORMAL", "ANN-SNOW-AVGNDS-GE010TI", "ANN-SNWD-AVGNDS-GE001WI"]


def normals(lon, lat):
    """Nearest 1991-2020 normals station with HDD/CDD, and nearest with snowfall (may be the same)."""
    def fetch():
        out = {}
        for pad in (0.4, 1.0):
            r = http("GET", NCEI_SEARCH, params={"dataset": "normals-annualseasonal", "limit": 200,
                                                 "bbox": f"{lat + pad},{lon - pad},{lat - pad},{lon + pad}"})
            if r is None:
                return None
            st = []
            for x in r.json().get("results", []):
                try:
                    sid = x["filePath"].rsplit("/", 1)[-1].replace(".csv", "")
                    lo_, la_ = x["boundingPoints"][0]["coordinates"]
                    nm = (x.get("stations") or [{}])[0].get("name")
                    st.append((hav_km(lon, lat, lo_, la_), sid, nm))
                except Exception:  # noqa: BLE001
                    continue
            if st:
                break
        st.sort()
        for d, sid, nm in st[:12]:
            rr = http("GET", NCEI_DATA, params={"dataset": "normals-annualseasonal-1991-2020", "stations": sid,
                                                "dataTypes": ",".join(NORM_TYPES), "format": "json"})
            if rr is None:
                continue
            try:
                row = (rr.json() or [{}])[0]
            except Exception:  # noqa: BLE001
                continue
            vals = {k: num(row.get(k)) for k in NORM_TYPES}
            if "hdd" not in out and vals["ANN-HTDD-NORMAL"] is not None:
                out["hdd"] = {"station": sid, "name": nm, "km": round(d, 1), "hdd": vals["ANN-HTDD-NORMAL"], "cdd": vals["ANN-CLDD-NORMAL"]}
            if "snow" not in out and vals["ANN-SNOW-NORMAL"] is not None:
                out["snow"] = {"station": sid, "name": nm, "km": round(d, 1), "snow_in": vals["ANN-SNOW-NORMAL"],
                               "snow_days_ge1in": vals["ANN-SNOW-AVGNDS-GE010TI"], "snow_depth_days_ge1in": vals["ANN-SNWD-AVGNDS-GE001WI"]}
            if "hdd" in out and "snow" in out:
                break
        return out
    return cached(f"normals:{lon:.3f},{lat:.3f}", fetch)


def lep_at(lon, lat):
    """SSURGO linear extensibility: depth-weighted LEP of the upper 150 cm of the largest component that has LEP data."""
    def fetch():
        q = ("SELECT mu.mukey, mu.muname, c.compname, c.comppct_r, ch.hzdept_r, ch.hzdepb_r, ch.lep_r "
             f"FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon:.6f} {lat:.6f})') k "
             "JOIN mapunit mu ON mu.mukey=k.mukey JOIN component c ON c.mukey=mu.mukey "
             "LEFT JOIN chorizon ch ON ch.cokey=c.cokey ORDER BY c.comppct_r DESC, ch.hzdept_r")
        r = http("POST", SDA, json={"query": q, "format": "JSON+COLUMNNAME"})
        if r is None:
            return None
        try:
            rows = r.json().get("Table") or []
        except Exception:  # noqa: BLE001
            return {"lep": None}
        if len(rows) < 2:
            return {"lep": None, "muname": None}
        hdr, rows = rows[0], rows[1:]
        comps = {}
        for row in rows:
            d = dict(zip(hdr, row))
            comps.setdefault((d["compname"], d["comppct_r"]), []).append(d)
        mu = rows[0][hdr.index("muname")]
        for (cn, pct), hz in comps.items():
            tot = w = 0.0
            for z in hz:
                top, bot, lep = num(z["hzdept_r"]), num(z["hzdepb_r"]), num(z["lep_r"])
                if top is None or bot is None or lep is None or top >= 150:
                    continue
                t = min(bot, 150) - top
                tot += t * lep; w += t
            if w > 0:
                return {"lep": round(tot / w, 1), "component": cn, "comppct": num(pct), "muname": mu}
        return {"lep": None, "muname": mu}
    return cached(f"sda:{lon:.5f},{lat:.5f}", fetch)


def lep_class(v):
    if v is None:
        return None
    return "low" if v < 3 else "moderate" if v < 6 else "high" if v < 9 else "very high"


def wetlands(lon, lat, radius_m):
    """NWI polygons within radius of a point, geometry in EPSG:4326."""
    def fetch():
        r = http("GET", NWI, params={"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326, "outSR": 4326,
                                     "distance": radius_m, "units": "esriSRUnit_Meter", "outFields": "WETLAND_TYPE,ATTRIBUTE",
                                     "returnGeometry": "true", "geometryPrecision": 6, "f": "json"})
        if r is None:
            return None
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return None
        if "error" in j:
            return None
        out = []
        for f in j.get("features", []):
            rings = (f.get("geometry") or {}).get("rings")
            if rings:
                out.append({"type": (f.get("attributes") or {}).get("WETLAND_TYPE"), "code": (f.get("attributes") or {}).get("ATTRIBUTE"),
                            "rings": rings})
        return out
    return cached(f"nwi:{lon:.4f},{lat:.4f},{radius_m}", fetch)


_GNSS = None


def gnss_near(lon, lat):
    global _GNSS
    if _GNSS is None:
        _GNSS = load(GNSS) or {"stations": []}
    best = None
    for s in _GNSS.get("stations", []):
        try:
            sid, slon, slat, vu = s[0], float(s[1]), float(s[2]), num(s[5])
        except Exception:  # noqa: BLE001
            continue
        if vu is None:
            continue
        d = hav_km(lon, lat, slon, slat)
        if best is None or d < best[0]:
            best = (d, sid, vu)
    return {"station": best[1], "vu_mm_yr": best[2], "km": round(best[0], 1)} if best else None


# ---------------------------------------------------------------- LiDAR
class Lidar:
    def __init__(self, d, meta):
        pts = load_points(d, meta)   # every return
        u = meta.get("point_unit_m", 0.02)
        self.x, self.y = pts["x"] * u, pts["y"] * u
        self.z = pts["z"] / 100.0
        self.hag = pts["h"] / 8.0
        k = pts["k"]
        self.k = k
        self.veg = (k == 2) | ((k == 3) & (self.hag >= 2.0))   # same rule as build_pool_service.py
        self.ground = k == 0
        half = float(meta["half_m"])
        self.half = half
        n = int(round(2 * half / R)) + 1
        self.n = n
        ci = np.clip(((self.x + half) / R).astype(int), 0, n - 1)
        cj = np.clip(((self.y + half) / R).astype(int), 0, n - 1)
        idx = cj * n + ci
        chm = np.zeros(n * n); np.maximum.at(chm, idx[self.veg], self.hag[self.veg]); self.chm = chm.reshape(n, n)
        # near walls a single high return can be a roof overhang: within 3 m of a house only multi-return vegetation counts
        mr = k == 2
        chm2 = np.zeros(n * n); np.maximum.at(chm2, idx[mr], self.hag[mr]); self.chm_mr = chm2.reshape(n, n)
        gs = np.zeros(n * n); gc = np.zeros(n * n)
        np.add.at(gs, idx[self.ground], self.z[self.ground]); np.add.at(gc, idx[self.ground], 1)
        self.gz = np.where(gc > 0, gs / np.maximum(gc, 1), np.nan).reshape(n, n)
        c = -half + (np.arange(n) + 0.5) * R
        self.CX, self.CY = np.meshgrid(c, c)
        self.cx, self.cy = self.CX.ravel(), self.CY.ravel()
        self.dens = meta.get("points_per_m2")

    def cells_in(self, geom):
        b = geom.bounds
        m = (self.cx >= b[0] - R) & (self.cx <= b[2] + R) & (self.cy >= b[1] - R) & (self.cy <= b[3] + R)
        ii = np.nonzero(m)[0]
        inside = shapely.contains_xy(geom, self.cx[ii], self.cy[ii])
        return ii[inside]

    def pts_near(self, geom, buf):
        b = geom.bounds
        m = (self.x >= b[0] - buf) & (self.x <= b[2] + buf) & (self.y >= b[1] - buf) & (self.y <= b[3] + buf)
        return np.nonzero(m)[0]

    def cover(self, geom, min_h, multi_only=False):
        """share of 0.5 m cells inside geom whose tallest vegetation return is >= min_h, and the max height."""
        ii = self.cells_in(geom)
        if not len(ii):
            return None, None, 0
        v = (self.chm_mr if multi_only else self.chm).ravel()[ii]
        hit = v >= min_h
        return round(100 * hit.mean(), 1), (round(float(v.max()), 1) if hit.any() else 0.0), len(ii)


# ---------------------------------------------------------------- area context
def area(meta, earth, wt_all):
    lon, lat = meta["origin"]
    fips = str(meta.get("fips") or meta.get("county_fips") or "").zfill(5)
    ec = (earth or {}).get("counties", {}).get(fips) or {}
    wtc = (wt_all or {}).get("counties", {}).get(fips) or ((meta.get("weather_triggers") or {}).get("county") or {})
    nm = normals(lon, lat) or {}
    g = gnss_near(lon, lat)
    return {
        "county_fips": fips, "county": meta.get("county"),
        "hdd": (nm.get("hdd") or {}).get("hdd"), "cdd": (nm.get("hdd") or {}).get("cdd"),
        "hdd_station": nm.get("hdd"), "snow_in": (nm.get("snow") or {}).get("snow_in"),
        "snow_days_ge1in": (nm.get("snow") or {}).get("snow_days_ge1in"),
        "snow_depth_days_ge1in": (nm.get("snow") or {}).get("snow_depth_days_ge1in"), "snow_station": nm.get("snow"),
        "dust_days_yr": num(wtc.get("dust_days_yr")), "high_wind_days_yr": num(wtc.get("high_wind_days_yr")),
        "hail_days_yr": num(wtc.get("hail_days_yr")),
        "pm25_dv": num(ec.get("pm25_dv")), "o3_dv": num(ec.get("o3_dv")),
        "radon_zone": ec.get("radon_zone"), "nure_eu_ppm": num(ec.get("nure_eu")),
        "gnss": g,
        "lat": lat, "lidar": meta.get("lidar"), "lidar_year": meta.get("lidar_year"),
    }


# ---------------------------------------------------------------- per-house geometry
def ring_poly(h):
    try:
        p = Polygon(h["ring"])
        return p if p.is_valid and p.area > 1 else p.buffer(0)
    except Exception:  # noqa: BLE001
        return None


def frontage(P, h, roads):
    """Strip between the house and the nearest road: the house footprint swept toward the road, minus the house."""
    if roads is not None and not roads.is_empty:
        a, b = nearest_points(P, roads)
        dx, dy = b.x - a.x, b.y - a.y
        dist = math.hypot(dx, dy)
        src = "streets.json road surface"
    else:
        f = h.get("facing") or {}
        az, dist = num(f.get("azimuth")), num(f.get("street_m"))
        if az is None or dist is None:
            return None, None, None
        dx, dy = dist * math.sin(math.radians(az)), dist * math.cos(math.radians(az))
        src = "houses.json facing azimuth + street distance"
    if dist < 1 or dist > 60:
        return None, dist, src
    moved = affinity.translate(P, dx, dy)
    strip = MultiPoint(list(P.exterior.coords) + list(moved.exterior.coords)).convex_hull.difference(P)
    if roads is not None and not roads.is_empty:
        strip = strip.difference(roads)
    return strip, dist, src


def noon_shadow(P, h, lat):
    """Ground the house itself shades at solar noon on Dec 21 (prism of LiDAR height; trees not included)."""
    hm = num(h.get("height_m"))
    if hm is None:
        return None, None
    elev = 90 - abs(lat) - 23.44
    if elev <= 2:
        return None, None
    L = min(hm / math.tan(math.radians(elev)), 60)
    sgn = 1 if lat >= 0 else -1   # shadow points north in the northern hemisphere
    moved = affinity.translate(P, 0, sgn * L)
    sh = MultiPoint(list(P.exterior.coords) + list(moved.exterior.coords)).convex_hull.difference(P)
    return sh, round(L, 1)


def year_built(h):
    a = h.get("assessor") if isinstance(h.get("assessor"), dict) else {}
    for v in (a.get("yearbuilt"), a.get("year_built"), h.get("year_built")):
        y = num(v)
        if y and 1700 < y <= TODAY.year:
            return int(y)
    return None


def solar_evidence(h):
    if h.get("solar_permit"):
        return str(h["solar_permit"])[:10], "solar_permit"
    for p in h.get("permits") or []:
        t = " ".join(str(p.get(k) or "") for k in ("type", "work", "comments")).lower()
        if "solar" in t or "photovoltaic" in t:
            return str(p.get("date") or "")[:10] or None, "permit text mentions solar"
    return None, None


def years_since(dstr):
    try:
        d = date.fromisoformat(str(dstr)[:10])
        return round((TODAY - d).days / 365.25, 1)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- trade scoring
class T:
    """one trade for one house: signals + scored parts. Unknown house-level parts score 0."""
    def __init__(self):
        self.signals, self.reasons, self.pts = [], [], 0.0
        self.scored = False

    def s(self, *a):
        self.signals.append(sig(*a))

    def add(self, w, part, why):
        if part is None:
            return
        self.scored = True
        p = round(w * part)
        self.pts += w * part
        if p > 0 and why:
            self.reasons.append(f"+{p} {why}")

    def out(self, tid, score_null=False):
        if not self.signals:
            return None
        sc = None if (score_null or not self.scored) else int(round(min(100, self.pts)))
        return {"score": sc, "signals": self.signals, "reasons": self.reasons,
                "next": NEXT[tid] if sc is not None and sc >= 30 else None}


def house_trades(h, P, L, A, ctx, trees, roads, snow, pool_rows, used):
    t = {}
    eave = num(h.get("eave_m")) or 2.5
    hm = num(h.get("height_m"))
    yb = year_built(h)
    age = TODAY.year - yb if yb else None
    lat = A["lat"]
    lon_h, lat_h = num(h.get("lon")), num(h.get("lat"))
    sm = snow.get(str(h["id"])) if snow else None
    sm_roof = (sm or {}).get("roof") or {}
    sm_fast = str(sm_roof.get("flag") or "").startswith("faster")
    perim = P.length
    used.update({"houses", "lidar"})

    # ---- LiDAR measures shared across trades
    band3 = P.buffer(3.0).symmetric_difference(P.buffer(-3.0)) if P.area > 40 else P.buffer(3.0)
    ii = L.pts_near(P, 30)
    px, py, ph = L.x[ii], L.y[ii], L.hag[ii]
    dist_edge = shapely.distance(P.exterior, shapely.points(px, py))
    inside = shapely.contains_xy(P, px, py)
    above_eave = (L.k[ii] == 2) & (ph > eave)   # multi-return vegetation only next to the walls
    g_mask = above_eave & (dist_edge <= 3.0)
    n_g = int(g_mask.sum())
    g_max = float(ph[g_mask].max()) if n_g else None
    g_cov, _, _ = L.cover(band3, eave, multi_only=True)
    ov_mask = (L.k[ii] == 2) & inside & (ph > eave)
    ov_cells = L.cells_in(P)
    ov_area = float((L.chm_mr.ravel()[ov_cells] > eave).sum() * R * R) if len(ov_cells) else 0.0
    ov_pct = round(100 * ov_area / max(P.area, 1), 1)
    crowns = []
    for tr in trees or []:
        x, y, th, cr = num(tr.get("x")), num(tr.get("y")), num(tr.get("h")), num(tr.get("r")) or 0
        if x is None or th is None:
            continue
        d = P.distance(Point(x, y))
        crowns.append((d, th, cr))
    c10 = [c for c in crowns if c[0] <= 10]
    tall10 = max(c10, key=lambda c: c[1]) if c10 else None
    reach = [c for c in crowns if c[0] > 0 and c[1] >= c[0] and c[1] >= 6]
    gut_crowns = [c for c in crowns if c[1] > eave and c[0] - c[2] <= 3]

    # ================= roof
    r = T()
    hh = h.get("hail_history") or []
    hc = h.get("hail_checked") or {}
    hmax = max([num(x[1]) or 0 for x in hh if isinstance(x, (list, tuple)) and len(x) > 1] or [num(h.get("hail_max_in")) or 0])
    if hc or hh:
        r.s("Days with radar hail >= 0.5 in at the house", len(hh), "days", f"NOAA MRMS MESH (houses.json hail_history, {hc.get('from', '?')} to {hc.get('to', '?')})")
        r.s("Largest radar-estimated hail at the house", round(hmax, 2), "in", "NOAA MRMS MESH (houses.json hail_max_in)")
        last = max((x[0] for x in hh), default=None)
        r.add(30, lin(hmax, 0.75, 2.0) if hh else 0.0,
              f"radar hail up to {hmax:.2f} in at the house on {len(hh)} day(s), last {last}" if hh else None)
    age_src = None
    if h.get("roof_permit"):
        ra, age_src = years_since(h["roof_permit"]), f"roof permit {str(h['roof_permit'])[:10]}"
        r.s("Years since last roof permit", ra, "yr", S_PERMIT); used.add("permits")
    elif (h.get("roof_color_change") or {}).get("year"):
        rc = h["roof_color_change"]
        ra, age_src = TODAY.year - int(rc["year"]), f"roof colour change in NAIP {rc['year']} (strength {rc.get('strength')})"
        r.s("Years since roof colour changed (NAIP, likely re-roof)", ra, "yr", "USDA NAIP roof colour series")
    elif age is not None:
        ra, age_src = age, f"home built {yb}, no roof permit or roof colour change found"
        r.s("Home age (no newer roof evidence)", age, "yr", "assessor year built"); used.add("permits")
    else:
        ra = None
    if ra is not None:
        r.add(30 if not age_src.startswith("home") else 15, lin(ra, 8, 25), f"{ra:.0f} yr since {age_src}")
    rd = h.get("roof_dev") if isinstance(h.get("roof_dev"), dict) else None
    if rd and num(rd.get("p95_cm")) is not None:
        r.s("Roof surface deviation from fitted planes (95th pct)", rnd(rd["p95_cm"]), "cm", S_LIDAR)
        r.add(15, lin(rd["p95_cm"], 10, 40), f"LiDAR roof points sit up to {rd['p95_cm']:.0f} cm off the fitted planes (95th pct; sag, debris or complex roof)")
    if hm is not None:
        r.s("Roof height (95th pct) / eave (20th pct)", f"{hm} / {h.get('eave_m')}", "m", S_LIDAR)
        r.add(5, 1.0 if hm >= 6.5 else 0.0, f"{hm:.1f} m tall roof (2+ storeys: steep/ladder work)")
    sun = h.get("sun") or {}
    if num(sun.get("direct_sun_hours_yr")) is not None:
        r.s("Direct sun on the roof", sun["direct_sun_hours_yr"], "h/yr", "LiDAR sun ray-cast (houses.json sun)")
        r.add(15, lin(sun["direct_sun_hours_yr"], 1000, 3000), f"{sun['direct_sun_hours_yr']} h/yr direct sun on the roof (UV ageing)")
    if sm_roof:
        used.add("snowmelt")
        r.s("Snow-melt test (roof vs neighbours)", sm_roof.get("flag"), f"{sm_roof.get('obs', 0)} scenes", "snowmelt.json (Sentinel-2)")
        r.add(5, 1.0 if sm_fast else 0.0, f"roof lost snow faster than neighbours on {sm_roof.get('early_melt_share')} of {sm_roof.get('obs')} scenes (possible heat loss / ice-dam risk)")
    t["roof"] = r.out("roof")

    # ================= gutters
    gt = T()
    gt.s("Vegetation returns above the eave within 3 m of the roof edge", n_g, "points", S_LIDAR)
    gt.s("Tallest of those", rnd(g_max) if g_max else 0, "m", S_LIDAR)
    gt.s("Canopy above eave height in the 3 m band around the roof edge", g_cov, "% of band", S_LIDAR)
    gt.s("Tree crowns above the eave reaching within 3 m of the roof", len(gut_crowns), "crowns", "trees.json (LiDAR)")
    gt.s("Roof edge length (footprint perimeter)", round(perim, 1), "m", "houses.json ring")
    mc = h.get("meta_canopy") or {}
    if num(mc.get("cover_pct")) is not None:
        gt.s("Canopy cover around the house", mc["cover_pct"], "%", "Meta/WRI 1 m canopy height (houses.json meta_canopy)")
    gt.add(55, lin(g_cov, 2, 40), f"{g_cov}% of the 3 m roof-edge band has canopy above the {eave} m eave"
           + (f" (max {g_max:.1f} m, {n_g} returns)" if g_max else ""))
    gt.add(25, min(1.0, len(gut_crowns) / 3), f"{len(gut_crowns)} tree crown(s) taller than the eave within 3 m of the roof edge")
    gt.add(20, lin(perim, 30, 90), f"{perim:.0f} m of roof edge")
    t["gutters"] = gt.out("gutters")

    # ================= trees
    tr = T()
    tr.s("Canopy over the roof (above eave)", round(ov_area, 1), "m2", S_LIDAR)
    tr.s("Share of roof under canopy", ov_pct, "%", S_LIDAR)
    tr.s("Vegetation returns over the roof", int(ov_mask.sum()), "points", S_LIDAR)
    if tall10:
        tr.s("Tallest tree within 10 m", tall10[1], "m", "trees.json (LiDAR)")
        tr.s("Its distance to the house", round(tall10[0], 1), "m", "trees.json (LiDAR)")
    tr.s("Trees taller than their distance to the house (fall reach)", len(reach), "trees", "trees.json (LiDAR)")
    tr.add(45, lin(ov_pct, 2, 30), f"{ov_area:.0f} m2 ({ov_pct}%) of the roof is under canopy taller than the eave")
    if reach:
        tmax = max(reach, key=lambda c: c[1])
        tr.add(35, min(1.0, len(reach) / 3), f"{len(reach)} tree(s) tall enough to reach the house if they fell (e.g. {tmax[1]:.0f} m tall, {tmax[0]:.1f} m away)")
    else:
        tr.add(35, 0.0, None)
    tr.add(20, lin(tall10[1] if tall10 else 0, 8, 20), f"tallest tree within 10 m is {tall10[1]:.1f} m" if tall10 else None)
    t["trees"] = tr.out("trees")

    # ================= sidewalk_leaves
    strip, sdist, ssrc = frontage(P, h, roads)
    if strip is not None and not strip.is_empty and strip.area > 2:
        if ssrc.startswith("streets"):
            used.add("streets")
        sc_, smax, _ = L.cover(strip, 2.0)
        sl = T()
        sl.s("Canopy (>= 2 m) over the house-to-street strip", sc_, "%", S_LIDAR)
        sl.s("Tallest vegetation over the strip", smax, "m", S_LIDAR)
        sl.s("Strip area", round(strip.area, 1), "m2", ssrc)
        sl.s("House-to-street distance", round(sdist, 1), "m", ssrc)
        sl.add(70, lin(sc_, 5, 50), f"{sc_}% of the {strip.area:.0f} m2 frontage strip is under canopy (max {smax} m)")
        sl.add(30, lin(strip.area * (sc_ or 0) / 100, 20, 300), f"~{strip.area * (sc_ or 0) / 100:.0f} m2 of canopy over the frontage")
        t["sidewalk_leaves"] = sl.out("sidewalk_leaves")

    # ================= snow_ice
    si = T()
    if A["snow_in"] is not None:
        used.add("normals")
        st = A["snow_station"]
        si.s("Normal annual snowfall", A["snow_in"], "in", f"{S_NORM} {st['station']} ({st['km']} km)")
        if A["snow_days_ge1in"] is not None:
            si.s("Days with >= 1 in snowfall", A["snow_days_ge1in"], "days/yr", f"{S_NORM} {st['station']}")
        si.add(60, lin(A["snow_in"], 3, 60), f"{A['snow_in']:.0f} in normal snowfall/yr at {st['station']} ({st['km']} km)")
    sh, slen = noon_shadow(P, h, lat)
    if sh is not None:
        si.s("Ground in the house's Dec 21 noon shadow", round(sh.area, 1), "m2", "LiDAR roof height + sun geometry")
        si.s("Dec 21 noon shadow length", slen, "m", "LiDAR roof height + sun geometry")
        if strip is not None and not strip.is_empty:
            fs = strip.intersection(sh).area
            si.s("Frontage strip (walk/drive side) in that shadow", round(fs, 1), "m2", "LiDAR + street geometry")
            if A["snow_in"]:
                si.add(25, lin(fs, 5, 80), f"{fs:.0f} m2 of the street-side strip lies in the house's winter noon shadow (ice stays)")
        if A["snow_in"]:
            si.add(10, lin(sh.area, 20, 150), f"{sh.area:.0f} m2 of ground in winter noon shadow")
    ap = (sm or {}).get("apron") if sm else None
    if ap:
        si.s("Mapped driveway snow-melt test", ap.get("flag"), f"{ap.get('obs', 0)} scenes", "snowmelt.json (Sentinel-2)")
        if A["snow_in"]:
            si.add(5, 1.0 if str(ap.get("flag", "")).startswith("stays clear") else 0.0, "mapped driveway stays clear after storms (already cleared or heated)")
    t["snow_ice"] = si.out("snow_ice", score_null=A["snow_in"] is None)

    # ================= heating
    he = T()
    if A["hdd"] is not None:
        st = A["hdd_station"]
        he.s("Heating degree-days (base 65 F)", A["hdd"], "F-days/yr", f"{S_NORM} {st['station']} ({st['km']} km)")
        he.add(55, lin(A["hdd"], 1500, 8000), f"{A['hdd']:.0f} heating degree-days/yr ({st['station']})")
    if age is not None:
        he.s("Home age", age, "yr", f"assessor year built {yb}")
        he.add(35, lin(age, 15, 70), f"home built {yb} ({age} yr)")
    if sm_roof:
        he.s("Faster-melting roof after storms", "yes" if sm_fast else "no", "", "snowmelt.json (Sentinel-2)")
        he.add(10, 1.0 if sm_fast else 0.0, "roof sheds snow faster than neighbours (possible heat loss)")
    t["heating"] = he.out("heating")

    # ================= cooling
    co = T()
    if A["cdd"] is not None:
        st = A["hdd_station"]
        co.s("Cooling degree-days (base 65 F)", A["cdd"], "F-days/yr", f"{S_NORM} {st['station']} ({st['km']} km)")
        co.add(45, lin(A["cdd"], 500, 4500), f"{A['cdd']:.0f} cooling degree-days/yr ({st['station']})")
    fsun = h.get("facade_sun") or {}
    fmax = max([num((fsun.get(k) or {}).get("sun_hours_yr")) or 0 for k in ("front", "back")] or [0]) if fsun else None
    if fsun:
        for k in ("front", "back"):
            f = fsun.get(k) or {}
            if num(f.get("sun_hours_yr")) is not None:
                co.s(f"{k.title()} facade ({f.get('compass')}) sun", f["sun_hours_yr"], "h/yr", "LiDAR sun ray-cast (houses.json facade_sun)")
        co.add(15, lin(fmax, 500, 2500), f"sunniest facade gets {fmax:.0f} h/yr")
    if num(sun.get("direct_sun_hours_yr")) is not None:
        co.s("Direct sun on the roof", sun["direct_sun_hours_yr"], "h/yr", "LiDAR sun ray-cast")
        co.add(10, lin(sun["direct_sun_hours_yr"], 1000, 3000), f"{sun['direct_sun_hours_yr']} h/yr sun on the roof")
    th = h.get("thermal") or {}
    if num(th.get("summer_lst_c")) is not None:
        co.s("Summer surface temperature (30 m Landsat pixel)", th["summer_lst_c"], "C", f"Landsat C2 ST {th.get('summer_date')}")
        co.add(10, lin(th["summer_lst_c"], 30, 55), f"{th['summer_lst_c']} C summer surface temperature (Landsat, 30 m pixel)")
    if age is not None:
        co.s("Home age", age, "yr", f"assessor year built {yb}")
        co.add(20, lin(age, 15, 70), f"home built {yb} ({age} yr)")
    t["cooling"] = co.out("cooling")

    # ================= solar_clean (only with solar evidence on record)
    sd, ssrc2 = solar_evidence(h)
    if sd or ssrc2:
        used.add("permits")
        sc = T()
        sc.s("Solar permit on record", sd, "date", f"{S_PERMIT} ({ssrc2})")
        sc.add(30, 1.0, f"solar permit {sd} on record")
        if A["dust_days_yr"] is not None:
            used.add("weather")
            sc.s("Dust days", A["dust_days_yr"], "days/yr", S_WX)
            sc.add(30, lin(A["dust_days_yr"], 0, 25), f"{A['dust_days_yr']} dust days/yr (county)")
        if A["pm25_dv"] is not None:
            used.add("earth")
            sc.s("PM2.5 annual design value", A["pm25_dv"], "ug/m3", "EPA AQS (earth_county.json)")
            sc.add(15, lin(A["pm25_dv"], 5, 12), f"PM2.5 {A['pm25_dv']} ug/m3 (county)")
        sc.s("Share of roof under canopy", ov_pct, "%", S_LIDAR)
        sc.add(25, lin(ov_pct, 2, 30), f"{ov_pct}% of the roof under canopy (leaf/pollen/sap on panels)")
        t["solar_clean"] = sc.out("solar_clean")

    # ================= pressure_wash
    pw = T()
    if fsun:
        fmin_k = min(("front", "back"), key=lambda k: num((fsun.get(k) or {}).get("sun_hours_yr")) if num((fsun.get(k) or {}).get("sun_hours_yr")) is not None else 1e9)
        fm = num((fsun.get(fmin_k) or {}).get("sun_hours_yr"))
        if fm is not None:
            pw.s(f"Shadiest facade ({fmin_k}, {(fsun.get(fmin_k) or {}).get('compass')}) sun", fm, "h/yr", "LiDAR sun ray-cast")
            pw.add(45, 1 - lin(fm, 200, 1500), f"{fmin_k} facade gets only {fm:.0f} h/yr sun (mildew/algae streaks)")
    pw.s("Canopy above eave in the 3 m band", g_cov, "%", S_LIDAR)
    pw.add(20, lin(g_cov, 5, 50), f"{g_cov}% canopy within 3 m of the walls")
    if A["dust_days_yr"] is not None:
        pw.s("Dust days", A["dust_days_yr"], "days/yr", S_WX)
        pw.add(20, lin(A["dust_days_yr"], 0, 25), f"{A['dust_days_yr']} dust days/yr (county)")
    if A["pm25_dv"] is not None:
        pw.s("PM2.5 annual design value", A["pm25_dv"], "ug/m3", "EPA AQS (earth_county.json)")
        pw.add(15, lin(A["pm25_dv"], 5, 12), f"PM2.5 {A['pm25_dv']} ug/m3 (county)")
    t["pressure_wash"] = pw.out("pressure_wash")

    # ================= foundation
    fo = T()
    g = A["gnss"]
    if g:
        used.add("gnss")
        fo.s("Nearest GNSS vertical velocity", g["vu_mm_yr"], "mm/yr", f"EarthScope/GAGE {g['station']}")
        fo.s("Distance to that GNSS station", g["km"], "km", "EarthScope/GAGE")
        if g["km"] <= 50:
            fo.add(25, lin(-g["vu_mm_yr"], 1, 6), f"GNSS {g['station']} ({g['km']} km) moves {g['vu_mm_yr']} mm/yr vertically (sinking)")
    soil = ctx["soil"].get(h["id"]) if ctx.get("soil") else None
    if soil and soil.get("lep") is not None:
        used.add("sda")
        fo.s("Soil linear extensibility (0-150 cm)", soil["lep"], "%", f"USDA SSURGO {soil.get('component')} ({soil.get('muname')})")
        fo.s("Shrink-swell class", lep_class(soil["lep"]), "", "NRCS LEP classes (<3 low, 3-6 moderate, 6-9 high, >=9 very high)")
        fo.add(55, lin(soil["lep"], 2, 9), f"{lep_class(soil['lep'])} shrink-swell soil: LEP {soil['lep']}% ({soil.get('component')})")
    fz = h.get("flood_zone")
    if fz:
        fo.s("FEMA flood zone", fz, "", "FEMA NFHL (houses.json)")
        fo.add(20, 1.0 if str(fz)[:1] in ("A", "V") else 0.4 if "0.2" in str(fz) else 0.0,
               f"FEMA flood zone {fz}")
    t["foundation"] = fo.out("foundation")

    # ================= flood_water
    fw = T()
    if fz:
        fw.s("FEMA flood zone", fz, "", "FEMA NFHL (houses.json)")
        fw.add(50, 1.0 if str(fz)[:1] in ("A", "V") else 0.45 if "0.2" in str(fz) else 0.25 if "levee" in str(fz) else 0.0, f"FEMA flood zone {fz}")
    ring_g = L.cells_in(P.buffer(2.0).difference(P))
    near_g = L.cells_in(P.buffer(30.0))
    gz = L.gz.ravel()
    hg = gz[ring_g]; ng = gz[near_g]
    hg, ng = hg[np.isfinite(hg)], ng[np.isfinite(ng)]
    if len(hg) >= 5 and len(ng) >= 20:
        h_ground = float(np.median(hg)); low = float(np.percentile(ng, 2)); hi_ = float(np.percentile(ng, 98))
        above = h_ground - low
        rank = float((ng < h_ground).mean() * 100)
        fw.s("House ground above the lowest ground within 30 m", round(above, 2), "m", f"{S_LIDAR} ground returns")
        fw.s("Ground relief within 30 m (2nd-98th pct)", round(hi_ - low, 2), "m", f"{S_LIDAR} ground returns")
        fw.s("Share of ground within 30 m lower than the house", round(rank), "%", f"{S_LIDAR} ground returns")
        part = (1 - lin(rank, 10, 60)) * (lin(hi_ - low, 0.3, 2.0) or 0)
        fw.add(50, part, f"only {rank:.0f}% of the ground within 30 m is lower than the house (sits {above:.2f} m above the local low, relief {hi_ - low:.1f} m)")
    t["flood_water"] = fw.out("flood_water")

    # ================= radon
    ra_ = T()
    if A["radon_zone"] is not None:
        used.add("earth")
        ra_.s("EPA radon zone (county)", A["radon_zone"], "1 high - 3 low", "EPA Map of Radon Zones")
        ra_.add(65, {1: 1.0, 2: 0.5, 3: 0.1}.get(int(A["radon_zone"]), 0), f"county is EPA radon zone {A['radon_zone']}")
    if A["nure_eu_ppm"] is not None:
        ra_.s("Equivalent uranium in soil/rock (county mean)", A["nure_eu_ppm"], "ppm", "USGS NURE gamma (Goldman & Ellefsen 2020)")
        ra_.add(35, lin(A["nure_eu_ppm"], 1.0, 4.0), f"NURE eU {A['nure_eu_ppm']} ppm (county mean)")
    t["radon"] = ra_.out("radon")

    # ================= pests
    pe = T()
    wl = ctx["wet"].get(h["id"]) if ctx.get("wet") is not None else None
    if wl is not None:
        used.add("nwi")
        pe.s("NWI wetlands within 300 m", wl["n"], "polygons", "USFWS National Wetlands Inventory")
        if wl["nearest_m"] is not None:
            pe.s("Nearest wetland", wl["nearest_m"], "m", f"USFWS NWI ({wl['nearest_type']})")
        pe.add(40, (1 - lin(wl["nearest_m"], 30, 300)) if wl["nearest_m"] is not None else 0.0,
               f"{wl['nearest_type']} wetland {wl['nearest_m']:.0f} m away ({wl['n']} within 300 m)" if wl["nearest_m"] is not None else None)
    gp = ctx["green_pools"]
    if gp is not None:
        used.add("pools")
        pe.s("Houses in the block with a green pool seen on NAIP (any year)", gp, "houses", "usa/pools.py (houses.json pool)")
        pe.add(20, min(1.0, gp / 2), f"{gp} house(s) in the block had a green pool on aerial imagery (mosquito breeding)")
    c_all, _, _ = L.cover(P.buffer(10.0).difference(P), 2.0)
    pe.s("Canopy (>= 2 m) within 10 m of the house", c_all, "%", S_LIDAR)
    pe.add(40, lin(c_all, 5, 60), f"{c_all}% canopy within 10 m of the house")
    t["pests"] = pe.out("pests")

    # ================= wildfire (defensible space, vegetation only)
    wf = T()
    z0, _, _ = L.cover(P.buffer(1.5).difference(P), 0.5, multi_only=True)
    z1, z1max, _ = L.cover(P.buffer(9.0).difference(P.buffer(1.5)), 1.0)
    z2, _, _ = L.cover(P.buffer(30.0).difference(P.buffer(9.0)), 1.0)
    wf.s("Vegetation >= 0.5 m in 0-1.5 m zone (Zone 0)", z0, "%", S_LIDAR)
    wf.s("Vegetation >= 1 m in 1.5-9 m zone (Zone 1)", z1, "%", S_LIDAR)
    wf.s("Vegetation >= 1 m in 9-30 m zone (Zone 2)", z2, "%", S_LIDAR)
    wf.add(45, lin(z0, 5, 50), f"{z0}% of the 1.5 m strip around the house is vegetated")
    wf.add(35, lin(z1, 10, 60), f"{z1}% vegetation cover 1.5-9 m from the house (max {z1max} m)")
    wf.add(20, lin(z2, 15, 70), f"{z2}% vegetation cover 9-30 m from the house")
    t["wildfire"] = wf.out("wildfire")

    # ================= pool
    pr = pool_rows.get(h["id"]) if pool_rows else None
    if pr and pr.get("lead"):
        used.add("pools")
        po = T()
        po.s("Pool lead score (pool_service.json)", pr["lead"].get("score"), "0-100", "pool_service.json (lane PS1)")
        if pr.get("state_now"):
            po.s("Pool state in latest image", pr["state_now"], pr.get("state_now_date") or "", "usa/pools.py (NAIP)")
        if pr.get("area_m2") is not None:
            po.s("Pool water area", pr["area_m2"], "m2", "usa/pools.py (NAIP)")
        po.reasons = list(pr["lead"].get("reasons") or [])
        sc_ = num(pr["lead"].get("score"))
        t["pool"] = {"score": int(sc_) if sc_ is not None else None, "signals": po.signals, "reasons": po.reasons,
                     "next": (NEXT["pool"] if sc_ is not None and sc_ >= 30 else None)}
    return {k: v for k, v in ((tid, t.get(tid)) for tid, _, _ in TRADES) if v}


# ---------------------------------------------------------------- block
def build(slug):
    d = BLOCKS / slug
    meta, hs = load(d / "meta.json"), load(d / "houses.json")
    if meta is None or hs is None:
        return None
    hs = hs["houses"] if isinstance(hs, dict) else hs
    homes = [h for h in hs if not h.get("garage") and h.get("ring")]
    earth, wt = load(DATA / "earth_county.json"), load(DATA / "weather_triggers.json")
    A = area(meta, earth, wt)
    used = {"lidar", "houses"}
    if A["hdd"] is not None or A["snow_in"] is not None:
        used.add("normals")
    L = Lidar(d, meta) if (d / "points_0.txt").exists() else None
    trees = load(d / "trees.json") or []
    st = load(d / "streets.json") or {}
    rp = []
    for rd in st.get("roads") or []:
        try:
            pg = Polygon([(p[0], p[1]) for p in rd.get("poly") or []])
            if pg.area > 1:
                rp.append(pg.buffer(0))
        except Exception:  # noqa: BLE001
            pass
    roads = unary_union(rp) if rp else None
    snow = {}
    sj = load(d / "snowmelt.json")
    if isinstance(sj, dict):
        for x in sj.get("houses") or []:
            snow[str(x.get("house_id"))] = x
    ps = load(d / "pool_service.json")
    pool_rows = {p.get("house_id"): p for p in (ps or {}).get("pools") or []} if isinstance(ps, dict) else {}
    green = sum(1 for h in hs if isinstance(h.get("pool"), dict) and h["pool"].get("green_years"))
    has_pool_data = any(isinstance(h.get("pool"), dict) for h in hs)
    ctx = {"green_pools": green if has_pool_data else None, "soil": {}, "wet": None}

    # soil (per house, cached) and wetlands (one query per block, distances per house)
    def soil_one(h):
        lo, la = num(h.get("lon")), num(h.get("lat"))
        return h["id"], (lep_at(lo, la) if lo is not None else None)
    with ThreadPoolExecutor(4) as ex:
        ctx["soil"] = dict(ex.map(soil_one, homes))
    lon0, lat0 = meta["origin"]
    wl = wetlands(lon0, lat0, 300 + int(meta.get("half_m", 50) * 1.5))
    if wl is not None:
        kx = 111320 * math.cos(math.radians(lat0)); ky = 110540
        polys = []
        for f in wl:
            try:
                rings = [[((x - lon0) * kx, (y - lat0) * ky) for x, y in r] for r in f["rings"]]
                pg = Polygon(rings[0], rings[1:]).buffer(0)
                polys.append((pg, f["type"]))
            except Exception:  # noqa: BLE001
                continue
        ctx["wet"] = {}
        for h in homes:
            lo, la = num(h.get("lon")), num(h.get("lat"))
            if lo is None:
                continue
            hp = Point((lo - lon0) * kx, (la - lat0) * ky)
            ds = sorted((pg.distance(hp), tp) for pg, tp in polys)
            ds = [x for x in ds if x[0] <= 300]
            ctx["wet"][h["id"]] = {"n": len(ds), "nearest_m": round(ds[0][0]) if ds else None, "nearest_type": ds[0][1] if ds else None}

    out_h = []
    for h in homes:
        P = ring_poly(h)
        if P is None or L is None:
            continue
        tr = house_trades(h, P, L, A, ctx, trees, roads, snow, pool_rows, used)
        out_h.append({"house_id": h["id"], "address": h.get("address"), "trades": tr})

    blk = {}
    for tid, label, _ in TRADES:
        rows = [(x["trades"][tid]["score"], x) for x in out_h if tid in x["trades"]]
        scored = sorted([r for r in rows if r[0] is not None], key=lambda r: -r[0])
        if not rows:
            continue
        top = [x["house_id"] for _, x in scored[:5]]
        if scored:
            s0, x0 = scored[0]
            why = (x0["trades"][tid]["reasons"] or ["no scored part above zero"])[0]
            summ = (f"{len(rows)} of {len(out_h)} houses have {label.lower()} signals; top {x0['address'] or 'house #' + str(x0['house_id'])} "
                    f"scores {s0} ({why}); median {int(np.median([r[0] for r in scored]))}")
        else:
            summ = f"{len(rows)} houses have {label.lower()} signals but no area value to score them (see climate)"
        blk[tid] = {"top": top, "summary": summ}

    if any(x["trades"].get("pool") for x in out_h):
        used.add("pools")
    if A["dust_days_yr"] is not None:
        used.add("weather")
    if A["radon_zone"] is not None or A["pm25_dv"] is not None:
        used.add("earth")
    clim = {k: A[k] for k in ("county_fips", "county", "hdd", "cdd", "snow_in", "snow_days_ge1in", "snow_depth_days_ge1in",
                              "dust_days_yr", "high_wind_days_yr", "hail_days_yr", "pm25_dv", "o3_dv", "radon_zone", "nure_eu_ppm")}
    clim["units"] = {"hdd": "F-days/yr (base 65 F)", "cdd": "F-days/yr (base 65 F)", "snow_in": "in/yr", "snow_days_ge1in": "days/yr",
                     "snow_depth_days_ge1in": "days/yr", "dust_days_yr": "days/yr", "high_wind_days_yr": "days/yr", "hail_days_yr": "days/yr",
                     "pm25_dv": "ug/m3 annual DV 2023-2025", "o3_dv": "ppb 8-h DV 2023-2025", "radon_zone": "1 high - 3 low", "nure_eu_ppm": "ppm"}
    clim["normals_station_hdd"] = A["hdd_station"]; clim["normals_station_snow"] = A["snow_station"]
    clim["gnss"] = A["gnss"]
    clim["green_pools_in_block"] = ctx["green_pools"]
    clim["wetlands_queried"] = ctx["wet"] is not None
    clim["lidar"] = A["lidar"]; clim["lidar_year"] = A["lidar_year"]
    clim["snowmelt_json"] = bool(snow); clim["pool_service_json"] = bool(pool_rows)
    clim["not_available"] = ["pollen counts (no open, commercial-OK national pollen data)", "LANDFIRE fuel/hazard (not used; wildfire = LiDAR vegetation only)",
                             "real paving extent (snow_ice uses ground in the house's shadow, not mapped pavement)",
                             "service history of any trade (not in open data)"]
    return {"slug": slug, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "version": VERSION,
            "sources": [{k2: SOURCES[k][k2] for k2 in ("name", "licence", "url")} for k in SOURCES if k in used],
            "trades": [{"id": a, "label": b, "blurb": c} for a, b, c in TRADES],
            "houses": out_h, "block": {"trades": blk, "climate": clim}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append")
    a = ap.parse_args()
    idx = load(BLOCKS / "index.json") or []
    slugs = a.slug or [b["slug"] for b in idx]
    cov = {tid: [0, 0] for tid, _, _ in TRADES}
    tot = 0
    for s in slugs:
        t0 = time.time()
        try:
            out = build(s)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc(); print(f"{s}: FAILED {e}"); continue
        if out is None:
            print(f"{s}: no meta/houses, skipped"); continue
        f = BLOCKS / s / "maintenance.json"
        tmp = f.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, f)
        n = len(out["houses"]); tot += n
        for tid in cov:
            have = [x for x in out["houses"] if tid in x["trades"]]
            cov[tid][0] += len(have); cov[tid][1] += sum(1 for x in have if x["trades"][tid]["score"] is not None)
        c = out["block"]["climate"]
        print(f"== {s}: {n} houses, HDD {c['hdd']} CDD {c['cdd']} snow {c['snow_in']} in, radon {c['radon_zone']}, "
              f"wetlands {'Y' if c['wetlands_queried'] else '-'}, snowmelt {'Y' if c['snowmelt_json'] else '-'}, pool_service {'Y' if c['pool_service_json'] else '-'} "
              f"({time.time() - t0:.0f}s)")
    print(f"\ncoverage over {tot} houses in {len(slugs)} blocks (trade: houses with signals / with a score)")
    for tid, (a_, b_) in cov.items():
        print(f"  {tid:<16} {a_:>4} / {b_:>4}   ({100 * a_ / max(tot, 1):.0f}%)")


if __name__ == "__main__":
    main()

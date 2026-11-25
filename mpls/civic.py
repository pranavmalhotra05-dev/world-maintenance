"""City / federal / OpenStreetMap civic layers for the block (keyless; CC0, public domain or ODbL only).

Per house: code violations + inspections, zoning (City of Minneapolis, CC0).
Map layers: crime (last 12 months, block-anonymised positions only; CC0), snow-emergency zones (CC0),
speed limits + traffic signals (OpenStreetMap maxspeed / highway=traffic_signals from the local Geofabrik extract, ODbL),
street traffic volume (FHWA HPMS public sections AADT, public domain), places of worship (HIFLD open),
public schools (NCES EDGE, public domain).
Rooftop solar: our own LiDAR sun model (houses[].sun from mpls/derive.py) - the city's solar-suitability layer is not used.
Writes houses.json (adds fields; merged with the latest file on disk), civic.json and its entries in sources.json.
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from shapely.geometry import Point, Polygon, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usa"))
import osm_extract  # noqa: E402

OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json")); LON, LAT = meta["origin"]; W, So, E, N = meta["bounds"]
houses = json.load(open(OUT / "houses.json"))
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
MPLS = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
HPMS = "https://geo.dot.gov/server/rest/services/Hosted"
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1 (open-data research)"
loc = lambda lon, lat: (round((lon - LON) * MX, 1), round((lat - LAT) * MY, 1))
CC0 = "CC0 1.0 (City of Minneapolis open data)"
AGO = "https://www.arcgis.com/home/item.html?id="


def q(url, box, where="1=1", fields="*", fmt="geojson"):
    w, s, e, n = box
    r = S.get(url, params={"where": where, "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
                           "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "f": fmt, "resultRecordCount": 4000}, timeout=180)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(str(d["error"])[:200])
    return d.get("features", [])


def grow(m):
    return (W - m / MX, So - m / MY, E + m / MX, N + m / MY)


civic, status, sources = {}, {}, []


def safe(name, fn, src=None):
    try:
        civic[name] = fn(); status[name] = "ok"; print("ok  ", name)
        if src:
            sources.append({**src, "by": "civic.py"})
    except Exception as e:  # noqa: BLE001
        status[name] = f"{type(e).__name__}: {e}"[:200]; print("FAIL", name, status[name])


since = int((datetime.now(timezone.utc) - timedelta(days=365)).timestamp() * 1000)


def crime():
    fs = q(MPLS + "Crime_Data/FeatureServer/0/query", grow(150), fields="Reported_Date,Offense_Category,Offense,NIBRS_Crime_Against,wgsXAnon,wgsYAnon")
    out, cats = [], {}
    for f in fs:
        p = f["properties"]
        if (p.get("Reported_Date") or 0) < since or p.get("wgsXAnon") is None:
            continue
        x, y = loc(p["wgsXAnon"], p["wgsYAnon"])
        out.append({"x": x, "y": y, "cat": p.get("Offense_Category"), "against": p.get("NIBRS_Crime_Against"),
                    "date": datetime.fromtimestamp(p["Reported_Date"] / 1000, timezone.utc).strftime("%Y-%m-%d")})
        cats[p.get("Offense_Category")] = cats.get(p.get("Offense_Category"), 0) + 1
    return {"points": out, "by_category": dict(sorted(cats.items(), key=lambda kv: -kv[1])), "note": "positions are anonymised by the city to the block, not the house"}


def violations():
    fs = q(MPLS + "CaseViolations/FeatureServer/0/query", grow(20), fields="Display,APN,Case_Type,Violation_Code_Description,Status_Violation,Processed_Date,Resolved_Date,Latitude,Longitude")
    ins = q(MPLS + "CaseInspections/FeatureServer/0/query", grow(20), fields="Display,Inspection_Type_Desc,Inspection_Result,Completed_Date,Latitude,Longitude")
    return {"violations": [f["properties"] for f in fs], "inspections": [f["properties"] for f in ins]}


def zoning():
    return [{"code": f["properties"].get("Land_Use_Code"), "use": f["properties"].get("Land_Use"), "geom": f["geometry"]} for f in q(MPLS + "Planning_Primary_Zoning/FeatureServer/0/query", (W, So, E, N))]


def snow():
    return [{"rules": f["properties"].get("WINTERRULES"), "day1": f["properties"].get("DAY1"), "day2": f["properties"].get("DAY2"), "day3": f["properties"].get("DAY3"),
             "rings": [[list(loc(*c[:2])) for c in ring] for ring in (f["geometry"]["coordinates"] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"][0])][:1]}
            for f in q(MPLS + "Snow_Emergency_Routes/FeatureServer/0/query", (W, So, E, N)) if f.get("geometry")]


# ---------------------------------------------------------------- OpenStreetMap (local Geofabrik extract, ODbL)
def civic_osm_match(kind, t):
    return kind == "n" or "maxspeed" in t


_osm = {}


def osm():
    if "d" not in _osm:
        _osm["d"] = osm_extract.query("MN", nodes=[("highway", "traffic_signals")], ways=[("highway", None)],
                                      bbox=grow(700), match=civic_osm_match)
    return _osm["d"]


def mph(v):
    v = str(v or "").strip().lower()
    num = "".join(ch for ch in v.split(";")[0] if ch.isdigit() or ch == ".")
    if not num:
        return None
    x = float(num)
    return round(x if "mph" in v else x / 1.609344)  # untagged unit = km/h (OSM convention)


def speed():
    out = []
    for f in osm():
        if f["type"] != "w" or "maxspeed" not in f["tags"] or len(f.get("coords") or []) < 2:
            continue
        lons = [c[0] for c in f["coords"]]; lats = [c[1] for c in f["coords"]]
        if max(lons) < W - 50 / MX or min(lons) > E + 50 / MX or max(lats) < So - 50 / MY or min(lats) > N + 50 / MY:
            continue  # keep ways touching the block (+50 m)
        t = f["tags"]
        out.append({"path": [list(loc(*c[:2])) for c in f["coords"]], "street": t.get("name") or t.get("ref"),
                    "mph": mph(t["maxspeed"]), "bus": None, "osm_id": f["id"]})
    return out


def signals():
    return [{"x": loc(f["lon"], f["lat"])[0], "y": loc(f["lon"], f["lat"])[1], "name": f["tags"].get("name") or "", "type": f["tags"].get("traffic_signals"),
             "osm_id": f["id"]} for f in osm() if f["type"] == "n" and f["tags"].get("highway") == "traffic_signals"]


# ---------------------------------------------------------------- FHWA HPMS AADT (public domain)
def aadt():
    svcs = [s_["name"].split("/")[-1] for s_ in S.get(HPMS, params={"f": "json"}, timeout=120).json()["services"]
            if s_["type"] == "FeatureServer" and s_["name"].split("/")[-1].upper().startswith("HPMS_FULL_MN_")]
    name = max(svcs, key=lambda n: n.rsplit("_", 1)[-1])
    out = []
    for f in q(f"{HPMS}/{name}/FeatureServer/0/query", grow(400), where="aadt > 0", fields="route_name,aadt,data_year,f_system,facility_type"):
        p = f["properties"]; g = f.get("geometry")
        if not g:
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for part in parts:
            out.append({"path": [list(loc(*c[:2])) for c in part], "aadt": p.get("aadt"), "name": p.get("route_name"), "year": p.get("data_year")})
    civic["aadt_source"] = f"FHWA HPMS {name}"
    return out


def worship():
    fs = q("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/All_Places_Of_Worship__HiFLD_Open_/FeatureServer/42/query", grow(1500))
    out = []
    for f in fs:
        lo, la = f["geometry"]["coordinates"][:2]; p = f["properties"]
        name = next((p[k] for k in p if k.upper() == "NAME"), None); sub = next((p[k] for k in p if k.upper() in ("SUBTYPE", "DENOM", "RELIGION")), None)
        out.append({"name": name, "kind": sub, "m": round(math.hypot((lo - LON) * MX, (la - LAT) * MY)), "x": loc(lo, la)[0], "y": loc(lo, la)[1]})
    return sorted(out, key=lambda r: r["m"])


def schools():
    fs = q("https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0/query", grow(1500), fields="NAME,STREET,CITY")
    return sorted([{"name": f["properties"].get("NAME"), "m": round(math.hypot((f["geometry"]["coordinates"][0] - LON) * MX, (f["geometry"]["coordinates"][1] - LAT) * MY))} for f in fs], key=lambda r: r["m"])


OSM_SRC = {"url": "https://download.geofabrik.de/north-america/us/minnesota.html", "licence": "ODbL 1.0 - " + osm_extract.ATTRIBUTION,
           "licence_url": "https://www.openstreetmap.org/copyright"}
for name, fn, src in [
    ("crime", crime, {"name": "City of Minneapolis crime data", "detail": "last 12 months, block-anonymised positions", "url": "https://opendata.minneapolismn.gov",
                      "licence": CC0, "licence_url": AGO + "68c6de8cf6a047e888ceb6f507010851"}),
    ("violations", violations, {"name": "City of Minneapolis housing inspections and code violations", "detail": "per house", "url": "https://opendata.minneapolismn.gov",
                                "licence": CC0, "licence_url": AGO + "96f3ea4697a64e0abaa2f33853048e0b"}),
    ("zoning", zoning, {"name": "City of Minneapolis primary zoning", "detail": "zoning district per house", "url": "https://opendata.minneapolismn.gov",
                        "licence": CC0, "licence_url": AGO + "5dd8138c953a4555b17caf44d777c5fb"}),
    ("snow", snow, {"name": "City of Minneapolis snow emergency routes", "detail": "parking rules by day", "url": "https://opendata.minneapolismn.gov",
                    "licence": CC0, "licence_url": AGO + "9a1e00d0a52d4e8db2a0b85ad3495fd6"}),
    ("speed", speed, {"name": "OpenStreetMap speed limits", "detail": "maxspeed tags on streets touching the block", **OSM_SRC}),
    ("signals", signals, {"name": "OpenStreetMap traffic signals", "detail": "highway=traffic_signals within 700 m", **OSM_SRC}),
    ("aadt", aadt, {"name": "FHWA HPMS traffic volumes (AADT)", "detail": "Minnesota public sections within 400 m", "url": HPMS + " (HPMS_FULL_MN_<year>)",
                    "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://www.fhwa.dot.gov/policyinformation/hpms.cfm"}),
    ("worship", worship, {"name": "HIFLD places of worship", "detail": "within 1.5 km", "url": "https://hifld-geoplatform.hub.arcgis.com",
                          "licence": "HIFLD Open: 'has no use limitations'", "licence_url": AGO + "495cc33ef490462ab2d8933247a66a87"}),
    ("schools", schools, {"name": "NCES EDGE public school locations 2023-24", "detail": "within 1.5 km", "url": "https://nces.ed.gov/programs/edge/",
                          "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://www.copyright.gov/title17/92chap1.html#105"}),
]:
    safe(name, fn, src)

# ---------------------------------------------------------------- per-house joins
zones = [(shape(z["geom"]), z) for z in civic.get("zoning", [])]
viol = civic.get("violations", {})
per = {}
for h in houses:
    poly = Polygon(h["ring"]); c = poly.centroid
    lon_c, lat_c = LON + c.x / MX, LAT + c.y / MY
    z = next((zz for g, zz in zones if g.contains(Point(lon_c, lat_c))), None)
    addr = (h.get("address") or "").upper()
    match = lambda r: addr and (r.get("Display") or "").upper().startswith(addr)
    per[h["id"]] = {
        "zoning": {"code": z["code"], "use": z["use"]} if z else None,
        "violations": [{"date": datetime.fromtimestamp(v["Processed_Date"] / 1000, timezone.utc).strftime("%Y-%m-%d") if v.get("Processed_Date") else None,
                        "type": v.get("Case_Type"), "what": v.get("Violation_Code_Description"), "status": v.get("Status_Violation")}
                       for v in viol.get("violations", []) if match(v)],
        "inspections": [{"date": datetime.fromtimestamp(v["Completed_Date"] / 1000, timezone.utc).strftime("%Y-%m-%d") if v.get("Completed_Date") else None,
                         "type": v.get("Inspection_Type_Desc"), "result": v.get("Inspection_Result")} for v in viol.get("inspections", []) if match(v)]}

civic.pop("zoning", None); civic.pop("violations", None)
civic["status"] = status
fresh = json.load(open(OUT / "houses.json"))  # merge onto the latest file: other scripts add fields too
for h in fresh:
    h.pop("solar_city", None)  # the city's solar-suitability layer is no longer used (grey licence)
    if h["id"] in per:
        h.update(per[h["id"]])
json.dump(fresh, open(OUT / "houses.json", "w"), separators=(",", ":"))
json.dump(civic, open(OUT / "civic.json", "w"), separators=(",", ":"))
try:
    other = [s_ for s_ in json.load(open(OUT / "sources.json")) if s_.get("by") and s_["by"] != "civic.py"]
except Exception:  # noqa: BLE001
    other = []
json.dump(other + sources, open(OUT / "sources.json", "w"), indent=1)
print("houses with violations:", sum(1 for h in fresh if h.get("violations")), "| zoning:", sum(1 for h in fresh if h.get("zoning")),
      "| speed segments:", len(civic.get("speed", [])), "| signals:", len(civic.get("signals", [])), "| aadt:", len(civic.get("aadt", [])))

"""Infrastructure and public facilities around the block (all keyless; public domain / city or Met Council open data).

Per house: nearest hydrant, catch basin, transmission line, rail line, grade crossing, transit stop, school,
fire/EMS, police, hospital, post office, park; water system (EPA CWS).
Map layers: hydrants, catch basins, rail, crossings, transit stops,
transitways, bridges (with condition), tunnels, schools, facilities, parks, transmission lines.
Context: power plants, product pipelines, wastewater plants, dams, airports within reach.
HIFLD Open was retired 2025-08-26; layers below come from the agencies themselves (no Esri-hosted copies):
  power plants        EIA-860M monthly generator inventory (eia.gov, US federal work, public domain:
                      "U.S. government publications are in the public domain" - eia.gov/about/copyrights_reuse.php)
  transmission lines  OpenStreetMap power=line / minor_line / cable (ODbL 1.0) from the local Geofabrik extract
  product pipelines   OpenStreetMap man_made=pipeline (oil / fuel / gas / petroleum products, ODbL 1.0), same extract.
                      The EIA Energy Atlas pipeline/plant services now require a token (checked 2026-11-24), so they
                      cannot be used keylessly.
"""
import json
import math
from pathlib import Path

import sys

import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, Point, shape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json")); LON, LAT = meta["origin"]; W, So, E, N = meta["bounds"]
houses = json.load(open(OUT / "houses.json"))
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1 (open-data research)"
MPLS = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
NTAD = "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
METC = "https://arcgis.metc.state.mn.us/"
loc = lambda lon, lat: [round((lon - LON) * MX, 1), round((lat - LAT) * MY, 1)]


def merge_json_houses(path, houses, keys=(), dict_keys=(), sub=None, drop=()):
    """Re-read houses.json just before writing and merge ONLY this script's keys (other lanes write other keys).
    keys: replaced whole; dict_keys: dict-merged (other sub-keys kept); sub: {key: [sub-keys]} set inside a dict; drop: removed."""
    cur = json.load(open(path, encoding="utf-8"))
    mine = {h.get("id", i): h for i, h in enumerate(houses)}
    for i, c in enumerate(cur):
        h = mine.get(c.get("id", i))
        if h is None:
            continue
        for k in keys:
            if k in h:
                c[k] = h[k]
        for k in dict_keys:
            if k in h:
                c[k] = h[k] if not isinstance(h[k], dict) else {**(c.get(k) if isinstance(c.get(k), dict) else {}), **h[k]}
        for k, subs in (sub or {}).items():
            if isinstance(h.get(k), dict):
                if not isinstance(c.get(k), dict):
                    c[k] = {}
                for s_ in subs:
                    if s_ in h[k]:
                        c[k][s_] = h[k][s_]
        for k in drop:
            c.pop(k, None)
    tmp = Path(str(path) + ".tmp")
    json.dump(cur, open(tmp, "w", encoding="utf-8"), separators=(",", ":"))
    tmp.replace(path)


def merge_json_dict(path, new, keys):
    """Same for a dict file (layers.json / extras.json): re-read, set only `keys`."""
    cur = json.load(open(path, encoding="utf-8")) if Path(path).exists() else {}
    for k in keys:
        if k in new:
            cur[k] = new[k]
    tmp = Path(str(path) + ".tmp")
    json.dump(cur, open(tmp, "w", encoding="utf-8"), separators=(",", ":"))
    tmp.replace(path)
sys.path.insert(0, str(ROOT / "usa"))
import osm_extract  # noqa: E402
OSM_ATTR = osm_extract.ATTRIBUTION


def box(m):
    return (W - m / MX, So - m / MY, E + m / MX, N + m / MY)


def q(url, m=0, where="1=1", fields="*", point=False):
    geom = f"{LON},{LAT}" if point else ",".join(map(str, box(m)))
    r = S.get(url + "/query", params={"where": where, "geometry": geom, "geometryType": "esriGeometryPoint" if point else "esriGeometryEnvelope",
                                     "inSR": 4326, "outSR": 4326, "outFields": fields, "f": "geojson", "resultRecordCount": 4000}, timeout=120)
    r.raise_for_status()
    return r.json().get("features", [])


def name_of(p):
    for k in p:
        if k.upper() in ("NAME", "SCHOOL_NAME", "SCH_NAME", "PARK_NAME1", "STATION_NAME", "FACILITY_NAME", "PWS_NAME", "PWSNAME", "NAME1", "FAC_NAME"):
            if p[k]:
                return str(p[k]).strip()
    if p.get("site_on"):  # Met Council transit stops
        return f"{p['site_on']} & {p.get('site_at') or ''}".strip(" &")
    return None


def as_points(feats, keep=()):
    out = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        c = shape(g).centroid if g["type"] != "Point" else Point(g["coordinates"][:2])
        out.append({"xy": loc(c.x, c.y), "name": name_of(f["properties"]), **{k: f["properties"].get(k) for k in keep if k in f["properties"]}})
    return out


def as_lines(feats, keep=()):
    out = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]] if g["type"] == "LineString" else []
        for part in parts:
            out.append({"path": [loc(c[0], c[1]) for c in part], "name": name_of(f["properties"]), **{k: f["properties"].get(k) for k in keep if k in f["properties"]}})
    return out


L, status = {}, {}


def safe(name, fn):
    try:
        L[name] = fn(); status[name] = f"ok ({len(L[name]) if isinstance(L[name], list) else 1})"; print("ok  ", name, status[name])
    except Exception as e:
        status[name] = f"{type(e).__name__}: {e}"[:200]; print("FAIL", name, status[name])


# point layers
safe("hydrants", lambda: as_points(q(MPLS + "Hydrants/FeatureServer/0", 150)))
safe("catch_basins", lambda: as_points(q(MPLS + "Stormwater_Catch_Basins/FeatureServer/0", 150)))
safe("crossings", lambda: as_points(q(NTAD + "NTAD_Railroad_Grade_Crossings/FeatureServer/0", 2500)))
# Met Council Transit Stops (public domain under MGDPA; current) - BTS NTM stops are older/incomplete for this block
safe("transit_stops", lambda: as_points(q(METC + "data2/rest/services/transportation/Transit_Stops/FeatureServer/0", 1200, where="active_status='Y'"), keep=("site_on", "site_at", "RoutesServed")))
safe("bridges", lambda: as_points(q(NTAD + "NTAD_National_Bridge_Inventory/FeatureServer/0", 2500), keep=("YEAR_BUILT_027", "ADT_029", "DECK_COND_058", "SUPERSTRUCTURE_COND_059", "SUBSTRUCTURE_COND_060", "FACILITY_CARRIED_007", "FEATURES_DESC_006A")))
safe("tunnels", lambda: as_points(q(NTAD + "NTAD_National_Tunnel_Inventory/FeatureServer/0", 4000)))
safe("schools_public", lambda: as_points(q("https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2324/MapServer/0", 2500)))
safe("schools_private", lambda: as_points(q("https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PRIVATESCH_2122/MapServer/0", 2500)))
USGS = "https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer/"
safe("hospitals", lambda: as_points(q(USGS + "14", 6000)))
safe("fire_ems", lambda: as_points(q(USGS + "16", 4000)))
safe("police", lambda: as_points(q(USGS + "18", 5000)))
safe("post_offices", lambda: as_points(q(USGS + "3", 3000)))
safe("parks", lambda: as_points(q(MPLS + "Parks/FeatureServer/0", 1500)))
safe("wastewater_plants", lambda: as_points(q("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/FRS_Wastewater/FeatureServer/0", 25000)))
def eia_plants(m=25000):
    """EIA-860M (latest monthly 'Operating' sheet), generators summed per plant, within the box."""
    import re
    page = S.get("https://www.eia.gov/electricity/data/eia860m/", timeout=60).text
    cache = ROOT / "usa" / "_cache" / "eia"; cache.mkdir(parents=True, exist_ok=True)
    df = None
    for name in dict.fromkeys(re.findall(r"xls/([a-z]+_generator\d{4}\.xlsx)", page)):
        f = cache / name
        if not f.exists():
            r = S.get("https://www.eia.gov/electricity/data/eia860m/xls/" + name, timeout=300)
            if not (r.ok and r.content[:2] == b"PK"):
                continue  # announced but not yet posted
            f.write_bytes(r.content)
        df = pd.read_excel(f, sheet_name="Operating", header=2); break
    if df is None:
        raise ValueError("no EIA-860M file reachable")
    w, s_, e, n = box(m)
    df = df[pd.to_numeric(df["Longitude"], errors="coerce").between(w, e) & pd.to_numeric(df["Latitude"], errors="coerce").between(s_, n)]
    out = []
    for pid, g in df.groupby("Plant ID"):
        r0 = g.iloc[0]
        out.append({"xy": loc(float(r0["Longitude"]), float(r0["Latitude"])), "name": str(r0["Plant Name"]).strip(),
                    "mw": round(float(pd.to_numeric(g["Nameplate Capacity (MW)"], errors="coerce").sum()), 1),
                    "tech": sorted(set(map(str, g["Technology"]))), "operator": str(r0["Entity Name"]), "eia_plant_id": int(pid),
                    "source": f"EIA-860M {name.split('.')[0]}"})
    return out


def osm_lines(pairs, m, match=None, keep=()):
    w, s_, e, n = box(m)
    out = []
    for f in osm_extract.query("MN", ways=pairs, bbox=(w, s_, e, n), match=match):
        t = f["tags"]
        kv = [float(v) / 1000 for v in re.split(r"[;,]", t.get("voltage", "")) if v.strip().replace(".", "", 1).isdigit()]
        out.append({"path": [loc(c[0], c[1]) for c in f["coords"]], "name": t.get("name") or t.get("operator"),
                    **({"VOLTAGE": max(kv)} if kv else {}), **({"OWNER": t["operator"]} if "operator" in t else {}),
                    **{k: t[k] for k in keep if k in t}, "osm": f"w{f['id']}"})
    return out


import re  # noqa: E402
PETRO = re.compile(r"oil|fuel|petroleum|gasoline|diesel|kerosene|jet|propane|lpg|ngl|hydrocarbon|gas", re.I)


def petro_pipe(kind, t):
    sub = t.get("substance", "") + " " + t.get("type", "")
    return PETRO.search(sub) is not None and "landfill" not in sub.lower()


safe("power_plants", lambda: eia_plants(25000))
safe("dams", lambda: as_points(q("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/National_Inventory_of_Dams_Public_Service/FeatureServer/0", 15000)))
safe("airports", lambda: as_points(q(NTAD + "NTAD_Aviation_Facilities/FeatureServer/0", 15000)))
# line layers
safe("rail", lambda: as_lines(q(NTAD + "NTAD_North_American_Rail_Network_Lines/FeatureServer/0", 2500)))
safe("transmission", lambda: osm_lines([("power", "line"), ("power", "minor_line"), ("power", "cable")], 8000, keep=("power", "cables", "circuits")))
# removed (licence audit 2026-11-24): Met Council interceptors + sewersheds ("do not transfer or share"), Minneapolis
# Sanitary_Mains / Storm_Mains 2019 (no licence text), TPP2050 transitway alignments ("FOR INTERNAL USE ONLY").
# Transitways: Met Council "Transitway Stations and Alignments" (item c920b202e0d44c62af7469757e761401, public domain under MGDPA)
safe("transitways", lambda: as_lines(q(METC + "data2/rest/services/transportation/Transitways/FeatureServer/1", 3000)))
safe("transitway_stations", lambda: as_points(q(METC + "data2/rest/services/transportation/Transitways/FeatureServer/0", 3000)))
safe("product_pipelines", lambda: osm_lines([("man_made", "pipeline")], 25000, match=petro_pipe, keep=("substance", "type", "location", "usage")))
# polygons containing the block
safe("water_system", lambda: [{k: v for k, v in f["properties"].items() if isinstance(v, (str, int, float))} for f in q("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Water_System_Boundaries/FeatureServer/0", point=True)][:1])

# ---- per-house nearest
def nearest_pt(layer, x, y):
    pts = L.get(layer) or []
    if not pts:
        return None
    d = [math.hypot(p["xy"][0] - x, p["xy"][1] - y) for p in pts]; i = int(np.argmin(d))
    return {"m": round(d[i]), "name": pts[i].get("name")}


def nearest_line(layer, x, y):
    ls = [l for l in (L.get(layer) or []) if len(l["path"]) >= 2]
    if not ls:
        return None
    pt = Point(x, y); best = min(ls, key=lambda l: LineString(l["path"]).distance(pt))
    return {"m": round(LineString(best["path"]).distance(pt)), "name": best.get("name"), **({"kv": best.get("VOLTAGE")} if "VOLTAGE" in best else {})}


for h in houses:
    r = np.array(h["ring"]); x, y = r[:-1].mean(0)
    h["nearby"] = {k: nearest_pt(k, x, y) for k in ("hydrants", "catch_basins", "crossings", "transit_stops", "schools_public", "schools_private",
                                                    "fire_ems", "police", "hospitals", "post_offices", "parks", "bridges")}
    h["nearby"].update({k: nearest_line(k, x, y) for k in ("rail", "transmission", "transitways")})
ws = (L.get("water_system") or [{}])[0]
utility = {"water_system": name_of(ws) or ws.get("PWS_Name") or ws.get("PWSID"), "pwsid": ws.get("PWSID") or ws.get("PWS_ID")}
for h in houses:
    h["utility"] = utility
merge_json_houses(OUT / "houses.json", houses, keys=("nearby", "utility"))
sources = {"power_plants": "EIA-860M monthly generator inventory (US EIA, public domain; 'Source: U.S. Energy Information Administration')",
           "transmission": OSM_ATTR + " - power=line/minor_line/cable", "product_pipelines": OSM_ATTR + " - man_made=pipeline, petroleum/gas substance"}
json.dump({"layers": L, "status": status, "utility": utility, "sources": sources,
           "not_open": ["Pipeline detail (PHMSA NPMS: viewer only, GIS restricted)", "Water, sanitary and storm networks and sewersheds (no openly licensed layer: Minneapolis mains have no licence text, Met Council utility layers forbid sharing)",
                        "Electric substations and distribution lines (no open federal layer)", "Local gas mains (not in NPMS; call 811)"]},
          open(OUT / "infra.json", "w"), separators=(",", ":"))
print("utility:", utility)

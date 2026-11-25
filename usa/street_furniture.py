"""Street furniture per 3D block: street lamps, stop / yield / speed-limit / other signs, traffic signals, PUBLIC
(government / traffic) camera locations, fire hydrants, utility poles, crossings -- at the EXACT coordinates the
source records, cross-checked against LiDAR pole detections from the block's own points.

    .venv\\Scripts\\python -u -W ignore -m usa.street_furniture [slug ...] [--no-city] [--png slug]

Owner rules (strict, see usa/research/street_furniture.md):
  * an item exists only if a data record says so (OSM tag or a licence-green city inventory). Nothing is inferred:
    no signals at intersections, no signs from way maxspeed, no relabelled LiDAR poles.
  * positions are the source's exact coordinates (never moved off the road); lon/lat + source id are kept.
  * heading_deg only from data (OSM direction / camera:direction in degrees or compass points, a city
    'direction_facing' field); otherwise null.
  * kind_class: 'physical_asset' (the object itself: lamp, hydrant, sign post, pole, an OSM traffic_sign /
    street_lamp node that is not on a carriageway) or 'control_point' (OSM highway=stop / give_way /
    traffic_signals / crossing nodes, and traffic_sign nodes that sit ON the carriageway way -- they mark the
    controlled point, not the pole; also city signal records located at the intersection).
  * LiDAR: thin vertical clusters are measured pole positions of unknown type -> kind 'unknown_pole',
    confirmed_by_lidar true. A data item with a LiDAR pole within 3 m is marked confirmed_by_lidar (its own
    position is kept; the pole offset + measured height are recorded) and that pole is not emitted again.
  * public cameras only: OSM surveillance=public|traffic, surveillance:zone=traffic, highway=speed_camera, or a
    government operator; surveillance=outdoor|indoor (private property) is always excluded. Locations only.

Output web/usa/blocks/<slug>/furniture.json, local metres east/north of meta.origin (Web-Mercator metres / k, the
house3d convention), z = ground (LiDAR ground returns) in metres above meta.z_base. meta.json gets
files += furniture.json and a 'furniture' summary (via usa.blocklock.block_lock).
furniture.json contains OSM data -> it is a Derivative Database under ODbL 1.0 (share-alike, attribution).
"""
import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402
from usa import osm_extract  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "furniture"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "roofprint-usa-furniture/0.1 (open-data research)"}
MATCH_M = 3.0
TO_M = Transformer.from_crs(4326, 3857, always_xy=True)

CAR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
       "service", "road", "busway", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
GOV = re.compile(r"city|county|state|department|dept|police|sheriff|\bdot\b|transport|traffic|government|"
                 r"municipal|public works|txdot|mndot|cdot|odot|adot|modot|aldot|highway patrol", re.I)
COMPASS = {d: i * 22.5 for i, d in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}
COMPASS.update({"NORTH": 0, "EAST": 90, "SOUTH": 180, "WEST": 270, "NORTHEAST": 45, "SOUTHEAST": 135,
                "SOUTHWEST": 225, "NORTHWEST": 315, "NB": 0, "EB": 90, "SB": 180, "WB": 270})

LIC = {
    "osm": {"name": "OpenStreetMap (Geofabrik state extract, read locally with pyosmium)", "licence": "ODbL 1.0",
            "url": "https://www.openstreetmap.org/copyright",
            "quote": "OpenStreetMap is open data, licensed under the Open Data Commons Open Database License (ODbL)",
            "extract_terms": osm_extract.GEOFABRIK_TERMS, "attribution": osm_extract.ATTRIBUTION},
    "lidar": {"name": "USGS 3DEP lidar (block point cloud, meta.lidar)", "licence": "Public domain (US federal)",
              "url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
              "quote": "USGS-authored or produced data and information are considered to be in the U.S. public domain"},
}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[furniture]", *a, flush=True)


# ----------------------------------------------------------------------------------------------- city sources
SOC_AUSTIN = "https://data.austintexas.gov/resource/{}.json"
AUSTIN_LIC = {"licence": "Public Domain (Socrata licenseId PUBLIC_DOMAIN)",
              "quote": "license: {\"name\": \"Public Domain\"} (dataset metadata, /api/views/<id>.json)"}
MPLS_LIC = {"licence": "CC0-style copyright waiver",
            "quote": "To the extent possible under law, City of Minneapolis has waived all copyright and related or "
                     "neighboring rights to Open Data."}
BOULDER_LIC = {"licence": "CC0 1.0", "terms_url": "https://bouldercolorado.gov/services/open-data#section-4148",
               "quote": "The City of Boulder Open Data Catalog provides open data licensed under the Creative Commons "
                        "CC Zero license (CC0 1.0)."}
DENVER_LIC = {"licence": "CC BY 3.0 (Denver Open Data Catalog terms)",
              "terms_url": "https://opendata-geospatialdenver.hub.arcgis.com/ (Terms of Use)",
              "quote": "The City and County of Denver Open Data Catalog provides open data licensed under the Creative "
                       "Commons Attribution 3.0 license (CC BY 3.0).",
              "note": "item licenseInfo itself is only a liability disclaimer; licence comes from the catalog terms "
                      "(same basis as the Denver entries in licence_registry_local.json)",
              "attribution": "Contains data from the City and County of Denver Open Data Catalog (CC BY 3.0)"}
SCOTTSDALE_LIC = {"licence": "ODbL 1.0", "quote": "Contains information from the City of Scottsdale Open Database "
                  "Portal, which is made available here under the Open Database License.",
                  "attribution": "Contains information from the City of Scottsdale Open Database Portal (ODbL)"}
CINCY_LIC = {"licence": "Public Domain (Socrata licenseId PUBLIC_DOMAIN)",
             "quote": "license: {\"name\": \"Public Domain\"} (dataset metadata, /api/views/qhw6-ujsg.json)"}

CITY = {  # slug-prefix city key -> list of layer specs
    "austin": [
        dict(id="austin_signs", kind="sign", soc="ggnk-3ykn", lic=AUSTIN_LIC,
             name="City of Austin Roadway Sign Assets", url="https://data.austintexas.gov/d/ggnk-3ykn",
             where="asset_status = 1 OR asset_status IS NULL", latlon=("latitude", "longitude"), numeric=True),
        dict(id="austin_signals", kind="traffic_signal", soc="p53x-x73x", lic=AUSTIN_LIC,
             name="City of Austin Traffic Signals and Pedestrian Signals", url="https://data.austintexas.gov/d/p53x-x73x"),
        dict(id="austin_cameras", kind="camera_public", soc="b4k4-adkb", lic=AUSTIN_LIC,
             name="City of Austin Traffic Cameras (locations only)", url="https://data.austintexas.gov/d/b4k4-adkb"),
    ],
    "cincinnati": [
        dict(id="cincy_hydrants", kind="fire_hydrant", soc="qhw6-ujsg", dom="data.cincinnati-oh.gov", lic=CINCY_LIC,
             name="Fire Hydrants (Greater Cincinnati Water Works)", url="https://data.cincinnati-oh.gov/d/qhw6-ujsg",
             latlon=("latitude", "longitude")),
    ],
    "minneapolis": [
        dict(id="mpls_hydrants", kind="fire_hydrant", lic=MPLS_LIC, name="City of Minneapolis Hydrants",
             url="https://www.arcgis.com/home/item.html?id=4d7fc1675df843c7bbf3b63f77bc19dc",
             fs="https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Hydrants/FeatureServer/0"),
        dict(id="mpls_planimetric", kind="planimetric", lic=MPLS_LIC, name="City of Minneapolis PW_Planimetric_Points",
             url="https://www.arcgis.com/home/item.html?id=ae864fe030134ceb8115aa95b4e97378",
             fs="https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/PW_Planimetric_Points/FeatureServer/0",
             where="SYMBOL_NAME IN ('HYDRANT','POWER_POLE','STREET_LIGHT','STREET_SIGN')"),
    ],
    "denver": [
        dict(id="denver_signals", kind="traffic_signal", lic=DENVER_LIC, name="Denver Traffic Signals",
             url="https://www.arcgis.com/home/item.html?id=b07e3baeaa37459981cb717e33ebab3a",
             fs="https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_TRANS_TRAFFICSIGNAL_P/FeatureServer/147"),
        dict(id="denver_halo", kind="camera_public", lic=DENVER_LIC, name="Denver Police HALO Cameras (locations only)",
             url="https://www.arcgis.com/home/item.html?id=c3c8ffc9f5a14c1b9859c4813f0bd565",
             fs="https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/POLICE_HALO_P/FeatureServer/0"),
    ],
    "boulder": [
        dict(id="boulder_lights", kind="street_lamp", lic=BOULDER_LIC, name="City of Boulder Contractor Streetlights (View)",
             url="https://www.arcgis.com/home/item.html?id=303d4f500ec8402d8b43029b65f189c4",
             fs="https://services.arcgis.com/ePKBjXrBZ2vEEgWd/arcgis/rest/services/Contractor_Streetlights_(View)/FeatureServer/0"),
        dict(id="boulder_signals", kind="traffic_signal", lic=BOULDER_LIC, name="City of Boulder Signalized Intersections",
             url="https://www.arcgis.com/home/item.html?id=e2b24cbef4d84fe4885439e37073eafa",
             fs="https://services.arcgis.com/ePKBjXrBZ2vEEgWd/arcgis/rest/services/Signalized_Intersection_FINAL_PUBLIC_VIEW/FeatureServer/0"),
        dict(id="boulder_rlc", kind="camera_public", lic=BOULDER_LIC, name="City of Boulder Red Light Cameras (locations only)",
             url="https://www.arcgis.com/home/item.html?id=48adb3d13b8e485b88315d4766eaa254",
             fs="https://services.arcgis.com/ePKBjXrBZ2vEEgWd/arcgis/rest/services/Red_Light_Cameras_PUBLIC_VIEW/FeatureServer/0"),
    ],
    # "scottsdale": Street Signs (OpenData MapServer/8) EXCLUDED -- the item says ODbL but the City of Scottsdale
    # portal data licence (item b056ccb8140b404a95fa7eb71d064151, s3.1) says "These rights explicitly exclude
    # commercial use." -> grey under the owner's rule. See usa/research/street_furniture.md.
}


def sign_kind(code, text=""):
    """MUTCD code (US:R1-1, R2-1 ...) or OSM traffic_sign value -> (kind, speed value or None)."""
    c = (code or "").upper().replace("US:", "").strip()
    t = f"{code or ''} {text or ''}"
    if c.startswith("R1-1") or c in ("STOP",):
        return "stop_sign", None
    if c.startswith("R1-2") or c in ("GIVE_WAY", "YIELD"):
        return "yield_sign", None
    if c.startswith("R2-1") or c.startswith("MAXSPEED"):
        m = re.search(r"\[(\d+)|(\d{2})\s*(?:MPH)?", t.upper())
        return "speed_limit_sign", (int(m.group(1) or m.group(2)) if m else None)
    if re.match(r"R\d", c):
        return "regulatory_sign", None
    return "other_sign", None


def heading_from(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    try:
        return round(float(s) % 360, 1)
    except ValueError:
        pass
    s = s.split(";")[0].split("-")[0].strip()
    return COMPASS.get(s)


def _get(url, params, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(3)


def fetch_city(spec, bounds):
    w, s, e, n = bounds
    ck = CACHE / f"{spec['id']}_{w:.5f}_{s:.5f}.json"
    if ck.exists():
        return json.loads(ck.read_text("utf-8"))
    recs = []
    if "soc" in spec:
        dom = spec.get("dom", "data.austintexas.gov")
        if "latlon" in spec:
            la, lo = spec["latlon"]
            where = f"{la}::number between {s} and {n} AND {lo}::number between {w} and {e}"
        else:
            where = f"within_box(location, {n}, {w}, {s}, {e})"
        if spec.get("where"):
            where += f" AND ({spec['where']})"
        rows = _get(f"https://{dom}/resource/{spec['soc']}.json", {"$where": where, "$limit": 5000})
        for r in rows:
            if "latlon" in spec:
                lat, lon = r.get(spec["latlon"][0]), r.get(spec["latlon"][1])
            else:
                loc = r.get("location") or {}
                lon, lat = (loc.get("coordinates") or [None, None]) if isinstance(loc, dict) else (None, None)
                if lon is None and r.get("longitude"):
                    lon, lat = r.get("longitude"), r.get("latitude")
            if lon is None or lat is None:
                continue
            recs.append({"lon": float(lon), "lat": float(lat), "attrs": r, "lonlat_text": [str(lon), str(lat)]})
    else:
        p = {"geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
             "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "where": spec.get("where", "1=1"),
             "returnGeometry": "true", "f": "json"}
        j = _get(spec["fs"] + "/query", p)
        if "error" in j:
            raise RuntimeError(j["error"])
        for f in j.get("features", []):
            g = f.get("geometry") or {}
            if "x" not in g:
                continue
            recs.append({"lon": g["x"], "lat": g["y"], "attrs": f.get("attributes", {})})
    ck.write_text(json.dumps(recs), "utf-8")
    return recs


def city_items(spec, recs):
    out = []
    for r in recs:
        a = {k.lower(): v for k, v in r["attrs"].items()}
        it = {"lon": r["lon"], "lat": r["lat"], "source": spec["name"], "source_key": spec["id"],
              "heading_deg": None, "value": None, "height_m": None, "location_basis": "recorded asset point"}
        rid = next((a[k] for k in ("sign_asset_id", "signal_id", "camera_id", "assetid", "facilityid", "objectid",
                                   "camera_num", "mslink", "id") if a.get(k) not in (None, "")), None)
        it["source_id"] = str(rid) if rid is not None else None
        k = spec["kind"]
        if k == "sign":
            code = a.get("mutcd_code") or a.get("code")
            text = a.get("sign_message") or a.get("printing") or ""
            k, val = sign_kind(code, text)
            it["value"] = val
            it["sign_code"] = code
            it["heading_deg"] = heading_from(a.get("direction_facing"))
            if a.get("direction_facing"):
                it["facing_raw"] = a.get("direction_facing")
            it["kind_class"] = "physical_asset"
        elif k == "planimetric":
            k = {"HYDRANT": "fire_hydrant", "POWER_POLE": "utility_pole", "STREET_LIGHT": "street_lamp",
                 "STREET_SIGN": "other_sign"}[a.get("symbol_name")]
            it["kind_class"] = "physical_asset"
            it["location_basis"] = "planimetric (photogrammetric) symbol point"
        elif k == "traffic_signal":
            if str(a.get("signal_status") or "TURNED_ON").upper() not in ("TURNED_ON", "ON", "ACTIVE"):
                continue
            it["kind_class"] = "control_point"
            it["location_basis"] = "intersection point of the signal record (not a pole position)"
            it["signal_type"] = a.get("signal_type") or a.get("type")
        elif k == "camera_public":
            it["kind_class"] = "physical_asset"
            it["location_basis"] = "camera record location (may be the intersection)"
        else:
            it["kind_class"] = "physical_asset"
        if r.get("lonlat_text"):
            it["coord_precision_dp"] = min(len(t.split(".")[1]) if "." in t else 0 for t in r["lonlat_text"])
        it["kind"] = k
        if a.get("asset_location_id"):
            it["location_id"] = a["asset_location_id"]
        if a.get("asset_category"):
            it["asset_category"] = a["asset_category"]
        out.append(it)
    # Austin: SIGN_POLE rows are the posts carrying the signs at the same asset_location_id -> attach, don't duplicate
    signs_at = {it["location_id"] for it in out if it.get("location_id") and it.get("asset_category") != "SIGN_POLE"}
    for it in out:
        if it.get("asset_category") == "SIGN_POLE":
            it["kind"] = "sign_post"
    posts = [it for it in out if it.get("asset_category") == "SIGN_POLE" and it.get("location_id") in signs_at]
    out = [it for it in out if it not in posts]
    for p_ in posts:
        for it in out:
            if it.get("location_id") == p_["location_id"]:
                it.setdefault("post_asset_ids", []).append(p_["source_id"])
                break
    return out


# ----------------------------------------------------------------------------------------------- OSM
NODE_KEYS = ("highway", "traffic_sign", "man_made", "emergency", "power", "surveillance", "utility")


def osm_classify(t):
    """OSM node tags -> (kind, extra) or None. Nothing outside these explicit tags is emitted."""
    hw = t.get("highway")
    if t.get("man_made") == "surveillance" or hw == "speed_camera":
        sv = t.get("surveillance", "")
        if sv in ("outdoor", "indoor"):
            return None  # private property cameras: never
        if hw == "speed_camera" or sv in ("public", "traffic") or t.get("surveillance:zone") == "traffic" or \
                (GOV.search(t.get("operator", "")) and sv == ""):
            return "camera_public", {}
        return None
    if hw == "street_lamp" or t.get("man_made") == "street_lamp":
        return "street_lamp", {}
    if hw == "stop":
        return "stop_sign", {}
    if hw == "give_way":
        return "yield_sign", {}
    if hw == "traffic_signals":
        return "traffic_signal", {}
    if hw == "crossing":
        return "crossing", {}
    if t.get("traffic_sign"):
        k, v = sign_kind(t["traffic_sign"].split(";")[0], t.get("maxspeed", "") if "R2-1" in t["traffic_sign"].upper()
                         or "MAXSPEED" in t["traffic_sign"].upper() else "")
        return k, {"value": v, "sign_code": t["traffic_sign"]}
    if t.get("emergency") == "fire_hydrant":
        return "fire_hydrant", {}
    if t.get("power") == "pole" or t.get("man_made") == "utility_pole":
        return "utility_pole", {}
    return None


def wait_extract(region, max_wait_s=2700):
    region = osm_extract.STATES.get(region, region)
    ok = osm_extract.CACHE / f"{region}-latest.osm.pbf.ok"
    t0 = time.time()
    while not ok.exists():
        if time.time() - t0 > max_wait_s:
            raise TimeoutError(f"OSM extract {region} not ready")
        log(f"waiting for {region} extract (osm_extract lane)...")
        time.sleep(60)
    return osm_extract.download(region)


def osm_for_state(state, boxes):
    """One pass over the state's extract for all of that state's blocks. boxes: {slug: (w,s,e,n)}."""
    import osmium
    pbf = wait_extract(state)
    key = CACHE / f"osm_{state}_{int(pbf.stat().st_mtime)}_{hashlib.sha1(json.dumps(boxes, sort_keys=True).encode()).hexdigest()[:10]}.json"
    if key.exists():
        return json.loads(key.read_text("utf-8"))
    t0 = time.time()
    found = {}
    bl = list(boxes.items())
    for o in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter(*NODE_KEYS)):
        if not o.location.valid():
            continue
        lo, la = o.location.lon, o.location.lat
        for slug, (w, s, e, n) in bl:
            if w <= lo <= e and s <= la <= n:
                t = dict(o.tags)
                c = osm_classify(t)
                if c:
                    found[o.id] = {"id": o.id, "slug": slug, "lon": lo, "lat": la, "tags": t, "kind": c[0], **c[1],
                                   "ways": []}
                break
    log(f"{state}: {len(found)} furniture nodes ({time.time() - t0:.0f}s); checking way membership")
    ids = set(found)
    if ids:
        for o in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway")):
            for nd in o.nodes:
                if nd.ref in ids:
                    found[nd.ref]["ways"].append({"id": o.id, "highway": o.tags.get("highway"),
                                                  "name": o.tags.get("name")})
    log(f"{state}: done in {time.time() - t0:.0f}s")
    out = list(found.values())
    key.write_text(json.dumps(out), "utf-8")
    return out


def osm_items(nodes):
    out = []
    for f in nodes:
        t = f["tags"]
        on_car = [w for w in f["ways"] if w["highway"] in CAR]
        k = f["kind"]
        if k in ("stop_sign", "yield_sign", "traffic_signal", "crossing") and t.get("highway") in (
                "stop", "give_way", "traffic_signals", "crossing"):
            kc = "control_point" if f["ways"] else "physical_asset"
        elif k in ("stop_sign", "yield_sign", "speed_limit_sign", "regulatory_sign", "other_sign", "street_lamp"):
            kc = "control_point" if on_car else "physical_asset"
        else:
            kc = "physical_asset"
        hd = heading_from(t.get("direction")) if t.get("direction") else None
        if k == "camera_public" and t.get("camera:direction"):
            hd = heading_from(t.get("camera:direction"))
        it = {"kind": k, "kind_class": kc, "lon": f["lon"], "lat": f["lat"], "source": "OpenStreetMap",
              "source_key": "osm", "source_id": f"node/{f['id']}", "coord_precision_dp": 7,
              "heading_deg": hd, "value": f.get("value"), "height_m": None,
              "location_basis": "OSM node" + (" on highway way " + ",".join(str(w["id"]) for w in f["ways"])
                                               if f["ways"] else "")}
        if t.get("direction") and hd is None:
            it["direction_tag"] = t["direction"]  # forward/backward: applies along the way; not a compass heading
        if f.get("sign_code"):
            it["sign_code"] = f["sign_code"]
        if k == "stop_sign" and t.get("stop"):
            it["stop"] = t["stop"]
        if on_car:
            it["road"] = on_car[0].get("name")
        if t.get("height"):
            try:
                it["height_m_tag"] = float(re.sub(r"[^\d.]", "", t["height"]))
            except ValueError:
                pass
        for kk in ("operator", "surveillance", "surveillance:type", "crossing", "ref", "lamp_type", "fire_hydrant:type"):
            if t.get(kk):
                it.setdefault("tags", {})[kk] = t[kk]
        out.append(it)
    return out


# ----------------------------------------------------------------------------------------------- LiDAR
def load_points(bdir, meta):
    dt = np.dtype([("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")])
    parts = [np.frombuffer(base64.b64decode((bdir / f"points_{i}.txt").read_text()), dtype=dt)
             for i in range(meta.get("point_parts", 1)) if (bdir / f"points_{i}.txt").exists()]
    q = np.concatenate(parts)
    return q["x"] * 0.02, q["y"] * 0.02, q["z"] * 0.01, q["k"], q["h"] / 8.0


class Ground:
    def __init__(self, x, y, z, k, half, res=1.0):
        from scipy import ndimage
        g = k == 0
        n = int(2 * half / res) + 1
        gi = np.clip(((x[g] + half) / res).astype(int), 0, n - 1)
        gj = np.clip(((y[g] + half) / res).astype(int), 0, n - 1)
        G = np.full(n * n, np.inf)
        np.minimum.at(G, gj * n + gi, z[g])
        G = G.reshape(n, n)
        bad = ~np.isfinite(G)
        if bad.all():
            G[:] = 0
        elif bad.any():
            idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
            G = G[tuple(idx)]
        self.G, self.half, self.res, self.n = G, half, res, n

    def __call__(self, x, y):
        i = int(np.clip((x + self.half) / self.res, 0, self.n - 1))
        j = int(np.clip((y + self.half) / self.res, 0, self.n - 1))
        return float(self.G[j, i])


def lidar_poles(x, y, k, h, half, min_trunk=2):
    """Thin vertical clusters: trunk slice (0.5-2.8 m above ground) with <=0.6 m horizontal extent, rising to >=3 m,
    isolated (no wall / hedge / house around), no tree canopy around. Returns [{x, y, height_m, n}]."""
    from scipy import ndimage
    from scipy.spatial import cKDTree
    nonground = (k != 0) & (h > 0.3) & (h < 20)
    P = np.column_stack([x[nonground], y[nonground], h[nonground]])
    Kp = k[nonground]
    tree = cKDTree(P[:, :2])
    house_xy = np.column_stack([x[k == 1], y[k == 1]])
    htree = cKDTree(house_xy) if len(house_xy) else None
    tr = (P[:, 2] >= 0.5) & (P[:, 2] <= 2.8) & (Kp != 1)
    T = P[tr]
    if len(T) < 3:
        return []
    res = 0.3
    n = int(2 * half / res) + 2
    gi = np.clip(((T[:, 0] + half) / res).astype(int), 0, n - 1)
    gj = np.clip(((T[:, 1] + half) / res).astype(int), 0, n - 1)
    occ = np.zeros((n, n), bool)
    occ[gj, gi] = True
    lab, nl = ndimage.label(occ, structure=np.ones((3, 3)))
    pl = lab[gj, gi]
    out = []
    order = np.argsort(pl)
    bounds = np.searchsorted(pl[order], np.arange(1, nl + 2))
    for c in range(nl):
        sel = order[bounds[c]:bounds[c + 1]]
        if len(sel) < min_trunk:
            continue
        S = T[sel]
        ext = max(np.ptp(S[:, 0]), np.ptp(S[:, 1]))
        if ext > 0.6:
            continue
        cx, cy = float(S[:, 0].mean()), float(S[:, 1].mean())
        if htree is not None and htree.query_ball_point([cx, cy], 0.6, return_length=True) > 0:
            continue
        col = P[tree.query_ball_point([cx, cy], 0.5)]
        top = col[:, 2].max() if len(col) else 0
        if top < 3.0:
            continue
        # vertical continuity: returns in >= 3 distinct 1 m height bins between 0.5 m and the top
        bins = np.unique(np.floor(col[(col[:, 2] >= 0.5)][:, 2]))
        if len(bins) < 3 or S[:, 2].min() > 2.0:
            continue
        ring = P[tree.query_ball_point([cx, cy], 2.0)]
        d = np.hypot(ring[:, 0] - cx, ring[:, 1] - cy)
        low_ring = ((d > 0.8) & (ring[:, 2] >= 0.5) & (ring[:, 2] <= 2.8)).sum()
        canopy = ((d > 0.6) & (ring[:, 2] > 3.0)).sum()
        if low_ring > 3 * len(S) + 6 or canopy > 2 * len(col) + 15:
            continue
        # height: highest return directly above the trunk or on a short arm (<=1.5 m) that is still thin
        arm = P[tree.query_ball_point([cx, cy], 1.5)]
        arm = arm[arm[:, 2] > 3.0]
        hgt = float(max(top, arm[:, 2].max() if len(arm) and len(arm) < 3 * len(col) + 10 else top))
        out.append({"x": round(cx, 2), "y": round(cy, 2), "height_m": round(hgt, 1), "n": int(len(col)),
                    "extent_m": round(float(ext), 2)})
    return out


# ----------------------------------------------------------------------------------------------- per block
def local_xy(meta, lon, lat):
    ox, oy = TO_M.transform(*meta["origin"])
    kk = 1 / math.cos(math.radians(meta["origin"][1]))
    mx, my = TO_M.transform(lon, lat)
    return (mx - ox) / kk, (my - oy) / kk


def merge(items, radius=3.0):
    """Same kind within `radius` from two sources = one physical thing. City inventory wins (authoritative), its exact
    coordinates are kept; the other record is listed in also_recorded_by with its own coordinates."""
    pri = lambda it: (0 if it["source_key"] != "osm" else 1)  # noqa: E731
    items = sorted(items, key=pri)
    kept = []
    for it in items:
        dup = None
        for k in kept:
            if k["kind"] == it["kind"] and k["source_key"] != it["source_key"] and \
                    math.hypot(k["x"] - it["x"], k["y"] - it["y"]) <= radius:
                dup = k
                break
        if dup:
            dup.setdefault("also_recorded_by", []).append(
                {"source": it["source"], "source_id": it.get("source_id"), "lon": it["lon"], "lat": it["lat"],
                 "x": it["x"], "y": it["y"], "kind_class": it["kind_class"]})
        else:
            kept.append(it)
    return kept


def build(slug, osm_nodes, use_city=True):
    bdir = BLOCKS / slug
    meta = json.loads((bdir / "meta.json").read_text("utf-8"))
    half = meta["half_m"]
    w, s, e, n = meta["bounds"]
    items = osm_items(osm_nodes)
    srcs, lics = [], [LIC["osm"], LIC["lidar"]]
    srcs.append({"key": "osm", **{k: LIC["osm"][k] for k in ("name", "licence", "url")}, "count": len(items)})
    if use_city:
        ck = next((c for c in CITY if slug.startswith(c)), None)
        for spec in CITY.get(ck, []):
            try:
                recs = fetch_city(spec, (w, s, e, n))
                ci = city_items(spec, recs)
                items += ci
                srcs.append({"key": spec["id"], "name": spec["name"], "url": spec["url"], "count": len(ci),
                             "licence": spec["lic"]["licence"]})
                lics.append({"name": spec["name"], "url": spec["url"], **spec["lic"]})
            except Exception as ex:  # noqa: BLE001
                log(f"{slug}: city source {spec['id']} failed: {ex}")
                srcs.append({"key": spec["id"], "name": spec["name"], "url": spec["url"], "error": str(ex)[:200]})
    for it in items:
        it["x"], it["y"] = (round(v, 2) for v in local_xy(meta, it["lon"], it["lat"]))
    items = [it for it in items if abs(it["x"]) <= half and abs(it["y"]) <= half]
    items = merge(items)
    # LiDAR
    x, y, z, k, h = load_points(bdir, meta)
    ground = Ground(x, y, z, k, half)
    poles = lidar_poles(x, y, k, h, half, min_trunk=2 if meta.get("points_per_m2", 0) >= 15 else 3)
    used = set()
    for it in items:
        it["z"] = round(ground(it["x"], it["y"]), 2)
        best, bd = None, MATCH_M
        for i, p in enumerate(poles):
            d = math.hypot(p["x"] - it["x"], p["y"] - it["y"])
            if d <= bd and i not in used:
                best, bd = i, d
        it["confirmed_by_lidar"] = best is not None
        if best is not None and it["kind"] not in ("fire_hydrant", "crossing"):
            used.add(best)
            p = poles[best]
            it["lidar_pole"] = {"x": p["x"], "y": p["y"], "offset_m": round(bd, 2), "height_m": p["height_m"]}
            it["height_m"] = p["height_m"]
        elif best is not None:
            it["confirmed_by_lidar"] = False  # a hydrant / crossing cannot be a >=3 m pole
    for i, p in enumerate(poles):
        if i in used:
            continue
        items.append({"kind": "unknown_pole", "kind_class": "physical_asset", "x": p["x"], "y": p["y"],
                      "z": round(ground(p["x"], p["y"]), 2), "height_m": p["height_m"], "heading_deg": None,
                      "value": None, "source": "LiDAR pole detection (USGS 3DEP points)", "source_key": "lidar",
                      "source_id": None, "confirmed_by_lidar": True, "location_basis": "measured LiDAR trunk centroid",
                      "lidar_points": p["n"], "lidar_extent_m": p["extent_m"]})
    for it in items:
        it.setdefault("height_m", None)
    # counts
    counts, by_class = {}, {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
        by_class.setdefault(it["kind_class"], {})
        by_class[it["kind_class"]][it["kind"]] = by_class[it["kind_class"]].get(it["kind"], 0) + 1
    att = [osm_extract.ATTRIBUTION, "USGS 3DEP lidar (public domain)"] + \
          [l.get("attribution") for l in lics if l.get("attribution")] + \
          [f"{l['name']} ({l['licence']})" for l in lics[2:] if not l.get("attribution")]
    out = {"items": items, "meta": {
        "frame": "local metres east (x) / north (y) of meta.origin (Web-Mercator metres / k); z metres above meta.z_base "
                 "= LiDAR ground under the item; lon/lat are the source's exact coordinates",
        "rules": ["items only where a data record (OSM tag / licence-green city inventory) says so; nothing inferred",
                  "positions = exact source coordinates, never moved",
                  "heading_deg only from data (OSM direction / camera:direction, city facing field), else null; "
                  "heading = compass direction the face points",
                  "kind_class control_point = OSM stop/give_way/traffic_signals/crossing node on the road way (the "
                  "controlled point, not the pole), city signal records at the intersection",
                  "speed_limit_sign only from an actual sign record; OSM way maxspeed is never used",
                  f"LiDAR poles = measured position, type unknown -> unknown_pole; a data item with a pole within "
                  f"{MATCH_M} m is confirmed_by_lidar and takes the pole's measured height",
                  "public cameras only (government / traffic); OSM surveillance=outdoor|indoor excluded; no feeds"],
        "sources": srcs, "licences": lics, "attribution": "; ".join(dict.fromkeys(a for a in att if a)),
        "licence": "ODbL 1.0 (Derivative Database of OpenStreetMap; share-alike)",
        "counts": counts, "counts_by_kind_class": by_class, "lidar_poles_detected": len(poles),
        "lidar_poles_matched": len(used), "match_radius_m": MATCH_M,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    tmp = bdir / "furniture.json.tmp"
    tmp.write_text(json.dumps(out, separators=(",", ":")), "utf-8")
    os.replace(tmp, bdir / "furniture.json")
    with block_lock(bdir):
        m = json.loads((bdir / "meta.json").read_text("utf-8"))
        if "furniture.json" not in m.setdefault("files", []):
            m["files"].append("furniture.json")
        m["furniture"] = {"file": "furniture.json", "counts": counts, "counts_by_kind_class": by_class,
                          "lidar_poles_detected": len(poles), "lidar_poles_matched": len(used),
                          "confirmed_by_lidar": sum(1 for it in items if it.get("confirmed_by_lidar")),
                          "sources": [s_["key"] for s_ in srcs if not s_.get("error")],
                          "licence": "ODbL 1.0 (OSM derivative); city sources per furniture.json meta.licences",
                          "generated": out["meta"]["generated"]}
        t2 = bdir / "meta.json.furn.tmp"
        t2.write_text(json.dumps(m, indent=1, ensure_ascii=False), "utf-8")
        os.replace(t2, bdir / "meta.json")
    log(f"{slug}: {len(items)} items {counts} poles={len(poles)} matched={len(used)}")
    return out


# ----------------------------------------------------------------------------------------------- render
def render_png(slug):
    from PIL import Image, ImageDraw
    bdir = BLOCKS / slug
    meta = json.loads((bdir / "meta.json").read_text("utf-8"))
    F = json.loads((bdir / "furniture.json").read_text("utf-8"))
    half = meta["half_m"]
    S = 1000
    sc = S / (2 * half + 20)
    P = lambda x, y: ((x + half + 10) * sc, (half + 10 - y) * sc)  # noqa: E731
    im = Image.new("RGB", (S, S), (245, 245, 240))
    try:
        a = Image.open(bdir / "aerial.jpg").convert("RGB").resize((int(2 * half * sc), int(2 * half * sc)))
        a = Image.blend(a, Image.new("RGB", a.size, (255, 255, 255)), 0.45)
        im.paste(a, (int(10 * sc), int(10 * sc)))
    except Exception:  # noqa: BLE001
        pass
    d = ImageDraw.Draw(im, "RGBA")
    st = bdir / "streets.json"
    if st.exists():
        sj = json.loads(st.read_text("utf-8"))
        for r in sj.get("roads", []):
            d.polygon([P(p[0], p[1]) for p in r["poly"]], fill=(90, 90, 100, 150), outline=(40, 40, 40, 200))
        for c in sj.get("curbs", []):
            d.line([P(p[0], p[1]) for p in c["path"]], fill=(255, 255, 255, 220), width=2)
    d.rectangle([P(-half, half), P(half, -half)], outline=(0, 0, 0), width=2)
    col = {"street_lamp": (255, 200, 0), "stop_sign": (220, 0, 0), "yield_sign": (255, 120, 0),
           "speed_limit_sign": (255, 255, 255), "regulatory_sign": (200, 200, 255), "other_sign": (0, 160, 220),
           "traffic_signal": (0, 200, 0), "camera_public": (180, 0, 200), "fire_hydrant": (255, 0, 120),
           "utility_pole": (120, 70, 20), "unknown_pole": (0, 0, 0), "crossing": (255, 255, 255)}
    for it in F["items"]:
        x, y = P(it["x"], it["y"])
        r = 6 if it["kind_class"] == "physical_asset" else 5
        c = col.get(it["kind"], (0, 0, 0))
        if it["kind_class"] == "control_point":
            d.rectangle([x - r, y - r, x + r, y + r], fill=c + (255,), outline=(0, 0, 0))
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=c + (255,), outline=(0, 0, 0))
        if it.get("confirmed_by_lidar"):
            d.ellipse([x - r - 4, y - r - 4, x + r + 4, y + r + 4], outline=(0, 120, 255), width=2)
        if it.get("lidar_pole"):
            lx, ly = P(it["lidar_pole"]["x"], it["lidar_pole"]["y"])
            d.line([x, y, lx, ly], fill=(0, 120, 255), width=2)
        if it.get("heading_deg") is not None:
            a_ = math.radians(it["heading_deg"])
            d.line([x, y, x + 18 * math.sin(a_), y - 18 * math.cos(a_)], fill=(0, 0, 0), width=2)
    yy = S - 20 - 16 * sum(1 for kk in col if F['meta']['counts'].get(kk))
    for kk, c in col.items():
        n = F["meta"]["counts"].get(kk, 0)
        if n:
            d.rectangle([8, yy, 20, yy + 12], fill=c, outline=(0, 0, 0))
            d.text((26, yy), f"{kk} {n}", fill=(0, 0, 0))
            yy += 16
    d.text((8, S - 16), "circle=physical asset, square=control point, blue ring=LiDAR pole within 3 m", fill=(0, 0, 0))
    out = ROOT / "usa" / "_cache" / "furniture" / f"{slug}_top.png"
    im.save(out)
    log("wrote", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--no-city", action="store_true")
    ap.add_argument("--png", default=None)
    a = ap.parse_args()
    slugs = a.slugs or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists())
    metas = {s: json.loads((BLOCKS / s / "meta.json").read_text("utf-8")) for s in slugs}
    by_state = {}
    for s, m in metas.items():
        by_state.setdefault(m["state"], {})[s] = tuple(m["bounds"])
    summary = {}
    for st in sorted(by_state, key=lambda st_: (osm_extract.CACHE / f"{osm_extract.STATES[st_]}-latest.osm.pbf.ok").exists(),
                     reverse=True):
        nodes = osm_for_state(st, by_state[st])
        for s in by_state[st]:
            out = build(s, [n for n in nodes if n["slug"] == s], use_city=not a.no_city)
            summary[s] = out["meta"]["counts_by_kind_class"]
    print(json.dumps(summary, indent=1))
    if a.png:
        render_png(a.png)


if __name__ == "__main__":
    main()

"""Build web/app/data/emit.json (+ web/usa/blocks/<slug>/emit.json): NASA EMIT imaging-spectrometer
coverage, methane / CO2 plume detections, and AVIRIS airborne hyperspectral flight lines near every
built block (web/usa/blocks/index.json).

Sources (all no login):
  NASA CMR granule search  https://cmr.earthdata.nasa.gov/search/granules.umm_json  (public metadata)
    scenes  : EMITL2ARFL (reflectance), EMITL2BMIN (mineralogy), EMITL2BCH4ENH (CH4 enhancement)
              granules whose footprint contains the block point -> date, cloud %, granule ids,
              public browse PNG (lp-prod-public).
    plumes  : EMITL2BCH4PLM.002 / EMITL2BCO2PLM.002 granules inside +-pad deg -> plume outline polygon
              exactly as the product's CMR footprint, observation time, plume id, public quicklook PNG.
    AVIRIS  : ORNL DAAC facility collections (AVIRIS-NG L2 RFL, AVIRIS-3 L2A RFL / L2B GHG,
              AVIRIS-5 L2A RFL, AVIRIS-Classic L2 RFL) granules whose footprint contains the point.
  EMIT Open Data Portal plume table (JPL / EMIT SDS, the mission's own public copy of the V002 plume
    product metadata): https://earth.jpl.nasa.gov/emit-mmgis-lb/Missions/EMIT/Layers/coverage/
    combined_plume_metadata.json -> max plume concentration (ppm m) and its location, and the emission
    rate estimate (kg/h) +- uncertainty *only where the product gives one* ("NA" -> null).
    Joined by plume id + time. Disable with --no-portal.
Pixel data (reflectance cubes, mineral maps, plume rasters, CH4PLMMETA json) sit in lp-prod-protected
and need an Earthdata Login; this script never logs in, so per-pixel values are not extracted.

Licence: Earthdata data use guidance: "Unless the content is marked with a use restriction or
license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."
EMIT collections carry no UseConstraints in CMR; AVIRIS collections point to the NASA Earth science
data information policy. Cache: usa/cache/emit/ (re-runs reuse it).

Usage: .venv\\Scripts\\python usa\\build_emit.py [--pad 0.3] [--no-portal] [--refresh]
"""
import argparse
import datetime
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "cache", "emit")
BLOCKS = os.path.join(ROOT, "web", "usa", "blocks", "index.json")
BLOCKDIR = os.path.join(ROOT, "web", "usa", "blocks")
OUT = os.path.join(ROOT, "web", "app", "data", "emit.json")
CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
PORTAL = "https://earth.jpl.nasa.gov/emit-mmgis-lb/Missions/EMIT/Layers/coverage/combined_plume_metadata.json"
UA = {"User-Agent": "roof-intel-builder/1.0"}

SCENE_COLS = {  # key -> CMR collection concept id
    "rfl1": "C2408750690-LPCLOUD", "rfl2": "C4079844428-LPCLOUD",
    "min1": "C2408034484-LPCLOUD", "min2": "C4079846859-LPCLOUD",
    "ch4enh2": "C3242680113-LPCLOUD", "ch4enh3": "C4303752968-LPCLOUD",
}
PLUME_COLS = {"CH4": "C3242707413-LPCLOUD", "CO2": "C3244277342-LPCLOUD"}
AVIRIS_COLS = {
    "AVIRIS-NG L2 RFL": "C2659129205-ORNL_CLOUD",
    "AVIRIS-3 L2A RFL": "C3369603199-ORNL_CLOUD",
    "AVIRIS-3 L2B GHG": "C3236537512-ORNL_CLOUD",
    "AVIRIS-5 L2A RFL": "C4184090548-ORNL_CLOUD",
    "AVIRIS-Classic L2 RFL": "C2711871294-ORNL_CLOUD",
}


def get(url, timeout=120, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def cached_json(name, url, refresh=False):
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    j = json.loads(get(url).decode("utf-8"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, separators=(",", ":"))
    os.replace(tmp, path)
    return j


def cmr_all(name, params, refresh=False):
    """All granules for a CMR query (paged by page_num, cached per page)."""
    items, page = [], 1
    while True:
        q = dict(params, page_size=2000, page_num=page)
        j = cached_json(f"{name}_p{page}.json", CMR + "?" + urllib.parse.urlencode(q), refresh)
        items += j.get("items", [])
        if len(items) >= j.get("hits", 0) or not j.get("items"):
            return items
        page += 1


def umm_time(u):
    t = u.get("TemporalExtent", {})
    return t.get("SingleDateTime") or t.get("RangeDateTime", {}).get("BeginningDateTime")


def public_png(u):
    for r in u.get("RelatedUrls", []):
        url = r.get("URL", "")
        if url.startswith("https://") and "lp-prod-public" in url and url.lower().endswith((".png", ".jpg", ".jpeg")):
            return url
    for r in u.get("RelatedUrls", []):  # ORNL browse images
        url = r.get("URL", "")
        if url.startswith("https://") and r.get("Type") == "GET RELATED VISUALIZATION":
            return url
    return None


def attr(u, name):
    for a in u.get("AdditionalAttributes", []):
        if a.get("Name") == name:
            return (a.get("Values") or [None])[0]
    return None


def polygons(u):
    g = u.get("SpatialExtent", {}).get("HorizontalSpatialDomain", {}).get("Geometry", {})
    out = []
    for p in g.get("GPolygons", []):
        out.append([[round(pt["Longitude"], 5), round(pt["Latitude"], 5)] for pt in p["Boundary"]["Points"]])
    for r in g.get("BoundingRectangles", []):
        w, e, s, n = (round(r[k], 5) for k in ("WestBoundingCoordinate", "EastBoundingCoordinate",
                                                 "SouthBoundingCoordinate", "NorthBoundingCoordinate"))
        out.append([[w, s], [e, s], [e, n], [w, n], [w, s]])
    return out


def km(lon1, lat1, lon2, lat2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(a))


def nearest_vertex_km(b, polys):
    return min(km(b["lon"], b["lat"], x, y) for poly in polys for x, y in poly)


def scene_key(t):
    return re.sub(r"[-:]", "", t)[:15]  # 20240128T191151


def portal_table(refresh):
    j = cached_json("combined_plume_metadata.json", PORTAL, refresh)
    tab = {}
    for f in j.get("features", []):
        p = f.get("properties", {})
        if f.get("geometry", {}).get("type") != "Point" or not p.get("Plume ID"):
            continue
        m = re.search(r"(\d+)$", p["Plume ID"])
        if not m:
            continue

        def num(k):
            v = p.get(k)
            return v if isinstance(v, (int, float)) else None
        tab[(int(m.group(1)), (p.get("UTC Time Observed") or "")[:19])] = {
            "maxPpmM": num("Max Plume Concentration (ppm m)"),
            "maxAt": [num("Longitude of max concentration"), num("Latitude of max concentration")],
            "emissionKgH": num("Emissions Rate Estimate (kg/hr)"),
            "emissionUncKgH": num("Emissions Rate Estimate Uncertainty (kg/hr)"),
            "windMs": num("Wind Speed (m/s)"),
            "windSource": p.get("Wind Speed Source") if p.get("Wind Speed Source") not in (None, "NA") else None,
        }
    return tab


def do_block(b, pad, portal, refresh):
    slug = b["slug"]
    pt = f"{b['lon']},{b['lat']}"
    # --- EMIT scenes over the point
    scenes = {}
    for key, cid in SCENE_COLS.items():
        for it in cmr_all(f"{slug}_{key}", {"collection_concept_id": cid, "point": pt}, refresh):
            u = it["umm"]
            t = umm_time(u)
            s = scenes.setdefault(scene_key(t), {"t": t[:19] + "Z", "cloud": u.get("CloudCover"), "ids": {}, "browse": None})
            s["ids"][key] = u["GranuleUR"]
            if s["cloud"] is None:
                s["cloud"] = u.get("CloudCover")
            if not s["browse"] and key.startswith("rfl"):
                s["browse"] = public_png(u)
    scenes = [scenes[k] for k in sorted(scenes)]
    # --- plumes inside +-pad
    bb = f"{b['lon'] - pad:.4f},{b['lat'] - pad:.4f},{b['lon'] + pad:.4f},{b['lat'] + pad:.4f}"
    plumes = []
    for gas, cid in PLUME_COLS.items():
        for it in cmr_all(f"{slug}_plm{gas}_{pad}", {"collection_concept_id": cid, "bounding_box": bb}, refresh):
            u = it["umm"]
            t = umm_time(u)
            polys = polygons(u)
            pid = attr(u, "PLUME_ID")
            rec = {"gas": gas, "id": u["GranuleUR"], "plumeId": int(pid) if pid and pid.isdigit() else pid,
                   "t": t[:19] + "Z", "poly": polys, "distKm": round(nearest_vertex_km(b, polys), 2) if polys else None,
                   "png": public_png(u), "srcScenes": attr(u, "SOURCE_SCENES")}
            if portal is not None and gas == "CH4" and isinstance(rec["plumeId"], int):
                extra = portal.get((rec["plumeId"], t[:19]))
                if extra:
                    rec.update(extra)
            plumes.append(rec)
    plumes.sort(key=lambda r: (r["distKm"] if r["distKm"] is not None else 1e9, r["t"]))
    # --- AVIRIS flight lines over the point
    aviris = []
    for name, cid in AVIRIS_COLS.items():
        for it in cmr_all(f"{slug}_av_{cid}", {"collection_concept_id": cid, "point": pt}, refresh):
            u = it["umm"]
            aviris.append({"product": name, "id": u["GranuleUR"], "t": (umm_time(u) or "")[:19] + "Z",
                           "browse": public_png(u)})
    aviris.sort(key=lambda r: r["t"])
    return slug, {"lon": b["lon"], "lat": b["lat"], "state": b.get("state"),
                  "scenes": scenes, "plumes": plumes, "aviris": aviris}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad", type=float, default=0.3, help="plume search half-width, degrees")
    ap.add_argument("--no-portal", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    with open(BLOCKS, encoding="utf-8") as f:
        blocks = [b for b in json.load(f) if b["slug"] != "slc-ut-test"]
    portal = None if a.no_portal else portal_table(a.refresh)
    with ThreadPoolExecutor(a.workers) as ex:
        res = dict(ex.map(lambda b: do_block(b, a.pad, portal, a.refresh), blocks))
    out = {
        "source": "NASA EMIT (ISS imaging spectrometer, 60 m) granule metadata via NASA CMR; plume concentration/"
                  "emission figures from the EMIT Open Data Portal plume table; AVIRIS facility flight lines via CMR "
                  "(ORNL DAAC)",
        "fields": {
            "scenes": ["t", "cloud %", "ids{rfl1,rfl2,min1,min2,ch4enh2,ch4enh3}", "browse png"],
            "plumes": ["gas", "id", "plumeId", "t", "poly[[lon,lat]...] (product outline)", "distKm (block to nearest outline vertex)",
                       "png", "srcScenes", "maxPpmM|null", "maxAt[lon,lat]", "emissionKgH|null", "emissionUncKgH|null",
                       "windMs|null", "windSource|null"],
            "aviris": ["product", "id", "t", "browse"],
        },
        "licence": "CC0 (NASA-led mission data; no restrictions on use, citation requested)",
        "licenceUrl": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
        "attribution": "EMIT: NASA JPL / LP DAAC (Green et al., doi:10.5067/EMIT/EMITL2BCH4PLM.002, "
                       "10.5067/EMIT/EMITL2BCO2PLM.002, 10.5067/EMIT/EMITL2ARFL.001, 10.5067/EMIT/EMITL2BMIN.001); "
                       "AVIRIS: NASA JPL / ORNL DAAC",
        "plumePadDeg": a.pad,
        "portalJoined": portal is not None,
        "built": datetime.date.today().isoformat(),
        "blocks": res,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    for slug, r in res.items():
        d = os.path.join(BLOCKDIR, slug)
        if os.path.isdir(d):
            with open(os.path.join(d, "emit.json"), "w", encoding="utf-8") as f:
                json.dump({k: out[k] for k in ("source", "licence", "licenceUrl", "attribution", "built")} | r, f,
                          separators=(",", ":"))
    for slug, r in sorted(res.items()):
        em = sum(1 for p in r["plumes"] if p.get("emissionKgH") is not None)
        print(f"{slug:28s} scenes={len(r['scenes']):3d} plumes={len(r['plumes']):3d} (withRate={em}) aviris={len(r['aviris'])}")
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()

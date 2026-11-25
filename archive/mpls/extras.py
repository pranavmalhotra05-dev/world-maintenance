"""Every researched source that is free, keyless and legal for commercial use, added to the block.

  python extras.py   -> extras.json (map layers + context) and new per-house fields in houses.json

Sources (licence):
  OpenStreetMap, local Geofabrik extract (ODbL; usa/osm_extract.py)  signals, stop/give-way, crossings, lamps, hydrants, power, parking, benches, cameras, trees
  NAIP 2023 via Microsoft Planetary Computer   near-infrared lawn health per yard (public domain)
  Meta / WRI 1 m canopy height (CC-BY 4.0)     tree height per lot
  NOAA GOES-19 GeoColor via NASA GIBS WMS      latest Upper-Midwest satellite image (public domain, every 10 min)
  NASA FIRMS VIIRS 24 h (open)                 active fires within 300 km
  FHFA HPI (public domain)                     annual house-price index for ZIP 55407
  CFPB HMDA (public)                           mortgage originations in the census tract
  EPA Smart Location Database (public domain)  walkability, transit distance for the block group
  Census LODES (public domain)                 jobs located in the tract
  NREL ResStock (CC-BY 4.0)                    typical yearly energy for MN single-family homes by age/size -> per-house estimate
  BTS National Transportation Noise (public domain, bts.gov download only) road/rail/air noise at the block
"""
import base64
import csv
import gzip
import io
import sys
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from shapely import contains_xy
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json")); LON, LAT = meta["origin"]; HALF = meta["half_m"]; W, So, E, N = meta["bounds"]
houses = json.load(open(OUT / "houses.json"))
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
loc = lambda lon, lat: (round((lon - LON) * MX, 2), round((lat - LAT) * MY, 2))
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1 (open-data research)"
TRACT = "27053110200"; ZIP = "55407"
ex, status = {}, {}
NOW = datetime.now(timezone.utc).isoformat(timespec="minutes")


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


def safe(name, licence, updates):
    def wrap(fn):
        try:
            ex[name] = {"data": fn(), "licence": licence, "updates": updates, "fetched": NOW}; status[name] = "ok"; print("ok  ", name)
        except Exception as e:
            status[name] = f"{type(e).__name__}: {e}"[:220]; print("FAIL", name, status[name])
        return fn
    return wrap


sys.path.insert(0, str(ROOT / "usa"))
import osm_extract  # noqa: E402


@safe("osm", "ODbL 1.0 - " + osm_extract.ATTRIBUTION + " (share-alike; keep separate)", "daily (Geofabrik extract)")
def osm():
    # same selection as the former Overpass query, now read from the local Geofabrik Minnesota extract
    hw = ("traffic_signals", "stop", "give_way", "crossing", "street_lamp", "bus_stop")
    am = ("bench", "waste_basket", "bicycle_parking", "parking", "post_box", "drinking_water")
    d = osm_extract.query("MN", nodes=[("highway", v) for v in hw] + [("emergency", "fire_hydrant"), ("man_made", "surveillance"), ("power", None)]
                          + [("amenity", v) for v in am] + [("natural", "tree")],
                          ways=[("power", "line"), ("power", "minor_line"), ("power", "cable"), ("amenity", "parking")], bbox=(W, So, E, N))
    pts, lines = [], []
    for e in d:
        t = e.get("tags", {})
        kind = (t.get("highway") if t.get("highway") in hw else None) or ("fire_hydrant" if t.get("emergency") == "fire_hydrant" else None) \
            or ("camera" if t.get("man_made") == "surveillance" else None) \
            or (("power_" + t["power"]) if t.get("power") else None) or (t.get("amenity") if t.get("amenity") in am else None) \
            or ("tree" if t.get("natural") == "tree" else "other")
        if e["type"] == "n":
            x, y = loc(e["lon"], e["lat"]); pts.append({"x": x, "y": y, "kind": kind, "tags": {k: v for k, v in t.items() if k in ("name", "crossing", "species", "ref", "voltage", "operator", "surveillance:type", "fire_hydrant:type")}})
        elif e.get("coords"):
            lines.append({"kind": kind, "path": [list(loc(g[0], g[1])) for g in e["coords"]], "tags": {k: v for k, v in t.items() if k in ("voltage", "operator", "name", "capacity")}})
    counts = {}
    for p_ in pts + lines:
        counts[p_["kind"]] = counts.get(p_["kind"], 0) + 1
    return {"points": pts, "lines": lines, "counts": counts}


@safe("naip_lawn", "public domain (USDA NAIP)", "every 2-3 years")
def naip():
    import planetary_computer, rasterio
    from pystac_client import Client
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    it = next(Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
              .search(collections=["naip"], bbox=[W, So, E, N], sortby=[{"field": "properties.datetime", "direction": "desc"}], max_items=1).items())
    n = 400
    with rasterio.open(it.assets["image"].href) as src:
        bb = transform_bounds("EPSG:4326", src.crs, W, So, E, N)
        a = src.read(window=from_bounds(*bb, src.transform), out_shape=(src.count, n, n)).astype(float)
    ndvi = np.flipud((a[3] - a[0]) / np.maximum(a[3] + a[0], 1))  # row 0 = south
    gx = -HALF + (np.arange(n) + 0.5) * (2 * HALF / n); GX, GY = np.meshgrid(gx, gx)
    for h in houses:
        y = h.get("yard")
        if not y:
            continue
        ring = np.array(h["ring"]); c = ring[:-1].mean(0)
        lot = Polygon(ring).buffer(12)  # the house plus its yard
        m = contains_xy(lot, GX, GY) & ~contains_xy(Polygon(ring), GX, GY)
        v = ndvi[m]
        if len(v):
            y["naip_ndvi"] = round(float(np.median(v)), 3)
            y["naip_green_pct"] = round(float((v > 0.3).mean()) * 100, 1)
            y["lawn_health"] = "lush" if np.median(v) > 0.45 else "healthy" if np.median(v) > 0.3 else "patchy / dry" if np.median(v) > 0.15 else "little vegetation"
    return {"date": it.properties["datetime"][:10], "gsd_m": it.properties.get("gsd"), "block_ndvi": round(float(np.nanmean(ndvi)), 3)}


@safe("canopy_height", "CC-BY 4.0 (Meta & World Resources Institute)", "one-off (2018-2020 imagery)")
def canopy():
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    url = "/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/021333011.tif"
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(url) as src:
        bb = transform_bounds("EPSG:4326", src.crs, W, So, E, N)
        n = 200
        a = np.flipud(src.read(1, window=from_bounds(*bb, src.transform), out_shape=(n, n)).astype(float))
    gx = -HALF + (np.arange(n) + 0.5) * (2 * HALF / n); GX, GY = np.meshgrid(gx, gx)
    for h in houses:
        ring = np.array(h["ring"])
        m = contains_xy(Polygon(ring).buffer(15), GX, GY) & ~contains_xy(Polygon(ring), GX, GY)
        v = a[m]
        if len(v):
            h["meta_canopy"] = {"mean_m": round(float(v.mean()), 1), "max_m": round(float(v.max()), 1), "cover_pct": round(float((v > 3).mean()) * 100)}
    return {"block_mean_m": round(float(a.mean()), 1), "block_cover_pct": round(float((a > 3).mean()) * 100)}


@safe("goes", "public domain (NOAA GOES-19 via NASA GIBS)", "every 10 min")
def goes():
    # NOAA's STAR CDN does not resolve here; NASA GIBS serves the same GOES-East (GOES-19) ABI GeoColor product keylessly.
    from datetime import timedelta
    from PIL import Image
    wms = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"; layer = "GOES-East_ABI_GeoColor"
    cap = S.get(wms, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"}, timeout=180).text
    blk = cap[cap.index(f"<Name>{layer}</Name>"):]
    dim = re.search(r'<Dimension name="time"[^>]*>([^<]+)</Dimension>', blk).group(1)
    last = datetime.strptime(dim.strip().split(",")[-1].split("/")[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    p = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": layer, "CRS": "EPSG:4326",
         "BBOX": "41,-98,49,-88", "WIDTH": 1250, "HEIGHT": 1000, "FORMAT": "image/jpeg"}
    for k in range(18):  # newest slots can still be empty (black) while GIBS ingests; step back 10 min at a time
        t = (last - timedelta(minutes=10 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = S.get(wms, params={**p, "TIME": t}, timeout=120)
        if r.ok and r.headers.get("content-type", "").startswith("image") and np.asarray(Image.open(io.BytesIO(r.content))).mean() > 3:
            (OUT / "goes.jpg").write_bytes(r.content)
            return {"source": f"NASA GIBS WMS {layer} (NOAA GOES-19 ABI)", "time": t, "file": "goes.jpg", "bbox": [-98, 41, -88, 49], "bytes": len(r.content)}
    raise ValueError("no non-empty GOES GeoColor frame in the last 3 h")


@safe("fires", "open (NASA FIRMS)", "~3 hours")
def fires():
    t = S.get("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_USA_contiguous_and_Hawaii_24h.csv", timeout=120).text
    out = []
    for r in csv.DictReader(io.StringIO(t)):
        d = math.hypot((float(r["longitude"]) - LON) * MX, (float(r["latitude"]) - LAT) * MY) / 1000
        if d <= 300:
            out.append({"km": round(d), "date": r["acq_date"], "time": r["acq_time"], "frp": r.get("frp"), "confidence": r.get("confidence")})
    return sorted(out, key=lambda f: f["km"])


@safe("fhfa_hpi", "public domain (FHFA)", "yearly")
def fhfa():
    import pandas as pd
    r = S.get("https://www.fhfa.gov/hpi/download/annual/hpi_at_zip5.xlsx", timeout=300); r.raise_for_status()
    raw = pd.read_excel(io.BytesIO(r.content), header=None, dtype=str)  # title + notes rows sit above the header
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
            pass  # "." = missing
    ser = [s_ for s_ in sorted(ser) if not math.isnan(s_[1])]
    if not ser:
        raise ValueError(f"ZIP {ZIP} not in FHFA file")
    by = dict(ser); y0 = ser[-1][0] - 10
    return {"zip": ZIP, "series": ser[-30:], "change_10y_pct": round((ser[-1][1] / by[y0] - 1) * 100, 1) if y0 in by else None,
            "base": hc, "latest_year": ser[-1][0]}


@safe("hmda", "public (CFPB HMDA)", "yearly")
def hmda():
    out = {}
    for y in (2025, 2024, 2023):
        r = S.get("https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations", params={"years": y, "census_tracts": TRACT, "actions_taken": 1}, timeout=60)
        if r.ok:
            ag = r.json().get("aggregations", [])
            if ag:
                out[y] = {"loans": ag[0].get("count"), "total_usd": ag[0].get("sum")}
    return {"tract": TRACT, "originations": out}


@safe("walkability", "public domain (EPA)", "static (2021)")
def sld():
    d = S.get("https://geodata.epa.gov/arcgis/rest/services/OA/SmartLocationDatabase/MapServer/14/query",
              params={"geometry": f"{LON},{LAT}", "geometryType": "esriGeometryPoint", "inSR": 4326, "outFields": "*", "returnGeometry": "false", "f": "json"}, timeout=60).json()
    a = d["features"][0]["attributes"]
    pick = {k: a.get(k) for k in ("GEOID20", "NatWalkInd", "D3B", "D4A", "D2A_EPHHM", "Pct_AO0", "AutoOwn0", "TotPop", "CountHU", "HH")}
    return pick


@safe("jobs_lodes", "public domain (Census LEHD)", "yearly")
def lodes():
    for yr in (2023, 2022, 2021):
        r = S.get(f"https://lehd.ces.census.gov/data/lodes/LODES8/mn/wac/mn_wac_S000_JT00_{yr}.csv.gz", timeout=180)
        if r.ok:
            rows = [x for x in csv.DictReader(io.StringIO(gzip.decompress(r.content).decode())) if x["w_geocode"].startswith(TRACT)]
            jobs = sum(int(x["C000"]) for x in rows)
            return {"year": yr, "tract": TRACT, "jobs_in_tract": jobs, "blocks_with_jobs": len(rows)}
    raise ValueError("no LODES file")


@safe("energy_resstock", "CC-BY 4.0 (NREL ResStock 2024.2)", "yearly releases")
def resstock():
    import pandas as pd
    url = ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2024/"
           "resstock_amy2018_release_2/metadata_and_annual_results/by_state/state=MN/csv/MN_baseline_metadata_and_annual_results.csv")
    df = pd.read_csv(io.BytesIO(S.get(url, timeout=600).content), low_memory=False)
    sf = df[df["in.geometry_building_type_recs"].astype(str).str.contains("Single-Family Detached")]
    elec = [c for c in sf.columns if c.startswith("out.electricity.total.energy_consumption")][0]
    gas = [c for c in sf.columns if c.startswith("out.natural_gas.total.energy_consumption")][0]
    vint = "in.vintage"
    bins = {}
    for v, g in sf.groupby(vint):
        bins[str(v)] = {"elec_kwh": round(float(g[elec].median())), "gas_kwh": round(float(g[gas].median())), "n": int(len(g))}
    def vint_of(y):
        y = int(y)
        return "<1940" if y < 1940 else f"{y // 10 * 10}s"
    for h in houses:
        if h.get("garage"):
            continue
        yb = (h.get("assessor") or {}).get("yearbuilt") or h.get("year_built")
        try:
            b = bins.get(vint_of(yb))
        except Exception:
            b = None
        if b:
            h["energy_est"] = {"vintage": vint_of(yb), "elec_kwh_yr": b["elec_kwh"], "gas_therms_yr": round(b["gas_kwh"] / 29.3),
                               "basis": f"median of {b['n']} modelled MN single-family homes of that age"}
    return {"vintage_medians": bins}


@safe("noise", "public domain (US DOT BTS National Transportation Noise Map, federal work)", "static (2022/2023)")
def noise():
    # Read from BTS's own download (not the Esri Living Atlas copy, which is under the Esri MLA). bts.gov refuses scripted
    # downloads (403), so the zip must be fetched once by hand from https://www.bts.gov/geospatial/national-transportation-noise-map
    # ("Continental U.S. road, rail, and aviation noise 2022/2023") into usa/_cache/bts_noise/.
    import zipfile
    import rasterio
    from rasterio.warp import transform
    z = ROOT / "usa" / "_cache" / "bts_noise" / "CONUS_rail_road_and_aviation_noise_2022.zip"
    if not z.exists():
        raise FileNotFoundError(f"{z.name} not downloaded (bts.gov blocks scripted downloads; fetch it manually)")
    tif = next(n for n in zipfile.ZipFile(z).namelist() if n.lower().endswith((".tif", ".tiff")))
    with rasterio.open(f"/vsizip/{z.as_posix()}/{tif}") as src:
        xs, ys = transform("EPSG:4326", src.crs, [LON], [LAT])
        r, c = src.index(xs[0], ys[0])
        win = src.read(1, window=((r - 3, r + 4), (c - 3, c + 4)), boundless=True, fill_value=0).astype(float)
        res = abs(src.transform.a)
    v = win[3, 3]
    if not v or v <= 0:
        raise ValueError("no data at origin pixel")
    return {"db_laeq_24h": round(float(v), 1), "max_within_100m_db": round(float(np.nanmax(win)), 1),
            "method": f"BTS raster {tif} pixel ({r},{c}), {res:.0f} m cells",
            "source": "US DOT BTS National Transportation Noise Map 2022/2023 (road + rail + aviation, LAeq 24 h)", "floor_db": 45}


merge_json_houses(OUT / "houses.json", houses, keys=("meta_canopy", "energy_est"), sub={"yard": ["naip_ndvi", "naip_green_pct", "lawn_health"]})
# keep keys other scripts put in extras.json; replace this script's sources (a failed one is dropped, never kept stale) and
# remove the retired grey sources (MnDOT IRIS incidents; Esri-hosted noise copy - re-added only from the BTS download)
_cur = json.load(open(OUT / "extras.json", encoding="utf-8")) if (OUT / "extras.json").exists() else {}
for _k in list(status) + ["traffic_incidents", "noise"]:
    _cur.pop(_k, None); _cur.get("status", {}).pop(_k, None)
_cur.update(ex); _cur["generated"] = NOW; _cur.setdefault("status", {}).update(status)
merge_json_dict(OUT / "extras.json", _cur, list(_cur))
print(f"\n{sum(v == 'ok' for v in status.values())}/{len(status)} sources ok")

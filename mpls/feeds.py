"""Live and frequently-updated open feeds for the block. Re-run any time; every feed is stamped.

  python feeds.py     -> web/mpls/data/feeds.json  (+ sentinel2.jpg, landsat_lst.png)

Each entry: {name, provider, updates, fetched, ok, data | error}. One failing feed never stops the rest.
No API keys are used anywhere.
"""
import csv
import io
import json
import math
import traceback

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json"))
LON, LAT = meta["origin"]
W, So, E, N = meta["bounds"]
ZIP = "55407"
UA = {"User-Agent": "roofprint-osint/0.1 (open-data research)"}
S = requests.Session(); S.headers.update(UA)
_req = S.request
S.request = lambda *a, **k: _req(*a, **{**k, "timeout": k.get("timeout", 90)})
NOW = datetime.now(timezone.utc)
feeds = []


import os
ONLY = [x.strip().lower() for x in os.environ.get('FEEDS_ONLY', '').split(',') if x.strip()]


def feed(name, provider, updates):
    def wrap(fn):
        if ONLY and not any(name.lower().startswith(o) for o in ONLY):
            return fn
        entry = {"name": name, "provider": provider, "updates": updates, "fetched": NOW.isoformat(timespec="minutes")}
        try:
            try:
                entry["data"] = fn()
            except Exception:
                import time; time.sleep(5); entry["data"] = fn()
            entry["ok"] = True
            print(f"ok    {name}")
        except Exception as e:  # keep going
            entry["ok"] = False; entry["error"] = f"{type(e).__name__}: {e}"[:300]
            print(f"FAIL  {name}: {entry['error']}")
        feeds.append(entry)
        return fn
    return wrap


def km(lon, lat):
    return math.hypot((lon - LON) * 111.32 * math.cos(math.radians(LAT)), (lat - LAT) * 110.54)


@feed("Weather forecast (hourly, 48 h) + active alerts", "NOAA National Weather Service", "hourly / live")
def nws():
    pt = S.get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}", timeout=60).json()["properties"]
    hourly = S.get(pt["forecastHourly"], timeout=60).json()["properties"]["periods"][:48]
    alerts = S.get(f"https://api.weather.gov/alerts/active?point={LAT:.4f},{LON:.4f}", timeout=60).json().get("features", [])
    return {"office": pt.get("cwa"), "hourly": [{"t": h["startTime"][:16], "temp_f": h["temperature"], "wind": h["windSpeed"], "dir": h["windDirection"],
                                                  "pop": (h.get("probabilityOfPrecipitation") or {}).get("value"), "sky": h["shortForecast"]} for h in hourly],
            "alerts": [{"event": a["properties"]["event"], "severity": a["properties"]["severity"], "headline": a["properties"]["headline"],
                        "ends": a["properties"].get("ends")} for a in alerts]}


@feed("Earthquakes, last 30 days, M2.5+ within 1,500 km", "USGS Earthquake Hazards Program", "every minute")
def quakes():
    d = S.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_month.geojson", timeout=60).json()["features"]
    out = []
    for f in d:
        lo, la = f["geometry"]["coordinates"][:2]
        dist = km(lo, la)
        if dist <= 1500:
            out.append({"mag": f["properties"]["mag"], "place": f["properties"]["place"], "km": round(dist),
                        "time": datetime.fromtimestamp(f["properties"]["time"] / 1000, timezone.utc).isoformat(timespec="minutes")})
    return sorted(out, key=lambda q: q["km"])


@feed("Creek flow and level: Minnehaha Creek at Hiawatha Ave", "USGS Water Services (site 05289800)", "every 15 min")
def creek():
    d = S.get("https://waterservices.usgs.gov/nwis/iv/", params={"sites": "05289800", "parameterCd": "00060,00065", "period": "P7D", "format": "json"}, timeout=60).json()
    out = {}
    for ts in d["value"]["timeSeries"]:
        name = ts["variable"]["variableName"].split(",")[0]
        vals = ts["values"][0]["value"]
        out[name] = {"unit": ts["variable"]["unit"]["unitCode"], "latest": vals[-1]["value"] if vals else None, "time": vals[-1]["dateTime"][:16] if vals else None,
                     "series": [[v["dateTime"][:13], float(v["value"])] for v in vals[::8]]}
    return out


@feed("Daily climate at the block: sun, UV index, temperature, rain, snow depth, wind", "NASA POWER", "daily (2â€“3 day lag)")
def power():
    end = date.today() - timedelta(days=3); start = end - timedelta(days=365 * 5)
    p = "ALLSKY_SFC_SW_DWN,ALLSKY_SFC_UV_INDEX,T2M_MAX,T2M_MIN,PRECTOTCORR,WS10M_MAX,SNODP"
    d = S.get("https://power.larc.nasa.gov/api/temporal/daily/point", params={"parameters": p, "community": "RE", "longitude": LON, "latitude": LAT,
              "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON"}, timeout=180).json()["properties"]["parameter"]
    days = sorted(d["T2M_MAX"])
    val = lambda k, dd: d[k][dd] if d[k][dd] > -900 else None
    monthly = {}
    for dd in days:
        m = dd[4:6]; r = monthly.setdefault(m, {"sun": [], "uv": [], "tmax": [], "tmin": [], "rain": [], "freeze_thaw": 0, "days": 0})
        for k, key in [("ALLSKY_SFC_SW_DWN", "sun"), ("ALLSKY_SFC_UV_INDEX", "uv"), ("T2M_MAX", "tmax"), ("T2M_MIN", "tmin"), ("PRECTOTCORR", "rain")]:
            v = val(k, dd)
            if v is not None:
                r[key].append(v)
        tx, tn = val("T2M_MAX", dd), val("T2M_MIN", dd)
        if tx is not None and tn is not None and tx > 0 > tn:
            r["freeze_thaw"] += 1
        r["days"] += 1
    years = len(days) / 365.25
    clim = {m: {"sun_kwh_m2_day": round(float(np.mean(r["sun"])), 2), "uv_index": round(float(np.mean(r["uv"])), 1) if r["uv"] else None,
                "tmax_c": round(float(np.mean(r["tmax"])), 1), "tmin_c": round(float(np.mean(r["tmin"])), 1),
                "rain_mm": round(float(np.sum(r["rain"])) / years, 0), "freeze_thaw_days": round(r["freeze_thaw"] / years, 1)} for m, r in sorted(monthly.items())}
    last30 = days[-30:]
    return {"period": f"{days[0]}â€“{days[-1]}", "monthly_climatology": clim,
            "freeze_thaw_days_per_year": round(sum(c["freeze_thaw_days"] for c in clim.values()), 0),
            "last30": [{"d": dd, "sun": val("ALLSKY_SFC_SW_DWN", dd), "uv": val("ALLSKY_SFC_UV_INDEX", dd), "tmax": val("T2M_MAX", dd), "tmin": val("T2M_MIN", dd),
                        "rain": val("PRECTOTCORR", dd), "wind_max": val("WS10M_MAX", dd), "snow_cm": val("SNODP", dd)} for dd in last30]}


@feed("Station weather: Minneapolis–St Paul airport, last 7 days (temp, gusts, rain, snow depth)", "NOAA NWS observations (KMSP)", "hourly / live")
def station():
    fs = S.get("https://api.weather.gov/stations/KMSP/observations", params={"limit": 168}, timeout=90).json()["features"]
    v = lambda p, k: (p.get(k) or {}).get("value")
    rows = [{"t": f["properties"]["timestamp"][:16], "temp_c": v(f["properties"], "temperature"), "gust_kmh": v(f["properties"], "windGust"),
             "wind_kmh": v(f["properties"], "windSpeed"), "precip_mm": v(f["properties"], "precipitationLastHour"), "text": f["properties"].get("textDescription")} for f in fs]
    gusts = [r for r in rows if r["gust_kmh"]]
    return {"latest": rows[0] if rows else None, "hours": len(rows), "max_gust_kmh": max((r["gust_kmh"] for r in gusts), default=None),
            "rain_mm_7d": round(sum(r["precip_mm"] or 0 for r in rows), 1), "series": rows[::3]}


# Zillow ZHVI removed: Zillow's terms are not a clear open licence (grey). FHFA HPI for ZIP 55407 is in extras.json (fhfa_hpi).

@feed("Flood insurance claims in ZIP 55407 (NFIP)", "OpenFEMA", "monthly")
def nfip():
    d = None
    for ds in ("NfipClaims", "FimaNfipClaims"):
        for ver in ("v3", "v2"):
            r = S.get(f"https://www.fema.gov/api/open/{ver}/{ds}", params={"$filter": f"reportedZipCode eq '{ZIP}'", "$top": 1000}, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("application/json"):
                d = r.json()[ds]; break
        if d is not None:
            break
    if d is None:
        raise ValueError("OpenFEMA NFIP claims endpoint not found under known names")
    yrs = {}
    for c in d:
        y = c.get("yearOfLoss"); yrs[y] = yrs.get(y, 0) + 1
    return {"claims": len(d), "paid_building_usd": round(sum(c.get("amountPaidOnBuildingClaim") or 0 for c in d)), "by_year": dict(sorted(yrs.items()))}


@feed("Disaster declarations for Hennepin County", "OpenFEMA", "as declared (daily)")
def declarations():
    d = S.get("https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries", params={"$filter": "state eq 'MN' and fipsCountyCode eq '053'",
              "$orderby": "declarationDate desc", "$top": 25, "$select": "declarationDate,incidentType,declarationTitle,disasterNumber"}, timeout=120).json()["DisasterDeclarationsSummaries"]
    return [{"date": x["declarationDate"][:10], "type": x["incidentType"], "title": x["declarationTitle"], "number": x["disasterNumber"]} for x in d]


@feed("National Risk Index for this census tract (18 hazards)", "FEMA National Risk Index", "yearly (static)")
def nri():
    url = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0/query"
    d = S.get(url, params={"geometry": f"{LON},{LAT}", "geometryType": "esriGeometryPoint", "inSR": 4326, "outFields": "*", "returnGeometry": "false", "f": "json"}, timeout=120).json()
    a = d["features"][0]["attributes"]
    keys = {"RISK_RATNG": "overall risk", "SOVI_RATNG": "social vulnerability", "RESL_RATNG": "community resilience", "HAIL_RISKR": "hail", "TRND_RISKR": "tornado",
            "SWND_RISKR": "strong wind", "WNTW_RISKR": "winter weather", "CWAV_RISKR": "cold wave", "HWAV_RISKR": "heat wave", "IFLD_RISKR": "inland flood",
            "ERQK_RISKR": "earthquake", "LTNG_RISKR": "lightning", "WFIR_RISKR": "wildfire", "DRGT_RISKR": "drought", "ICES_RISKR": "ice storm"}
    return {"tract": a.get("TRACTFIPS"), "ratings": {v: a.get(k) for k, v in keys.items() if a.get(k)}}


@feed("Soil under the block (drainage, hydrologic group, texture)", "USDA NRCS Soil Data Access (SSURGO)", "yearly")
def soil():
    q = (f"SELECT mu.muname, c.compname, c.comppct_r, c.drainagecl, c.hydgrp, c.taxorder FROM mapunit mu "
         f"INNER JOIN component c ON c.mukey = mu.mukey WHERE mu.mukey IN (SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({LON} {LAT})')) "
         f"ORDER BY c.comppct_r DESC")
    d = S.post("https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest", data={"query": q, "format": "JSON+COLUMNNAME"}, timeout=120).json()
    t = d["Table"]; cols = t[0]
    return [dict(zip(cols, r)) for r in t[1:6]]


@feed("Places within 1 km (shops, schools, parks, services)", "Overture Maps places (open data)", "monthly")
def places():
    import duckdb
    con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    dl, dt = 0.0127, 0.009
    rows = con.execute(f"""SELECT struct_extract(names, 'primary'), coalesce(basic_category, struct_extract(taxonomy, 'primary')), bbox.xmin, bbox.ymin, confidence FROM
        read_parquet('s3://overturemaps-us-west-2/release/2026-11-18.0/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {LON - dl} AND {LON + dl} AND bbox.ymin BETWEEN {LAT - dt} AND {LAT + dt} AND confidence > 0.6""").fetchall()
    out = [{"name": r[0], "category": r[1], "m": round(km(r[2], r[3]) * 1000), "lon": r[2], "lat": r[3]} for r in rows if r[0]]
    cats = {}
    for p in out:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    return {"count": len(out), "top_categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])[:15]), "nearest": sorted(out, key=lambda p: p["m"])[:40]}


@feed("Bus and rail stops within 600 m", "Metropolitan Council 'Transit Stops' (public domain under the MN Government Data Practices Act); fallback USDOT BTS National Transit Map Stops (CC BY 3.0 US)", "daily (stop locations and routes served; no live departures)")
def transit():
    # Stop locations only. Metro Transit's GTFS / NexTrip terms are not a clear open licence, so live departures were dropped.
    # Primary: Met Council Transit Stops (item ee70abbc572c46d189f7aa4a0966536c, licenseInfo: "None. This dataset is public
    # domain under the Minnesota Government Data Practices Act"). BTS NTM is older/incomplete here, used only as a fallback.
    dl, dt = 0.0077, 0.0055  # ~600 m box
    try:
        r = S.get("https://arcgis.metc.state.mn.us/data2/rest/services/transportation/Transit_Stops/FeatureServer/0/query",
                  params={"where": "1=1", "geometry": f"{LON - dl},{LAT - dt},{LON + dl},{LAT + dt}", "geometryType": "esriGeometryEnvelope",
                          "inSR": 4326, "outSR": 4326, "outFields": "site_id,site_on,site_at,corn_desc,RoutesServed,active_status,adaaccess",
                          "returnGeometry": "true", "resultRecordCount": 2000, "f": "json"}, timeout=120)
        r.raise_for_status(); js = r.json()
        if "error" in js or not js.get("features"):
            raise ValueError(js.get("error", "no stops"))
        near = sorted([{"id": str(a["site_id"]), "name": f"{a.get('site_on') or ''} & {a.get('site_at') or ''}".strip(" &"),
                        "corner": a.get("corn_desc"), "routes": a.get("RoutesServed"), "ada": a.get("adaaccess"),
                        "m": round(km(f["geometry"]["x"], f["geometry"]["y"]) * 1000), "lon": round(f["geometry"]["x"], 6), "lat": round(f["geometry"]["y"], 6),
                        "source": "Met Council"}
                       for f in js["features"] for a in [f["attributes"]] if f.get("geometry")
                       and str(a.get("active_status") or "A").upper()[:1] not in ("I", "0", "N")], key=lambda s_: s_["m"])
        return [s_ for s_ in near if s_["m"] <= 600][:12]
    except Exception as e:  # noqa: BLE001
        print("     Met Council stops failed, using BTS NTM:", str(e)[:100])
    r = S.get("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_National_Transit_Map_Stops/FeatureServer/0/query",
              params={"where": "1=1", "geometry": f"{LON - dl},{LAT - dt},{LON + dl},{LAT + dt}", "geometryType": "esriGeometryEnvelope",
                      "inSR": 4326, "outFields": "stop_id,stop_name,stop_lat,stop_lon,ntd_id,feed_id", "returnGeometry": "false",
                      "resultRecordCount": 2000, "f": "json"}, timeout=120)
    r.raise_for_status(); js = r.json()
    if "error" in js:
        raise ValueError(js["error"])
    near = sorted([{"id": a["stop_id"], "name": a["stop_name"], "m": round(km(a["stop_lon"], a["stop_lat"]) * 1000),
                    "lon": a["stop_lon"], "lat": a["stop_lat"], "ntd_id": a.get("ntd_id")}
                   for a in (f["attributes"] for f in js.get("features", [])) if a.get("stop_lon") is not None], key=lambda s_: s_["m"])
    near = list({(s_["name"], s_["id"]): s_ for s_ in near}.values())
    return [s_ for s_ in near if s_["m"] <= 600][:12]

@feed("Latest clear Sentinel-2 image: greenness (NDVI) and true colour", "ESA Copernicus Sentinel-2 via Element 84 Earth Search", "every ~5 days")
def sentinel2():
    import rasterio
    from pystac_client import Client
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    from PIL import Image
    cat = Client.open("https://earth-search.aws.element84.com/v1")
    items = list(cat.search(collections=["sentinel-2-l2a"], bbox=[W, So, E, N], datetime=f"{(date.today() - timedelta(days=120))}/{date.today()}",
                            query={"eo:cloud_cover": {"lt": 15}}, sortby=[{"field": "properties.datetime", "direction": "desc"}], max_items=1).items())
    it = items[0]
    def read(band):
        with rasterio.Env(AWS_NO_SIGN_REQUEST='YES', GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR'), rasterio.open(it.assets[band].href) as src:
            b = transform_bounds("EPSG:4326", src.crs, W, So, E, N)
            return src.read(1, window=from_bounds(*b, src.transform), boundless=True).astype(float)
    red, nir, grn, blu = read("red"), read("nir"), read("green"), read("blue")
    ndvi = (nir - red) / np.maximum(nir + red, 1)
    rgb = np.clip(np.dstack([red, grn, blu]) / 2500 * 255, 0, 255).astype(np.uint8)
    Image.fromarray(rgb).resize((200, 200), Image.NEAREST).save(OUT / "sentinel2.jpg", quality=90)
    return {"date": it.properties["datetime"][:10], "cloud_pct": it.properties.get("eo:cloud_cover"), "ndvi_mean": round(float(np.nanmean(ndvi)), 3),
            "ndvi_grid": np.round(ndvi, 2).tolist(), "pixel_m": 10}


@feed("Summer land surface temperature (urban heat), 30 m", "USGS/NASA Landsat 8â€“9 via Microsoft Planetary Computer", "every ~8 days")
def landsat():
    import planetary_computer, rasterio
    from pystac_client import Client
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
    yr = date.today().year if date.today().month > 8 else date.today().year - 1
    items = list(cat.search(collections=["landsat-c2-l2"], bbox=[W, So, E, N], datetime=f"{yr}-06-15/{yr}-08-31",
                            query={"eo:cloud_cover": {"lt": 10}, "platform": {"in": ["landsat-8", "landsat-9"]}}, max_items=10).items())
    it = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])[0]
    with rasterio.open(it.assets["lwir11"].href) as src:
        b = transform_bounds("EPSG:4326", src.crs, W - 0.004, So - 0.003, E + 0.004, N + 0.003)
        win = from_bounds(*b, src.transform)
        t = src.read(1, window=win, boundless=True).astype(float) * 0.00341802 + 149.0 - 273.15
    return {"date": it.properties["datetime"][:10], "scene": it.id, "lst_c_mean": round(float(np.mean(t)), 1), "lst_c_max": round(float(np.max(t)), 1),
            "grid": np.round(t, 1).tolist(), "pixel_m": 30, "bounds": [W - 0.004, So - 0.003, E + 0.004, N + 0.003]}


@feed("Air quality and smoke forecast proxy: daily max UV + wind from NWS gridpoint", "NOAA National Weather Service gridpoints", "hourly")
def nws_grid():
    pt = S.get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}", timeout=60).json()["properties"]
    g = S.get(pt["forecastGridData"], timeout=60).json()["properties"]
    take = lambda k: [[v["validTime"][:13], v["value"]] for v in g.get(k, {}).get("values", [])[:24]]
    return {"wind_gust_kmh": take("windGust"), "sky_cover_pct": take("skyCover"), "snowfall_mm": take("snowfallAmount"), "ice_mm": take("iceAccumulation")}


DROPPED = ("Zillow", "Metro Transit")  # providers of retired feeds (grey licences) - never carried over
if ONLY and (OUT / "feeds.json").exists():
    old = json.load(open(OUT / "feeds.json"))["feeds"]
    new_names = {f["name"].split(":")[0] for f in feeds}
    feeds = [f for f in old if f["name"].split(":")[0] not in new_names and not any(d in f.get("provider", "") for d in DROPPED)] + feeds
json.dump({"generated": NOW.isoformat(timespec="minutes"), "feeds": feeds}, open(OUT / "feeds.json", "w"), separators=(",", ":"))
print(f"\n{sum(f['ok'] for f in feeds)}/{len(feeds)} feeds ok -> feeds.json")

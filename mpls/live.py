"""Fast refresh of the frequently changing layers for the Roofprint block (for the 6-hour scheduled task).

  python live.py   -> updates in place, keeping everything else:
    extras.json  fires (NASA FIRMS, ~3 h), goes (NASA GIBS GOES-19, 10 min) + goes.jpg
    layers.json  permits (Minneapolis CCS, all years) and cases311 (current + previous year refetched, older kept)
    houses.json  per-house permits, cases311, roof_permit, solar_permit
A source that fails keeps its previous data. Prints one ok/FAIL line per source and a final "N/4 ok".
(MnDOT IRIS live incidents were dropped: no stated licence terms.)
"""
import csv
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
MPLS = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
meta = json.load(open(OUT / "meta.json")); LON, LAT = meta["origin"]; W, So, E, N = meta["bounds"]
MX = 111320 * math.cos(math.radians(LAT)); MY = 110540
S = requests.Session(); S.headers["User-Agent"] = "roofprint-osint/0.1 (open-data research)"
NOW = datetime.now(timezone.utc)
NOWS = NOW.isoformat(timespec="minutes")


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

houses = json.load(open(OUT / "houses.json", encoding="utf-8"))
layers = json.load(open(OUT / "layers.json", encoding="utf-8"))
extras = json.load(open(OUT / "extras.json", encoding="utf-8"))
hx = np.array([np.mean([p[0] for p in h["ring"]]) for h in houses]); hy = np.array([np.mean([p[1] for p in h["ring"]]) for h in houses])
status = {}


def nearest_house(lx, ly, maxd):
    d = np.hypot(hx - lx, hy - ly); i = int(np.argmin(d))
    return i if d[i] <= maxd else None


def run(name, fn):
    try:
        fn(); status[name] = "ok"; print("ok  ", name)
    except Exception as e:
        status[name] = f"{type(e).__name__}: {e}"[:200]; print("FAIL", name, status[name])


def put(key, data, licence, updates):
    extras[key] = {"data": data, "licence": licence, "updates": updates, "fetched": NOWS}
    extras.setdefault("status", {})[key] = "ok"


def fires():
    t = S.get("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_USA_contiguous_and_Hawaii_24h.csv", timeout=120)
    t.raise_for_status(); out = []
    for r in csv.DictReader(io.StringIO(t.text)):
        d = math.hypot((float(r["longitude"]) - LON) * MX, (float(r["latitude"]) - LAT) * MY) / 1000
        if d <= 300:
            out.append({"km": round(d), "date": r["acq_date"], "time": r["acq_time"], "frp": r.get("frp"), "confidence": r.get("confidence")})
    put("fires", sorted(out, key=lambda f: f["km"]), "open (NASA FIRMS)", "~3 hours")


def goes():
    from PIL import Image
    wms = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"; layer = "GOES-East_ABI_GeoColor"
    cap = S.get(wms, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"}, timeout=180).text
    blk = cap[cap.index(f"<Name>{layer}</Name>"):]
    dim = re.search(r'<Dimension name="time"[^>]*>([^<]+)</Dimension>', blk).group(1)
    last = datetime.strptime(dim.strip().split(",")[-1].split("/")[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    p = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": layer, "CRS": "EPSG:4326",
         "BBOX": "41,-98,49,-88", "WIDTH": 1250, "HEIGHT": 1000, "FORMAT": "image/jpeg"}
    for k in range(18):
        t = (last - timedelta(minutes=10 * k)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = S.get(wms, params={**p, "TIME": t}, timeout=120)
        if r.ok and r.headers.get("content-type", "").startswith("image") and np.asarray(Image.open(io.BytesIO(r.content))).mean() > 3:
            (OUT / "goes.jpg").write_bytes(r.content)
            put("goes", {"source": f"NASA GIBS WMS {layer} (NOAA GOES-19 ABI)", "time": t, "file": "goes.jpg", "bbox": [-98, 41, -88, 49], "bytes": len(r.content)},
                "public domain (NOAA via NASA GIBS)", "every 10 minutes")
            return
    raise ValueError("no non-empty GOES GeoColor frame in the last 3 h")


def query(svc, fields):
    r = S.get(f"{MPLS}{svc}/FeatureServer/0/query", params={
        "where": "1=1", "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
        "outFields": fields, "returnGeometry": "true", "f": "geojson", "resultRecordCount": 5000}, timeout=120)
    r.raise_for_status(); j = r.json()
    if "error" in j:
        raise ValueError(j["error"])
    return j.get("features", [])


def day(v):
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if isinstance(v, (int, float)) else (str(v)[:10] if v else None)


def permits():
    P = []
    for f in query("CCS_Permits", "APN,permitType,workType,status,issueDate,completeDate,permit_Value,comments,occupancyType"):
        g = f.get("geometry"); p = f["properties"]
        if not g:
            continue
        lx, ly = (g["coordinates"][0] - LON) * MX, (g["coordinates"][1] - LAT) * MY
        P.append({"house": nearest_house(lx, ly, 25), "x": round(lx, 1), "y": round(ly, 1), "type": p.get("permitType"), "work": p.get("workType"),
                  "status": p.get("status"), "value": p.get("permit_Value"), "comments": (p.get("comments") or "")[:140], "date": day(p.get("issueDate"))})
    if not P and layers.get("permits"):
        raise ValueError("0 permits returned; keeping previous")
    layers["permits"] = P


def cases311():
    yrs = {NOW.year, NOW.year - 1}; new = []
    for yr in sorted(yrs):
        try:
            feats = query(f"Public_311_{yr}", "TYPENAME,TITLE,REASONNAME,OPENEDDATETIME,CASESTATUS")
        except Exception as e:
            if yr == NOW.year:
                print("     311", yr, "not available:", str(e)[:80])
                yrs.discard(yr); continue
            raise
        for f in feats:
            g = f.get("geometry"); p = f["properties"]
            if not g:
                continue
            lx, ly = (g["coordinates"][0] - LON) * MX, (g["coordinates"][1] - LAT) * MY
            new.append({"house": nearest_house(lx, ly, 20), "x": round(lx, 1), "y": round(ly, 1), "type": p.get("TYPENAME"), "title": p.get("TITLE"),
                        "status": p.get("CASESTATUS"), "date": day(p.get("OPENEDDATETIME"))})
    keep = [c for c in layers.get("cases311", []) if not (c.get("date") or "")[:4].isdigit() or int(c["date"][:4]) not in yrs]
    layers["cases311"] = keep + new


run("fires", fires)
run("goes", goes)
run("permits", permits)
run("cases311", cases311)

txt = lambda q: ((q.get("work") or "") + " " + (q.get("comments") or "")).lower()
roofy = lambda q: any(w in txt(q) for w in ("reroof", "re-roof", "re roof", "tear off", "shingle", "roof replace", "new roof")) and "solar" not in txt(q)
for i, h in enumerate(houses):
    h["permits"] = [q for q in layers["permits"] if q["house"] == i]
    h["cases311"] = sorted([q for q in layers["cases311"] if q["house"] == i], key=lambda c: c.get("date") or "", reverse=True)
    h["roof_permit"] = max((q["date"] for q in h["permits"] if q["date"] and roofy(q)), default=None)
    h["solar_permit"] = max((q["date"] for q in h["permits"] if q["date"] and "solar" in txt(q)), default=None)

extras.pop("traffic_incidents", None); extras.get("status", {}).pop("traffic_incidents", None)  # no licence terms (MnDOT IRIS)
extras["live_refreshed"] = NOWS
merge_json_houses(OUT / "houses.json", houses, keys=("permits", "cases311", "roof_permit", "solar_permit"))
merge_json_dict(OUT / "layers.json", layers, ("permits", "cases311"))
_x = json.load(open(OUT / "extras.json", encoding="utf-8"))
_x.update({k: extras[k] for k in ("fires", "goes", "live_refreshed") if k in extras})
_x.setdefault("status", {}).update({k: v for k, v in extras.get("status", {}).items() if k in ("fires", "goes")})
_x.pop("traffic_incidents", None); _x["status"].pop("traffic_incidents", None)
merge_json_dict(OUT / "extras.json", _x, list(_x))
print(f"\n{sum(v == 'ok' for v in status.values())}/{len(status)} ok | permits {len(layers['permits'])} | 311 {len(layers['cases311'])}"
      f" | fires<=300km {len(extras.get('fires', {}).get('data', []))}")

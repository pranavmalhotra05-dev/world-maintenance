"""Open-data dossier for every house in the 3D block (run after house3d.py).

Pulls, for the block in web/mpls/data/meta.json:
  imagery     public-domain aerial photos, one per year -> img/<year>.jpg: USDA NAIP 2010-2023 (1 m -> 30 cm, summer)
              and Met Council 7-county orthos 2020 + 2025 (30 cm, spring leaf-off), via the MnGeo image server WMS
              (NAIP falls back to Microsoft Planetary Computer); + per-roof colour time series and the year the
              roof colour changed (likely re-roof)
  assessor    City of Minneapolis Assessing 2026: roof, exterior wall, construction, stories, heating,
              garage stalls, bedrooms, baths, units, value (owner name NOT kept)
  permits     City of Minneapolis CCS permits (roof/solar/building/mech/plumbing), applicant names NOT kept
  311         City of Minneapolis 311 cases 2015-2026 near each house
  pavement    Public Works street pavement condition index (PCI), build / overlay / seal-coat years
  alleys, planimetric curb/sidewalk/driveway lines
  flood       FEMA National Flood Hazard Layer zone
  hail        NOAA MRMS radar hail at the house since Oct 2020 (from data/mpls/hail), and NOAA SPC
              ground hail reports within 10 km since 2015
  lidar 2011  Minnesota 2011 statewide LiDAR vs 2022: height change per building (additions / rebuilds)
Writes houses.json (enriched), layers.json (streets/alleys/curbs/311/permits as map features), sources.json.
houses.json / layers.json / sources.json are merged (re-read just before writing) because other scripts add to them.
"""
import io
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path

import laspy
import numpy as np
import pandas as pd
import requests
from PIL import Image
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import Point, Polygon, shape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
(OUT / "img").mkdir(exist_ok=True)
MPLS = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
S = requests.Session()
meta = json.load(open(OUT / "meta.json"))
houses = json.load(open(OUT / "houses.json"))
W, So, E, N = meta["bounds"]
lon0, lat0 = meta["origin"]
half = meta["half_m"]
to_m = Transformer.from_crs(4326, 3857, always_xy=True)
cx, cy = to_m.transform(lon0, lat0)
k = 1 / math.cos(math.radians(lat0))
X0, Y0, X1, Y1 = cx - half * k, cy - half * k, cx + half * k, cy + half * k
mx_per_deg = 111320 * math.cos(math.radians(lat0)); my_per_deg = 110540


def local(lon, lat):
    return (np.asarray(lon) - lon0) * mx_per_deg, (np.asarray(lat) - lat0) * my_per_deg


def query(svc, where="1=1", fields="*", layer=0, geom=True):
    r = S.get(f"{MPLS}{svc}/FeatureServer/{layer}/query", params={
        "where": where, "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "outSR": 4326, "outFields": fields, "returnGeometry": str(geom).lower(), "f": "geojson", "resultRecordCount": 5000}, timeout=120)
    r.raise_for_status()
    return r.json().get("features", [])


polys = [Polygon(h["ring"]) for h in houses]
hx = np.array([p.centroid.x for p in polys]); hy = np.array([p.centroid.y for p in polys])
sources = []


def nearest_house(lx, ly, maxd=25.0):
    d = np.hypot(hx - lx, hy - ly)
    i = int(np.argmin(d))
    return i if d[i] <= maxd else None


# ---------------------------------------------------------------- imagery time series (public domain only)
SZ = 2048
WMS = "https://imageserver.gisdata.mn.gov/cgi-bin/wms"
# year -> (MnGeo WMS layer, product); the Met Council orthos take the years they exist (30 cm vs NAIP 0.6-1 m)
IMAGERY = {2010: ("fsa2010", "naip"), 2013: ("fsa2013", "naip"), 2015: ("fsa2015", "naip"), 2017: ("fsa2017", "naip"),
           2019: ("fsa2019", "naip"), 2020: ("met20", "metc"), 2021: ("fsa2021", "naip"), 2023: ("fsa2023", "naip"),
           2025: ("met25", "metc")}


def naip_pc(year):
    """Fallback: the NAIP tile for `year` from Microsoft Planetary Computer, warped onto the block's 3857 grid."""
    import planetary_computer
    import rasterio
    from pystac_client import Client
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject
    it = next((i for i in Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
               .search(collections=["naip"], bbox=[W, So, E, N], datetime=f"{year}").items()), None)
    if it is None:
        return None
    dst = np.zeros((3, SZ, SZ), dtype=np.uint8)
    with rasterio.open(it.assets["image"].href) as src:
        for b in range(3):
            reproject(rasterio.band(src, b + 1), dst[b], dst_transform=from_bounds(X0, Y0, X1, Y1, SZ, SZ), dst_crs="EPSG:3857",
                      resampling=Resampling.bilinear)
    buf = io.BytesIO(); Image.fromarray(dst.transpose(1, 2, 0)).save(buf, "JPEG", quality=88)
    return buf.getvalue()


img_years, img_src = [], {}
for y, (lyr, prod) in IMAGERY.items():
    data = None
    try:
        r = S.get(WMS, params={"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": lyr, "STYLES": "", "SRS": "EPSG:3857",
                               "BBOX": f"{X0},{Y0},{X1},{Y1}", "WIDTH": SZ, "HEIGHT": SZ, "FORMAT": "image/jpeg"}, timeout=180)
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 20000:
            data = r.content
    except Exception as e:  # noqa: BLE001
        print("WMS failed", y, e)
    if data is None and prod == "naip":
        try:
            data = naip_pc(y)
        except Exception as e:  # noqa: BLE001
            print("Planetary Computer failed", y, e)
    if data:
        (OUT / "img" / f"{y}.jpg").write_bytes(data)
        img_years.append(y); img_src[y] = prod
for f in (OUT / "img").glob("*.jpg"):  # drop photo years we no longer publish
    if f.stem.isdigit() and int(f.stem) not in img_years:
        f.unlink()
print("imagery years:", img_years)
ny = [y for y in img_years if img_src[y] == "naip"]; my_ = [y for y in img_years if img_src[y] == "metc"]
if ny:
    sources.append({"name": "USDA NAIP aerial imagery", "detail": f"{', '.join(map(str, ny))}; 1 m to 30 cm, summer; photo slider + roof-colour history",
                    "url": "https://imageserver.gisdata.mn.gov/cgi-bin/wms (layers fsa<year>); fallback planetarycomputer.microsoft.com (naip)",
                    "licence": "public domain (US federal work, USDA FSA)", "licence_url": "https://registry.opendata.aws/naip/"})
if my_:
    sources.append({"name": "Metropolitan Council 7-county orthoimagery", "detail": f"{', '.join(map(str, my_))}; 30 cm, spring leaf-off; photo slider + roof-colour history",
                    "url": "https://imageserver.gisdata.mn.gov/cgi-bin/wms (layers met20, met25)",
                    "licence": "public domain under the Minnesota Government Data Practices Act (Minn. Stat. ch. 13)",
                    "licence_url": "https://mnatlas.org/metadata/2025_7_county.html"})

# pixel grid in local metres (image is web-mercator aligned to the block)
px = (np.arange(SZ) + 0.5) / SZ * 2 * half - half
GX, GY = np.meshgrid(px, -px)
roof_rgb = {}
for y in img_years:
    a = np.asarray(Image.open(OUT / "img" / f"{y}.jpg").convert("RGB")).astype(float)
    for i, p in enumerate(polys):
        inner = p.buffer(-0.8)
        if inner.is_empty:
            continue
        b = inner.bounds
        c0, c1 = int((b[0] + half) / (2 * half) * SZ), int((b[2] + half) / (2 * half) * SZ) + 1
        r0, r1 = int((half - b[3]) / (2 * half) * SZ), int((half - b[1]) / (2 * half) * SZ) + 1
        sub = contains_xy(inner, GX[r0:r1, c0:c1], GY[r0:r1, c0:c1])
        if sub.sum() < 20:
            continue
        pix = a[r0:r1, c0:c1][sub]
        roof_rgb.setdefault(i, {})[y] = np.median(pix, axis=0).round(1).tolist()

# ---------------------------------------------------------------- assessor (city)
AFIELDS = ("ADDRESSFORMATTED,ZIP1,NEIGHBORHOOD,ZONING,PROPERTYTYPE,LANDVALUE,BUILDINGVALUE,TOTALVALUE,HOMESTEAD,NUMBEROFBUILDINGS,"
           "BUILDINGUSE,YEARBUILT,BASEMENTAREA,ABOVEGROUNDAREA,STORIES,PRIMARYHEATING,CONSTRUCTIONTYPE,EXTERIORWALL,ROOF,GARAGESTALLS,"
           "TOTALBEDROOMS,TOTALUNITS,FIREPLACES,TOTALBATHROOMS")
addrs = sorted({h["address"].upper() for h in houses if h.get("address")})
ass = []
for i in range(0, len(addrs), 40):
    where = " OR ".join(f"ADDRESSFORMATTED LIKE '{a_.replace(chr(39), '')}%'" for a_ in addrs[i:i + 40])
    r = S.post(f"{MPLS}Assessing_Department_Parcel_Data_2026/FeatureServer/0/query",
               data={"where": where, "outFields": AFIELDS, "returnGeometry": "false", "f": "json"}, timeout=120).json()
    ass += [f["attributes"] for f in r.get("features", [])]
amap = {}
for rec in ass:
    amap[(rec.get("ADDRESSFORMATTED") or "").strip().upper()] = rec
print(f"assessor records: {len(ass)}")
sources.append({"name": "City of Minneapolis Assessing parcel data 2026", "detail": "roof, walls, construction, stories, heating, rooms, value (owner names not kept)", "url": "https://opendata.minneapolismn.gov", "licence": "CC0 1.0 (City of Minneapolis open data)", "licence_url": "https://www.arcgis.com/home/item.html?id=f6748f040c6a4244bb2b314b26b44b48"})

# ---------------------------------------------------------------- permits
perm = query("CCS_Permits", fields="APN,permitType,workType,status,issueDate,completeDate,permit_Value,comments,occupancyType")
P = []
for f in perm:
    p = f["properties"]; g = f.get("geometry")
    if not g:
        continue
    lx, ly = local(*g["coordinates"][:2])
    i = nearest_house(float(lx), float(ly))
    dt = p.get("issueDate")
    P.append({"house": i, "x": round(float(lx), 1), "y": round(float(ly), 1), "type": p.get("permitType"), "work": p.get("workType"),
              "status": p.get("status"), "value": p.get("permit_Value"), "comments": (p.get("comments") or "")[:140],
              "date": datetime.fromtimestamp(dt / 1000, tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d") if isinstance(dt, (int, float)) else None})
print(f"permits: {len(P)}")
sources.append({"name": "City of Minneapolis building permits (CCS)", "detail": "roof (RoofWind), solar, building, mechanical; applicant names not kept", "url": "https://opendata.minneapolismn.gov", "licence": "CC0 1.0 (City of Minneapolis open data)", "licence_url": "https://www.arcgis.com/home/item.html?id=df4bfccbe60b42a6a93fb7bc1fd84cc0"})

# ---------------------------------------------------------------- 311 (all years with a public layer)
C = []
for yr in range(2015, 2027):
    try:
        feats = query(f"Public_311_{yr}", fields="TYPENAME,TITLE,REASONNAME,OPENEDDATETIME,CASESTATUS")
    except Exception:
        continue
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        lx, ly = local(*g["coordinates"][:2])
        p = f["properties"]; dt = p.get("OPENEDDATETIME")
        C.append({"house": nearest_house(float(lx), float(ly), 20), "x": round(float(lx), 1), "y": round(float(ly), 1),
                  "type": p.get("TYPENAME"), "title": p.get("TITLE"), "status": p.get("CASESTATUS"),
                  "date": datetime.fromtimestamp(dt / 1000, tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d") if isinstance(dt, (int, float)) else str(dt)[:10]})
print(f"311 cases: {len(C)}")
sources.append({"name": "City of Minneapolis 311 cases", "detail": "2015–2026", "url": "https://opendata.minneapolismn.gov", "licence": "CC0 1.0 (City of Minneapolis open data)", "licence_url": "https://www.arcgis.com/home/item.html?id=cb378a2e807f4cbfb626850b2767e234"})

# ---------------------------------------------------------------- streets, alleys, curbs/sidewalks
def lines(feats, props):
    out = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for part in parts:
            lx, ly = local([c[0] for c in part], [c[1] for c in part])
            out.append({"path": [[round(float(a), 2), round(float(b), 2)] for a, b in zip(lx, ly)], **{k_: f["properties"].get(k_) for k_ in props}})
    return out


pave = lines(query("PWStreetPavementMgmt", fields="STREET_O_NAME_1,PAVEMENT,CONST_YR,RENOVATION_YR,SEALCOAT_YR,OVERLAY_YR,PCI,INSPECTION_DATE"),
             ["STREET_O_NAME_1", "PAVEMENT", "CONST_YR", "RENOVATION_YR", "SEALCOAT_YR", "OVERLAY_YR", "PCI"])
alleys = lines(query("PW_Alleys", fields="PAVED,SEGMENT_LENGTH"), ["PAVED"])
plan = lines(query("PW_Planimetric_Lines", fields="SYMBOL_NAME,SOURCE_DATE"), ["SYMBOL_NAME"])
print(f"pavement segments {len(pave)}, alleys {len(alleys)}, planimetric lines {len(plan)}")
sources.append({"name": "Minneapolis Public Works", "detail": "pavement condition (PCI), alleys, curb/sidewalk/driveway lines", "url": "https://opendata.minneapolismn.gov", "licence": "CC0 1.0 (City of Minneapolis open data)", "licence_url": "https://www.arcgis.com/home/item.html?id=02bfab32c2ff40c1b5692f9ab816820f"})

# ---------------------------------------------------------------- FEMA flood zones
for attempt in range(5):  # the NFHL service intermittently returns an empty body
    try:
        fz = S.get("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query", params={
            "geometry": f"{W},{So},{E},{N}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF", "f": "geojson"}, timeout=120).json().get("features", [])
        break
    except ValueError:
        if attempt == 4:
            raise
        __import__("time").sleep(10 * (attempt + 1))
fzones = [(Polygon(np.column_stack(local(*np.array(f["geometry"]["coordinates"][0]).T[:2]))), f["properties"]) for f in fz
          if f.get("geometry") and f["geometry"]["type"] == "Polygon"]
print(f"FEMA flood polygons: {len(fzones)}")
sources.append({"name": "FEMA National Flood Hazard Layer", "detail": "flood zone per house", "url": "https://hazards.fema.gov", "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://www.copyright.gov/title17/92chap1.html#105"})

# ---------------------------------------------------------------- hail: radar at each house + ground reports nearby
hail_days = []
H = ROOT / "data" / "mpls" / "hail"
lon_h = lon0 + hx / mx_per_deg; lat_h = lat0 + hy / my_per_deg
for f in sorted(H.glob("*.npz")):
    z = np.load(f); la, lo, m = z["lats"], z["lons"], z["mesh"].astype(float)
    r = np.clip(np.round((la[0] - lat_h) / (la[0] - la[1])).astype(int), 0, len(la) - 1)
    c = np.clip(np.round((lon_h - lo[0]) / (lo[1] - lo[0])).astype(int), 0, len(lo) - 1)
    v = np.minimum(m[r, c] / 25.4, 4.5)
    if v.max() >= 0.5:
        hail_days.append((f.stem, v))
print(f"radar hail days over the block: {len(hail_days)}")
spc = []
hz = ROOT / "data" / "national" / "hail.zip"
with zipfile.ZipFile(hz) as zf:
    df = pd.read_csv(zf.open(zf.namelist()[0]))
df = df[(df.yr >= 2015) & (df.slat > 0)]
d_km = np.hypot((df.slon - lon0) * mx_per_deg, (df.slat - lat0) * my_per_deg) / 1000
for _, r in df[d_km <= 10].iterrows():
    spc.append({"date": f"{int(r.yr)}-{int(r.mo):02d}-{int(r.dy):02d}", "size_in": float(r.mag), "km": round(float(np.hypot((r.slon - lon0) * mx_per_deg, (r.slat - lat0) * my_per_deg) / 1000), 1)})
sources.append({"name": "NOAA MRMS radar hail (MESH)", "detail": "daily since Oct 2020", "url": "https://registry.opendata.aws/noaa-mrms-pds", "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://registry.opendata.aws/noaa-mrms-pds/"})
sources.append({"name": "NOAA SPC storm reports", "detail": f"{len(spc)} hail reports within 10 km since 2015", "url": "https://www.spc.noaa.gov/wcm", "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://www.weather.gov/disclaimer"})

# ---------------------------------------------------------------- 2011 LiDAR (statewide) vs 2022
EPT11 = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/MN_FullState"
root = S.get(f"{EPT11}/ept.json", timeout=60).json()["bounds"]
box = (X0, Y0, X1, Y1)


def nodes(rootb):
    found, todo = [], ["0-0-0-0"]
    while todo:
        k0 = todo.pop()
        for kk, nn in S.get(f"{EPT11}/ept-hierarchy/{k0}.json", timeout=60).json().items():
            d, x, y, _ = map(int, kk.split("-")); s = 2 ** d
            w_ = (rootb[3] - rootb[0]) / s; h_ = (rootb[4] - rootb[1]) / s
            if rootb[0] + (x + 1) * w_ < box[0] or rootb[0] + x * w_ > box[2] or rootb[1] + (y + 1) * h_ < box[1] or rootb[1] + y * h_ > box[3]:
                continue
            if nn == -1:
                if kk != k0:
                    todo.append(kk)
            elif nn > 0:
                found.append(kk)
    return found


pts = []
for kk in nodes(root):
    las = laspy.read(io.BytesIO(S.get(f"{EPT11}/ept-data/{kk}.laz", timeout=180).content))
    x_, y_ = np.asarray(las.x), np.asarray(las.y)
    m_ = (x_ >= box[0]) & (x_ <= box[2]) & (y_ >= box[1]) & (y_ <= box[3])
    pts.append(np.column_stack([(x_[m_] - cx) / k, (y_[m_] - cy) / k, np.asarray(las.z)[m_], np.asarray(las.classification)[m_]]))
old = np.vstack(pts) if pts else np.zeros((0, 4))
print(f"2011 LiDAR points in block: {len(old):,}")
sources.append({"name": "Minnesota 2011 LiDAR (USGS MN_FullState)", "detail": f"{len(old) / (2 * half) ** 2:.1f} pts/m², for change since 2011", "url": "https://registry.opendata.aws/usgs-lidar/", "licence": "public domain (US federal work, 17 U.S.C. 105)", "licence_url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits"})
g_old = old[old[:, 3] == 2]

# ---------------------------------------------------------------- per-house dossier
block_med = {y: np.median([np.array(v[y]) for v in roof_rgb.values() if y in v], axis=0) for y in img_years}
for i, h in enumerate(houses):
    p = polys[i]
    # assessor match by address, else nearest record
    rec = amap.get((h.get("address") or "").upper())
    h["assessor"] = {kk3.lower(): v for kk3, v in rec.items()} if rec else None
    h["permits"] = [q for q in P if q["house"] == i]
    h["cases311"] = [q for q in C if q["house"] == i]
    txt = lambda q: ((q.get("work") or "") + " " + (q.get("comments") or "")).lower()
    roofy = lambda q: any(w in txt(q) for w in ("reroof", "re-roof", "re roof", "tear off", "shingle", "roof replace", "new roof")) and "solar" not in txt(q)
    h["roof_permit"] = max((q["date"] for q in h["permits"] if q["date"] and roofy(q)), default=None)
    h["solar_permit"] = max((q["date"] for q in h["permits"] if q["date"] and "solar" in ((q.get("work") or "") + (q.get("comments") or "")).lower()), default=None)
    fzone = next((pp for poly, pp in fzones if poly.contains(Point(hx[i], hy[i]))), None)
    h["flood_zone"] = fzone["FLD_ZONE"] if fzone else "X (minimal, outside mapped hazard)"
    hh = [(d, round(float(v[i]), 2)) for d, v in hail_days if v[i] >= 0.5]
    h["hail_history"] = hh
    h["hail_max_in"] = max((v for _, v in hh), default=0)
    # roof colour time series and the biggest year-to-year change
    ts = roof_rgb.get(i, {})
    h["roof_color"] = ts
    ys = sorted(ts)
    rel = {y: np.array(ts[y]) / block_med[y] for y in ys}  # relative to the block's typical roof that year
    best, best_y = 0.0, None
    for t in range(2, len(ys) - 1):  # at least 2 photos before and 2 after the change
        before = np.mean([rel[y] for y in ys[:t]], axis=0); after = np.mean([rel[y] for y in ys[t:]], axis=0)
        spread = np.mean([np.linalg.norm(rel[y] - before) for y in ys[:t]] + [np.linalg.norm(rel[y] - after) for y in ys[t:]])
        step = float(np.linalg.norm(after - before)) / max(spread, 0.05)
        if step > best:
            best, best_y = step, ys[t]
    h["roof_color_change"] = {"year": best_y, "strength": round(best, 2)} if best_y and best > 3.0 else None
    # 2011 vs 2022 height
    if len(old):
        ins = contains_xy(p, old[:, 0], old[:, 1])
        nb = np.hypot(g_old[:, 0] - hx[i], g_old[:, 1] - hy[i]) < 20
        if ins.sum() >= 3 and nb.sum() >= 3:
            h11 = float(np.percentile(old[ins, 2], 90) - np.median(g_old[nb, 2]))
            h["height_2011_m"] = round(h11, 1)
            h["height_change_m"] = round(h["height_m"] - h11, 1)
        else:
            h["height_2011_m"] = None
            h["height_change_m"] = None

OWN = ("assessor", "permits", "cases311", "roof_permit", "solar_permit", "flood_zone", "hail_history", "hail_max_in",
       "roof_color", "roof_color_change", "height_2011_m", "height_change_m")
fresh = json.load(open(OUT / "houses.json"))  # merge: keep fields other scripts wrote meanwhile
byid = {h["id"]: h for h in houses}
for h in fresh:
    if h["id"] in byid:
        h.update({k_: byid[h["id"]].get(k_) for k_ in OWN})
houses = fresh
json.dump(houses, open(OUT / "houses.json", "w"), separators=(",", ":"))
try:
    L_ = json.load(open(OUT / "layers.json"))
except Exception:  # noqa: BLE001
    L_ = {}
L_.update({"pavement": pave, "alleys": alleys, "planimetric": plan, "cases311": C, "permits": P, "spc_hail": spc,
           "hail_days": [d for d, _ in hail_days], "img_years": img_years, "img_source": {str(y): img_src[y] for y in img_years}})
json.dump(L_, open(OUT / "layers.json", "w"), separators=(",", ":"))
for s_ in sources:
    s_["by"] = "osint.py"
try:  # keep entries other scripts (house3d.py, civic.py) wrote
    other = [s_ for s_ in json.load(open(OUT / "sources.json")) if s_.get("by") and s_["by"] != "osint.py"]
except Exception:  # noqa: BLE001
    other = []
json.dump(other + sources, open(OUT / "sources.json", "w"), indent=1)
print("matched assessor:", sum(1 for h in houses if h["assessor"]), "| permits on houses:", sum(len(h["permits"]) for h in houses),
      "| roof permits:", sum(1 for h in houses if h["roof_permit"]), "| colour-change roofs:", sum(1 for h in houses if h["roof_color_change"]),
      "| 311 on houses:", sum(len(h["cases311"]) for h in houses))

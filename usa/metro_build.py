"""Whole-metro building layer for the globe - lane MX.

Every Overture building inside a set of counties, each with green-licence attributes, cut into small JSON tiles.

  .venv\\Scripts\\python -u -W ignore -m usa.metro_build --metro phoenix-az --counties 04013,04021
  ... --stages buildings,tracts,flood,hail,lst,tiles     (default: all, each stage cached; --force STAGE to redo)

Stages (each writes usa/_cache/metro/<slug>/<stage>.*, aligned row-for-row with buildings.parquet):
  buildings  Overture Maps buildings (DuckDB over s3://overturemaps-us-west-2, bbox prefilter, centroid-in-county
             filter). Centroid, footprint area (EPSG:6933 equal-area), height (Overture height, else
             num_floors x 3.2 m, else unknown), class/subtype, upstream dataset. Licence ODbL 1.0.
  tracts     2020 census tract GEOID by point-in-polygon on web/usa/data/tracts/<ST>.json (federal, public domain);
             tract metrics (ACS, OZ, QCT, NMTC, NRI, FHFA) go once per tract into index.json, not per building.
  flood      FEMA NFHL county packages (S_FLD_HAZ_AR) from the FEMA Map Service Center; public domain.
  hail       NOAA MRMS MESH_Max_1440min (s3://noaa-mrms-pds, public domain) on every storm day of the last
             --hail-months that has a NOAA SPC hail report within --hail-spc-km of the metro box; per-building
             nearest-pixel max (inches) and the day of that max.
  lst        Landsat 8/9 C2 L2 surface temperature (USGS, public domain) via Planetary Computer STAC; clearest
             summer (Jun-Aug) scenes, greedy until every building has a clear 30 m pixel; per-building degC.
  tiles      web/usa/metros/<slug>/tiles/<tx>_<ty>.json (0.05 deg grid) + index.json. County dust/wind/hail
             trigger stats (web/usa/data/weather_triggers.json, NOAA/EPA/IEM public domain) go in index.json.

No owner or occupant data is read or written.
"""
import argparse
import csv
import io
import json
import math
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import shapely

ROOT = Path(__file__).resolve().parents[1]
NAT = ROOT / "data" / "national"
TRACTS = ROOT / "web" / "usa" / "data" / "tracts"
WEATHER = ROOT / "web" / "usa" / "data" / "weather_triggers.json"
SHARED_HAIL = ROOT / "data" / "usa" / "hail"          # read-only reuse of enrich_national.py caches
UA = {"User-Agent": "roof-intel-metro/1.0"}
TILE = 0.05
Q = 1e-5                                              # lon/lat quantum inside a tile (~1 m)
FLOOR_M = 3.2

ap = argparse.ArgumentParser()
ap.add_argument("--metro", required=True, help="slug, e.g. phoenix-az")
ap.add_argument("--counties", required=True, help="comma list of 5-digit county FIPS")
ap.add_argument("--name", default=None, help="display name (default from slug)")
ap.add_argument("--release", default=None, help="Overture release (default: newest on S3)")
ap.add_argument("--stages", default="buildings,tracts,flood,hail,lst,tiles")
ap.add_argument("--force", default="", help="comma list of stages to recompute")
ap.add_argument("--hail-months", type=int, default=24)
ap.add_argument("--hail-spc-km", type=float, default=150)
ap.add_argument("--lst-years", default=None, help="comma list of summers to search (default: last 3)")
ap.add_argument("--lst-max-scenes", type=int, default=14)
A = ap.parse_args()
SLUG = A.metro
FIPS = [c.strip().zfill(5) for c in A.counties.replace(" ", ",").split(",") if c.strip()]  # PowerShell may drop 0s
CACHE = ROOT / "usa" / "_cache" / "metro" / SLUG
OUT = ROOT / "web" / "usa" / "metros" / SLUG
CACHE.mkdir(parents=True, exist_ok=True)
STAGES = [s.strip() for s in A.stages.split(",") if s.strip()]
FORCE = {s.strip() for s in A.force.split(",") if s.strip()}
NOW = datetime.now(timezone.utc)
S = requests.Session(); S.headers.update(UA)
T_START = time.time()
TIMING = {}


def log(*a):
    print(f"[{time.time() - T_START:7.1f}s]", *a, flush=True)


def wjson(p, obj, **kw):
    tmp = Path(str(p) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, **kw)
    os.replace(tmp, p)


def get(url, timeout=120, tries=4, **kw):
    for k in range(tries):
        try:
            r = S.get(url, timeout=timeout, **kw)
            if r.status_code in (404, 403):
                return r
            r.raise_for_status()
            return r
        except Exception:  # noqa: BLE001
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)


def stage_wanted(name, out):
    return name in STAGES and (name in FORCE or not out.exists())


# ------------------------------------------------------------------------------------------------ counties
def load_counties():
    import geopandas as gpd
    g = gpd.read_file(f"zip://{NAT / 'counties.zip'}!cb_2023_us_county_5m.shp")
    g = g[g.GEOID.isin(FIPS)].to_crs(4326)
    if len(g) != len(FIPS):
        raise SystemExit(f"counties not found: {set(FIPS) - set(g.GEOID)}")
    return g


CTY = load_counties()
BBOX = [float(v) for v in CTY.total_bounds]           # W S E N
STATES = sorted(set(CTY.STUSPS))
COUNTY_LIC = {"name": "U.S. Census Bureau 2023 cartographic boundary counties 1:5,000,000 (cb_2023_us_county_5m)",
              "url": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_5m.zip",
              "licence": "US federal government work, public domain (17 U.S.C. 105)", "commercial_ok": "yes"}


def latest_release():
    if A.release:
        return A.release
    xml = get("https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/").text
    rel = sorted(p.split("</Prefix>")[0].split("release/")[1].strip("/") for p in xml.split("<Prefix>")[2:])
    return rel[-1]


# ------------------------------------------------------------------------------------------------ 1 buildings
BPQ = CACHE / "buildings.parquet"


def st_buildings():
    import duckdb
    rel = latest_release()
    W, So, E, N = BBOX
    log(f"buildings: Overture {rel}, bbox {BBOX}")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2'; "
                "SET threads=16; SET enable_progress_bar=false;")
    raw = CACHE / "overture_raw.parquet"
    if not raw.exists() or "buildings" in FORCE:
        con.execute(f"""
          COPY (
            SELECT id, ST_AsWKB(geometry) AS wkb, height, num_floors, class, subtype,
                   sources[1].dataset AS dataset, is_underground
            FROM read_parquet('s3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*',
                              hive_partitioning=1)
            WHERE bbox.xmax >= {W} AND bbox.xmin <= {E} AND bbox.ymax >= {So} AND bbox.ymin <= {N}
          ) TO '{raw.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    df = pd.read_parquet(raw)
    log(f"  {len(df):,} buildings intersect the bbox")
    geom = shapely.from_wkb(df.wkb.values)
    cen = shapely.centroid(geom)
    cx, cy = shapely.get_x(cen), shapely.get_y(cen)
    cty_code = np.full(len(df), -1, np.int16)
    for i, g in enumerate(CTY.geometry.values):
        shapely.prepare(g)
        m = (cty_code < 0) & shapely.contains_xy(g, cx, cy)
        cty_code[m] = i
    keep = cty_code >= 0
    if "is_underground" in df:
        keep &= ~df.is_underground.fillna(False).values.astype(bool)
    log(f"  {keep.sum():,} with centroid inside {', '.join(CTY.NAME)}")
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 6933, always_xy=True)
    gk = geom[keep]
    gp = shapely.transform(gk, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
    area = shapely.area(gp)
    d = df[keep].reset_index(drop=True)
    h = pd.to_numeric(d.height, errors="coerce").values
    nf = pd.to_numeric(d.num_floors, errors="coerce").values
    hsrc = np.where(np.isfinite(h) & (h > 0), 1, np.where(np.isfinite(nf) & (nf > 0), 2, 0)).astype(np.int8)
    hgt = np.where(hsrc == 1, h, np.where(hsrc == 2, nf * FLOOR_M, np.nan))
    out = pd.DataFrame({"id": d.id.values, "lon": cx[keep], "lat": cy[keep], "area_m2": area.astype(np.float32),
                        "height_m": hgt.astype(np.float32), "height_src": hsrc, "num_floors": nf.astype(np.float32),
                        "class": d["class"].values, "subtype": d.subtype.values, "dataset": d.dataset.values,
                        "county": np.array(CTY.GEOID.values)[cty_code[keep]]})
    out.to_parquet(BPQ, compression="zstd")
    (CACHE / "buildings_meta.json").write_text(json.dumps({"release": rel, "bbox_hits": int(len(df)),
                                                           "kept": int(keep.sum())}))
    log(f"  saved {BPQ}")


# ------------------------------------------------------------------------------------------------ 2 tracts
TPQ = CACHE / "tracts.parquet"


def load_tracts():
    rows, metas = [], {}
    for st in STATES:
        d = json.loads((TRACTS / f"{st}.json").read_text())
        metas[st] = d["meta"]
        for t in d["tracts"]:
            if t["g"][:5] in FIPS:
                rows.append(t)
    return rows, metas


def st_tracts():
    b = pd.read_parquet(BPQ, columns=["lon", "lat"])
    rows, _ = load_tracts()
    polys = [shapely.MultiPolygon([shapely.Polygon(p[0], p[1:]) for p in t["geom"]]) for t in rows]
    tree = shapely.STRtree(polys)
    pts = shapely.points(b.lon.values, b.lat.values)
    ii, jj = tree.query(pts, predicate="intersects")
    idx = np.full(len(b), -1, np.int32)
    idx[ii[::-1]] = jj[::-1]          # first match wins on a shared (simplified) edge
    miss = np.where(idx < 0)[0]
    if len(miss):                      # simplified tract edges: nearest tract within ~200 m
        mi, mj = tree.query_nearest(pts[miss], max_distance=0.002, all_matches=False)
        idx[miss[mi]] = mj
    how = np.where(idx >= 0, 1, 0).astype(np.int8)
    how[miss] = np.where(idx[miss] >= 0, 2, 0)
    geoid = np.array([t["g"] for t in rows])
    pd.DataFrame({"tract": np.where(idx >= 0, geoid[np.maximum(idx, 0)], None), "tract_how": how}).to_parquet(TPQ)
    log(f"tracts: {(how == 1).sum():,} inside, {(how == 2).sum():,} nearest (<200 m), {(how == 0).sum():,} none")


# ------------------------------------------------------------------------------------------------ 3 flood
FPQ = CACHE / "flood.parquet"
FDIR = CACHE / "nfhl"


def nfhl_product(cfips):
    st = cfips[:2]
    url = (f"https://msc.fema.gov/portal/advanceSearch?affiliate=fema&query&selstate={st}&selcounty={cfips}"
           f"&selcommunity={cfips}C&searchedCid={cfips}C&method=search")
    j = get(url, timeout=120).json()
    prods = (j.get("EFFECTIVE") or {}).get("NFHL_COUNTY_DATA") or []
    p = next((p for p in prods if p.get("product_NAME") == f"NFHL_{cfips}C"), None)
    return p


def st_flood():
    import geopandas as gpd
    FDIR.mkdir(exist_ok=True)
    b = pd.read_parquet(BPQ, columns=["lon", "lat", "county"])
    zone = np.full(len(b), "", object)
    meta = []
    for cf in FIPS:
        p = nfhl_product(cf)
        if not p:
            log(f"flood: no NFHL county package for {cf}")
            meta.append({"county": cf, "status": "no NFHL county package"})
            continue
        zp = FDIR / p["product_FILE_PATH"]
        if not zp.exists():
            url = ("https://msc.fema.gov/portal/downloadProduct?productTypeID=NFHL&productSubTypeID=NFHL_COUNTY_DATA"
                   f"&productID={p['product_NAME']}")
            log(f"flood: downloading {p['product_FILE_PATH']} ({p.get('product_FILE_SIZE')})")
            with S.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(str(zp) + ".part", "wb") as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch)
            os.replace(str(zp) + ".part", zp)
        names = zipfile.ZipFile(zp).namelist()
        shp = next((n for n in names if n.upper().endswith("S_FLD_HAZ_AR.SHP")), None)
        if shp:
            fz = gpd.read_file(f"zip://{zp}!{shp}", columns=["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"])
        else:
            gdb = next(n.split(".gdb/")[0] + ".gdb" for n in names if ".gdb/" in n)
            fz = gpd.read_file(f"zip://{zp}!{gdb}", layer="S_FLD_HAZ_AR", columns=["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"])
        fz = fz.to_crs(4326)
        sub = fz.ZONE_SUBTY.fillna("").str.strip().str.upper()
        lab = fz.FLD_ZONE.fillna("").str.strip()
        lab = np.where(sub.str.contains("0.2 PCT"), lab + " (0.2% annual chance)",
                       np.where(sub.str.contains("FLOODWAY"), lab + " (floodway)",
                                np.where(sub.str.contains("LEVEE"), lab + " (levee-reduced)",
                                         np.where(sub.str.contains("MINIMAL"), lab + " (minimal)",
                                                  np.where(sub.str.contains("DEPTH LESS THAN 1"),
                                                           lab + " (1% chance, < 1 ft deep)", lab)))))
        lab = np.asarray(lab, object)
        rank = np.where(fz.SFHA_TF.fillna("F").values == "T", 0, np.where(np.char.find(lab.astype(str), "0.2%") >= 0, 1, 2))
        sel = np.where(b.county.values == cf)[0]
        pts = shapely.points(b.lon.values[sel], b.lat.values[sel])
        # tree over the points, polygons as (prepared) query geometries: fast even for huge zone-X polygons
        tree = shapely.STRtree(pts)
        geoms = shapely.make_valid(fz.geometry.values)
        jj, ii = tree.query(geoms, predicate="intersects")
        order = np.lexsort((rank[jj], ii))[::-1]        # per building keep the highest-hazard polygon
        ii, jj = ii[order], jj[order]
        best = {}
        for i, j in zip(ii.tolist(), jj.tolist()):
            best[i] = j
        if best:
            k = np.fromiter(best.keys(), int); v = np.fromiter(best.values(), int)
            zone[sel[k]] = lab[v]
        meta.append({"county": cf, "package": p["product_FILE_PATH"], "effective": p.get("product_EFFECTIVE_DATE_STRING"),
                     "polygons": int(len(fz)), "buildings_in_polygon": int(len(best))})
        log(f"flood {cf}: {len(fz):,} polygons, {len(best):,}/{len(sel):,} buildings in a mapped zone")
    pd.DataFrame({"flood_zone": zone}).to_parquet(FPQ)
    (CACHE / "flood_meta.json").write_text(json.dumps(meta))


# ------------------------------------------------------------------------------------------------ 4 hail
HPQ = CACHE / "hail.parquet"
HDIR = CACHE / "mrms"


def km_box(lo, la):
    W, So, E, N = BBOX
    dx = max(W - lo, 0, lo - E) * 111.32 * math.cos(math.radians(la))
    dy = max(So - la, 0, la - N) * 110.57
    return math.hypot(dx, dy)


def spc_days(start):
    """MRMS 12Z file dates (convective day + 1) with an SPC hail report near the metro since start."""
    fdays = set()

    def add(d, hh, lo, la):
        fd = d + timedelta(days=1) if hh >= 600 else d
        if fd >= start and km_box(lo, la) <= A.hail_spc_km:
            fdays.add(fd)
    with zipfile.ZipFile(NAT / "hail.zip") as zf:
        df = pd.read_csv(zf.open([n for n in zf.namelist() if n.endswith(".csv") and "MACOSX" not in n][0]),
                         encoding="latin-1")
    last = int(df.yr.max())
    df = df[(df.yr >= start.year - 1) & (df.slat > 0)]

    def wcm(df):
        for r in df.itertuples():
            try:
                add(date(int(r.yr), int(r.mo), int(r.dy)), int(str(r.time).replace(":", "")[:4].ljust(4, "0")),
                    float(r.slon), float(r.slat))
            except Exception:  # noqa: BLE001
                pass
    wcm(df)
    y = last + 1
    while y <= date.today().year:
        f = next((p for p in (SHARED_HAIL / "spc" / f"{y}_hail.csv", CACHE / "spc" / f"{y}_hail.csv") if p.exists()), None)
        if f is None:
            r = get(f"https://www.spc.noaa.gov/wcm/data/{y}_hail.csv", timeout=120)
            if r.ok and r.text.startswith("om,"):
                (CACHE / "spc").mkdir(exist_ok=True); f = CACHE / "spc" / f"{y}_hail.csv"; f.write_bytes(r.content)
        if f is None:
            break
        if y >= start.year - 1:
            wcm(pd.read_csv(f, encoding="latin-1"))
        y += 1
    d0 = max(date(y, 1, 1), start - timedelta(days=1))
    days = [d0 + timedelta(i) for i in range((date.today() - d0).days)]

    def daily(d):
        for p in (SHARED_HAIL / "spc" / "daily" / f"{d:%y%m%d}.csv", CACHE / "spc" / "daily" / f"{d:%y%m%d}.csv"):
            if p.exists():
                return d, p.read_bytes().decode("utf-8", "replace")
        r = get(f"https://www.spc.noaa.gov/climo/reports/{d:%y%m%d}_rpts_hail.csv", timeout=60)
        if not r.ok:
            return d, ""
        if (date.today() - d).days > 7:
            p = CACHE / "spc" / "daily" / f"{d:%y%m%d}.csv"; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        return d, r.content.decode("utf-8", "replace")
    with ThreadPoolExecutor(12) as tp:
        for d, txt in tp.map(daily, days):
            for r in csv.DictReader(io.StringIO(txt)):
                try:
                    lo, la = float(r["Lon"]), float(r["Lat"])
                    if km_box(lo, la) <= A.hail_spc_km and d + timedelta(days=1) >= start:
                        fdays.add(d + timedelta(days=1))
                except (KeyError, ValueError, TypeError):
                    pass
    return sorted(fd for fd in fdays if fd <= date.today())


def st_hail():
    sys.path.insert(0, str(ROOT / "dallas"))
    import hail as dh                       # dallas/hail.py: MRMS key naming + GRIB2 decode (crop to dh.W/E/S/N)
    W, So, E, N = BBOX
    dh.W, dh.E, dh.S, dh.N = W - 0.05, E + 0.05, So - 0.05, N + 0.05
    HDIR.mkdir(exist_ok=True)
    start = date.today() - timedelta(days=int(A.hail_months * 30.44))
    fdays = spc_days(start)
    log(f"hail: {len(fdays)} MRMS days with SPC hail within {A.hail_spc_km:g} km since {start}")
    b = pd.read_parquet(BPQ, columns=["lon", "lat"])
    lon, lat = b.lon.values, b.lat.values

    def one(fd):
        name = f"{fd:%Y%m%d}-120000.grib2.gz"
        f = SHARED_HAIL / "mrms" / name
        if not f.exists():
            f = HDIR / name
        if not f.exists():
            r = dh.session.get(f"{dh.BUCKET}/{dh.key_for(fd)}", timeout=120)
            if r.status_code == 404:
                lst = dh.session.get(dh.BUCKET, params={"list-type": "2", "prefix": f"{dh.PRODUCT}/{fd:%Y%m%d}/"},
                                     timeout=60).text
                keys = [k.split("</Key>")[0] for k in lst.split("<Key>")[1:]]
                if not keys:
                    return fd, None
                kk = min(keys, key=lambda k: abs(int(k[-15:-9]) - 120000))
                r = dh.session.get(f"{dh.BUCKET}/{kk}", timeout=120)
            r.raise_for_status(); f.write_bytes(r.content)
        la, lo, m = dh.decode(f.read_bytes())
        rr = np.clip(np.round((la[0] - lat) / (la[0] - la[1])).astype(int), 0, len(la) - 1)
        cc = np.clip(np.round((lon - lo[0]) / (lo[1] - lo[0])).astype(int), 0, len(lo) - 1)
        return fd, np.minimum(m[rr, cc] / 25.4, 6.0).astype(np.float32)
    hmax = np.zeros(len(b), np.float32); hday = np.full(len(b), -1, np.int16); n075 = np.zeros(len(b), np.int16)
    days = []
    with ThreadPoolExecutor(6) as tp:
        for fd, v in tp.map(one, fdays):
            if v is None:
                continue
            storm = (fd - timedelta(days=1)).isoformat()
            k = len(days); days.append({"date": storm, "max_in": round(float(v.max()), 2),
                                        "buildings_ge_0_75": int((v >= 0.75).sum())})
            up = v > hmax
            hmax[up] = v[up]; hday[up] = k
            n075 += (v >= 0.75)
    pd.DataFrame({"hail_max_in": hmax, "hail_day": hday, "hail_days_ge_0_75": n075}).to_parquet(HPQ)
    (CACHE / "hail_meta.json").write_text(json.dumps({"from": start.isoformat(), "to": date.today().isoformat(),
                                                      "spc_km": A.hail_spc_km, "days": days}))
    log(f"hail: {len(days)} days decoded; {(hmax >= 0.75).sum():,} buildings saw >= 0.75 in, max {hmax.max():.2f} in")


# ------------------------------------------------------------------------------------------------ 5 LST
LPQ = CACHE / "lst.parquet"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".TIF,.tif",
                GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="3", VSI_CACHE="TRUE", GDAL_CACHEMAX=1024)


def lst_items():
    cp = CACHE / "lst_search.json"
    if cp.exists() and "lst" not in FORCE:
        return json.loads(cp.read_text())
    import pystac_client
    years = [int(y) for y in A.lst_years.split(",")] if A.lst_years else [NOW.year - k for k in range(3)]
    c = pystac_client.Client.open(PC_STAC)
    out = []
    for y in years:
        for it in c.search(collections=["landsat-c2-l2"], bbox=BBOX, datetime=f"{y}-06-01/{y}-08-31",
                           query={"platform": {"in": ["landsat-8", "landsat-9"]}, "eo:cloud_cover": {"lt": 20}}).items():
            p = it.properties
            if not all(k in it.assets for k in ("lwir11", "qa_pixel")):
                continue
            if (p.get("landsat:collection_category") or "T1") != "T1":
                continue
            out.append({"id": it.id, "dt": it.datetime.isoformat(), "platform": p.get("platform"),
                        "cloud": p.get("eo:cloud_cover"), "path": p.get("landsat:wrs_path"),
                        "row": p.get("landsat:wrs_row"), "bbox": list(it.bbox),
                        "lwir": it.assets["lwir11"].href, "qa_pixel": it.assets["qa_pixel"].href})
    cp.write_text(json.dumps(out))
    return out


def st_lst():
    import planetary_computer
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window
    b = pd.read_parquet(BPQ, columns=["lon", "lat"])
    lon, lat = b.lon.values, b.lat.values
    items = lst_items()
    log(f"lst: {len(items)} candidate summer scenes (cloud < 20%)")
    # tiers: newest summer first, near-cloudless first. Inside a tier, greedy: the scene covering the most
    # still-unassigned buildings, with a bonus for an overpass date already used (rows of one pass = no seam).
    tier = lambda i: (-int(i["dt"][:4]), 0 if (i["cloud"] or 0) <= 2 else 1)
    pool = sorted(items, key=tier)
    val = np.full(len(b), np.nan, np.float32); scn = np.full(len(b), -1, np.int16)
    used, tried = [], set()
    while len(used) < A.lst_max_scenes and not np.isfinite(val).all():
        todo = ~np.isfinite(val)
        best = None
        for it in pool:
            if it["id"] in tried:
                continue
            if best is not None and tier(it) != tier(best[1]):
                break
            w, s, e, n = it["bbox"]
            cnt = int((todo & (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)).sum())
            if cnt < 200:
                continue
            sc = cnt * (1.25 if it["dt"][:10] in {u["datetime_utc"][:10] for u in used} else 1.0)
            if best is None or sc > best[0]:
                best = (sc, it)
        if best is None:
            break
        it = best[1]; tried.add(it["id"])
        w, s, e, n = it["bbox"]
        cand = np.where(todo & (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n))[0]
        t0 = time.time()
        try:
            with rasterio.Env(**GDAL_ENV):
                with rasterio.open(planetary_computer.sign(it["qa_pixel"])) as ds:
                    tr = Transformer.from_crs(4326, ds.crs, always_xy=True)
                    x, y = tr.transform(lon[cand], lat[cand])
                    r_, c_ = rasterio.transform.rowcol(ds.transform, x, y)
                    r_, c_ = np.asarray(r_), np.asarray(c_)
                    ok = (r_ >= 0) & (r_ < ds.height) & (c_ >= 0) & (c_ < ds.width)
                    cand, r_, c_ = cand[ok], r_[ok], c_[ok]
                    if len(cand) < 200:
                        continue
                    r0, r1, c0, c1 = int(r_.min()), int(r_.max()) + 1, int(c_.min()), int(c_.max()) + 1
                    win = Window(c0, r0, c1 - c0, r1 - r0)
                    qp = ds.read(1, window=win)
                with rasterio.open(planetary_computer.sign(it["lwir"])) as ds:
                    st = ds.read(1, window=win)
        except Exception as ex:  # noqa: BLE001
            log(f"  ! {it['id']}: {ex}")
            continue
        q = qp[r_ - r0, c_ - c0]; v = st[r_ - r0, c_ - c0].astype(np.float32)
        clear = ((q & 0b11111) == 0) & (v > 0)
        if clear.sum() < 200:
            log(f"  {it['id']}: no clear buildings")
            continue
        k = len(used)
        degc = v * 0.00341802 + 149.0 - 273.15
        val[cand[clear]] = degc[clear]; scn[cand[clear]] = k
        used.append({"id": it["id"], "datetime_utc": it["dt"], "platform": it["platform"], "cloud_pct": it["cloud"],
                     "wrs": f"{it['path']}/{it['row']}", "buildings": int(clear.sum()),
                     "median_c": round(float(np.median(degc[clear])), 1)})
        log(f"  {it['id']} {it['dt'][:10]} cloud {it['cloud']}: +{clear.sum():,} buildings "
            f"({np.isfinite(val).mean() * 100:.1f}% done, {time.time() - t0:.0f}s)")
    pd.DataFrame({"lst_c": val, "lst_scene": scn}).to_parquet(LPQ)
    (CACHE / "lst_meta.json").write_text(json.dumps({"scenes": used}))
    log(f"lst: {np.isfinite(val).sum():,}/{len(val):,} buildings with a clear summer pixel from {len(used)} scenes")


# ------------------------------------------------------------------------------------------------ 6 tiles
SOURCES = {
    "overture": {"name": "Overture Maps Foundation buildings theme", "url": "https://docs.overturemaps.org/attribution/",
                 "licence": "ODbL 1.0 (Open Database License); outputs derived from it are share-alike under ODbL",
                 "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/", "commercial_ok": "yes (share-alike)",
                 "attribution": "© OpenStreetMap contributors, Overture Maps Foundation"},
    "counties": COUNTY_LIC,
    "tracts": {"name": "web/usa/data/tracts/<ST>.json (Census 2020 tracts, ACS 2020-2024 5-yr, CDFI OZ 2018 & 2027, "
                       "HUD QCT 2026, CDFI NMTC, FEMA NRI, FHFA tract HPI) - see that file's meta.sources",
               "licence": "US federal government works, public domain (17 U.S.C. 105); HUD/FHFA not endorsing",
               "commercial_ok": "yes"},
    "nfhl": {"name": "FEMA National Flood Hazard Layer county packages (S_FLD_HAZ_AR), FEMA Map Service Center",
             "url": "https://msc.fema.gov/portal/advanceSearch", "licence": "US federal government work, public domain "
             "(17 U.S.C. 105); FEMA: not a substitute for an official FIRM determination", "commercial_ok": "yes"},
    "mrms": {"name": "NOAA MRMS MESH_Max_1440min (radar max estimated hail size, ~1 km), NOAA Open Data on AWS",
             "url": "https://registry.opendata.aws/noaa-mrms-pds/", "licence": "NOAA data, public domain; attribution "
             "requested", "commercial_ok": "yes"},
    "spc": {"name": "NOAA SPC storm reports (hail) - used only to choose which MRMS days to read",
            "url": "https://www.spc.noaa.gov/wcm/", "licence": "US federal government work, public domain",
            "commercial_ok": "yes"},
    "landsat": {"name": "USGS Landsat 8/9 Collection 2 Level-2 Surface Temperature (ST_B10), via Microsoft Planetary "
                        "Computer STAC (landsat-c2-l2)",
                "url": "https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature",
                "licence": "US Government public domain: 'There are no restrictions on the use of Landsat products.'",
                "citation": "EROS (2020) Landsat 8-9 OLI/TIRS Level-2, Collection 2. USGS. doi:10.5066/P9OGBGM6",
                "commercial_ok": "yes"},
    "weather_triggers": {"name": "web/usa/data/weather_triggers.json (NOAA NCEI Storm Events, NWS warnings via IEM, "
                                 "EPA AQS PM10, NOAA GHCN-Daily)",
                         "licence": "public domain (NOAA/NWS/EPA; IEM: 'may be used freely by anyone for any lawful "
                                    "purpose')", "commercial_ok": "yes"},
}


class Dict:
    def __init__(self, first=None):
        self.vals, self.idx = [], {}
        if first is not None:
            self.code(first)

    def code(self, v):
        if v not in self.idx:
            self.idx[v] = len(self.vals); self.vals.append(v)
        return self.idx[v]

    def codes(self, arr):
        return np.fromiter((self.code(v) for v in arr), np.int32, len(arr))


def col(a):
    """list, or a scalar when every value in the tile is the same (client: Array.isArray(c) ? c[i] : c)."""
    a = np.asarray(a)
    if len(a) and (a == a[0]).all():
        return int(a[0])
    return a.astype(np.int64).tolist()


def st_tiles():
    b = pd.read_parquet(BPQ)
    n = len(b)
    parts = {"tracts": TPQ, "flood": FPQ, "hail": HPQ, "lst": LPQ}
    have = {}
    for k, p in parts.items():
        if p.exists():
            x = pd.read_parquet(p)
            assert len(x) == n, f"{p} not aligned with buildings ({len(x)} vs {n}); rerun --force {k}"
            b = pd.concat([b, x], axis=1); have[k] = True
        else:
            log(f"tiles: stage {k} missing - field left out"); have[k] = False
    bmeta = json.loads((CACHE / "buildings_meta.json").read_text())
    # ---- dictionaries
    def cat(series):
        s = series.fillna("").astype(str).values
        return s
    clsD, subD, dsD = Dict(""), Dict(""), Dict("")
    cls = clsD.codes(cat(b["class"])); sub = subD.codes(cat(b.subtype)); ds = dsD.codes(cat(b.dataset))
    area = np.clip(np.round(b.area_m2.values), 0, None).astype(np.int64)
    hdm = np.where(np.isfinite(b.height_m.values), np.round(b.height_m.values * 10), 0).astype(np.int64)
    hs = b.height_src.values.astype(np.int64)
    cols = {"area": area, "h": hdm, "hs": hs, "cls": cls, "sub": sub, "src": ds}
    tract_rows, tract_meta = load_tracts()
    tract_ids = [t["g"] for t in tract_rows]
    tix = {g: i for i, g in enumerate(tract_ids)}
    if have["tracts"]:
        tr_g = np.array([tix.get(g, -1) if g else -1 for g in b.tract.values], np.int64)
    if have["flood"]:
        fzD = Dict("")
        cols["fz"] = fzD.codes(b.flood_zone.fillna("").values)
    if have["hail"]:
        cols["hail"] = np.round(b.hail_max_in.values * 100).astype(np.int64)
        cols["hd"] = b.hail_day.values.astype(np.int64) + 1
    if have["lst"]:
        cols["lst"] = np.where(np.isfinite(b.lst_c.values), np.round(b.lst_c.values * 10), -9999).astype(np.int64)
        cols["lsc"] = b.lst_scene.values.astype(np.int64) + 1
    ctyD = {g: i for i, g in enumerate(CTY.GEOID.values)}
    cols["cty"] = np.array([ctyD[c] for c in b.county.values], np.int64)
    # ---- tiles
    tx = np.floor((b.lon.values + 180) / TILE).astype(np.int64)
    ty = np.floor((b.lat.values + 90) / TILE).astype(np.int64)
    qx = np.round(((b.lon.values + 180) - tx * TILE) / Q).astype(np.int64)
    qy = np.round(((b.lat.values + 90) - ty * TILE) / Q).astype(np.int64)
    key = tx * 100000 + ty
    order = np.lexsort((qx, qy, key))
    key_s = key[order]
    starts = np.r_[0, np.where(np.diff(key_s))[0] + 1]; ends = np.r_[starts[1:], len(order)]
    tdir = OUT / "tiles"; tdir.mkdir(parents=True, exist_ok=True)
    for old in tdir.glob("*.json"):
        old.unlink()
    tiles, total = [], 0
    for s0, s1 in zip(starts, ends):
        ix = order[s0:s1]
        X, Y = int(tx[ix[0]]), int(ty[ix[0]])
        tid = f"{X}_{Y}"
        w, s = round(X * TILE - 180, 6), round(Y * TILE - 90, 6)
        yy = qy[ix]
        obj = {"id": tid, "bbox": [w, s, round(w + TILE, 6), round(s + TILE, 6)], "n": int(len(ix)),
               "x": qx[ix].tolist(), "dy": np.diff(yy, prepend=0).tolist()}
        for k, v in cols.items():
            obj[k] = col(v[ix])
        if have["tracts"]:
            g = tr_g[ix]
            u, inv = np.unique(g, return_inverse=True)
            obj["tracts"] = u.tolist()                      # global tract index (index.json tracts.geoid), -1 none
            obj["tr"] = col(inv)
        p = tdir / f"{tid}.json"
        txt = json.dumps(obj, separators=(",", ":"))
        p.write_text(txt, encoding="utf-8")
        nb = len(txt.encode())
        assert nb <= 4 * 1024 * 1024, f"tile {tid} is {nb} bytes"
        total += nb
        tiles.append({"id": tid, "bbox": obj["bbox"], "count": int(len(ix)), "bytes": nb})
    log(f"tiles: {len(tiles)} tiles, {total / 1e6:.1f} MB, max {max(t['bytes'] for t in tiles) / 1e6:.2f} MB")
    # ---- index
    metric_keys = ["income", "home_value", "year_built", "owner_pct", "sf_pct", "pop", "oz18", "oz27", "qct", "nmtc",
                   "nri", "nri_hail", "nri_wind", "nri_flood", "hpi_1y", "hpi_5y"]
    tracts = {"geoid": tract_ids}
    for k in metric_keys:
        tracts[k] = [t.get("m", {}).get(k) for t in tract_rows]
    wt = json.loads(WEATHER.read_text())
    counties = []
    for i, r in enumerate(CTY.itertuples()):
        c = (wt.get("counties") or {}).get(r.GEOID, {})
        counties.append({"code": i, "fips": r.GEOID, "name": r.NAME, "state": r.STUSPS,
                         "buildings": int((b.county.values == r.GEOID).sum()),
                         "weather_triggers": {k: c.get(k) for k in ("dust_days_yr", "dust_then_rain_yr",
                                              "high_wind_days_yr", "hail_days_yr", "trigger_days_yr",
                                              "monthly_trigger_profile", "last_events", "pm10_monitors")} if c else None})
    coverage = {"buildings": n, "height_any": float((hs > 0).mean()), "height_overture": float((hs == 1).mean()),
                "height_from_floors": float((hs == 2).mean()), "class": float((cls > 0).mean()),
                "subtype": float((sub > 0).mean())}
    if have["tracts"]:
        coverage["tract"] = float((tr_g >= 0).mean())
    if have["flood"]:
        coverage["flood_zone_mapped"] = float((cols["fz"] > 0).mean())
        fzc = pd.Series(b.flood_zone.fillna("").values).replace("", "(outside mapped polygons)").value_counts()
        coverage["flood_zone_counts"] = {k: int(v) for k, v in fzc.items()}
    if have["hail"]:
        coverage["hail_ge_0_75in"] = float((b.hail_max_in.values >= 0.75).mean())
        coverage["hail_any_ge_0_25in"] = float((b.hail_max_in.values >= 0.25).mean())
    if have["lst"]:
        coverage["lst"] = float(np.isfinite(b.lst_c.values).mean())
    hm = json.loads((CACHE / "hail_meta.json").read_text()) if have["hail"] else None
    lm = json.loads((CACHE / "lst_meta.json").read_text()) if have["lst"] else None
    fm = json.loads((CACHE / "flood_meta.json").read_text()) if have["flood"] else None
    fields = {
        "x": "lon = tile.bbox[0] + x * quantum (int, quantum 1e-5 deg); buildings sorted by y then x",
        "dy": "lat = tile.bbox[1] + cumsum(dy) * quantum (delta-coded)",
        "area": "footprint area, m^2 (Overture polygon, EPSG:6933 equal-area)",
        "h": "height in decimetres; 0 = unknown", "hs": "height source: 0 unknown, 1 Overture height, 2 Overture "
        f"num_floors x {FLOOR_M} m",
        "cls": "Overture class, index into dicts.cls ('' = none)", "sub": "Overture subtype, index into dicts.sub",
        "src": "upstream dataset in Overture (OpenStreetMap / Microsoft ML / Google Open Buildings / Esri ...), "
               "index into dicts.src",
        "cty": "county code, index into counties[]",
        "tracts": "per tile: global tract indices into index.tracts (-1 = none)", "tr": "per building: index into tile.tracts",
        "fz": "FEMA NFHL flood zone at the footprint centroid, index into dicts.fz ('' = outside mapped NFHL polygons)",
        "hail": "max MRMS MESH at the building pixel over hail.days, hundredths of an inch",
        "hd": "1-based index into hail.days of that max (0 = none)",
        "lst": "Landsat summer land-surface temperature at the 30 m pixel holding the centroid, degC x 10; -9999 none "
               "(the pixel covers roof + yard + street; not a roof-only reading)",
        "lsc": "1-based index into lst.scenes (0 = none). Scenes differ in date/time: compare against "
               "lst.scenes[k].median_c for a heat anomaly",
        "_scalar": "any per-building column may be a single number when all buildings in the tile share it",
    }
    dicts = {"cls": clsD.vals, "sub": subD.vals, "src": dsD.vals}
    if have["flood"]:
        dicts["fz"] = fzD.vals
    index = {
        "metro": SLUG, "name": A.name or SLUG.replace("-", " ").title(), "generated": NOW.isoformat(timespec="seconds"),
        "counties": counties, "bbox": [round(v, 6) for v in BBOX], "tile_deg": TILE, "quantum_deg": Q,
        "overture_release": bmeta["release"], "count": n, "total_bytes": total,
        "fields": {k: v for k, v in fields.items() if k in ("x", "dy", "_scalar") or k in cols or k in ("tracts", "tr")},
        "dicts": dicts, "tracts": tracts, "tract_fields": tract_meta[STATES[0]]["fields"],
        "hail": hm, "lst": lm, "flood": fm, "coverage": coverage,
        "sources": SOURCES, "licences": {
            "tiles": "ODbL 1.0 (derived database of Overture/OpenStreetMap buildings; share-alike applies)",
            "attributes": "public domain US federal data (Census, CDFI, HUD, FEMA, NOAA, USGS, EPA)"},
        "attribution": "© OpenStreetMap contributors, Overture Maps Foundation (ODbL). Attributes: U.S. Census "
                       "Bureau, U.S. Treasury CDFI Fund, HUD, FEMA (NFHL, NRI), FHFA, NOAA (MRMS, SPC, NCEI), USGS "
                       "Landsat, EPA AQS, Iowa Environmental Mesonet.",
        "privacy": "no owner, occupant or personal data",
        "tiles": tiles,
    }
    wjson(OUT / "index.json", index, separators=(",", ":"))
    ib = (OUT / "index.json").stat().st_size
    log(f"index.json {ib / 1e6:.2f} MB; total {(total + ib) / 1e6:.1f} MB")


STEPS = [("buildings", BPQ, st_buildings), ("tracts", TPQ, st_tracts), ("flood", FPQ, st_flood),
         ("hail", HPQ, st_hail), ("lst", LPQ, st_lst), ("tiles", OUT / "index.json", st_tiles)]
for name, out, fn in STEPS:
    if name == "tiles" and name in STAGES:
        t0 = time.time(); fn(); TIMING[name] = round(time.time() - t0, 1)
    elif stage_wanted(name, out):
        t0 = time.time()
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            log(f"FAIL {name}: {type(e).__name__}: {e}")
        TIMING[name] = round(time.time() - t0, 1)
log("timing", TIMING)

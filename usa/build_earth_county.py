"""build_earth_county.py - county "earth" metrics from official public-domain US government data.

Writes
  web/usa/data/earth_county.json          {"counties": {"<fips>": {...}}, "sources": {...}}
  web/usa/data/earth_county_licence.json  one entry per source: name, provider, licence URL, download URL

Fields (all computed per county; null when the source has no value for that county)
  radon_zone      EPA Map of Radon Zones (1 = highest predicted indoor radon, 3 = lowest)
  nure_k          mean potassium, % (USGS Bayesian NURE gamma-ray grid, 2 km, conterminous US only)
  nure_eu         mean equivalent uranium, ppm (same release)
  nure_eth        mean equivalent thorium, ppm (same release)
  pm25_dv         PM2.5 annual design value 2023-2025, ug/m3 (EPA AQS, county = highest valid site)
  pm25_24h_dv     PM2.5 24-hour design value 2023-2025, ug/m3
  o3_dv           ozone 8-hour design value 2023-2025, ppb

Skipped on purpose: USGS Vs30 (the only national grid is a single 631 MB file, over the 500 MB cap),
FEMA SFHA share (no national county summary; the full NFHL is far too large), USGS aeromagnetic.

Downloads go to usa/cache/earth/ (git-ignored) and are reused on re-runs. Re-runnable; prints coverage.
Needs: numpy, rasterio, shapely, pyproj, openpyxl, xlrd (for EPA's legacy .xls).
"""
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "cache" / "earth"
DATA = ROOT / "web" / "usa" / "data"
OUT = DATA / "earth_county.json"
LIC = DATA / "earth_county_licence.json"
UA = {"User-Agent": "Mozilla/5.0 (open-data county build)"}
MAX_BYTES = 500 * 1024 * 1024

SB_ITEM = "5dd2f264e4b0695797628351"
SB_FACET = "https://www.sciencebase.gov/catalog/file/get/%s?facet=%%s" % SB_ITEM
RADON_URL = "https://www.epa.gov/system/files/documents/2024-05/radon_zones-spreadsheet.xls"
O3_URL = "https://www.epa.gov/system/files/documents/2026-06/o3_designvalues_2023_2025_final_06_08_26.xlsx"
PM_URL = "https://www.epa.gov/system/files/documents/2026-06/pm25_designvalues_2023_2025_final_06_12_26.xlsx"
NURE = {"nure_k": "K_Y_mean_per", "nure_eu": "eU_Y_mean_ppm", "nure_eth": "eTh_Y_mean_ppm"}

SOURCES = {
    "epa_radon_zones": {
        "name": "EPA Map of Radon Zones (county table, EPA 402-R-93-071; table updated 2025-09)",
        "provider": "U.S. Environmental Protection Agency",
        "licence": "US public domain (EPA Standard Open Data License)",
        "licence_url": "https://edg.epa.gov/EPA_Data_License.html",
        "page_url": "https://www.epa.gov/radon/epa-map-radon-zones",
        "download_url": RADON_URL,
        "fields": ["radon_zone"],
        "method": "matched by county name and state (the table has no FIPS); renamed or split units since 1993 take the zone of the unit they came from; Connecticut planning regions and Puerto Rico have no zone",
    },
    "usgs_nure_gamma": {
        "name": "Bayesian modeling of NURE airborne radiometric data for the conterminous United States: predictions and grids (Goldman & Ellefsen 2020, doi:10.5066/P9YEAFHI)",
        "provider": "U.S. Geological Survey",
        "licence": "US public domain (USGS data release, listed CC0 on data.usgs.gov)",
        "licence_url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
        "licence_url_item": "https://data.usgs.gov/datacatalog/data/USGS:5dd2f264e4b0695797628351",
        "page_url": "https://www.sciencebase.gov/catalog/item/%s" % SB_ITEM,
        "download_url": [SB_FACET % f for f in NURE.values()],
        "fields": list(NURE),
        "method": "area mean of 2 km EPSG:5070 grid cells whose centres fall in the county (all touched cells if none); conterminous US only",
    },
    "epa_aqs_design_values": {
        "name": "EPA Air Quality Design Values 2023-2025 (ozone Table 4, PM2.5 Tables 4a/4b county level), from AQS",
        "provider": "U.S. Environmental Protection Agency",
        "licence": "US public domain (EPA Standard Open Data License)",
        "licence_url": "https://edg.epa.gov/EPA_Data_License.html",
        "page_url": "https://www.epa.gov/air-trends/air-quality-design-values",
        "download_url": [O3_URL, PM_URL],
        "fields": ["o3_dv", "pm25_dv", "pm25_24h_dv"],
        "method": "EPA's county design value (highest valid monitor in the county); Connecticut planning regions rebuilt from EPA's site tables by site location; ozone converted ppm to ppb",
    },
}


def fetch(url, name):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists() and p.stat().st_size > 0:
        return p
    print("  download", url)
    with requests.get(url, headers=UA, stream=True, timeout=300) as r:
        r.raise_for_status()
        n = int(r.headers.get("Content-Length") or 0)
        if n > MAX_BYTES:
            raise RuntimeError("skip %s: %d bytes > cap" % (url, n))
        tmp = p.with_suffix(p.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(p)
    return p


def load_counties():
    d = json.loads((DATA / "counties.json").read_text(encoding="utf-8"))
    c = d["cols"]
    i, n, s = c.index("fips"), c.index("NAME"), c.index("STUSPS")
    return [(r[i], r[n], r[s]) for r in d["rows"]]


# ---------------- EPA radon zones (matched by name + state: the table has no FIPS)
# renamed / split county-equivalents since the 1993 map: new FIPS -> (state, name in EPA's table).
# A split unit inherits the zone of the unit it was carved from (same ground).
RADON_ALIAS = {
    "46102": "Shannon",            # Oglala Lakota SD, renamed 2015
    "02158": "Wade Hampton",       # Kusilvak AK, renamed 2015
    "02063": "Valdez-Cordova", "02066": "Valdez-Cordova",   # Chugach, Copper River (2019 split)
    "02105": "Skagway-Hoonah-Angoon", "02230": "Skagway-Hoonah-Angoon",
    "02195": "Wrangell-Petersburg", "02275": "Wrangell-Petersburg",
    "02198": "Prince of Wales-Outer Ketchikan",
}


def norm(name):
    import unicodedata
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("&", "and")
    t = re.sub(r"^\s*\.", "", t)
    t = re.sub(r"\b(saint|ste?\.?)\s", "st ", t)
    t = re.sub(r"\b(county|parish|borough|census area|city and borough|municipality|municipio)\b", "", t)
    return re.sub(r"[^a-z]", "", t)


def radon(counties):
    import xlrd
    p = fetch(RADON_URL, "radon_zones.xls")
    sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
    county_tab, city_tab = {}, {}
    for i in range(1, sh.nrows):
        cs, label, _st, _reg, zone = sh.row_values(i)[:5]
        if not isinstance(zone, float):
            continue
        if str(cs).strip() == "District of Columbia":
            cs = "District of Columbia, DC"
        if "," not in str(cs):
            continue
        nm, st = [x.strip() for x in str(cs).rsplit(",", 1)]
        is_city = bool(re.search(r"\bcity\s*$", str(label).strip(), re.I)) and not re.search(r"county", str(label), re.I)
        key = (st.upper(), norm(re.sub(r"\s+city\s*$", "", nm, flags=re.I) if is_city else nm))
        (city_tab if is_city else county_tab)[key] = int(zone)
    out, miss = {}, []
    for f, nm, st in counties:
        k = (st, norm(RADON_ALIAS.get(f, nm)))
        indep_city = int(f[2:]) >= 510 and st in ("VA", "MO", "MD", "NV")
        z = (city_tab.get(k) or county_tab.get(k)) if indep_city else (county_tab.get(k) or city_tab.get(k))
        if z is None:  # e.g. 'Carson City' listed as county, 'DeKalb' vs 'De Kalb'
            z = county_tab.get((st, norm(nm.replace(" City", "")))) or city_tab.get((st, norm(nm + " city")))
        if z is None:
            miss.append(f"{f} {nm}, {st}")
        out[f] = z
    if miss:
        # Connecticut's 2022 planning regions and Puerto Rico have no zone in EPA's table
        print("  radon: %d unmatched (e.g. %s)" % (len(miss), "; ".join(miss[:12])))
    return out


# ---------------- USGS NURE gamma grids: county area means
def nure(counties):
    import rasterio
    from rasterio.features import rasterize
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely import transform as shp_transform

    geo = json.loads((DATA / "counties.geojson").read_text(encoding="utf-8"))
    tr = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    idx = {f: i + 1 for i, (f, _, _) in enumerate(counties)}
    shapes = []
    for ft in geo["features"]:
        f = ft["properties"].get("f")
        if f in idx and not f.startswith(("02", "15", "72")):
            shapes.append((shp_transform(shape(ft["geometry"]), lambda xy: np.column_stack(tr(xy[:, 0], xy[:, 1]))), idx[f]))
    out = {k: {} for k in NURE}
    zones = None
    for key, facet in NURE.items():
        z = fetch(SB_FACET % facet, "nure_%s.zip" % facet)
        tif = CACHE / "nure" / (facet + ".tif")
        if not tif.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extract(facet + ".tif", CACHE / "nure")
        with rasterio.open(tif) as r:
            a = r.read(1).astype("float64")
            if zones is None:
                zones = rasterize(shapes, out_shape=a.shape, transform=r.transform, fill=0, dtype="int32")
                got = set(np.unique(zones)) - {0}
                small = [s for s in shapes if s[1] not in got]
                if small:  # counties smaller than a grid cell: take every touched cell
                    zt = rasterize(small, out_shape=a.shape, transform=r.transform, fill=0, dtype="int32", all_touched=True)
                    zones_small = zt
                else:
                    zones_small = None
            ok = np.isfinite(a) & (a > -1e30)
            if r.nodata is not None:
                ok &= a != r.nodata
            n = len(counties) + 1
            for zz in [zones] + ([zones_small] if zones_small is not None else []):
                m = ok & (zz > 0)
                s = np.bincount(zz[m], weights=a[m], minlength=n)
                c = np.bincount(zz[m], minlength=n)
                for f, i in idx.items():
                    if c[i] and f not in out[key]:
                        out[key][f] = s[i] / c[i]
    return out


# ---------------- EPA AQS design values (county tables)
def dv_table(path, sheet_prefix, scale=1.0):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = next(w for w in wb.worksheets if w.title.startswith(sheet_prefix))
    out = {}
    for row in ws.iter_rows(values_only=True):
        st, co, v = row[2], row[3], row[6]
        if st is None or co is None or not re.fullmatch(r"\d{1,2}", str(st).strip()) or not re.fullmatch(r"\d{1,3}", str(co).strip()):
            continue
        if isinstance(v, (int, float)):
            f = str(st).strip().zfill(2) + str(co).strip().zfill(3)
            out[f] = max(out.get(f, -1e9), float(v) * scale)
    return out


def dv_sites_ct(path, sheet_prefix, scale=1.0):
    """Connecticut only: EPA's county tables use the old CT counties, counties.json uses the 2022
    planning regions. Rebuild the county value from the site table (highest valid site DV inside each region)."""
    import openpyxl
    from shapely.geometry import Point, shape
    geo = json.loads((DATA / "counties.geojson").read_text(encoding="utf-8"))
    regs = [(ft["properties"]["f"], shape(ft["geometry"])) for ft in geo["features"] if ft["properties"].get("f", "").startswith("09")]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = next(w for w in wb.worksheets if w.title.startswith(sheet_prefix))
    hdr, out = None, {}
    for row in ws.iter_rows(values_only=True):
        if hdr is None:
            if row and "Site Latitude" in [str(c).strip() if c else "" for c in row]:
                h = [str(c).strip() if c else "" for c in row]
                hdr = (h.index("Site Latitude"), h.index("Site Longitude"), next(i for i, c in enumerate(h) if c.startswith("Valid")))
            continue
        if row[0] != "Connecticut":
            continue
        la, lo, v = row[hdr[0]], row[hdr[1]], row[hdr[2]]
        if not all(isinstance(x, (int, float)) for x in (la, lo, v)):
            continue
        pt = Point(lo, la)
        for f, g in regs:
            if g.contains(pt):
                out[f] = max(out.get(f, -1e9), float(v) * scale)
                break
    return out


def rnd(v, d):
    if v is None or not np.isfinite(v):
        return None
    return int(round(v)) if d == 0 else round(float(v), d)


def main():
    counties = load_counties()
    fips = [f for f, _, _ in counties]
    print("counties:", len(fips))
    rows = {f: {} for f in fips}
    produced = {}

    print("EPA radon zones")
    try:
        rz = radon(counties)
        for f in fips:
            rows[f]["radon_zone"] = rz.get(f)
        produced["epa_radon_zones"] = True
    except Exception as e:
        print("  radon failed:", e)

    print("USGS NURE gamma grids")
    try:
        g = nure(counties)
        for k, m in g.items():
            for f in fips:
                rows[f][k] = rnd(m.get(f), 2)
        produced["usgs_nure_gamma"] = True
    except Exception as e:
        print("  NURE failed:", e)

    print("EPA AQS design values")
    try:
        o3 = dv_table(fetch(O3_URL, "o3_dv.xlsx"), "Table4", 1000.0)
        pmp = fetch(PM_URL, "pm25_dv.xlsx")
        pm = dv_table(pmp, "Table4a")
        pm24 = dv_table(pmp, "Table4b")
        o3p = CACHE / "o3_dv.xlsx"
        o3.update(dv_sites_ct(o3p, "Table5", 1000.0))
        pm.update(dv_sites_ct(pmp, "Table5a"))
        pm24.update(dv_sites_ct(pmp, "Table5b"))
        unknown = (set(o3) | set(pm) | set(pm24)) - set(fips)
        if unknown and all(u.startswith("09") for u in unknown):
            print("  AQS: old Connecticut county FIPS dropped (regions rebuilt from sites):", len(unknown))
        elif unknown:
            print("  AQS FIPS not in counties.json (dropped):", sorted(unknown)[:12])
        for f in fips:
            rows[f]["o3_dv"] = rnd(o3.get(f), 0)
            rows[f]["pm25_dv"] = rnd(pm.get(f), 1)
            rows[f]["pm25_24h_dv"] = rnd(pm24.get(f), 0)
        produced["epa_aqs_design_values"] = True
    except Exception as e:
        print("  AQS failed:", e)

    fields = sorted({k for r in rows.values() for k in r})
    cov = {k: sum(1 for r in rows.values() if r.get(k) is not None) for k in fields}
    srcs = {k: {kk: v[kk] for kk in ("name", "provider", "licence", "fields")} | {"coverage": {fl: cov.get(fl, 0) for fl in v["fields"]}}
            for k, v in SOURCES.items() if produced.get(k)}
    units = {"radon_zone": "1-3 (1 highest)", "nure_k": "%", "nure_eu": "ppm", "nure_eth": "ppm",
             "pm25_dv": "ug/m3 (2023-2025 annual DV)", "pm25_24h_dv": "ug/m3 (2023-2025 24-hr DV)", "o3_dv": "ppb (2023-2025 8-hr DV)"}
    doc = {"counties": rows, "sources": srcs, "units": {k: units[k] for k in fields if k in units}}
    OUT.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    LIC.write_text(json.dumps({k: v for k, v in SOURCES.items() if produced.get(k)}, indent=1), encoding="utf-8")
    print("wrote", OUT, "%.0f KB" % (OUT.stat().st_size / 1024))
    print("coverage (of %d counties):" % len(fips))
    for k in fields:
        print("  %-12s %5d" % (k, cov[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

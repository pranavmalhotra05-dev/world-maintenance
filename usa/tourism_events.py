"""Events, calendar and connectivity signals for a short-term-rental demand index (places and dates only).

Sources (all permanently free + commercial use allowed; licence quotes in usa/research/tourism_events.md):
  venues        OpenStreetMap from the local Geofabrik north-america/us extract via usa/osm_extract.py (ODbL 1.0:
                attribution + share-alike on the derived DB; the public Overpass/QLever endpoints are not used)
                Wikidata (CC0) - capacity gap-fill
                NCES IPEDS HD2024 + DRVEF2023 (US federal work, public domain) - universities + enrollment
                US Dept of Education EADA 2024-25 (federal work) - which universities field football (FBS/FCS)
  calendar      5 U.S.C. 6103 + E.O. 11582 rules (verified against OPM's 2027/2028 holiday tables)
                recurring events: fixed public dates from Wikipedia (CC BY-SA 4.0; dates are facts)
  connectivity  FTA National Transit Database (data.transportation.gov, "Public Domain U.S. Government")
                BTS NTAD Amtrak Stations, Intermodal Passenger Connectivity DB (US gov work, public domain)
                BTS NTAD National Transit Map Stops (CC BY 3.0 US)
                EPA National Walkability Index (Smart Location Database v3 derivative, federal work)
                Census PEP Vintage 2025 county population, 2020 centres of population, 2020 UA-county
                relationship + 2020 county urban/rural table (public domain)
  county shapes web/usa/data/counties.geojson (existing project layer, Census cartographic boundaries)

Output: web/usa/data/tourism_events.json      Cache: usa/_cache/tourism/
Run:    .venv\\Scripts\\python -u -W ignore usa/tourism_events.py
"""
import datetime as dt
import io
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "tourism"
OUT = ROOT / "web" / "usa" / "data" / "tourism_events.json"
CACHE.mkdir(parents=True, exist_ok=True)
UA = "roofprint-usa/0.1 (open-data research; python-requests)"
S = requests.Session()
S.headers["User-Agent"] = UA
BTS = "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/{}/FeatureServer/0/query"
EPA_WALK = "https://geodata.epa.gov/arcgis/rest/services/OA/WalkabilityIndex/MapServer/0/query"
SODA = "https://data.transportation.gov/resource/{}.json"
BIG_CAP = 10000  # "big venue" threshold (seats) for big_venue_capacity_sum


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(url, tries=5, **kw):
    kw.setdefault("timeout", 180)
    last = None
    for i in range(tries):
        try:
            r = S.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {url}")
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise last


def cached_file(name, url, **kw):
    p = CACHE / name
    if not p.exists() or p.stat().st_size == 0:
        log("download", url)
        r = get(url, **kw)
        p.write_bytes(r.content)
    return p


def cached_json(name, fn):
    p = CACHE / name
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    d = fn()
    p.write_text(json.dumps(d), "utf-8")
    return d


# ------------------------------------------------------------------ ArcGIS / Socrata paging
def arcgis_all(url, fields, where="1=1", page=2000, geom=False):
    """Keyset paging on OBJECTID (large offsets are refused by some hosted layers)."""
    out, lastid, n = [], -1, 0
    if "OBJECTID" not in fields:
        fields = "OBJECTID," + fields
    while True:
        js = None
        for i in range(5):
            js = get(url, params={"where": f"({where}) AND OBJECTID>{lastid}", "outFields": fields, "returnGeometry": str(geom).lower(),
                                  "resultRecordCount": page, "orderByFields": "OBJECTID", "f": "json"}).json()
            if "error" not in js:
                break
            time.sleep(10 * (i + 1))
        if "error" in js:
            raise RuntimeError(js["error"])
        feats = js.get("features", [])
        out += [f["attributes"] | ({"_x": f["geometry"]["x"], "_y": f["geometry"]["y"]} if geom and f.get("geometry") else {})
                for f in feats]
        n += 1
        if n % 25 == 0:
            log(f"  {url.split('/')[-4] if 'FeatureServer' in url else 'walk'} {len(out)}")
        if not feats:
            break
        lastid = max(f["attributes"]["OBJECTID"] for f in feats)
    return out


def soda_all(ds, where=None, select=None):
    out, off = [], 0
    while True:
        p = {"$limit": 50000, "$offset": off}
        if where:
            p["$where"] = where
        if select:  # the "View" datasets compute ratios that can divide by zero unless only plain columns are selected
            p["$select"] = select
        rows = get(SODA.format(ds), params=p).json()
        out += rows
        if len(rows) < 50000:
            return out
        off += 50000


# ------------------------------------------------------------------ counties
def load_counties():
    g = gpd.read_file(ROOT / "web" / "usa" / "data" / "counties.geojson")[["f", "geometry"]]
    g = g.set_crs(4326, allow_override=True)
    return g


def assign_fips(df, counties, lon="lon", lat="lat"):
    if df.empty:
        df["f"] = []
        return df
    pts = gpd.GeoDataFrame(df.copy(), geometry=gpd.points_from_xy(df[lon], df[lat]), crs=4326)
    j = gpd.sjoin(pts, counties, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    df = df.copy()
    df["f"] = j["f"].values
    return df


def county_pop():
    p = cached_file("co-est2025-alldata.csv",
                    "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv")
    d = pd.read_csv(p, dtype=str, encoding="latin-1")
    d = d[d["SUMLEV"] == "050"]
    col = "POPESTIMATE2025" if "POPESTIMATE2025" in d else [c for c in d if c.startswith("POPESTIMATE")][-1]
    return dict(zip(d["STATE"] + d["COUNTY"], d[col].astype(int))), col


def county_centres(counties):
    p = cached_file("CenPop2020_Mean_CO.txt", "https://www2.census.gov/geo/docs/reference/cenpop2020/county/CenPop2020_Mean_CO.txt")
    d = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
    cen = {r.STATEFP + r.COUNTYFP: (float(r.LONGITUDE), float(r.LATITUDE)) for r in d.itertuples()}
    rp = counties.geometry.representative_point()
    for f, pt in zip(counties["f"], rp):
        cen.setdefault(f, (pt.x, pt.y))  # counties missing from 2020 file (CT planning regions etc.)
    return cen


def xyz(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]


def chord_km(d):
    return 2 * 6371.0088 * np.arcsin(np.clip(d / 2, 0, 1))


# ------------------------------------------------------------------ 1. venues (OSM + IPEDS/EADA)
OSM_FILTERS = [
    'nwr["leisure"="stadium"]',
    'nwr["leisure"~"^(sports_centre|sports_hall)$"]["capacity"]',
    'nwr["building"="arena"]',
    'nwr["amenity"~"^(conference_centre|exhibition_centre|convention_centre)$"]',
    'nwr["amenity"~"^(concert_hall|music_venue)$"]',
    'nwr["amenity"="theatre"]["theatre:type"~"amphi|open_air"]',
    'nwr["amenity"="theatre"]["capacity"]',
    'nwr["amenity"="events_venue"]["capacity"]',
    'nwr["leisure"="track"]["sport"~"motor|horse_racing|dog_racing|karting|motocross|speedway|harness"]',
    'nwr["leisure"="sports_centre"]["sport"~"^(motor|horse_racing)$"]',
]
def classify(t, cap):
    am, le, sp = t.get("amenity", ""), t.get("leisure", ""), t.get("sport", "")
    if le == "track" or (le in ("stadium", "sports_centre") and re.search(r"motor|horse_racing|karting|speedway|dog_racing", sp)):
        return "racetrack"
    if le == "stadium":
        return "stadium"
    if am in ("conference_centre", "exhibition_centre", "convention_centre"):
        return "convention"
    if t.get("building") == "arena" or (le in ("sports_centre", "sports_hall") and (cap or 0) >= 2000):
        return "arena"
    if am in ("concert_hall", "music_venue") or re.search(r"amphi|open_air", t.get("theatre:type", "")) or             (am in ("theatre", "events_venue") and (cap or 0) >= 2000):
        return "concert"
    return None


def parse_cap(v):
    if not v:
        return None
    v = v.replace(",", "").replace(" ", "")
    m = re.match(r"^~?(\d{2,6})", v)
    if not m:
        return None
    c = int(m.group(1))
    return c if 50 <= c <= 260000 else None


sys.path.insert(0, str(ROOT / "usa"))
import osm_extract  # noqa: E402

# exact tag pairs (C++ TagFilter in pyosmium) covering OSM_FILTERS; the extra conditions are applied in venue_match()
VENUE_PAIRS = [("leisure", "stadium"), ("leisure", "sports_centre"), ("leisure", "sports_hall"), ("leisure", "track"), ("building", "arena"),
               ("amenity", "conference_centre"), ("amenity", "exhibition_centre"), ("amenity", "convention_centre"), ("amenity", "concert_hall"),
               ("amenity", "music_venue"), ("amenity", "theatre"), ("amenity", "events_venue")]
RACE = re.compile(r"motor|horse_racing|dog_racing|karting|motocross|speedway|harness")


def venue_match(kind, t):
    """Same selection as OSM_FILTERS: named; sports_centre/hall and events_venue need capacity (racing sports_centre excepted);
    tracks need a racing sport; theatres need capacity or amphi/open-air."""
    if not (t.get("name") or t.get("official_name")):
        return False
    le, am, sp = t.get("leisure"), t.get("amenity"), t.get("sport", "")
    cap = t.get("capacity") or t.get("capacity:seats") or t.get("seats")
    if le == "stadium" or t.get("building") == "arena" or am in ("conference_centre", "exhibition_centre", "convention_centre", "concert_hall", "music_venue"):
        return True
    if le == "track":
        return bool(RACE.search(sp))
    if le in ("sports_centre", "sports_hall"):
        return bool(cap) or (le == "sports_centre" and sp in ("motor", "horse_racing"))
    if am == "theatre":
        return bool(cap) or bool(re.search(r"amphi|open_air", t.get("theatre:type", "")))
    if am == "events_venue":
        return bool(cap)
    return False


def osm_venues():
    """OpenStreetMap features (ODbL) read from the Geofabrik north-america/us extract (all 50 states + DC + PR) with pyosmium."""
    feats = osm_extract.query("us", nodes=VENUE_PAIRS, ways=VENUE_PAIRS, rels=VENUE_PAIRS, match=venue_match)
    rows = []
    for f in feats:
        t = f["tags"]
        name = t.get("name") or t.get("official_name")
        cap = parse_cap(t.get("capacity") or t.get("capacity:seats") or t.get("seats"))
        k = classify(t, cap)
        if k is None or not name:
            continue
        rows.append({"n": name.strip(), "k": k, "lon": round(f["lon"], 5), "lat": round(f["lat"], 5), "cap": cap,
                     "osm": f"{f['type']}{f['id']}", "wd": t.get("wikidata")})
    log(f"OSM (Geofabrik us extract): {len(feats)} tagged features, {len(rows)} named venues kept")
    rows = list({r["osm"]: r for r in rows}.values())  # border elements appear in two states
    df = pd.DataFrame(rows)
    # de-duplicate (node + way/relation of one venue): same kind within ~2 km and one normalised name contains the
    # other, or same wikidata id
    df["key"] = df["n"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df = df.sort_values("cap", ascending=False, na_position="last")
    keep, seen = [], []
    for r in df.itertuples():
        dup = any(r.k == k and abs(r.lon - a) < 0.025 and abs(r.lat - b) < 0.02 and (r.key in key or key in r.key or
                  (isinstance(r.wd, str) and r.wd == wd)) for k, key, a, b, wd in seen
                  if abs(r.lon - a) < 0.025)
        if not dup:
            keep.append(r.Index)
            seen.append((r.k, r.key, r.lon, r.lat, r.wd))
    return df.loc[keep].drop(columns=["key"])


WD_KINDS = {"stadium": "Q483110", "arena": "Q641226", "convention": "Q1378975", "racetrack": "Q1777138",
            "concert": "Q1060829", "music_venue": "Q8719053"}


def wikidata_venues():
    """Wikidata (CC0): US venues with coordinates; capacity (P1083) where stated. Used to fill OSM capacity gaps."""
    def fetch(k, q):
        sp = f"""SELECT ?i ?iLabel (SAMPLE(?co) AS ?coord) (MAX(?c) AS ?cap) WHERE {{
          ?i wdt:P31/wdt:P279* wd:{q}; wdt:P17 wd:Q30; wdt:P625 ?co.
          OPTIONAL {{ ?i wdt:P1083 ?c }}
          FILTER NOT EXISTS {{ ?i wdt:P576 ?gone }} FILTER NOT EXISTS {{ ?i wdt:P3999 ?closed }} FILTER NOT EXISTS {{ ?i wdt:P5817 wd:Q56556915 }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} GROUP BY ?i ?iLabel"""
        for i in range(5):
            r = S.get("https://query.wikidata.org/sparql", params={"query": sp, "format": "json"}, timeout=300)
            if r.status_code == 200:
                return r.json()["results"]["bindings"]
            time.sleep(20 * (i + 1))
        r.raise_for_status()
    rows = []
    for k, q in WD_KINDS.items():
        for b in cached_json(f"wd_{k}.json", lambda: fetch(k, q)):
            m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
            if not m:
                continue
            cap = parse_cap(str(int(float(b["cap"]["value"])))) if "cap" in b else None
            kk = "concert" if k == "music_venue" else k
            if k == "music_venue" and (cap or 0) < 1500:
                continue
            name = b["iLabel"]["value"]
            if re.fullmatch(r"Q\d+", name):
                continue
            rows.append({"n": name, "k": kk, "lon": round(float(m.group(1)), 5), "lat": round(float(m.group(2)), 5), "cap": cap,
                         "wd": b["i"]["value"].rsplit("/", 1)[1]})
    df = pd.DataFrame(rows).drop_duplicates("wd")
    log(f"Wikidata venues: {len(df)} ({df['cap'].notna().sum()} with capacity)")
    return df


def merge_osm_wd(osm, wd):
    if osm.empty:
        w = wd[wd["cap"].notna() | wd["k"].isin(["stadium", "arena", "convention", "racetrack"])].drop(columns=["wd"]).copy()
        w["src"] = "wd"
        return w
    """OSM is the base list. Wikidata fills capacity by explicit wikidata tag, else nearest same-kind venue <=1 km with a
    shared name token; unmatched Wikidata venues with capacity are appended (source 'wd')."""
    osm = osm.copy(); osm["src"] = "osm"
    wdi = wd.set_index("wd")
    used = set()
    if "wd" in osm:
        for i, q in osm["wd"].items():
            if isinstance(q, str) and q in wdi.index:
                used.add(q)
                if pd.isna(osm.at[i, "cap"]) and pd.notna(wdi.at[q, "cap"]):
                    osm.at[i, "cap"] = wdi.at[q, "cap"]; osm.at[i, "src"] = "osm+wd"
    rest = wd[~wd["wd"].isin(used)]
    tree = cKDTree(xyz(osm["lon"], osm["lat"]))
    tok = lambda s: set(re.findall(r"[a-z]{4,}", s.lower())) - {"stadium", "arena", "center", "centre", "field", "park", "the", "theatre", "theater", "hall"}
    add = []
    km = 1.0 / 6371
    for r in rest.itertuples():
        q = xyz([r.lon], [r.lat])[0]
        named = [j for j in tree.query_ball_point(q, 1.5 * km) if tok(osm.iloc[j]["n"]) & tok(r.n)]
        if named:  # same venue under a matching name: fill a missing capacity
            j = osm.index[named[0]]
            if pd.isna(osm.at[j, "cap"]) and pd.notna(r.cap):
                osm.at[j, "cap"] = r.cap; osm.at[j, "src"] = "osm+wd"
        elif tree.query_ball_point(q, 0.25 * km):
            continue  # something is mapped at this spot under another (e.g. sponsor) name: do not add a second copy
        elif pd.notna(r.cap):
            add.append({"n": r.n, "k": r.k, "lon": r.lon, "lat": r.lat, "cap": r.cap, "src": "wd"})
    out = pd.concat([osm, pd.DataFrame(add)], ignore_index=True)
    log(f"merge: OSM {len(osm)}, capacity filled from Wikidata {(out['src'] == 'osm+wd').sum()}, Wikidata-only added {len(add)}")
    return out


def universities():
    hdz = cached_file("HD2024.zip", "https://nces.ed.gov/ipeds/datacenter/data/HD2024.zip")
    efz = cached_file("DRVEF2023.zip", "https://nces.ed.gov/ipeds/datacenter/data/DRVEF2023.zip")
    rd = lambda z: pd.read_csv(io.BytesIO(zipfile.ZipFile(z).read([n for n in zipfile.ZipFile(z).namelist() if n.lower().endswith(".csv") and "_rv" not in n.lower()][0])),
                               dtype=str, encoding="latin-1")
    hd, ef = rd(hdz), rd(efz)
    for x in (hd, ef):
        x.columns = x.columns.str.upper().str.replace(r"[^A-Z0-9_]", "", regex=True)
    d = hd.merge(ef[["UNITID", "ENRTOT", "PCTDEEXC"]], on="UNITID", how="left")
    # on-site enrollment = total minus students enrolled exclusively in distance education (drops online-only giants)
    d["ENRTOT"] = (pd.to_numeric(d["ENRTOT"], errors="coerce") * (1 - pd.to_numeric(d["PCTDEEXC"], errors="coerce").fillna(0) / 100)).round()
    d = d[(d["ICLEVEL"] == "1") & (d["ENRTOT"] >= 5000) & (d["CYACTIVE"] == "1")]
    # football: EADA per-sport file
    ez = cached_file("EADA_2024-2025.zip", "https://ope.ed.gov/athletics/api/dataFiles/file?fileName=EADA_2024-2025.zip")
    sch = pd.read_excel(io.BytesIO(zipfile.ZipFile(ez).read("schools.xlsx")), dtype=str)
    sch.columns = sch.columns.str.lower()
    sc = "sports" if "sports" in sch else [c for c in sch if "sport" in c][0]
    fb = sch[sch[sc].str.contains("Football", case=False, na=False)]
    cls = "classification_name" if "classification_name" in fb else None
    fbmap = {}
    for r in fb.itertuples():
        c = getattr(r, cls) if cls else ""
        lab = "FBS" if "FBS" in str(c) else "FCS" if "FCS" in str(c) else "other"
        fbmap[str(r.unitid)] = lab
    out = pd.DataFrame({"n": d["INSTNM"].str.strip(), "k": "university", "lon": pd.to_numeric(d["LONGITUD"], errors="coerce").round(5),
                        "lat": pd.to_numeric(d["LATITUDE"], errors="coerce").round(5), "cap": None,
                        "enr": d["ENRTOT"].astype(int), "fb": d["UNITID"].map(fbmap)})
    log(f"IPEDS universities >=5k enrolled: {len(out)}; with football: {out['fb'].notna().sum()} (FBS {(out['fb'] == 'FBS').sum()})")
    return out.dropna(subset=["lon", "lat"])


# ------------------------------------------------------------------ 2. calendar
def observed(d):
    return d - dt.timedelta(1) if d.weekday() == 5 else d + dt.timedelta(1) if d.weekday() == 6 else d


def nth(y, m, wd, n):
    d = dt.date(y, m, 1)
    d += dt.timedelta((wd - d.weekday()) % 7)
    return d + dt.timedelta(7 * (n - 1))


def last(y, m, wd):
    d = dt.date(y, m + 1, 1) - dt.timedelta(1)
    return d - dt.timedelta((d.weekday() - wd) % 7)


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    return dt.date(y, mo, (h + l - 7 * m + 114) % 31 + 1)


def holidays(years=(2026, 2027, 2028)):
    out = []
    for y in years:
        hs = [("New Year's Day", observed(dt.date(y, 1, 1))), ("Birthday of Martin Luther King, Jr.", nth(y, 1, 0, 3)),
              ("Washington's Birthday", nth(y, 2, 0, 3)), ("Memorial Day", last(y, 5, 0)),
              ("Juneteenth National Independence Day", observed(dt.date(y, 6, 19))), ("Independence Day", observed(dt.date(y, 7, 4))),
              ("Labor Day", nth(y, 9, 0, 1)), ("Columbus Day", nth(y, 10, 0, 2)), ("Veterans Day", observed(dt.date(y, 11, 11))),
              ("Thanksgiving Day", nth(y, 11, 3, 4)), ("Christmas Day", observed(dt.date(y, 12, 25)))]
        for name, d in hs:
            wd = d.weekday()
            if wd == 0:
                lw = {"days": 3, "start": str(d - dt.timedelta(2)), "end": str(d)}
            elif wd == 4:
                lw = {"days": 3, "start": str(d), "end": str(d + dt.timedelta(2))}
            elif wd == 3 and name.startswith("Thanksgiving"):
                lw = {"days": 4, "start": str(d), "end": str(d + dt.timedelta(3)), "note": "Fri after Thanksgiving widely taken off (not a federal holiday)"}
            else:
                lw = None
            out.append({"date": str(d), "name": name, "year": y, "long_weekend": lw})
    # OPM cross-check (2027/2028 tables read from opm.gov 2026-11-24)
    opm = {"2027": ["2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-10-11",
                    "2027-11-11", "2027-11-25", "2027-12-24"],
           "2028": ["2027-12-31", "2028-01-17", "2028-02-21", "2028-05-29", "2028-06-19", "2028-07-04", "2028-09-04", "2028-10-09",
                    "2028-11-10", "2028-11-23", "2028-12-25"]}
    for y, ds in opm.items():
        mine = [h["date"] for h in out if h["year"] == int(y)]
        assert mine == ds, (y, mine, ds)
    return out


def break_proxies(years=(2026, 2027, 2028)):
    """Heuristic school-break windows (no open federal school-calendar source exists; labelled as proxies)."""
    out = []
    for y in years:
        tg = nth(y, 11, 3, 4)
        out.append({"name": "Thanksgiving week (proxy)", "start": str(tg - dt.timedelta(3)), "end": str(tg + dt.timedelta(3))})
        out.append({"name": "Winter break (proxy)", "start": str(dt.date(y, 12, 20)), "end": str(dt.date(y + 1, 1, 2))})
        e = easter(y)
        out.append({"name": "Spring-break season (proxy: first Sat of March to Easter Monday, at least to Mar 31)",
                    "start": str(nth(y, 3, 5, 1)), "end": str(max(e + dt.timedelta(1), dt.date(y, 3, 31)))})
        out.append({"name": "Summer vacation (proxy, Memorial Day to Labor Day)", "start": str(last(y, 5, 0)), "end": str(nth(y, 9, 0, 1))})
    return out


WP = "Wikipedia (CC BY-SA 4.0), article '{}' (accessed 2026-11-24)"
# Only items verified on the cited article; city/state -> county FIPS of the venue.
EVENTS = [
    # name, start, end, venue, city, state, county fips, article, basis
    ("College Football Playoff National Championship", "2027-01-25", "2027-01-25", "Allegiant Stadium", "Paradise", "NV", "32003",
     "College Football Playoff National Championship", "stated"),
    ("Super Bowl LXI", "2027-02-14", "2027-02-14", "SoFi Stadium", "Inglewood", "CA", "06037", "Super Bowl LXI", "stated"),
    ("NBA All-Star Game", "2027-02-21", "2027-02-21", None, "Phoenix", "AZ", "04013", "NBA All-Star Game",
     "host city stated; date from stated rule 'third Sunday of February'"),
    ("NCAA Men's Final Four championship game", "2027-04-05", "2027-04-05", "Ford Field", "Detroit", "MI", "26163",
     "2027 NCAA Division I men's basketball tournament", "stated"),
    ("MLB All-Star Game", "2027-07-13", "2027-07-13", "Wrigley Field", "Chicago", "IL", "17031", "Major League Baseball All-Star Game", "stated"),
    ("College Football Playoff National Championship", "2028-01-24", "2028-01-24", "Caesars Superdome", "New Orleans", "LA", "22071",
     "College Football Playoff National Championship", "stated"),
    ("Super Bowl LXII", "2028-02-13", "2028-02-13", "Mercedes-Benz Stadium", "Atlanta", "GA", "13121", "Super Bowl LXII", "stated"),
    ("2028 Summer Olympics (LA28)", "2028-07-14", "2028-07-30", "multiple", "Los Angeles", "CA", "06037", "2028 Summer Olympics",
     "stated; some events elsewhere in Greater LA and two subsites in Oklahoma City"),
    ("2028 Democratic National Convention", "2028-08-07", "2028-08-10", None, None, None, None, "2028 Democratic National Convention",
     "dates stated; host city undecided (Boston, Denver, Philadelphia shortlisted as of Aug 2026)"),
    ("2028 Republican National Convention", None, None, "Toyota Center", "Houston", "TX", "48201", "2028 Republican National Convention",
     "venue stated; dates TBD"),
    ("College Football Playoff National Championship", "2029-01-22", "2029-01-22", "Raymond James Stadium", "Tampa", "FL", "12057",
     "College Football Playoff National Championship", "stated"),
    ("Super Bowl LXIII", "2029-02-11", "2029-02-11", "Allegiant Stadium", "Paradise", "NV", "32003", "Super Bowl LXIII", "stated"),
]


def rule_events(years=(2027, 2028)):
    out = []
    for y in years:
        fs = nth(y, 5, 5, 1)
        out.append(("Kentucky Derby", str(fs), str(fs), "Churchill Downs", "Louisville", "KY", "21111", "Kentucky Derby",
                    "rule stated: 'first Saturday in May'"))
        su = last(y, 5, 0) - dt.timedelta(1)
        out.append(("Indianapolis 500", str(su), str(su), "Indianapolis Motor Speedway", "Speedway", "IN", "18097", "Indianapolis 500",
                    "rule stated: held over Memorial Day weekend; Sunday assumed - verify"))
        mg = easter(y) - dt.timedelta(47)
        out.append(("Mardi Gras (Shrove Tuesday)", str(mg), str(mg), None, "New Orleans", "LA", "22071", "Mardi Gras",
                    "computed: Easter minus 47 days"))
    return out


def events():
    ex = cached_json("events_extra.json", lambda: {})
    out = []
    for name, s, e, venue, city, st, f, art, basis in EVENTS + rule_events() + [tuple(x) for x in ex.get("events", [])]:
        out.append({"name": name, "date": s, "end": e, "venue": venue, "city": city, "state": st, "fips": f, "basis": basis,
                    "source": WP.format(art), "url": "https://en.wikipedia.org/wiki/" + art.replace(" ", "_")})
    return out


# ------------------------------------------------------------------ 3. connectivity
def amtrak():
    d = cached_json("amtrak.json", lambda: arcgis_all(BTS.format("NTAD_Amtrak_Stations"), "Code,Name,StnType,StaType,City,State,lon,lat"))
    return pd.DataFrame(d)


def ipcd_bus():
    d = cached_json("ipcd_bus.json", lambda: arcgis_all(BTS.format("NTAD_Intermodal_Passenger_Connectivity_Database"),
                                                         "FAC_ID,FAC_NAME,CITY,STATE,LONGITUDE,LATITUDE,BUS_I,BUS_T,RAIL_I,AIR_SERVE,FERRY_I",
                                                         where="BUS_I=1"))
    return pd.DataFrame(d)


def ntm_stops():
    p = CACHE / "ntm_stops.parquet"
    if p.exists():
        return pd.read_parquet(p)
    d = arcgis_all(BTS.format("NTAD_National_Transit_Map_Stops"), "OBJECTID,ntd_id,stop_lat,stop_lon,location_type")
    df = pd.DataFrame(d)
    df.to_parquet(p)
    return df


def walkability():
    p = CACHE / "walkability.parquet"
    if p.exists():
        return pd.read_parquet(p)
    d = arcgis_all(EPA_WALK, "OBJECTID,STATEFP,COUNTYFP,NatWalkInd,TotPop", page=1000)
    df = pd.DataFrame(d)
    df.to_parquet(p)
    return df


def ct_walk(counties):
    """CT: the index uses legacy CT counties; place each CT block group in a 2022+ planning region by its interior point."""
    p = CACHE / "walk_ct.json"
    if not p.exists():
        feats, off = [], 0
        while True:
            js = get(EPA_WALK, params={"where": "STATEFP='09'", "outFields": "OBJECTID,NatWalkInd", "returnGeometry": "true",
                                       "outSR": 4326, "maxAllowableOffset": 0.0005, "resultOffset": off, "resultRecordCount": 500,
                                       "orderByFields": "OBJECTID", "f": "geojson"}).json()
            fs = js.get("features", [])
            feats += fs
            if len(fs) < 500:
                break
            off += len(fs)
        p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), "utf-8")
    g = gpd.read_file(p).set_crs(4326, allow_override=True)
    g["geometry"] = g.geometry.buffer(0).representative_point()
    j = gpd.sjoin(g, counties, how="inner", predicate="within")
    j = j[j["f"].str.startswith("09")]
    return j.groupby("f")["NatWalkInd"].median().round(2).to_dict()


def ntd():
    svc = cached_json("ntd_service_by_agency.json", lambda: soda_all("6y83-7vuw", select="_5_digit_ntd_id,agency,report_year,max_city,max_state,max_primary_uza_code,max_primary_uza_name,sum_unlinked_passenger_trips_upt"))
    met = cached_json("ntd_metrics_by_agency.json", lambda: soda_all("g27i-aq2u", select="ntd_id,max_agency,max_city,max_state,report_year,max_uace_code,sum_fare_revenues_earned,sum_unlinked_passenger_trips"))
    info = cached_json("ntd_agency_info_2024.json", lambda: soda_all("ccvf-fykn"))
    return pd.DataFrame(svc), pd.DataFrame(met), pd.DataFrame(info)


def ua_county_weights():
    rel = cached_file("tab20_ua20_county20_natl.txt", "https://www2.census.gov/geo/docs/maps-data/data/rel2020/ua/tab20_ua20_county20_natl.txt")
    uc = cached_file("2020_UA_COUNTY.xlsx", "https://www2.census.gov/geo/docs/reference/ua/2020_UA_COUNTY.xlsx")
    r = pd.read_csv(rel, sep="|", dtype=str, encoding="utf-8-sig")
    r = r[r["GEOID_UA_20"].notna() & (r["GEOID_UA_20"] != "")]
    u = pd.read_excel(uc, dtype=str)
    u["f"] = u["STATE"] + u["COUNTY"]
    dens = dict(zip(u["f"], pd.to_numeric(u["POPDEN_URB"], errors="coerce")))  # urban persons per sq mile of urban land
    r["w"] = pd.to_numeric(r["AREALAND_PART"]) / 2589988.11 * r["GEOID_COUNTY_20"].map(dens).fillna(0)
    r["w"] = r["w"] / r.groupby("GEOID_UA_20")["w"].transform("sum")
    return {ua: list(zip(g["GEOID_COUNTY_20"], g["w"])) for ua, g in r.groupby("GEOID_UA_20")}


# ------------------------------------------------------------------ build
def main():
    counties = load_counties()
    fips_all = sorted(counties["f"])
    pop, popcol = county_pop()
    cen = county_centres(counties)

    # venues
    import os
    osm = pd.DataFrame(columns=["n", "k", "lon", "lat", "cap", "osm", "wd"]) if os.environ.get("TE_NO_OSM") else osm_venues()
    v = pd.concat([merge_osm_wd(osm, wikidata_venues()), universities()], ignore_index=True)
    v = assign_fips(v, counties)
    v = v[v["f"].notna()]
    log(f"venues total {len(v)}  by kind {v['k'].value_counts().to_dict()}")

    # holidays / events
    hol = holidays()
    evs = events()

    # Amtrak / bus / stops
    am = amtrak()
    am = am[am["StnType"].isin(["TRAIN", "TRAIN-BUS"]) | am["StnType"].str.contains("TRAIN|RAIL", na=False)]
    am = am.dropna(subset=["lon", "lat"])
    am = assign_fips(am, counties)
    tree = cKDTree(xyz(am["lon"], am["lat"]))
    cf = [f for f in fips_all if f in cen]
    dist, _ = tree.query(xyz([cen[f][0] for f in cf], [cen[f][1] for f in cf]))
    # no Amtrak rail in Hawaii / Puerto Rico / the territories; Alaska distances run to the Lower 48 - not meaningful
    amtrak_km = {f: d for f, d in zip(cf, np.round(chord_km(dist), 1)) if f[:2] not in ("02", "15", "60", "66", "69", "72", "78")}
    amtrak_n = am["f"].value_counts().to_dict()

    bus = ipcd_bus().rename(columns={"LONGITUDE": "lon", "LATITUDE": "lat"}).dropna(subset=["lon", "lat"])
    bus = assign_fips(bus, counties)
    bus_n = bus["f"].value_counts().to_dict()

    st = ntm_stops().rename(columns={"stop_lon": "lon", "stop_lat": "lat"})
    st = st[st["lon"].between(-180, 180) & st["lat"].between(-90, 90) & ~st["location_type"].isin(["1", "2", "3", "4"])]
    st = st.drop_duplicates(subset=["ntd_id", "lon", "lat"])
    st = assign_fips(st[["ntd_id", "lon", "lat"]], counties)
    st = st[st["f"].notna()]
    stops_n = st["f"].value_counts().to_dict()
    log(f"NTM stops kept {len(st)} in {len(stops_n)} counties")

    # NTD trips -> counties
    svc, met, info = ntd()
    svc["upt"] = pd.to_numeric(svc["sum_unlinked_passenger_trips_upt"], errors="coerce").fillna(0)
    yr = svc["report_year"].astype(int).max()
    sv = svc[svc["report_year"].astype(int) == yr].copy()
    sv["id"] = sv["_5_digit_ntd_id"].str.zfill(5)
    uaw = ua_county_weights()
    hq = {}
    if not info.empty:
        for r in info.itertuples():
            g = getattr(r, "street_address_geo_reference", None)
            if isinstance(g, dict) and g.get("coordinates"):
                hq[str(r.ntd_id).zfill(5)] = tuple(g["coordinates"])
    hqdf = pd.DataFrame([{"id": k, "lon": a, "lat": b} for k, (a, b) in hq.items()])
    hqdf = assign_fips(hqdf, counties) if not hqdf.empty else hqdf
    hqf = dict(zip(hqdf["id"], hqdf["f"])) if not hqdf.empty else {}
    st["id"] = st["ntd_id"].astype(str).str.extract(r"(\d+)")[0].str.zfill(5)
    agency_stop_share = {a: (g["f"].value_counts(normalize=True)).to_dict() for a, g in st.dropna(subset=["id"]).groupby("id")}
    agency_stop_n = st.dropna(subset=["id"]).groupby("id").size().to_dict()
    trips = {}
    how = {"stops": 0, "uza": 0, "hq": 0, "none": 0}
    for r in sv.itertuples():
        if r.upt <= 0:
            continue
        if r.id in agency_stop_share:
            # served counties = those holding >=3 stops and >=1% of the agency's stops; split by resident population
            # (splitting by raw stop counts over-credits low-density counties with many bus stops)
            sh = agency_stop_share[r.id]
            served = [f for f, x in sh.items() if x >= 0.01 and x * agency_stop_n[r.id] >= 3] or [max(sh, key=sh.get)]
            pw = np.array([pop.get(f, 0) for f in served], float)
            pw = pw / pw.sum() if pw.sum() > 0 else np.full(len(served), 1 / len(served))
            alloc = list(zip(served, pw)); how["stops"] += r.upt
        elif str(r.max_primary_uza_code or "").zfill(5) in uaw:
            alloc = uaw[str(r.max_primary_uza_code).zfill(5)]; how["uza"] += r.upt
        elif hqf.get(r.id):
            alloc = [(hqf[r.id], 1.0)]; how["hq"] += r.upt
        else:
            how["none"] += r.upt
            continue
        for f, w in alloc:
            trips[f] = trips.get(f, 0) + r.upt * w
    tot = sum(how.values())
    log(f"NTD {yr}: UPT {tot:,.0f}; allocation share " + ", ".join(f"{k} {v / tot:.1%}" for k, v in how.items()))

    # fare-free proxy: NTD reports zero fare revenue earned with >=100k annual trips
    met["fare"] = pd.to_numeric(met["sum_fare_revenues_earned"], errors="coerce")
    met["upt"] = pd.to_numeric(met["sum_unlinked_passenger_trips"], errors="coerce")
    myr = met["report_year"].astype(int).max()
    mf = met[(met["report_year"].astype(int) == myr) & (met["fare"] == 0) & (met["upt"] >= 100000)].copy()
    mf["id"] = mf["ntd_id"].str.zfill(5)
    fare0 = {}
    for r in mf.itertuples():
        f = hqf.get(r.id)
        if f is None and r.id in agency_stop_share:
            f = max(agency_stop_share[r.id], key=agency_stop_share[r.id].get)
        if f:
            fare0.setdefault(f, []).append(r.max_agency)
    log(f"zero-fare-revenue agencies (NTD {myr}, >=100k UPT): {len(mf)}, mapped to {len(fare0)} counties")

    # walkability (median of block groups per county)
    w = walkability()
    w["f"] = w["STATEFP"].astype(str).str.zfill(2) + w["COUNTYFP"].astype(str).str.zfill(3)
    walk = w.groupby("f")["NatWalkInd"].median().round(2).to_dict()
    if any(f.startswith("091") for f in fips_all):
        walk.update(ct_walk(counties))
    # counties
    out_c = {}
    for f in fips_all:
        vv = v[v["f"] == f]
        big = vv[(vv["cap"].fillna(0) >= BIG_CAP) & (vv["k"] != "university")]
        p = pop.get(f)
        out_c[f] = {
            "venues_n": int(len(vv)),
            "big_venue_capacity_sum": int(big["cap"].sum()) if len(big) else 0,
            "big_venues_n": int(len(big)),
            "univ_enr": int(vv["enr"].fillna(0).sum()) if "enr" in vv else 0,
            "fbs": int((vv.get("fb") == "FBS").sum()) if "fb" in vv else 0,
            "nearest_amtrak_km": float(amtrak_km[f]) if f in amtrak_km else None,
            "amtrak_n": int(amtrak_n.get(f, 0)),
            "intercity_bus_n": int(bus_n.get(f, 0)),
            "transit_stops_n": int(stops_n.get(f, 0)),
            "transit_trips_per_capita": round(trips[f] / p, 2) if f in trips and p else None,
            "walkability_median": float(walk[f]) if f in walk else None,
            "fare_free_agency": fare0.get(f, [None])[0] is not None if f in fare0 else False,
        }
    for f, names in fare0.items():
        if f in out_c:
            out_c[f]["fare_free_agencies"] = names[:5]

    venues = []
    for r in v.itertuples():
        o = {"n": r.n, "k": r.k, "lon": float(r.lon), "lat": float(r.lat), "cap": None if pd.isna(r.cap) else int(r.cap), "f": r.f}
        if r.k == "university":
            o["enr"] = int(r.enr)
            if isinstance(r.fb, str):
                o["fb"] = r.fb
        venues.append(o)

    meta = build_meta(popcol, yr, myr, how, len(venues))
    out = {"meta": meta, "venues": venues, "holidays": hol, "break_proxies": break_proxies(), "events": evs, "counties": out_c}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), "utf-8")
    log(f"wrote {OUT} {OUT.stat().st_size / 1e6:.2f} MB")
    report(out)


def build_meta(popcol, yr, myr, how, nv):
    tot = sum(how.values()) or 1
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "scope": "places and dates only; no personal data",
        "big_venue_threshold": BIG_CAP,
        "notes": {
            "big_venue_capacity_sum": f"sum of OSM capacity tags of non-university venues with capacity >= {BIG_CAP} (venues without a capacity tag do not count)",
            "nearest_amtrak_km": "great-circle km from the county 2020 mean centre of population (Census) to the nearest Amtrak TRAIN station (NTAD); null for AK, HI, PR and territories",
            "transit_trips_per_capita": f"NTD {yr} unlinked passenger trips; each agency's trips are split over the counties holding >=3 and >=1% of its GTFS stops (NTM) in proportion to county population, else by its primary UZA (2020 UA-county land x county urban density), else agency HQ county; divided by Census {popcol}. Shares of UPT: "
                                        + ", ".join(f"{k} {v / tot:.1%}" for k, v in how.items()),
            "walkability_median": "median EPA National Walkability Index (1-20) over the county's 2019 block groups; Connecticut block groups are re-assigned to 2022+ planning regions by interior point",
            "fare_free_agency": f"derived: an agency headquartered (or with most stops) in the county reported $0 fare revenues earned and >=100k trips in NTD {myr}; NTD does not itself publish a 'fare-free' list",
            "break_proxies": "heuristic windows; no open federal school-calendar source exists",
        },
        "sources": SOURCES,
        "attribution": ("Venue locations (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright); the venue list is a derived "
                        "database that must be shared under ODbL. Transit stops: USDOT/BTS National Transit Map (CC BY 3.0 US) incl. City of Lawndale and "
                        "Via Mobility Services. Amtrak stations: National Railroad Passenger Corporation (Amtrak) and BTS [distributor]. IPCD: BTS. "
                        "NTD: Federal Transit Administration. Walkability: US EPA. Universities: NCES IPEDS; football: US Dept of Education EADA. "
                        "Venue capacities partly from Wikidata (CC0). Event facts: Wikipedia contributors, CC BY-SA 4.0. Population/centres/urban areas: US Census Bureau."),
        "rejected": REJECTED,
        "counts": {"venues": nv},
    }


SOURCES = [
    {"id": "osm", "name": "OpenStreetMap, Geofabrik north-america/us extract (https://download.geofabrik.de/north-america/us.html) read locally with pyosmium (usa/osm_extract.py)", "url": "https://www.openstreetmap.org/copyright", "licence": "ODbL 1.0",
     "geofabrik": osm_extract.GEOFABRIK_TERMS,
     "quote": "You are free to copy, distribute, transmit and adapt our data, as long as you credit OpenStreetMap and its contributors. If you alter or build upon our data, you may distribute the result only under the same license."},
    {"id": "wikidata", "name": "Wikidata SPARQL (venue coordinates + capacity P1083, fills OSM capacity gaps)", "url": "https://www.wikidata.org/wiki/Wikidata:Licensing",
     "licence": "CC0 1.0", "quote": "All structured data (i.e. the main, Property, Lexeme, and EntitySchema namespaces) is released into the public domain under Creative Commons Zero."},
    {"id": "ipeds", "name": "NCES IPEDS HD2024 + DRVEF2023", "url": "https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx", "licence": "US federal government work (17 U.S.C. 105), public domain"},
    {"id": "eada", "name": "US Dept of Education Equity in Athletics (EADA) 2024-25", "url": "https://ope.ed.gov/athletics/#/datafile/list", "licence": "US federal government work, public domain"},
    {"id": "opm", "name": "OPM Federal Holidays + 5 U.S.C. 6103", "url": "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/", "licence": "US federal government work, public domain"},
    {"id": "wikipedia", "name": "Wikipedia event articles (dates/venues are facts)", "url": "https://en.wikipedia.org/wiki/Wikipedia:Text_of_the_Creative_Commons_Attribution-ShareAlike_4.0_International_License", "licence": "CC BY-SA 4.0"},
    {"id": "ntd", "name": "FTA National Transit Database (6y83-7vuw service by agency, g27i-aq2u metrics, ccvf-fykn 2024 agency info)", "url": "https://data.transportation.gov/d/6y83-7vuw",
     "licence": "Public Domain U.S. Government", "quote": "licence field: 'Public Domain U.S. Government' (https://www.usa.gov/government-works)"},
    {"id": "amtrak", "name": "BTS NTAD Amtrak Stations (as of 2026-10-08)", "url": "https://geodata.bts.gov/datasets/usdot::amtrak-stations",
     "licence": "public domain", "quote": "This NTAD dataset is a work of the United States government as defined in 17 U.S.C. § 101 and as such are not protected by any U.S. copyrights. This work is available for unrestricted public use."},
    {"id": "ipcd", "name": "BTS NTAD Intermodal Passenger Connectivity Database (BUS_I=1 intercity bus)", "url": "https://geodata.bts.gov/datasets/usdot::intermodal-passenger-connectivity-database-ipcd",
     "licence": "public domain", "quote": "This NTAD dataset is a work of the United States government ... available for unrestricted public use."},
    {"id": "ntm", "name": "BTS NTAD National Transit Map Stops (compiled 2026-09-23)", "url": "https://geodata.bts.gov/datasets/usdot::national-transit-map-stops",
     "licence": "CC BY 3.0 US", "quote": "Creative Commons Attribution 3.0 United States (CC-BY-3.0)"},
    {"id": "epa_walk", "name": "EPA National Walkability Index (from Smart Location Database v3)", "url": "https://geodata.epa.gov/arcgis/rest/services/OA/WalkabilityIndex/MapServer/0",
     "licence": "US federal government work, public domain", "quote": "These data are available for public access and use and carry no restrictions. (layer metadata)"},
    {"id": "census", "name": "Census PEP Vintage 2025 county totals; 2020 mean centres of population; 2020 UA-county relationship; 2020 county urban/rural",
     "url": "https://www2.census.gov/", "licence": "US federal government work, public domain"},
]
REJECTED = [
    {"name": "Ticketmaster Discovery API", "url": "https://developer.ticketmaster.com/support/terms-of-use/",
     "reason": "terms forbid deriving revenues from use of the API and caching/storing Event Content beyond reasonable periods"},
    {"name": "SeatGeek Platform API", "url": "https://seatgeek.com/api-terms",
     "reason": "proprietary terms; SeatGeek may charge fees/limit access; scraping and data-mining of SeatGeek content prohibited"},
    {"name": "PredictHQ", "url": "https://www.predicthq.com/legal/terms",
     "reason": "paid service (14-day trial / paid plans); may not sell or sub-license PredictHQ Data without written consent"},
    {"name": "League/venue schedule pages (NFL, NBA, NHL, MLB, NCAA, NASCAR, venue sites)", "url": "",
     "reason": "verify/not used: site terms typically prohibit automated collection; only a handful of headline host/date facts were taken from Wikipedia"},
    {"name": "Public Overpass API servers and the QLever OSM-planet endpoint (rejected notes only - not queried)", "url": "",
     "reason": "Overpass main-instance usage policy: commercial use should use self-hosted or paid servers; QLever states no terms (grey). "
               "The same ODbL data is read from a local Geofabrik extract instead."},
    {"name": "HIFLD Open (sports venues, colleges)", "url": "https://hifld-geoplatform.hub.arcgis.com/",
     "reason": "HIFLD Open retired 2025; replaced here by OSM + NCES IPEDS"},
    {"name": "School-break calendars", "url": "", "reason": "no open federal source (NCES does not publish district calendars); heuristic proxies only"},
    {"name": "Free-fare agency list", "url": "", "reason": "no official federal list; derived flag from NTD $0 fare revenue instead"},
]


def report(out):
    c = out["counties"]
    n = len(c)
    cov = lambda k: sum(1 for x in c.values() if x.get(k) not in (None, 0))
    print("\nCOVERAGE")
    for k in ["venues_n", "big_venue_capacity_sum", "nearest_amtrak_km", "transit_stops_n", "transit_trips_per_capita", "walkability_median", "fare_free_agency"]:
        print(f"  {k:26s} {cov(k):5d}/{n}")
    print("\nTOP 20 by big_venue_capacity_sum")
    top = sorted(c.items(), key=lambda kv: -kv[1]["big_venue_capacity_sum"])[:20]
    for f, x in top:
        print(f"  {f} {x['big_venue_capacity_sum']:>9,} ({x['big_venues_n']} venues)  trips/cap {x['transit_trips_per_capita']}  walk {x['walkability_median']}  amtrak {x['nearest_amtrak_km']} km")


if __name__ == "__main__":
    main()

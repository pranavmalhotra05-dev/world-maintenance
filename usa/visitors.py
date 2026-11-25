"""Lane V2 - aggregate VISITOR COUNTS per state, city, county and site (never individuals).

Sources (all US federal / NY State open data; licence quotes recorded in meta.sources and
usa/research/visitors.md):
  * NTTO "Overseas Visitor Impact on State Economies 2024" Table 1  -> overseas visits/spend/jobs by state
  * NTTO SIAT Inbound Market Profiles (Vacation + Business, 2024)   -> city share -> city overseas visits
  * BEA Travel & Tourism Satellite Account 2023 (national only)      -> meta.national
  * BEA Outdoor Recreation Satellite Account, states 2025 (Nov 2026)  -> state outdoor-rec VA / jobs
  * BTS / CBP Border Crossing Entry Data (data.bts.gov keg4-3bc2)    -> inbound persons by port, last 12 m
  * BTS / MARAD Top 25 ports by cruise passenger arrivals 2023 (3z7h-xatu)
  * FICOR FY2024 Interagency Recreation Visitation Data Report, Table A (BLM, USFS, USACE, FWS, Reclamation)
      + unit boundaries from USFS EDW, BLM national admin units + NLCS, USACE recreation areas,
        FWS refuge boundaries, Reclamation RMAs  -> area-weighted split to counties
  * NY State Parks annual attendance by facility (data.ny.gov 8f3n-xj78) + facility points (9uuk-x7vh)

NOT rebuilt here (other lanes): airport passengers, NPS visits, I-94 by state (tourism_air.json);
hotel tax, QCEW lodging, STR registries, Wikipedia views (tourism_econ.json).

Usage:  .venv\\Scripts\\python -u -W ignore usa\\visitors.py
Output: web/usa/data/visitors.json
"""
import csv
import difflib
import io
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date

import openpyxl
import pypdf
import requests
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "_cache", "visitors")
RAW = os.path.join(CACHE, "raw")
OUT = os.path.join(ROOT, "web", "usa", "data", "visitors.json")
COUNTIES_JSON = os.path.join(ROOT, "web", "usa", "data", "counties.json")
COUNTIES_GEO = os.path.join(ROOT, "web", "usa", "data", "counties.geojson")
os.makedirs(RAW, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) open-data research script"}

STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
          "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
          "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
          "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
          "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
          "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
          "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
          "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
          "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR", "Guam": "GU",
          "U.S. Virgin Islands": "VI"}
FIPS_ST = {}  # filled from counties.json


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fetch(url, name, params=None, binary=True, tries=3):
    path = os.path.join(RAW, name)
    if os.path.exists(path) and os.path.getsize(path) > 200:
        return path
    last = None
    for t in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=300)
            r.raise_for_status()
            with open(path + ".part", "wb") as fh:
                fh.write(r.content)
            os.replace(path + ".part", path)
            log("  got", name, len(r.content) // 1024, "KB")
            return path
        except Exception as e:  # noqa: BLE001
            last = e
            log("  retry", name, e)
            time.sleep(5 * (t + 1))
    raise last


def jload(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------------ geometry helpers
def esri_to_shape(g):
    if not g or "rings" not in g:
        return None
    shells, holes = [], []
    for ring in g["rings"]:
        if len(ring) < 4:
            continue
        p = Polygon(ring)
        # Esri: outer rings clockwise (negative signed area in shapely's convention), holes counter-clockwise
        signed = sum((ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]) for i in range(len(ring) - 1))
        if not p.is_valid:
            p = make_valid(p).buffer(0)
        (shells if signed < 0 else holes).append(p)
    if not shells:
        shells, holes = holes, []
    geom = unary_union(shells)
    if holes:
        geom = geom.difference(unary_union(holes))
    if geom is None or geom.is_empty:
        return None
    return geom if geom.is_valid else make_valid(geom)


def arcgis_layer(url, name, fields, where="1=1", offset=0.005, page=500):
    """Download a polygon layer (outSR 4326, generalised) to cache json [{attrs, esri geometry}]."""
    path = os.path.join(CACHE, name + ".json")
    if os.path.exists(path):
        return jload(path)
    feats, start = [], 0
    while True:
        params = {"where": where, "outFields": ",".join(fields), "outSR": 4326, "f": "json",
                  "returnGeometry": "true", "maxAllowableOffset": offset, "geometryPrecision": 4,
                  "resultOffset": start, "resultRecordCount": page}
        for t in range(4):
            try:
                r = requests.get(url + "/query", params=params, headers=UA, timeout=300).json()
                if "error" in r:
                    raise RuntimeError(r["error"])
                break
            except Exception as e:  # noqa: BLE001
                log("  retry", name, start, e)
                time.sleep(5 * (t + 1))
                if t == 3:
                    raise
        fs = r.get("features", [])
        for f in fs:
            g = esri_to_shape(f.get("geometry"))
            if g is not None:
                g = g.simplify(offset, preserve_topology=True)
            feats.append({"a": f["attributes"], "g": mapping(g) if g is not None and not g.is_empty else None})
        log("   page", name, start, len(fs))
        if not fs or not (r.get("exceededTransferLimit") or len(fs) == page):
            break
        start += len(fs)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(feats, fh)
    log("  layer", name, len(feats))
    return feats


class Counties:
    def __init__(self):
        cj = jload(COUNTIES_JSON)
        ci = {c: i for i, c in enumerate(cj["cols"])}
        self.meta = {}
        self.by_name = {}
        for r in cj["rows"]:
            f = r[ci["fips"]]
            self.meta[f] = {"name": r[ci["NAME"]], "st": r[ci["STUSPS"]], "lon": r[ci["lon"]], "lat": r[ci["lat"]]}
            self.by_name[(norm_county(r[ci["NAME"]]), r[ci["STUSPS"]])] = f
            FIPS_ST[f[:2]] = r[ci["STUSPS"]]
        g = jload(COUNTIES_GEO)
        self.fips, self.geoms = [], []
        for ft in g["features"]:
            s = shape(ft["geometry"])
            if not s.is_valid:
                s = make_valid(s)
            self.fips.append(ft["properties"]["f"])
            self.geoms.append(s)
        self.tree = STRtree(self.geoms)

    def point(self, lon, lat):
        p = Point(lon, lat)
        for i in self.tree.query(p):
            if self.geoms[i].contains(p):
                return self.fips[i]
        # nearest within ~25 km (coastal ports, simplified coastlines)
        i = self.tree.nearest(p)
        if i is not None and self.geoms[i].distance(p) < 0.25:
            return self.fips[i]
        return None

    def split(self, geom):
        """area shares of geom per county (equal-area via cos(lat) cancels within a unit)."""
        out = {}
        for i in self.tree.query(geom):
            try:
                a = self.geoms[i].intersection(geom).area
            except Exception:  # noqa: BLE001
                a = make_valid(self.geoms[i]).intersection(make_valid(geom)).area
            if a > 0:
                out[self.fips[i]] = out.get(self.fips[i], 0) + a
        tot = sum(out.values())
        return {k: v / tot for k, v in out.items()} if tot else {}


def norm_county(n):
    n = n.lower().replace("st.", "saint").replace("ste.", "sainte")
    n = re.sub(r"\b(county|parish|borough|census area|city and borough|municipality)\b", "", n)
    return re.sub(r"[^a-z]", "", n)


STOP = {"national", "forest", "forests", "field", "office", "wildlife", "refuge", "nwr", "the", "and", "of",
        "recreation", "management", "area", "areas", "conservation", "monument", "lake", "lakes", "dam", "reservoir",
        "complex", "district", "wetland", "project", "river", "grassland", "grasslands", "nf", "s"}


def nkey(s):
    s = s.lower().replace("&", " and ").replace("’", "'").replace("'", "")
    s = re.sub(r"\(.*?\)", " ", s)
    toks = [ABBR.get(t, t) for t in re.split(r"[^a-z0-9]+", s) if t]
    return " ".join(t for t in toks if t not in STOP)


ABBR = {"ft": "fort", "mt": "mount", "r": "river", "rvr": "river", "miss": "mississippi", "nav": "navigation",
        "sys": "system", "hunt": "huntington", "st": "saint", "ark": "arkansas", "rivers": "river"}


def match(name, index, cutoff=0.86):
    k = nkey(name)
    if k in index:
        return index[k]
    m = difflib.get_close_matches(k, list(index), n=1, cutoff=cutoff)
    if m:
        return index[m[0]]
    kt = set(k.split())
    if len(kt) >= 1:  # token-subset fallback, only when unambiguous
        sup = [c for c in index if kt <= set(c.split())]
        if len(sup) == 1:
            return index[sup[0]]
        sub = [c for c in index if len(c.split()) >= 2 and set(c.split()) <= kt and len(c.split()) >= 0.6 * len(kt)]
        if len(sub) == 1:
            return index[sub[0]]
    return None


def match_parts(name, index, cutoff):
    """match a unit; if it names several areas ('A/B', 'A & B'), union the parts that match."""
    h = match(name, index, cutoff)
    if h:
        return h
    parts = [x for x in re.split(r"/| & | and (?=[A-Z])", name) if x.strip()]
    hits = [match(x, index, cutoff) for x in parts] if len(parts) > 1 else []
    hits = [x for x in hits if x]
    if hits:
        return (" + ".join(x[0] for x in hits), unary_union([x[1] for x in hits]))
    return None


# ------------------------------------------------------------------ 1. NTTO states + cities
NTTO_STATE_PDF = "https://www.trade.gov/sites/default/files/2026-04/2024-Visitors-by-State_Final.pdf"
NTTO_PROFILE = "https://www.trade.gov/sites/default/files/2025-06/Inbound-Market-Profile-{}.xlsx"


def ntto_states():
    p = fetch(NTTO_STATE_PDF, "2024_visitors_by_state.pdf")
    txt = "\n".join(pg.extract_text() or "" for pg in pypdf.PdfReader(p).pages)
    i = txt.find("Table 1. Economic Impact of Overseas Visitors by State and")
    lines = [l.strip() for l in txt[i:].splitlines() if l.strip()]
    out = {}
    names = sorted(STATES, key=len, reverse=True)
    j = 0
    while j < len(lines) - 3:
        nm = lines[j].rstrip("*").strip()
        if nm in STATES and re.fullmatch(r"[\d,]+", lines[j + 1]) and re.fullmatch(r"[\d,.]+", lines[j + 2]):
            st = STATES[nm]
            if st not in out:
                out[st] = {"overseas_visits": int(lines[j + 1].replace(",", "")),
                           "overseas_spend_musd": float(lines[j + 2].replace(",", "")),
                           "overseas_jobs": int(lines[j + 3].replace(",", "")) if re.fullmatch(r"[\d,]+", lines[j + 3]) else None,
                           "overseas_low_sample": lines[j].endswith("*")}
            j += 4
            continue
        j += 1
    _ = names
    return out


def ntto_profile(kind, year=2024):
    p = fetch(NTTO_PROFILE.format(kind), "imp_%s.xlsx" % kind)
    ws = openpyxl.load_workbook(p, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    vol, ycol = None, None
    for r in rows[:80]:
        if r and isinstance(r[1], str) and r[1].startswith("Total %s Visitors" % kind):
            vol = r
        if r and 2024 in r and ycol is None:
            ycol = list(r).index(year)
    volume = float(vol[ycol]) * 1000
    dest, in_dest, state = {}, False, None
    for r in rows:
        if not r:
            continue
        c1 = " ".join(str(x) for x in r[:3] if isinstance(x, str))
        if c1.startswith("Destinations Visited"):
            in_dest = True
            continue
        if c1.startswith("Notes:") or c1.startswith("Market Profile"):
            in_dest = False
            continue
        if not in_dest:
            continue
        v = r[ycol] if len(r) > ycol else None
        if not isinstance(v, (int, float)):
            continue
        if r[2]:
            nm = str(r[2]).strip()
            if nm in STATES or nm == "Washington, D.C.":
                state = "DC" if nm == "Washington, D.C." else STATES[nm]
                dest[(state, None)] = float(v)
                if nm == "Washington, D.C.":
                    dest[("DC", "Washington, D.C.")] = float(v)
            else:
                state = None  # region total in col 2? (regions are in col 1)
        elif r[3] and state:
            dest[(state, str(r[3]).strip())] = float(v)
    return volume, dest


def ntto_cities(state_tot):
    profs = [ntto_profile("Vacation"), ntto_profile("Business")]
    city_w, state_w = defaultdict(float), defaultdict(float)
    for vol, d in profs:
        for (st, city), pct in d.items():
            if city is None:
                state_w[st] += vol * pct / 100
            else:
                city_w[(st, city)] += vol * pct / 100
    out = []
    for (st, city), w in city_w.items():
        if st not in state_tot or not state_w.get(st):
            continue
        share = min(1.0, w / state_w[st])
        out.append({"name": city, "state": st, "overseas_visits": int(round(state_tot[st]["overseas_visits"] * share, -3)),
                    "share_of_state": round(share, 3), "year": 2024,
                    "source": "NTTO SIAT 2024 (Vacation+Business profile city/state share x NTTO state total)"})
    out.sort(key=lambda c: -c["overseas_visits"])
    return out


# ------------------------------------------------------------------ 2. BEA
ORSA_STATE = "https://www.bea.gov/sites/default/files/2026-11/ores1126-state.xlsx"
TTSA_2023 = "https://www.bea.gov/sites/default/files/2025-02/Tour2023AU24.xlsx"


def bea():
    p = fetch(ORSA_STATE, "ores1126-state.xlsx")
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    t1 = list(wb["Table 1"].iter_rows(values_only=True))
    title = t1[0][0]
    yr = int(re.findall(r"(20\d\d)", title)[-1]) if re.findall(r"(20\d\d)", str(title)) else None
    st = {}
    for r in t1:
        nm = (r[0] or "").strip() if isinstance(r[0], str) else ""
        if nm in STATES and isinstance(r[1], (int, float)):
            st[STATES[nm]] = {"outdoor_rec_va_kusd": int(r[1]), "outdoor_rec_va_pct": r[2],
                              "outdoor_rec_jobs": int(r[3]) if isinstance(r[3], (int, float)) else None,
                              "outdoor_rec_year": yr}
    p = fetch(TTSA_2023, "Tour2023AU24.xlsx")
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    nat = {"year": 2023, "source": "BEA TTSA 2023 (Tour2023AU24.xlsx)"}
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            if not r or not isinstance(r[0], str):
                continue
            k = r[0].strip().lower()
            if ws.title.startswith("Table 5") and k.startswith("total"):
                nat["direct_tourism_output_musd"] = r[3]
                nat["total_tourism_related_output_musd"] = r[5]
            if ws.title.startswith("Table 7") and k.startswith("total"):
                nat["direct_tourism_jobs_k"] = r[1]
                nat["total_tourism_related_jobs_k"] = r[3]
    return st, nat, title


# ------------------------------------------------------------------ 3. Border crossings
BORDER = "https://data.bts.gov/resource/keg4-3bc2.json"
PERSON_MEASURES = ["Personal Vehicle Passengers", "Bus Passengers", "Pedestrians", "Train Passengers"]


def border():
    mx = requests.get(BORDER, params={"$select": "max(date)"}, headers=UA, timeout=120).json()[0]["max_date"][:10]
    y, m = int(mx[:4]), int(mx[5:7])
    sy, sm = (y - 1, m + 1) if m < 12 else (y, 1)
    start = "%04d-%02d-01" % (sy, sm)
    name = "border_12m_%s.json" % mx[:7]
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        q = {"$select": "port_code,port_name,state,border,measure,max(latitude) as lat,max(longitude) as lon,sum(value) as v",
             "$where": "date >= '%sT00:00:00' and measure in (%s)" % (start, ",".join("'%s'" % x for x in PERSON_MEASURES)),
             "$group": "port_code,port_name,state,border,measure", "$limit": 50000}
        rows = requests.get(BORDER, params=q, headers=UA, timeout=300).json()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    rows = jload(path)
    ports = {}
    for r in rows:
        k = r["port_code"]
        p = ports.setdefault(k, {"name": r.get("port_name"), "state": r.get("state"), "border": r.get("border"),
                                 "lat": float(r["lat"]) if r.get("lat") else None,
                                 "lon": float(r["lon"]) if r.get("lon") else None, "by": {}, "v": 0})
        v = int(float(r.get("v") or 0))
        p["by"][r["measure"]] = v
        p["v"] += v
    return ports, start, mx


# ------------------------------------------------------------------ 4. Cruise
CRUISE = "https://data.bts.gov/resource/3z7h-xatu.json"
CRUISE_LL = {  # terminal coordinates (approximate, public knowledge)
    "Miami, FL": (-80.170, 25.778), "Port Canaveral, FL": (-80.610, 28.410), "Juneau, AK": (-134.408, 58.297),
    "Port Everglades, FL": (-80.118, 26.093), "Ketchikan, AK": (-131.649, 55.341),
    "Charlotte Amalie, VI": (-64.930, 18.335), "Galveston, TX": (-94.792, 29.309), "San Juan, PR": (-66.113, 18.462),
    "Skagway, AK": (-135.322, 59.451), "Seattle, WA": (-122.383, 47.629), "New York, NY": (-74.000, 40.767),
    "Long Beach, CA": (-118.190, 33.753), "Los Angeles, CA": (-118.278, 33.742), "Sitka, AK": (-135.384, 57.115),
    "New Orleans, LA": (-90.062, 29.938), "Newark, NJ": (-74.090, 40.668), "Tampa, FL": (-82.447, 27.943),
    "Key West, FL": (-81.808, 24.560), "San Diego, CA": (-117.173, 32.716), "Charleston, SC": (-79.924, 32.784),
    "Anchorage, AK": (-149.888, 61.226), "Boston, MA": (-71.030, 42.344), "Bar Harbor, ME": (-68.204, 44.392),
    "Baltimore, MD": (-76.578, 39.272), "Oakland, CA": (-122.300, 37.796)}
CRUISE_FIPS = {"Newark, NJ": "34013", "Charlotte Amalie, VI": None, "San Juan, PR": None, "Juneau, AK": "02110",
               "Ketchikan, AK": "02130", "Skagway, AK": "02230", "Sitka, AK": "02220", "Anchorage, AK": "02020"}


def cruise(C):
    rows = requests.get(CRUISE, params={"$limit": 100}, headers=UA, timeout=120).json()
    out = []
    for r in rows:
        nm = r["row_labels"]
        lon, lat = CRUISE_LL.get(nm, (None, None))
        f = CRUISE_FIPS.get(nm, "x")
        if f == "x":
            f = C.point(lon, lat) if lon is not None else None
        out.append({"name": nm, "lon": lon, "lat": lat, "visits": int(float(r["sum_of_total_pax"])), "fips": f,
                    "state": nm.split(", ")[-1]})
    return out


# ------------------------------------------------------------------ 6. Federal lands (FICOR Table A)
FICOR = "https://ridb.recreation.gov/downloads/FY2024%20Interagency%20Recreation%20Visitation%20Data%20Report.pdf"
AGENCIES = ["BLM", "FWS", "NPS", "Reclamation", "USACE", "USFS"]
VAL = re.compile(r"\s+((?:\d{1,3}(?:,\d{3})+|\d+)|Not Recorded|Not Reported|Not recorded|Not reported|Not Applicable|Not applicable)\s*$")


def ficor_table_a():
    p = fetch(FICOR, "interagency_fy2024.pdf")
    cache = os.path.join(CACHE, "ficor_table_a.json")
    if os.path.exists(cache):
        return jload(cache)
    reader = pypdf.PdfReader(p)
    lines = []
    for pg in reader.pages:
        t = pg.extract_text() or ""
        if "Table A. FY 2024 Total Visits by Reporting Unit" not in t:
            if lines:
                break
            continue
        lines += [l.strip() for l in t.splitlines()]
    recs, buf = [], ""
    for l in lines:
        if not l or l.startswith("Recreation Visitation Data Appendix") or l.startswith("Agency Administrative") \
                or l.startswith("Table A.") or l.startswith("Within Table A") or l.startswith("====="):
            continue
        buf = (buf + " " + l).strip() if buf else l
        m = VAL.search(buf)
        if m and buf.split(" ")[0] in AGENCIES and len(buf.split(" ")) > 2:
            ag = buf.split(" ")[0]
            body = buf[len(ag):m.start()].strip()
            v = m.group(1)
            recs.append({"agency": ag, "body": body, "visits": int(v.replace(",", "")) if v[0].isdigit() else None})
            buf = ""
        elif buf.split(" ")[0] not in AGENCIES:
            buf = ""  # stray text
    st_names = sorted(list(STATES) + ["Eastern States", "Oregon/Washington", "Montana/Dakotas"], key=len, reverse=True)
    for r in recs:
        b = r["body"]
        if r["agency"] in ("BLM", "FWS"):
            found, rest, more = [], b, True
            while more:
                more = False
                for s in st_names + ["United States Minor Outlying Islands"]:
                    if rest.startswith(s + ", ") or rest.startswith(s + " "):
                        found.append(s)
                        more = rest.startswith(s + ", ")
                        rest = rest[len(s):].lstrip(",").strip()
                        break
            r["filter"], r["unit"] = (found[0] if found else None), rest
            r["filters"] = found
        elif r["agency"] == "USFS":
            m = re.match(r"(Region \d+)\s+(.*)", b)
            r["filter"], r["unit"] = (m.group(1), m.group(2)) if m else (None, b)
        elif r["agency"] == "Reclamation":
            m = re.match(r"(.*?Office)\s+(.*)", b)
            r["filter"], r["unit"] = (m.group(1), m.group(2)) if m else (None, b)
        elif r["agency"] == "NPS":
            m = re.match(r"(Northeast|Midwest|Alaska|Intermountain|Southeast|National Capital|Pacific West)\s+(.*)", b)
            r["filter"], r["unit"] = (m.group(1), re.sub(r"NP\d(,\s?NP\d)*$", "", m.group(2)).strip()) if m else (None, b)
        else:
            r["filter"], r["unit"] = None, b
        del r["body"]
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(recs, fh)
    return recs


LAYERS = {
    "USFS": ("https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestSystemBoundaries_01/MapServer/1",
             ["forestname", "region"], "forestname"),
    "BLM_FO": ("https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3",
               ["ADMU_NAME", "ADMIN_ST", "ADM_UNIT_CD"], "ADMU_NAME"),
    "BLM_NLCS": ("https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_NLCS_NM_NCA_poly/MapServer/1",
                 ["*"], None),
    "FWS": ("https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/National_Wildlife_Refuge_System_Boundaries/FeatureServer/0",
            ["ORGNAME", "ORGCODE", "RSL_TYPE"], "ORGNAME"),
    "USACE": ("https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/usace_recreation_areas/FeatureServer/0",
              ["RECPROJECTSITENAME", "RECPROJECTSITEID", "DISTRICT"], "RECPROJECTSITENAME"),
    # USGS PAD-US 4.1 (public domain) "Manager Name" layer, Bureau of Reclamation lands (managed or owned by USBR).
    # Replaces Reclamation's own Recreation Management Areas layer, whose terms forbid republishing / deriving (red).
    "Reclamation": ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Manager_Name_PADUS/FeatureServer/0",
                    ["Unit_Nm", "Loc_Nm", "Des_Tp", "State_Nm"], "Unit_Nm", "Mang_Name='USBR' OR Own_Name='USBR'", "Loc_Nm"),
}
CACHE_NAME = {"Reclamation": "layer_Reclamation_padus41_own"}
PAGE = {"USFS": 50, "BLM_FO": 5, "BLM_NLCS": 10, "FWS": 100, "USACE": 500, "Reclamation": 100}
KIND = {"USFS": "national_forest", "BLM": "blm_unit", "FWS": "wildlife_refuge", "USACE": "usace_project",
        "Reclamation": "reclamation_area"}


def build_index(key):
    url, fields, nf, *extra = LAYERS[key]
    where = extra[0] if extra else "1=1"
    alt = extra[1] if len(extra) > 1 else None
    feats = arcgis_layer(url, CACHE_NAME.get(key, "layer_" + key), fields, where=where,
                         offset=0.01 if key in ("USFS", "BLM_FO") else 0.003, page=PAGE.get(key, 200))
    if nf is None:  # pick the name field
        cand = [k for k in feats[0]["a"] if re.search(r"name", k, re.I)]
        nf = cand[0]
    groups = defaultdict(list)
    for f in feats:
        g = f["g"]
        g = None if not g else (esri_to_shape(g) if "rings" in g else shape(g))
        if g is not None and not g.is_valid:
            g = make_valid(g)
        nm = f["a"].get(nf)
        if g is None or not nm:
            continue
        groups[nkey(str(nm))].append((str(nm), g))
        an = f["a"].get(alt) if alt else None
        if an and nkey(str(an)) != nkey(str(nm)):
            groups[nkey(str(an))].append((str(an), g))
    idx = {}
    for k, lst in groups.items():
        try:
            geom = unary_union([g for _, g in lst])
        except Exception:  # noqa: BLE001
            geom = unary_union([make_valid(g).buffer(0) for _, g in lst])
        idx[k] = (lst[0][0], geom)
    return idx


def federal(C):
    recs = ficor_table_a()
    idx = {k: build_index(k) for k in ("USFS", "BLM_FO", "BLM_NLCS", "FWS", "USACE", "Reclamation")}
    county = defaultdict(float)
    county_sites = defaultdict(int)
    state = defaultdict(lambda: defaultdict(float))
    sites, stats = [], defaultdict(lambda: [0, 0, 0, 0])  # n, matched, visits, matched visits
    for r in recs:
        ag, v = r["agency"], r["visits"]
        if ag == "NPS" or not v:
            continue
        hit = None
        if ag == "BLM":
            if re.search(r"(Field|District) Office", r["unit"]):
                hit = match_parts(r["unit"], idx["BLM_FO"], 0.88)
            else:
                hit = match_parts(r["unit"], idx["BLM_NLCS"], 0.88) or match_parts(r["unit"], idx["BLM_FO"], 0.88)
        elif ag == "USFS":
            hit = match_parts(r["unit"], idx["USFS"], 0.88)
        elif ag == "FWS":
            hit = match_parts(r["unit"], idx["FWS"], 0.9)
        elif ag == "USACE":
            hit = match_parts(re.sub(r"( - MV[A-Z]|, [A-Z]{2})$", "", r["unit"]), idx["USACE"], 0.9)
        elif ag == "Reclamation":
            hit = match_parts(r["unit"], idx["Reclamation"], 0.88)
        s = stats[ag]
        s[0] += 1
        s[2] += v
        st_guess = STATES.get(r.get("filter") or "")
        if hit is None:
            if st_guess:
                state[st_guess]["federal_land_visits_unlocated"] += v
            continue
        s[1] += 1
        s[3] += v
        shares = C.split(hit[1])
        if not shares:
            continue
        for f, w in shares.items():
            county[f] += v * w
            state[FIPS_ST.get(f[:2], "?")]["federal_land_visits"] += v * w
        top = max(shares, key=shares.get)
        for f in shares:
            county_sites[f] += 1
        rp = hit[1].representative_point()
        sites.append({"name": r["unit"], "kind": KIND[ag], "agency": ag, "lon": round(rp.x, 4), "lat": round(rp.y, 4),
                      "visits": v, "year": 2024, "fips": top, "counties_n": len(shares),
                      "source": "FICOR FY2024 Interagency Recreation Visitation Report, Table A"})
    return county, county_sites, state, sites, stats, recs


# ------------------------------------------------------------------ 7. NY State Parks
NY_ATT = "https://data.ny.gov/resource/8f3n-xj78.json"
NY_PTS = "https://data.ny.gov/resource/9uuk-x7vh.json"


def ny_parks(C):
    yr = requests.get(NY_ATT, params={"$select": "max(year)"}, headers=UA, timeout=120).json()[0]["max_year"]
    att = jload(fetch(NY_ATT, "ny_parks_att_%s.json" % yr, params={"year": yr, "$limit": 5000}))
    pts = jload(fetch(NY_PTS, "ny_parks_points.json", params={"$limit": 5000}))
    pidx = {}
    for p in pts:
        if p.get("x") and p.get("y"):
            pidx[nkey(p["name"])] = p
    out = []
    for a in att:
        v = a.get("attendance")
        if not v:
            continue
        v = int(float(v))
        f = C.by_name.get((norm_county(a.get("county") or ""), "NY"))
        p = match(a["facility"], pidx, cutoff=0.8)
        if p is None:  # "Allegany Quaker Area" -> "Allegany"
            first = nkey(a["facility"]).split(" ")[0] if nkey(a["facility"]) else ""
            p = pidx.get(first)
        lon, lat = (float(p["x"]), float(p["y"])) if p else (C.meta[f]["lon"], C.meta[f]["lat"]) if f else (None, None)
        if p and f is None:
            f = C.point(lon, lat)
        out.append({"name": a["facility"], "kind": "state_park", "lon": round(lon, 4) if lon else None,
                    "lat": round(lat, 4) if lat else None, "visits": v, "year": int(yr), "fips": f,
                    "located": "facility point" if p else "county centroid",
                    "source": "NY OPRHP State Park Annual Attendance (data.ny.gov 8f3n-xj78)"})
    return out, int(yr)


# ------------------------------------------------------------------ licences / rejected
SOURCES = [
    {"id": "ntto_states", "name": "NTTO Overseas Visitor Impact on State Economies 2024 (Table 1: visits, spending, jobs)",
     "url": NTTO_STATE_PDF, "licence": "US federal government work (17 U.S.C. 105) - public domain",
     "quote": "trade.gov: works of the US Government are not subject to copyright (17 U.S.C. 105); NTTO data published for public use"},
    {"id": "ntto_siat", "name": "NTTO SIAT Inbound Market Profiles 2024 - Vacation and Business (destinations visited %)",
     "url": NTTO_PROFILE.format("Vacation") + " ; " + NTTO_PROFILE.format("Business"),
     "licence": "US federal government work - public domain", "quote": "U.S. Department of Commerce, ITA, National Travel and Tourism Office"},
    {"id": "bea_orsa", "name": "BEA Outdoor Recreation Satellite Account, U.S. and States (ores1126-state.xlsx)", "url": ORSA_STATE,
     "licence": "Public domain", "quote": "BEA: 'BEA data are in the public domain and may be used without restriction' (bea.gov/help/guidelines-for-citing-bea)"},
    {"id": "bea_ttsa", "name": "BEA Travel and Tourism Satellite Account 2023 (national only)", "url": TTSA_2023,
     "licence": "Public domain", "quote": "as above (bea.gov)"},
    {"id": "bts_border", "name": "BTS Border Crossing Entry Data (CBP), inbound persons by port & month", "url": "https://data.bts.gov/d/keg4-3bc2",
     "licence": "Public Domain", "quote": "data.bts.gov dataset licence field: 'Public Domain'; attribution 'U.S. Department of Homeland Security, Customs and Border Protection'"},
    {"id": "bts_cruise", "name": "BTS/MARAD Top 25 Ports by Cruise Ship Passenger Arrivals 2023", "url": "https://data.bts.gov/d/3z7h-xatu",
     "licence": "US federal government work - public domain",
     "quote": "Citation: 'U.S. Maritime Administration Analysis of Data from Customs and Border Protection Vessel Entrance and Clearance System'"},
    {"id": "ficor", "name": "Federal Interagency Council on Outdoor Recreation - FY2024 Interagency Recreation Visitation Data Report (Table A)",
     "url": FICOR, "licence": "US federal government work - public domain", "quote": "Report published on ridb.recreation.gov by DOI/USDA/USACE (EXPLORE Act)"},
    {"id": "usfs_edw", "name": "USFS EDW Administrative Forest Boundaries", "url": LAYERS["USFS"][0], "licence": "Public domain (USDA)",
     "quote": "USFS EDW data are public information and may be freely distributed"},
    {"id": "blm_admu", "name": "BLM National Administrative Unit Field Office boundaries + NLCS NM/NCA polygons",
     "url": LAYERS["BLM_FO"][0] + " ; " + LAYERS["BLM_NLCS"][0], "licence": "Public domain (DOI)", "quote": "BLM geospatial data: no restrictions on use"},
    {"id": "fws_bnd", "name": "FWS National Wildlife Refuge System Boundaries", "url": LAYERS["FWS"][0], "licence": "Public domain (DOI)",
     "quote": "U.S. Fish and Wildlife Service, National Wildlife Refuge System, Division of Realty"},
    {"id": "usace_rec", "name": "USACE Recreation Areas (CRREL)", "url": LAYERS["USACE"][0], "licence": "Public domain (US Army)", "quote": "USACE work product"},
    {"id": "padus_usbr", "name": "USGS PAD-US 4.1 Manager Name layer, Bureau of Reclamation lands (Mang_Name or Own_Name = 'USBR')",
     "url": LAYERS["Reclamation"][0] + " ; https://www.sciencebase.gov/catalog/item/6759abcfd34edfeb8710a004", "licence": "Public domain (USGS)",
     "quote": "ScienceBase PAD-US 4.1 release, rights field: 'Public Domain.' (USGS data release; attribution: U.S. Geological Survey (USGS) GAP, PAD-US 4.1)"},
    {"id": "ny_parks", "name": "NY OPRHP State Park Annual Attendance by Facility + State Park Facility Points",
     "url": "https://data.ny.gov/d/8f3n-xj78 ; https://data.ny.gov/d/9uuk-x7vh",
     "licence": "NY Open Data Terms of Use - free, commercial use allowed",
     "quote": "data.ny.gov Terms of Use: data 'may be used ... for any lawful purpose', including commercial (open-data-handbook ToU)"},
]
REJECTED = [
    {"name": "Reclamation Recreation Management Areas (ArcGIS owner Reclamation_Public)", "url": "https://www.arcgis.com/home/item.html?id= (Reclamation RMA item)",
     "reason": "Item licenseInfo: 'Reclamation does not authorize downloading or republishing, deriving new data products, or the dissemination "
               "of Reclamation's data into other systems or by others.' (red) - replaced by USGS PAD-US 4.1 USBR lands (public domain)."},
    {"name": "Amtrak station ridership (state fact sheets)", "url": "https://www.amtrak.com/state-fact-sheets",
     "reason": "Amtrak Web Notices & Terms of Use: Materials 'are and will remain the property of Amtrak'; 'you may not copy, reproduce, republish ... or distribute the Materials ... without our prior written permission'. Amtrak is not a federal agency (works not public domain)."},
    {"name": "Rail Passengers Association station boarding fact sheets", "url": "https://www.railpassengers.org/",
     "reason": "'Copyright National Association of Railroad Passengers' - no open licence."},
    {"name": "NTTO States & Cities Visited Monitor (Power BI)", "url": "https://www.trade.gov/us-states-cities-visited-overseas-travelers",
     "reason": "Not rejected on licence (public domain) - only reachable via an interactive Power BI export; city totals derived from the downloadable SIAT profiles instead."},
    {"name": "BEA state-level TTSA", "url": "https://www.bea.gov/data/special-topics/travel-and-tourism",
     "reason": "Not published - BEA TTSA is national only; tourism_output/tourism_jobs left null, ORSA state outdoor-recreation used as the state-level proxy (separate fields)."},
    {"name": "Texas / Minnesota / Colorado / Ohio / Oklahoma / Missouri / Alabama state park attendance", "url": "",
     "reason": "No machine-readable open-data release with an explicit commercial-reuse licence found (figures appear only in press releases / news). Gap."},
    {"name": "Private DMO/consultant reports (Tourism Economics, Longwoods, Dean Runyan, Destination Analysts, STR/CoStar, Visa/Mastercard, Placer.ai)",
     "url": "", "reason": "Copyrighted/proprietary; no commercial-reuse grant. Not used."},
    {"name": "NASPD AIX state park annual statistics", "url": "https://www.naspd.org/",
     "reason": "Association publication, copyright, member/paid distribution."},
    {"name": "FWS per-refuge visitor surveys (USGS DS 643/754)", "url": "https://pubs.usgs.gov/ds/643",
     "reason": "Public domain but 2010-2012 survey samples, not visit totals; superseded by FICOR FY2024 Table A."},
]


# ------------------------------------------------------------------ main
def main():
    C = Counties()
    log("NTTO states")
    nst = ntto_states()
    log("  states", len(nst))
    cities = ntto_cities(nst)
    log("  cities", len(cities))
    log("BEA")
    orsa, ttsa, orsa_title = bea()
    log("border crossings")
    ports, b_start, b_end = border()
    log("cruise")
    cr = cruise(C)
    log("federal lands")
    fcounty, fsites_n, fstate, fsites, fstats, recs = federal(C)
    log("NY parks")
    ny, ny_year = ny_parks(C)

    counties = defaultdict(lambda: {"federal_land_visits": None, "border_crossings_12m": None, "cruise_pax": None,
                                    "amtrak_riders": None, "state_park_visits": None, "sites_n": 0})
    sites = list(fsites)

    def add(f, k, v):
        c = counties[f]
        c[k] = (c[k] or 0) + v

    for f, v in fcounty.items():
        add(f, "federal_land_visits", v)
    for f, n in fsites_n.items():
        counties[f]["sites_n"] += n
    st_border = defaultdict(int)
    for code, p in ports.items():
        stc = STATES.get(p["state"])
        f = C.point(p["lon"], p["lat"]) if p["lon"] is not None else None
        if stc:
            st_border[stc] += p["v"]
        if f:
            add(f, "border_crossings_12m", p["v"])
            counties[f]["sites_n"] += 1
        sites.append({"name": "%s (%s) border port" % (p["name"], p["state"]), "kind": "border_port", "lon": p["lon"],
                      "lat": p["lat"], "visits": p["v"], "year": "%s..%s" % (b_start[:7], b_end[:7]), "fips": f,
                      "by_mode": p["by"], "border": p["border"], "source": "BTS Border Crossing Entry Data (inbound persons)"})
    st_cruise = defaultdict(int)
    for c in cr:
        st_cruise[c["state"]] += c["visits"]
        if c["fips"]:
            add(c["fips"], "cruise_pax", c["visits"])
            counties[c["fips"]]["sites_n"] += 1
        sites.append({"name": c["name"] + " cruise port", "kind": "cruise_port", "lon": c["lon"], "lat": c["lat"],
                      "visits": c["visits"], "year": 2023, "fips": c["fips"],
                      "source": "BTS/MARAD Top 25 cruise ports by passenger arrivals 2023"})
    st_park = defaultdict(int)
    for s in ny:
        st_park["NY"] += s["visits"]
        if s["fips"]:
            add(s["fips"], "state_park_visits", s["visits"])
            counties[s["fips"]]["sites_n"] += 1
        sites.append(s)

    for c in counties.values():
        for k in ("federal_land_visits",):
            if c[k] is not None:
                c[k] = int(round(c[k]))
    states = {}
    ranked = sorted(nst, key=lambda s: -nst[s]["overseas_visits"])
    for st in sorted(set(STATES.values())):
        n = nst.get(st, {})
        o = orsa.get(st, {})
        states[st] = {"overseas_visits": n.get("overseas_visits"),
                      "overseas_rank": ranked.index(st) + 1 if st in nst else None,
                      "overseas_spend_musd": n.get("overseas_spend_musd"), "overseas_jobs": n.get("overseas_jobs"),
                      "overseas_low_sample": n.get("overseas_low_sample"),
                      "tourism_output": None, "tourism_jobs": None,
                      "outdoor_rec_va_kusd": o.get("outdoor_rec_va_kusd"), "outdoor_rec_jobs": o.get("outdoor_rec_jobs"),
                      "outdoor_rec_year": o.get("outdoor_rec_year"),
                      "border_crossings_12m": st_border.get(st) or None,
                      "cruise_pax": st_cruise.get(st) or None,
                      "federal_land_visits": int(round(fstate[st]["federal_land_visits"])) if st in fstate and fstate[st]["federal_land_visits"] else None,
                      "federal_land_visits_unlocated": int(fstate[st]["federal_land_visits_unlocated"]) if st in fstate and fstate[st]["federal_land_visits_unlocated"] else None,
                      "state_park_visits": st_park.get(st) or None}
    for s in sites:
        if s.get("lon") is not None:
            s["lon"], s["lat"] = round(s["lon"], 4), round(s["lat"], 4)
    sites.sort(key=lambda s: -(s["visits"] or 0))

    cov = {ag: {"units_with_visits": v[0], "units_located": v[1], "visits": v[2], "visits_located": v[3]}
           for ag, v in fstats.items()}
    meta = {
        "generated": date.today().isoformat(),
        "note": "Aggregate visit counts only (no individuals). NPS park visits, airport passengers and I-94 by state are in "
                "tourism_air.json (lane T1); hotel tax, QCEW lodging, STR registries, Wikipedia views in tourism_econ.json (lane T2).",
        "fields": {
            "states.overseas_visits": "NTTO 2024 overseas (excl. Canada & Mexico) visitors to the state (persons; a visitor can count in several states)",
            "states.overseas_low_sample": "NTTO asterisk: <100 SIAT respondents, use with caution",
            "states.tourism_output/tourism_jobs": "null - BEA publishes TTSA nationally only (see meta.national)",
            "states.outdoor_rec_*": "BEA ORSA state outdoor recreation value added (thousand $) and employment",
            "cities.overseas_visits": "derived: NTTO state total x (city share / state share) from SIAT Vacation+Business 2024 profiles, weighted by segment volume",
            "counties.federal_land_visits": "FICOR FY2024 visits for BLM, USFS, USACE, FWS, Reclamation units, split to counties by unit-area overlap (NPS excluded - lane T1)",
            "counties.border_crossings_12m": "inbound persons (personal-vehicle + bus passengers, pedestrians, train passengers) last 12 months at land ports",
            "counties.cruise_pax": "2023 cruise passenger arrivals (top 25 ports only)",
            "counties.amtrak_riders": "null - Amtrak ridership terms forbid reuse (see rejected)",
            "counties.state_park_visits": "state park attendance where the state publishes open data (currently NY only)",
        },
        "national": {"ttsa": ttsa, "overseas_visitors_2024": 35_159_000, "federal_visits_fy2024_by_agency": {
            "BLM": 80_760_858, "Reclamation": 38_771_919, "FWS": 70_697_349, "NPS": 332_635_237, "USACE": 260_477_042, "USFS": 164_169_098}},
        "periods": {"border": [b_start, b_end], "ny_parks_year": ny_year, "federal": "FY2024", "ntto": 2024,
                    "cruise": 2023, "orsa": orsa_title},
        "federal_coverage": cov,
        "sources": SOURCES,
        "attribution": "Sources: U.S. Dept. of Commerce ITA/NTTO; U.S. Bureau of Economic Analysis; Bureau of Transportation "
                       "Statistics (CBP, MARAD); Federal Interagency Council on Outdoor Recreation (BLM, USFS, USACE, FWS, "
                       "Reclamation); NY State Office of Parks, Recreation and Historic Preservation via data.ny.gov.",
        "rejected": REJECTED,
    }
    out = {"meta": meta, "states": states, "cities": cities, "sites": sites,
           "counties": {k: counties[k] for k in sorted(counties)}}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    log("wrote", OUT, os.path.getsize(OUT) // 1024, "KB", "| sites", len(sites), "| counties", len(counties))
    for ag, c in cov.items():
        log("  %-12s units %4d located %4d  visits %12s located %12s" % (ag, c["units_with_visits"], c["units_located"],
                                                                          f"{c['visits']:,}", f"{c['visits_located']:,}"))


if __name__ == "__main__":
    main()

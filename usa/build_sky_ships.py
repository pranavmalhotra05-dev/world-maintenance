"""One day of real ship positions and tracks in US waters, for the globe's sky/sea layer.

    .venv/Scripts/python -u -W ignore -m usa.build_sky_ships [YYYY-MM-DD]

Source: MarineCadastre.gov "Vessel Traffic (AIS)" daily point files (BOEM + NOAA Office for Coastal Management,
from the U.S. Coast Guard national AIS network), https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/AIS_{y}_{m}_{d}.zip
Licence: US Government work, public domain. The item's licence text: "The information on government web pages is in
the public domain and not subject to copyright protection within the United States unless specifically annotated
otherwise" (https://hub.marinecadastre.gov/documents/4a1d5c56ceb94fe2a656bf53eaece8d3). 17 U.S.C. 403 notice is kept in
the output's attribution. The newest published day (checked 2026-11-26) is 2024-12-31; the 2025 folder does not exist yet.

Privacy: pleasure craft (37), sailing (36), military (35), law enforcement (55), "other"/unknown/reserved types
(0-29, 90-99, missing) are dropped entirely. MMSI, IMO, call sign and vessel name are never written.

Size: tracks keep only real reported positions; per vessel a point is dropped when it is less than 30 s after the
previous kept point, or when its position (rounded to 4 decimals, ~11 m) is identical to the previous kept point
(a ship lying still at a berth). The whole-US day is ~68 MB that way, so "ships" (last positions) stay nationwide but
"tracks" are limited to the top US port/coastal regions in REGIONS, added in priority order while the file stays under
BUDGET; a track is cut where it leaves a region (no line is drawn across a gap). The regions used are written to
"trackRegions".

Output web/app/data/sky/ships.json:
  {generated, data_date, source, licence, licenceUrl, attribution, privacy, fields, trackThinning,
   ships: [[lon, lat, sog_kn, cog_deg, type_group, length_m|null, "YYYY-MM-DDTHH:MM"], ...],
   tracks: [{type_group, path: [[lon, lat, "HH:MM"], ...]}, ...]}
"""
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "ships"
OUT = ROOT / "web" / "app" / "data" / "sky" / "ships.json"
URL = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{y}/AIS_{y}_{m}_{d}.zip"
DEFAULT_DAY = "2024-12-31"
BUDGET = 15_000_000        # bytes
# (name, lon0, lat0, lon1, lat1) in priority order: largest US port complexes first
REGIONS = [
    ("Los Angeles / Long Beach", -118.75, 33.30, -117.70, 33.95),
    ("Houston / Galveston", -95.40, 28.90, -94.40, 29.85),
    ("New York / New Jersey", -74.35, 40.35, -73.65, 40.95),
    ("San Francisco Bay", -122.75, 37.40, -121.95, 38.20),
    ("Puget Sound (Seattle / Tacoma)", -123.20, 47.00, -122.15, 48.45),
    ("Savannah", -81.25, 31.85, -80.70, 32.20),
    ("Chesapeake (Norfolk / Baltimore)", -77.05, 36.80, -75.85, 39.35),
    ("Lower Mississippi (New Orleans)", -91.30, 28.90, -89.00, 30.60),
    ("Delaware River (Philadelphia)", -75.65, 38.75, -74.85, 40.10),
    ("Miami / Port Everglades", -80.35, 25.60, -79.95, 26.20),
    ("Charleston", -80.05, 32.65, -79.70, 32.95),
    ("Columbia River (Portland)", -124.20, 45.55, -122.60, 46.35),
    ("Boston", -71.10, 42.20, -70.70, 42.45),
    ("Corpus Christi", -97.50, 27.55, -96.95, 27.95),
    ("Tampa Bay", -82.85, 27.45, -82.35, 28.00),
    ("Jacksonville", -81.70, 30.25, -81.30, 30.45),
    ("Honolulu", -158.20, 21.20, -157.75, 21.40),
    ("San Diego", -117.35, 32.55, -117.05, 32.80),
]
COLS = ["MMSI", "BaseDateTime", "LAT", "LON", "SOG", "COG", "VesselType", "Length"]


def type_group(t):
    if t != t:  # NaN
        return None
    t = int(t)
    if t == 30 or t in (1001, 1002):
        return "fishing"
    if t in (31, 32, 52) or t in (1023, 1025):
        return "tug"
    if 60 <= t <= 69 or 1012 <= t <= 1015:
        return "passenger"
    if 70 <= t <= 79 or t in (1003, 1004, 1016):
        return "cargo"
    if 80 <= t <= 89 or t in (1017, 1024):
        return "tanker"
    if t in (33, 34, 50, 51, 53, 54, 58, 59) or 40 <= t <= 49:
        return "other commercial"   # dredging, diving, pilot, SAR, port tender, anti-pollution, medical, high-speed craft
    return None                     # 0-29, 35 military, 36 sailing, 37 pleasure, 55 law enforcement, 90-99, other


def fetch(day):
    y, m, d = day.split("-")
    CACHE.mkdir(parents=True, exist_ok=True)
    z = CACHE / f"AIS_{y}_{m}_{d}.zip"
    if not z.exists():
        print("downloading", URL.format(y=y, m=m, d=d))
        req = urllib.request.Request(URL.format(y=y, m=m, d=d), headers={"User-Agent": "world-maintenance-sky/1.0"})
        with urllib.request.urlopen(req, timeout=600) as r, open(z.with_suffix(".part"), "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        z.with_suffix(".part").rename(z)
    return z


def load(z):
    with zipfile.ZipFile(z) as zf:
        name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        with zf.open(name) as f:
            df = pd.read_csv(f, usecols=COLS, dtype={"MMSI": "int64", "LAT": "float64", "LON": "float64", "SOG": "float32",
                                                     "COG": "float32", "VesselType": "float32", "Length": "float32"})
    print(f"  {len(df):,} AIS rows, {df.MMSI.nunique():,} vessels")
    return df


def prepare(df):
    # one type per vessel: the most common non-null reported type that day
    vt = df.dropna(subset=["VesselType"]).groupby("MMSI")["VesselType"].agg(lambda s: s.mode().iloc[0])
    grp = vt.map(type_group).dropna()
    df = df[df.MMSI.isin(grp.index)].copy()
    df["grp"] = df.MMSI.map(grp)
    df = df[df.LAT.between(-90, 90) & df.LON.between(-180, 180)]
    df["t"] = pd.to_datetime(df.BaseDateTime, format="%Y-%m-%dT%H:%M:%S")
    df = df.sort_values(["MMSI", "t"]).reset_index(drop=True)
    df["lon4"] = df.LON.round(4)
    df["lat4"] = df.LAT.round(4)
    print(f"  kept {len(df):,} rows, {df.MMSI.nunique():,} commercial vessels; by type: "
          + ", ".join(f"{k} {v}" for k, v in grp[grp.index.isin(df.MMSI.unique())].value_counts().items()))
    return df


def thin(df, gap_s):
    """Per vessel keep a point only if >= gap_s after the previous KEPT point and not at the same rounded position."""
    mm = df.MMSI.to_numpy()
    ts = df.t.to_numpy().astype("datetime64[s]").astype("int64")
    lo = df.lon4.to_numpy()
    la = df.lat4.to_numpy()
    keep = np.zeros(len(df), bool)
    last_m, last_t, last_lo, last_la = -1, 0, 0.0, 0.0
    for i in range(len(df)):
        if mm[i] != last_m:
            keep[i] = True
            last_m, last_t, last_lo, last_la = mm[i], ts[i], lo[i], la[i]
            continue
        if ts[i] - last_t < gap_s or (lo[i] == last_lo and la[i] == last_la):
            continue
        keep[i] = True
        last_t, last_lo, last_la = ts[i], lo[i], la[i]
    return df[keep]


def num(x, nd):
    if x is None or x != x:
        return None
    v = round(float(x), nd)
    return int(v) if v == int(v) else v


def segments(g, boxes):
    """Split one vessel's points into runs that stay inside one of the boxes."""
    lo, la = g.lon4.to_numpy(), g.lat4.to_numpy()
    inside = np.zeros(len(g), bool)
    for _, x0, y0, x1, y1 in boxes:
        inside |= (lo >= x0) & (lo <= x1) & (la >= y0) & (la <= y1)
    out, start = [], None
    for i, v in enumerate(list(inside) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= 2:
                out.append((start, i))
            start = None
    return out


def build(df, day, gap_s, boxes):
    last = df.groupby("MMSI").tail(1)
    ships = []
    for r in last.itertuples(index=False):
        sog = None if r.SOG != r.SOG or r.SOG >= 102.2 else num(r.SOG, 1)
        cog = None if r.COG != r.COG or r.COG >= 360 or r.COG < 0 else num(r.COG, 1)
        ln = None if r.Length != r.Length or r.Length <= 0 else int(round(float(r.Length)))
        ships.append([num(r.lon4, 4), num(r.lat4, 4), sog, cog, r.grp, ln, r.t.strftime("%Y-%m-%dT%H:%M")])
    tdf = thin(df, gap_s)
    tracks = []
    for _, g in tdf.groupby("MMSI", sort=False):
        if len(g) < 2:
            continue
        hm = g.t.dt.strftime("%H:%M").tolist()
        los, las = g.lon4.tolist(), g.lat4.tolist()
        for a0, a1 in segments(g, boxes):
            tracks.append({"type_group": g.grp.iloc[0],
                           "path": [[num(los[i], 4), num(las[i], 4), hm[i]] for i in range(a0, a1)]})
    npts = sum(len(t["path"]) for t in tracks)
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data_date": day,
        "source": "MarineCadastre.gov Vessel Traffic (AIS) daily points, BOEM + NOAA Office for Coastal Management, "
                  "from the U.S. Coast Guard national AIS network",
        "licence": "US Government work - public domain",
        "licenceUrl": "https://hub.marinecadastre.gov/documents/4a1d5c56ceb94fe2a656bf53eaece8d3",
        "attribution": "Ship positions: MarineCadastre.gov (BOEM, NOAA OCM, U.S. Coast Guard). U.S. Government material, "
                       "not subject to copyright protection within the United States (17 U.S.C. 403).",
        "privacy": "Pleasure craft, sailing, military, law-enforcement and other/unknown-type vessels removed entirely; "
                   "no MMSI, IMO, call sign or vessel name stored.",
        "fields": {"ships": ["lon", "lat", "sog_kn", "cog_deg", "type_group", "length_m", "last_report_utc"],
                   "tracks": ["lon", "lat", "HH:MM UTC"]},
        "trackThinning": f"real AIS reports only; per vessel a point is dropped if < {gap_s} s after the previous kept point "
                         f"or at the identical position (4-decimal, ~11 m); coordinates rounded to 4 decimals",
        "trackRegions": [{"name": n, "bbox": [x0, y0, x1, y1]} for n, x0, y0, x1, y1 in boxes],
        "counts": {"ships": len(ships), "tracks": len(tracks), "trackPoints": npts},
        "ships": ships,
        "tracks": tracks,
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else DEFAULT_DAY
    z = fetch(day)
    df = prepare(load(z))
    s = None
    for k in range(1, len(REGIONS) + 1):
        out = build(df, day, 30, REGIONS[:k])
        t = json.dumps(out, separators=(",", ":"))
        print(f"  {k} regions (+{REGIONS[k - 1][0]}): {out['counts']}, {len(t) / 1e6:.1f} MB")
        if len(t) > BUDGET and s is not None:
            break
        s = t
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(s, encoding="utf-8")
    print("wrote", OUT, f"{len(s) / 1e6:.1f} MB")
    if "--keep" not in sys.argv:
        z.unlink()
        print("deleted", z)


if __name__ == "__main__":
    main()

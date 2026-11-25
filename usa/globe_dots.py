"""Build the 'dot globe' surface for web/usa/globe.html.

Every dot is a real sample: NOAA ETOPO 2022 (60 arc-second, ice-surface elevation, CC0-1.0) on an
equal-area grid, plus the NASA Black Marble 2016 night-lights brightness at the same spot.

  world grid : land every ~0.30 deg, ocean every ~0.80 deg (equal-area rows), US states removed
  USA grid   : every ~0.055 deg inside the 50 states + DC + PR (states.geojson), with a lookup to
               county (counties.geojson index), census tract (tracts/<ST>.json GEOID) and a flag for
               USGS 3DEP EPT LiDAR coverage (data/national/ept_boundaries.geojson)

Output (web/usa/data/earth/):
  dots_world.bin, dots_usa.bin   little-endian struct-of-arrays, layout in dots_meta.json
  dots_meta.json                 counts, byte offsets, scales, county FIPS list, sources + licences

Usage:  python usa/globe_dots.py <ETOPO_2022_v1_60s_N90W180_surface.tif> [--land 0.30 --ocean 0.80 --usa 0.055]
The GeoTIFF (466 MB) is downloaded from
https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif
"""
import argparse
import glob
import json
import os
import time

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
import shapely
from shapely.geometry import shape
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web", "usa", "data")
OUT = os.path.join(WEB, "earth")
Q_LON, Q_LAT = 180.0 / 32767, 90.0 / 32767


def ea_grid(step, lat0=-90.0, lat1=90.0):
    """Equal-area grid: rows every `step` deg of latitude, columns every step/cos(lat)."""
    lons, lats = [], []
    lat = -90.0 + step / 2
    while lat < 90.0:
        if lat0 <= lat <= lat1:
            n = max(1, int(round(360.0 * np.cos(np.radians(lat)) / step)))
            lons.append(-180.0 + (np.arange(n) + 0.5) * 360.0 / n)
            lats.append(np.full(n, lat))
        lat += step
    return np.concatenate(lons), np.concatenate(lats)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("etopo")
    ap.add_argument("--land", type=float, default=0.30)
    ap.add_argument("--ocean", type=float, default=0.80)
    ap.add_argument("--usa", type=float, default=0.055)
    a = ap.parse_args()

    src = rasterio.open(a.etopo)
    # ---- coarse global elevation (0.1 deg, area average) for the world grid
    log("reading coarse ETOPO")
    coarse = src.read(1, out_shape=(1800, 3600), resampling=Resampling.average).astype(np.float32)

    def coarse_at(lon, lat):
        c = np.clip(((lon + 180) / 0.1).astype(int), 0, 3599)
        r = np.clip(((90 - lat) / 0.1).astype(int), 0, 1799)
        return coarse[r, c]

    # ---- night lights (NASA Black Marble 2016, 0.1 deg)
    bm = np.asarray(Image.open(os.path.join(OUT, "black_marble_2016_3600.jpg")).convert("L"), dtype=np.uint8)

    def light_at(lon, lat):
        h, w = bm.shape
        c = np.clip(((lon + 180) / 360 * w).astype(int), 0, w - 1)
        r = np.clip(((90 - lat) / 180 * h).astype(int), 0, h - 1)
        return bm[r, c]

    # ---- US states
    st = json.load(open(os.path.join(WEB, "states.geojson")))
    states = [(f["properties"]["f"], shape(f["geometry"])) for f in st["features"]]
    for _, g in states:
        shapely.prepare(g)

    def in_states(lon, lat):
        """index of the containing state (or -1); lon in -180..180"""
        idx = np.full(lon.shape, -1, np.int16)
        for i, (_, g) in enumerate(states):
            x0, y0, x1, y1 = g.bounds
            m = (idx < 0) & (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
            if m.any():
                w = np.where(m)[0]
                hit = shapely.contains_xy(g, lon[w], lat[w])
                idx[w[hit]] = i
        return idx

    # ---- world dots
    log("world grid")
    lo_l, la_l = ea_grid(a.land)
    e_l = coarse_at(lo_l, la_l)
    keep = e_l > 0
    lo_l, la_l, e_l = lo_l[keep], la_l[keep], e_l[keep]
    lo_o, la_o = ea_grid(a.ocean)
    e_o = coarse_at(lo_o, la_o)
    keep = e_o <= 0
    lo_o, la_o, e_o = lo_o[keep], la_o[keep], e_o[keep]
    wl, wa, we = np.concatenate([lo_l, lo_o]), np.concatenate([la_l, la_o]), np.concatenate([e_l, e_o])
    inside = in_states(wl, wa) >= 0
    land_in_us = inside & (we > 0)          # US land is replaced by the fine grid; US waters stay
    wl, wa, we = wl[~land_in_us], wa[~land_in_us], we[~land_in_us]
    log("world dots", len(wl), "land", int((we > 0).sum()), "ocean", int((we <= 0).sum()))

    # ---- USA dots: fine equal-area grid inside the states
    log("usa grid")
    ul, ua = ea_grid(a.usa, 17.0, 72.0)
    pre = np.zeros(ul.shape, bool)
    for _, g in states:
        x0, y0, x1, y1 = g.bounds
        pre |= (ul >= x0) & (ul <= x1) & (ua >= y0) & (ua <= y1)
    ul, ua = ul[pre], ua[pre]
    sidx = in_states(ul, ua)
    ul, ua, sidx = ul[sidx >= 0], ua[sidx >= 0], sidx[sidx >= 0]
    log("usa dots", len(ul))

    # full-res elevation for the US: read the latitude band 17..72 N once, 3x3 mean around each dot
    r0 = int((90 - 72) * 60)
    r1 = int((90 - 17) * 60)
    log("reading ETOPO rows", r0, r1)
    band = src.read(1, window=Window(0, r0, src.width, r1 - r0)).astype(np.float32)
    band[band < -20000] = np.nan
    c = np.clip(((ul + 180) * 60).astype(int), 1, src.width - 2)
    r = np.clip(((90 - ua) * 60).astype(int) - r0, 1, band.shape[0] - 2)
    acc = np.zeros(len(ul), np.float64)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            acc += band[r + dr, c + dc]
    ue = (acc / 9).astype(np.float32)
    ue = np.where(np.isfinite(ue), ue, band[r, c])
    del band

    # county lookup
    log("county lookup")
    cg = json.load(open(os.path.join(WEB, "counties.geojson")))
    cfips = [f["properties"]["f"] for f in cg["features"]]
    cgeoms = [shape(f["geometry"]) for f in cg["features"]]
    pts = shapely.points(ul, ua)
    tree = shapely.STRtree(cgeoms)
    pi, gi = tree.query(pts, predicate="within")
    ucounty = np.full(len(ul), 0xFFFF, np.uint16)
    ucounty[pi] = gi
    # dots in a coastal sliver with no county polygon: nearest county
    miss = np.where(ucounty == 0xFFFF)[0]
    if len(miss):
        near = tree.nearest(pts[miss])
        ucounty[miss] = near
    log("county miss filled by nearest", len(miss))

    # tract lookup (tracts/<ST>.json, 2020 tracts)
    log("tract lookup")
    tgeoid, tgeom = [], []
    for fn in sorted(glob.glob(os.path.join(WEB, "tracts", "*.json"))):
        if fn.endswith("index.json"):
            continue
        d = json.load(open(fn))
        for t in d.get("tracts", []):
            g = t.get("geom")
            if not g:
                continue
            try:
                tgeom.append(shapely.MultiPolygon([shapely.Polygon(p[0], p[1:]) for p in g]))
                tgeoid.append(t["g"])
            except Exception:
                pass
    ttree = shapely.STRtree(tgeom)
    pi, gi = ttree.query(pts, predicate="within")
    utst = np.zeros(len(ul), np.uint8)
    utract = np.full(len(ul), 0xFFFFFFFF, np.uint32)
    for p, g in zip(pi, gi):
        s = tgeoid[g]
        utst[p] = int(s[:2])
        utract[p] = int(s[2:])
    log("tracts", len(tgeoid), "dots with tract", int((utract != 0xFFFFFFFF).sum()))

    # 3DEP EPT coverage
    log("EPT coverage")
    eg = json.load(open(os.path.join(ROOT, "data", "national", "ept_boundaries.geojson")))
    egeoms = [shape(f["geometry"]) for f in eg["features"] if f.get("geometry")]
    etree = shapely.STRtree(egeoms)
    pi, _ = etree.query(pts, predicate="within")
    uflag = np.zeros(len(ul), np.uint8)
    uflag[np.unique(pi)] |= 1
    log("dots in EPT coverage", int((uflag & 1).sum()))

    os.makedirs(OUT, exist_ok=True)

    def q(lon, lat):
        lon = ((lon + 180) % 360) - 180
        return np.round(lon / Q_LON).astype(np.int16), np.round(lat / Q_LAT).astype(np.int16)

    def write(fn, cols):
        layout, off, buf = {}, 0, bytearray()
        for name, arr in cols:
            arr = np.ascontiguousarray(arr)
            pad = (-off) % 4
            buf += b"\0" * pad
            off += pad
            layout[name] = {"offset": off, "type": arr.dtype.name, "length": int(arr.size)}
            buf += arr.astype(arr.dtype.newbyteorder("<")).tobytes()
            off += arr.nbytes
        open(os.path.join(OUT, fn), "wb").write(buf)
        return layout, off

    wq = q(wl, wa)
    wlay, wbytes = write("dots_world.bin", [("lon", wq[0]), ("lat", wq[1]),
                                            ("elev", np.clip(np.round(we), -11000, 9000).astype(np.int16)),
                                            ("light", light_at(wl, wa))])
    uq = q(ul, ua)
    ulay, ubytes = write("dots_usa.bin", [("tract", utract), ("lon", uq[0]), ("lat", uq[1]),
                                          ("elev", np.clip(np.round(ue), -500, 7000).astype(np.int16)),
                                          ("county", ucounty), ("light", light_at(ul, ua)),
                                          ("tract_st", utst), ("flags", uflag)])
    meta = {
        "generated": time.strftime("%Y-%m-%dT%H:%M%z"),
        "script": "usa/globe_dots.py",
        "endian": "little",
        "lon_scale": Q_LON, "lat_scale": Q_LAT, "elev_unit": "m (EGM2008 height, ETOPO 2022 ice surface)",
        "grid": {"type": "equal-area rows (row step = d deg lat, column step = d / cos(lat))",
                 "world_land_deg": a.land, "world_ocean_deg": a.ocean, "usa_deg": a.usa,
                 "world_land_rule": "coarse 0.1 deg area-mean elevation > 0 m",
                 "usa_rule": "inside states.geojson (50 states + DC + PR); elevation = 3x3 mean of 60 arc-second cells"},
        "world": {"file": "dots_world.bin", "count": int(len(wl)), "bytes": wbytes, "layout": wlay},
        "usa": {"file": "dots_usa.bin", "count": int(len(ul)), "bytes": ubytes, "layout": ulay,
                "county_index": "index into counties[] (0xFFFF none)",
                "tract": "census tract GEOID digits 3..11 as uint32 (0xFFFFFFFF none); state FIPS in tract_st",
                "flags": {"1": "inside a USGS 3DEP EPT LiDAR project boundary"}},
        "counties": cfips,
        "light": "NASA Black Marble 2016 (VIIRS DNB) 0.1 deg grey value 0-255 at the dot",
        "sources": [
            {"id": "etopo", "name": "NOAA NCEI ETOPO 2022 Global Relief Model, 60 arc-second ice surface GeoTIFF",
             "url": "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif",
             "doi": "https://doi.org/10.25921/fd45-gt74",
             "licence": "CC0-1.0 public domain dedication (NOAA metadata: 'not subject to copyright protection in the United States. NOAA waives any potential copyright ... (CC0-1.0)')"},
            {"id": "blackmarble", "name": "NASA Earth Observatory Black Marble 2016 (Suomi NPP VIIRS), 0.1 deg",
             "url": "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg",
             "licence": "NASA imagery, not subject to copyright in the US (NASA Images and Media Usage Guidelines); credit NASA; no endorsement implied"},
            {"id": "states", "name": "web/usa/data/states.geojson (US Census cartographic boundaries)", "licence": "US federal government work, public domain"},
            {"id": "counties", "name": "web/usa/data/counties.geojson (US Census)", "licence": "US federal government work, public domain"},
            {"id": "tracts", "name": "web/usa/data/tracts/<ST>.json (US Census 2024 cartographic boundary tracts)", "licence": "US federal government work, public domain"},
            {"id": "ept", "name": "data/national/ept_boundaries.geojson (USGS 3DEP Entwine Point Tile boundaries)", "licence": "USGS, public domain"},
        ],
    }
    json.dump(meta, open(os.path.join(OUT, "dots_meta.json"), "w"), separators=(",", ":"))
    log("done", meta["world"]["count"], "world dots", wbytes, "B;", meta["usa"]["count"], "usa dots", ubytes, "B")


if __name__ == "__main__":
    main()

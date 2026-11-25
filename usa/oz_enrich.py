"""Mark houses in a 3D block with their Qualified Opportunity Zone status.

  .venv\\Scripts\\python -u -W ignore usa/oz_enrich.py <slug> [<slug> ...]
  .venv\\Scripts\\python -u -W ignore usa/oz_enrich.py --point <lon> <lat>     (lookup only, writes nothing)

Point-in-polygon against FULL-RESOLUTION source geometry (not the simplified web file):
  2018 round: CDFI Fund QOZ shapefile (2010 TIGER tracts), cached by usa/opportunity_zones.py
  2027 round: Census TIGER/Line 2024 tracts for the block's state (2020 tract boundaries), downloaded
              on demand to usa/_cache/oz/, filtered to the CDFI Fund 2027 designation list.
Writes houses.json  h.opportunity_zone  and  meta.json  meta.opportunity_zone ; touches nothing else.
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opportunity_zones as oz  # noqa: E402

BLOCKS = oz.ROOT / "web" / "usa" / "blocks"
TIGER = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_{st}_tract.zip"
SOURCE_2018 = "IRS Notice 2018-48/2019-42 via CDFI Fund QOZ list + shapefile (2010 tracts)"
SOURCE_2027 = "Treasury/IRS 2027 QOZ designations (CDFI Fund final_oz2_designations.xlsx, 2026-11-05) + Census TIGER/Line 2024 tracts"
ROUND_INFO = {"2018": {"effective": "2018", "expires": "2028-12-31"},
              "2027": {"effective": "2027-01-01", "expires": "2036-12-31"}}
TODAY = "2026-11-24"


def _tiger(st):
    p = oz.CACHE / f"tl_2024_{st}_tract.zip"
    if not p.exists() or p.stat().st_size < 1000:
        print(f"  downloading TIGER/Line 2024 tracts for state {st}")
        r = requests.get(TIGER.format(st=st), headers=oz.UA, timeout=600)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def zones_near(lon0, lat0, lon1, lat1, pad=0.02):
    """Full-res OZ polygons (both rounds) intersecting a lon/lat bbox, EPSG:4326."""
    bb = box(lon0 - pad, lat0 - pad, lon1 + pad, lat1 + pad)
    bb3857 = gpd.GeoSeries([bb], crs=4326).to_crs(3857).total_bounds
    z18 = gpd.read_file(f"zip://{oz.fetch('oz2018_shp').as_posix()}!8764oz.shp", bbox=tuple(bb3857)).to_crs(4326)
    lst18 = pd.read_excel(oz.fetch("oz2018_xlsx"), header=4, dtype=str)
    lst18.columns = ["state", "county", "geoid", "type", "acs"]
    tmap = {"Low-Income Community": "low-income", "Non-LIC Contiguous": "contiguous"}
    t18 = dict(zip(lst18.geoid, lst18.type.map(tmap)))
    rows = []
    for g, geom in zip(z18.CENSUSTRAC, z18.geometry):
        g = oz.GEOID_FIX_2018.get(g, g)
        rows.append({"geoid": g, "round": "2018", "type": t18.get(g), "rural": None, "geometry": geom,
                     "source": SOURCE_2018 + (" (Puerto Rico deemed zone, expires 2027-12-31)" if g[:2] == "72" else "")})
    # 2027: which states does the bbox touch? use the CB national file to find them cheaply
    lst27 = pd.read_excel(oz.fetch("oz2027_xlsx"), dtype=str)
    lst27.columns = ["geoid", "qozid", "state", "county", "rural"]
    r27 = dict(zip(lst27.geoid, lst27.rural.str.strip().str.lower() == "rural"))
    cb = gpd.read_file(f"zip://{oz.fetch('cb2024').as_posix()}", bbox=tuple(bb.bounds), ignore_geometry=True)
    for st in sorted(set(cb.STATEFP)):
        t = gpd.read_file(f"zip://{_tiger(st).as_posix()}", bbox=tuple(bb.bounds)).to_crs(4326)
        for g, geom in zip(t.GEOID, t.geometry):
            if g in r27:
                rows.append({"geoid": g, "round": "2027", "type": "low-income", "rural": bool(r27[g]),
                             "geometry": geom, "source": SOURCE_2027})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else \
        gpd.GeoDataFrame(columns=["geoid", "round", "type", "rural", "source", "geometry"], geometry="geometry", crs=4326)


def classify(zones, lon, lat):
    pt = Point(lon, lat)
    hits = []
    for z in zones.itertuples(index=False):
        if z.geometry.covers(pt):
            hits.append({"round": z.round, "geoid": z.geoid, "type": z.type, "rural": z.rural,
                         "in_force": z.round == "2018" and TODAY <= ROUND_INFO["2018"]["expires"],
                         **ROUND_INFO[z.round], "source": z.source})
    hits.sort(key=lambda h: h["round"])
    prim = hits[0] if hits else None
    return {"in_zone": bool(hits),
            "geoid": prim["geoid"] if prim else None,
            "round": prim["round"] if prim else None,
            "type": prim["type"] if prim else None,
            "source": prim["source"] if prim else "no 2018 or 2027 QOZ contains this point (checked " + TODAY + ")",
            "in_zone_2018": any(h["round"] == "2018" for h in hits),
            "in_zone_2027": any(h["round"] == "2027" for h in hits),
            "rounds": [{k: v for k, v in h.items() if k != "source"} for h in hits]}


def enrich(slug):
    import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from usa.blocklock import block_lock
    with block_lock(Path(__file__).resolve().parents[1] / "web" / "usa" / "blocks" / slug):
        return _enrich(slug)


def _enrich(slug):
    B = BLOCKS / slug
    meta = json.load(open(B / "meta.json", encoding="utf-8"))
    houses = json.load(open(B / "houses.json", encoding="utf-8"))
    w, s, e, n = meta["bounds"]
    lons = [h["lon"] for h in houses if h.get("lon") is not None] + [w, e]
    lats = [h["lat"] for h in houses if h.get("lat") is not None] + [s, n]
    zones = zones_near(min(lons), min(lats), max(lons), max(lats))
    cnt = {"2018": 0, "2027": 0, "any": 0}
    for h in houses:
        if h.get("lon") is None or h.get("lat") is None:
            h["opportunity_zone"] = {"in_zone": None, "geoid": None, "round": None, "type": None,
                                     "source": "house has no lon/lat"}
            continue
        r = classify(zones, h["lon"], h["lat"])
        h["opportunity_zone"] = r
        cnt["any"] += r["in_zone"]
        cnt["2018"] += r["in_zone_2018"]
        cnt["2027"] += r["in_zone_2027"]
    o = classify(zones, *meta["origin"])
    tracts = sorted({(h["opportunity_zone"].get("round"), h["opportunity_zone"].get("geoid"))
                     for h in houses if h["opportunity_zone"].get("in_zone")})
    meta["opportunity_zone"] = {
        "houses": len(houses), "in_zone": cnt["any"], "in_zone_2018": cnt["2018"], "in_zone_2027": cnt["2027"],
        "origin_in_zone": o["in_zone"], "origin_rounds": o["rounds"],
        "zone_tracts": [{"round": r, "geoid": g} for r, g in tracts],
        "block_tract": meta.get("tract"),
        "rounds": {"2018": {**ROUND_INFO["2018"], "source": SOURCE_2018, "tract_vintage": "2010"},
                   "2027": {**ROUND_INFO["2027"], "source": SOURCE_2027, "tract_vintage": "2020"}},
        "method": "point-in-polygon of house lon/lat against full-resolution designated tract polygons",
        "licence": "US federal government works (public domain, 17 U.S.C. 105); see web/usa/data/oz_licence.json",
        "checked": TODAY,
    }
    json.dump(houses, open(B / "houses.json", "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(meta, open(B / "meta.json", "w", encoding="utf-8"), indent=1)
    print(f"{slug}: {len(houses)} houses, in any QOZ {cnt['any']} (2018: {cnt['2018']}, 2027: {cnt['2027']}); "
          f"origin in zone: {o['in_zone']} {[(x['round'], x['geoid']) for x in o['rounds']]}; "
          f"zone polygons nearby: {len(zones)}")


def main(argv):
    if len(argv) >= 3 and argv[0] == "--point":
        lon, lat = float(argv[1]), float(argv[2])
        print(json.dumps(classify(zones_near(lon, lat, lon, lat, pad=0.001), lon, lat), indent=1))
        return 0
    if not argv:
        print(__doc__)
        return 2
    for slug in argv:
        enrich(slug)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Build web/app/data/gedi.json: NASA GEDI 25 m laser footprints (ground elevation, canopy height
RH98/RH100, canopy cover, plant area index, aboveground biomass) in a ~+-0.05 deg box around every
built block (web/usa/blocks/index.json).

Source: gediDB global TileDB database (GFZ Potsdam), a public mirror of NASA GEDI Release 3 (V003)
L2A + L2B + L4A footprints, values "as distributed by NASA". No login: unsigned S3 reads from
  s3://dog-ext--gedidb--gedi-l2-l4-v003/array_uri   endpoint https://s3.gfz.de
(https://gedidb.readthedocs.io/en/latest/user/tiledb_database.html, "Access Control: Public").
Dimensions are the footprint latitude/longitude (lowest mode) and time (days since 1970-01-01).
The original LP DAAC / ORNL DAAC HDF5 granules need Earthdata Login, so they are not used.

Filters (product-defined only): a shot is kept when l2a_quality_flag_rel3 == 1 and degrade_flag == 0.
cover/pai are null unless l2b_quality_flag_rel3 == 1; agbd is null unless l4a_quality_flag_rel3 == 1.
Every other shot is kept: no thinning, no interpolation.
RH98 / RH100 are TileDB attributes rh_99 / rh_101 (profile labels 0..100 stored 1-indexed).

Licence: Earthdata data use guidance: "Unless the content is marked with a use restriction or
license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."
GEDI collections in CMR carry only the NASA Data Use Policy (GEDI02_A V003: FreeAndOpenData true).
gediDB asks for acknowledgement (Besnard, Dombrowski & Holcomb, doi:10.5281/zenodo.13885229).

GEDI flies on the ISS, so nothing exists north of ~51.6 N (anchorage-ak gets no shots).
Per-block results are cached in usa/cache/gedi/ so re-runs resume. Needs `pip install tiledb`.

Usage: .venv\\Scripts\\python usa\\build_gedi.py [--pad 0.05] [--workers 6]
"""
import argparse
import datetime
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "cache", "gedi")
BLOCKS = os.path.join(ROOT, "web", "usa", "blocks", "index.json")
OUT = os.path.join(ROOT, "web", "app", "data", "gedi.json")
URI = "s3://dog-ext--gedidb--gedi-l2-l4-v003/array_uri"
ATTRS = ["shot_number", "beam_name", "degrade_flag", "l2a_quality_flag_rel3", "elev_lowestmode",
         "rh_99", "rh_101", "sensitivity", "l2b_quality_flag_rel3", "cover", "pai",
         "l4a_quality_flag_rel3", "agbd"]
SKIP = {"slc-ut-test"}
MAX_LAT = 52.0  # ISS orbit inclination 51.6 deg


def ctx():
    import tiledb
    return tiledb.Ctx(tiledb.Config({
        "vfs.s3.endpoint_override": "s3.gfz.de", "vfs.s3.region": "eu-central-1", "vfs.s3.scheme": "https",
        "vfs.s3.use_virtual_addressing": "false", "vfs.s3.no_sign_request": "true",
        "vfs.s3.connect_timeout_ms": "60000", "vfs.s3.request_timeout_ms": "600000",
        "sm.tile_cache_size": str(256 * 1024 ** 2)}))


def f(v, nd=2):
    v = float(v)
    return None if v != v or abs(v) > 1e30 or v <= -9999 else round(v, nd)


def query(A, b, pad):
    y0, y1, x0, x1 = b["lat"] - pad, b["lat"] + pad, b["lon"] - pad, b["lon"] + pad
    d = A.query(attrs=ATTRS).multi_index[y0:y1, x0:x1, :]
    rows = []
    for i in range(len(d["shot_number"])):
        rows.append({k: (d[k][i].item() if hasattr(d[k][i], "item") else d[k][i])
                     for k in ATTRS + ["latitude", "longitude", "time"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--reverse", action="store_true", help="query blocks in reverse order (for a 2nd process)")
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    blocks = [b for b in json.load(open(BLOCKS, encoding="utf-8")) if b["slug"] not in SKIP]
    t0 = time.time()
    raw = {}
    todo = []
    for b in blocks:
        p = os.path.join(CACHE, f"{b['slug']}.json")
        if abs(b["lat"]) > MAX_LAT:
            raw[b["slug"]] = []
        elif os.path.exists(p):
            raw[b["slug"]] = json.load(open(p, encoding="utf-8"))
        else:
            todo.append(b)
    if a.reverse:
        todo.reverse()
    if todo:
        import tiledb
        print(f"opening {URI} (~2 min of fragment metadata)...", flush=True)
        A = tiledb.open(URI, "r", ctx=ctx())
        print(f"  open in {time.time() - t0:.0f}s; {len(todo)} blocks to query", flush=True)

        def job(b):
            if os.path.exists(os.path.join(CACHE, f"{b['slug']}.json")):  # done by a parallel run
                return b["slug"], json.load(open(os.path.join(CACHE, f"{b['slug']}.json"), encoding="utf-8"))
            rows = query(A, b, a.pad)
            p = os.path.join(CACHE, f"{b['slug']}.json")
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(rows, fh, separators=(",", ":"))
            os.replace(p + ".tmp", p)
            return b["slug"], rows

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(job, b): b["slug"] for b in todo}
            for fu in as_completed(futs):
                try:
                    slug, rows = fu.result()
                    raw[slug] = rows
                    print(f"  {slug}: {len(rows)} shots ({time.time() - t0:.0f}s)", flush=True)
                except Exception as e:
                    print("   fail", futs[fu], e, flush=True)
        A.close()

    # assemble
    pts, cov = [], {}
    epoch = datetime.date(1970, 1, 1)
    for b in blocks:
        rows = raw.get(b["slug"])
        if rows is None:
            cov[b["slug"]] = {"queried": False, "points": 0}
            continue
        n0 = len(pts)
        for r in rows:
            if r["l2a_quality_flag_rel3"] != 1 or r["degrade_flag"] != 0:
                continue
            h = f(r["elev_lowestmode"])
            if h is None:
                continue
            l2b = r["l2b_quality_flag_rel3"] == 1
            l4a = r["l4a_quality_flag_rel3"] == 1
            date = (epoch + datetime.timedelta(days=int(r["time"]))).isoformat()
            pts.append([round(r["longitude"], 6), round(r["latitude"], 6), h,
                        f(r["rh_99"]), f(r["rh_101"]),
                        f(r["cover"], 3) if l2b else None, f(r["pai"], 3) if l2b else None,
                        f(r["agbd"], 1) if l4a else None,
                        date, str(r["beam_name"]).lstrip("/"), str(r["shot_number"])])
        cov[b["slug"]] = {"queried": True, "shots": len(rows), "points": len(pts) - n0}

    # heights to NAVD88 (GEOID18) exactly as build_icesat2.py does (GEDI elevations are WGS84-ellipsoid)
    from pyproj import Transformer, network
    network.set_network_enabled(True)
    tr = Transformer.from_crs("EPSG:7912", "EPSG:6318+5703", always_xy=True)
    if pts:
        _, _, hz = tr.transform([p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts])
        bad = 0
        for p, h in zip(pts, hz):
            if h == h and abs(h) < 1e5: p[2] = round(float(h), 2)
            else: bad += 1
        print(f"NAVD88 conversion: {len(pts) - bad} converted, {bad} outside the GEOID18 grid (left as ellipsoid height)")
    doc = {
        "source": "NASA GEDI Release 3 (V003) L2A/L2B/L4A footprints via the gediDB public TileDB database (GFZ Potsdam)",
        "fields": ["lon", "lat", "elev_lowestmode_m", "rh98_m|null", "rh100_m|null", "cover_frac|null",
                   "pai_m2m2|null", "agbd_Mg_ha|null", "date", "beam", "shot_number"],
        "heights": "elev_lowestmode (ground) in metres, NAVD88 orthometric (GEOID18) converted from the WGS84 ellipsoid; "
                   "rh98/rh100 = canopy/top-of-object height above ground; cover = canopy cover fraction 0-1; "
                   "pai = plant area index; agbd = aboveground biomass density",
        "filters": "l2a_quality_flag_rel3==1 and degrade_flag==0; cover/pai only where l2b_quality_flag_rel3==1; "
                   "agbd only where l4a_quality_flag_rel3==1",
        "licence": "CC0 (NASA-led mission data; no restrictions on use, citation requested)",
        "licenceUrl": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
        "attribution": "GEDI L2A/L2B (Dubayah et al., NASA LP DAAC, doi:10.5067/GEDI/GEDI02_A.003, doi:10.5067/GEDI/GEDI02_B.003) "
                       "and L4A (ORNL DAAC); processed with gediDB (Besnard, Dombrowski & Holcomb, doi:10.5281/zenodo.13885229)",
        "boxPadDeg": a.pad,
        "built": time.strftime("%Y-%m-%d"),
        "coverage": cov,
        "points": pts,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"wrote {OUT}: {len(pts)} points, {os.path.getsize(OUT) / 1e6:.1f} MB, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

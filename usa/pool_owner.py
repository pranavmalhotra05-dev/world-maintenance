"""Check which house each detected pool belongs to, against EVERY nearby building, not only the block's houses.

    .venv/Scripts/python -u -W ignore -m usa.pool_owner [slug ...]

Why: usa/pools.py gives each pool to the nearest house *in the block list*. A block is a 120 m square, so the
real owner is often a neighbour just outside it (scottsdale-az: the pool given to 10022 N 77th St is 10.1 m from a
house outside the block and 16.1 m from 10022).

Rule, per pool (houses[].pool with present = true):
  1. parcels.json has open parcels  -> owner = the block house whose parcel contains the pool centre.
     The pool's parcel holds no block house -> neighbour's pool.  (Lot lines from green sources only.)
  2. no open parcels -> owner = the building nearest the pool among ALL Overture footprints in the block + 80 m
     (Overture buildings, ODbL / CDLA-Permissive). A garage or shed counts for the block house nearest to it.
     Nearest building outside the block list -> neighbour's pool.
     This is a proximity test, not a lot line: it is labelled 'nearest building' and kept lower confidence.
  A pool that moves goes to its owner house (if that house has none); a neighbour's pool is removed from the house
  and listed in meta.pools.owner_check.neighbour_pools (position only).
Every pool gets pool.owner = {method, confidence, nearest_m, runner_up_m, parcel_id?}.
Overture footprints are cached in usa/_cache/pool_owner/<slug>.json.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely import wkb
from shapely.geometry import Point, Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "pool_owner"
CACHE.mkdir(parents=True, exist_ok=True)
PAD_M = 80.0


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, j):
    tmp = Path(str(p) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, separators=(",", ":"))
    tmp.replace(p)


def overture_buildings(slug, meta):
    """[(overture id, local-metre ring)] for every Overture building in the block square + PAD_M."""
    cf = CACHE / f"{slug}.json"
    if cf.exists():
        return load(cf)["buildings"]
    import duckdb
    from usa.build_block import overture_release
    lon0, lat0 = meta["origin"]
    pad = float(meta.get("half_m", 60)) + PAD_M
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540.0
    dl, dt = pad / kx, pad / ky
    rel = overture_release()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT id, ST_AsWKB(geometry) FROM read_parquet(
        's3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*', hive_partitioning=1)
        WHERE bbox.xmin > {lon0 - dl} AND bbox.xmax < {lon0 + dl} AND bbox.ymin > {lat0 - dt} AND bbox.ymax < {lat0 + dt}""").fetchall()
    out = []
    for oid, g in rows:
        gg = wkb.loads(bytes(g))
        polys = [gg] if gg.geom_type == "Polygon" else list(gg.geoms)
        for k, pg in enumerate(polys):
            out.append([oid if k == 0 else f"{oid}#{k}",
                        [[round((x - lon0) * kx, 2), round((y - lat0) * ky, 2)] for x, y in pg.exterior.coords]])
    save(cf, {"release": rel, "pad_m": PAD_M, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "buildings": out})
    return out


def run(slug):
    d = BLOCKS / slug
    meta, hj = load(d / "meta.json"), load(d / "houses.json")
    hs = hj["houses"] if isinstance(hj, dict) else hj
    pooled = [h for h in hs if isinstance(h.get("pool"), dict) and h["pool"].get("present")]
    if not pooled:
        return None
    homes = [h for h in hs if not h.get("garage")]
    hpoly = {h["id"]: Polygon(h["ring"]) for h in hs if h.get("ring") and len(h["ring"]) >= 3}

    def home_of(hid):
        """a garage/shed belongs to the nearest non-garage block house"""
        h = next(x for x in hs if x["id"] == hid)
        if not h.get("garage"):
            return hid
        g = hpoly[hid]
        return min((hpoly[x["id"]].distance(g), x["id"]) for x in homes if x["id"] in hpoly)[1]

    # parcels (green sources only, built by usa/parcels_hoa.py)
    par = []
    pj = d / "parcels.json"
    if pj.exists():
        pdoc = load(pj)
        for p in pdoc.get("parcels", []) or []:
            ring = p.get("ring") or []
            if len(ring) >= 3:
                par.append((p, Polygon([(q[0], q[1]) for q in ring]).buffer(0)))
    # map block houses -> Overture building ids: match by footprint overlap
    blds = [(oid, Polygon(r).buffer(0)) for oid, r in overture_buildings(slug, meta) if len(r) >= 4]
    b2h = {}
    for oid, bp in blds:
        best = max(((bp.intersection(hp).area / max(min(bp.area, hp.area), 1e-6), hid) for hid, hp in hpoly.items()), default=(0, None))
        if best[0] > 0.3:
            b2h[oid] = best[1]
    # small outbuildings (< 100 m2: garages, sheds, casitas) count for the main building nearest to them
    big = [(oid, bp) for oid, bp in blds if bp.area >= 100]
    for oid, bp in blds:
        if oid in b2h or bp.area >= 100 or not big:
            continue
        mo = min((bb.distance(bp), o) for o, bb in big)[1]
        if mo in b2h:
            b2h[oid] = b2h[mo]
    # block houses that Overture lacks still compete with their own footprint
    extra = [(f"house:{hid}", hp) for hid, hp in hpoly.items() if hid not in b2h.values()]
    for oid, _ in extra:
        b2h[oid] = oid.split(":", 1)[1]
    cand = blds + extra

    old = {h["id"]: h["pool"] for h in pooled}
    for h in pooled:
        h.pop("pool")
    new, neighbours, moved = {}, [], []
    for hid, P in sorted(old.items(), key=lambda kv: -kv[1].get("confidence", 0) or 0):
        pt = Point(float(P["x"]), float(P["y"]))
        own = None
        if par:
            hit = sorted((pg.area, p, pg) for p, pg in par if pg.contains(pt))
            if hit:
                _, p, pg = hit[0]
                inside = [x["id"] for x in homes if x["id"] in hpoly and pg.contains(hpoly[x["id"]].centroid)]
                own = {"method": "parcel", "parcel_id": p.get("id"), "confidence": "high",
                       "owner": inside[0] if inside else None}
        if own is None:
            ds = sorted((bp.distance(pt), oid) for oid, bp in cand)
            if not ds:
                continue
            first = ds[0]
            own_of = lambda o: home_of(b2h[o]) if o in b2h else o
            ru = next((dd for dd, o in ds[1:] if own_of(o) != own_of(first[1])), None)
            owner = home_of(b2h[first[1]]) if first[1] in b2h else None
            margin = (ru - first[0]) if ru is not None else 99
            own = {"method": "nearest building (all Overture footprints, not a lot line)", "owner": owner,
                   "nearest_m": round(first[0], 1), "runner_up_m": round(ru, 1) if ru is not None else None,
                   "confidence": "medium" if margin >= 4 else "low"}
        P = dict(P)
        P["owner"] = {k: v for k, v in own.items() if k != "owner"}
        if own["owner"] is None:
            neighbours.append({"x": P["x"], "y": P["y"], "area_m2": P.get("area_m2"), "was_given_to": hid,
                               "method": own["method"], "nearest_m": own.get("nearest_m"), "parcel_id": own.get("parcel_id")})
            continue
        tgt = own["owner"]
        if tgt in new:   # owner already has a (more confident) pool: keep that one, list this as extra water
            new[tgt].setdefault("extra_water", []).append({"x": P["x"], "y": P["y"], "area_m2": P.get("area_m2")})
            continue
        if tgt != hid:
            moved.append({"from": hid, "to": tgt, "x": P["x"], "y": P["y"]})
            P["house_dist_m"] = round(hpoly[tgt].distance(pt), 1) if tgt in hpoly else None
        new[tgt] = P

    for h in hs:
        if h["id"] in new:
            h["pool"] = new[h["id"]]
        elif h["id"] in old and not h.get("garage"):
            gone = next((n for n in neighbours if n["was_given_to"] == h["id"]), None)
            mv = next((m for m in moved if m["from"] == h["id"]), None)
            why = ("the water seen near this house sits on a neighbour's lot (" + gone["method"] + ")" if gone
                   else f"the water seen near this house belongs to house {mv['to']} (nearer to it)" if mv else "")
            h["pool"] = {"present": False, "confidence": None, "area_m2": 0, "first_seen_year": None, "by_year": [],
                         "green_years": [], "last_state": None, "note": "no pool of its own on open aerial snapshots; " + why}
    chk = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "method": "parcel" if par else "nearest building among all Overture footprints (block + 80 m)",
           "parcels": len(par), "overture_buildings": len(blds), "pools_in": len(old), "pools_kept": len(new),
           "moved": moved, "neighbour_pools": neighbours,
           "sources": ["Overture Maps buildings (OSM ODbL; Microsoft/Google/Esri CC BY / ODbL / CDLA-P)"]
           + ([pdoc.get("meta", {}).get("source") or "parcels.json (green parcel source)"] if par else [])}
    meta.setdefault("pools", {})["owner_check"] = chk
    if isinstance(meta["pools"], dict):
        meta["pools"]["n_pools"] = len(new)
        nh = sum(1 for h in hs if not h.get("garage"))
        meta["pools"]["share_houses_with_pool"] = round(len(new) / nh, 3) if nh else None
    with block_lock(d):
        save(d / "houses.json", hj)
        save(d / "meta.json", meta)
    return chk


def main():
    slugs = [a for a in sys.argv[1:] if not a.startswith("-")] or sorted(p.name for p in BLOCKS.iterdir() if (p / "houses.json").exists())
    for s in slugs:
        try:
            c = run(s)
        except Exception as e:  # keep going across blocks
            print(f"{s}: FAILED {e!r}", flush=True)
            continue
        if c:
            print(f"{s}: {c['pools_in']} pools -> kept {c['pools_kept']}, moved {len(c['moved'])}, "
                  f"neighbour {len(c['neighbour_pools'])} ({c['method']})", flush=True)
            for n in c["neighbour_pools"]:
                print(f"    neighbour pool at ({n['x']}, {n['y']}) was given to house {n['was_given_to']}, nearest {n['nearest_m']} m")
            for m in c["moved"]:
                print(f"    moved {m['from']} -> {m['to']}")


if __name__ == "__main__":
    main()

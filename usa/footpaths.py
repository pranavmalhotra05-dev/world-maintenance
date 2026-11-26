"""Measured footpaths (public sidewalks, stairs, crosswalk areas) for every 3D block, from GREEN city layers only.

    .venv/Scripts/python -u -W ignore -m usa.footpaths [slug ...] [--refresh]

Sources (licence read on the publisher's own item, 2026-11-25):
  * District of Columbia  Planimetrics_2023 layer 11 'Sidewalks - 2023' (polygons: SIDEWALK / HIDDEN SIDEWALK /
    STAIRS / CROSSWALK), CC BY 4.0 - Open Data DC (OCTO).
  * Portland metro (OR)   Oregon Metro RLIS 'Sidewalks' (polylines = surveyed sidewalk lines, no width), ODbL 1.0 + DbCL.
Every other block: footpaths.json with status 'no open sidewalk layer' - nothing is drawn from setbacks or guessed widths.
OpenStreetMap footways already sit in streets.json (paths[]) and are drawn by the viewer separately.

Output web/usa/blocks/<slug>/footpaths.json in local metres of meta.origin, z above meta.z_base from the block terrain
(usa/streets.Terrain), clipped to the block square + 30 m; polygons are cut off house footprints (+0.3 m).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon, box as sbox, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402
from usa.parcels_hoa import query, densify  # noqa: E402
from usa.streets import Frame, Terrain  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
PAD_M = 30.0

SRC = {
    "DC": dict(id="dc_planimetrics_sidewalks_2023", kind="poly", name="Open Data DC Planimetrics 2023 - Sidewalks (layer 11)",
               url="https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Planimetrics_2023/MapServer/11",
               fields="FEATURECODE,DESCRIPTION,CAPTUREYEAR", licence="CC BY 4.0",
               licence_url="https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Planimetrics_2023/MapServer/11/iteminfo?f=pjson",
               attribution="District of Columbia, Office of the Chief Technology Officer - Open Data DC (CC BY 4.0)"),
    "OR": dict(id="rlis_sidewalks", kind="line", name="Oregon Metro RLIS Sidewalks (item f2bd3c2486de47b49186121da1f9c2d0)",
               url="https://services2.arcgis.com/McQ0OlIABe29rJJy/arcgis/rest/services/Sidewalks/FeatureServer/0",
               fields="STREETNAME", licence="ODbL 1.0 + DbCL 1.0 (share-alike for derived databases)",
               licence_url="https://www.arcgis.com/home/item.html?id=f2bd3c2486de47b49186121da1f9c2d0",
               attribution="Oregon Metro RLIS - Sidewalks (ODbL)"),
}
CODE = {1480: "sidewalk", 1481: "sidewalk (under cover)", 1482: "stairs", 1483: "crosswalk area"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def z3(coords, ter):
    pts = densify(list(coords))
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    zs = ter.z(xs, ys)
    return [[round(float(x), 2), round(float(y), 2), round(float(z), 2)] for x, y, z in zip(xs, ys, zs)]


def build(slug, refresh=False):
    bdir = BLOCKS / slug
    meta = load(bdir / "meta.json")
    src = SRC.get(meta.get("state"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out = {"polys": [], "lines": [], "meta": {"generated": now, "status": "no open sidewalk layer", "source": None,
                                                "licence": None, "attribution": None,
                                                "note": "OpenStreetMap footways are in streets.json (paths); no city sidewalk layer "
                                                        "with an explicit commercial-use licence was found for this place"}}
    if src:
        fr = Frame(meta)
        R = float(meta["half_m"]) + PAD_M
        clip = sbox(-R, -R, R, R)
        lo, la = fr.ll([-R, R], [-R, R])
        bbox = (float(lo[0]), float(la[0]), float(lo[1]), float(la[1]))
        hj = load(bdir / "houses.json")
        hs = hj["houses"] if isinstance(hj, dict) else hj
        houses = unary_union([Polygon(h["ring"]).buffer(0.3) for h in hs if len(h.get("ring") or []) >= 3])
        ter = Terrain(bdir, fr, slug)
        feats = query(src["url"], bbox, src["fields"], slug, "walk_" + src["id"], refresh)
        for ft in feats:
            g, p = ft.get("geometry"), ft.get("properties") or {}
            if not g:
                continue
            gg = shape(g)
            if src["kind"] == "poly":
                kind = CODE.get(p.get("FEATURECODE"), (p.get("DESCRIPTION") or "sidewalk").lower())
                for part in (list(gg.geoms) if gg.geom_type == "MultiPolygon" else [gg]):
                    ex = np.asarray(part.exterior.coords)
                    x, y = fr.local(ex[:, 0], ex[:, 1])
                    holes = []
                    for h in part.interiors:
                        hc = np.asarray(h.coords); hx, hy = fr.local(hc[:, 0], hc[:, 1]); holes.append(list(zip(hx, hy)))
                    q = Polygon(list(zip(x, y)), holes).buffer(0).intersection(clip).difference(houses)
                    for r in (list(q.geoms) if q.geom_type == "MultiPolygon" else [q] if q.geom_type == "Polygon" else []):
                        if r.area < 0.5:
                            continue
                        rec = {"poly": z3(r.exterior.coords, ter), "kind": kind, "area_m2": round(r.area, 1),
                               "year": p.get("CAPTUREYEAR"), "source": src["id"]}
                        if r.interiors:
                            rec["holes"] = [z3(h.coords, ter) for h in r.interiors]
                        out["polys"].append(rec)
            else:
                for part in (list(gg.geoms) if gg.geom_type == "MultiLineString" else [gg]):
                    c = np.asarray(part.coords)
                    x, y = fr.local(c[:, 0], c[:, 1])
                    q = LineString(list(zip(x, y))).intersection(clip)
                    for r in (list(q.geoms) if q.geom_type == "MultiLineString" else [q] if q.geom_type == "LineString" else []):
                        if r.length < 1:
                            continue
                        out["lines"].append({"path": z3(r.coords, ter), "kind": "sidewalk (surveyed line, width not recorded)",
                                             "street": p.get("STREETNAME"), "length_m": round(r.length, 1), "source": src["id"]})
        out["meta"].update({"status": "ok" if (out["polys"] or out["lines"]) else "ok (none in box)", "source": src["name"],
                            "source_url": src["url"], "licence": src["licence"], "licence_url": src["licence_url"],
                            "attribution": src["attribution"], "note": "measured city sidewalk layer; clipped to the block square + 30 m"})
    out["meta"]["counts"] = {"polys": len(out["polys"]), "lines": len(out["lines"])}
    with block_lock(bdir):
        (bdir / "footpaths.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        m = load(bdir / "meta.json")
        m["footpaths"] = {k: out["meta"][k] for k in ("generated", "status", "source", "licence", "attribution", "counts")}
        if "footpaths.json" not in m.get("files", []):
            m.setdefault("files", []).append("footpaths.json")
        (bdir / "meta.json").write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    return out["meta"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    slugs = a.slugs or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists() and (p / "houses.json").exists())
    for s in slugs:
        try:
            m = build(s, a.refresh)
            print(f"{s}: {m['status']} {m['counts']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: FAILED {e!r}", flush=True)


if __name__ == "__main__":
    main()

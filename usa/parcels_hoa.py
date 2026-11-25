"""Property (parcel) boundaries and HOA / covenant boundaries for every 3D block, from GREEN sources only.

    .venv/Scripts/python -u -W ignore -m usa.parcels_hoa [slug ...] [--refresh]

Rules (see usa/research/parcels_hoa.md and usa/research/licence_registry_local.json):
  * a block gets parcels only when its county has a parcel layer with explicit commercial-use terms; every other block
    gets parcels: [] and meta.parcels.status = 'no open parcel data'. Lot lines are never synthesized.
  * owner rule: only geometry + parcel id + situs address are requested (outFields lists them explicitly; owner,
    mailing, tax-name and sale fields are never asked for).
  * HOA: Boulder County platted areas with HoaArcRef = Yes -> 'HOA (recorded)'; Hamilton County condominium plats
    -> 'condo association'; Columbus (CC0) and Hamilton County
    (public domain) subdivision plats -> 'subdivision plat (possible covenants - not confirmed HOA)'. Nothing is ever
    labelled 'non-HOA': absence of data is 'no open HOA data'.

Output web/usa/blocks/<slug>/parcels.json in local metres of meta.origin (houses.json frame) with z from the block
terrain (LiDAR mesh inside the square, 3DEP DEM offset to it outside - usa/streets.Terrain), z above meta.z_base.
Edges are densified to <= 2 m so the drape follows the ground (vertices on straight source edges, shape unchanged).
Polygons are clipped to the query square (block half + 30 m); a cut polygon carries clipped: true (the cut edge is
the query box, not a boundary). Adds meta.parcels, appends 'parcels.json' to meta.files, sets houses[].parcel_id
(parcel containing the footprint centroid; null when none / no data).
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from shapely.geometry import Point, Polygon, box as sbox, shape
from shapely.geometry.polygon import orient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402
from usa.streets import Frame, Terrain  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
CACHE = ROOT / "usa" / "_cache" / "parcels_hoa"
CACHE.mkdir(parents=True, exist_ok=True)
PAD_M = 30.0
SEG_M = 2.0
S = requests.Session()
S.headers["User-Agent"] = "roofprint-usa-parcels/0.1 (open-data research; parcel + plat boundaries for 3D block viewer; python-requests)"

METC = "https://arcgis.metc.state.mn.us/data1/rest/services/parcels/Parcels_2025/FeatureServer/"
METC_FIELDS = "COUNTY_PIN,STATE_PIN,ANUMBERPRE,ANUMBER,ANUMBERSUF,ST_PRE_MOD,ST_PRE_DIR,ST_PRE_TYP,ST_PRE_SEP,ST_NAME,ST_POS_TYP,ST_POS_DIR,ST_POS_MOD,SUB_TYPE1,SUB_ID1,CTU_NAME,ZIP"
METC_LIC = {"licence": "Public domain (Minnesota Government Data Practices Act, Minn. Stat. ch. 13)",
            "licence_url": "https://www.arcgis.com/home/item.html?id=27d9f56cfec6454688110d08ae6826a0",
            "licence_quote": "None. This dataset is public domain under the Minnesota Government Data Practices Act (Minnesota Statutes Chapter 13)."}

PARCEL_SRC = {
    "Hennepin County": dict(id="metc_parcels_2025", name="Metropolitan Council Parcels_2025 layer 3 'Hennepin County Parcels'",
                            url=METC + "3", fields=METC_FIELDS, attribution="Metropolitan Council / Hennepin County (MetroGIS regional parcels 2025)",
                            borderline=False, **METC_LIC),
    "Ramsey County": dict(id="metc_parcels_2025", name="Metropolitan Council Parcels_2025 layer 4 'Ramsey County Parcels'",
                          url=METC + "4", fields=METC_FIELDS, attribution="Metropolitan Council / Ramsey County (MetroGIS regional parcels 2025)",
                          borderline=False, **METC_LIC),
    "Boulder County": dict(id="boco_parcels", name="Boulder County 'Parcels (with address and ownership information)' (owner fields not read)",
                           url="https://maps.bouldercounty.org/arcgis/rest/services/PARCELS/PARCELS_OWNER/MapServer/0",
                           fields="ParcelNo,AccountNo,StrNum,StrPrx,Street,StrSuf,StrUnit,StrCity",
                           licence="CC BY 4.0", licence_url="https://www.arcgis.com/home/item.html?id=90e3547382df4d498b779672d2685b0b",
                           licence_quote="The Boulder County Geospatial Open Data site provides open data licensed under the Creative Commons Attribution 4.0 International License",
                           attribution="Boulder County, Colorado - Parcels (CC BY 4.0)", borderline=False),
    "Denver County": dict(id="den_parcels", name="City and County of Denver ODC_PROP_PARCELS_A (parcel polygons)",
                          url="https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_PROP_PARCELS_A/FeatureServer/245",
                          fields="SCHEDNUM,SITUS_ADDRESS_LINE1,SITUS_CITY,SITUS_ZIP",
                          licence="CC BY 3.0 (Denver Open Data Catalog terms)", licence_url="https://opendata-geospatialdenver.hub.arcgis.com/",
                          licence_quote="The City and County of Denver Open Data Catalog provides open data licensed under the Creative Commons Attribution 3.0 license (CC BY 3.0).",
                          attribution="City and County of Denver Open Data Catalog (CC BY 3.0)", borderline=False),
    "Hamilton County": dict(id="cagis_parcels_opendata", name="Hamilton County Parcel Polygons (City of Cincinnati Open Data g24g-2pi5; CAGIS HCE/Cadastral layer 0)",
                            url="https://cagisonline.hamilton-co.org/arcgis/rest/services/HCE/Cadastral/MapServer/0",
                            fields="PARCELID,AUDPTYID,ADDRNO,ADDRST,ADDRSF,UNIT",
                            licence="Public Domain (data.cincinnati-oh.gov listing g24g-2pi5)", licence_url="https://data.cincinnati-oh.gov/d/g24g-2pi5",
                            licence_quote="Licence: Public Domain (City of Cincinnati Open Data listing 'Hamilton County Parcel Polygons')",
                            attribution="Hamilton County / CAGIS via City of Cincinnati Open Data", borderline=False),
}
# county -> state guard (Franklin / Hamilton exist in many states); Franklin County OH has HOA plats (City of Columbus CC0)
# only - its Auditor parcel/condo layers were removed (owner: no clear legal standing)
PARCEL_STATE = {"Hennepin County": "MN", "Ramsey County": "MN", "Boulder County": "CO", "Denver County": "CO",
                "Franklin County": "OH", "Hamilton County": "OH"}

K_HOA = "HOA (recorded)"
K_CONDO = "condo association"
K_SUB = "subdivision plat (possible covenants - not confirmed HOA)"

HOA_SRC = {
    "Boulder County": [dict(id="boco_platted", reader="boco", name="Boulder County Subdivisions and Other Platted Areas (HoaArcRef; unincorporated county only)",
                            url="https://maps.bouldercounty.org/arcgis/rest/services/PLANNING/LUC_PlattedAreas/MapServer/0",
                            fields="NAME_ALL,HoaArcRef,TYPE,REC_DATE,REC_NUM", licence="CC BY 4.0",
                            licence_url="https://www.arcgis.com/home/item.html?id=537483fcf5c9445a8f848ae426790f25",
                            attribution="Boulder County, Colorado - Subdivisions and Other Platted Areas (CC BY 4.0)", borderline=False)],
    "Franklin County": [dict(id="col_subdiv", reader="col", name="City of Columbus Subdivisions (plats)",
                             url="https://maps2.columbus.gov/arcgis/rest/services/Schemas/PublicService/MapServer/10",
                             fields="PLATNAME,PLATBOOK,PAGES,NOLOTS", licence="CC0 1.0 (City of Columbus)",
                             licence_url="https://www.arcgis.com/home/item.html?id=e0890bf8626f402ba336a03391f7a750",
                             attribution="City of Columbus (CC0)", borderline=False)],
    "Hamilton County": [dict(id="cagis_subdiv_opendata", reader="cin", name="Hamilton County Subdivision Boundaries (City of Cincinnati Open Data rynu-bywd; CAGIS HCE/Cadastral layer 3)",
                             url="https://cagisonline.hamilton-co.org/arcgis/rest/services/HCE/Cadastral/MapServer/3",
                             fields="NAME,TYPE,CONVEYANCETYPE,HISTORICAL,LEGALENDDATE", licence="Public Domain (data.cincinnati-oh.gov listing rynu-bywd)",
                             licence_url="https://data.cincinnati-oh.gov/d/rynu-bywd",
                             attribution="Hamilton County / CAGIS via City of Cincinnati Open Data", borderline=False)],
}

NO_PARCEL_NOTES = {
    "Franklin County": "Franklin County Auditor parcels removed (owner: no clear legal standing - public-domain statement only on the "
                       "Auditor's map item, the data service shows only a disclaimer); lot lines are not synthesized",
}

NO_HOA_NOTES = {
    "TX": "Texas TREC HOA management-certificate registry has no geometry (not mapped); City of Dallas HOA/NA layer and Austin subdivisions are grey (disclaimer only)",
    "MO": "KCMO Registered Homes Associations layer is grey (no licence; data.kcmo.org terms reserve IP)",
    "OK": "Tulsa County subdivisions are grey (no licence)",
}


def clean(v):
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def join(*parts):
    s = " ".join(p for p in (clean(x) for x in parts) if p)
    return s or None


def address_of(county, p):
    if county in ("Hennepin County", "Ramsey County"):
        num = "".join(x for x in (clean(p.get("ANUMBERPRE")), clean(p.get("ANUMBER")) if p.get("ANUMBER") not in (None, 0) else None,
                                  clean(p.get("ANUMBERSUF"))) if x)
        st = join(p.get("ST_PRE_MOD"), p.get("ST_PRE_DIR"), p.get("ST_PRE_TYP"), p.get("ST_PRE_SEP"), p.get("ST_NAME"),
                  p.get("ST_POS_TYP"), p.get("ST_POS_DIR"), p.get("ST_POS_MOD"))
        unit = join(p.get("SUB_TYPE1"), p.get("SUB_ID1"))
        a = join(num, st, unit)
        return a
    if county == "Boulder County":
        return join(p.get("StrNum"), p.get("StrPrx"), p.get("Street"), p.get("StrSuf"), ("#" + str(p["StrUnit"])) if clean(p.get("StrUnit")) else None)
    if county == "Denver County":
        return clean(p.get("SITUS_ADDRESS_LINE1"))
    if county == "Hamilton County":
        num = p.get("ADDRNO")
        return join(None if num in (None, 0, "0") else num, p.get("ADDRST"), p.get("ADDRSF"), ("Unit " + str(p["UNIT"])) if clean(p.get("UNIT")) else None)
    return None


def parcel_id_of(county, p):
    if county in ("Hennepin County", "Ramsey County"):
        return clean(p.get("COUNTY_PIN")) or clean(p.get("STATE_PIN"))
    if county == "Boulder County":
        return clean(p.get("ParcelNo")) or clean(p.get("AccountNo"))
    if county == "Denver County":
        return clean(p.get("SCHEDNUM"))
    if county == "Hamilton County":
        return clean(p.get("PARCELID")) or clean(p.get("AUDPTYID"))
    return None


# ------------------------------------------------------------------ fetch
def query(url, bbox, fields, slug, tag, refresh=False):
    """All polygons intersecting the WGS84 envelope as GeoJSON features (attributes limited to `fields`)."""
    f = CACHE / f"{slug}_{tag}.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text())
    feats, offset = [], 0
    while True:
        p = {"where": "1=1", "outFields": fields, "returnGeometry": "true", "outSR": 4326, "f": "geojson",
             "geometry": ",".join(f"{v:.7f}" for v in bbox), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
             "spatialRel": "esriSpatialRelIntersects", "resultOffset": offset, "resultRecordCount": 1000}
        last = None
        for i in range(4):
            try:
                r = S.get(url.rstrip("/") + "/query", params=p, timeout=90)
                r.raise_for_status()
                j = r.json()
                if "error" in j:
                    raise RuntimeError(f"{j['error'].get('code')}: {j['error'].get('message')}")
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 * (i + 1))
        else:
            raise last
        batch = j.get("features") or []
        feats += batch
        more = j.get("exceededTransferLimit") or (j.get("properties") or {}).get("exceededTransferLimit")
        if not batch or not more:
            break
        offset += len(batch)
    # guard: drop any attribute we did not ask for (owner rule)
    keep = set(fields.split(","))
    for ft in feats:
        ft["properties"] = {k: v for k, v in (ft.get("properties") or {}).items() if k in keep}
    f.write_text(json.dumps(feats))
    return feats


# ------------------------------------------------------------------ geometry
def local_polys(geom, fr, clipbox):
    """GeoJSON (WGS84) -> list of (shapely Polygon in local metres clipped to clipbox, clipped flag)."""
    if not geom:
        return []
    g = shape(geom)
    if g.is_empty:
        return []
    parts = list(g.geoms) if g.geom_type == "MultiPolygon" else [g] if g.geom_type == "Polygon" else []
    out = []
    for poly in parts:
        ex = np.asarray(poly.exterior.coords)
        x, y = fr.local(ex[:, 0], ex[:, 1])
        holes = []
        for h in poly.interiors:
            hc = np.asarray(h.coords)
            hx, hy = fr.local(hc[:, 0], hc[:, 1])
            holes.append(list(zip(hx, hy)))
        lp = Polygon(list(zip(x, y)), holes)
        if not lp.is_valid:
            lp = lp.buffer(0)
        if lp.is_empty:
            continue
        inside = clipbox.contains(lp)
        cp = lp if inside else lp.intersection(clipbox)
        for q in (list(cp.geoms) if cp.geom_type == "MultiPolygon" else [cp] if cp.geom_type == "Polygon" else []):
            if q.area >= 0.5:
                out.append((orient(q, 1.0), not inside, lp.area))
    return out


def densify(coords, seg=SEG_M):
    c = list(coords)
    out = []
    for a, b in zip(c[:-1], c[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(d / seg)))
        for i in range(n):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    out.append(c[-1])
    return out


def ring3(coords, ter):
    pts = densify(coords)
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    zs = ter.z(xs, ys)
    return [[round(float(x), 2), round(float(y), 2), round(float(z), 2)] for x, y, z in zip(xs, ys, zs)]


# ------------------------------------------------------------------ HOA readers
def hoa_rows(src, feats):
    """-> [(attrs dict for output, geometry)] following the green kind rules."""
    out = []
    for ft in feats:
        p, g = ft.get("properties") or {}, ft.get("geometry")
        r = src["reader"]
        if r == "boco":
            if (p.get("HoaArcRef") or "").strip().upper() != "YES":
                continue
            out.append(({"name": clean(p.get("NAME_ALL")), "kind": K_HOA, "confidence": "medium",
                         "note": "Boulder County plat / land-use approval references an HOA (HoaArcRef = Yes)",
                         "plat_type": clean(p.get("TYPE")), "rec_num": clean(p.get("REC_NUM"))}, g))
        elif r == "col":
            out.append(({"name": clean(p.get("PLATNAME")), "kind": K_SUB, "confidence": "low",
                         "note": "recorded subdivision plat; deed restrictions may exist - no HOA confirmed",
                         "plat_book": clean(p.get("PLATBOOK")), "pages": clean(p.get("PAGES"))}, g))
        elif r == "cin":
            if p.get("HISTORICAL") == 1 or p.get("LEGALENDDATE"):
                continue
            t = (p.get("CONVEYANCETYPE") or "").upper()
            if "CONDO" in t:
                out.append(({"name": clean(p.get("NAME")), "kind": K_CONDO, "confidence": "medium",
                             "note": "recorded condominium plat (Ohio Rev. Code 5311 requires a unit owners' association)"}, g))
            elif "SUBDIV" in t:
                out.append(({"name": clean(p.get("NAME")), "kind": K_SUB, "confidence": "low",
                             "note": "recorded subdivision plat; deed restrictions may exist - no HOA confirmed"}, g))
    return out


# ------------------------------------------------------------------ per block
def build(slug, refresh=False):
    bdir = BLOCKS / slug
    meta0 = json.loads((bdir / "meta.json").read_text())
    fr = Frame(meta0)
    half = float(meta0["half_m"])
    R = half + PAD_M
    clipbox = sbox(-R, -R, R, R)
    lo, la = fr.ll([-R, R], [-R, R])
    bbox = (float(lo[0]), float(la[0]), float(lo[1]), float(la[1]))
    county, state = meta0.get("county"), meta0.get("state")
    psrc = PARCEL_SRC.get(county) if PARCEL_STATE.get(county) == state else None
    hsrcs = HOA_SRC.get(county, []) if PARCEL_STATE.get(county) == state else []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    ter = None
    if psrc or hsrcs:
        ter = Terrain(bdir, fr, slug)

    parcels, pstatus, perr = [], "no open parcel data", None
    if psrc:
        try:
            feats = query(psrc["url"], bbox, psrc["fields"], slug, "parcels", refresh)
            seen = {}
            for ft in feats:
                p = ft.get("properties") or {}
                pid = parcel_id_of(county, p)
                for q, clipped, full_area in local_polys(ft.get("geometry"), fr, clipbox):
                    key = (pid, round(q.centroid.x, 1), round(q.centroid.y, 1), round(q.area))
                    if key in seen:  # same parcel returned twice (e.g. one row per owner record)
                        continue
                    seen[key] = 1
                    rec = {"id": pid, "ring": ring3(q.exterior.coords, ter), "address": address_of(county, p),
                           "area_m2": round(full_area, 1), "source": psrc["id"]}
                    if q.interiors:
                        rec["holes"] = [ring3(h.coords, ter) for h in q.interiors]
                    if clipped:
                        rec["clipped"] = True
                    rec["_poly"] = q
                    parcels.append(rec)
            pstatus = "ok" if parcels else "ok (no parcels in box)"
        except Exception as e:  # noqa: BLE001
            perr = str(e)[:200]
            pstatus = f"source error: {perr}"
            print("  parcels failed:", perr)

    hoa, hoa_src_used, hoa_errs = [], [], {}
    for src in hsrcs:
        try:
            feats = query(src["url"], bbox, src["fields"], slug, "hoa_" + src["id"], refresh)
            rows = hoa_rows(src, feats)
            for o, g in rows:
                for q, clipped, full_area in local_polys(g, fr, clipbox):
                    rec = dict(o)
                    rec["ring"] = ring3(q.exterior.coords, ter)
                    rec["source"] = src["id"]
                    rec["area_m2"] = round(full_area, 1)
                    if clipped:
                        rec["clipped"] = True
                    rec["_poly"] = q
                    hoa.append(rec)
            hoa_src_used.append(src)
        except Exception as e:  # noqa: BLE001
            hoa_errs[src["id"]] = str(e)[:200]
            print(f"  {src['id']} failed:", str(e)[:200])

    # ---- link houses
    with block_lock(bdir):
        meta = json.loads((bdir / "meta.json").read_text())
        houses = json.loads((bdir / "houses.json").read_text())
        linked, ambiguous = 0, 0
        for h in houses:
            h["parcel_id"] = None
            ring = h.get("ring") or []
            if len(ring) < 3 or not parcels:
                continue
            fp = Polygon([(c[0], c[1]) for c in ring])
            c = fp.centroid if fp.is_valid and not fp.is_empty else Point(np.mean([r[0] for r in ring]), np.mean([r[1] for r in ring]))
            hits = [p for p in parcels if p["_poly"].contains(c)]
            ids = sorted({p["id"] for p in hits if p["id"]})
            if ids:
                # stacked condo-unit parcels share one footprint: take the smallest polygon, report the ambiguity
                best = min(hits, key=lambda p: p["_poly"].area)
                h["parcel_id"] = best["id"]
                linked += 1
                if len(ids) > 1:
                    ambiguous += 1
        for hz in hoa:
            hz["houses"] = [h["id"] for h in houses if len(h.get("ring") or []) >= 3
                            and hz["_poly"].contains(Polygon([(c[0], c[1]) for c in h["ring"]]).centroid)]
        for x in parcels + hoa:
            x.pop("_poly", None)

        kinds = {}
        for hz in hoa:
            kinds[hz["kind"]] = kinds.get(hz["kind"], 0) + 1
        if kinds.get(K_HOA) or kinds.get(K_CONDO):
            hoa_status = "recorded HOA"
        elif kinds.get(K_SUB):
            hoa_status = "possible covenants"
        else:
            hoa_status = "no open HOA data"

        licences = []
        if psrc:
            licences.append({"source": psrc["id"], "name": psrc["name"], "licence": psrc["licence"], "licence_url": psrc["licence_url"],
                             "attribution": psrc["attribution"], "borderline": psrc["borderline"], "use": "parcels"})
        for s in hoa_src_used:
            licences.append({"source": s["id"], "name": s["name"], "licence": s["licence"], "licence_url": s["licence_url"],
                             "attribution": s["attribution"], "borderline": s["borderline"], "use": "hoa"})
        out_meta = {
            "generated": now,
            "source": psrc["name"] if psrc else None,
            "licence": psrc["licence"] if psrc else None,
            "attribution": "; ".join(sorted({l["attribution"] for l in licences})) or None,
            "licences": licences,
            "frame": "local metres east/north of meta.origin (houses.json frame); z above meta.z_base from block terrain "
                     "(LiDAR mesh inside the square, USGS 3DEP DEM offset to it outside); edges densified to <= 2 m",
            "query_box_m": R,
            "clip_note": "polygons cut to the query box carry clipped: true - the cut edge is the box, not a lot line",
            "fields_requested": {"parcels": psrc["fields"] if psrc else None, **{s["id"]: s["fields"] for s in hoa_src_used}},
            "owner_rule": "no owner / mailing / tax-name / sale fields requested; only geometry, parcel id, situs address",
            "hoa_rule": "absence of data is 'no open HOA data', never 'non-HOA'; 'subdivision plat' = possible covenants, not a confirmed HOA",
        }
        if psrc and psrc.get("borderline"):
            out_meta["borderline"] = psrc["borderline_note"]
        doc = {"parcels": parcels, "hoa": hoa, "meta": out_meta}
        tmp = bdir / "parcels.json.tmp"
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        os.replace(tmp, bdir / "parcels.json")

        summ = {"file": "parcels.json", "generated": now, "status": pstatus, "count": len(parcels),
                "unique_ids": len({p["id"] for p in parcels if p["id"]}),
                "source": psrc["name"] if psrc else None, "licence": psrc["licence"] if psrc else None,
                "attribution": out_meta["attribution"], "borderline": bool(psrc and psrc["borderline"]),
                "houses_linked": linked, "houses_total": len(houses), "houses_ambiguous": ambiguous,
                "hoa_status": hoa_status, "hoa_counts": kinds,
                "hoa_sources": [s["id"] for s in hoa_src_used], "hoa_licences": sorted({s["licence"] for s in hoa_src_used}),
                "method": "usa/research/parcels_hoa.md"}
        if not psrc:
            summ["note"] = NO_PARCEL_NOTES.get(county, "no green (explicit commercial-use) parcel layer for this county; lot lines are not synthesized")
        if hoa_errs:
            summ["hoa_errors"] = hoa_errs
        if not hsrcs:
            summ["hoa_note"] = NO_HOA_NOTES.get(state, "no green HOA / plat polygon layer found for this county")
        meta["parcels"] = summ
        files = meta.get("files")
        if isinstance(files, list) and "parcels.json" not in files:
            files.append("parcels.json")
        tmp = bdir / "houses.json.tmp"
        tmp.write_text(json.dumps(houses, separators=(",", ":")))
        os.replace(tmp, bdir / "houses.json")
        tmp = bdir / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1))
        os.replace(tmp, bdir / "meta.json")
    print(f"{slug}: parcels {len(parcels)} ({pstatus}), linked {linked}/{len(houses)} (ambiguous {ambiguous}), "
          f"hoa {kinds or '-'} -> {hoa_status}")
    return summ


def main():
    a = argparse.ArgumentParser()
    a.add_argument("slugs", nargs="*")
    a.add_argument("--refresh", action="store_true")
    a = a.parse_args()
    slugs = a.slugs or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists() and (p / "houses.json").exists())
    for s in slugs:
        try:
            build(s, a.refresh)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: FAILED {e}")


if __name__ == "__main__":
    main()

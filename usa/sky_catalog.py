"""Build the night-sky data for the 3D block viewer (stars, constellation figures, licences).

Outputs (compact JSON, J2000 equatorial degrees):
  web/usa/data/stars.json           naked-eye stars (V < 6.5) with B-V colour, HIP id, IAU name
  web/usa/data/constellations.json  88 constellation stick figures + label positions
  web/usa/data/sky_licences.json    per-source licence records + combined attribution

Sources (licences checked on the primary pages on 2026-11-24, see LICENCES below):
  * d3-celestial (Olaf Frohn, BSD-3-Clause): stars.8.json (XHIP-derived stars, filtered to V<6.5),
    constellations.lines.json (lines after the IAU / Sky & Telescope charts), constellations.json (labels)
  * IAU Catalog of Star Names (IAU WGSN, CC BY 4.0): proper names by HIP number

Usage:
  .venv/Scripts/python -m usa.sky_catalog            # default build
  .venv/Scripts/python -m usa.sky_catalog --strict   # stars only from the IAU WGSN list (CC BY 4.0,
                                                     # ~450 named stars, no B-V) -- no ESA-derived data
Downloads are cached under usa/_cache/sky/ (git-ignored); --refresh re-downloads.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "usa" / "_cache" / "sky"
OUT = ROOT / "web" / "usa" / "data"

D3 = "https://raw.githubusercontent.com/ofrohn/d3-celestial/master"
DOWNLOADS = {
    "d3_stars.8.json": f"{D3}/data/stars.8.json",
    "d3_constellations.json": f"{D3}/data/constellations.json",
    "d3_constellations.lines.json": f"{D3}/data/constellations.lines.json",
    "d3_LICENSE.txt": f"{D3}/LICENSE",
    "IAU-CSN.txt": "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt",
}
MAG_LIMIT = 6.5
CHECKED = "2026-11-24"

LICENCES = {
    "checked": CHECKED,
    "policy": "Only permanently free data whose terms allow commercial use and redistribution. "
              "Verdicts: OK / OK-attribution / OK-share-alike / verify (unclear) / excluded.",
    "used": [
        {
            "name": "d3-celestial data (stars.8.json, constellations.lines.json, constellations.json)",
            "url": "https://github.com/ofrohn/d3-celestial",
            "licence": "BSD-3-Clause",
            "licence_url": "https://github.com/ofrohn/d3-celestial/blob/master/LICENSE",
            "licence_text": "Copyright (c) 2015, Olaf Frohn. All rights reserved. Redistribution and use in source "
                            "and binary forms, with or without modification, are permitted provided that the "
                            "copyright notice, conditions and disclaimer are retained/reproduced and the author's "
                            "name is not used to endorse derived products. Readme: 'Released under BSD License'.",
            "attribution": "Star and constellation-figure data: d3-celestial (c) 2015 Olaf Frohn, BSD-3-Clause",
            "used_for": ["stars.json: ra, dec, mag, bv, hip", "constellations.json: lines and labels"],
            "verdict": "OK-attribution (BSD-3). Caveat 'verify upstream': the star values come from XHIP "
                       "(Anderson & Francis 2012, CDS V/137D), itself compiled from the ESA Hipparcos "
                       "catalogue, which ESA now distributes under CC BY-NC 3.0 IGO (commercial use needs ESA "
                       "authorisation, data.licences@esa.int). Star positions/magnitudes are facts, but if the "
                       "upstream claim matters use --strict. Lines/labels follow the IAU charts (CC BY 4.0).",
        },
        {
            "name": "IAU constellation charts (IAU + Sky & Telescope; figures by Alan MacRobert)",
            "url": "https://www.iau.org/IAU/IAU/Astronomy-FAQs/Constellations.aspx",
            "licence": "CC BY 4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "licence_text": "IAU page: 'The images are released under the Creative Commons Attribution 4.0 "
                            "International license.'",
            "attribution": "Constellation figures after the IAU and Sky & Telescope charts (Roger Sinnott, Rick "
                           "Fienberg, Alan MacRobert), CC BY 4.0",
            "used_for": ["upstream of the d3-celestial constellation lines"],
            "verdict": "OK-attribution",
        },
        {
            "name": "IAU Catalog of Star Names (IAU-CSN), IAU Working Group on Star Names",
            "url": "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt",
            "licence": "CC BY (IAU products); IAU site uses CC BY 4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "licence_text": "File header (WGSN): 'All IAU-produced products (Images, Videos, Texts) are released "
                            "under Creative Commons Attribution (i.e. free to use in all perpetuity, world-wide, as "
                            "long as the source is mentioned).'",
            "attribution": "Star names: IAU Working Group on Star Names (WGSN), IAU Catalog of Star Names, CC BY",
            "used_for": ["stars.json: name (by HIP number)", "--strict mode: all star rows"],
            "verdict": "OK-attribution",
        },
    ],
    "rejected": [
        {
            "name": "Yale Bright Star Catalogue BSC5 (Hoffleit & Warren 1991; CDS V/50; HEASARC bsc5p)",
            "url": "https://heasarc.gsfc.nasa.gov/W3Browse/star-catalog/bsc5p.html",
            "licence": "none stated",
            "verdict": "verify / excluded: HEASARC states it provides the catalogue 'solely for the purposes "
                       "of scientific research', does not own the copyright and cannot clarify commercial terms; "
                       "Yale University Observatory and/or the authors may retain copyright.",
        },
        {
            "name": "Hipparcos and Tycho Catalogues (ESA SP-1200; CDS I/239, I/259 Tycho-2)",
            "url": "https://www.cosmos.esa.int/web/hipparcos/catalogues",
            "licence": "CC BY-NC 3.0 IGO",
            "verdict": "excluded: non-commercial. ESA science archive T&C "
                       "(https://www.cosmos.esa.int/web/esdc/terms-and-conditions) require a request for "
                       "authorisation/licence before any commercial use.",
        },
        {
            "name": "Gaia DR3 (ESA / DPAC)",
            "url": "https://www.cosmos.esa.int/web/gaia-users/license",
            "licence": "CC BY-NC 3.0 IGO",
            "verdict": "excluded: non-commercial (page: 'Gaia data are distributed under the CC BY-NC 3.0 IGO "
                       "license'); also saturates for the brightest stars.",
        },
        {
            "name": "SKY2000 Master Catalog v5 (Myers et al., NASA GSFC Flight Dynamics Division; CDS V/145)",
            "url": "https://vizier.cfa.harvard.edu/ftp/cats/V/145/ReadMe",
            "licence": "none stated",
            "verdict": "verify / not adopted. Checked the CDS ReadMe (CfA mirror; CDS itself unreachable), the "
                       "v5 rev 4 description (sky2kv5r4.pdf) and notes.txt: none contains a licence, terms of "
                       "use, copyright or public-domain / US-government-work statement. HEASARC does not host it "
                       "(no W3Browse sky2kv5 page, no table in the HEASARC TAP schema). The system documents it "
                       "cites are Computer Sciences Corporation contractor reports, so federal-work status is "
                       "not established. Its positions also come mostly from Hipparcos/Tycho (notes.txt: "
                       "95,332 HIP + 12,111 HIP components + 12,015 Tycho + 178,231 ACT positions), so it would "
                       "not remove the ESA upstream question anyway.",
        },
        {
            "name": "HYG database v4.x (astronexus)",
            "url": "https://codeberg.org/astronexus/hyg",
            "licence": "CC BY-SA 4.0",
            "verdict": "not used: share-alike, and compiled from Hipparcos (now ESA CC BY-NC) + Yale BSC "
                       "(unclear) + Gliese, so the same upstream question as d3-celestial with an added SA "
                       "obligation. d3-celestial (BSD-3) is the cleaner redistribution.",
        },
        {
            "name": "Stellarium 'modern' sky culture (constellation lines, names)",
            "url": "https://github.com/Stellarium/stellarium/tree/master/skycultures/modern",
            "licence": "Text and data: CC BY-SA 4.0; illustrations: Free Art License",
            "verdict": "acceptable as share-alike but not needed: d3-celestial lines are BSD-3 + IAU CC BY 4.0.",
        },
        {
            "name": "d3-celestial mw.json (Milky Way outline)",
            "url": "https://github.com/ofrohn/d3-celestial/blob/master/data/mw.json",
            "licence": "BSD-3 in repo, but derived from the 'Milky Way Outline Catalog' by Jose R. Vieira "
                       "(skymap.com, page now 404, no licence found)",
            "verdict": "excluded: upstream licence unclear. No milkyway polygons in constellations.json.",
        },
        {
            "name": "IAU constellation boundaries (Davenhall & Leggett 1989, CDS VI/49)",
            "url": "https://cdsarc.cds.unistra.fr/viz-bin/cat/VI/49",
            "licence": "not verified (CDS servers unreachable from this machine on " + CHECKED + ")",
            "verdict": "not included (optional).",
        },
    ],
}


def fetch(name: str, refresh: bool = False) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if refresh or not p.exists() or p.stat().st_size == 0:
        r = requests.get(DOWNLOADS[name], timeout=120, headers={"User-Agent": "sky-catalog-builder"})
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def read_wgsn(path: Path) -> list[dict]:
    """Parse IAU-CSN.txt (fixed-width name columns, whitespace for the tail)."""
    out = []
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line[0] in "#$":
            continue
        name = line[18:36].strip() or line[0:18].strip()
        toks = line.split()
        di = next((i for i in range(len(toks) - 1, 0, -1) if date_re.match(toks[i])), None)
        if di is None:
            continue
        hip, ra, dec = toks[di - 4], toks[di - 2], toks[di - 1]
        mag = toks[di - 6] if di >= 6 else "_"
        con = None
        m = re.search(r"\s([A-Z][a-z]{2})\s", line[36:60])
        if m:
            con = m.group(1)

        def num(s):
            try:
                return float(s)
            except ValueError:
                return None
        out.append({"name": name, "hip": int(hip) if hip.isdigit() else None,
                    "ra": num(ra), "dec": num(dec), "mag": num(mag), "con": con})
    return out


def build_stars(wgsn: list[dict], strict: bool, refresh: bool) -> dict:
    names = {}
    for w in wgsn:
        if w["hip"] and w["hip"] not in names:
            names[w["hip"]] = w["name"]
    rows = []
    if strict:
        for w in wgsn:
            if w["ra"] is None or w["mag"] is None or w["mag"] >= MAG_LIMIT:
                continue
            rows.append([round(w["ra"], 4), round(w["dec"], 4), round(w["mag"], 2), None, w["hip"], w["name"]])
        source = "IAU WGSN IAU-CSN (strict mode)"
    else:
        feats = json.loads(fetch("d3_stars.8.json", refresh).read_text(encoding="utf-8"))["features"]
        for f in feats:
            mag = f["properties"]["mag"]
            if mag is None or mag >= MAG_LIMIT:
                continue
            lon, lat = f["geometry"]["coordinates"]
            bv = f["properties"].get("bv")
            try:
                bv = round(float(bv), 2)
            except (TypeError, ValueError):
                bv = None
            hip = int(f["id"])
            rows.append([round(lon % 360.0, 4), round(lat, 4), round(mag, 2), bv, hip, names.get(hip)])
        source = "d3-celestial stars.8.json (XHIP) filtered to V<6.5; names IAU WGSN"
    rows.sort(key=lambda r: r[2])
    return {"epoch": "J2000", "frame": "equatorial (ICRS/J2000), degrees", "mag_limit": MAG_LIMIT,
            "source": source, "fields": ["ra", "dec", "mag", "bv", "hip", "name"], "rows": rows}


def build_constellations(refresh: bool) -> dict:
    meta = json.loads(fetch("d3_constellations.json", refresh).read_text(encoding="utf-8"))["features"]
    lines = json.loads(fetch("d3_constellations.lines.json", refresh).read_text(encoding="utf-8"))["features"]
    order, names, labels = [], {}, {}
    for f in meta:
        cid = f["id"]
        nm = f["properties"]["name"]
        lon, lat = f["geometry"]["coordinates"]
        part = {"name": nm, "ra": round(lon % 360.0, 3), "dec": round(lat, 3)}
        if cid not in labels:
            order.append(cid)
            names[cid] = "Serpens" if cid == "Ser" else nm
            labels[cid] = {"id": cid, "name": names[cid], "ra": part["ra"], "dec": part["dec"]}
        labels[cid].setdefault("parts", [])
        labels[cid]["parts"].append(part)
    for lab in labels.values():
        if len(lab["parts"]) < 2:
            del lab["parts"]
    segs: dict[str, list] = {cid: [] for cid in order}
    for f in lines:
        for poly in f["geometry"]["coordinates"]:
            pts = [[round(lon % 360.0, 4), round(lat, 4)] for lon, lat in poly]
            for a, b in zip(pts, pts[1:]):
                if a != b:
                    segs.setdefault(f["id"], []).append([a, b])
    return {"epoch": "J2000",
            "source": "d3-celestial constellations.lines.json / constellations.json (after IAU + Sky & Telescope)",
            "lines": [{"id": cid, "name": names[cid], "segments": segs.get(cid, [])} for cid in order],
            "labels": [labels[cid] for cid in order]}


def write_json(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(s, encoding="utf-8")
    return path.stat().st_size


def sanity(stars: dict, cons: dict) -> list[str]:
    msgs = []
    by_hip = {r[4]: r for r in stars["rows"]}
    msgs.append(f"stars: {len(stars['rows'])} rows (V<{MAG_LIMIT}); named: {sum(1 for r in stars['rows'] if r[5])}")
    for hip, label in ((32349, "Sirius"), (11767, "Polaris"), (27989, "Betelgeuse")):
        msgs.append(f"{label}: {by_hip.get(hip)}")
    msgs.append(f"constellations: {len(cons['lines'])} line sets, {len(cons['labels'])} labels, "
                f"{sum(len(c['segments']) for c in cons['lines'])} segments, empty: "
                f"{[c['id'] for c in cons['lines'] if not c['segments']]}")
    ori = next(c for c in cons["lines"] if c["id"] == "Ori")
    belt = {(83.0017, -0.2991), (84.0534, -1.2019), (85.1897, -1.9426)}  # Mintaka, Alnilam, Alnitak
    belt_segs = [s for s in ori["segments"] if {tuple(s[0]), tuple(s[1])} <= belt]
    msgs.append(f"Orion: {len(ori['segments'])} segments, belt segments: {belt_segs}")
    return msgs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="stars only from IAU WGSN (no ESA-derived values)")
    ap.add_argument("--refresh", action="store_true", help="re-download cached sources")
    a = ap.parse_args(argv)

    fetch("d3_LICENSE.txt", a.refresh)
    wgsn = read_wgsn(fetch("IAU-CSN.txt", a.refresh))
    stars = build_stars(wgsn, a.strict, a.refresh)
    cons = build_constellations(a.refresh)
    lic = dict(LICENCES)
    lic["mode"] = "strict" if a.strict else "default"
    lic["combined_attribution"] = (
        ("Star names & positions: IAU WGSN (CC BY). " if a.strict else
         "Stars & constellation figures: d3-celestial (c) 2015 Olaf Frohn, BSD-3-Clause; "
         "star names: IAU WGSN (CC BY). ")
        + "Constellation figures after IAU / Sky & Telescope charts (CC BY 4.0)."
    )
    sizes = {
        "stars.json": write_json(OUT / "stars.json", stars),
        "constellations.json": write_json(OUT / "constellations.json", cons),
        "sky_licences.json": write_json(OUT / "sky_licences.json", lic),
    }
    for m in sanity(stars, cons):
        print(m)
    for k, v in sizes.items():
        print(f"{k}: {v / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

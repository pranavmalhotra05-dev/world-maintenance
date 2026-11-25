"""Local OpenStreetMap reader on Geofabrik extracts (replaces the public Overpass / QLever endpoints).

Why: the public Overpass servers' usage policy asks commercial users to self-host, and QLever states no terms.
The OSM data itself is ODbL 1.0 (commercial use + redistribution allowed with attribution + share-alike on derived
databases), so we download it once from Geofabrik and query it locally with pyosmium.

Geofabrik terms (https://download.geofabrik.de/ and https://www.geofabrik.de/data/download.html, checked 2026-11-24):
  the extracts are OpenStreetMap data, "available under the Open Database License" -- see GEOFABRIK_TERMS below
  for the exact quote recorded at build time.

Cache: usa/_cache/osm/<region>-latest.osm.pbf  (+ .md5, + query result cache under usa/_cache/osm/q/)

API
  path = download("minnesota")                     # or "us" for the whole north-america/us extract
  feats = query("minnesota",
                nodes=[("highway", "traffic_signals"), ("emergency", "fire_hydrant")],
                ways=[("power", "line")], rels=[],
                bbox=(w, s, e, n), match=lambda kind, tags: True)
  -> [{"type": "n"|"w"|"r", "id": int, "tags": {...}, "lon": float, "lat": float,   # centroid / node position
       "coords": [[lon, lat], ...] (ways), "parts": [[[lon, lat], ...], ...] (relations)}]

  A (key, None) pair means "has key". bbox keeps nodes inside it and ways/relations with any vertex inside it.
CLI
  python usa/osm_extract.py prefetch [region ...]   (default: the project states MN TX CO OH OK MO AL AZ)
"""
import hashlib
import json
import sys
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "usa" / "_cache" / "osm"
QCACHE = CACHE / "q"
BASE = "https://download.geofabrik.de/north-america/"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright); extract via Geofabrik GmbH"
LICENCE = "ODbL 1.0"
GEOFABRIK_TERMS = {
    "url": "https://download.geofabrik.de/",
    "quote": "\"This open data download service is offered free of charge by Geofabrik GmbH.\" ... footer: \"Data processed by "
             "Geofabrik GmbH and created by OpenStreetMap Contributors | License: ODbL 1.0\"; technical.html: \"Our .osm.pbf files "
             "are 100% pure, unfiltered OSM\" (no further usage restrictions stated). Checked 2026-11-24.",
    "verdict": "green (ODbL 1.0: commercial use + redistribution with attribution; share-alike on derived databases)",
}
# every US state + DC + PR (Geofabrik north-america/us sub-region names)
STATES = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado", "CT": "connecticut",
                "DE": "delaware", "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
                "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
                "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
                "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
                "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
                "PR": "puerto-rico", "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee", "TX": "texas",
                "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming"}
DEFAULT_STATES = ["MN", "TX", "CO", "OH", "OK", "MO", "AL", "AZ"]
UA = {"User-Agent": "roofprint-osm/0.1 (open-data research)"}


def _url(region):
    region = STATES.get(region, region)
    return BASE + ("us-latest.osm.pbf" if region == "us" else f"us/{region}-latest.osm.pbf"), region


def log(*a):
    print(time.strftime("%H:%M:%S"), "[osm]", *a, flush=True)


def download(region, max_age_days=45):
    """Download (or reuse) a Geofabrik extract. One process downloads a given extract at a time
    (exclusive lock file); others wait, then reuse the finished file."""
    url, _ = _url(region)
    CACHE.mkdir(parents=True, exist_ok=True)
    lock = CACHE / (url.rsplit("/", 1)[1] + ".dl.lock")
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd); break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 4 * 3600:   # stale lock from a killed process
                lock.unlink(missing_ok=True); continue
            if time.time() - t0 > 4 * 3600:
                raise TimeoutError(f"waited 4 h for another download of {url}")
            time.sleep(10)
    try:
        return _download(region, max_age_days)
    finally:
        lock.unlink(missing_ok=True)


def _download(region, max_age_days=45):
    """Download (or reuse) a Geofabrik extract; verifies the published .md5. Resumes partial downloads."""
    url, region = _url(region)
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / url.rsplit("/", 1)[1]
    if out.exists() and (time.time() - out.stat().st_mtime) < max_age_days * 86400 and (CACHE / (out.name + ".ok")).exists():
        return out
    # "-latest" redirects to a dated file (e.g. us/us-261123.osm.pbf); pin it so a resumed download never mixes two days
    for _ in range(5):
        r = requests.head(url, headers=UA, timeout=60, allow_redirects=False)
        if r.status_code in (301, 302, 307, 308) and r.headers.get("Location"):
            url = requests.compat.urljoin(url, r.headers["Location"])
        else:
            break
    md5 = requests.get(url + ".md5", headers=UA, timeout=60).text.split()[0]
    part = out.with_suffix(out.suffix + ".part")
    for attempt in range(8):
        have = part.stat().st_size if part.exists() else 0
        h = {**UA, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with requests.get(url, headers=h, stream=True, timeout=120) as r:
                if r.status_code == 416:
                    break
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0
                total = have + int(r.headers.get("content-length", 0))
                log(f"download {url} ({total / 1e9:.2f} GB, from {have / 1e9:.2f})")
                t0, last = time.time(), 0
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(1 << 22):
                        f.write(chunk); have += len(chunk)
                        if time.time() - t0 > last + 60:
                            last = time.time() - t0; log(f"  {region}: {have / 1e9:.2f}/{total / 1e9:.2f} GB")
            break
        except Exception as e:  # noqa: BLE001
            log(f"  {region} attempt {attempt} failed: {e}"); time.sleep(10 * (attempt + 1))
    hm = hashlib.md5()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            hm.update(chunk)
    if hm.hexdigest() != md5:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"md5 mismatch for {url}")
    part.replace(out)
    (CACHE / (out.name + ".ok")).write_text(json.dumps({"url": url, "md5": md5, "fetched": time.strftime("%Y-%m-%d")}))
    log(f"ok {out.name} {out.stat().st_size / 1e9:.2f} GB")
    return out


def _filter(pairs):
    import osmium
    if all(v is not None for _, v in pairs):
        return osmium.filter.TagFilter(*pairs)
    return osmium.filter.KeyFilter(*sorted({k for k, _ in pairs}))


def _hit(tags, pairs):
    return any(k in tags and (v is None or tags[k] == v) for k, v in pairs)


def _in(bbox, lon, lat):
    return bbox is None or (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3])


def query(region, nodes=(), ways=(), rels=(), bbox=None, match=None, cache=True):
    """Tagged nodes / ways / relations from a local extract (see module doc). Results cached per extract date + spec."""
    import osmium
    src = download(region)
    key = json.dumps([src.name, int(src.stat().st_mtime), list(map(list, nodes)), list(map(list, ways)), list(map(list, rels)),
                      bbox, getattr(match, "__name__", None) if match else None], sort_keys=True)
    qf = QCACHE / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
    if cache and qf.exists():
        return json.loads(qf.read_text("utf-8"))
    ok = (lambda kind, t: match(kind, t)) if match else (lambda kind, t: True)
    out, t0 = [], time.time()
    # 1. tagged nodes (position known immediately)
    if nodes:
        for o in osmium.FileProcessor(str(src), osmium.osm.NODE).with_filter(_filter(nodes)):
            t = dict(o.tags)
            if o.location.valid() and _hit(t, nodes) and ok("n", t) and _in(bbox, o.location.lon, o.location.lat):
                out.append({"type": "n", "id": o.id, "tags": t, "lon": o.location.lon, "lat": o.location.lat})
    # 2. tagged ways / relations (geometry resolved below)
    W, R, need_w = {}, {}, set()
    if ways:
        for o in osmium.FileProcessor(str(src), osmium.osm.WAY).with_filter(_filter(ways)):
            t = dict(o.tags)
            if _hit(t, ways) and ok("w", t):
                W[o.id] = {"tags": t, "refs": [n.ref for n in o.nodes]}
    if rels:
        for o in osmium.FileProcessor(str(src), osmium.osm.RELATION).with_filter(_filter(rels)):
            t = dict(o.tags)
            if _hit(t, rels) and ok("r", t):
                R[o.id] = {"tags": t, "ways": [m.ref for m in o.members if m.type == "w"],
                           "nodes": [m.ref for m in o.members if m.type == "n"]}
                need_w.update(R[o.id]["ways"])
    MW = {}
    need_w -= set(W)
    if need_w:
        for o in osmium.FileProcessor(str(src), osmium.osm.WAY).with_filter(osmium.filter.IdFilter(need_w)):
            MW[o.id] = [n.ref for n in o.nodes]
    for i in W:
        MW[i] = W[i]["refs"]
    need_n = {n for refs in MW.values() for n in refs} | {n for r in R.values() for n in r["nodes"]}
    loc = {}
    if need_n:
        for o in osmium.FileProcessor(str(src), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(need_n)):
            if o.location.valid():
                loc[o.id] = (round(o.location.lon, 7), round(o.location.lat, 7))
    line = lambda refs: [list(loc[n]) for n in refs if n in loc]

    def centroid(parts):
        from shapely.geometry import LineString, MultiLineString, Polygon
        pts = [p for p in parts if len(p) >= 2]
        if not pts:
            return None
        if len(pts) == 1 and len(pts[0]) >= 4 and pts[0][0] == pts[0][-1]:
            c = Polygon(pts[0]).centroid
            if not c.is_empty:
                return c.x, c.y
        c = (MultiLineString(pts) if len(pts) > 1 else LineString(pts[0])).centroid
        return c.x, c.y

    for i, w in W.items():
        c = line(w["refs"])
        cen = centroid([c]) if len(c) >= 2 else (tuple(c[0]) if c else None)
        if cen and (bbox is None or any(_in(bbox, *p) for p in c)):
            out.append({"type": "w", "id": i, "tags": w["tags"], "lon": cen[0], "lat": cen[1], "coords": c})
    for i, r in R.items():
        parts = [line(MW.get(w, [])) for w in r["ways"]]
        parts = [p for p in parts if p] + [[list(loc[n])] for n in r["nodes"] if n in loc]
        big = [p for p in parts if len(p) >= 2]
        # closed outer rings -> area centroid of the largest; else centroid of all member lines; else mean of member nodes
        cen = centroid([max(big, key=len)]) if big and all(p[0] == p[-1] for p in big) else centroid(big) if big else \
            ((sum(p[0][0] for p in parts) / len(parts), sum(p[0][1] for p in parts) / len(parts)) if parts else None)
        if cen and (bbox is None or any(_in(bbox, *q) for p in parts for q in p)):
            out.append({"type": "r", "id": i, "tags": r["tags"], "lon": cen[0], "lat": cen[1], "parts": big})
    log(f"query {src.name}: {len(out)} features in {time.time() - t0:.0f}s")
    if cache:
        QCACHE.mkdir(parents=True, exist_ok=True)
        qf.write_text(json.dumps(out), "utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "prefetch":
        for reg in (sys.argv[2:] or DEFAULT_STATES):
            download(reg)
    else:
        print(__doc__)

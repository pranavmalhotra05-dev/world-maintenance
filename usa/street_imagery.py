"""Lane SV - REAL street-level photos (no AI, no synthesis) pinned at their capture locations for every 3D block.

    .venv\\Scripts\\python -u -W ignore -m usa.street_imagery [slug ...]

Per block it writes web/usa/blocks/<slug>/street_imagery.json and small thumbnails under web/usa/blocks/<slug>/street/
(<= 120 KB each, max 60 per block), then appends them to meta.files and sets meta.street_imagery inside
usa.blocklock.block_lock (re-read inside the lock, only our keys, tmp + os.replace).

Sources (licence evidence -> usa/research/street_imagery.md):
  * KartaView  (api.kartaview.org, public, keyless)  - imagery CC BY-SA 4.0, credit "(c) Grab and KartaView Contributors"
  * Mapillary  (graph.mapillary.com, free client token) - User Content CC BY-SA 4.0; token read ONLY from
    usa/local_secrets.json key "mapillary_token" (git-ignored). Absent -> status "no token". The token is never printed
    or written anywhere.

Privacy: images are used exactly as published by the platforms (blurred faces / plates). No detection, cropping or
zooming into people or vehicles. KartaView photos whose platform blur status is not "BLURRED" are kept as metadata only
(thumb = null, not downloaded). Thumbnails are only downscaled (whole frame), never cropped.
"""
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

from usa.blocklock import block_lock

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "web" / "usa" / "blocks"
SECRETS = ROOT / "usa" / "local_secrets.json"
CACHE = ROOT / "usa" / "_cache" / "street_imagery"
CACHE.mkdir(parents=True, exist_ok=True)
UA = "roofprint-usa/0.1 (open-data research)"
S = requests.Session()
S.headers["User-Agent"] = UA

MARGIN_M = 50.0          # block bbox + 50 m
MAX_THUMBS = 60          # per block
MAX_ENTRIES = 400        # metadata entries kept per block
THUMB_MAX_BYTES = 120_000
THUMB_W = 640            # px, whole frame downscaled
KV_API = "https://api.kartaview.org"
MLY_API = "https://graph.mapillary.com"
KV_LICENCE = "CC BY-SA 4.0"
KV_CREDIT = "© Grab and KartaView Contributors"
MLY_LICENCE = "CC BY-SA 4.0"
R_EARTH = 6378137.0


# ------------------------------------------------------------------------------------------------ helpers
def to_local(lon, lat, lon0, lat0):
    """house3d convention: local metres = Web-Mercator metres / k, k = 1/cos(lat0); y = north."""
    c = math.cos(math.radians(lat0))
    x = R_EARTH * math.radians(lon - lon0) * c
    my = lambda la: R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(la) / 2))  # noqa: E731
    y = (my(lat) - my(lat0)) * c
    return round(x, 2), round(y, 2)


def search_box(meta):
    """bbox (W,S,E,N) = block bounds + MARGIN_M."""
    lon0, lat0 = meta["origin"]
    W, S_, E, N = meta.get("bounds") or [None] * 4
    if W is None:
        h = meta.get("half_m", 50.0)
        dlon = h / (111_320.0 * math.cos(math.radians(lat0))); dlat = h / 110_540.0
        W, S_, E, N = lon0 - dlon, lat0 - dlat, lon0 + dlon, lat0 + dlat
    dlon = MARGIN_M / (111_320.0 * math.cos(math.radians(lat0))); dlat = MARGIN_M / 110_540.0
    return W - dlon, S_ - dlat, E + dlon, N + dlat


def get_json(url, method="GET", tries=3, **kw):
    for i in range(tries):
        try:
            r = S.request(method, url, timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 + 3 * i); continue
            return r.status_code, r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 + 3 * i)
    return None, None


def iso(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        s = str(ts)
        if s.startswith("0001"):
            return None
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        return None


def fnum(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def make_thumb(raw, out_path):
    """Downscale the WHOLE frame (no crop) to <= THUMB_W px wide and <= THUMB_MAX_BYTES."""
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w = THUMB_W
    for q in (80, 72, 64, 56, 48):
        for ww in (w, 512, 400, 320):
            t = im.copy()
            if t.width > ww:
                t = t.resize((ww, max(1, round(t.height * ww / t.width))), Image.LANCZOS)
            b = io.BytesIO(); t.save(b, "JPEG", quality=q, optimize=True, progressive=True)
            if b.tell() <= THUMB_MAX_BYTES:
                tmp = out_path.with_suffix(".tmp")
                tmp.write_bytes(b.getvalue()); os.replace(tmp, out_path)
                return t.width, t.height, b.tell()
    return None


def fetch_bytes(url, cache_name):
    cp = CACHE / cache_name
    if cp.exists() and cp.stat().st_size > 0:
        return cp.read_bytes()
    for i in range(3):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                cp.write_bytes(r.content); return r.content
            if r.status_code in (403, 404):
                return None
        except requests.RequestException:
            pass
        time.sleep(2 + 3 * i)
    return None


# ------------------------------------------------------------------------------------------------ KartaView
def kartaview(box, lon0, lat0):
    W, S_, E, N = box
    clat, clon = (S_ + N) / 2, (W + E) / 2
    half_diag = math.hypot((E - W) * 111_320 * math.cos(math.radians(clat)), (N - S_) * 110_540) / 2
    radius = int(min(500, math.ceil(half_diag) + 5))
    rows, page, status = {}, 1, "ok"
    while page <= 30:
        code, js = get_json(f"{KV_API}/2.0/photo/", params={"lat": clat, "lng": clon, "radius": radius,
                                                            "itemsPerPage": 150, "page": page})
        if js is None or code != 200 or (js.get("status") or {}).get("apiCode") != 600:
            status = f"api error {code} {(js or {}).get('status', {}).get('apiMessage', '')}".strip(); break
        data = (js.get("result") or {}).get("data") or []
        for d in data:
            rows[str(d["id"])] = d
        if not (js.get("result") or {}).get("hasMoreData") or not data:
            break
        page += 1
    # v1 nearby-photos: returns many photos the v2 radius search omits (checked: Denver 58 vs 3 in the bbox) and carries
    # the contributor username for attribution. Union both; v1-only ids get their v2 detail record (blur status, CDN url).
    users, v1_ids, seen_pages = {}, [], set()
    p = 1
    while p <= 30:
        code, js = get_json(f"{KV_API}/1.0/list/nearby-photos/", method="POST",
                            data={"lat": clat, "lng": clon, "radius": radius, "page": p, "ipp": 200})
        items = (js or {}).get("currentPageItems") or []
        key = tuple(str(it.get("id")) for it in items[:3])
        if key in seen_pages:
            break
        seen_pages.add(key)
        for it in items:
            pid = str(it["id"])
            if it.get("username"):
                users[pid] = it["username"]
            la, lo = fnum(it.get("lat")), fnum(it.get("lng"))
            if la is not None and lo is not None and W <= lo <= E and S_ <= la <= N:
                v1_ids.append(pid)
        if len(items) < 200:
            break
        p += 1
    for pid in dict.fromkeys(v1_ids):
        if pid in rows or len(rows) >= MAX_ENTRIES:
            continue
        code, js = get_json(f"{KV_API}/2.0/photo/{pid}")
        d = ((js or {}).get("result") or {}).get("data")
        if code == 200 and isinstance(d, dict) and d.get("id"):
            rows[pid] = d
    out = []
    for pid, d in rows.items():
        lat, lon = fnum(d.get("lat")), fnum(d.get("lng"))
        if lat is None or lon is None or not (W <= lon <= E and S_ <= lat <= N):
            continue
        if d.get("visibility") not in (None, "public") or d.get("status") not in (None, "active"):
            continue
        x, y = to_local(lon, lat, lon0, lat0)
        hd = fnum(d.get("heading"))
        blurred = d.get("autoImgProcessingResult") == "BLURRED"
        seq = d.get("sequenceId")
        user = users.get(pid)
        out.append({
            "id": f"kv:{pid}", "source": "kartaview", "lon": round(lon, 7), "lat": round(lat, 7), "x": x, "y": y,
            "heading_deg": round(hd % 360, 1) if hd is not None and hd != 0 else None,
            "captured_at": iso(d.get("shotDate")) or iso(d.get("dateAdded")),
            "captured_at_kind": "shot" if iso(d.get("shotDate")) else "uploaded",
            "thumb": None,
            "url": f"https://kartaview.org/details/{seq}/{d.get('sequenceIndex')}/track-info" if seq else "https://kartaview.org/",
            "licence": KV_LICENCE,
            "attribution": f"{KV_CREDIT}" + (f" - {user}" if user else "") + f", sequence {seq}, photo {pid}",
            "platform_blur": d.get("autoImgProcessingResult"),
            "gps_accuracy_m": fnum(d.get("gpsAccuracy")) or None,
            "_src": d.get("imageLthUrl") or d.get("imageThUrl"), "_blurred": blurred,
        })
    return out, status


# ------------------------------------------------------------------------------------------------ Mapillary
def mapillary_token():
    try:
        tok = json.loads(SECRETS.read_text(encoding="utf-8-sig")).get("mapillary_token") or ""
        return tok.strip() or None
    except (OSError, ValueError):
        return None


def mapillary(box, lon0, lat0, token):
    W, S_, E, N = box
    fields = "id,computed_geometry,geometry,compass_angle,computed_compass_angle,captured_at,thumb_256_url,thumb_1024_url,sequence,creator"
    code, js = get_json(f"{MLY_API}/images", params={"access_token": token, "fields": fields,
                                                      "bbox": f"{W:.7f},{S_:.7f},{E:.7f},{N:.7f}", "limit": 2000})
    if code != 200 or js is None:
        msg = ((js or {}).get("error") or {}).get("message", "")
        return [], f"api error {code}" + (f" ({msg[:80]})" if msg and "token" not in msg.lower() else "")
    out = []
    for d in js.get("data") or []:
        g = (d.get("computed_geometry") or d.get("geometry") or {}).get("coordinates")
        if not g:
            continue
        lon, lat = float(g[0]), float(g[1])
        if not (W <= lon <= E and S_ <= lat <= N):
            continue
        x, y = to_local(lon, lat, lon0, lat0)
        hd = fnum(d.get("computed_compass_angle"))
        if hd is None:
            hd = fnum(d.get("compass_angle"))
        cr = (d.get("creator") or {}).get("username")
        seq = d.get("sequence")
        out.append({
            "id": f"mly:{d['id']}", "source": "mapillary", "lon": round(lon, 7), "lat": round(lat, 7), "x": x, "y": y,
            "heading_deg": round(hd % 360, 1) if hd is not None else None,
            "captured_at": iso(d.get("captured_at")), "captured_at_kind": "shot",
            "thumb": None, "url": f"https://www.mapillary.com/app/?pKey={d['id']}",
            "licence": MLY_LICENCE,
            "attribution": "Mapillary" + (f" - {cr}" if cr else "") + (f", sequence {seq}" if seq else "") + f", image {d['id']}",
            "platform_blur": "Mapillary automatic face/plate blurring",
            "_src": d.get("thumb_1024_url") or d.get("thumb_256_url"), "_blurred": True,
        })
    return out, "ok"


# ------------------------------------------------------------------------------------------------ selection
def pick_for_thumbs(imgs, half):
    """Spread thumbnails over the block: prefer in-block, newest; farthest-point sampling up to MAX_THUMBS."""
    cand = [i for i in imgs if i["_blurred"] and i["_src"]]
    # one per ~4 m cell (newest wins), so a parked car's burst does not eat the budget
    cells = {}
    for i in sorted(cand, key=lambda i: i["captured_at"] or "", reverse=True):
        k = (round(i["x"] / 4), round(i["y"] / 4), round((i["heading_deg"] or 0) / 90) % 4)
        cells.setdefault(k, i)
    cand = list(cells.values())
    inside = lambda i: abs(i["x"]) <= half and abs(i["y"]) <= half  # noqa: E731
    cand.sort(key=lambda i: (not inside(i), -(int((i["captured_at"] or "0000")[:4]) if (i["captured_at"] or "")[:4].isdigit() else 0)))
    if len(cand) <= MAX_THUMBS:
        return cand
    chosen = [cand[0]]
    d2 = [ (c["x"] - chosen[0]["x"]) ** 2 + (c["y"] - chosen[0]["y"]) ** 2 for c in cand]
    while len(chosen) < MAX_THUMBS:
        j = max(range(len(cand)), key=lambda k: d2[k] * (1.0 if inside(cand[k]) else 0.5))
        if d2[j] <= 0:
            break
        chosen.append(cand[j])
        for k, c in enumerate(cand):
            d2[k] = min(d2[k], (c["x"] - cand[j]["x"]) ** 2 + (c["y"] - cand[j]["y"]) ** 2)
    return chosen


# ------------------------------------------------------------------------------------------------ per block
def run_block(slug, token):
    bdir = BLOCKS / slug
    meta = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
    lon0, lat0 = meta["origin"]
    half = float(meta.get("half_m", 50.0))
    box = search_box(meta)
    imgs, status = [], {}
    kv, status["kartaview"] = kartaview(box, lon0, lat0)
    imgs += kv
    if token:
        ml, status["mapillary"] = mapillary(box, lon0, lat0, token)
        imgs += ml
    else:
        status["mapillary"] = "no token"
    # keep nearest-to-block entries if huge
    imgs.sort(key=lambda i: max(abs(i["x"]), abs(i["y"])))
    imgs = imgs[:MAX_ENTRIES]

    sdir = bdir / "street"
    sdir.mkdir(exist_ok=True)
    thumbs = []
    for i in pick_for_thumbs(imgs, half):
        name = i["id"].replace(":", "_") + ".jpg"
        raw = fetch_bytes(i["_src"], name)
        if not raw:
            continue
        try:
            res = make_thumb(raw, sdir / name)
        except Exception:  # noqa: BLE001
            res = None
        if res:
            i["thumb"] = f"street/{name}"; i["thumb_px"] = [res[0], res[1]]
            thumbs.append(i["thumb"])
    # drop stale thumbnails from earlier runs that are no longer referenced
    keep = {Path(t).name for t in thumbs}
    for f in sdir.glob("*.jpg"):
        if f.name not in keep:
            f.unlink(missing_ok=True)
    if not any(sdir.iterdir()):
        sdir.rmdir()

    for i in imgs:
        i.pop("_src", None); i.pop("_blurred", None)
    imgs.sort(key=lambda i: (i["source"], i["captured_at"] or ""), reverse=False)

    counts, newest, oldest = {}, {}, {}
    for i in imgs:
        s = i["source"]
        c = counts.setdefault(s, {"images": 0, "in_block": 0, "with_thumb": 0, "with_heading": 0})
        c["images"] += 1
        c["in_block"] += abs(i["x"]) <= half and abs(i["y"]) <= half
        c["with_thumb"] += bool(i["thumb"])
        c["with_heading"] += i["heading_deg"] is not None
        if i["captured_at"]:
            newest[s] = max(newest.get(s, ""), i["captured_at"]); oldest[s] = min(oldest.get(s, "9999"), i["captured_at"])
    for s in ("kartaview", "mapillary"):
        counts.setdefault(s, {"images": 0, "in_block": 0, "with_thumb": 0, "with_heading": 0})
    n = len(imgs)
    note = (f"{n} real street-level photos within the block bbox + {int(MARGIN_M)} m "
            f"(KartaView {counts['kartaview']['images']}, Mapillary {counts['mapillary']['images']}"
            f"{' - not queried: no token' if status['mapillary'] == 'no token' else ''}). "
            + ("No public street-level photos found here; walk mode should fall back to the 3D scene only. " if n == 0 else "")
            + "Photos are shown as published (platform-blurred), never synthesised; x/y are local metres from meta.origin "
              "(Web-Mercator/k convention, y north); heading_deg is the camera compass bearing (null = not recorded).")
    out = {
        "images": imgs,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "origin": [lon0, lat0], "search_bbox": [round(v, 7) for v in box], "margin_m": MARGIN_M,
            "sources": {
                "kartaview": {"status": status["kartaview"], "api": f"{KV_API}/2.0/photo/ (+ /1.0/list/nearby-photos/ for usernames)",
                              "licence": KV_LICENCE, "credit": KV_CREDIT, "terms": "https://kartaview.org/terms"},
                "mapillary": {"status": status["mapillary"], "api": f"{MLY_API}/images",
                              "licence": MLY_LICENCE, "terms": "https://www.mapillary.com/terms",
                              "attribution_rule": "self-hosted images: visibly display the Mapillary logo and link to the Mapillary image page"},
            },
            "licences": sorted({i["licence"] for i in imgs}) or [KV_LICENCE],
            "share_alike": "CC BY-SA 4.0: thumbnails (downscaled adaptations) are redistributed under CC BY-SA 4.0 with per-image attribution",
            "counts": counts, "newest": newest, "oldest": oldest,
            "coverage_note": note,
            "privacy": "No person-identifying processing; whole frames downscaled only; KartaView frames without platform blur status BLURRED are metadata-only.",
            "artifact_csp_note": "Artifact pages block external URLs, so only local thumbs render inside the artifact; 'url' links open the platform page.",
        },
    }
    tmp = bdir / "street_imagery.json.tmp"
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, bdir / "street_imagery.json")

    with block_lock(bdir):
        m = json.loads((bdir / "meta.json").read_text(encoding="utf-8"))
        files = [f for f in (m.get("files") or []) if not str(f).startswith("street/")]
        for f in ["street_imagery.json"] + sorted(thumbs):
            if f not in files:
                files.append(f)
        m["files"] = files
        m["street_imagery"] = {
            "file": "street_imagery.json", "images": n, "thumbs": len(thumbs),
            "counts": {s: c["images"] for s, c in counts.items()}, "newest": newest,
            "status": {k: v["status"] for k, v in out["meta"]["sources"].items()},
            "licences": out["meta"]["licences"], "generated": out["meta"]["generated"],
        }
        t = bdir / "meta.json.tmp"
        t.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(t, bdir / "meta.json")
    return slug, counts, newest, status, len(thumbs)


def main(argv):
    slugs = [a for a in argv if not a.startswith("-")] or sorted(p.name for p in BLOCKS.iterdir() if (p / "meta.json").exists())
    token = mapillary_token()
    print(f"Mapillary token: {'present' if token else 'absent (skipping Mapillary)'}")
    for slug in slugs:
        try:
            s, counts, newest, status, nt = run_block(slug, token)
            print(f"{s:16s} KV {counts['kartaview']['images']:4d} (in-block {counts['kartaview']['in_block']:3d}) "
                  f"MLY {counts['mapillary']['images']:4d}  thumbs {nt:2d}  newest {newest}  status {status}")
        except Exception as e:  # noqa: BLE001
            print(f"{slug}: FAILED {type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])

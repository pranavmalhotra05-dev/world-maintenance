"""Repackage web/usa into dist/usa so the site fits one claude.ai artifact (<= 512 files).

Nothing is thinned or dropped: every byte of every file is copied, either as a normal file or
inside a pack file. web/usa/pack.js (loaded first by each page) serves packed paths from the packs.

  blocks/<slug>/**                -> blocks/<slug>.pack<N>.bin
  metros/<m>/tiles/<x>_<y>.json   -> metros/<m>/packs/<x//G>_<y//G>[.<N>].bin   (G x G tile groups)
  data/**                         -> data.pack<N>.bin
  everything else                 -> copied as is
  pack-manifest.json              -> {"site/relative/path": [pack, offset, length, contentType, encoding, rawLength], ...}
                                     encoding "gzip" = stored gzip-compressed (text/JSON; pack.js inflates it), "" = raw;
                                     a file larger than one pack: [null, 0, length, type, enc, rawLength, [[pack, off, len], ...]]

Skipped: blocks/slc-ut-test, *.lock / *.tmp / *.log / *.part, dot-files, __pycache__.
Re-runnable: dist/usa is deleted and rebuilt each run, so new blocks are picked up.

Usage:  .venv\\Scripts\\python usa\\pack_site.py [--src web/usa] [--dst dist/usa] [--group 5]
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import mimetypes
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_MAX = 11_800_000               # raw bytes per pack; written base64 (x4/3 -> <=15.8 MB, artifact limit 16 MB)
PACK_EXT = ".b64.txt"                # artifacts serve no binary type, so packs are base64 text
MAX_FILES = 350                      # target dist file count (artifact version limit is 512)
MAX_TOTAL = 256 * 1024 * 1024        # artifact version size limit
NO_GZIP = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SKIP_DIRS = {"blocks/slc-ut-test"}
SKIP_EXT = {".lock", ".tmp", ".log", ".part"}
SKIP_SEGMENTS = ("/renders/",)       # Blender beauty renders: no page displays them (keeps the 256 MiB version cap)
TYPES = {".json": "application/json", ".geojson": "application/geo+json", ".txt": "text/plain; charset=utf-8",
         ".bin": "application/octet-stream", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml", ".csv": "text/csv; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}
TILE_RE = re.compile(r"^(-?\d+)_(-?\d+)\.json$")


def ctype(p: str) -> str:
    ext = Path(p).suffix.lower()
    return TYPES.get(ext) or mimetypes.guess_type(p)[0] or "application/octet-stream"


def skipped(rel: str) -> bool:
    parts = rel.split("/")
    if any(x.startswith(".") or x == "__pycache__" for x in parts):
        return True
    if Path(rel).suffix.lower() in SKIP_EXT:
        return True
    if any(seg in rel for seg in SKIP_SEGMENTS):
        return True
    return any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS)


def group_of(rel: str, g: int) -> str | None:
    """Pack base name (without .bin) for a file, or None to copy it as a normal file."""
    p = rel.split("/")
    if p[0] == "blocks" and len(p) >= 3:
        return f"blocks/{p[1]}.pack"
    if p[0] == "metros" and len(p) == 4 and p[2] == "tiles":
        m = TILE_RE.match(p[3])
        key = f"{int(m.group(1)) // g}_{int(m.group(2)) // g}" if m else "misc"
        return f"metros/{p[1]}/packs/{key}"
    if p[0] == "data" and len(p) >= 2:
        return "data.pack"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ROOT / "web" / "usa"))
    ap.add_argument("--dst", default=str(ROOT / "dist" / "usa"))
    ap.add_argument("--group", type=int, default=5, help="metro tiles per pack side (G x G tiles)")
    a = ap.parse_args()
    src, dst = Path(a.src).resolve(), Path(a.dst).resolve()
    if dst.parent.name != "dist" or dst == src:
        sys.exit(f"refusing to rebuild {dst}: destination must sit inside a 'dist' folder")
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    normal: list[str] = []
    groups: dict[str, list[str]] = {}
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src).as_posix()
        if skipped(rel) or rel == "pack-manifest.json":
            continue
        g = group_of(rel, a.group)
        (groups.setdefault(g, []) if g else normal).append(rel)

    # normal files (pack.js gets switched on)
    for rel in normal:
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if rel == "pack.js":
            js = (src / rel).read_text(encoding="utf-8")
            on = js.replace("var ENABLED = false; /*PACK_ENABLED*/", "var ENABLED = true; /*PACK_ENABLED*/", 1)
            if on == js:
                sys.exit("pack.js: ENABLED flag line not found")
            out.write_text(on, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src / rel, out)
    if "pack.js" not in normal:
        sys.exit("web/usa/pack.js missing")

    # packs
    manifest: dict[str, list] = {}
    pack_files: list[str] = []
    for base, files in sorted(groups.items()):
        metro = base.startswith("metros/")
        chunks: list[list[tuple[str, bytes, str, int]]] = [[]]
        size = 0
        for rel in files:
            raw = (src / rel).read_bytes()
            enc, data = "", raw
            if Path(rel).suffix.lower() not in NO_GZIP and len(raw) > 512:
                z = gzip.compress(raw, compresslevel=9, mtime=0)
                if len(z) < 0.9 * len(raw):
                    enc, data = "gzip", z
                # base64 point files: store the decoded bytes (gzip), pack.js re-encodes them exactly
                if re.search(r"/points_\d+\.txt$", rel):
                    try:
                        b = base64.b64decode(raw, validate=True)
                        if base64.b64encode(b) == raw:
                            z2 = gzip.compress(b, compresslevel=9, mtime=0)
                            if len(z2) < len(data):
                                enc, data = "b64gz", z2
                    except Exception:
                        pass
            if size and size + len(data) > PACK_MAX:
                chunks.append([]); size = 0
            chunks[-1].append((rel, data, enc, len(raw))); size += len(data)
        names = []
        for i in range(len(chunks)):
            names.append(f"{base}{PACK_EXT}" if metro and len(chunks) == 1 else
                         f"{base}.{i}{PACK_EXT}" if metro else f"{base}{i}{PACK_EXT}")
        # write; a single file bigger than PACK_MAX spills into extra "<name>.x<k>.bin" parts
        for name, chunk in zip(names, chunks):
            buf = bytearray()
            extra: list[str] = []
            for rel, data, enc, rawlen in chunk:
                if len(data) <= PACK_MAX:
                    manifest[rel] = [name, len(buf), len(data), ctype(rel), enc, rawlen]
                    buf += data
                    continue
                segs = []
                for o in range(0, len(data), PACK_MAX):
                    part = data[o:o + PACK_MAX]
                    pn = f"{name[:-len(PACK_EXT)]}.x{len(extra)}{PACK_EXT}"
                    (dst / pn).parent.mkdir(parents=True, exist_ok=True)
                    (dst / pn).write_bytes(base64.b64encode(part))
                    extra.append(pn)
                    segs.append([pn, 0, len(part)])
                manifest[rel] = [None, 0, len(data), ctype(rel), enc, rawlen, segs]
            if buf:
                (dst / name).parent.mkdir(parents=True, exist_ok=True)
                (dst / name).write_bytes(base64.b64encode(bytes(buf)))
                pack_files.append(name)
            pack_files += extra

    (dst / "pack-manifest.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")

    allf = [p for p in dst.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in allf)
    big = max(allf, key=lambda p: p.stat().st_size)
    src_bytes = sum((src / r).stat().st_size for r in normal) + sum(e[5] for e in manifest.values())
    print(f"dist: {dst}")
    print(f"files: {len(allf)}  ({len(normal)} normal + {len(pack_files)} packs + manifest)  packed paths: {len(manifest)}")
    print(f"total: {total / 1e6:.1f} MB   largest: {big.relative_to(dst).as_posix()} {big.stat().st_size / 1e6:.2f} MB")
    print(f"source bytes covered: {src_bytes / 1e6:.1f} MB")
    if big.stat().st_size > 16 * 1024 * 1024:
        print("ERROR: a file exceeds 16 MB"); return 1
    if total > MAX_TOTAL:
        print(f"WARNING: total {total / 2**20:.1f} MiB exceeds the {MAX_TOTAL // 2**20} MiB artifact version limit")
    if len(allf) > MAX_FILES:
        print(f"WARNING: {len(allf)} files > target {MAX_FILES}; raise --group or pack more folders")
    return 0


if __name__ == "__main__":
    sys.exit(main())

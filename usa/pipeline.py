"""One command: a complete 3D block for any US address, with every data layer the project has.

    .venv\\Scripts\\python -u -W ignore -m usa.pipeline --address "1385 E Yale Ave, Salt Lake City, UT" --half-m 40 --slug slc-ut-test
    .venv\\Scripts\\python -u -W ignore -m usa.pipeline --lon -111.856 --lat 40.741 --half-m 60 --slug slc-ut-pt
    .venv\\Scripts\\python -u -W ignore -m usa.pipeline --batch blocks.csv [--jobs 2]
    options: --skip stage,...  --only stage,...  --renders  --list  --no-retry  --events

Batch CSV (header optional): `address,slug[,half_m]` or `lon,lat,slug[,half_m]`; '#' lines are comments.

Stages run in this order (each is a subprocess `python -u -W ignore -m usa.<script> ...`, cwd = repo root):

    build_block         --address/--lon --lat --half-m --slug              (everything else needs its meta.json)
    enrich_national     <slug> --hail
    city_enrich         <slug>              only when usa.adapters.for_point(origin) names a city adapter
    oz_enrich           <slug>
    federal_flags       <slug>
    local_flags         <slug>
    assistance_flags    <slug>
    thermal             <slug>
    imagery             <slug>
    pools               --slug <slug>
    weather_triggers    --blocks-only <slug>
    cleaning_market     --blocks-only --slugs <slug>
    cleaning_leads      --blocks <slug>
    streets             <slug>
    street_furniture    <slug>
    sky_weather         <slug>
    street_imagery      <slug>
    road_traffic        blocks <slug>
    str_score           --blocks <slug>     (index_blocks first, so city legal rules see the new block); in batch
                                            mode it runs once at the end with --blocks <every slug of the batch>
    render_export       <slug>              --renders only
    render_blender      blender -b --factory-startup -P usa/render_blender.py -- <slug>   (--renders, Blender present)
    render_register     render_export --register <slug>                                   (--renders)
    index_blocks        rebuild web/usa/blocks/index.json; then meta.files is refreshed from the folder

Per stage: a timeout (TIMEOUT_S), one retry on failure (--no-retry turns it off), full output appended to
usa/_cache/pipeline/<slug>.log, and meta.pipeline[stage] = {status: ok|failed|skipped, seconds, ...} written under
usa.blocklock.block_lock (the same lock every stage script takes, so it never races them). A failed stage never stops
the later ones; only a failed build_block (no meta.json) skips the rest, because every stage reads that block.
"failed" = non-zero exit, timeout, or a block-level failure line in the output (the scripts catch their own
exceptions and exit 0, so their "<slug>: FAILED" / Traceback output is checked too).

--events prints one machine-readable line per stage change on stdout, used by usa/serve.py:
    @@pipeline {"slug": ..., "stage": ..., "status": "running|ok|failed|skipped", "seconds": ..., "i": k, "n": N}
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usa.blocklock import block_lock  # noqa: E402

BLOCKS = ROOT / "web" / "usa" / "blocks"
LOGDIR = ROOT / "usa" / "_cache" / "pipeline"
_venv_py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PYTHON = str(_venv_py if _venv_py.exists() else Path(sys.executable))
BLENDER = Path(os.environ.get("ROOF_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"))

STAGES = ["build_block", "enrich_national", "city_enrich", "oz_enrich", "federal_flags", "local_flags",
          "assistance_flags", "thermal", "imagery", "pools", "weather_triggers", "cleaning_market", "cleaning_leads",
          "streets", "street_furniture", "sky_weather", "street_imagery", "road_traffic", "str_score",
          "render_export", "render_blender", "render_register", "index_blocks"]
RENDER_STAGES = {"render_export", "render_blender", "render_register"}
MIN = 60
TIMEOUT_S = {"build_block": 45 * MIN, "enrich_national": 90 * MIN, "imagery": 40 * MIN, "pools": 40 * MIN,
             "weather_triggers": 40 * MIN, "cleaning_market": 40 * MIN, "thermal": 40 * MIN, "streets": 30 * MIN,
             "street_furniture": 30 * MIN, "road_traffic": 30 * MIN, "str_score": 30 * MIN,
             "render_blender": 120 * MIN}
DEFAULT_TIMEOUT_S = 20 * MIN

_print_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return s[:60].rstrip("-") or "block"


def py(module, *args):
    return [PYTHON, "-u", "-W", "ignore", "-m", f"usa.{module}", *map(str, args)]


# ------------------------------------------------------------------------------------------------ block meta helpers
def read_meta(slug):
    p = BLOCKS / slug / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(d, meta):
    mp = d / "meta.json"
    pretty = mp.read_text(encoding="utf-8")[:2] == "{\n" if mp.exists() else True
    tmp = mp.with_suffix(".json.pipeline.tmp")
    tmp.write_text(json.dumps(meta, indent=1) if pretty else json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, mp)


def record(slug, stage, entry, run_info=None, refresh_files=False):
    """meta.pipeline[stage] = entry (re-read inside the shared block lock)."""
    d = BLOCKS / slug
    if not (d / "meta.json").exists():
        return
    try:
        with block_lock(d):
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            pl = meta.get("pipeline") if isinstance(meta.get("pipeline"), dict) else {}
            stages = pl.get("stages") if isinstance(pl.get("stages"), dict) else {}
            if stage:
                stages[stage] = entry
            pl["stages"] = stages
            if run_info:
                pl.update(run_info)
            meta["pipeline"] = pl
            if refresh_files:   # same rule as serve.write_manifest: every real file, so the viewer never 404s
                files = set(meta.get("files") or [])
                files |= {p.relative_to(d).as_posix() for p in d.rglob("*")
                          if p.is_file() and not p.name.endswith(".tmp") and p.name != ".lock"}
                meta["files"] = sorted(f for f in files if (d / f).exists())
            _write_meta(d, meta)
    except Exception as e:  # noqa: BLE001  - bookkeeping must never kill the run
        say(slug, f"warning: could not record meta.pipeline[{stage}]: {e}")


# ------------------------------------------------------------------------------------------------ output
class Run:
    def __init__(self, slug, quiet, events):
        self.slug, self.quiet, self.events = slug, quiet, events
        LOGDIR.mkdir(parents=True, exist_ok=True)
        self.log = open(LOGDIR / f"{slug}.log", "a", encoding="utf-8", errors="replace")
        self.lock = threading.Lock()

    def write(self, line, echo=True):
        line = line.rstrip("\r\n")
        with self.lock:
            self.log.write(line + "\n"); self.log.flush()
        if echo and not self.quiet:
            with _print_lock:
                print(line, flush=True)

    def event(self, **kw):
        kw = {"slug": self.slug, **kw}
        self.write(f"[pipeline] {kw.get('stage')} {kw.get('status')}" +
                   (f" {kw['seconds']}s" if kw.get("seconds") is not None else "") +
                   (f" ({kw['note']})" if kw.get("note") else ""), echo=not self.events)
        with _print_lock:
            if self.events:
                print("@@pipeline " + json.dumps(kw), flush=True)
            elif self.quiet:   # batch with --jobs > 1: only stage lines on stdout
                print(f"[{self.slug}] {kw.get('stage')} {kw.get('status')}" +
                      (f" {kw['seconds']}s" if kw.get("seconds") is not None else ""), flush=True)


def say(slug, msg):
    with _print_lock:
        print(f"[{slug}] {msg}", flush=True)


def kill_tree(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            p.kill()
    except Exception:  # noqa: BLE001
        p.kill()


def run_cmd(run, cmd, timeout):
    """Run one subprocess, tee its output to the log; returns (rc, lines, timed_out)."""
    shown = " ".join(os.path.relpath(a, ROOT) if os.path.isabs(a) and a.startswith(str(ROOT)) else
                     (f'"{a}"' if " " in a else a) for a in cmd)
    run.write("$ " + shown)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        run.write(f"could not start: {e}")
        return -1, [str(e)], False
    timed_out = threading.Event()

    def _kill():
        timed_out.set(); kill_tree(p)
    t = threading.Timer(timeout, _kill); t.daemon = True; t.start()
    lines = []
    try:
        for line in p.stdout:  # type: ignore[union-attr]
            lines.append(line.rstrip("\r\n"))
            if len(lines) > 4000:
                del lines[:1000]
            run.write(line)
        rc = p.wait()
    finally:
        t.cancel()
    if timed_out.is_set():
        run.write(f"TIMEOUT after {timeout}s - process tree killed")
    return rc, lines, timed_out.is_set()


def block_failure(slug, lines):
    """The stage scripts catch per-block exceptions and still exit 0; detect that from their output."""
    pat = re.compile(rf"(^Traceback \(most recent call last\))|(\b{re.escape(slug)}\b\s*:?\s+FAILED\b)|(FAIL _block)")
    for k, ln in enumerate(lines):
        if pat.search(ln):
            if ln.startswith("Traceback"):   # report the exception line that ends the traceback, not its header
                for nxt in lines[k + 1:]:
                    if nxt and not nxt.startswith((" ", "\t", "~", "^")):
                        return ("Traceback: " + nxt.strip())[:200]
            return ln.strip()[:200]
    return None


# ------------------------------------------------------------------------------------------------ stage plan
def city_adapter(slug):
    meta = read_meta(slug) or {}
    o = meta.get("origin")
    if not (isinstance(o, list) and len(o) >= 2):
        return None
    try:
        from usa.adapters import for_point
        ad = for_point(float(o[0]), float(o[1]))
    except Exception:  # noqa: BLE001
        return None
    return getattr(ad, "__name__", str(ad)).split(".")[-1] if ad else None


def stage_cmds(stage, job, batch_mode):
    """List of commands for a stage, or a str = reason to skip it."""
    s = job["slug"]
    if stage == "build_block":
        loc = ["--address", job["address"]] if job.get("address") else ["--lon", job["lon"], "--lat", job["lat"]]
        return [py("build_block", *loc, "--half-m", job["half_m"], "--slug", s)]
    simple = {"enrich_national": [s, "--hail"], "oz_enrich": [s], "federal_flags": [s], "local_flags": [s],
              "assistance_flags": [s], "thermal": [s], "imagery": [s], "pools": ["--slug", s],
              "weather_triggers": ["--blocks-only", s], "cleaning_market": ["--blocks-only", "--slugs", s],
              "cleaning_leads": ["--blocks", s], "streets": [s], "street_furniture": [s], "sky_weather": [s],
              "street_imagery": [s], "road_traffic": ["blocks", s], "render_export": [s]}
    if stage in simple:
        return [py(stage, *simple[stage])]
    if stage == "city_enrich":
        ad = city_adapter(s)
        return [py("city_enrich", s)] if ad else "no city adapter covers this point (usa.adapters.for_point)"
    if stage == "str_score":
        if batch_mode:
            return "deferred: runs once at the end of the batch"
        return [py("index_blocks"), py("str_score", "--blocks", s)]
    if stage == "render_blender":
        if not BLENDER.exists():
            return f"Blender not found at {BLENDER}"
        return [[str(BLENDER), "-b", "--factory-startup", "-P", str(ROOT / "usa" / "render_blender.py"), "--", s]]
    if stage == "render_register":
        return [py("render_export", "--register", s)]
    if stage == "index_blocks":
        return [py("index_blocks")]
    raise KeyError(stage)


def selected(stages_arg, skip, only, renders):
    out = []
    for st in STAGES:
        if st in RENDER_STAGES and not renders and st not in only:
            continue
        out.append(st)
    return out


def run_stage(run, job, stage, cmds, retry):
    t0 = time.time()
    attempts, why = 0, None
    for attempt in range(2 if retry else 1):
        attempts = attempt + 1
        why = None
        for cmd in cmds:
            rc, lines, to = run_cmd(run, cmd, TIMEOUT_S.get(stage, DEFAULT_TIMEOUT_S))
            if to:
                why = f"timeout {TIMEOUT_S.get(stage, DEFAULT_TIMEOUT_S)}s"
            elif rc != 0:
                why = f"exit {rc}" + (f": {lines[-1][:160]}" if lines else "")
            else:
                why = block_failure(job["slug"], lines)
            if why:
                break
        if not why:
            break
        if attempts == 1 and retry:
            run.write(f"[pipeline] {stage} failed ({why}); retrying once")
    entry = {"status": "failed" if why else "ok", "seconds": round(time.time() - t0, 1), "attempts": attempts,
             "finished": now_iso()}
    if why:
        entry["error"] = why
    return entry


def run_block(job, opts, batch_mode=False, quiet=False):
    slug = job["slug"]
    run = Run(slug, quiet, opts.events)
    plan = [st for st in selected(None, opts.skip, opts.only, opts.renders)]
    n = len(plan)
    run.write(f"\n===== pipeline {slug} @ {now_iso()} : {job.get('address') or (job.get('lon'), job.get('lat'))} "
              f"half_m={job['half_m']} stages={','.join(plan)}")
    t_all = time.time()
    results = {}
    have_block = (BLOCKS / slug / "meta.json").exists()
    for i, stage in enumerate(plan, 1):
        reason = None
        if stage in opts.skip or (opts.only and stage not in opts.only):
            reason = "--skip" if stage in opts.skip else "not in --only"
        elif stage != "build_block" and not (BLOCKS / slug / "meta.json").exists():
            reason = "no block (build_block did not produce meta.json)"
        elif stage == "render_blender" and results.get("render_export", {}).get("status") == "failed":
            reason = "render_export failed"
        elif stage == "render_register" and results.get("render_blender", {}).get("status") != "ok":
            reason = "no Blender renders this run"
        cmds = None
        if not reason:
            try:
                cmds = stage_cmds(stage, job, batch_mode)
            except Exception as e:  # noqa: BLE001
                reason = f"could not plan stage: {e}"
            if isinstance(cmds, str):
                reason, cmds = cmds, None
        if reason:
            entry = {"status": "skipped", "seconds": 0, "reason": reason, "finished": now_iso()}
            results[stage] = entry
            run.event(stage=stage, status="skipped", seconds=0, i=i, n=n, note=reason)
            if reason not in ("--skip", "not in --only"):   # user-chosen skips keep the stage's earlier record
                record(slug, stage, entry)
            continue
        if stage == "build_block" and have_block:
            run.write(f"[pipeline] note: {slug} already exists; build_block rebuilds it in place")
        run.event(stage=stage, status="running", i=i, n=n)
        entry = run_stage(run, job, stage, cmds, opts.retry)
        if stage == "build_block" and entry["status"] == "ok" and not (BLOCKS / slug / "meta.json").exists():
            entry.update(status="failed", error="build_block exited 0 but wrote no meta.json")
        results[stage] = entry
        run.event(stage=stage, status=entry["status"], seconds=entry["seconds"], i=i, n=n, note=entry.get("error"))
        info = None
        if stage == "build_block":
            info = {"started": now_iso(), "address": job.get("address"), "half_m": job["half_m"]}
        record(slug, stage, entry, run_info=info, refresh_files=(stage == "index_blocks"))
    total = round(time.time() - t_all, 1)
    summary = {"ok": sum(1 for v in results.values() if v["status"] == "ok"),
               "failed": [k for k, v in results.items() if v["status"] == "failed"],
               "skipped": [k for k, v in results.items() if v["status"] == "skipped"]}
    record(slug, None, None, run_info={"finished": now_iso(), "seconds": total, "summary": summary},
           refresh_files=True)
    run.write(f"===== {slug} done in {total}s: {summary['ok']} ok, failed {summary['failed'] or '-'}")
    run.write("stage timings:")
    for k, v in results.items():
        run.write(f"  {k:18s} {v['status']:8s} {v['seconds']:8.1f}s" +
                  (f"  ({v.get('error') or v.get('reason')})" if v["status"] != "ok" else ""))
    run.log.close()
    return {"slug": slug, "seconds": total, "stages": results, "summary": summary}


# ------------------------------------------------------------------------------------------------ batch
def parse_batch(path, half_default):
    jobs = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            row = [c.strip() for c in row]
            if not row or not row[0] or row[0].startswith("#"):
                continue
            if row[0].lower() in ("address", "lon"):
                continue   # header
            try:
                lon, lat = float(row[0]), float(row[1])
                slug = row[2] if len(row) > 2 and row[2] else f"pt-{lat:.5f}-{lon:.5f}".replace(".", "_")
                half = float(row[3]) if len(row) > 3 and row[3] else half_default
                jobs.append({"lon": lon, "lat": lat, "slug": slugify(slug), "half_m": half})
            except (ValueError, IndexError):
                # address row; tolerate an unquoted address with commas: trailing [slug[,half_m]] are peeled off
                cols, half, slug = list(row), half_default, None
                if len(cols) >= 3 and re.fullmatch(r"\d+(\.\d+)?", cols[-1]) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", cols[-2]):
                    half, slug, cols = float(cols[-1]), cols[-2], cols[:-2]
                elif len(cols) >= 2 and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", cols[-1]) and not cols[-1].isdigit():
                    slug, cols = cols[-1], cols[:-1]
                addr = ", ".join(c for c in cols if c)
                jobs.append({"address": addr, "slug": slugify(slug or addr), "half_m": half})
    return jobs


def finish_batch(slugs, opts):
    """Batch tail: the deferred str_score over every batch block, then one index rebuild."""
    if not slugs:
        return {}
    tail = {}
    if "str_score" not in opts.skip and (not opts.only or "str_score" in opts.only):
        run = Run("_batch", False, opts.events)
        live = [s for s in slugs if (BLOCKS / s / "meta.json").exists()]
        run.event(stage="str_score", status="running", note=",".join(live))
        cmds = [py("index_blocks"), py("str_score", "--blocks", ",".join(live))]
        e = run_stage(run, {"slug": "_batch"}, "str_score", cmds, opts.retry)
        run.event(stage="str_score", status=e["status"], seconds=e["seconds"], note=e.get("error"))
        for s in live:
            record(s, "str_score", {**e, "batch": True}, refresh_files=True)
        tail["str_score"] = e
        run.log.close()
    return tail


# ------------------------------------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--half-m", type=float, default=50)
    ap.add_argument("--slug")
    ap.add_argument("--batch", help="CSV: address,slug[,half_m] or lon,lat,slug[,half_m] per line")
    ap.add_argument("--jobs", type=int, default=1, help="batch: blocks built in parallel (1 or 2)")
    ap.add_argument("--skip", default="", help="comma list of stages to skip")
    ap.add_argument("--only", default="", help="comma list: run only these stages (on an existing block)")
    ap.add_argument("--renders", action="store_true", help="also run render_export + Blender + register")
    ap.add_argument("--no-retry", dest="retry", action="store_false")
    ap.add_argument("--events", action="store_true", help="print @@pipeline JSON stage events (for usa/serve.py)")
    ap.add_argument("--list", action="store_true", help="print the stage list and exit")
    a = ap.parse_args(argv)
    a.skip = {x.strip() for x in a.skip.split(",") if x.strip()}
    a.only = {x.strip() for x in a.only.split(",") if x.strip()}
    bad = (a.skip | a.only) - set(STAGES)
    if bad:
        ap.error(f"unknown stage(s) {sorted(bad)}; stages: {','.join(STAGES)}")
    if a.list:
        print("\n".join(STAGES)); return 0
    if a.batch:
        jobs = parse_batch(a.batch, a.half_m)
        if not jobs:
            ap.error("batch file has no rows")
        n = max(1, min(2, a.jobs))
        print(f"batch: {len(jobs)} blocks, {n} at a time", flush=True)
        if n == 1:
            res = [run_block(j, a, batch_mode=True) for j in jobs]
        else:
            with ThreadPoolExecutor(n) as ex:
                res = list(ex.map(lambda j: run_block(j, a, batch_mode=True, quiet=not a.events), jobs))
        tail = finish_batch([j["slug"] for j in jobs], a)
        print("\nbatch summary:")
        for r in res:
            print(f"  {r['slug']:28s} {r['seconds']:8.1f}s  ok {r['summary']['ok']}  failed {r['summary']['failed'] or '-'}")
        if tail.get("str_score"):
            print(f"  str_score (batch tail): {tail['str_score']['status']} {tail['str_score']['seconds']}s")
        return 0 if all(not r["summary"]["failed"] for r in res) else 1
    if not a.address and (a.lon is None or a.lat is None):
        if a.slug and (BLOCKS / a.slug / "meta.json").exists() and (a.only or "build_block" in a.skip):
            pass   # re-run stages on an existing block
        else:
            ap.error("give --address, or --lon and --lat, or --batch (or --slug of an existing block with --only/--skip build_block)")
    slug = slugify(a.slug) if a.slug else slugify(a.address or f"pt-{a.lat:.5f}-{a.lon:.5f}".replace(".", "_"))
    job = {"slug": slug, "half_m": a.half_m}
    if a.address:
        job["address"] = a.address
    elif a.lon is not None:
        job.update(lon=a.lon, lat=a.lat)
    r = run_block(job, a)
    return 0 if r["stages"].get("build_block", {}).get("status") in ("ok", "skipped") and not r["summary"]["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())

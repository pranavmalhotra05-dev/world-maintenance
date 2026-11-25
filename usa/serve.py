"""Local server for Roofprint 3D · USA.

Serves web/ on http://127.0.0.1:8780/ and a small build API:

  GET|POST /api/build?address=...&half_m=60   queue a block build, returns {"job": id, ...}
           optional: renders=1 (Blender stills), skip=stage,stage
  GET      /api/status?job=<id>&since=<n>     poll progress: state, log lines from n, stages {name: status/seconds}, slug
  GET      /api/events?job=<id>&since=<n>     the same progress as a text/event-stream (SSE): "line" events with log
                                               lines, "stage" events on every pipeline stage change, "end" at the end
  GET      /api/ping                           {"ok": true, "builder": bool, "pipeline": [stages]}
  GET      /api/jobs                           recent jobs (no log lines)

A build runs, one at a time (FIFO queue), as the full data pipeline (usa/pipeline.py, every data layer):
  .venv/Scripts/python -u -W ignore -m usa.pipeline --address <addr> --half-m <m> --slug <slug> --events [--renders]
Its "@@pipeline {json}" lines become job["stages"] plus readable "[i/n] stage: status" log lines. The job is "done"
when build_block succeeded; later stage failures are listed in job["failed_stages"] (the block is still usable).
If the pipeline's index_blocks stage did not list the block, {slug,name,city,state,fips,lon,lat} is upserted into
web/usa/blocks/index.json here as before.

Only binds to 127.0.0.1. The address is passed as one argv element (never through a shell).
Run:  .venv\\Scripts\\python usa/serve.py   then open http://127.0.0.1:8780/usa/build.html
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST, PORT = "127.0.0.1", int(os.environ.get("ROOF_PORT", "8780"))
ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
BLOCKS = WEB / "usa" / "blocks"
INDEX = BLOCKS / "index.json"
BUILDER = ROOT / "usa" / "build_block.py"
ENRICH = ROOT / "usa" / "enrich_national.py"
PIPELINE = ROOT / "usa" / "pipeline.py"
_venv_py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PYTHON = str(_venv_py if _venv_py.exists() else Path(sys.executable))
HALF_SIZES = (40, 60, 100)
BUILD_TIMEOUT_S = 6 * 60 * 60   # whole pipeline; each stage also has its own timeout in usa/pipeline.py
MAX_LINES = 3000

ADDR_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.#'&()/\-]{2,159}$")

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
index_lock = threading.Lock()
work: "queue.Queue[str]" = queue.Queue()


def clean_address(raw: str) -> str | None:
    """Collapse whitespace, drop control chars; must start alphanumeric (so it can never look like a CLI flag)."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", raw or "")
    s = re.sub(r"\s+", " ", s).strip().lstrip("-").strip()
    return s if ADDR_OK.match(s) else None


def slugify(addr: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", addr.lower()).strip("-")
    return (s[:60].rstrip("-") or "block")


def log(job: dict, line: str) -> None:
    line = line.rstrip("\r\n")
    with jobs_lock:
        job["lines"].append(line)
        if len(job["lines"]) > MAX_LINES:
            drop = len(job["lines"]) - MAX_LINES
            del job["lines"][:drop]
            job["dropped"] += drop


def run_step(job: dict, args: list[str]) -> int:
    shown = ["python"] + [os.path.relpath(a, ROOT) if os.path.isabs(a) else a for a in args[1:]]
    log(job, "$ " + " ".join(shown))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=env, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        log(job, f"could not start: {e}")
        return -1
    with jobs_lock:
        job["pid"] = p.pid
    killer = threading.Timer(BUILD_TIMEOUT_S, p.kill)
    killer.start()
    try:
        for line in p.stdout:  # type: ignore[union-attr]
            if line.startswith("@@pipeline "):
                stage_event(job, line[len("@@pipeline "):])
            else:
                log(job, line)
        return p.wait()
    finally:
        killer.cancel()
        with jobs_lock:
            job["pid"] = None


def stage_event(job: dict, raw: str) -> None:
    """One usa/pipeline.py stage event -> job["stages"][stage] + a readable log line."""
    try:
        ev = json.loads(raw)
    except ValueError:
        return log(job, "@@pipeline " + raw)
    st = ev.get("stage")
    with jobs_lock:
        job["stages"][st] = {k: ev.get(k) for k in ("status", "seconds", "note", "i", "n")}
        job["stage_seq"] += 1
        if ev.get("status") == "running":
            job["stage"] = st
    secs = f" {ev['seconds']}s" if ev.get("seconds") else ""
    note = f" ({ev['note']})" if ev.get("note") else ""
    log(job, f"[{ev.get('i', '?')}/{ev.get('n', '?')}] {st}: {ev.get('status')}{secs}{note}")


def write_manifest(slug: str) -> None:
    """Add meta.files = every file in the block folder, so view.html never requests absent optional files (no 404 noise)."""
    d = BLOCKS / slug
    mp = d / "meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta["files"] = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
    tmp = mp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(tmp, mp)


def county_fips(meta: dict) -> str | None:
    """5-digit county FIPS: meta.fips, else state_fips + 3-digit county_fips."""
    if meta.get("fips"):
        return str(meta["fips"])
    c, s = str(meta.get("county_fips") or ""), str(meta.get("state_fips") or "")
    if len(c) == 5:
        return c
    if len(c) == 3 and len(s) == 2:
        return s + c
    return c or None


def upsert_index(slug: str, fallback_name: str) -> dict:
    meta = json.loads((BLOCKS / slug / "meta.json").read_text(encoding="utf-8"))
    origin = meta.get("origin") if isinstance(meta.get("origin"), list) else None
    entry = {
        "slug": slug,
        "name": meta.get("name") or meta.get("address") or fallback_name,
        "city": meta.get("city"),
        "state": meta.get("state"),
        "fips": county_fips(meta),
        "lon": meta.get("lon", origin[0] if origin else None),
        "lat": meta.get("lat", origin[1] if origin else None),
    }
    with index_lock:
        try:
            cur = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
            if not isinstance(cur, list):
                cur = []
        except (OSError, ValueError):
            cur = []
        cur = [b for b in cur if isinstance(b, dict) and b.get("slug") != slug] + [entry]
        tmp = INDEX.with_suffix(".json.tmp")
        BLOCKS.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, INDEX)
    return entry


def worker() -> None:
    while True:
        jid = work.get()
        job = jobs[jid]
        with jobs_lock:
            job["state"], job["started"] = "running", time.time()
        try:
            if not PIPELINE.exists():
                raise RuntimeError("usa/pipeline.py does not exist - the block pipeline has not been installed")
            cmd = [PYTHON, "-u", "-W", "ignore", "-m", "usa.pipeline", "--address", job["address"],
                   "--half-m", str(job["half_m"]), "--slug", job["slug"], "--events"]
            if job.get("renders"):
                cmd.append("--renders")
            if job.get("skip"):
                cmd += ["--skip", job["skip"]]
            rc = run_step(job, cmd)
            bb = job["stages"].get("build_block", {}).get("status")
            if bb != "ok" or not (BLOCKS / job["slug"] / "meta.json").exists():
                raise RuntimeError(f"build_block {bb or 'did not run'} (pipeline exit code {rc}); "
                                   f"see usa/_cache/pipeline/{job['slug']}.log")
            failed = [k for k, v in job["stages"].items() if v.get("status") == "failed"]
            with jobs_lock:
                job["failed_stages"] = failed
            if failed:
                log(job, f"warning: {len(failed)} stage(s) failed: {', '.join(failed)}; the 3D block is still usable")
            entry = None
            try:
                entry = next((b for b in json.loads(INDEX.read_text(encoding="utf-8"))
                              if isinstance(b, dict) and b.get("slug") == job["slug"]), None)
            except (OSError, ValueError, TypeError):
                entry = None
            if entry is None:   # index_blocks skipped/failed (it needs mesh.txt): fall back to the old upsert
                entry = upsert_index(job["slug"], job["address"])
            log(job, f"in blocks/index.json: {entry.get('name')}")
            with jobs_lock:
                job["state"], job["entry"] = "done", entry
        except Exception as e:  # report every failure to the poller
            log(job, f"ERROR: {e}")
            with jobs_lock:
                job["state"], job["error"] = "error", str(e)
        finally:
            with jobs_lock:
                job["finished"] = time.time()
            work.task_done()


def public(job: dict, since: int | None = None) -> dict:
    with jobs_lock:
        out = {k: job.get(k) for k in ("id", "state", "address", "half_m", "slug", "error", "entry", "created", "started", "finished",
                                       "stage", "failed_stages", "renders")}
        out["stages"] = {k: dict(v) for k, v in job["stages"].items()}
        out["view"] = f"view.html?block={job['slug']}" if job["state"] == "done" else None
        if since is not None:
            start = max(0, since - job["dropped"])
            out["lines"] = job["lines"][start:]
            out["next"] = job["dropped"] + len(job["lines"])
        ahead = [j for j in jobs.values() if j["state"] == "queued" and j["created"] < job["created"]]
        out["queue_position"] = len(ahead) if job["state"] == "queued" else 0
    return out


class Handler(SimpleHTTPRequestHandler):
    server_version = "RoofprintLocal/1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):  # quieter: only API calls and errors
        code = str(args[1]) if len(args) > 1 else ""
        if "/api/" in (getattr(self, "path", "") or "") or code[:1] in ("4", "5"):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def end_headers(self):
        if self.path.split("?")[0].endswith((".json", ".html")) or self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # --- guard: same-machine, same-origin only (no CORS, no DNS-rebinding, no cross-site GET triggers)
    def _local_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return False
        if (self.headers.get("Sec-Fetch-Site") or "") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != host:
            return False
        return True

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _params(self) -> dict:
        u = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        if self.command == "POST":
            n = min(int(self.headers.get("Content-Length") or 0), 10_000)
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
            ctype = (self.headers.get("Content-Type") or "").lower()
            if raw and "json" in ctype:
                try:
                    body = json.loads(raw)
                    if isinstance(body, dict):
                        q.update({k: str(v) for k, v in body.items()})
                except ValueError:
                    pass
            elif raw:
                q.update({k: v[-1] for k, v in parse_qs(raw).items()})
        return q

    def _api(self) -> None:
        path = urlparse(self.path).path
        if not self._local_ok():
            return self._json(403, {"error": "the build API only answers same-origin requests on 127.0.0.1"})
        p = self._params()
        if path == "/api/ping":
            return self._json(200, {"ok": True, "builder": BUILDER.exists() and PIPELINE.exists(), "enrich": ENRICH.exists(),
                                    "half_sizes": HALF_SIZES, "pipeline": pipeline_stages()})
        if path == "/api/jobs":
            with jobs_lock:
                ids = sorted(jobs, key=lambda k: jobs[k]["created"])[-30:]
            return self._json(200, [public(jobs[i]) for i in ids])
        if path == "/api/status":
            job = jobs.get(p.get("job", ""))
            if not job:
                return self._json(404, {"error": "unknown job id"})
            try:
                since = max(0, int(p.get("since", "0")))
            except ValueError:
                since = 0
            return self._json(200, public(job, since))
        if path == "/api/events":
            job = jobs.get(p.get("job", ""))
            if not job:
                return self._json(404, {"error": "unknown job id"})
            try:
                since = max(0, int(p.get("since", "0")))
            except ValueError:
                since = 0
            return self._sse(job, since)
        if path == "/api/build":
            addr = clean_address(p.get("address", ""))
            if not addr:
                return self._json(400, {"error": "give a US street address (3-160 letters, digits, spaces and , . # ' & ( ) / -), e.g. '1600 Pennsylvania Ave NW, Washington, DC'"})
            try:
                half = int(float(p.get("half_m", "60")))
            except ValueError:
                half = 60
            if half not in HALF_SIZES:
                return self._json(400, {"error": f"half_m must be one of {list(HALF_SIZES)}"})
            if not BUILDER.exists() or not PIPELINE.exists():
                return self._json(503, {"error": "usa/build_block.py or usa/pipeline.py does not exist yet, so new addresses cannot be built. Existing blocks still work.", "builder": False})
            renders = str(p.get("renders", "")).lower() in ("1", "true", "yes", "on")
            known = set(pipeline_stages())
            skip = ",".join(x for x in (x.strip() for x in str(p.get("skip", "")).split(",")) if x in known and x != "build_block")
            slug = slugify(addr)
            with jobs_lock:
                for j in jobs.values():  # same address already waiting or running: reuse that job
                    if j["slug"] == slug and j["half_m"] == half and j["state"] in ("queued", "running"):
                        jid = j["id"]
                        break
                else:
                    jid = uuid.uuid4().hex[:12]
                    jobs[jid] = {"id": jid, "state": "queued", "address": addr, "half_m": half, "slug": slug, "lines": [], "dropped": 0,
                                 "created": time.time(), "started": None, "finished": None, "error": None, "entry": None, "pid": None,
                                 "renders": renders, "skip": skip, "stages": {}, "stage": None, "stage_seq": 0, "failed_stages": []}
                    work.put(jid)
            return self._json(202, public(jobs[jid], 0))
        return self._json(404, {"error": "unknown API path"})

    def _sse(self, job: dict, since: int) -> None:
        """Stream log lines + stage changes until the job ends (or the client goes away)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        sent_seq, last_beat = -1, time.time()
        try:
            while True:
                out = public(job, since)
                chunks = [f"event: line\ndata: {json.dumps(ln)}\n\n" for ln in out["lines"]]
                since = out["next"]
                if job["stage_seq"] != sent_seq:
                    sent_seq = job["stage_seq"]
                    chunks.append(f"event: stage\ndata: {json.dumps({'stage': out['stage'], 'stages': out['stages']})}\n\n")
                ended = out["state"] in ("done", "error")
                if ended:
                    out.pop("lines", None)
                    chunks.append(f"event: end\ndata: {json.dumps(out)}\n\n")
                if not chunks and time.time() - last_beat > 15:
                    chunks.append(": keep-alive\n\n")
                if chunks:
                    self.wfile.write("".join(chunks).encode("utf-8"))
                    self.wfile.flush()
                    last_beat = time.time()
                if ended:
                    return
                time.sleep(0.7)
        except OSError:   # client went away (BrokenPipe / ConnectionReset / ConnectionAborted)
            return

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api()
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api()
        self._json(405, {"error": "POST is only accepted under /api/"})


def pipeline_stages() -> list[str]:
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from usa.pipeline import STAGES  # noqa: PLC0415
        return list(STAGES)
    except Exception:  # noqa: BLE001
        return []


def main() -> None:
    BLOCKS.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True, name="builder").start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"Roofprint local server: http://{HOST}:{PORT}/usa/build.html  (serving {WEB})", flush=True)
    print(f"builder: {'found' if BUILDER.exists() else 'MISSING'} {BUILDER.relative_to(ROOT)} · pipeline: "
          f"{'found' if PIPELINE.exists() else 'MISSING'} ({len(pipeline_stages())} stages) · python: {PYTHON}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

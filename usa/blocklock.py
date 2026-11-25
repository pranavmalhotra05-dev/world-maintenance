"""Per-block write lock shared by every script that edits web/usa/blocks/<slug>/houses.json or meta.json.

    from usa.blocklock import block_lock
    with block_lock(block_dir):
        houses = json.load(...); meta = json.load(...)   # re-read inside the lock
        ...; json.dump(...)

Protocol (same as federal_flags.py / local_flags.py): exclusive-create <block>/.lock, retry every 1 s for up to
10 minutes, treat a lock older than 15 minutes as stale, always delete it in finally.
"""
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def block_lock(block_dir, wait_s=600, stale_s=900):
    p = Path(block_dir) / ".lock"
    t0 = time.time()
    while True:
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > stale_s:
                    p.unlink(missing_ok=True); continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > wait_s:
                raise TimeoutError(f"lock busy: {p}")
            time.sleep(1)
    try:
        yield
    finally:
        p.unlink(missing_ok=True)

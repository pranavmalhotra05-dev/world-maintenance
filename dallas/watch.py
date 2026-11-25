"""Stage 6 - live storm watcher for Dallas County.

Every run: read the newest NOAA MRMS 24-hour hail file (updated every 30 min). If hail >= 0.75 in fell
anywhere in the county, fold it into that storm day's grid, re-score every home's hail exposure, and write
the storm's exposure list (homes = Overture footprints with a USDOT NAD address; NAD is not for mailing lists):

  data/dallas/events/<storm_day>/exposure.csv   all homes hit, most exposed first
  data/dallas/events/<storm_day>/summary.json   counts by hail size, by ZIP

  python watch.py            # one check (schedule this every 30 min)
  python watch.py --loop     # keep checking every 30 min in this window
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import hail  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "dallas"
PY = sys.executable
HERE = Path(__file__).parent
LOG = D / "watch.log"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def check():
    valid, lats, lons, mesh = hail.latest()
    county_max = float(mesh.max())
    # MRMS is in UTC; Dallas storms are afternoon/evening Central time, so label by local date
    storm_day = (valid - timedelta(hours=6)).date().isoformat()
    log(f"MRMS {valid:%Y-%m-%d %H:%MZ}: max {county_max / 25.4:.2f} in over Dallas County")
    if county_max < hail.MIN_MM:
        return None
    out = hail.HAIL / f"{storm_day}.npz"
    if out.exists():  # keep the larger value per cell across refreshes of the same storm day
        prev = np.load(out)["mesh"].astype(np.float32)
        if prev.shape == mesh.shape:
            mesh = np.maximum(prev, mesh)
    np.savez_compressed(out, lats=lats, lons=lons, mesh=mesh.astype(np.float16))
    (hail.HAIL / f"{storm_day}.none").unlink(missing_ok=True)

    subprocess.run([PY, str(HERE / "score.py")], check=True)
    write_event(storm_day)
    subprocess.run([PY, str(HERE / "export.py")], check=True)
    return storm_day


def write_event(storm_day):
    s = pd.read_parquet(D / "scored.parquet")
    z = np.load(hail.HAIL / f"{storm_day}.npz")
    lats, lons, mesh = z["lats"], z["lons"], z["mesh"].astype(np.float32)
    r = np.clip(np.round((lats[0] - s["lat"].values) / (lats[0] - lats[1])).astype(int), 0, len(lats) - 1)
    c = np.clip(np.round((s["lon"].values - lons[0]) / (lons[1] - lons[0])).astype(int), 0, len(lons) - 1)
    s["storm_hail_in"] = (mesh[r, c] / 25.4).round(2)
    hit = s[s["storm_hail_in"] >= 0.75].sort_values(["storm_hail_in", "score"], ascending=False)
    ev = D / "events" / storm_day
    ev.mkdir(parents=True, exist_ok=True)
    keep = ["address", "city", "zip", "storm_hail_in", "score", "roof_squares", "area_m2", "hail_days_1in", "lat", "lon"]
    for old in ("leads.csv",):  # pre-2026-11-24 files carried appraisal-district fields
        (ev / old).unlink(missing_ok=True)
    hit[keep].to_csv(ev / "exposure.csv", index=False)
    summary = {
        "storm_day": storm_day, "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "homes_hit": {k: int((hit["storm_hail_in"] >= v).sum()) for k, v in
                      {"0.75in": 0.75, "1in": 1.0, "1.5in": 1.5, "1.75in": 1.75, "2.5in": 2.5}.items()},
        "top_zips": hit.groupby("zip").size().sort_values(ascending=False).head(10).to_dict(),
    }
    (ev / "summary.json").write_text(json.dumps(summary, indent=2))
    log(f"storm {storm_day}: {summary['homes_hit']['1in']:,} homes hit by 1in+ hail -> {ev / 'exposure.csv'}")


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            try:
                check()
            except Exception as e:  # keep watching through a bad file or network blip
                log(f"error: {e}")
            time.sleep(30 * 60)
    else:
        check()

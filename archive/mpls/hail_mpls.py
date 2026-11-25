"""NOAA MRMS radar hail (MESH) since Oct 2020 for south Minneapolis, reusing the Dallas downloader.

  python hail_mpls.py      -> data/mpls/hail/<storm_day>.npz for every day with >= 0.75 in hail in the box
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dallas"))
import hail  # noqa: E402

hail.W, hail.E, hail.S, hail.N = -93.40, -93.10, 44.83, 45.05  # Minneapolis core, ~1 km radar cells
hail.HAIL = ROOT / "data" / "mpls" / "hail"
hail.HAIL.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    hail.backfill()

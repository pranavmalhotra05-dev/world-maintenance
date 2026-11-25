"""Rebuild web/usa/blocks/index.json from every finished block folder (one with meta.json, houses.json, mesh.txt).

  python usa/index_blocks.py
"""
import json
from pathlib import Path

B = Path(__file__).resolve().parents[1] / "web" / "usa" / "blocks"
out = []
for d in sorted(p for p in B.iterdir() if p.is_dir() and not p.name.startswith("_")):
    if not all((d / f).exists() for f in ("meta.json", "houses.json", "mesh.txt")):
        continue
    m = json.load(open(d / "meta.json", encoding="utf-8"))
    lon, lat = m.get("origin", [m.get("lon"), m.get("lat")])
    fips = m.get("fips") or (f"{m.get('state_fips', '')}{str(m.get('county_fips', '')).zfill(3)}" if m.get("state_fips") else None)
    out.append({"slug": d.name, "name": m.get("name") or d.name, "city": m.get("city"), "state": m.get("state"), "fips": fips,
                "lon": lon, "lat": lat, "houses": m.get("houses"), "lidar_year": m.get("lidar_year"),
                "city_data": (m.get("city_data") or {}).get("adapter")})
json.dump(out, open(B / "index.json", "w", encoding="utf-8"), indent=1)
print(len(out), "blocks:", ", ".join(f"{b['slug']} ({b['city']}, {b['state']})" for b in out))

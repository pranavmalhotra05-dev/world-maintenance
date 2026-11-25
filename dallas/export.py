"""Stage 8 - export compact data for the Dallas dashboard (web/dallas/data/).

storms.json   every hail day since Oct 2020 with homes affected (homes = Overture footprints with a NAD address)
swaths.json   per storm day, the ~1 km radar cells with hail >= 0.75 in (sparse)
exposure.json most hail-exposed homes (column arrays): NAD address, footprint size, radar hail, exposure score
zips.json     territory table: homes, recent hits, high-exposure homes, exposed roof squares per ZIP
status.json   when the data was refreshed and the newest radar file seen

Every field comes from a clear-green source: NOAA MRMS (public domain), Overture buildings (ODbL),
Overture addresses from the USDOT NAD (public domain; not for use as a mailing list), Census TIGER county + 2020 ZCTA (public domain).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "dallas"
OUT = ROOT / "web" / "dallas" / "data"
OUT.mkdir(parents=True, exist_ok=True)
PER_SQ = (450, 650)  # USD per roofing square, DFW asphalt shingle replacement (rough 2026 range)
HIGH_MIN = 60
MAX_ROWS = 45000


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, separators=(",", ":"), default=str))


storms = pd.read_csv(D / "storms.csv")
storms["county_max_in"] = storms["county_max_in"].clip(upper=4.5)
dump("storms.json", storms.to_dict("records"))

sw = {}
for f in sorted((D / "hail").glob("*.npz")):
    z = np.load(f)
    lats, lons, m = z["lats"], z["lons"], np.minimum(z["mesh"].astype(np.float32) / 25.4, 4.5)
    r, c = np.nonzero(m >= 0.75)
    sw[f.stem] = [[round(float(lons[j]), 3), round(float(lats[i]), 3), round(float(m[i, j]), 2)] for i, j in zip(r, c)]
dump("swaths.json", {"cell_deg": float(abs(lons[1] - lons[0])), "days": sw})

s = pd.read_parquet(D / "scored.parquet")
s["recent_1in"] = s["recent_hail_in"].fillna(0) >= 1
s["high"] = s["score"] >= HIGH_MIN
sz = s[s["zip"].str.len() == 5]  # homes with no ZIP even after the nearest-neighbour fill stay in the home list
z = sz.groupby("zip").agg(city=("city", lambda x: x.mode().iat[0] if len(x.mode()) else ""), homes=("bid", "size"),
                         recent_1in=("recent_1in", "sum"), high=("high", "sum"),
                         high_squares=("roof_squares", lambda x: float(x[sz.loc[x.index, "high"]].sum())),
                         max_hail=("recent_hail_in", "max"))
z["value_low"] = (z["high_squares"] * PER_SQ[0]).round(-3)
z["value_high"] = (z["high_squares"] * PER_SQ[1]).round(-3)
z["max_hail"] = z["max_hail"].fillna(0)
z = z.reset_index().sort_values("value_high", ascending=False)
dump("zips.json", z.round(2).to_dict("records"))

L = s[s["high"]].sort_values(["score", "recent_hail_in"], ascending=False).head(MAX_ROWS)
d = lambda c: L[c].dt.strftime("%Y-%m-%d").where(L[c].notna(), None).tolist()
rows = {
    "address": L["address"].tolist(), "city": L["city"].tolist(), "zip": L["zip"].tolist(),
    "lon": L["lon"].round(5).tolist(), "lat": L["lat"].round(5).tolist(),
    "score": L["score"].tolist(), "pts": [[int(p) for p in v] for v in L["pts"]],
    "hail": L["recent_hail_in"].round(2).fillna(0).tolist(), "hit_day": d("recent_hit_day"),
    "hail_ever": L["hail_max_in"].round(2).fillna(0).tolist(), "hail_days": L["recent_days_1in"].tolist(),
    "squares": L["roof_squares"].tolist(), "area": L["area_m2"].round().astype(int).tolist(),
}
dump("exposure.json", rows)

status = {"refreshed": datetime.now(timezone.utc).isoformat(timespec="minutes"), "homes": int(len(s)),
          "hail_days": int(len(storms)), "high_total": int(s["high"].sum()), "high_exported": int(len(L)),
          "high_min": HIGH_MIN, "per_square": PER_SQ, "first_day": storms["storm_day"].min(),
          "last_day": storms["storm_day"].max()}
log = D / "watch.log"
if log.exists():
    checks = [l for l in log.read_text().strip().splitlines() if " MRMS " in l]
    if checks:
        status["last_check"] = checks[-1]
dump("status.json", status)
print(json.dumps(status, indent=1))
for f in OUT.iterdir():
    print(f"  {f.name}: {f.stat().st_size / 1e6:.2f} MB")

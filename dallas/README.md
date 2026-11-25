# Dallas Hail Desk: pipeline

Maps radar hail exposure for every house-sized building with a street address in Dallas County, and
re-scores automatically when NOAA radar shows hail over the county. It is a hail-swath and
building-exposure view, not a lead list. Only clear-green open data is used.

## Sources

| Data | Source | Licence |
|---|---|---|
| Hail size (MESH, ~1 km, daily since Oct 2020) | NOAA MRMS on AWS (`noaa-mrms-pds`) | NOAA open data, public domain |
| Building footprints, road segments | Overture Maps buildings + transportation (OSM, Microsoft, Google, Esri) | ODbL |
| Street addresses | Overture Maps addresses, USDOT National Address Database rows only | Public domain. **Not for use as a mailing list.** |
| County outline, ZIP gaps | US Census TIGERweb county + 2020 ZCTAs | Public domain |
| 3D block | USGS 3DEP LiDAR TX_Pecos_Dallas_B3_2018, USGS National Map imagery | Public domain |

What is **not** known: year built, roof material, roof age, owner occupancy, value and re-roof permits.
The Dallas Central Appraisal District files ("All Rights Reserved") and the City of Dallas permit feed
(no licence) were removed. See `usa/research/licence_registry_local.md`.

## Stages

| Script | What it does | How often |
|---|---|---|
| `build_base.py` | Overture buildings + NAD addresses → `data/dallas/homes.parquet` (~532k homes: 50–600 m² footprint, not tagged non-residential, exactly one street address). Raw Overture extracts cached in `data/dallas/raw/`. | Each Overture release |
| `hail.py backfill` | NOAA MRMS radar hail, one 24 h file per day since Oct 2020, cropped to the county | Once (117 hail days) |
| `score.py` | Hail at every home and a 0–100 hail exposure score: biggest hail in 24 months 60, recency 25, repeat 1 in+ days 10, roof size 5. Missing ZIPs come from Census ZCTAs. | After any new hail |
| `watch.py` | Checks the newest radar file. On ≥0.75 in hail it saves the storm, re-scores and writes `data/dallas/events/<day>/exposure.csv` | Every 30 min |
| `export.py` | Writes dashboard data in `web/dallas/data/`: `storms.json`, `swaths.json`, `exposure.json`, `zips.json`, `status.json` | After scoring |
| `scan3d.py`, `scene.py` | 3D block: LiDAR points, aerial photo, Overture footprints raised to LiDAR heights and linked to scored homes, Overture streets | When the block changes |

## Run

```powershell
.venv\Scripts\python dallas\build_base.py      # base table (downloads Overture extracts once)
.venv\Scripts\python dallas\score.py
.venv\Scripts\python dallas\export.py
.venv\Scripts\python dallas\watch.py           # one check
.venv\Scripts\python dallas\watch.py --loop    # keep watching in this window
```

Dashboard locally: `.venv\Scripts\python -m http.server 8765 --directory web`, then open http://localhost:8765/dallas/

## Known limits

- There is no re-roof history, so many exposed roofs have already been replaced. Verify roofs from the street.
- Radar hail size (MESH) is an estimate, capped at 4.5 in because storm cores overestimate.
- Roof squares are the footprint area × an assumed 1.15 pitch and 5% overhang. They are not measured.
- NAD addresses must not be used to build mailing lists. The page and CSV say so.
- Attribution required: © OpenStreetMap contributors and Overture Maps Foundation (ODbL), plus Google Open Buildings and Esri Community Maps (CC BY 4.0).

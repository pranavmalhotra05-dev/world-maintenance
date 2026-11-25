# World Maintenance Open Source Intelligence

An open-data property intelligence toolkit. It rebuilds each house in 3D from its own USGS 3DEP LiDAR points, then adds free public data on top: radar hail, assessor records, permits, 311 cases, aerial imagery, flood zones, infrastructure, sun, moon and UV, market and mortgage estimates, and live feeds. The goal is to help maintenance trades see which homes are likely to need work: roofing, solar, tree care, lawn, pool, remodelling, insurance and real estate.

Every laser point is kept. Roof planes are fitted to the points with RANSAC, and the page shows how far the real roof deviates from those planes. The deviation is not smoothed away, so sagging or damaged roofs stay visible.

## What it does

- **3D houses from LiDAR.** Ground, roof, wall and tree points are separated, roof planes are fitted and walls are drawn down to the terrain. Each roof gets deviation statistics (spread, 95th percentile).
- **Hail history.** NOAA MRMS radar hail (MESH) at every home since October 2020. For Dallas County there is also a live watcher that re-scores homes after each storm.
- **Property dossier.** Assessor attributes and value, permits (roof and solar dates), 311 cases, a yearly aerial image series with roof colour change, flood zone, and the change in building height between the 2011 and 2022 LiDAR.
- **Yard and trees.** Trees found in the LiDAR with genus from the city inventory, front and back lawn area, NAIP lawn health, canopy, and likely pools and parking.
- **Sun and sky.** Which way the house faces, roof aspect and pitch, facade sun hours, a year-long sun trace, moon position, clear-sky UV, and shadows ray-traced over the point cloud.
- **Infrastructure.** The nearest hydrant, catch basin, transmission line, rail line, crossing, transit stop, school, hospital, fire, police, post office and park. Also the water system and sewershed.
- **Money.** Last sale, tax, estimated value today (assessed value adjusted by the FHFA House Price Index), and monthly payments with 20% down over 10, 15 and 30 years at FFIEC/CFPB APOR rates.
- **Live feeds.** NWS forecast, alerts and observations; USGS earthquakes and stream gauge; Metro Transit departures; Sentinel-2 and Landsat; NASA FIRMS fires; and the latest GOES-19 satellite image.
- **National ranking.** Every US county is scored by how many free data layers overlap there.

## Design

The whole USA is browsable; only the area you select loads in full, everything else stays a light preview. LiDAR streams directly from the free USGS 3DEP bucket. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository layout

| Path | What it is |
|---|---|
| `scripts/` | San Francisco demo: LiDAR download from the public EPT bucket, city data, per-home scoring, national county ranking |
| `dallas/` | Dallas Hail Desk: MRMS hail exposure view (DCAD data removed: no commercial licence), hail backfill and live watcher, permits, lead scoring, 3D block scene (see `dallas/README.md`) |
| `mpls/` | Minneapolis Roofprint 3D: per-house LiDAR reconstruction plus all the open-data layers |
| `web/index.html` | Rooftop Intel page (San Francisco) |
| `web/dallas/` | Dallas Hail Desk page |
| `web/mpls/` | Roofprint 3D page (deck.gl) |
| `web/catalog/` | Open Property Intelligence Catalog: 100+ free sources, with update rate, licence and a commercial-use verdict for each |
| `config.json` | Study area for the San Francisco demo |

## Quick start

Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\pip install numpy pandas scipy requests shapely pyproj geopandas "laspy[lazrs]" eccodes rasterio pystac-client planetary-computer pvlib pillow lerc duckdb pyarrow openpyxl
```

To build the Minneapolis block, run these from the repo root, in this order:

```powershell
.venv\Scripts\python mpls\house3d.py --lon -93.2420 --lat 44.9290 --half-m 100
.venv\Scripts\python mpls\osint.py
.venv\Scripts\python mpls\hail_mpls.py
.venv\Scripts\python mpls\feeds.py
.venv\Scripts\python mpls\derive.py
.venv\Scripts\python mpls\money.py
.venv\Scripts\python mpls\civic.py
.venv\Scripts\python mpls\orient.py
.venv\Scripts\python mpls\extras.py
.venv\Scripts\python mpls\infra.py
```

`mpls\live.py` re-pulls the fast-changing layers (fires, traffic incidents, GOES image, permits, 311) in about a minute. It is meant to run every few hours. `mpls\feeds.py` does the same for the live feeds.

To view the pages, serve the web folder and open http://localhost:8765/mpls/:

```powershell
.venv\Scripts\python -m http.server 8765 -d web
```

## Data is not in this repo

The scripts download everything themselves. Raw downloads go to `data/` and page data to `web/*/data/`, and both are git-ignored. A full Minneapolis block is about 40 MB of point data.

## Data sources and licences

The code is licensed under Apache-2.0 (see `LICENSE`). Each dataset keeps its own licence. The catalog page lists them all.

Policy: only sources that are permanently free and whose terms allow commercial use. A free key or signup is acceptable under those terms. Non-commercial sources are excluded.

- OpenStreetMap data is ODbL, which is share-alike. Keep it as a separate layer.
- **Green-only data policy:** only sources whose licence explicitly allows commercial use are kept. Grey or unclear sources (Zillow, Realtor.com, Freddie Mac PMMS, FRED, Hennepin and Maricopa county data, DCAD, Franklin County Auditor, public Overpass servers) were removed or replaced. See the catalog for every source and licence.
- NOAA, USGS, NASA, Census, FEMA, EPA and other federal data is public domain. Most city and county portals publish under CC0 or as open public records.

## Privacy rules

This project is about houses, not people.

- No owner names, mailing addresses or phone numbers are kept. Permit applicant names are dropped.
- Crime appears only as block-level, anonymised points.
- There is no tracking of individuals, vehicles, phones or whether anyone is home.
- Sex-offender registry names are never used.

## Status

This is a research toolkit, not a finished product. The Dallas and Minneapolis pipelines work end to end for the areas they were built on. Other places need their own city and county sources wired in. USGS LiDAR, NOAA hail and the federal layers already cover the whole country.

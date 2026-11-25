# World Maintenance Open Source Intelligence

An open-data property intelligence toolkit. It rebuilds each house in 3D from its own USGS 3DEP LiDAR points, then adds free public data on top: radar hail, assessor records, permits, 311 cases, aerial imagery, flood zones, infrastructure, sun, moon and UV, market and mortgage estimates, and live feeds. The goal is to help maintenance trades see which homes are likely to need work: roofing, solar, tree care, lawn, pool, remodelling, insurance and real estate.

Every laser point is kept. Roof planes are fitted to the points with RANSAC, and the page shows how far the real roof deviates from those planes. The deviation is not smoothed away, so sagging or damaged roofs stay visible.

## What it does

- **3D houses from LiDAR.** Ground, roof, wall and tree points are separated, roof planes are fitted and walls are drawn down to the terrain. Each roof gets deviation statistics (spread, 95th percentile).
- **Hail history.** NOAA MRMS radar hail (MESH) at every home since October 2020. (The old Dallas live hail watcher is in `archive/dallas/`.)
- **Property dossier.** Assessor attributes and value, permits (roof and solar dates), 311 cases, a yearly aerial image series with roof colour change, flood zone, and the change in building height between the 2011 and 2022 LiDAR.
- **Yard and trees.** Trees found in the LiDAR with genus from the city inventory, front and back lawn area, NAIP lawn health, canopy, and likely pools and parking.
- **Sun and sky.** Which way the house faces, roof aspect and pitch, facade sun hours, a year-long sun trace, moon position, clear-sky UV, and shadows ray-traced over the point cloud.
- **Infrastructure.** The nearest hydrant, catch basin, transmission line, rail line, crossing, transit stop, school, hospital, fire, police, post office and park. Also the water system and sewershed.
- **Money.** Last sale, tax, estimated value today (assessed value adjusted by the FHFA House Price Index), and monthly payments with 20% down over 10, 15 and 30 years at FFIEC/CFPB APOR rates.
- **Live feeds.** NWS forecast, alerts and observations; USGS earthquakes and stream gauge; Metro Transit departures; Sentinel-2 and Landsat; NASA FIRMS fires; and the latest GOES-19 satellite image.
- **National ranking.** Every US county is scored by how many free data layers overlap there.

## One globe app

Everything is one page: **`web/app/index.html`**, the World Maintenance globe. The dot globe, the USA county map, the 3D LiDAR blocks and the LiDAR explorer used to be separate pages; they are now zoom levels of the same globe. What loads depends on how far you zoom in and where you hover:

| Level | Zoom (approx.) | What you see | Loaded when |
|---|---|---|---|
| Globe | about 0-3 | Real-data dot globe, stars, weather | Always, once |
| USA / counties | about 3-9 | County colours and scores, outlines | Zooming onto the USA |
| Preview | about 9-14 | Coarse LiDAR, tract colours, building dots | The area is on screen or hovered, but not selected |
| Selected area | about 14+ | Every LiDAR point, 3D houses with roof-deviation colours, per-house data | You select a county, a city or a block |

Moving to another area unloads the previous one. LiDAR streams straight from the free USGS 3DEP bucket. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Links into the app use one URL scheme:

```
app/index.html?lat=<deg>&lon=<deg>&z=<zoom>&pitch=<deg>&bearing=<deg>&block=<slug>&house=<id>&area=county:<fips>|place:<geoid>
```

The old page URLs still work; each one is now a small redirect into the app:

| Old URL | Opens |
|---|---|
| `/` (site root) | `app/` at the USA view (z 4) |
| `usa/index.html` (county radar) | `app/` at the USA view (z 4), or `area=county:<fips>` when given `?county=` |
| `usa/globe.html` | `app/` at the whole globe (z 1.5) |
| `usa/view.html?block=<slug>&house=<id>` | `app/?block=<slug>&house=<id>` |
| `catalog/` | `app/?panel=catalog` (the data catalog panel) |
| `lidar/index.html?lat=&lon=&r=<m>&area=` | `app/?lat=&lon=&z=<from r>&area=` (r = 1000 m gives z 15; each halving of r adds 1) |

## Repository layout

| Path | What it is |
|---|---|
| `web/app/` | The World Maintenance globe app (the whole site) |
| `web/usa/` | Shared page code and data used by the app: `theme.js`, `pack.js`, `data/`, `blocks/`, `metros/`, plus `build.html` and `rules.html` |
| `web/lidar/` | Potree build and USGS 3DEP EPT project boundaries used for point streaming |
| `web/catalog/` | Redirect to the catalog panel in the app (`app/?panel=catalog`): the Open Property Intelligence Catalog, 100+ free sources with update rate, licence and a commercial-use verdict for each |
| `usa/` | National build pipeline: blocks, metros, county and tract layers, area packs (`usa/pack_site.py`) |
| `scripts/` | San Francisco demo scripts and the national county ranking |
| `dallas/hail.py` | MRMS radar hail download and GRIB2 decode, used by `usa/enrich_national.py` and `usa/metro_build.py` |
| `archive/` | Early prototypes, not maintained: Rooftop Intel (San Francisco), Roofprint Minneapolis, Dallas Hail Desk, and copies of the pages the app replaced (see `archive/README.md`) |
| `config.json` | Study area for the San Francisco demo |

## Quick start

Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\pip install numpy pandas scipy requests shapely pyproj geopandas "laspy[lazrs]" eccodes rasterio pystac-client planetary-computer pvlib pillow lerc duckdb pyarrow openpyxl
```

Build blocks and metros with the `usa/` pipeline (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#build-flow)), for example:

```powershell
.venv\Scripts\python -m usa.pipeline --address "4116 21st Ave S, Minneapolis, MN"
```

To run the site locally, serve the `web` folder and open http://localhost:8765/ (it opens the app):

```powershell
python -m http.server 8765 --directory web
```

Live site: https://pranavmalhotra05-dev.github.io/world-maintenance/ (the root opens `app/`).

## Data is not in this repo

The scripts download everything themselves. Raw downloads go to `data/` and page data to `web/*/data/`, and both are git-ignored. A full block is about 40 MB of point data.

## Data sources and licences

The code is licensed under Apache-2.0 (see `LICENSE`). Each dataset keeps its own licence. The catalog panel in the app (`app/?panel=catalog`) lists them all.

Policy: only sources that are permanently free and whose terms allow commercial use. A free key or signup is acceptable under those terms. Non-commercial sources are excluded.

- OpenStreetMap data is ODbL, which is share-alike. Keep it as a separate layer.
- **Green-only data policy:** only sources whose licence explicitly allows commercial use are kept. Grey or unclear sources (Zillow, Realtor.com, Freddie Mac PMMS, FRED, Hennepin and Maricopa county data, DCAD, Franklin County Auditor, public Overpass servers) were removed or replaced. See the catalog panel (`app/?panel=catalog`) for every source and licence.
- NOAA, USGS, NASA, Census, FEMA, EPA and other federal data is public domain. Most city and county portals publish under CC0 or as open public records.

## Privacy rules

This project is about houses, not people.

- No owner names, mailing addresses or phone numbers are kept. Permit applicant names are dropped.
- Crime appears only as block-level, anonymised points.
- There is no tracking of individuals, vehicles, phones or whether anyone is home.
- Sex-offender registry names are never used.

## Status

This is a research toolkit, not a finished product. The `usa/` pipeline builds blocks and metros anywhere USGS 3DEP covers; city and county layers exist only where their sources are wired in (`usa/adapters/`). USGS LiDAR, NOAA hail and the federal layers already cover the whole country.

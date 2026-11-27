# Ships (AIS) for the globe's sky/sea layer - sources and licences

Checked 2026-11-26 with the built-in browser (licence pages) and curl (file listings, CORS). Machine-readable: `ships.json`.

| Source | Coverage | Access | Licence (quote) | Verdict |
|---|---|---|---|---|
| **MarineCadastre.gov Vessel Traffic (AIS)**: BOEM + NOAA OCM, from USCG NAIS | US waters | daily zip `coast.noaa.gov/htdata/CMSP/AISDataHandler/{y}/AIS_{y}_{m}_{d}.zip`, no key. Newest is **2024-12-31** (the 2025 folder returns 404) | "The information on government web pages is in the public domain and not subject to copyright protection within the United States unless specifically annotated otherwise". Needs a 17 U.S.C. 403 notice. | **GREEN** (used) |
| **Fintraffic Digitraffic marine AIS** | Finland / Baltic | live REST `meri.digitraffic.fi/api/ais/v1/locations` + `/vessels`, no key, **CORS `*`** | "licensed under the Creative Commons 4.0 By license ... even commercially". Credit: "Source: Fintraffic / digitraffic.fi, license CC 4.0 BY" | **GREEN** (live in the browser) |
| Kystverket open AIS | Norway | raw NMEA over TCP 153.44.253.27:5631; the BarentsWatch API needs client registration | "dataene er regulert under Norsk Lisens for offentlige data - NLOD" (credit Kystverket) | GREEN licence, but a static page can't use it (raw TCP) |
| Danish Maritime Authority AIS | Denmark | web.ais.dk/aisdata | Not verified: the browser refused the page and dma.dk shows no licence wording | EXCLUDED |
| aisstream.io | global, live WebSocket | GitHub sign-in + key | No data licence published (/terms returns 404) | EXCLUDED |
| AISHub | global | only if you run your own receiver | "passing on the raw data to others, for free or for money, is not allowed" | RED |
| Global Fishing Watch | global | API key | "only available for non-commercial purposes" | RED |
| MarineTraffic / VesselFinder / FleetMon | global | paid | proprietary | RED |
| USCG NAIS, NOAA NCEI | - | - | Not checked separately. MarineCadastre is the public release of USCG NAIS | - |

## What was built
`usa/build_sky_ships.py` → `web/app/data/sky/ships.json` (one day, MarineCadastre 2024-12-31).

**Privacy.** These vessels are dropped entirely: pleasure craft (37), sailing (36), military (35), law enforcement (55), and types 0-29, 90-99 or missing. The file never stores MMSI, IMO, call sign or name.

**Type groups.** Fishing 30; tug 31/32/52; passenger 60-69; cargo 70-79; tanker 80-89; other commercial 33/34/40-49/50/51/53/54/58/59.

**Tracks.** Only real AIS reports are kept. Per vessel, a point is dropped if it comes less than 30 s after the previous kept point, or if it sits at an identical position to the previous kept point (rounded to 4 decimals, about 11 m).

**Size.** The source data is already about one report per minute, so full-US tracks would be about 68 MB (2.5 M points). The file therefore has:
- **ships** for the whole US: 8,375 commercial vessels, each at its last position of the day. That's 3,265 tug, 1,397 cargo, 1,311 fishing, 1,041 passenger, 739 tanker and 622 other.
- **tracks** only for the top port regions: LA/Long Beach, Houston/Galveston, NY/NJ, SF Bay, Puget Sound and Savannah. That's 1,771 track segments with 460,726 real points. Each track is cut where the vessel leaves a region.

The file is 13.0 MB. Adding Chesapeake would bring it to 15.1 MB, over the budget. The region boxes are listed in `trackRegions`.

The 7.6 M raw rows (17,291 vessels) came down to 5.0 M rows (8,375 vessels) after the privacy filter. The zip was deleted after the build.

## Live Digitraffic (browser)
- `GET https://meri.digitraffic.fi/api/ais/v1/locations` (GeoJSON FeatureCollection, about 900 features). Each feature has `geometry.coordinates [lon,lat]` and `properties {mmsi, sog, cog, navStat, heading, timestampExternal(ms)}`.
- `GET https://meri.digitraffic.fi/api/ais/v1/vessels`: an array of `{mmsi, shipType, name, callSign, imo, ...}`. Join this on mmsi for the type only, and never display the name or IDs.
- Send the header `Digitraffic-User: <app name>`. There is no position-history endpoint, so trails must be built from successive polls (for example once a minute).

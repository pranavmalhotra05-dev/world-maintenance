# Ground, water and sea sensor networks (in-situ) — research lane "ground-networks"

Date: 2026-11-26. Full records (access URLs, exact licence wording, caveats): `ground_networks.json`.
Licences read on official pages with the built-in browser. CORS = curl probe with an `Origin` header; "yes" means `Access-Control-Allow-Origin` came back, so the browser can fetch it directly.

Rule: GREEN only for public domain, CC0, CC BY, CC BY-SA, ODbL or an explicit commercial-use statement, permanently free.

## Table

| Source | Measures | Density | CORS | Licence (official) | Verdict | In app? | Per-house use |
|---|---|---|---|---|---|---|---|
| USGS Water Data OGC API (api.waterdata.usgs.gov) | stage, discharge, well depth-to-water, peaks | ~13k live gages, 1M+ wells | yes | USGS public domain | GREEN (filter agency_code=USGS for cooperator sites) | wells only (web/app/points.js) | high: flood, basement, foundation |
| USGS NWIS WaterServices (legacy) | same | same | yes | USGS PD | GREEN (being retired) | archive only | fallback |
| USGS annual peaks (`peaks` collection) | yearly max flow/stage | ~28k sites | yes | USGS PD | GREEN | no | flood history |
| NOAA NWPS gauges (api.water.noaa.gov/nwps) | observed/forecast stage + flood categories + impact text | ~10k points | yes | NWS: PD, "any lawful purpose" | GREEN | no | high: storm/flood leads |
| USGS STN high-water marks | surveyed flood heights after events | 100s-1000s per event | API moved (403) | USGS PD | GREEN licence; access route unclear | no | very high after floods |
| USGS ComCat earthquakes | location, magnitude | all US M~1+ | yes | USGS PD | GREEN | archive only | medium |
| USGS ShakeMap | MMI/PGA grids per event | ~1 km | yes | USGS PD | GREEN | no | high in quake areas |
| USGS Did You Feel It? | reported intensity, 1 km cells / ZIP | 1 km | yes | USGS PD | GREEN | no | high |
| EarthScope SAGE seismic (FDSN) | waveforms, stations | 1000s | yes (station) | CC BY 4.0 unless contributor's licence is stricter | GREEN (per network) | no | low |
| EarthScope GAGE GNSS | ground motion mm/yr | ~2k stations | no | CC BY 4.0, "including commercial" | GREEN | yes (usa/build_gnss.py) | medium |
| Strong motion: USGS NSMP / CESMD | PGA per station | ~1.5k | via USGS | USGS PD / CGS unstated | GREEN (USGS) / GREY (CESMD) | no | low beyond ShakeMap |
| NOAA CO-OPS tide gauges + high-tide flooding | water level, datums, sea-level trend, HTF days | ~210 NWLON+ | yes | "public domain ... may be used freely" | GREEN | no | high for coast |
| NOAA NDBC buoys | waves, water temp | ~1.3k incl. partners | no | NWS PD | GREEN (NDBC-owned stations) | no | low |
| Argo floats | ocean T/S profiles | ~4k floats | varies | CC BY 4.0 | GREEN | no | none (globe visual) |
| IOOS regional ERDDAPs | coastal sensors | 1000s | yes | per-dataset; generic ERDDAP text, no commercial statement | GREY | no | low |
| NRCS SNOTEL + SCAN (AWDB API) | SWE, snow depth, soil moisture/temp | ~900 + ~200 sites | yes | USDA: "public domain information may be freely distributed or copied" | GREEN | no (SSURGO is) | medium: mountain roof load |
| NOAA NOHRSC / SNODAS | snow depth, SWE 1 km grid, snowfall obs | 1 km | no | NWS PD | GREEN (gridded) | no | high: roof snow load |
| NOAA USCRN soil | soil moisture/temp 5-100 cm | ~140 stations | yes | NOAA PD | GREEN | no | low (sparse) |
| State mesonets' soil sensors (e.g. Oklahoma) | soil moisture | ~1/county | varies | Oklahoma: commercial customers pay fees | RED (check each state) | no | — |
| U.S. Drought Monitor | weekly D0-D4 polygons | county-ish | yes | "authorized for use by third parties, including by commercial and non-commercial entities" + attribution | GREEN | no | high: foundation, landscaping, pools |
| USGS landslides (inventory, ComCat landslide events, monitoring) | landslide polygons, slope sensors | varies | ComCat yes | USGS PD | GREEN | no | medium in hills |
| USGS extensometers | land subsidence | tens of sites | yes | USGS PD | GREEN | no | low-medium |
| USGS geomagnetic observatories | magnetic field | 14 sites | yes | USGS PD | GREEN | no | none |
| Water Quality Portal | well/stream chemistry (hardness, chloride, nitrate...) | millions of sites | yes | no portal licence; NWIS records USGS PD; WQX records unstated | GREEN (providers=NWIS) / GREY (WQX) | no | medium: plumbing/water heaters |
| EPA RadNet | air radiation | ~140 | no | EPA disclaimer hedges commercial use | GREY | no | none |
| ALERTWildfire cameras | live images | ~1k | n/a | no terms published | GREY | no | low |
| GBIF / iNaturalist, CC0 + CC BY records | termites, ash borer, invasive plants | dense | yes | per record CC0/CC BY (CC BY-NC excluded) | GREEN (filtered) | listed in catalog.js | medium: wood-damage/tree leads |
| eBird | birds | dense | n/a | "will not use ... for commercial purposes whatsoever" | RED | excluded | — |
| NASA GLOBE Observer | citizen obs | sparse | ? | not verified | GREY | no | low |
| Chicago Array of Things / city IoT | street sensors | city | yes | City reserves IP rights | GREY | no | low |
| Noise monitors (airport, SONYC) | dB | local | n/a | vendor terms, no open licence | GREY | no | low |
| EIA | plants, prices, RECS | utility/state | yes | US gov PD | GREEN | yes | already used |
| HIFLD Open | — | — | — | retired 2025-08-26 | RED | noted as retired | — |
| Building permits | permits | parcel | varies | per city | n/a | yes (usa/city_enrich.py) | high |

## Top 5 to build next

1. **Live flood-stage layer** — NOAA NWPS gauges (flood categories + impact text) joined to USGS Water Data streamgages. Both CORS-open, public domain. Show gage pins turning orange/red at minor/moderate/major flood stage; per house: "nearest river gage is 2 ft above flood stage". Strongest storm/flood-damage trigger.
2. **Drought x shrink-swell foundation risk** — USDM weekly polygons (explicit commercial permission, attribution text required) combined with the SSURGO shrink-swell already in `usa/build_maintenance.py`. Per house: "9 weeks in D2 on high-shrink clay". Also feeds landscaping/irrigation and pool-evaporation leads.
3. **Earthquake felt-shaking** — DYFI 1 km cells plus ShakeMap MMI per event (USGS PD, CORS). Per house: "reported shaking MMI VI on 2026-xx-xx" → foundation, chimney and pool-crack inspections.
4. **Coastal water levels** — NOAA CO-OPS live water level vs flood thresholds plus annual high-tide-flooding day counts and sea-level trend at the nearest gauge (PD, CORS).
5. **Roof snow load** — NOHRSC SNODAS 1 km snow-water-equivalent (NOAA PD; build offline, no CORS) converted to lb/sq ft per house, with SNOTEL points in mountain towns. Roof/ice-dam leads after storms.

Honourable mentions: USGS STN high-water marks (best flood evidence, but the API moved and returned 403 on 2026-11-26; re-probe or use ScienceBase releases), WQP USGS-only hardness for plumbing leads, GBIF CC0/CC BY termite sightings.

## Exclusions

- RED: eBird (non-commercial API terms), Oklahoma Mesonet and other fee-for-commercial state mesonets, HIFLD Open (retired), GBIF/iNat CC BY-NC records.
- GREY: IOOS regional datasets without a per-dataset CC/PD licence, ALERTWildfire (no terms), EPA RadNet (hedged commercial wording, and no value), Chicago/city IoT (rights reserved), CESMD-only strong-motion records, WQP WQX records, GLOBE Observer (not verified), noise monitors.

# SAT-4: Americas/other government satellites, commercial open-data programmes, derived global products

Checked 2026-11-24. Licences read from the official registry, programme or dataset page linked in each row; S3 buckets listed anonymously where noted. Data file: `satellites_americas_commercial.json`.

**Counts:** 42 records - commercial_ok yes 22, verify 5, no 15. By kind: government 15, commercial open-data 12, derived global 15. Status: accepted 21, verify 2, accepted-not-relevant 2, rejected 17.

## Summary table

| Dataset | Kind | Sensor | Res (m) | US coverage | Licence | Commercial OK | Status |
|---|---|---|---|---|---|---|---|
| [RADARSAT-1 archive (Level-1, COG)](https://registry.opendata.aws/radarsat-1/) | government | C-band SAR | 8-100 (mode dependent) | Global archive incl. North America / US scenes; not wall-to-wall in time | Registry: 'Open Government License (OGL)' (Open Government Licence - Canada) | yes | accepted |
| [RADARSAT-1 US mosaic (1998-1999)](https://open.canada.ca/data/en/dataset/bfb2331e-6d68-41c0-8cf4-c2e9c4d68eb7) | government | C-band SAR mosaic | ~100 (mosaic) | Whole contiguous US (190 scenes) | open.canada.ca record licence: Open Government Licence - Canada | yes | accepted |
| [RADARSAT Constellation Mission (RCM) National Land Mosaic](https://open.canada.ca/data/en/dataset/f316c469-c068-46f9-a650-95a75f461106) | government | C-band SAR mosaic | 30 | Canada landmass only - NO US | Open Government Licence - Canada (open.canada.ca record) | yes | accepted |
| [RADARSAT Constellation Mission imagery (scene level)](https://open.canada.ca/data/en/dataset?q=RADARSAT+Constellation) | government | C-band SAR (3 sats) | 3-100 | Mostly Canada/North America tasked; access via EODMS | Open-data access for RCM scenes is restricted/partial; public open.canada.ca records found are only derived products (mosaic, sea-ice motion, acquisition plans). CSA site refused automated access. | verify | verify |
| [SCISAT (ACE-FTS / MAESTRO)](https://open.canada.ca/data/en/dataset/02969436-8c0b-4e6e-ad40-781cdb43cf24) | government | Solar-occultation spectrometer (atmosphere) | n/a (profiles) | Global atmospheric profiles | Open Government Licence - Canada | yes | accepted-not-relevant |
| [CBERS-4 / CBERS-4A (MUX, WFI, PAN, WPM)](https://registry.opendata.aws/cbers/) | government | Optical multispectral + pan | 2 (WPM pan) / 8 / 16 / 64 | South America focus (Brazilian receiving stations) - essentially no US | Registry licence: CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0/) | yes | accepted |
| [Amazonia-1 (WFI)](http://www.dgi.inpe.br/catalogo/explore) | government | Optical wide-field | 64 | South America; no US | Free catalogue; no explicit open licence text found on pages fetched | verify | rejected |
| [SAOCOM-1A/1B](https://catalogos.conae.gov.ar/catalogo/catalogo.html) | government | L-band SAR | 10-100 | Argentina/South America primarily; some global acquisitions | Portal lists SAOCOM under 'Productos Licenciados' (contact CONAE); a 'free-access SAOCOM products' search exists but terms of use did not open (popup blocked) - not confirmed commercial-OK | verify | rejected |
| [SAC-D / Aquarius](https://podaac.jpl.nasa.gov/aquarius) | government | L-band radiometer (sea-surface salinity) | ~100 km | Global oceans | NASA data, no restriction on use | yes | accepted-not-relevant |
| [PeruSAT-1](https://www.gob.pe/conida) | government | Optical VHR | 0.7 | Peru-focused tasking | No public open-data licence located | no | rejected |
| [FASat-Charlie / FASat-Delta](https://www.fach.mil.cl/) | government | Optical | 1.45 / ~0.7 | Chile | No open-data programme | no | rejected |
| [Mexico (no national EO open-data satellite)](https://www.inegi.org.mx/temas/imagenes/) | government | - | - | Mexico | INEGI redistributes third-party imagery (e.g. SPOT/Landsat mosaics); no own open satellite feed | no | rejected |
| [NigeriaSat-2 / NigeriaSat-X](https://nasrda.gov.ng/) | government | Optical | 2.5-32 | Africa | Not open | no | rejected |
| [SANSA (South Africa) data](https://www.sansa.org.za/) | government | Redistributes SPOT/Landsat/Sentinel; ZACube | - | South Africa | SPOT data under licence; no own open VHR feed | no | rejected |
| [EgyptSat-A / MisrSat-2](https://www.egsa.gov.eg/) | government | Optical | ~1-7 | Egypt | Not open | no | rejected |
| [Capella Space Open Data](https://registry.opendata.aws/capella_opendata/) | commercial | X-band SAR (spotlight/stripmap/sliding spotlight; SLC/GEO/GEC/SICD/SIDD) | 0.3-1.2 | Event/sample based worldwide incl. many US sites; NOT wall-to-wall | Registry: 'License CC BY 4.0' | yes | accepted |
| [Umbra Open Data](https://registry.opendata.aws/umbra-open-data/) | commercial | X-band SAR spotlight (SICD/GEC/CPHD) | 0.25-1 | Event/sample based: US sites include Houston TX, Port of Long Beach, Savannah, Newark, Las Vegas stadium, Highmark stadium, Nevada mines, ND farms, Texas floods 2025, Washington atmospheric river 2025 | Registry: 'All data is provided with a Creative Commons License (CC by 4.0)' | yes | accepted |
| [ICEYE Open Data](https://registry.opendata.aws/iceye-opendata/) | commercial | X-band SAR (spot/strip/scan; stacks & pairs) | 0.25-15 | Event/sample based incl. disaster releases; some US | Registry: 'provided under the Creative Commons License CC BY 4.0' | yes | accepted |
| [Satellogic EarthView](https://satellogic-earthview.s3.us-west-2.amazonaws.com/index.html) | commercial | Optical RGB+NIR (TOA reflectance) | 1 | Regional tiles (large area, multi-region, some revisits); check US tiles per region - not complete coverage | Dataset page: 'licensed under the Creative Commons Attribution 4.0 International Public License (CC BY 4.0)' | yes | accepted |
| [Maxar / Vantor Open Data Program](https://www.maxar.com/open-data) | commercial | Optical VHR (WorldView) | 0.3-0.5 | Event based incl. US hurricanes/wildfires | Registry: 'Creative Commons Attribution Non Commercial 4.0'; maxar.com/open-data: CC BY-NC 4.0 | no | rejected |
| [Planet Disaster Data](https://www.planet.com/disasterdata/) | commercial | Optical (PlanetScope/SkySat) | 0.5-3 | Event based | Page: data 'subject to a CC-BY-NC license, unless otherwise stated' | no | rejected |
| [Planet NICFI tropical basemaps](https://www.planet.com/nicfi/) | commercial | Optical mosaics | 4.77 | Tropics only - NO US | NICFI licence was non-commercial-restricted; nicfi page now redirects to planet.com/impact (programme appears closed). Derived cTrees Amazon canopy height on AWS inherits NICFI licence. | no | rejected |
| [BlackSky open data](https://www.blacksky.com/) | commercial | Optical VHR | ~0.35-1 | - | No open-data programme found | no | rejected |
| [Airbus OneAtlas samples](https://www.airbus.com/en/space/earth-observation) | commercial | Optical (Pleiades/Pleiades Neo/SPOT) | 0.3-1.5 | Sample scenes | Samples are for evaluation under Airbus terms, not an open licence | no | rejected |
| [Spire data](https://spire.com/data/) | commercial | RO weather, AIS, ADS-B | - | - | Commercial licence | no | rejected |
| [Satellite Vu (thermal)](https://www.satellitevu.com/) | commercial | Thermal IR video/imagery | ~3.5 | - | No open licence found | no | rejected |
| [Pixxel (hyperspectral)](https://pixxel.space/) | commercial | Hyperspectral | 5 | - | No open licence found | no | rejected |
| [Esri / Impact Observatory / Microsoft 10 m annual LULC](https://registry.opendata.aws/io-lulc/) | derived | Sentinel-2 derived land cover (9 classes) | 10 | Global wall-to-wall incl. all US | Registry: 'License CC BY 4.0' | yes | accepted |
| [ESA WorldCover](https://registry.opendata.aws/esa-worldcover-vito/) | derived | Sentinel-1/2 land cover | 10 | Global incl. US | Registry: 'CC-BY 4.0' | yes | accepted |
| [Dynamic World V1](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1) | derived | Sentinel-2 per-scene land cover probabilities | 10 | Global incl. US | Earth Engine catalogue: 'This dataset is licensed under CC-BY 4.0' with required attribution | yes | accepted |
| [Meta / WRI High-Resolution Canopy Height](https://registry.opendata.aws/dataforgood-fb-forests/) | derived | ML canopy height from Maxar imagery | 1 | Global incl. US | Registry licence: https://creativecommons.org/licenses/by/4.0/ | yes | accepted |
| [Meta HRSL population (hrsl-cogs)](https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps) | derived | ML population density | 30 | Global incl. US | CC BY 4.0 per Data for Good HRSL (same bucket); verify per-file README | verify | verify |
| [Google Open Buildings v3](https://sites.research.google/open-buildings/) | derived | ML building polygons (+2.5D temporal) | ~0.5 (source imagery) | Africa, S/SE Asia, Latin America, Caribbean - NOT the US | Page: shared under CC BY 4.0 AND ODbL v1.0 - user picks | yes | accepted |
| [Microsoft Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) | derived | ML building polygons + heights | n/a (from ~0.3-0.5 m imagery) | Global incl. US (1.4B buildings) | README: 'licensed by Microsoft under the CDLA Permissive 2.0' | yes | accepted |
| [Microsoft US Building Footprints (2018 release)](https://github.com/microsoft/USBuildingFootprints) | derived | ML building polygons | n/a | All US states | README: Open Data Commons Open Database License (ODbL) | yes | accepted |
| [Overture Maps (buildings, places, addresses, transport)](https://registry.opendata.aws/overture/) | derived | Conflated vector map | n/a | Global incl. US | Registry: CDLA Permissive v2 unless derived from ODbL sources (OSM-derived layers ODbL v1.0) | yes | accepted |
| [Global Human Settlement Layer (GHSL)](https://registry.opendata.aws/ghs-land/) | derived | Built-up surface/volume/height, population, SMOD | 10-1000 | Global incl. US | Registry: 'GHSL data are provided under the Creative Commons Attribution 4.0 International (CC BY 4.0) license' | yes | accepted |
| [WorldPop](https://hub.worldpop.org/) | derived | Gridded population / demographics | 100 (some 1 km) | Global incl. US | Footer: 'WorldPop datasets are licensed under the Creative Commons Attribution 4.0 International License' | yes | accepted |
| [EOG VIIRS Nighttime Lights (VNL annual/monthly)](https://eogdata.mines.edu/products/vnl/) | derived | VIIRS DNB radiance composites | ~500 | Global incl. US | Page: 'Many of the VIIRS Nighttime Lights data are available under Creative Commons Attribution 4.0' - per-product list in EOG_products_CC_License.pdf | verify | accepted |
| [World Bank Light Every Night (DMSP+VIIRS)](https://registry.opendata.aws/wb-light-every-night/) | derived | Raw nightly DMSP/VIIRS | ~750 | Global incl. US | Registry: 'World Bank Open Database License (ODbL)' / World Bank open data licence | yes | accepted |
| [NASA VIIRS (Black Marble etc.)](https://registry.opendata.aws/nasa-viirs-snpp/) | derived | VIIRS land products | 375-500 | Global incl. US | Registry: 'Creative Commons BY 4.0' | yes | accepted |
| [cTrees Amazon canopy height](https://registry.opendata.aws/ctrees-amazon-canopy-height/) | derived | Canopy height from NICFI imagery | ~5 | Amazon only | Registry: follows NICFI licensing agreement | no | rejected |

## Best commercial-open sources for very-high-resolution US imagery

None of these is wall-to-wall. They are curated sample/event collections: useful for demos, ML training, and the specific sites they happen to cover, not as a nationwide imagery layer (NAIP and state orthophotos remain the wall-to-wall optical base).

1. **Umbra Open Data** (CC BY 4.0, 0.25-1 m SAR). Most US sites of any programme found, many with repeat stacks: Houston, the ports of Long Beach, Savannah and Newark, Las Vegas and Buffalo stadiums, Nevada mines and factories, North Dakota farms, the 2025 Texas floods and the Dec 2025 Washington atmospheric river. Anonymous S3, no key.
2. **Capella Space Open Data** (CC BY 4.0, 0.3-1.2 m SAR). About 1,000 collects worldwide, with STAC catalogues organised by capital city, use case and date. New collects are added quarterly.
3. **ICEYE Open Data** (CC BY 4.0, 0.25-15 m SAR). Disaster and event releases plus coherent stacks and pairs. Updated often.
4. **Satellogic EarthView** (CC BY 4.0, 1 m RGB+NIR optical). The only commercial-OK, free, roughly 1 m optical satellite set found. Captured Jul-Dec 2022 by region. Check its tiles for US areas of interest before relying on it.

All four require attribution. SAR at this resolution shows roof geometry, construction and flood state, but not roof material or colour.

Rejected for commercial use: **Maxar/Vantor Open Data** (CC BY-NC 4.0) and **Planet Disaster Data** (CC BY-NC), even though both cover US disasters at very high resolution.

## Best derived global products for US property work
- **Microsoft Global ML Building Footprints**: now CDLA Permissive 2.0 (not ODbL), with height estimates. Use it in preference to the 2018 ODbL US release.
- **Overture buildings/addresses/places**: CDLA-P 2.0. OSM-derived parts are ODbL.
- **Meta/WRI 1 m canopy height**: CC BY 4.0. Shows tree overhang and solar shading.
- **GHSL built height/volume** and **Impact Observatory 10 m LULC**: CC BY 4.0. Context layers.
- **Dynamic World**: data is CC BY 4.0, but Earth Engine access has its own commercial terms.
- **EOG VNL** (verify the product is on the CC BY list), **World Bank Light Every Night** (ODbL) and **NASA VIIRS** (CC BY 4.0): night-light occupancy proxies.

## Rejected / not usable

- **Amazonia-1 (WFI)**: Free catalogue; no explicit open licence text found on pages fetched. Coarse and out of area; licence not stated
- **SAOCOM-1A/1B**: Portal lists SAOCOM under 'Productos Licenciados' (contact CONAE); a 'free-access SAOCOM products' search exists but terms of use did not open (popup blocked) - not confirmed commercial-OK. Licensed product; free subset terms unverified
- **PeruSAT-1**: No public open-data licence located.
- **FASat-Charlie / FASat-Delta**: No open-data programme.
- **Mexico (no national EO open-data satellite)**: INEGI redistributes third-party imagery (e.g. SPOT/Landsat mosaics); no own open satellite feed.
- **NigeriaSat-2 / NigeriaSat-X**: Not open.
- **SANSA (South Africa) data**: SPOT data under licence; no own open VHR feed.
- **EgyptSat-A / MisrSat-2**: Not open.
- **Maxar / Vantor Open Data Program**: Registry: 'Creative Commons Attribution Non Commercial 4.0'; maxar.com/open-data: CC BY-NC 4.0.
- **Planet Disaster Data**: Page: data 'subject to a CC-BY-NC license, unless otherwise stated'.
- **Planet NICFI tropical basemaps**: NICFI licence was non-commercial-restricted; nicfi page now redirects to planet.com/impact (programme appears closed). Derived cTrees Amazon canopy height on AWS inherits NICFI licence..
- **BlackSky open data**: No open-data programme found.
- **Airbus OneAtlas samples**: Samples are for evaluation under Airbus terms, not an open licence.
- **Spire data**: Commercial licence.
- **Satellite Vu (thermal)**: No open licence found.
- **Pixxel (hyperspectral)**: No open licence found.
- **cTrees Amazon canopy height**: Registry: follows NICFI licensing agreement.

## Needs verification

- **RADARSAT Constellation Mission imagery (scene level)**: Open-data access for RCM scenes is restricted/partial; public open.canada.ca records found are only derived products (mosaic, sea-ice motion, acquisition plans). CSA site refused automated access..
- **Meta HRSL population (hrsl-cogs)**: CC BY 4.0 per Data for Good HRSL (same bucket); verify per-file README.
- **EOG VIIRS Nighttime Lights (VNL annual/monthly)**: Page: 'Many of the VIIRS Nighttime Lights data are available under Creative Commons Attribution 4.0' - per-product list in EOG_products_CC_License.pdf. Confirm the specific product is on the CC BY list

## Government satellites (non-US Americas / other): takeaway
Only Canada publishes satellite data under a clear commercial-OK licence (Open Government Licence - Canada): the RADARSAT-1 archive on AWS, the RADARSAT-1 US mosaic, the RCM land mosaic (Canada only) and SCISAT. Brazil's CBERS-4/4A is on AWS under CC BY-SA 3.0 but covers essentially no US territory. Argentina (SAOCOM), Peru, Chile, Mexico, Nigeria, South Africa and Egypt have no open, commercial-OK feed that helps US property work.

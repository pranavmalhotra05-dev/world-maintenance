# Earth data beyond LiDAR: green-licence audit (2026-11-25)

Seven research lanes. Each licence was checked on the provider's own page (or its AWS Open Data Registry entry).
**Green rule:** only public domain, CC0, CC BY, CC BY-SA, ODbL, OGL, CDLA-Permissive, Etalab or Copernicus free/full/open.
Anything non-commercial, needing advance notice or negotiation, or with unclear terms is excluded.

Common licences:
- **NASA** mission data is CC0 ([policy](https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance)).
  Citation is requested, and nothing may imply that NASA endorses the product.
- **USGS, NOAA, EPA, USDA, USFS, USFWS, CDC and FEMA** data are US public domain.
- **Copernicus** Sentinel data is free, full and open. Credit: "Contains modified Copernicus Sentinel data [year]".
  CAMS data has been CC BY 4.0 since July 2025.
- **EUMETSAT** "Core" data (all SAF products, Jason-3, IASI) is CC BY 4.0. Sentinel-6 is open.

## Top picks for the globe (USA first)

| Layer | Dataset | Res. | Licence |
|---|---|---|---|
| Ground sinking or rising (mm) | NASA OPERA DISP-S1 InSAR | 30 m, N. America | CC BY 4.0 |
| New construction and land clearing | NASA OPERA DIST | 30 m | CC0 |
| Real laser points on top of 3DEP | ICESat-2 ATL03/08, GEDI L2A/B | tracks / 25 m shots | CC0 |
| Tree height | Meta/WRI Canopy Height | ~1 m global | CC BY 4.0 |
| Building height | Microsoft ML Footprints, Overture | per building | CDLA-P 2.0 / ODbL |
| Underground "X-ray" of aquifers | USGS Earth MRI / MAP airborne EM resistivity | lines ~1–6 km, to ~300 m deep | PD |
| Groundwater | USGS NWIS wells; GRACE-FO mascons | points; ~300 km | PD; CC0 |
| Rock radiation, radon | USGS NURE K/U/Th gamma; EPA radon zones; USGS radon potential | ~1 km; county | PD |
| Minerals and methane | NASA EMIT; AVIRIS-NG; ASTER; USGS mineral maps | 1–60 m | CC0 / PD |
| Heat (roofs, heat islands) | Landsat surface temperature; ECOSTRESS | 30–100 m; 70 m | PD; CC0 |
| Hourly air quality | NASA TEMPO; Sentinel-5P; EPA AQS | ~2×5 km hourly | CC0; Copernicus; PD |
| Soil chemistry | SSURGO/gNATSGO*; SoilGrids; USGS soil geochemistry | 10 m–250 m | PD; CC BY 4.0; PD |
| Solar and UV | NREL NSRDB; EPA UV index | 2–4 km; ZIP | CC BY 4.0; PD |
| Lightning density | GOES GLM | ~8 km | NOAA open |
| Fire proximity, live IR | NASA FIRMS; GOES ABI | 375 m; 2 km every 5–10 min | CC0; NOAA open |
| Flood and water | FEMA NFHL; JRC Global Surface Water; SWOT; CYGNSS | parcel; 30 m | PD; open; CC0 |
| Vegetation and biology | HLS, Sentinel-2, MODIS NDVI; Hansen forest change; NLCD tree canopy | 10–30 m | CC0 / Copernicus / CC BY 4.0 / PD |
| Habitat and constraints | NWI wetlands; Critical Habitat; GAP; PAD-US; LANDFIRE fuels; Cropland Data Layer | 30 m / polygons | PD |
| Health and pests (aggregates) | CDC PLACES; CDC ArboNET and ticks; USFS insect damage; APHIS quarantines | tract / county | PD |
| Species sightings | GBIF and iNaturalist, **filtered to CC0/CC BY only** | points | per record |
| Radar images (any weather) | Sentinel-1; NISAR; Capella and ICEYE open samples | 0.5–20 m | Copernicus; CC0; CC BY 4.0 |
| Optical images | Sentinel-2; Landsat; Satellogic EarthView 1 m (US tiles unconfirmed) | 1–30 m | Copernicus; PD; CC BY 4.0 |
| Population and built-up area | JRC GHSL; WorldPop; Meta HRSL | 10 m–1 km | CC BY 4.0 |
| Night lights | VIIRS Black Marble | 500 m daily | CC0 |
| Geology and seismic | USGS SGMC, Qfaults, Vs30; EarthScope GNSS stations | various | PD; CC BY 4.0 |

\* SSURGO: a US federal work, but no current page states its licence plainly. Confirm before relying on it.

## Worldwide expansion (green)
- **Elevation:** Copernicus DEM GLO-30/90, NASADEM, ASTER GDEM v3, ArcticDEM/REMA (CC BY).
- **National LiDAR:**
  - England 1 m (OGL)
  - Netherlands AHN 0.5 m (CC0)
  - Denmark (free terms, commercial OK)
  - Finland 0.5 points/m² (CC BY)
  - Spain PNOA (CC BY)
  - Canada HRDEM (OGL-C)
  - New Zealand (CC BY)
  - Australian ACT (CC BY; check the other Australian sets one by one)
- **Ground motion:** Europe EGMS (CLMS terms, commercial OK).
- **Geophysics:**
  - Australia Radmap, magnetic and gravity (CC BY)
  - Canada aeromagnetic (OGL-C)
  - Ireland Tellus (CC BY; includes EM)
  - Finland GTK (CC BY)
  - EMAG2 global magnetic (PD)
  - XGM2019e / EIGEN-6C4 gravity (CC BY; includes GOCE)
- **Radiation and air:** EU natural-radiation atlas maps (CC BY), UK radon atlas (OGL), CAMS UV and pollen (CC BY), Safecast (CC0).
- **Imagery and land cover:**
  - ESA WorldCover, Dynamic World, Digital Earth Australia (CC BY)
  - Google Open Buildings 2.5D (CC BY / ODbL; Global South)
  - CBERS on AWS (CC BY-SA)
  - SPOT World Heritage archive 1986–2015 (Etalab 2.0)
- **Weather and climate scale (context only):**
  - GPM IMERG rain, SMAP, GMI, SSMIS CDR
  - Jason-3 and Sentinel-6 sea level, ASCAT Level 2 winds and soil moisture
  - AIRS, CrIS and IASI sounders; COSMIC-2 (CC BY)
  - CALIPSO and CATS lidar, CloudSat, PACE polarimetry, CERES, OCO-2/3, OMI UV

## Excluded (and why)
- **Non-commercial:**
  - Maxar Open Data, Vantor, Planet NICFI
  - FABDEM, Map of Life, eBird, IUCN ranges
  - Carbon Mapper, OpenSky ADS-B
  - GBIF/iNaturalist CC BY-NC records
- **Advance notice, negotiation or science-only:**
  - JAXA data (AW3D30, PALSAR, AMSR2, GCOM)
  - DLR TanDEM-X, EnMAP (raw data can't be redistributed), PRISMA, DESIS
  - GOSAT, MethaneSAT
  - Japan GSI DEM, Canada RCM
- **ESA terms with conditions** (registration, no rehosting of raw data, IP grant-back clause C.5), so grey under the green rule:
  - SMOS, GOCE Level 2, Aeolus, EarthCARE, CryoSat-2, BIOMASS, Swarm
  - Possible later only as displayed tiles, after a legal read of C.5
- **Conflicting or missing licence:**
  - Umbra (the AWS listing says CC BY, but Umbra's own linked page says non-commercial)
  - Himawari on AWS, POLDER, WGM2012, EDDMapS, TROPOSIF, the Earth Microbiome Project
  - NMDB, COSMOS-USA, Global Solar Atlas, Theia L2A, ISRO Bhuvan, KOMPSAT, Gaofen
- **Paid or restricted:** NLDN lightning, EarthScope real-time streams, BGS licensed products, satellite AIS/ADS-B/RF, EURDEP raw data, Finland's dense laser product.
- **Not open as a whole:** the AirNow real-time feed (use AQS instead), OpenAQ as a single source, state well logs.

## Physics notes
- **Gamma, X-ray and far-UV can't see the ground from orbit**: the atmosphere absorbs them.
  Earth gamma data comes from aircraft (NURE) or ground sensors. X-ray fluorescence is lab or handheld only.
- **Cosmic-ray neutrons** do measure soil water, but only from ground stations (COSMOS-UK, OGL).
- **Cosmic-ray muon tomography:** only one demo site has open data. No regional dataset exists.
- **Nothing from orbit sees more than a few metres into the ground.**
  Deep structure comes from gravity (GRACE, GOCE models), magnetics, and airborne EM and gamma surveys.

# US government Earth-observation satellites: open data for property intelligence (SAT-1)

As of 2026-11-24. 38 records in `satellites_us.json`. Commercial use: **yes 36**, **verify 1**, **no 1**.

## Licence baseline

- **NASA**: Earthdata's data-use guidance says that, unless content is marked otherwise, data from NASA-led missions are CC0, with no restrictions on use (citation strongly requested). The NASA data policy has no exclusive-access period and restricts partner-supplied products only as far as an MOU requires. https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance
- **USGS**: Landsat and USGS-produced data are US public domain, with no restrictions on use or redistribution (a source credit is requested). https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits
- **NOAA** (NODD on AWS/GCP/Azure): open to the public and usable as desired. NOAA asks for attribution and no implied endorsement. https://registry.opendata.aws/noaa-goes/
- **Copernicus inputs** (HLS Sentinel-2, OPERA Sentinel-1 products, Sentinel-6): free, full and open, including commercial use, with attribution.
- **Login**: a free NASA Earthdata Login is needed to download most NASA files. CMR/STAC search works without one. NOAA buckets are anonymous. AWS Landsat is requester-pays, so use the USGS or Planetary Computer routes to avoid egress charges.

## Summary table

| Mission | Status | Sensor | Resolution (m) | Revisit | Commercial OK | Tested |
|---|---|---|---|---|---|---|
| Landsat 9 (OLI-2/TIRS-2) | active | multispectral, thermal | 30 (VNIR/SWIR), 15 pan, 100 thermal (delivered at 30) | 16 days per satellite; 8 days combined with Landsat 8 | yes | LandsatLook STAC search over Dallas returned LC09_L2SP_027037_20261115 (2026-11-15, LANDSAT_9). |
| Landsat 8 (OLI/TIRS) | active | multispectral, thermal | 30, 15 pan, 100 thermal | 16 days (8 with Landsat 9) | yes | Same STAC endpoint (landsat-c2l2-sr) tested. |
| Landsat 7 (ETM+) | retired (science data ended 2024-01-19; decommissioned 2025-06-04) | multispectral, thermal | 30, 15 pan, 60 thermal | 16 days (historical) | yes | STAC query platform=LANDSAT_7 newest item LE07_L2SP_135047_20240119; USGS page confirms decommission 2025-06-04. |
| Landsat Next / Landsat 10 | planned | multispectral, thermal | USGS page now titles the mission 'Landsat 10' (restructured from the 3-satellite Landsat Next concept); continuity-class spec | TBD | yes | USGS page (fetched 2026-11-24): 'Landsat 10 is expected to launch in 2031'. |
| Harmonized Landsat Sentinel-2 (HLS v2.0) | active | multispectral | 30 | ~2-3 days combined | yes | CMR granule search over Dallas returned HLS.L30.T14SQB.2026320T165612.v2.0 (2026-11-16). |
| MODIS on Terra | active until 2026-12-01 (NASA alert: new Terra observations cease 1 Dec 2026; archive stays) | multispectral, thermal | 250 / 500 / 1000 | daily | yes | CMR latest MOD09GA 2026-11-22; Earthdata alert 'New Terra Observations to Cease on December 1, 2026' (issued 2026-11-17). |
| MODIS on Aqua | active (drifting orbit; NASA steering users to VIIRS) | multispectral, thermal | 250 / 500 / 1000 | daily | yes | CMR latest MYD09GA 2026-11-22. |
| Terra ASTER | active until 2026-12-01 with Terra (SWIR failed 2008) | multispectral, thermal | 15 VNIR, 90 TIR | tasked, ~16 days | verify | Planetary Computer collection list includes 'aster-l1t'. |
| VIIRS on Suomi NPP | retired (new science data ended 2026-11-02 15:40 UTC per NESDIS/NASA; archive remains) | multispectral, thermal | 375 (I) / 750 (M, Day/Night Band) | daily (archive) | yes | CMR latest VNP09GA / VNP46A2 = 2026-11-02, matching the Earthdata cessation alert. |
| VIIRS on NOAA-20 (JPSS-1) and NOAA-21 (JPSS-2) | active | multispectral, thermal | 375 / 750 | twice daily each | yes | CMR VJ146A1 latest 2026-11-19; anonymous listing of noaa-nesdis-n21-pds returned ATMS-SDR/ prefixes. |
| NASA Black Marble (night lights) | active (continues on NOAA-20/21; S-NPP series ended 2026-11-02) | optical (low-light) | ~500 (15 arc-sec) | daily (A1/A2), monthly (A3), annual (A4) | yes | CMR found VNP46A2 v2 (2012-), VJ146A2 v2 (2018-), VJ146A2_NRT. |
| GOES-East (GOES-19) and GOES-West (GOES-18) ABI | active | multispectral, thermal | 500 / 1000 / 2000 | CONUS 5 min, mesoscale 1 min, full disk 10 min | yes | Anonymous S3 listing noaa-goes19 ABI-L2-MCMIPC/2026/328/ OK; ABI-Flood-Day-TIF GeoTIFFs found. |
| GOES GLM (Geostationary Lightning Mapper) | active | optical (lightning) | ~8000-14000 | continuous, 20 s files | yes | S3 GLM-L2-LCFA/2026/328/ OK for goes18 & goes19; Planetary Computer newest goes-glm item OR_GLM-L2-LCFA_G19_s20263280840000. |
| JPSS programme (NOAA-20/21; JPSS-3/4) ATMS, CrIS, OMPS, CERES | active; JPSS-4 (NOAA-22) launch readiness 2027 per NESDIS | microwave, atmospheric, multispectral | ATMS 16-75 km; CrIS 14 km | twice daily | yes | noaa-jpss bucket root listing returned BHP_PCT/ etc. |
| DSCOVR EPIC | active | multispectral, atmospheric | ~10000 | ~13-22 images/day | yes | EPIC API available dates end 2026-11-22; CMR EPIC L1B latest 2026-11-22. |
| ICESat-2 (ATLAS photon LiDAR) | active | LiDAR | ~11-17 m footprints every 0.7 m along track; ATL08 100 m segments (20 m sub-segments) | 91-day repeat; sparse tracks | yes | CMR ATL08 v007 latest 2026-09-24. |
| GEDI (waveform LiDAR on ISS) | active (stored Mar 2023-Apr 2024 then resumed; lifetime tied to ISS) | LiDAR | 25 m footprints, 60 m along-track, tracks ~600 m apart | sparse sampling 51.6N-51.6S | yes | CMR GEDI02_A V003 latest 2026-06-10 (long processing lag). |
| SMAP (soil moisture) | active (radiometer; radar failed 2015) | microwave | 9000 enhanced / 36000 | 2-3 days | yes | CMR SPL3SMP_E latest 2026-11-22. |
| GRACE-FO (gravity / groundwater) | active | microwave (gravimetry) | ~300000 (mascons) | monthly | yes | CMR JPL mascon grid covers 2002-04 to 2026-09. |
| ECOSTRESS (thermal on ISS) | active | thermal | 70 | irregular 1-5 days, varying time of day | yes | CMR ECO_L2T_LSTE latest 2026-11-20. |
| EMIT (imaging spectrometer on ISS) | active (extended) | hyperspectral | 60 | irregular | yes | CMR EMITL2ARFL V002 latest 2026-11-22 (V001 collections end 2026-08-31, superseded). |
| SWOT (Surface Water and Ocean Topography) | active | SAR (Ka-band interferometric altimeter) | 100 / 250 rasters | 21-day repeat | yes | CMR SWOT_L2_HR_Raster_D latest 2026-11-21. |
| TEMPO (geostationary air quality) | active | atmospheric, hyperspectral (UV-vis) | ~2000 x 4750 | hourly daytime | yes | CMR TEMPO_NO2_L3 V04 latest 2026-11-24T17:25Z (same day). |
| PACE (OCI, HARP2, SPEXone) | active | hyperspectral, atmospheric | ~1000 | 1-2 days | yes | CMR PACE_OCI_L2_SFREFL latest 2026-10-01; 64 PACE_OCI collections. |
| NISAR (NASA-ISRO L- and S-band SAR) | active (science phase; validated L-band L0-L2 products released 2026-11-06 for acquisitions from 2026-10-21; provisional/beta products from Sep-Oct 2025) | SAR | ~3-10 (SLC); GCOV/GUNW coarser posting | 12 days | yes | CMR NISAR_L2_GCOV_V1 latest 2026-11-22; NISAR_L2_GUNW_V1 latest 2026-11-09 (25,367 granules). |
| OPERA DSWx (Dynamic Surface Water Extent) | active | multispectral, SAR | 30 | 2-3 days (HLS) / 6-12 days (S1) | yes | CMR DSWx-HLS latest 2026-11-19. |
| OPERA DIST-ALERT / DIST-ANN (surface disturbance) | active | multispectral | 30 | 2-3 days | yes | CMR DIST-ALERT latest 2026-11-19. |
| OPERA RTC-S1 (terrain-corrected Sentinel-1 backscatter) | active | SAR | 30 | 6-12 days | yes | CMR RTC-S1 latest 2026-11-24T16:07Z (Sentinel-1C). |
| OPERA CSLC-S1 and DISP-S1 (InSAR ground displacement) | active | SAR | CSLC ~5x10; DISP 30 | 12-day time series back to 2016 | yes | CMR DISP-S1 newest 2026-10-25/10-31 pair; Houston bbox returned 1,233 granules. |
| Sentinel-6 Michael Freilich (6A) and Sentinel-6B | active | SAR (altimeter), microwave | ~300 along-track | 10 days | yes | CMR S6A L2 LR NRT latest 2026-11-24; NOAA OSPO lists Sentinel-6A and 6B services. |
| Aqua AIRS | active | atmospheric | 13500 | twice daily | yes | CMR AIRS2RET v7.0 latest 2026-11-24. |
| OCO-2 | active (anomaly 28 Jul-13 Aug 2026; budget-termination risk - verify) | atmospheric | ~1300 x 2250 | 16 days | yes | CMR OCO2 Lite 11.2r latest 2026-03-31; Earthdata OCO-2 anomaly alert Sep 2026. |
| OCO-3 (on ISS) | active | atmospheric | <4000 | irregular | yes | CMR OCO3 Lite latest 2026-09-30. |
| NASA FIRMS (MODIS/VIIRS active fire) | active | thermal | 375 (VIIRS) / 1000 (MODIS) | several per day | yes | FIRMS API landing page HTTP 200. Note: Terra/S-NPP feeds end 2026; NOAA-20/21 continue. |
| NASA GIBS / Worldview tiles | active | optical (visualisation) | 15-2000 by layer | daily layers | yes | GIBS WMTS GetCapabilities HTTP 200 (5.3 MB). |
| NOAA ocean/weather open sets (Jason-3, COSMIC-2, CoastWatch, MRMS, HRRR, NEXRAD) | active | microwave, atmospheric, radar (ground-based) | MRMS 1000; HRRR 3000 | 2 min (MRMS) to 10 days | yes | noaa-mrms-pds CONUS/ listing OK; noaa-hrrr-bdp-pds OK; noaa-nexrad-level2 anonymous ListBucket 403 (check registry for current bucket). |
| USGS 3DEP aerial LiDAR (reference, not satellite) | active | LiDAR | QL2 >= 2 pts/m2 (often 8+); 1 m DEM | one-off / multi-year | yes | Planetary Computer 3dep-lidar-copc search over downtown Dallas returned USGS_LPC_TX_Pecos_Dallas_2018_D19_14SQB0730. |
| NASA CSDA (Commercial Satellite Data Acquisition: Planet, Maxar, Airbus, ICEYE, Capella, BlackSky ...) | active | optical, SAR, hyperspectral | 0.3-5 | daily+ | no | EULA page fetched 2026-11-24; no data access attempted. |

## Top 10 for property intelligence

1. **USGS 3DEP aerial LiDAR**: not a satellite, but it is the only free source dense enough for per-roof geometry (pitch, area, height, tree overhang). Public domain, and COPC is on Planetary Computer. Keep every point.
2. **OPERA DISP-S1 (+ CSLC-S1)**: InSAR displacement time series at 30 m across North America from 2016. Gives neighbourhood subsidence and heave flags for foundation risk (Houston, Phoenix, Central Valley, sinkhole belts). NASA product, CC0.
3. **NISAR L-band (GUNW/GCOV)**: validated products released 2026-11-06. L-band keeps coherence through vegetation, so it is the next-generation source for subsidence, sinkholes and flood mapping. L-band via ASF is open.
4. **HLS v2.0 (Landsat + Sentinel-2)**: 30 m every 2-3 days. The best free optical time series for lawn/pool/vegetation change and before/after storm or fire comparison.
5. **OPERA DIST-ALERT**: ready-made 30 m vegetation-loss alerts that flag clearing, new construction, burn and storm damage, so we don't have to build our own change detection.
6. **Landsat 8/9 Collection 2 (SR + ST)**: 30 m surface temperature for heat islands, plus a 50-year archive for "what changed on this block".
7. **ECOSTRESS LST (70 m)**: finer thermal at varying times of day, good for block-level heat and irrigated vs dry yards.
8. **VIIRS NOAA-20/21 + Black Marble**: night-light growth as a development/occupancy proxy, daily outage and recovery after hurricanes, and VIIRS active fire through FIRMS.
9. **GOES-19/18 ABI + GLM (plus NOAA MRMS hail, not a satellite)**: minute-scale fire, smoke and lightning, and ABI flood composites. Pair them with MRMS MESH hail swaths for roof-damage lead timing.
10. **OPERA DSWx + RTC-S1**: 30 m flood extent (optical and radar, including through clouds) for post-storm flood footprints and recurring-ponding risk. TEMPO hourly NO2 is a close 11th for neighbourhood air quality.

## Status changes to note (as of 2026-11-24)
- **Suomi NPP**: new science data ended 2026-11-02 (NESDIS/NASA). Switch to the NOAA-20/21 (VJ1*/VJ2*) equivalents.
- **Terra** (MODIS, ASTER, MISR, CERES, MOPITT): new observations cease 2026-12-01 (Earthdata alert issued 2026-11-17). Aqua MODIS continues but is drifting. Plan on VIIRS, HLS and Landsat.
- **Landsat 7**: imaging ended 2024-01-19 and the satellite was decommissioned on 2025-06-04. **Landsat Next** is now presented by USGS as **Landsat 10, launch expected 2031**.
- **NISAR**: launched 2025-07-30. Validated L-band products came out 2026-11-06, covering acquisitions from 2026-10-21.
- **OCO-2** had an instrument anomaly from 28 Jul to 13 Aug 2026. The OCO missions are irrelevant for parcels anyway.
- **GEDI**: the newest V003 granule in CMR is from 2026-06; processing lags by months, and its future depends on the ISS.

## Not open / caution
- **NASA CSDA commercial buy (Planet, Maxar, Airbus, ICEYE, Capella ...): NOT commercial.** EULAs are for non-commercial (US-government-funded science) use, and only by verified authorized users. Don't use it.
- **ASTER (Japan METI instrument)**: free since 2016, but the partner terms were not re-verified, so it is marked `verify`. Low priority anyway.
- **NISAR S-band** (ISRO Bhoonidhi): governed by ISRO terms, not verified. Use the NASA L-band copy.
- **AWS `usgs-landsat`** is requester-pays: the data is free but egress is not. NOAA `noaa-nexrad-level2` refused anonymous listing (403) in this test; check the registry for the current bucket.
- Copernicus-derived products (HLS S30, OPERA S1, Sentinel-6) need a Copernicus attribution line.

## Method
Every test was run on 2026-11-24 with Python requests against public endpoints: NASA CMR collection/granule search, the USGS LandsatLook STAC, the Planetary Computer STAC, anonymous S3 listings of NOAA NODD buckets, the EPIC API and GIBS GetCapabilities. Status claims come from the official USGS, NASA Earthdata alert, NESDIS and NISAR pages linked in the JSON. No credentials were used.

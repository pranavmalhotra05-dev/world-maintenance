# Imagery lane (I1): free, commercial-use-OK imagery per 3D block

Script: `usa/imagery.py` (run `.venv\Scripts\python -u -W ignore -m usa.imagery [slug ...]`).
Outputs: `web/usa/data/imagery_sources.json` (catalogue), `meta.imagery` + `sat/*.jpg` per block, cache in `usa/_cache/imagery/`.

Rule applied (owner's stricter rule, 2026-11-24): only imagery whose licence/terms page EXPLICITLY allows commercial use
and redistribution - public domain, CC0, CC BY, CC BY-SA, or the Copernicus open policy. Disclaimer-only sources and
sources with unstated terms are rejected with the reason "terms not explicit" and never clipped into `sat/`, even when
free (this removes Ohio OSIP, Missouri MSDIS, Hennepin/Ramsey county layers, CAP layers on NOAA viewers). OpenAerialMap
images qualify only when the image itself carries CC0 / CC BY / CC BY-SA. Licence quote + URL is recorded per source
and per image where licences vary.

Borderline kept: NOAA ERI - the NODD terms say the data "can be used as desired" (any use, incl. redistribution;
US federal work). Landsat - USGS: "it can be used or redistributed as desired".

## Sources used

| Source | Kind | Res. | Revisit / cadence | Licence (quote, URL) | Access |
|---|---|---|---|---|---|
| Sentinel-2 L2A | satellite | 10 m | 5 d nominal, ~2-3 d at tile overlaps | "users shall have a free, full and open access to Copernicus Sentinel Data" - EU legal notice (reproduction, distribution, modification allowed; credit "Contains modified Copernicus Sentinel data <year>") https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice | Planetary Computer STAC `sentinel-2-l2a`, anonymous SAS, no key |
| Landsat 8/9 C2 L2 | satellite | 30 m | 8 d combined | "There are no restrictions on Landsat data downloaded from the USGS" https://www.usgs.gov/faqs/are-there-any-restrictions-use-or-redistribution-landsat-data | PC STAC `landsat-c2-l2` |
| NASA HLS v2 S30/L30 | satellite | 30 m | 2-3 d combined | NASA-led mission data "licensed as Creative Commons Zero (CC0)" https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance | PC STAC `hls2-s30`, `hls2-l30` (keyless). LP DAAC's own CMR-STAC/cloud needs a free Earthdata login - not needed, not used |
| Sentinel-1 RTC | satellite radar | 10 m | 12 d (S1A), ~6 d once S1C in the mix | PC collection licence CC-BY-4.0 https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc | PC STAC `sentinel-1-rtc` (metadata only) |
| NAIP | aerial | 0.6 m (0.3 m from 2022 in some states) | 2-3 yr | "Public Domain with Attribution" https://registry.opendata.aws/naip/ | PC STAC `naip` (catalogue only; another lane renders `img/<year>.jpg`) |
| MnGeo / Met Council 7-county leaf-off `met25` | aerial | 0.3 m | ~5 yr (2016, 2020, 2025) | "This dataset is public domain under the Minnesota Government Data Practices Act" https://mnatlas.org/metadata/2025_7_county.html | WMS https://imageserver.gisdata.mn.gov/cgi-bin/wms layer `met25` |
| TxGIO StratMap 2020 CapArea | aerial | 0.076 m | regional rotation | CC0 1.0 (TxGIO DataHub collection licence) https://api.tnris.org/api/v1/collections | ArcGIS ImageServer `StratMap/StratMap20_NCCIR_CapArea_McLennan` (bandIds=0,1,2) |
| TxGIO NAIP 2022 60 cm | aerial | 0.6 m | NAIP | CC0 1.0 (TxGIO) | ImageServer `NAIP/NAIP22_NCCIR_60cm` (Dallas: no newer open sub-metre found) |
| Colorado DRAPP 2022 (State of Colorado public imagery on AWS) | aerial | 0.076 m | 2 yr (2024 not yet in the open bucket) | Registry "License: CC0 1.0" https://registry.opendata.aws/colorado-imagery/ | non-COG GeoTIFF tiles on S3 (~450 MB each) read with HTTP byte ranges (uncompressed strips), tile index `gis.colorado.gov/.../Public_Imagery_Layer_Index/MapServer/6` |
| NOAA NGS Emergency Response Imagery | disaster | ~0.25 m | event-driven (1-5 d after the event) | "NOAA data disseminated through NODD are open to the public and can be used as desired" https://registry.opendata.aws/noaa-eri/ | per event `storms.ngs.noaa.gov/storms/<event>/services/index.json` (bounds + XYZ tiles); older Leaflet viewers: layer ids parsed from `js/app.js`, footprint probed via z8 tiles |
| OpenAerialMap | drone/aerial | per image | ad hoc | per image; kept only CC BY 4.0 / CC BY-SA / CC0 / public domain, recorded per image with provider | `https://api.openaerialmap.org/meta?bbox=` + per-image TMS |

## Method

* **Latest clear Sentinel-2**: newest scenes (scene cloud <= 60 %) are read over the block bbox + 300 m; the SCL mask must
  show >= 97 % valid non-cloud pixels (classes 0,1,3,8,9,10 rejected). First passing scene -> `sat/s2_<date>_rgb.jpg`
  (true colour, reflectance 0-0.25 stretch, gamma 1.6) and `sat/s2_<date>_ndvi.jpg` (brown->green ramp), both 640 px
  wide with the 3D block outlined. Processing baseline >= 04.00 offset (-1000 DN) is removed.
* **24-month series**: per calendar month the clearest scene (SCL) over block + 300 m; mean NDVI and mean visible
  brightness ((B02+B03+B04)/3 reflectance) of clear pixels; months with < 60 % clear pixels are null.
  `sentinel2_trend` compares matched months year-over-year (removes seasonality): NDVI change > +/-0.03 => greening /
  browning; brightness change > +/-0.01 => brightening (dust, bare soil, new pale roofs/paving) / darkening.
* **Revisit**: distinct acquisition days in the last 12 months per collection and the median gap; `optical_combined`
  is the union of S2 + Landsat + HLS days (HLS days duplicate S2/Landsat, the union removes that).
* **Orthos**: block bbox + 50 m, up to 1024 px, block outlined; only services with an explicit open licence.
* **NOAA**: every event index on storms.ngs.noaa.gov; layers within 25 km of the block are listed; if a layer's footprint
  covers the block, tiles at z19->z17 are mosaicked (NOAA serves `clear.png` where a flight strip has no data, those
  are treated as gaps). Layers attributed MAXAR (Maxar Open Data = CC BY-NC 4.0) or CAP (licence not stated) are rejected.
* All jpgs < 1.5 MB; stale `sat/*.jpg` from earlier runs are removed and dropped from `meta.files`.

## Rejected

See `rejected` in `web/usa/data/imagery_sources.json`: Planet NICFI (non-commercial), Maxar Open Data (CC BY-NC),
Google / Esri World Imagery / Bing / Mapbox (proprietary), Nearmap (incl. CAGIS Hamilton Co), EagleView/Pictometry
(incl. Maricopa Assessor aerials, INCOG Tulsa), Colorado statewide Vexcel imagery, City of Huntsville 2026 aerials
(copyrighted, repackaging prohibited), Jefferson Co AL (login), Ohio OSIP and Missouri MSDIS 6-inch (they work, but have
no explicit licence - need written confirmation before use), Hennepin hen22 / Ramsey rams20 (licence not stated),
Oklahoma 2021 statewide (no endpoint found), NCTCOG (members only).

## Per-block results (run 2026-11-24, all 16 blocks)

Freshness: every block has a cloud-free Sentinel-2 view 1-20 days old. Optical satellites (S2 + Landsat 8/9 + HLS)
imaged each block on 120-209 days in the last 12 months, so the median gap is 2 days (3 days in Columbus). Sentinel-1
radar passes every 4-12 days. Landsat 8/9 and HLS latest scenes are 2026-11-05 to 2026-11-19.

| Block | Clear S2 (age) | Sub-metre open clip in sat/ | NAIP years (catalogue) | 24-mo series / YoY read | NOAA ERI | OAM (open, 25 km) |
|---|---|---|---|---|---|---|
| austin-tx | 2026-11-04 (20 d) | TxGIO StratMap 2020, 7.6 cm, CC0 | 2012-2022 | 24/24, stable | 5 layers 2025-07 (Hill Country floods); 20250717a footprint covers block but tiles are a strip gap | 3, none over block |
| birmingham-al | 2026-11-14 (10 d) | none (Jefferson Co login-only) | 2011-2023 | 24/24, stable | none | 0 |
| boulder-co | 2026-11-20 (4 d) | DRAPP 2022, 7.6 cm, CC0 | 2011-2023 | 24/24, browning (NDVI -0.031 YoY) | none | 880 (mostly DRAPP 2016 tiles), none over block; 6 Maxar CC BY-NC rejected |
| cincinnati-oh | 2026-11-22 (2 d) | none (CAGIS = Nearmap; OSIP terms not explicit) | 2011-2023 | 24/24, brightening (+0.021) | none | 4, none over block |
| columbus-oh | 2026-11-19 (5 d) | none (OSIP terms not explicit) | 2011-2023 | 21/24, greening (+0.071), darkening | none | 12, none over block |
| columbus-oz | 2026-11-19 (5 d) | none | 2011-2023 | 24/24, darkening | none | 10, none over block |
| dallas-tx | 2026-11-16 (8 d) | NAIP 2022 60 cm via TxGIO, CC0 | 2012-2022 | 23/24, browning (-0.033) | none | 1 |
| denver-co | 2026-11-23 (1 d) | DRAPP 2022, 7.6 cm, CC0 | 2011-2023 | 24/24, stable | none | 1900 (DRAPP 2016 uploads), none over block |
| huntsville-al | 2026-11-19 (5 d) | none (City of Huntsville copyrighted) | 2011-2023 | 24/24, stable | none | 0 |
| kansas-city-mo | 2026-11-21 (3 d) | none (MSDIS terms not explicit) | 2012-2023 (11 yrs) | 24/24, greening (+0.033) | none | 5 |
| minneapolis-mn | 2026-11-19 (5 d) | Met Council met25 Apr 2025, 30 cm, public domain | 2010-2023 | 23/24, stable | none | 5 |
| minneapolis-oz | 2026-11-19 (5 d) | met25 2025 | 2010-2023 | 23/24, brightening | none | 4 |
| saint-paul-mn | 2026-11-19 (5 d) | met25 2025 | 2010-2023 | 23/24, brightening | none | 5 |
| phoenix-az | 2026-11-19 (5 d) | none (Maricopa = EagleView) | 2010-2023 | 24/24, stable | none | 1 |
| scottsdale-az | 2026-11-19 (5 d) | none | 2010-2023 | 24/24, stable | none | 1 |
| tulsa-ok | 2026-11-19 (5 d) | none (INCOG = EagleView) | 2010-2023 | 24/24, stable | none | 1 (33 Maxar CC BY-NC rejected) |

No OpenAerialMap image and no NOAA ERI layer actually covers any block, so `drone_images` and `disaster_events` contain
metadata only. Re-running the script picks up new NOAA events and OAM uploads automatically.

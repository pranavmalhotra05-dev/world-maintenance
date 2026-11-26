# InSAR ground motion: NASA/JPL OPERA DISP-S1

Researched 2026-11-26. Built by `usa/build_insar.py` -> `web/app/data/insar.json`.

## Product
- OPERA L3 DISP-S1 v1 (Surface Displacement from Sentinel-1), 30 m posting, North America, 2016-07 to present.
- CMR collection `OPERA_L3_DISP-S1_V1`, concept id `C3294057315-ASF`, DOI 10.5067/SNWG/OPL3DISPS1-V1, archived at ASF DAAC.
- Measures displacement in radar **line-of-sight** (not vertical). Ascending and descending frames see different LOS geometry.
- Granules are per-epoch netCDF/HDF5 (`...F<frame>_VV_<ref>_<sec>_v1.0_<prod>.nc`) plus a per-frame
  `OPERA_L3_DISP-S1_STACK_V1/.../short_wavelength_displacement.zarr.json.gz` virtual stack.

## Licence (green)
- Earthdata Data Use and Citation Guidance
  (https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance):
  "Unless the content is marked with a use restriction or license, data provided from a NASA-led mission are licensed as Creative Commons Zero (CC0)."
- Earthdata catalog page for this collection (https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1):
  "This dataset is openly shared, without restriction, in accordance with the EOSDIS Data Use and Citation Guidance."
- CMR UMM `UseConstraints` is empty (no restriction marked) -> CC0 applies.
- The collection summary says it "contains modified Copernicus Sentinel data"; Copernicus Sentinel data terms are free, full and open including commercial use. Attribution kept: "contains modified Copernicus Sentinel data".
- Citation: NASA/JPL/OPERA (2025). OPERA Surface Displacement from Sentinel-1 validated product (Version 1). ASF DAAC. doi:10.5067/SNWG/OPL3DISPS1-V1.

## Access routes checked
| Route | Login? | Result |
|---|---|---|
| Granule .nc / .zarr.json.gz on `cumulus.asf.earthdatacloud.nasa.gov` | **Earthdata Login** (302 to urs.earthdata.nasa.gov) | not used |
| S3 `asf-cumulus-prod-opera-products` (us-west-2) | 403 anonymous; needs `/s3credentials` (Earthdata Login) | not used |
| Browse PNGs `.../BROWSE/...` | public (303 to CloudFront) | rendered images only, not measured values; not used |
| NASA GIBS WMTS (epsg3857 GetCapabilities) | public | **no DISP layer**. OPERA layers present: `OPERA_L2_Radiometric_Terrain_Corrected_SAR_Sentinel-1`, `OPERA_L3_DIST-ALERT-HLS_Color_Index`, `OPERA_L3_DIST-ANN-HLS_Color_Index`, `OPERA_L3_Dynamic_Surface_Water_Extent-HLS`, `OPERA_L3_Dynamic_Surface_Water_Extent-Sentinel-1` |
| displacement.asf.alaska.edu portal | page failed TLS check from this machine | n/a |
| **ASF displacement time-series service** `POST https://d2qmcvu7qty7vn.cloudfront.net/timeseries` | **no login** | **used** |

The time-series service is the backend of the public ASF Vertex displacement tool (search.asf.alaska.edu);
its URL is the `displacement_api` entry in Vertex's public JS bundle. Request body:
`{"wkt":"POINT(lon lat)","bucket":"asf-cumulus-prod-opera-products","polarization":"VV","flightDirection":"ASCENDING"|"DESCENDING"}`.
Response: one object per DISP-S1 granule with `short_wavelength_displacement` (m), `reference_datetime`
(first epoch of the frame; the service chains the ministacks), `secondary_datetime`, `temporal_baseline`,
pixel UTM `x`,`y`, `is_masked`, `netcdf_uri`; plus a `mean` summary row (skipped). ~10 s per call.
POLYGON WKT fails server-side ("cannot convert Datasets with 3 dimensions..."), so only points work.
`/frame_intersection` returned 404. Caveat: this endpoint is undocumented, so it may change; responses are cached in `usa/cache/insar/`.

## What is not available without login
- Full-resolution rasters (every 30 m pixel in the +-0.01 deg box), the long-wavelength `displacement` layer,
  and per-pixel quality/uncertainty layers (temporal coherence, phase similarity, etc.) are only in the
  login-gated granules. If wanted: product `OPERA_L3_DISP-S1_V1` via ASF/Earthdata Cloud needs a (free) **NASA Earthdata Login**; the user would have to create it.

## Build design
- 3x3 lattice per block (centre, +-0.005 deg), both flight directions -> up to 18 real-pixel series per block.
- Every epoch kept; masked epochs kept and listed by index. No interpolation or smoothing.
- `rate_mm_yr` = OLS slope over the unmasked epochs of that pixel, computed in the builder (clearly labelled).

## Result (run 2026-11-26)
473 real-pixel series across 38 blocks, ~1.9 MB `web/app/data/insar.json`; every block has ascending data.
Descending data came back for 15 blocks: anchorage, boulder, denver, honolulu, houston, las-vegas, phoenix, portland,
sacramento, salt-lake-city, san-diego, scottsdale, seattle, somerville, spokane. The service answers "No data found" for
descending elsewhere. Series run 2016 to Oct/Nov 2026 (anchorage ascending ends 2021-10, oak-park 2025-08).
cincinnati has 8 ascending series and new-orleans 6 (the other lattice points had no data).

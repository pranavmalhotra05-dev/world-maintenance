# NASA GEDI footprints around built blocks

## What GEDI is
Full-waveform space lidar on the ISS (2019-03 to 2023-03, resumed 2024-04). 25 m footprints,
~60 m along-track spacing, 8 beam tracks ~600 m apart. Orbit limits coverage to about 51.6 N/S, so
**anchorage-ak (61.1 N) has no GEDI data by design**.

## Licence (green)
- CMR collection metadata for GEDI02_A / GEDI02_B (LP DAAC) and GEDI L4A (ORNL DAAC):
  `UseConstraints.LicenseURL` = NASA "Data Use Policy"; GEDI02_A V003 also `"FreeAndOpenData": true`;
  `AccessConstraints` = "None". (cmr.earthdata.nasa.gov/search/collections.umm_json?short_name=GEDI02_A)
- NASA Earthdata Data Use and Citation Guidance
  (https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance), read 2026-11-26:
  > "Unless the content is marked with a use restriction or license, data provided from a NASA-led
  > mission are licensed as Creative Commons Zero (CC0). While there are no restrictions on the use of
  > these data, data users are very strongly urged to cite the data used in their work products."
  GEDI is a NASA Earth Venture Instrument mission (UMD / NASA GSFC). No GEDI-specific restriction is marked.
  -> CC0, commercial use OK.

## Access routes checked
| Route | Login? | Result |
|---|---|---|
| OpenAltimetry API (`/data/api/gedi/...`, `/v2/gedi/...`) | no | 404 - OpenAltimetry now serves ICESat-2 only |
| LP DAAC HDF5 granules (`data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/GEDI02_A.00x/...`) | **yes** | 302 to urs.earthdata.nasa.gov (Earthdata Login) |
| ORNL DAAC L4A granules (`data.ornldaac.earthdata.nasa.gov/protected/gedi/...`) | **yes** | Earthdata Login |
| **gediDB global TileDB database, GFZ Potsdam** (`s3://dog-ext--gedidb--gedi-l2-l4-v003/array_uri`, endpoint https://s3.gfz.de) | **no** | public, unsigned S3 reads. Used. |

gediDB database page (https://gedidb.readthedocs.io/en/latest/user/tiledb_database.html), read 2026-11-26:
"Access Control: Public"; it "stores all processed GEDI data for the products L2A, L2B, L4A and L4C"
(V003, ~12.8 billion shots, 0.25 deg tiles) and "All other variables are as distributed by NASA"
(the only recomputed values are L4A biomass for South-American broadleaf strata, not relevant to US blocks).
It asks publications to acknowledge gediDB (Besnard, Dombrowski & Holcomb, doi:10.5281/zenodo.13885229).
gediDB software licence: EUPL-1.2. **Caveat for the main thread:** GFZ states no separate licence on the
database itself; it is a public mirror of NASA CC0 values (facts copied verbatim), so the data licence that
applies is NASA's CC0. If the "green legal only" rule needs an explicit GFZ statement, the alternative is the
original LP DAAC / ORNL granules, which require a (free) Earthdata Login account - the user would have to create it.

## Filters (important)
gediDB already filters shots when it ingests NASA granules (source `gedidb/beam/l2a_beam.py`,
`DEFAULT_QUALITY_FILTERS`): l2a_quality_flag_rel3 == 1; sensitivity 0.5-1.0; sensitivity_a2 > 0.7;
degrade_flag in the GEDI-recommended "good" list {0,3,8,...,68}; surface_flag == 1; and
|elev_lowestmode - TanDEM-X DEM| < 150 m. So the database holds only quality shots. All are the GEDI
user guide's recommended filters except the +-150 m TanDEM-X sanity check, which gediDB adds.
Shots failing these are not recoverable without the original (login-gated) granules.
build_gedi.py then keeps l2a_quality_flag_rel3 == 1 and degrade_flag == 0 (no-op on the blocks checked),
and nulls cover/pai unless l2b_quality_flag_rel3 == 1 and agbd unless l4a_quality_flag_rel3 == 1.
No thinning, no interpolation.

## Fields / units
- elev_lowestmode: ground (centre of lowest waveform mode), WGS84 ellipsoid in the product; converted to
  NAVD88 (GEOID18) with pyproj EPSG:7912 -> EPSG:6318+5703, identical to build_icesat2.py.
- rh98 / rh100 (TileDB attrs rh_99 / rh_101, 1-indexed labels): height of 98 % / 100 % energy above
  ground, m. Over buildings this is roof/object height, not canopy.
- cover: canopy cover fraction 0-1 (L2B). pai: plant area index m2/m2 (L2B).
- agbd: aboveground biomass density, Mg/ha (L4A).
- date (UTC day), beam (BEAM0000..BEAM1011), shot_number (string, unique id).
Shots span 2019-04 .. 2025.

## How the build works
See `usa/build_gedi.py` (docstring). Read with python `tiledb` (pip), anonymous S3
(`vfs.s3.no_sign_request=true`). Opening the array costs ~2 min (596 fragments' metadata); each block
query is a dimension slice `latitude`, `longitude` (inclusive) over all time.

## Result (built 2026-11-26)
`web/app/data/gedi.json`: 159,110 shots, 16.2 MB, box +-0.05 deg (same as ICESat-2), 37 blocks
(slc-ut-test skipped). anchorage-ak 0 (outside the ISS orbit). degrade_flag != 0 removed 0-370 shots per
block; agbd null on 12,353 shots, cover/pai null on 503. Full run ~15-25 min (two processes:
`build_gedi.py` and `build_gedi.py --reverse` share the cache); re-assembly from cache takes 4 s.

# NASA EMIT + AVIRIS hyperspectral near built blocks

Built 2026-11-26 by `usa/build_emit.py` -> `web/app/data/emit.json` (~550 KB) and
`web/usa/blocks/<slug>/emit.json`. Cache: `usa/cache/emit/`.

## What is in the file
Per block (all 38 blocks in index.json; slc-ut-test skipped if present):
- `scenes`: every EMIT acquisition whose footprint contains the block point (CMR `point=` search) for
  L2A RFL v001/v002, L2B MIN v001/v002, L2B CH4ENH v002/v003, merged by acquisition time. Each has the
  time, cloud %, the granule ids per product and the public browse PNG of the reflectance scene.
- `plumes`: every EMIT L2B CH4PLM.002 / CO2PLM.002 plume complex inside +-0.3 deg of the block. Outline
  polygon = the product's own CMR footprint (pixel-stepped outline), observation time, plume id, source
  scene, public quicklook PNG, distance from block to the nearest outline vertex. For CH4 plumes that the
  EMIT Open Data Portal lists, max concentration (ppm m) + its location, wind speed/source, and emission
  rate +- uncertainty (kg/h) where the product gives one ("NA" -> null). Nothing interpolated.
- `aviris`: AVIRIS-NG L2 RFL, AVIRIS-3 L2A RFL / L2B GHG, AVIRIS-5 L2A RFL, AVIRIS-Classic L2 RFL flight
  lines whose footprint contains the point, with ORNL public browse JPG when present.

Totals: 66 plumes (56 CH4, 10 CO2) near 14 blocks; 13 CH4 plumes carry portal max-ppm-m, 1 has an emission
rate (Omaha, 769 +- 75 kg/h, 2024-10-07). 55 AVIRIS flight lines over 13 blocks. Scenes 8-42 per block
(Anchorage 0: outside the ISS +-52 deg latitude band).

## Licence evidence (verified in browser 2026-11-26)
- Earthdata Data Use and Citation Guidance
  (https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance):
  "Unless the content is marked with a use restriction or license, data provided from a NASA-led mission
  are licensed as Creative Commons Zero (CC0)."
- EMIT CH4PLM V002 catalog page (https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bch4plm-002):
  "This dataset is openly shared, without restriction, in accordance with the EOSDIS Data Use and
  Citation Guidance." Same sentence on the AVIRIS-NG L2 RFL page
  (https://www.earthdata.nasa.gov/data/catalog/ornl-cloud-aviris-ng-l2-reflectance-2110-1).
- CMR UMM-C: EMIT collections have no UseConstraints; ORNL AVIRIS collections link the NASA data policy.
- US GHG Center STAC: `emit-ch4plume-v2` license "CC0-1.0", `emit-ch4plume-v1` "CC-BY-1.0" (not used).
- EMIT Open Data Portal (JPL) says the plume map is published "in keeping with our Open Science and Open
  Data policy". The plume table is the mission's own copy of the V002 product metadata (same plume ids /
  times / outlines as CMR), so it is NASA-mission data -> CC0. The portal page itself has no separate
  licence line; if you want zero ambiguity, rebuild with `--no-portal` (drops maxPpmM/emission/wind only).

## Access routes (all no login)
- CMR granule search (public): `https://cmr.earthdata.nasa.gov/search/granules.umm_json?collection_concept_id=...&point=lon,lat`
  EMIT ids: RFL C2408750690/C4079844428, MIN C2408034484/C4079846859, CH4ENH C3242680113/C4303752968,
  CH4PLM C3242707413, CO2PLM C3244277342 (all -LPCLOUD).
- Public quicklooks: `https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/...png` (HTTP 200, no auth).
- Portal plume table: `https://earth.jpl.nasa.gov/emit-mmgis-lb/Missions/EMIT/Layers/coverage/combined_plume_metadata.json` (7.4 MB, 1686 plumes).
- GIBS: no EMIT layers in either the EPSG:3857 or EPSG:4326 "best" GetCapabilities (checked).

## Blocked by Earthdata Login
Everything in `lp-prod-protected` (302 to urs.earthdata.nasa.gov): plume GeoTIFFs, CH4PLMMETA json
(emission estimates for plumes not in the portal table), CH4ENH rasters, L2B MIN mineral-ID / band-depth
rasters, L2A reflectance cubes, AVIRIS data files. AppEEARS also needs login. So no mineralogy or
reflectance pixel values were extracted; only scene ids/dates. A free Earthdata account (user must create
it) would unlock MIN/RFL sampling at each block; data stays CC0.

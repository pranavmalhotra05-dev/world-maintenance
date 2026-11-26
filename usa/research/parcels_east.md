# Parcels East/Midwest - licence check (2026-11-25)

Details and licence quotes are in `parcels_east.json`. Counts are parcels intersecting a box of about 200 m around each block centre (returnCountOnly).

| Block | Source | Verdict | Tested count |
|---|---|---|---|
| detroit-mi | Detroit Assessor parcel_file_current/0 | grey (the dataset has no licence; only a city FOIA page says "public domain") | 104 |
| oak-park-il | Cook County parcelHistorical/2025 | grey (commercial use only "if compiling it with other data") | 39 |
| levittown-ny | Nassau has no public source; the NYS public layer excludes it | red | 0 |
| philadelphia-pa | DOR_Parcel/0 (OpenDataPhilly) | red ("Commercial use is prohibited without ... written permission") | 330 |
| washington-dc | DCGIS Owner Polygons, Property_and_Land/40 | **green** CC BY 4.0 | 63 |
| somerville-ma | MassGIS Massachusetts_Property_Tax_Parcels/0 | **green** public domain (MassGIS FAQ) | 134 |
| columbus-oh | Franklin Co Auditor Parcel_Features/0 | grey (disclaimer only, no PD statement) | 50 |
| columbus-oz | Franklin Co Auditor Parcel_Features/0 | grey | 62 |

## Sidewalk / pavement layers

| Area | Layer | Verdict | Count |
|---|---|---|---|
| DC | Planimetrics_2023/11 Sidewalks (also Roads 10, Curb 3, Pools 12) | **green** CC BY 4.0 | 14 |
| Franklin Co OH | Reference_Data/2 Pavement (also 3 SecondaryTransportation) | **green** CC0 | 5 |
| Detroit | ROW street/alley openings (legal ROW, not surface) | grey | not tested |
| Philadelphia | city planimetrics | red | not tested |
| Cook | edge of pavement | grey | not tested |
| Somerville | no current open sidewalk polygons found | grey | not tested |

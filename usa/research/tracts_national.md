# Nationwide census-tract layer (lane TR)

Builder: `usa/tracts_national.py` (`.venv\Scripts\python -u -W ignore usa/tracts_national.py [ST ...]`).
Output: `web/usa/data/tracts/<ST>.json` (52 files: 50 states + DC + PR) and `web/usa/data/tracts/index.json`.
85,058 tracts (2020 census tracts as published in cb_2024; Connecticut uses planning-region county codes 0911x-0919x).

## Sources (all US federal works, 17 U.S.C. 105, public domain, commercial use OK)

| metric | source | vintage / join |
|---|---|---|
| geometry | Census cartographic boundary `cb_2024_us_tract_500k.zip` (www2.census.gov) | 2020 tracts |
| income, home_value, year_built, owner_pct, sf_pct, pop | ACS 2020-2024 5-year table-based summary file `.dat` (B19013, B25077, B25035, B25003, B25024, B01003) | direct GEOID |
| oz18 | CDFI Fund 2018 QOZ shapefile (2010 tracts) | Census `tab20_tract20_tract10_natl.txt`; flag = >=50% of 2020 tract land area in 2018 zones |
| oz27 | CDFI Fund `final_oz2_designations.xlsx` | direct (1 = QOZ, 2 = rural QOZ) |
| qct | HUD eGIS `QUALIFIED_CENSUS_TRACTS_2026` | direct GEOID |
| nmtc | HUD eGIS `NMTC20` (CDFI Fund NMTC LIC, 2016-2020 ACS) field `NMTC_LIC_INC` | direct GEOID |
| nri, nri_hail, nri_wind, nri_flood | FEMA NRI census tracts FeatureServer (version "December 2025"); flood = max(IFLD, CFLD) risk score | direct GEOID |
| hpi_1y, hpi_5y | FHFA annual tract HPI `hpi_at_tract.csv` (2010 tracts), latest year 2025 | dominant 2010 tract by land area |

CT legacy-county 2020 GEOIDs (relationship file, cb_2020) are mapped to planning-region GEOIDs by
representative-point join (879 tracts, tract codes all identical).

Skipped: HMDA tract counts (no single national tract bulk file; only per-state loan-level CSVs or the
aggregation API); housing-demand proxies (no further green tract-level source).

## Format

`{meta:{state,fips,name,count,simplify_deg,decimals:4,sources[],fields{}}, tracts:[{g, geom, c, m}]}`.
`geom` = MultiPolygon coordinates `[polygon[ring[[lon,lat]...]]]`; `c` = representative point inside the tract.
Null metrics are dropped; flag fields carry 0/1 (oz27 0/1/2). Simplification: finest tolerance in
(0.0005, 0.00075, 0.001) deg that fits 4 MB, else 0.001 deg if under the 8 MB cap.

## Coverage (non-null of 85,058)

income 83,828 · home_value 82,164 · year_built 83,894 · owner_pct 84,279 · sf_pct 84,305 · pop 85,044 ·
flags 85,058 (positives: oz18 9,866 · oz27 6,503 · qct 14,539 · nmtc 35,167) · NRI 84,092 (PR has no NRI
scores in the service; ~25 water-only tracts absent) · hpi_5y 40,506 · hpi_1y 36,715 (FHFA publishes tracts
only where enough transactions exist).

## Sizes

Total 77.3 MB. All states at 0.0005 deg except CA (5.40 MB, 0.001 deg) and TX (4.73 MB, 0.001 deg) - both
over the 4 MB preference, under the 8 MB cap. Largest others: AK 2.44, FL 3.21, NC 3.10, GA 3.03, NY 3.00 MB.
AK bbox spans the antimeridian (Aleutians: west -179.15, east +179.78).

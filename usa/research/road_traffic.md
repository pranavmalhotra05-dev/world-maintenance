# Road traffic volumes (lane R)

Script: `usa/road_traffic.py` (stages `counties`, `tmas`, `blocks`). National output: `web/usa/data/road_traffic.json`.
Per block: `meta.traffic`, per non-garage house `h.traffic`. Cache: `usa/_cache/traffic/`.
Scope: aggregate traffic volumes only (AADT, VMT, monthly station totals). No probe/vehicle/person-level data.
Road noise is not repeated here: the BTS National Transportation Noise Map 2020 is already pulled in
`usa/enrich_national.py` (`noise()`).

Prior work checked: `usa/research/federal_realestate.md` lists "HPMS AADT" under DOT/FAA but no endpoint;
`state_local_realestate.md` has no traffic source. `mpls/civic.py` already used MnDOT `AADT_SEGMENT_CURRENT`
(segment lines + AADT for the Minneapolis map layer); the same service is reused here for MN blocks.

## Accepted sources

| key | source | endpoint | licence (quote) | licence URL |
|---|---|---|---|---|
| hpms | FHWA Highway Performance Monitoring System, public section data (all states + DC + PR, data years 2024 and 2025) | `https://geo.dot.gov/server/rest/services/Hosted/HPMS_FULL_<ST>_<YEAR>/FeatureServer/0` | US federal work, 17 U.S.C. 105, public domain; NTAD wording: "This NTAD dataset is a work of the United States government ... available for unrestricted public use." | https://www.fhwa.dot.gov/policyinformation/hpms.cfm |
| tmas | NTAD Travel Monitoring Analysis System 2025: Stations Table + Volume table (continuous count stations, daily records with 24 hourly volumes) | ArcGIS items `ac3f8dd9c4454c20b8d3b7ac5247c8f4` (stations, 0.8 MB) and `5a9462b519854ec6a2334b3c0bdfc3c1` (volume, 547 MB zip, 8.03 M day-records); point layer `services.arcgis.com/xOi1kZaI0eWDREZv/.../NTAD_Travel_Monitoring_Analysis_System_Stations` | "This NTAD dataset is a work of the United States government as defined in 17 U.S.C. 101 and as such are not protected by any U.S. copyrights. This work is available for unrestricted public use." | https://www.arcgis.com/home/item.html?id=5a9462b519854ec6a2334b3c0bdfc3c1 |
| tvt | FHWA Traffic Volume Trends, September 2026 (Table 5, state VMT for the month and % change vs the same month of the previous year; also the revised prior month) | https://highways.dot.gov/sites/fhwa.dot.gov/files/26septvt.xlsx | US federal work, public domain (FHWA publication) | https://highways.dot.gov/policy/policy-information/resources/tvt |
| mndot | MnDOT Annual Average Daily Traffic Segments Current | `https://webgis.dot.state.mn.us/65agsf1/rest/services/sdw_incdt/AADT_SEGMENT_CURRENT/FeatureServer/0` | Use limitations: "None. ... Acknowledgement of the publisher would be appreciated." | https://www.arcgis.com/home/item.html?id=42923bcddafe4909b4eed0a03dea893a |
| cdot | CDOT Highways: Traffic Counts (open data) | `https://dtdapps.codot.gov/server/rest/services/Webapps/open_data_sde/FeatureServer/13` | "This work is licensed under a Creative Commons Attribution-ShareAlike 4.0 License" (attribute CDOT; share-alike on derived datasets) | https://www.arcgis.com/home/item.html?id=723375d00be646b0bf87313d4c474a25 |

Access notes
- HPMS: grouped statistics run server side (`outStatistics` with SQL expressions such as `aadt*section_length`), so
  the 1.2 M-section Texas layer never has to be downloaded. The 2025 services renamed `sectionlength` to
  `section_length` and `routename` to `route_name`; the script detects both.
- TVT: highways.dot.gov (Akamai) returns 403 to every scripted client (curl, requests with browser headers). The
  September 2026 xlsx was opened once in the built-in browser, unzipped in-page and Table 5 saved to
  `usa/_cache/traffic/tvt_2026_09_table5.json`. To refresh: repeat for the newest month and drop the file in the cache.
- TMAS volume zip is large (547 MB, 1.7 GB unzipped); it is aggregated once to `usa/_cache/traffic/tmas_monthly.parquet`.

## Rejected

| source | reason |
|---|---|
| INRIX, TomTom, HERE, StreetLight, Google traffic | commercial, licensed probe data; not free / not redistributable |
| FHWA NPMRDS | licensed (INRIX-derived) dataset, access restricted to public agencies and their contractors |
| TxDOT AADT services (`TxDOT_AADT`, `TxDOT_5_Year_Statewide_AADT_Traffic_Counts`, Truck Percent, Ramp AADT) | licence: "may not be used for commercial purposes or resold. Distribution of this content to third parties without TxDOT's written consent is strictly prohibited." Texas is covered by HPMS (which TxDOT itself submits). |
| ODOT Oklahoma `AADT_Network`, `POE_AADT`, `AADT_Count_Site_Locations` | licence "Authorized reference use only." Covered by HPMS. |
| ODOT Ohio traffic count segments | only third-party copies found on ArcGIS Online (HNTB, AMATS, university accounts), with ODOT's text "deemed to be public information" but no agency-hosted service; not used. Covered by HPMS. |
| MoDOT AADT | no MoDOT-hosted service found; the "MoDOT AADT 2006-2025" item is a third-party (MPO) copy. Covered by HPMS. |
| ALDOT `algis.dot.state.al.us/pubgis/.../COUNTERS_PUBLISHED` | endpoint returns 404; no licence statement. Covered by HPMS. |
| geo.dot.gov `Traffic_Stations` / `Traffic_Shed` | National Park Service road counters (223 points), not general traffic |

## Method

County metrics (`counties{fips}`), latest HPMS year per state (2025 everywhere except where only 2024 exists),
mainline sections only (`facility_type` 1 one-way or 2 two-way; ramps and non-inventory directions are left out so
divided highways are not counted twice):
- `vmt_daily` = sum(AADT x section miles), vehicle-miles per day.
- `max_aadt` = busiest section AADT.
- `mean_aadt_arterial` = length-weighted mean AADT on `f_system` 3-4 (principal arterials other than
  freeways, and minor arterials).
- `truck_pct` = 100 x sum((single-unit + combination AADT) x miles) / sum(AADT x miles) over sections that report both.
- `yoy` = county VMT latest / previous year - 1. Suppressed (null) for a whole state when statewide HPMS VMT moves more
  than 8 % between the two submissions (that is a resubmission/re-segmentation artefact, not traffic; TVT shows state
  changes of a few percent). Georgia 2025 vs 2024 (-21 %) is the case found.
- `road_mi`, `year`, `ccs_n` (continuous count stations in the county, when any).

TMAS (`tmas`): per station-day, lane 0 (all lanes combined) is used when present instead of summing lanes, and
direction 9 (both directions combined) instead of summing directions, so nothing is double counted; only day records
with all 24 hours present. Monthly ADT = mean daily volume of the month (>= 7 days). A station's seasonal index is
month ADT / mean of its month ADTs (stations with >= 10 months). State and county indices are the medians across
stations. `stations` rows: `[state_fips, station_id, lat, lon, f_system, county_fips, latest_month,
latest_month_adt, annual_adt, months_reported]`. Station YoY is null because NTAD only publishes the 2025 volume
table; state YoY is in `tvt`.

Blocks: HPMS sections (`facility_type` 1/2/4 with AADT > 0) plus MnDOT segments (MN) or CDOT counts (CO) intersecting
the block bounds grown by 300 m. Records that describe the same section in HPMS and the state layer (same AADT, nearest
points within 40 m) are merged, keeping both source tags (`hpms+cdot`). `meta.traffic.segments` = top 10 by AADT with
`km_from_center` measured from the block origin. `h.traffic` uses every segment within the radius: nearest road with an
AADT, its distance (m), and the busiest AADT within 150 m. Unnamed HPMS sections (common in Texas, where route_name is
blank) are labelled by functional class and HPMS route id.

## Coverage and results

Run of 2026-11-24 (`web/usa/data/road_traffic.json`, 1.15 MB):
- HPMS: 52 jurisdictions (50 states + DC + PR), data year 2025 everywhere, 2024 for YoY. 3,216 counties;
  truck_pct for 3,191; YoY for 2,834. YoY suppressed for GA (-20.6 %), MI (-11.2 %), NY (-14.2 %), PR (+14.7 %)
  statewide submission jumps. Sum of county VMT = 2.91 trillion vehicle-miles/yr, about 10 % under FHWA's national
  total because HPMS sections carry AADT mainly on collectors and above (local-road VMT is estimated by FHWA, not
  attached to sections) - treat `vmt_daily` as "VMT on counted roads".
  Spot checks: Travis TX 31.4 M VMT/day, max 187,783, arterial mean 24,099, trucks 8.4 %, YoY +5.1 %;
  Hennepin MN 27.9 M; Denver 14.0 M (max 286,000); Fulton GA max 404,000.
- TMAS 2025: 8,447 stations with monthly ADT, seasonal index for 51 states and 1,433 counties.
- TVT September 2026: 51 states (state VMT, % change vs Sept 2025; revised August 2026 likewise).

Per block (300 m around the block bounds; houses = non-garage buildings):

| block | segments | busiest within 300 m | houses | median house-to-nearest-counted-road m | nearest beyond 300 m |
|---|---|---|---|---|---|
| austin-tx | 3 | Major collector (HPMS route 258715) 5,272 | 13 | 135 | |
| birmingham-al | 2 | Clairmont Ave 2,005 | 4 | 103 | |
| boulder-co | 4 | Table Mesa Dr 27,000 (hpms) | 4 | 31 | |
| cincinnati-oh | 1 | Montana Ave 22,538 | 6 | 215 | |
| columbus-oh | 0 | none | 18 | - | N High St 11,629 at 435 m |
| columbus-oz | 1 | Sullivant Ave 4,252 | 12 | 76 | |
| dallas-tx | 0 | none | 11 | - | Minor arterial (HPMS route 263063) 20,472 at 308 m |
| denver-co | 5 | SR 2 / Colorado Blvd 48,000 (hpms+cdot) | 14 | 94 | |
| huntsville-al | 1 | Carl T Jones Dr SE 17,925 | 5 | 172 | |
| kansas-city-mo | 1 | Wornall Rd 13,950 | 13 | 256 | |
| minneapolis-mn | 1 | East 42nd Street 7,029 (hpms+mndot) | 15 | 155 | |
| minneapolis-oz | 1 | Fremont Avenue North 2,841 (hpms+mndot) | 16 | 86 | |
| saint-paul-mn | 7 | Edgecumbe Road 10,879 | 16 | 77 | |
| tulsa-ok | 0 | none | 5 | - | East 36th St 1,836 at 327 m |

Residential local streets usually have no AADT in HPMS, so "nearest road" means the nearest road with a published
count. When nothing is counted within 300 m, `meta.traffic.nearest_outside_radius` gives the nearest counted road
within 1 km and the houses' `h.traffic` fields are null.

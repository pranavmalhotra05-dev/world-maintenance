# Tourism / air-visitor demand layer (Lane T1)

Script: `usa/tourism_air.py` (`download` | `build` | `all`). Output: `web/usa/data/tourism_air.json`.
Cache: `usa/_cache/tourism/` (git-ignored). The data is **aggregate only**: counts of passengers and visits.
No individual-level or device-level data is used.

## Sources and licences

| Source | What we take | Licence (quote + URL) |
|---|---|---|
| BTS T-100 Segment (All Carriers), TranStats `gnoyr_VQ=FMG` | Passengers arriving at each US airport per month (scheduled F + charter L classes, domestic + international origins), 24 months | BTS open-data catalogue labels its T-100 datasets "Public Domain U.S. Government" (e.g. https://data.bts.gov/d/bu82-4pwz). US federal work, 17 U.S.C. §105 |
| BTS DB1B Origin & Destination Survey, Coupon (10% ticket sample), TranStats `FLM` / PREZIP | Coupons arriving at each airport. `TRIP_BREAK = X` marks the trip destination (or a stopover). Itinerary origin taken from `SEQ_NUM = 1` | US federal work (BTS), public domain, 17 U.S.C. §105 |
| BTS Reporting Carrier On-Time Performance, https://transtats.bts.gov/PREZIP/ | Local arrival hour of every reporting-carrier flight, 12 months | "Public Domain U.S. Government" (data.bts.gov, e.g. https://data.bts.gov/d/rns4-gpqn) |
| BTS Master Coordinate (TranStats `FLL`) | Airport lon/lat and name (`AIRPORT_IS_LATEST = 1`) | US federal work, public domain |
| DOC ITA / NTTO I-94 arrivals, monthly FINAL "Summary & Analysis", Table G, https://www.trade.gov/i-94-arrivals-historical-data | Overseas + Mexico (air/sea) visitor arrivals by state of first intended address, 12 months | US federal statistical product (ITA/NTTO from DHS/CBP ADIS I-94), 17 U.S.C. §105. trade.gov has no separate licence page. Only the I-94 columns are used, not the Statistics Canada component |
| NPS IRMA Visitor Use Statistics REST v3, https://irmaservices.nps.gov/v3/rest/stats/visitation | Monthly recreation visits per park unit | NPS disclaimer (https://www.nps.gov/aboutus/disclaimer.htm): "material created by the National Park Service … is generally considered in the public domain." Commercial republication must credit the U.S. Government source |
| NPS Land Resources Division boundaries (ArcGIS FeatureServer layer 2) | Park unit polygons (generalised to 0.01°) | NPS public-domain data (same disclaimer) |
| `web/usa/data/counties.json` | County centroids (lon/lat), state | US Census derived, public domain |

Attribution string (in `meta.attribution`): BTS, ITA/NTTO, NPS. U.S. Government works, public domain.

### Rejected
- **AirDNA**: proprietary paid STR data, no redistribution.
- **OpenSky Network**: terms limit use to non-commercial research.
- **ADS-B Exchange**: business use needs a commercial data licence, so it is not permanently free.
- **FlightAware / Flightradar24**: proprietary paid APIs; their ToS forbid redistribution.
- **SafeGraph / Advan / Placer.ai**: proprietary mobility data derived from device-level data, paid licences.
- **Google Community Mobility Reports**: discontinued in 2022, Google ToS, not an open licence.
- **Statistics Canada (Canadian arrivals in NTTO totals)**: not available by US state and not needed.

## Method

1. **Arrivals** (`pax_12m`, `pax_yoy`, `monthly_pax`, `seasonality`, `peak_months`): sum T-100 segment `PASSENGERS` where `DEST` is a US airport and the class is F or L. "Commercial airport" means at least 10,000 arriving passengers in the last 12 months, roughly the FAA commercial-service threshold. YoY compares the last 12 months with the 12 before. `seasonality[0]` is January.
2. **Destination vs connecting** (`od_share`, `visitor_share`): from DB1B coupons whose `DEST` is the airport.
   `od_share` = pax with `TRIP_BREAK = X` / all coupon pax. 1 − od_share is the connecting share (ATL 0.45, DFW 0.47, MCO 0.94, EYW 0.99).
   `visitor_share` also drops itineraries that began at this airport, i.e. residents returning home. This makes it the best "true inbound visitor" proxy.
3. **Arrival hour** (`arrival_hour_share[24]`, `checkin_window`): actual local `ArrTime` (scheduled `CRSArrTime` if the flight was cancelled or diverted), from reporting carriers only. `checkin_window` is the shortest contiguous local-hour span that holds ≥ 50% of arrivals (end hour exclusive), plus the peak hour.
4. **Counties**: haversine distance from the county centroid to each airport. Every airport within 60 km is listed with its km. `visitor_pax_12m_within_60km` = Σ pax_12m × visitor_share. When an airport has no DB1B share, the median share is used. Airports are **not apportioned**: a big hub counts in full for every county within 60 km. `seasonality` is the visitor-pax-weighted month mix of those airports.
5. **I-94** (`intl_arrivals_state`): 12 monthly Table G files (Total Overseas + Mexico excl. land), summed per state. A top-level `i94_states` block also holds the monthly series.
6. **NPS** (`nps_visits_80km`, `nps_units`, `nps_seasonality`): distance from the county centroid to the nearest point of each unit's boundary polygon (0 if the centroid is inside the polygon). Sum the 12-month recreation visits of units within 80 km. `nps_units` lists the top 5 by visits.

## Caveats
- T-100 counts **passengers deplaned on non-stop segments**, so connecting passengers are counted at the hub. Use `visitor_share` to discount them.
- DB1B is a 10% sample of tickets on US carriers only. Its shares are applied to all-carrier T-100 volumes, including international flights by foreign carriers. The DB1B period lags T-100 (BTS publishes DB1B about 6 months later). A "trip break" can also be an intermediate stopover.
- On-time data covers only the reporting carriers (large US airlines). Small airports served only by regional or charter operators get `arrival_hour_share = null`. Flight counts are used, not passenger-weighted.
- County radius is straight-line from the county centroid. Large western counties can have an airport inside the county but more than 60 km from the centroid.
- I-94 state data excludes Canadians and Mexican land arrivals. It is the *first intended* address only, and only at state resolution.
- NPS recreation visits are person-entries, not unique people. Parkways (BLRI, NATR) and urban units (GOGA, LINC) dominate the totals. Some units have no boundary polygon in the LRD service and are dropped (about 21, mostly small sites such as AFBG and FEHA). NPS monthly data is published by calendar year, so the NPS window is the latest complete year.
- Visits of a park or airport are counted for every county within the radius, so county values are "accessible demand", not additive shares.

## Build result (2026-11-24)
- Periods: T-100 2025-09..2026-08 (vs 2024-09..2025-08); DB1B 2024Q3..2025Q2 (Q1/Q2 2025 from PREZIP full coupon files because the TranStats form timed out); On-Time 2025-10..2026-09; I-94 2025-09..2026-08; NPS calendar 2025.
- 414 airports with at least 10k arrivals (967 M arrivals in total). 386 have an O&D share and 347 have an arrival-hour histogram.
- 3,222 counties: 1,587 have at least one airport within 60 km, 1,819 have NPS units within 80 km, and all have the state I-94 value. 379 NPS units are geolocated. File is about 1.4 MB.
- Top counties by visitor pax within 60 km are the NYC/NJ ring (about 42 M from JFK, LGA and EWR), then Broward, Los Angeles and the Orlando ring. These are raw, unnormalised totals; divide by population or housing units for an intensity index.

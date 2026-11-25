# Events, calendar and connectivity signals (lane T3)

Builder: `usa/tourism_events.py` -> `web/usa/data/tourism_events.json` (1.6 MB). Cache: `usa/_cache/tourism/`
(`osm_ql_*.json`, `wd_*.json`, `amtrak.json`, `ipcd_bus.json`, `ntm_stops.parquet`, `walkability.parquet`, `walk_ct.json`,
`ntd_*.json`, IPEDS/EADA zips, Census files). Built 2026-11-24.
Run it with `.venv\Scripts\python -u -W ignore usa/tourism_events.py`. Set `TE_NO_OSM=1` to build without OSM, using Wikidata venues only.

Scope: places and dates only. Nothing in the output is about a person.

## Output schema

```
meta{generated, scope, big_venue_threshold, notes{}, sources[], attribution, rejected[], counts}
venues[{n, k, lon, lat, cap|null, f, enr?, fb?}]  k = stadium|arena|convention|concert|racetrack|university
holidays[{date, name, year, long_weekend{days,start,end,note?}|null}]  observed dates, 2026-2028
break_proxies[{name, start, end}]   heuristic school-break windows (no open source)
events[{name, date|null, end, venue, city, state, fips|null, basis, source, url}]
counties{fips: {venues_n, big_venue_capacity_sum, big_venues_n, univ_enr, fbs, nearest_amtrak_km|null, amtrak_n,
                intercity_bus_n, transit_stops_n, transit_trips_per_capita|null, walkability_median|null,
                fare_free_agency, fare_free_agencies?[]}}
```
County keys are the 3,222 codes in `web/usa/data/counties.geojson`, which use the Connecticut planning-region codes 091xx.

## Sources and licences (all permanently free, commercial use allowed)

| Source | Use | Licence (quote / URL) |
|---|---|---|
| OpenStreetMap, read through the QLever OSM-planet SPARQL endpoint (Uni Freiburg, osm2rdf) | venues: stadium, sports centre/hall with capacity, building=arena, conference/exhibition/convention centre, concert_hall/music_venue, amphitheatre or theatre >= 2,000 seats, events_venue >= 2,000, leisure=track or sports_centre with motor/horse/dog racing | ODbL 1.0. "You are free to copy, distribute, transmit and adapt our data, as long as you credit OpenStreetMap and its contributors. If you alter or build upon our data, you may distribute the result only under the same license." https://www.openstreetmap.org/copyright. **The venue list is a derived database: credit "(c) OpenStreetMap contributors" and share it under ODbL.** |
| Wikidata SPARQL | fills missing OSM capacities (P1083); adds venues OSM lacks that have a stated capacity; excludes items with dissolved (P576), closure date (P3999) or demolished state | CC0. "All structured data (i.e. the main, Property, Lexeme, and EntitySchema namespaces) is released into the public domain under Creative Commons Zero." https://www.wikidata.org/wiki/Wikidata:Licensing |
| NCES IPEDS HD2024 + DRVEF2023 | 4-year institutions with >= 5,000 on-site students (total enrollment minus exclusively-distance share) | US federal work, public domain (17 U.S.C. 105) |
| US Dept of Education EADA 2024-25 (`schools.xlsx`) | football flag per institution, FBS/FCS/other | US federal work, public domain |
| OPM Federal Holidays + 5 U.S.C. 6103 / E.O. 11582 | holidays 2026-2028, computed from the statutory rules and asserted equal to OPM's 2027 and 2028 tables (read 2026-11-24) | US federal work, public domain |
| Wikipedia articles | 18 event facts: host venue/city and dates | CC BY-SA 4.0; the dates and venues are facts, and each item records its article URL |
| FTA National Transit Database (data.transportation.gov `6y83-7vuw`, `g27i-aq2u`, `ccvf-fykn`) | 2025 unlinked passenger trips by agency, fare revenue, agency HQ point | licence field: "Public Domain U.S. Government" (https://www.usa.gov/government-works) |
| BTS NTAD Amtrak Stations (as of 2026-10-08) | rail stations (StnType TRAIN), nearest-station distance | "This NTAD dataset is a work of the United States government as defined in 17 U.S.C. § 101 and as such are not protected by any U.S. copyrights. This work is available for unrestricted public use." |
| BTS NTAD Intermodal Passenger Connectivity DB | intercity bus facilities (BUS_I=1), 2,957 points | same NTAD public-domain statement |
| BTS NTAD National Transit Map Stops (compiled 2026-09-23) | 644k GTFS stops (stations, entrances and nodes dropped; duplicates removed) | CC BY 3.0 US. Acknowledge USDOT/BTS plus the City of Lawndale and Via Mobility Services |
| EPA National Walkability Index (SLD v3) | block-group NatWalkInd (1-20), county median | "These data are available for public access and use and carry no restrictions." (layer metadata) |
| Census: PEP Vintage 2025 county totals, 2020 mean centres of population, 2020 UA-county relationship, 2020 county urban/rural table | denominators, distance origins, UZA fallback weights | US federal work, public domain |

## Rejected or not used

- **Ticketmaster Discovery API.** The terms forbid deriving revenues from use of the API, "whether for direct commercial or monetary gain or otherwise". They also forbid caching or storing Event Content beyond reasonable periods. https://developer.ticketmaster.com/support/terms-of-use/
- **SeatGeek Platform API.** These are proprietary terms: SeatGeek may charge fees or restrict access, and robots, scraping and data-mining of SeatGeek content are prohibited. https://seatgeek.com/api-terms
- **PredictHQ.** It is a paid service (14-day trial or paid plans), and you may not "sell, rent, lease or sub-license" access or PredictHQ Data without written consent. https://www.predicthq.com/legal/terms
- **League and venue schedule pages** (NFL, NBA, NHL, MLB, NCAA, NASCAR, venue sites): marked verify/not used. Their terms typically bar automated collection, so nothing was scraped. Only a few headline host/date facts were taken from Wikipedia.
- **Public Overpass API servers**: tried but not used. overpass-api.de, kumi.systems and private.coffee returned 502/504 or timed out for the whole build. The same OSM selection was read from QLever instead (`QL_FILTERS`). The `overpass()` fallback and `OSM_FILTERS` remain in the script but are not called.
- **HIFLD Open** (sports venues, colleges): retired in 2025. Replaced here by OSM, Wikidata and IPEDS.
- **School-break calendars**: there is no open federal source. `break_proxies` holds heuristics only: Thanksgiving week, Dec 20 to Jan 2, first Saturday of March to Easter Monday, and Memorial Day to Labor Day.
- **Official free-fare agency list**: none exists federally. `fare_free_agency` is derived from NTD instead: the agency reported $0 fare revenue earned and at least 100k trips in 2025. This gives 50 agencies in 45 counties, for example Vail and Avon Transit (Eagle County, CO), Laguna Beach and Clovis Transit, and the Black Hawk tramway. Some are tourist circulators rather than city-wide free-fare systems.

## Method notes

- **Venues.** 6,980 OSM venues remain after de-duplication. Two features are treated as one venue when they are the same kind within about 2 km and one name contains the other or the wikidata id matches. Wikidata fills capacity only when a name token matches within 1.5 km. Wikidata-only venues are skipped when anything is already mapped within 250 m, which covers sponsor renames. Wikidata filled 754 capacities and added 546 venues with capacity. Each point gets a county by point-in-polygon on `counties.geojson`.
- **`big_venue_capacity_sum`.** This is the sum of capacities of non-university venues with capacity >= 10,000. A venue without a capacity tag does not count, so this is a lower bound. Racetracks count at full stated capacity: Indianapolis Motor Speedway is 257,325. A few historic or idle venues remain where neither OSM nor Wikidata marks them closed, for example NRG Astrodome, Auto Club Speedway and Robison Field. Review these by hand if one county matters.
- **`nearest_amtrak_km`.** Great-circle distance from the county's 2020 mean centre of population to the nearest Amtrak TRAIN station. It is null for AK, HI, PR and the territories.
- **`transit_trips_per_capita`.** Each agency's 2025 UPT is split over its served counties: those with >= 3 stops and >= 1% of the agency's NTM stops, weighted by county population. Splitting by raw stop counts over-credited Staten Island, at about 900 trips per head. When an agency has no GTFS stops, the fallback is its primary UZA. The UZA weight is the 2020 UA-county land part times the county's urban density. Failing that, the trips go to the agency HQ county. Shares of total UPT: stops 98.7%, UZA 0.9%, HQ 0.3%. Null means no NTD agency serves the county. Small resort counties get very high values, for example San Miguel CO (the Telluride gondola) at 436 and Eagle CO at 122.
- **`walkability_median`.** Median NatWalkInd over the county's block groups. Connecticut block groups are re-assigned to planning regions by interior point. 02063 and 02066 (the 2019 Alaska split) are null.
- **Holidays.** These are observed dates. A Saturday holiday moves to Friday and a Sunday holiday to Monday, so 2028 New Year's Day is observed Fri 2027-12-31, as on OPM's table. `long_weekend` is 3 days for a Monday or Friday holiday and 4 days for Thanksgiving (Thu-Sun).
- **Events.** Each event has a `basis`: "stated", a stated rule (Kentucky Derby on the first Saturday in May; NBA ASG on the third Sunday of February), "computed" (Mardi Gras is Easter minus 47 days), or "assumed - verify" (Indy 500 on the Sunday of Memorial Day weekend). The 2028 DNC has dates but no host city yet. The 2028 RNC is at the Toyota Center in Houston with dates TBD.

## Coverage (3,222 counties)

| field | counties non-null / non-zero |
|---|---|
| venues_n > 0 | 1,717 |
| big_venue_capacity_sum > 0 | 280 |
| nearest_amtrak_km | 3,109 (AK/HI/PR/territories null) |
| amtrak_n > 0 | 400 |
| intercity_bus_n > 0 | 1,111 (2,951 facilities) |
| transit_stops_n > 0 | 1,331 (644,353 stops) |
| transit_trips_per_capita | 1,788 |
| walkability_median | 3,220 |
| fare_free_agency | 45 |

Venues: 8,030 in total. By kind: stadium 4,359, concert 1,546, convention 757, racetrack 548, university 542 and arena 278. 1,526 have a capacity, and 602 are non-university venues with capacity >= 10k. Holidays: 33. Events: 18. Break proxies: 12.

## Top 20 counties by big_venue_capacity_sum

| fips | county | seats | big venues | trips/cap | walk | Amtrak km |
|---|---|---|---|---|---|---|
| 06037 | Los Angeles CA | 398,660 | 15 | 44.4 | 14.0 | 1.6 |
| 18097 | Marion IN | 337,911 | 3 | 7.0 | 12.2 | 3.6 |
| 48201 | Harris TX | 306,176 | 12 | 15.8 | 11.7 | 7.9 |
| 04013 | Maricopa AZ | 294,592 | 12 | 10.5 | 12.0 | 48.3 |
| 17031 | Cook IL | 273,151 | 9 | 72.5 | 14.0 | 9.3 |
| 48439 | Tarrant TX | 270,000 | 10 | 3.1 | 9.2 | 7.5 |
| 53033 | King WA | 224,614 | 6 | 66.2 | 13.3 | 9.2 |
| 48113 | Dallas TX | 213,775 | 4 | 11.1 | 12.0 | 4.2 |
| 32003 | Clark NV | 211,176 | 8 | 23.9 | 12.3 | 145.3 |
| 06085 | Santa Clara CA | 204,146 | 7 | 32.5 | 14.0 | 1.8 |
| 39061 | Hamilton OH | 201,640 | 6 | 19.7 | 11.5 | 8.3 |
| 42101 | Philadelphia PA | 192,389 | 4 | 55.3 | 14.8 | 1.5 |
| 29510 | St. Louis city MO | 186,975 | 7 | 17.1 | 13.9 | 4.3 |
| 13121 | Fulton GA | 184,789 | 5 | 31.6 | 11.7 | 3.7 |
| 12086 | Miami-Dade FL | 178,368 | 6 | 35.2 | 14.2 | 9.9 |
| 27053 | Hennepin MN | 174,451 | 4 | 21.6 | 13.2 | 21.9 |
| 11001 | District of Columbia | 171,562 | 6 | 94.3 | 14.5 | 1.6 |
| 39049 | Franklin OH | 170,786 | 5 | 8.2 | 12.0 | 142.3 |
| 12095 | Orange FL | 165,107 | 5 | 9.2 | 12.0 | 0.7 |
| 29095 | Jackson MO | 164,571 | 5 | 13.3 | 11.8 | 8.6 |

Caveats: OSM capacity tags are uneven. College towns appear through `univ_enr` and `fbs` as well as stadium seats; for example 01081 Lee AL holds Jordan-Hare at 87,451. Both Columbus OH and Las Vegas show long Amtrak distances because neither has Amtrak rail service.

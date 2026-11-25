# Visitor counts (lane V2) — sources, licences, method, gaps

Output: `web/usa/data/visitors.json`, built by `usa/visitors.py` (`.venv\Scripts\python -u -W ignore usa\visitors.py`).
Downloads are cached under `usa/_cache/visitors/` (raw files in `raw/`). Every number is an **aggregate count of visits,
passengers or visitors** published by a government body. Nothing here describes an individual.

Not rebuilt here (other lanes): airport passengers, NPS park visits and I-94 arrivals by state are in
`tourism_air.json` (lane T1). Hotel-tax receipts, QCEW lodging jobs, STR registries and Wikipedia views are in
`tourism_econ.json` (lane T2). NPS units are skipped in the federal-lands step for that reason.

## Accepted sources

| # | Source | What we take | Licence (quote / basis) | URL |
|---|---|---|---|---|
| 1 | NTTO *Overseas Visitor Impact on State Economies 2024*, Table 1 | overseas visits, spending ($M) and direct jobs by state (excludes Canada and Mexico) | US federal work, public domain (17 U.S.C. §105) | https://www.trade.gov/sites/default/files/2026-04/2024-Visitors-by-State_Final.pdf |
| 1b | NTTO SIAT Inbound Market Profiles, Vacation + Business (CY2024 columns) | "% visiting the destination" for states and cities, plus segment volumes | US federal work, public domain | https://www.trade.gov/sites/default/files/2025-06/Inbound-Market-Profile-Vacation.xlsx , …-Business.xlsx |
| 2 | BEA Travel & Tourism Satellite Account 2023 | **national only**: direct tourism output and employment (`meta.national.ttsa`) | BEA: "BEA data are in the public domain and may be used without restriction" | https://www.bea.gov/sites/default/files/2025-02/Tour2023AU24.xlsx |
| 2b | BEA Outdoor Recreation Satellite Account, U.S. and States (Nov 2026 release, year 2025) | state outdoor-recreation value added (k$) and employment | public domain (same BEA statement) | https://www.bea.gov/sites/default/files/2026-11/ores1126-state.xlsx |
| 3 | BTS Border Crossing Entry Data (CBP) | inbound persons by land port: personal-vehicle passengers, bus passengers, pedestrians and train passengers, summed over the last 12 published months; port lon/lat in the dataset | data.bts.gov licence field: "Public Domain"; attribution "U.S. Department of Homeland Security, Customs and Border Protection" | https://data.bts.gov/d/keg4-3bc2 |
| 4 | BTS / MARAD Top 25 Ports by Cruise Ship Passenger Arrivals, 2023 | passenger arrivals per port | US federal work. Citation: "U.S. Maritime Administration Analysis of Data from Customs and Border Protection Vessel Entrance and Clearance System" | https://data.bts.gov/d/3z7h-xatu |
| 6 | Federal Interagency Council on Outdoor Recreation, *FY2024 Interagency Recreation Visitation Data Report*, Appendix Table A | FY2024 total visits per reporting unit for BLM, USFS, USACE, FWS and Reclamation (NPS skipped, see lane T1) | US federal work, public domain (DOI/USDA/USACE report under the EXPLORE Act) | https://ridb.recreation.gov/downloads/FY2024%20Interagency%20Recreation%20Visitation%20Data%20Report.pdf |
| 6b | Unit boundaries: USFS EDW Administrative Forest Boundaries; BLM National Admin Unit field offices + NLCS NM/NCA polygons; FWS NWRS boundaries; USACE Recreation Areas (CRREL); Reclamation Recreation Management Areas | polygons used to place each unit and split its visits across counties | federal agency data, public domain | listed in `meta.sources` |
| 7 | NY OPRHP *State Park Annual Attendance by Facility* + *State Park Facility Points* | latest-year attendance per facility, with county and point | NY Open Data Terms of Use: free use for any lawful purpose, commercial use included | https://data.ny.gov/d/8f3n-xj78 , https://data.ny.gov/d/9uuk-x7vh |

Each source's URL and licence note is also in `meta.sources` of the JSON. The combined credit line is in `meta.attribution`.

## Rejected / not available (also in `meta.rejected`)

* **Amtrak station ridership (state fact sheets).** Rejected on licence. Amtrak's Web Notices & Terms of Use say the
  Materials "are and will remain the property of Amtrak", and "you may not copy, reproduce, republish … or distribute
  the Materials … without our prior written permission". Amtrak is a corporation, not a federal agency, so its works
  are not public domain. `counties.amtrak_riders` is therefore null everywhere.
* **Rail Passengers Association station boarding sheets.** Copyright NARP, no open licence.
* **NTTO States & Cities Visited Monitor (Power BI).** The licence is fine (federal work), but the totals can only be
  exported through an interactive Power BI app. City totals were derived from the downloadable SIAT profiles instead
  (see Method).
* **BEA state-level TTSA.** BEA does not publish it; the TTSA is national only. `tourism_output` and `tourism_jobs`
  are null. ORSA state figures are given in separate `outdoor_rec_*` fields as a state-level proxy.
* **State park attendance for TX, MN, CO, OH, OK, MO, AL.** No machine-readable release with an explicit
  commercial-reuse licence was found. Figures appear only in agency press releases and news stories (for example
  TPWD's 2025 top-10 list). This is a gap. NY is the only state with clean open data.
* **Private DMO and consultant reports** (Tourism Economics, Longwoods, Dean Runyan, Destination Analysts,
  STR/CoStar, Visa/Mastercard spend data, Placer.ai). Copyrighted or proprietary, with no commercial-reuse grant. Not used.
* **NASPD AIX state-park statistics.** Association copyright, member distribution only.
* **FWS per-refuge visitor surveys (USGS DS 643/754).** Public domain, but they are 2010–12 survey samples, not
  visit totals. Superseded by FICOR Table A.
* **USACE VERS/OMBIL per-project downloads.** No public bulk export. FICOR Table A carries the same FY2024 project totals.

## Method

* **States.** `overseas_visits`, `overseas_spend_musd` and `overseas_jobs` come from NTTO Table 1 (2024).
  `overseas_rank` ranks the 54 states and territories in that table. `overseas_low_sample` marks NTTO's asterisk
  (fewer than 100 SIAT respondents). An overseas visitor counts once in each state visited, so the state figures do
  not add up to the 35.2 M national total. `border_crossings_12m`, `cruise_pax`, `federal_land_visits` and
  `state_park_visits` are the county sums rolled up to the state. `federal_land_visits_unlocated` holds visits from
  units that could not be matched to a polygon; these are known only by state, from the BLM/FWS state filter.
* **Cities.** For each SIAT city and year 2024:
  city share = Σ_segments(volume × city %) / Σ_segments(volume × state %), with segments = Vacation and Business.
  City overseas visits = NTTO 2024 state total × share, rounded to the nearest thousand. This is a derived estimate.
  As a check, the 2024 NYC result can be compared with NTTO's published 2025 figure (8.915 M).
* **Border crossings.** Persons (not vehicles) are summed from the last 12 published months. Each port's lon/lat is
  placed in a county by point-in-polygon, falling back to the nearest county within about 25 km. The counts are
  inbound entries, so they include returning US residents as well as visitors.
* **Cruise.** Only the BTS top-25 table (2023) is public as data. Terminal coordinates were hard-coded; Alaska ports
  have their borough FIPS set explicitly. VI and PR ports are kept as sites with no county.
* **Federal lands.** FICOR Table A was parsed from the PDF. The parsed agency totals match Table 1 exactly
  (BLM 80.76 M, USFS 164.17 M, USACE 260.48 M, FWS 70.70 M, Reclamation 38.77 M). Each unit is matched by normalised
  name (stop-words removed, difflib ≥ 0.86) to its agency's polygons. BLM units try NLCS monuments/NCAs first, then
  field offices. USACE matches on project name in the recreation-areas layer, with all recreation areas of a project
  unioned. Visits are split across counties **in proportion to the unit's area in each county**. This is a known
  bias: area is not where people actually go, and a whole field office or forest is spread evenly.
  Each located unit becomes one `sites[]` row, at a representative point in its polygon, with `fips` = the county
  holding the largest share.
* **NY state parks.** Latest year of the data.ny.gov attendance table. County comes from the table's own county
  name. The point comes from the facility-points layer by fuzzy name match, falling back to the county centroid
  (`located` says which was used).

## Coverage and results

The build of 2026-11-24 produced a 711 KB file with 1,674 sites, 87 cities and 1,891 counties with at least one figure.

* **States.** NTTO overseas figures for 54 states and territories, 41 of them flagged low-sample. ORSA for 51.
  Border crossings for 14 states (219.6 M inbound persons, Nov 2025 to Oct 2026). Cruise for 14 states.
  Federal-land visits for 51. State parks for NY only.
* **Federal units located (FY2024 visits):**

  | Agency | Units located | Visits located |
  |---|---|---|
  | USFS | 114 / 114 | 164.2 M / 164.2 M |
  | BLM | 165 / 174 | 79.0 M / 80.8 M |
  | FWS | 521 / 542 | 69.6 M / 70.7 M |
  | Reclamation | 187 / 192 | 37.9 M / 38.8 M |
  | USACE | 319 / 402 | 227.4 M / 260.5 M |

  The main USACE misses are LA flood-control dams (Prado, Whittier Narrows, Sepulveda, Brea, Santa Fe), Cape Cod
  Canal, the Upper Mississippi pools, Buffalo Bayou and Duluth-Superior Harbor. They are not in the
  recreation-areas layer under a matching name.
* **County fields filled:** federal_land_visits 1,817; border_crossings_12m 65; cruise_pax 22; state_park_visits 55
  (NY); amtrak_riders 0.
* **NY parks:** 246 facilities, year 2025. 160 are placed at a facility point and 86 at the county centroid.
* **Caveats on outliers.** These are the published figures, kept as-is. FWS reports 13.8 M visits for Oregon Islands
  NWR (coastal viewpoints), which puts Curry County OR in the county top 20. San Diego and El Paso lead the counties
  because of border crossings, which count every inbound person, including commuters.

Top counties, summing all four count fields: San Diego CA 52.0 M, El Paso TX 28.5 M, Imperial CA 20.1 M,
Niagara NY 20.0 M, Suffolk NY 17.0 M, Hidalgo TX 15.7 M, Webb TX 14.1 M, Cameron TX 12.8 M, Nassau NY 12.2 M,
Santa Cruz AZ 10.7 M, Curry OR 10.6 M, Yuma AZ 9.4 M, Whatcom WA 8.2 M, Hall GA 7.8 M, Maverick TX 6.8 M,
Wayne MI 6.4 M, Los Angeles CA 6.1 M, Brevard FL 6.0 M, Cobb GA 5.9 M, Denton TX 5.3 M.

Top cities, 2024 overseas visits (derived): New York City 9.54 M, Miami 5.28 M, Los Angeles 4.59 M,
Orlando 4.16 M, Las Vegas 2.59 M, San Francisco 2.53 M, Washington DC 1.63 M, Honolulu 1.60 M, Boston 1.39 M,
Chicago 1.38 M, Houston 0.95 M, Atlanta 0.90 M, Dallas 0.84 M, San Diego 0.83 M, Ft. Lauderdale 0.81 M,
Seattle 0.76 M, Flagstaff/Grand Canyon/Sedona 0.68 M, Anaheim 0.64 M, Florida Keys 0.49 M, Philadelphia 0.48 M.

## Gaps / next steps

* State park attendance for the core states (MN, TX, CO, OH, OK, MO, AL). An open-records request, or an agency
  open-data release, would be needed.
* Cruise: only the top 25 ports are covered. The full MARAD per-port series (all US ports) is not published as open data.
* NTTO city totals are derived, not the official Power BI figures. Canada and Mexico visitors by state are not
  published by NTTO; Statistics Canada's frontier counts by US state are a separate licence question.
* Federal-land county split is area-weighted. A better split would weight by recreation-site points (RIDB facilities,
  which need a free API key and are public domain) or by FICOR Table B activity data.
* Amtrak ridership would need written permission from Amtrak.

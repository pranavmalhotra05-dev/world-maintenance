# Tourism economy and STR supply signals, by county (lane T2)

Script: `usa/tourism_econ.py` (cache `usa/_cache/tourism/`). Output: `web/usa/data/tourism_econ.json`, keyed by 5-digit county FIPS (all 3,222 rows in `web/usa/data/counties.json`). Aggregates only. No listing-level or personal data is stored. Raw permit and registry rows stay in the local cache and are counted only.

Run: `.venv\Scripts\python -u -W ignore usa\tourism_econ.py qcew cbp pop hot str wiki build` (or just `build` once the cache is warm; `build` never calls the pageviews API).

## Sources and licences (all permanently free, commercial use allowed)

| # | Source | What we take | Licence (quote + URL) |
|---|---|---|---|
| 1 | BLS QCEW open-data CSV slices `https://data.bls.gov/cew/data/api/{yr}/{q}/industry/{naics}.csv` | Private (own_code 5) county rows for NAICS 721, 7211, 7213, 71, 722, supersector 1026 and total private (10). Quarters 2025Q2-2026Q1, plus 2024Q2-2025Q1 for 721 YoY. 2026Q2 returned 404 on 2026-11-24, so it is not released yet | "everything that we publish ... is in the public domain ... we do ask that you cite the Bureau of Labor Statistics as the source." https://www.bls.gov/opub/copyright-information.htm (checked 2026-11-24) |
| 2 | Census County Business Patterns 2023 county file `https://www2.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip` (the 2024 file returned 404) | NAICS 721 establishments, used as a cross-check | U.S. Government work, so public domain under 17 U.S.C. 105. The Census citation policy asks for attribution: https://www.census.gov/about/policies/citation.html |
| 3 | Census Vintage 2025 county population `co-est2025-alldata.csv` | `pop`, for sanity checks only (Puerto Rico is not in this file) | Same as row 2 |
| 4 | Texas Comptroller "Local Hotel Occupancy Tax Reporting 2025" `data.texas.gov/resource/er34-v24h` | Local HOT revenue for FY2025 (Ch. 351 city + Ch. 352 county), summed to county | Dataset metadata licence is "Public Domain" (Socrata catalog API, checked 2026-11-24) |
| 5 | Texas Comptroller "Hotel Tax Permits" `data.texas.gov/resource/hdzd-884n` (42k rows, updated 2026-11-21) | (a) mapping each city to its modal county for HOT; (b) count of active permits with NAICS 531110/531311 (residential lessors and managers, i.e. owner-registered STRs) | "Public Domain" (dataset metadata) |
| 6 | City of Austin "STR Locations" `datahub.austintexas.gov/resource/2fah-4p7e` (3,813 rows, updated 2026-11-15) | Count only | "Public Domain U.S. Government", termsLink https://www.usa.gov/government-works (dataset metadata) |
| 7 | City of Denver "Short Term Rental Licenses" on the Colorado Information Marketplace `data.colorado.gov/resource/f3vc-vat3` | Count of licences with status "License Issued - Active" or "About to Expire" (2,063) | "Public Domain" (dataset metadata; attribution "City and county of Denver") |
| 8 | Wikidata SPARQL: county (P882 FIPS) to county seat (P36) to enwiki article | Article titles (3,107 counties have one) | "Copyrights of structured data in the main, Property, Lexeme, and EntitySchema namespaces are waived using the Creative Commons Zero (CC0)". https://dumps.wikimedia.org/legal.html |
| 9 | Wikimedia Pageviews REST API, per-article monthly, `agent=user`, Nov 2024-Oct 2026 | Monthly views of the county-seat article | "All Analytics datasets are available under the Creative Commons Zero (CC0) public domain dedication, unless otherwise specified." https://dumps.wikimedia.org/legal.html |

Attribution string: see `meta.attribution` in the JSON.

## Rejected

| Source | Reason |
|---|---|
| AirDNA, Mashvisor, AllTheRooms, Key Data, Lighthouse/Transparent | Paid and proprietary. Built from scraped listings. Terms bar redistribution |
| Inside Airbnb | The site licence is CC BY 4.0, but the data is scraped from Airbnb, whose terms forbid scraping. The project excludes scraped STR data |
| Any Airbnb/Vrbo listing scraper | Platform terms prohibit it. Not open data |
| Google Trends | Covered by the Google Terms of Service, with no open licence for bulk automated access or redistribution |
| STR (CoStar) hotel occupancy/ADR | Proprietary subscription |
| Chicago Shared Housing Registrations (`qfyy-956j`) | The City of Chicago Data Terms of Use (https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html) let the City "require a user of this data to terminate any and all display, distribution or other use ... for any reason". That is revocable, so it fails "permanently free". The rows also contain host names |
| Nashville Residential STR Permits (ArcGIS `b5315bda...`) | Its licenceInfo only points to Metro Executive Order 18 (Open Data Policy). The EO lets departments publish "with reasonable, user-friendly registration requirements, license requirements, or restrictions on the use and distribution". It grants no open licence. The rows include owner names and phones. Not licence-clean, so excluded |
| Colorado DOR "Taxes by County and Industry" (`ax6t-kdim`, includes a lodging column) | Public Domain, but stale: the latest month is 2016-02 |
| Boulder rental licences, Boulder County STR layer | Licence not stated or county ToS (see rentals.md) |
| Monthly Texas HOT receipts by hotel | Not published as open data any more. The Comptroller open-data index lists only Hotel Tax Permits and the annual local HOT reports, so `hot_receipts_12m` is the **annual FY2025** local HOT revenue, not a monthly series |
| Other HOT states (CO, MN, OH, MO, OK, AL) | No current, licence-clean, machine-readable lodging-tax receipts by county were found. data.mo.gov only has lodging licence lists with no licence stated. data.ok.gov and data.ohio.gov had no lodging-tax datasets. Coverage is null outside TX |

## Method

- **QCEW**: own_code 5 (private). Rows flagged `disclosure_code = N` keep establishment counts (BLS still publishes them), but employment and wages are null. An employment-based metric is published only if all 4 quarters are disclosed. `acc_suppressed=true` marks a county that has establishments but suppressed employment.
- `acc_emp` = mean of the 12 monthly employment levels. `acc_wages` = sum of 4 quarters' wages. `acc_emp_yoy` = % change against the prior 4 quarters. `tourism_intensity` = acc_emp / total private employment x 100. `leisure_emp` = NAICS 71 + 722. `leis_hosp_emp`/`lh_share` come from supersector 1026, which is suppressed far less often (about 110 counties against about 980 for 721).
- `seasonality_q` = [Q1..Q4] employment / 4-quarter mean, from 721, falling back to 1026 (`season_src`). Employment is a muted seasonality signal (staff are retained), so a ratio like Denali's Q3/Q1 of about 15x is the extreme case.
- **HOT**: city reports go to the county holding most of that city's hotel-tax permits; county reports match by name. Misspelled entity names (e.g. "Cleburner", "Nacodgches", "Angelton") stay unmatched and are counted in `meta.tx_hot_unmatched`.
- **STR**: `str_registered` = TX: active state hotel-tax permits with NAICS 531110/531311 (a floor, because Airbnb/Vrbo remit state HOT for most hosts, so many hosts hold no permit). Travis: max(Austin registry 3,813, TX permits). Denver: active licences. Null everywhere else, which means no data, not zero.
- **Wikipedia**: one article per county, the county seat from Wikidata P36. The anonymous Wikimedia API client was rate-limited (HTTP 429 with retry-after about 28 s, so about 10-20 requests a minute), so the fetch covers the 500 most populous counties plus the 200 most tourism-intensive ones (`WIKI_TOP`, `WIKI_TOP_TI`), 679 counties in total, and the cache is reused. `wiki_season_idx` is the calendar-month index over 24 months, and `wiki_peak_months` is the months with index of at least 1.15 (or the single top month). Caveat: county-seat views mix residents' and travellers' interest, and big-city articles are flat. Seat articles are a poor proxy where the resort town is not the seat (e.g. Sevier TN gives Sevierville, not Gatlinburg).

## Caveats for the demand index

- Casino hotels (NAICS 721120) sit inside 721, so casino counties (Tunica MS, Gilpin CO) top `tourism_intensity`. Consider down-weighting them or using `trav_emp`.
- Tiny counties are noisy. Combine intensity with absolute `acc_emp`/`acc_wages`.
- CBP (2023, March employer establishments) and QCEW (2026Q1, establishments) differ in definitions. Treat `cbp_acc_estabs` as a cross-check only.

## Results (build 2026-11-24, file 1.42 MB)

Coverage, out of 3,222 counties:

| Field | Counties |
|---|---|
| acc_estabs (QCEW 2026Q1, published even when employment is suppressed) | 2,971 |
| acc_emp / acc_wages (all 4 quarters disclosed) | 1,846 |
| tourism_intensity | 1,843 |
| acc_emp_yoy | 1,729 |
| leisure_emp (71 + 722) | 1,838 |
| seasonality_q (721, or 1026 fallback) | 3,041 |
| cbp_acc_estabs (CBP 2023) | 2,389 |
| hot_receipts_12m (TX FY2025 only) | 206 (9 TX reporting entities unmatched) |
| str_registered (TX permits + Austin + Denver) | 211 |
| wiki_views_12m / wiki_peak_months | 679 |

Top 20 by tourism_intensity (accommodation employment as % of private employment). Peak months are calendar months from seat-article pageviews:

| # | FIPS | County | Intensity % | Acc. emp | Private emp | Pop 2025 | seasonality_q [Q1..Q4] | Wiki peak months |
|---|---|---|---|---|---|---|---|---|
| 1 | 28143 | Tunica, MS | 45.99 | 1700 | 3695 | 8819 | [0.921, 1.035, 1.046, 0.998] | [1] |
| 2 | 08047 | Gilpin, CO | 43.85 | 1882 | 4291 | 6073 | [0.986, 0.99, 1.006, 1.018] | [7, 8] |
| 3 | 49017 | Garfield, UT | 43.75 | 884 | 2021 | 5256 | [0.659, 1.159, 1.255, 0.928] | [5, 7, 6] |
| 4 | 02068 | Denali, AK | 37.72 | 657 | 1741 | 1616 | [0.148, 1.484, 2.184, 0.184] | [8, 7] |
| 5 | 06043 | Mariposa, CA | 35.42 | 1310 | 3698 | 16918 | [0.812, 1.039, 1.166, 0.983] | [7, 8] |
| 6 | 06051 | Mono, CA | 32.58 | 2087 | 6407 | 12505 | [1.243, 0.967, 0.887, 0.903] | [7, 8, 6] |
| 7 | 35007 | Colfax, NM | 32.46 | 978 | 3012 | 12184 | [0.784, 1.113, 1.351, 0.752] | [7, 6] |
| 8 | 27077 | Lake of the Woods, MN | 25.86 | 325 | 1256 | 3770 | [1.127, 0.954, 1.079, 0.84] | [1, 5, 4] |
| 9 | 56039 | Teton, WY | 25.66 | 5394 | 21022 | 23333 | [0.896, 0.989, 1.266, 0.849] | [12, 4, 5] |
| 10 | 53059 | Skamania, WA | 24.74 | 445 | 1798 | 12669 | [0.984, 0.966, 1.036, 1.014] | [11, 5, 7] |
| 11 | 26097 | Mackinac, MI | 24.27 | 926 | 3814 | 11236 | [0.376, 1.238, 1.627, 0.758] | [7, 8, 6] |
| 12 | 05097 | Montgomery, AR | 23.51 | 192 | 816 | 8579 | [0.781, 1.213, 1.236, 0.77] | [8] |
| 13 | 27031 | Cook, MN | 22.85 | 410 | 1796 | 5522 | [0.859, 0.998, 1.164, 0.979] | [7, 8, 6] |
| 14 | 49025 | Kane, UT | 21.55 | 727 | 3374 | 8597 | [0.863, 1.074, 1.11, 0.953] | [5, 4, 3] |
| 15 | 12087 | Monroe, FL | 21.5 | 8793 | 40892 | 80406 | [1.014, 1.016, 0.97, 1.0] | [12, 3] |
| 16 | 34001 | Atlantic, NJ | 20.55 | 21667 | 105423 | 278657 | [0.954, 1.017, 1.043, 0.986] | [1, 8] |
| 17 | 48385 | Real, TX | 20.05 | 126 | 630 | 2745 | [0.783, 1.371, 1.136, 0.709] | [7, 6, 5] |
| 18 | 46075 | Jones, SD | 19.76 | 43 | 216 | 913 | [0.672, 1.055, 1.227, 1.047] | [8, 7, 11, 5, 6] |
| 19 | 50015 | Lamoille, VT | 19.54 | 2145 | 10974 | 26294 | [1.234, 0.845, 0.924, 0.997] | [7] |
| 20 | 72147 | Vieques, PR | 19.04 | 186 | 975 | n/a | [1.2, 0.916, 0.889, 0.995] | [2, 1] |

Population sanity: every top-20 county is a known resort, park, casino or gateway county, with populations of 0.9k-280k. Private employment of 216-105k is consistent with that, and none is a large metro. Three are casino counties: Tunica, Gilpin, Atlantic. Many are national-park gateways: Garfield (Bryce), Denali, Mariposa (Yosemite), Teton, Kane, Grand. Mono and Lamoille are ski counties with Q1 peaks, Mackinac and Hamilton NY are summer counties. Jones SD (pop 913) is a small-number outlier on the I-90 corridor.

Other spot checks: Pitkin CO (Aspen) peaks Jan/Dec, Sevier TN Aug/Sep, Dare NC (Manteo) May-Aug, Horry SC (Conway) Jun. Texas STR permits are highest in Travis (Austin registry 3,813), Nueces 1,824, Galveston 1,154, Cameron 965 and Gillespie 891. Texas HOT FY2025: Harris $184M, Travis $136M, Dallas $132M, Bexar $106M.


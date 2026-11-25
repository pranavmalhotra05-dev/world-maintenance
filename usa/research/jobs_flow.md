# Lane J - county job market and people-flow signals

Output: `web/usa/data/jobs_flow.json`, built by `usa/jobs_flow.py` (stages `qcew laus lodes irs pop build`; raw files cached in `usa/_cache/jobs/`).
Everything is a county aggregate. No person-level or establishment-level records are kept.

## Sources (all permanently free, commercial use allowed)

| # | Source | File(s) / URL | Vintage | Licence |
|---|---|---|---|---|
| 1 | BLS QCEW open-data CSV slices | `https://data.bls.gov/cew/data/api/{yr}/{q}/industry/{code}.csv` (code 10 = total; 1011-1028 = supersectors) | 2025Q2-2026Q1 (latest 4), plus 2024Q2-2025Q1 and 2020Q2-2021Q1 | Public domain. BLS copyright page (https://www.bls.gov/opub/copyright-information.htm): "everything that we publish ... is in the public domain"; BLS asks that it be cited as the source. |
| 2 | BLS LAUS flat file | `https://download.bls.gov/pub/time.series/la/la.data.0.CurrentU25-29` (series `LAUCN...03` rate, `...06` labour force) | Sep 2026 (preliminary), compared with Sep 2025. Not seasonally adjusted, so the year-over-year same-month comparison is used | Public domain (same BLS page). |
| 3 | Census LEHD LODES 8, origin-destination main + aux, JT00 (all jobs) | `https://lehd.ces.census.gov/data/lodes/LODES8/{st}/od/{st}_od_{main,aux}_JT00_{yr}.csv.gz`; CT block-to-planning-region from `ct_xwalk.csv.gz` | 2024 for 49 states + DC; MI 2021; AK 2016 (latest LODES publishes); no PR | US Government work: "Copyright protection under this title is not available for any work of the United States Government" (17 U.S.C. 105, https://www.copyright.gov/title17/92chap1.html#105). LODES are public-use files. |
| 4 | IRS SOI county-to-county migration | `https://www.irs.gov/pub/irs-soi/countyinflow2324.csv`, `countyoutflow2324.csv` (index: https://www.irs.gov/statistics/soi-tax-stats-migration-data) | Filing years 2023 to 2024 (latest released) | US Government work, 17 U.S.C. 105. |
| 5 | Census Population Estimates V2025, county totals and components of change | `https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv` | Jul 2024 to Jul 2025, plus 2020 base | US Government work, 17 U.S.C. 105. |
| 6 | Census Building Permits Survey | already in the per-county blocks | n/a | Referenced only, not re-ingested. |

Attribution string (also in `meta.attribution`): "Sources: U.S. Bureau of Labor Statistics (QCEW, LAUS); U.S. Census Bureau (LEHD LODES 8, Population Estimates V2025); IRS Statistics of Income migration data."

## Rejected / skipped

| Source | Reason |
|---|---|
| Lightcast (Burning Glass) postings | Paid, proprietary |
| LinkedIn Economic Graph | Proprietary; terms forbid scraping or commercial reuse |
| Indeed Hiring Lab | Terms restrict redistribution and commercial reuse; no open county bulk file |
| Revelio Labs | Paid |
| FRED API | Terms restrict commercial use, and many series belong to third parties. The primary BLS and Census files are used instead |
| Census Data API (ACS county-to-county flows) | Now needs a key. IRS SOI plus Pop Estimates cover migration from flat files |
| USAJOBS Search API | Needs a registered key and a contact email in the User-Agent. Skipped as instructed |
| Zillow, Redfin, Apartment List migration data | Terms restrict commercial redistribution |

## Field definitions (per county, keyed by 5-digit FIPS)

- `emp`: QCEW covered employment, average of the monthly levels over the latest 4 quarters (all ownerships, county total, agglvl 70).
- `emp_yoy`: % change against the prior 4 quarters. `emp_5y`: % change against 2020Q2-2021Q1. **Caution:** the 5-year base is depressed by the pandemic, so it overstates growth. Do not use it for ranking.
- `wage_wk`: total wages for the 4 quarters, divided by average employment, divided by 52. `wage_yoy`: same measure, year over year.
- `top_growth_sectors`: up to 3 private supersectors (agglvl 73, own 5) with the largest positive change in 4-quarter average jobs. Suppressed cells are skipped.
- `unemp`, `unemp_chg` (percentage points vs the same month a year earlier), `lf_yoy`, `labor_force`: LAUS.
- `jobs_here` (all OD rows with the work block in the county), `residents_employed` (all rows with the home block in the county, summed across every state's main + aux files), `in_commuters` = jobs_here − live-and-work-same-county, `out_commuters` = residents_employed − same, `inflow_ratio` = jobs_here / residents_employed (>1 means net daily inflow of workers), `in_commute_share`.
- `net_domestic_mig`, `net_intl_mig`, `net_mig_rate` (per 1,000), `pop`, `pop_change_pct`, `pop_change_5y_pct`, `net_domestic_mig_2021_25`: Census PEP V2025.
- `gross_inflow`, `gross_outflow`, `net_domestic_mig_irs`: IRS SOI individuals (n2 = exemptions), "Total Migration-US" rows.
- `top_origins`: the 5 largest IRS origin counties by people. Flows the IRS suppresses (under 20 returns) are invisible.
- `dom_mig_per_1k`: PEP net domestic migration per 1,000 residents.

## housing_demand_pressure (0-100)

Each input is converted to a national percentile (0-100) across all counties that have it, then combined as a weighted average:

| Component | Weight | Direction |
|---|---|---|
| `emp_yoy` (QCEW job growth) | 0.25 | higher = more pressure |
| `wage_yoy` (QCEW weekly wage growth) | 0.15 | higher |
| `dom_mig_per_1k` (PEP net domestic in-migration rate) | 0.25 | higher |
| `inflow_ratio` (LODES jobs here / employed residents) | 0.15 | higher. A job hub pulling commuters means latent demand to live closer |
| low unemployment (−`unemp`, LAUS) | 0.20 | lower unemployment = more pressure |

If a component is missing, the weights are renormalised over the components that exist. A score needs at least 0.6 of the total weight. `hdp_pctile` is the percentile rank of the blended score. Migration uses the per-capita rate rather than the raw count, so large counties do not dominate. One side effect is that small counties with noisy growth can rank high, so filter by `emp` or `pop` when building lead lists.

## Coverage and results

Build of 2026-11-24. File size 3.1 MB. 3,222 counties in `counties.json`.

| Field | Counties | Gaps |
|---|---|---|
| emp / emp_yoy / wage / top_growth_sectors (QCEW) | 3,221 | 15005 Kalawao HI has no QCEW data. emp_5y covers 3,212 |
| unemp / unemp_chg / lf_yoy (LAUS, Sep 2026) | 3,221 | Kalawao |
| jobs_here / commuters / inflow_ratio (LODES) | 3,144 | all 78 PR municipios, since PR is not in LODES |
| net_domestic_mig / pop_change_pct (PEP) | 3,144 | PR municipios are in a separate PEP file and not ingested |
| gross_inflow (IRS) | 3,091; top_origins 3,109 | very small counties are suppressed by the IRS |
| housing_demand_pressure | 3,221 | PR scores use only the QCEW and LAUS inputs (0.6 of the weight) |

Caveats: the LODES year is 2024, except MI (2021) and AK (2016), so AK borough changes after 2016 may leave gaps. LAUS rates for the latest month are preliminary. QCEW cells suppressed for disclosure are skipped, so a few small counties have fewer than 3 growth sectors.

### Top 20, all counties (small counties are noisy)
Kimball NE 91.1; Benton AR 90.3; Boone IN 88.6; Upton TX 87.4; Culebra PR 86.6; Powder River MT 86.2; Moore NC 85.9; Perkins NE 85.7; Ada ID 85.3; Sheridan WY 85.1; Custer ID 84.9; Loving TX 84.7; Harding SD 83.6; Scotland MO 82.9; Washington UT 82.7; Lincoln SD 82.7; Delaware OH 82.6; Trousdale TN 82.5; Wake NC 82.1; New Hanover NC 82.1.

### Top 20, counties with population of 50k or more (more useful for leads)
| County | FIPS | HDP | emp_yoy % | wage_yoy % | dom mig /1k | inflow | unemp % |
|---|---|---|---|---|---|---|---|
| Benton, AR | 05007 | 90.3 | 3.66 | 9.18 | 22.06 | 1.062 | 3.0 |
| Boone, IN | 18011 | 88.6 | 5.72 | 5.0 | 22.77 | 1.19 | 3.1 |
| Moore, NC | 37125 | 85.9 | 5.13 | 3.9 | 19.94 | 1.116 | 2.8 |
| Ada, ID | 16001 | 85.3 | 2.36 | 5.41 | 15.62 | 1.198 | 3.1 |
| Washington, UT | 49053 | 82.7 | 3.45 | 4.09 | 22.06 | 1.07 | 3.2 |
| Lincoln, SD | 46083 | 82.7 | 2.0 | 5.35 | 20.26 | 0.761 | 1.6 |
| Delaware, OH | 39041 | 82.6 | 2.07 | 5.97 | 15.36 | 0.949 | 3.1 |
| Wake, NC | 37183 | 82.1 | 1.96 | 4.57 | 10.17 | 1.228 | 2.8 |
| New Hanover, NC | 37129 | 82.1 | 1.53 | 5.26 | 9.17 | 1.238 | 2.8 |
| Chatham, NC | 37037 | 81.6 | 6.84 | 6.54 | 13.89 | 0.628 | 2.7 |
| Stanly, NC | 37167 | 80.7 | 1.59 | 5.55 | 17.36 | 0.805 | 2.6 |
| Bartow, GA | 13015 | 80.7 | 3.25 | 5.55 | 21.01 | 0.82 | 3.3 |
| Newton, MO | 29145 | 79.7 | 6.19 | 4.99 | 7.36 | 0.963 | 3.2 |
| Baldwin, AL | 01003 | 79.3 | 3.46 | 4.39 | 22.45 | 0.861 | 3.3 |
| Glynn, GA | 13127 | 79.1 | 2.87 | 4.54 | 6.53 | 1.131 | 3.0 |
| Madison, AL | 01089 | 79.0 | 2.01 | 3.53 | 20.11 | 1.246 | 3.2 |
| Bonneville, ID | 16019 | 78.8 | 3.03 | 3.72 | 8.89 | 1.205 | 3.0 |
| Bryan, GA | 13029 | 78.6 | 17.98 | 8.23 | 12.7 | 0.56 | 3.1 |
| Douglas, NV | 32005 | 78.4 | 5.39 | 4.5 | 16.24 | 0.96 | 3.7 |
| Licking, OH | 39089 | 77.3 | 4.91 | 6.33 | 5.39 | 0.885 | 3.3 |

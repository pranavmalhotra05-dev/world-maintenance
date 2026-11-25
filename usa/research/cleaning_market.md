# Cleaning-service market and area affluence (lane P4)

Script: `usa/cleaning_market.py` (cache `usa/_cache/cleaning/`).
Outputs: `web/usa/data/cleaning_market.json` (county table, ~2.8 MB), `web/usa/data/cleaning_market_tracts.json`
(tract lookup, ~3.5 MB: `[median_hh_income, share_hh_150k, share_hh_200k, households]`), and `meta.affluence` in every
`web/usa/blocks/<slug>/meta.json`. Aggregates only. No household-level income is used or inferred.

## Sources (all accepted: U.S. federal works, public domain, free, commercial use OK, no key)

| Source | What | Vintage |
|---|---|---|
| BLS QCEW open-data industry slices `data.bls.gov/cew/data/api/{yr}/{q}/industry/{naics}.csv` | estabs, monthly employment, wages, private ownership, county (agglvl 78) and state (58) | 2023Q1-2025Q4 (12 quarters) |
| Census Nonemployer Statistics `nonemp24co.zip` | sole proprietors / no-payroll firms, NAICS 56179/56172/56173 | 2024 |
| Census County Business Patterns `cbp23co.zip` | estabs/emp 561790, 561720, 561730, 238990, 459999, 423910 | 2023 |
| ACS 5-year table-based summary file B19001, B19013 (`www2.census.gov/.../table-based-SF/data/5YRData/`) | median HH income, HH income brackets; county, tract, block group, place | 2020-2024 |
| IRS SOI ZIP-code data `23zpallagi.csv` | AGI per return, share of returns with AGI >= $200k (agi_stub 6) | tax year 2023 |
| Census 2020 ZCTA5-tract relationship file | tract -> dominant ZCTA (largest land overlap) when a block has no ZIP | 2020 |
| EIA RECS 2020 public-use microdata (SWIMPOOL, NWEIGHT) | weighted state share of homes with a swimming pool | 2020 |

## Rejected

- IBISWorld, Statista: paid market research, proprietary licences.
- Angi / HomeAdvisor cost guides, Thumbtack data: proprietary site content, no open licence.
- Pool & Hot Tub Alliance and other industry-association pool counts: member or paid publications.
- Census Data API: needs a key for volume use. The same tables come from the bulk flat files instead.

## NAICS notes

- **561790** Other Services to Buildings and Dwellings: the NAICS 2022 index lists "Swimming pool cleaning and maintenance
  services" here. The code also covers chimney, gutter, duct and power-washing work, so treat it as an upper-bound proxy.
  There is no pool-only code.
- **561720** Janitorial Services, which includes window cleaning. **561730** Landscaping.
- **238990** All Other Specialty Trade Contractors includes outdoor pool construction. QCEW publishes no 238990 slice
  (HTTP 404), so this field comes from CBP 2023.
- **459999** All Other Miscellaneous Retailers includes "Swimming pool (above-ground) and supply stores". It is a mixed code.
- **423910** Sporting goods wholesale includes swimming pools and equipment. It is a mixed code.

## Pool counts

ACS has no pool variable. AHS publishes no county or tract pool counts. The only open federal pool measure found is the
RECS 2020 `SWIMPOOL` item, which is valid at state level only. Examples: AZ 19.2%, FL 18.8%, NV 12.0%, CA 7.8%.
Parcel-level pool flags, such as the Maricopa assessor's, belong to another lane.

## Method

- Annual figures are the QCEW 2025 mean of 4 quarters. Employment and wages are null when any quarter is suppressed
  (disclosure `N`). Establishment counts are always published.
- Seasonality is a Q1-Q4 employment index (mean = 1), averaged over the 2023-2025 years that have all 4 quarters
  disclosed. Where the county is suppressed, the state 561790 profile is used (`pool_svc_seasonality_src: "state"`).
- `svc_per_10k_households` = (QCEW 561790 estabs, or CBP if missing, plus NES 56179 nonemployers) per 10k ACS households.
- `pool_svc_emp_per_10k_households` = 561790 paid employment (QCEW, or CBP if suppressed) per 10k households.
- Block affluence: tract and block-group ACS figures, plus IRS ZIP figures. The ZIP is `meta.zip` or the tract's dominant
  ZCTA. The block group's median HH income is ranked against all block groups in its county (tract if the BG estimate is
  suppressed): >=90 very high, >=70 high, >=30 middle, else lower. BG medians carry large MOEs, which are stored as
  `block_group_moe`.

## Findings (first run, 2026-11-24)

- Coverage across 3,222 counties: QCEW 561790 estabs 1,796; QCEW 561790 emp disclosed 499 (899 including the CBP fallback);
  NES 56179 2,569; seasonality 3,222 (581 county-level); janitorial estabs 2,600; NES janitorial 3,106; landscaping estabs
  2,858; median HH income 3,221.
- In Maricopa (04013), QCEW 2025 561790 shows 788 estabs, 3,271 employees and $1,064/wk, plus 2,612 nonemployers. That is
  18.9 paid employees and 19.7 providers per 10k households. Seasonality is nearly flat (0.985/1.001/1.006/1.007), as
  expected for year-round desert pool service. Harris TX, by contrast, swings strongly (0.64/1.19/1.47/0.70).
- Places: Scottsdale city has median HH income $110,886, with 25.6% of households at $200k+. Phoenix city has $81,332 and
  12.5%. Across Maricopa's 1,009 tracts, the median tract income is $88.8k and the 90th percentile is $145.8k.
- The employment-per-household ranking is distorted by single large 561790 employers (e.g. Burnet TX, Howard MD). The
  establishment-plus-nonemployer density (`svc_per_10k_households`) is the cleaner "many small pool and exterior-cleaning
  operators" signal, and Florida dominates it.
- Block `scottsdale-az` (tract 04013216813, BG 040132168131, ZIP 85258): BG MHI $138,606 (±$54,765), tract $110,268;
  39.1% of BG households (28.0% of tract) at $200k+. IRS ZIP AGI is $195,373 per return, with 24.8% of returns at $200k+.
  County percentile is 82.2 (BG) / 69.5 (tract) -> **high**.
- Block `phoenix-az` (tract 04013103302, BG 040131033022, ZIP 85032): BG MHI $103,986 (±$30,720), tract $84,775;
  0% of the BG (3.5% of tract) at $200k+. ZIP AGI is $76,878 per return, with 5.8% at $200k+. County percentile is
  59.9 / 46.0 -> **middle**.

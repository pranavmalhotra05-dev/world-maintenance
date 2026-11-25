# Local rental assistance and landlord programs: 12 target cities

Checked 2026-11-24. The machine-readable version is `rental_assistance_local.json`, which has the same items plus the source PDFs and sample ZIP codes.
Verified means: **Y** = read on the official source this session; **P** = official source found, but a figure is missing or out of date; **N** = from a secondary source or memory, still to be verified.

## 1. Voucher payment standards (the most the agency will subsidize, gross rent incl. utilities)

| City | Agency | Year / effective | 2BR | 3BR | 4BR | Basis | Verified |
|---|---|---|---|---|---|---|---|
| Minneapolis | MPHA | 2027 (Jan 1) | 1,561 | 2,080 | 2,424 | 90-96% of metro FMR (below FMR due to funding) | Y |
| Saint Paul | Saint Paul PHA (stpha.org) | 2026 | 1,685 | 2,244 | 2,513 | flat | Y |
| Twin Cities suburbs | Metro HRA | current | 1,630-2,260 | 2,170-3,010 | 2,430-3,370 | by ZIP (API) | Y |
| Dallas | Dallas Housing Authority | 2026 | median 1,656 (873-2,610) | median 2,088 (1,170-3,285) | median 2,655 (1,341-4,176) | SAFMR by ZIP, 356 ZIPs | Y |
| Dallas (Walker high-opportunity) | DHA | 2026 | median 2,300 | median 2,900 | median 3,688 | 125% SAFMR | P |
| Austin | Austin Housing Authority | 2026 (Jan 1) | 1,949 | 2,484 | 2,882 | 100% of FY2025 FMR | P (column labels garbled) |
| Denver | Denver Housing Authority | 2026 (Jan 1) | 2,089 | 2,734 | 3,049 | flat | Y |
| Boulder | Boulder Housing Partners | - | ? | ? | ? | not found | N |
| Columbus | CMHA (cmhanet.com) | from 2026-08-01 | ~1,430* | ~1,715* | ~1,927* | 100% SAFMR by ZIP (cut from 110%) | P |
| Cincinnati | CMHA (cintimha.com) | 2027 | median 1,400 (1,040-2,160) | median 1,840 (1,370-2,840) | median 2,080 (1,540-3,210) | SAFMR by ZIP, 67 ZIPs | Y |
| Tulsa | Tulsa Housing Authority | 2024 (latest posted) | 1,128 | 1,489 | 1,733 | flat | P (out of date) |
| Kansas City | HAKC | 2026 | median 1,430 (1,133-2,244) | median 1,881 (1,485-2,926) | median 2,222 (1,551-3,476) | 110% SAFMR by ZIP | Y |
| Huntsville | Huntsville HA | 2026 (Jan 1) | 1,323 (ZIPs 1,293-1,990) | 1,707 | 2,130 | metro FMR + SAFMR for listed ZIPs | Y |
| Birmingham | HABD (bhmhousing.org) | 2025 (latest posted) | 1,356-2,280 | 1,704-2,844 | 1,896-3,228 | 120% SAFMR by ZIP group | P (may be out of date) |

\*Columbus: these are the FY2026 metro FMR figures. The ZIP table is a scanned PDF and was not read.
Funding signal: Columbus (cut to 100%) and Minneapolis (below FMR, 3BR+ frozen) are both pulling payment standards down because HUD funding is short. That caps the rent a voucher landlord can get in 2026-27.

## 2. Landlord incentives and guarantee funds

| Where | Program | What the landlord gets | Conditions | Verified |
|---|---|---|---|---|
| Minneapolis | MPHA Landlord Excellence Program | Damage claims beyond the deposit: Gold tier up to $2,500, Standard up to $1,250; Gold tier also gets biennial inspections and can show repairs with photos instead of a reinspection | Tier set each July 1 from inspection pass rate | Y |
| Minnesota | Property Owner Risk Mitigation Fund (Minnesota Housing) | Losses above the security deposit reimbursed through local grantees | Tenant must be FHPAP-eligible; grants run 2026-2030; caps set by each grantee | P |
| Dallas | Children First North Texas + Housing Connector | Signing bonus up to $1,250; up to 3 months emergency rent; damage up to $5,000 above the deposit; 1 month vacancy loss; eviction mediation | High-opportunity areas, families with children; check the address in DHA's Rental Property Incentives Finder | Y |
| Dallas | Walker vouchers | Payment standard 125% of SAFMR | Walker ZIPs | P |
| Boulder County | Landlord Assurance Fund | $500-$2,500 per damage claim after deposit and insurance, first 3 years of tenancy | Any voucher or rental assistance; lease on or after 2023-03-01 | Y |
| Boulder | BHP Owner Incentive Payment | One-time incentive; the amount is **not stated** | New owners, or owners back after more than 2 years; LIHTC and covenant units excluded | P |
| Colorado | DOH security deposit and damage payment process | Deposit and damage payments for units leased with state supportive-housing vouchers | DOH voucher programs | P |
| Tulsa | THA New Landlord Incentive Bonus | $1,000 per unit for the first 5 units (max $5,000) | No THA subsidy in the past 12 months; flyer is from 2020, so it may no longer be offered | P |
| Kansas City | KCMO $1M landlord mitigation fund | Help with the cost of accepting vouchers | Status after the 2025 state preemption is unknown | N |
| Austin, Denver, Columbus, Cincinnati, Huntsville, Birmingham, Saint Paul | - | No signing bonus or damage fund found on the agency's site; standard HAP payments only | - | P |

Inspections: Tulsa inspects every 2 years (HQS plus city code). HAKC inspects yearly at no cost, and units also need a KCMO Healthy Homes rental permit. CMHA Columbus inspects within 1-10 days of the RTA. MPHA inspects yearly, or every 2 years for Gold tier.
Where to list units:
- **Minneapolis:** HousingLink (free)
- **Kansas City and Columbus:** AffordableHousing.com; Columbus also has its own CMHA Community Listings
- **Denver:** Socialserve
- **Cincinnati:** its "List your unit" page
- **Dallas:** the DHA landlord portal

## 3. Source-of-income law (can a landlord refuse voucher holders?)

| Jurisdiction | Landlord may refuse vouchers? | Verified |
|---|---|---|
| Minneapolis | **No.** The city ordinance bans refusing renters with vouchers and "no Section 8" ads | Y |
| Minnesota statewide / Saint Paul | Unclear. The MHRA covers "public assistance", but Section 8 coverage is disputed in court; HF 2021 has not been enacted | P / N |
| Texas (Dallas, Austin) | **Yes.** LGC 250.007 stops cities from banning refusal; voluntary incentives are allowed | P |
| Colorado (Denver, Boulder) | **No.** Statewide protection since HB20-1332 | N (from memory) |
| Columbus | **No.** Ordinance 0494-2021, in force since 2021 | P |
| Cincinnati | **No** (2020 ordinance, as reported) | N |
| Ohio statewide | No state law; bill HB 150 proposed | P |
| Kansas City / Missouri | **Yes.** State HB 595 (signed July 2025) makes KC's 2024 ban unenforceable | P |
| Tulsa / Oklahoma | Yes, no protection found | N |
| Huntsville, Birmingham / Alabama | Yes, no protection found | N |

## 4. Open datasets

| Dataset | URL | Licence | Commercial use OK | Tested |
|---|---|---|---|---|
| Austin Affordable Housing Inventory: 2,431 properties with address, parcel, units by MFI band, affordability expiry, LIHTC, subsidies | https://data.austintexas.gov/resource/ifzc-3xz8.json | CC BY-SA 4.0 | yes (attribution, share-alike) | yes |
| Metro HRA payment standards by ZIP (JSON) | https://metrohra.metrocouncil.org/api/metrohra/payment-standards?q=ZIP | none stated | verify | yes |
| PHA ZIP payment-standard PDFs (Dallas, KC, Cincinnati as text; Huntsville, Birmingham as images) | see JSON | public agency docs | verify (facts) | yes |
| Met Council Publicly Subsidized Housing (7-county) | metrocouncil.org metadata page | verify | verify | no |
| MN DHS Licensing Lookup (adult foster care and similar settings; public licence records only, never resident data) | https://licensinglookup.dhs.state.mn.us/ | public record | verify | no |
| Austin Comprehensive Affordable Housing Directory | data.austintexas.gov/d/4syj-z4ky | verify | verify | no |

A Socrata catalog search turned up no voucher or affordable-unit datasets for the Dallas, KCMO, Cincinnati, Minneapolis, Saint Paul, Colorado or Texas portals. No Denver income-restricted dataset was found either.

## Not done / flagged
- Boulder payment standards; Tulsa 2025-26 and Birmingham 2026 schedules; the Columbus ZIP table (needs OCR).
- Group-home and sober-living licence lookups outside Minnesota; rapid rehousing and master-leasing programs by city; ERA successors other than Hennepin Rent Help and Denver TRUA (both N).
- MN Housing Support rates and the reported 2025 end of Housing Stabilization Services: not verified.

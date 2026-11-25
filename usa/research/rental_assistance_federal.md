# Federal rental assistance: who pays landlords, what a home must meet, and the data to score it

Generated 2026-11-24. Machine-readable companion: `rental_assistance_federal.json` (15 sources).
Builds on `federal_realestate.md`. FMR/SAFMR dollars, QCT/DDA, LIHTC, HCV-by-tract, public housing, the eGIS Picture snapshot, USDA eligibility and FEMA NFHL are already integrated there. Here they are only referenced.

All data below are US federal works (17 U.S.C. 105, no copyright), so **commercial use is OK** unless noted. Each was tested with one small request (ArcGIS query, or an HTTP range request for files).

---

## What changed in 2026 (read first)

- **Vouchers are funding-constrained.** HUD's [2026 Budget Management Letter](https://www.hud.gov/sites/default/files/PIH/documents/2026-Budget-Management-Letter.pdf) (Feb–Mar 2026) asks PHAs to stop issuing new vouchers (except HUD-VASH and FYI), pause new PBV commitments, and **cut payment standards, including ending approved exception payment standards and staying within the 90–110% basic range**. In practice, model the voucher ceiling at the low end (90–100%) unless a PHA's published schedule says otherwise.
- **Emergency Housing Vouchers are ending.** Families under EHV HAP contracts on Sept 30, 2026 move to Tenant Protection Vouchers by Dec 31, 2026 ([PIH 2026-25](https://www.hud.gov/sites/default/files/hudclips/documents/PIH-2026-25.pdf)). Closeout starts in early 2027. For landlords it continues as an ordinary voucher.
- **NSPIRE for vouchers is delayed to Feb 1, 2027** ([FR 2025-19070](https://www.federalregister.gov/documents/2025/09/30/2025-19070/economic-growth-regulatory-relief-and-consumer-protection-act-implementation-of-national-standards)), the third extension. Until then PHAs may still inspect HCV/PBV units under the old HQS ([24 CFR 982.401](https://www.ecfr.gov/current/title-24/part-982/section-982.401)). The checklist below meets both.

---

## 1. Housing Choice Voucher (Section 8, tenant-based)

**How the landlord gets paid.** The tenant finds the unit. The PHA inspects it, checks that the rent is reasonable, and signs a HAP contract (form HUD-52641, with tenancy addendum 52641-A) with the owner. The PHA then pays the housing assistance payment (HAP) **directly to the owner each month** ([982.451](https://www.ecfr.gov/current/title-24/part-982/section-982.451)). The tenant pays the rest, about 30% of adjusted income. At first lease-up the family's share may not exceed 40% of adjusted income if the rent is above the payment standard ([982.508](https://www.ecfr.gov/current/title-24/part-982/section-982.508)), which in practice caps the rent.

**Payment standards** ([982.503](https://www.ecfr.gov/current/title-24/part-982/section-982.503)):
- Basic range: **90–110% of the FMR**. In the 61 mandatory SAFMR metros (and PHAs that opt in) the base is the **ZIP-level Small Area FMR** (a).
- Exceptions: up to 110% of the SAFMR for a whole ZIP without HUD approval for non-SAFMR PHAs (d)(2); 110–120% after notifying HUD if success rate is under 75% or more than 40% of families are rent-burdened (d)(3); above that, HUD approval with market data (d)(4); up to 120% for a disability reasonable accommodation (d)(5). The 2026 letter above pushes PHAs back inside 90–110%.
- The subsidy uses the **lower** of the payment standard for the voucher size and for the unit size actually rented ([982.402(c)](https://www.ecfr.gov/current/title-24/part-982/section-982.402)). An extra bedroom adds no subsidy.

**Rent reasonableness** ([982.507](https://www.ecfr.gov/current/title-24/part-982/section-982.507)): the PHA compares the rent with similar unassisted units (location, size, type, quality, age, amenities, utilities). It may not be more than the owner charges for comparable unassisted units. The PHA re-checks before any rent increase and when FMR drops 10% or more. **Even below the payment standard, rent above local comps is refused.**

**Unit size / occupancy** ([982.402](https://www.ecfr.gov/current/title-24/part-982/section-982.402)): each PHA writes subsidy standards giving "the smallest number of bedrooms needed to house a family without overcrowding". These must agree with the space rule of **at least one bedroom or living/sleeping room per two persons** ([5.703(d)(5)](https://www.ecfr.gov/current/title-24/part-5/section-5.703); HQS 982.401(d)). A single person gets a 0 or 1BR voucher. Exceptions cover age, sex, health and disability. Families may rent a larger unit (no extra subsidy) or a smaller one that meets the space rule.

**Inspection, NSPIRE** ([24 CFR 5.703](https://www.ecfr.gov/current/title-24/part-5/section-5.703); mandatory for vouchers from Feb 1, 2027). The unit must have:
- hot and cold running water and safe drinking water in the kitchen and bathroom
- a private bathroom with sink, tub/shower and an interior flush toilet
- a living room and a kitchen area with sink, cooking appliance, refrigerator, prep and storage space
- **smoke alarms** on every level, inside each bedroom, and within 21 ft of each bedroom door (NFPA 72 satisfies this)
- **carbon-monoxide alarms** to HUD's Federal Register standard where there are fuel-burning appliances or an attached garage
- in each habitable room, 2 working outlets, or 1 outlet plus a permanent light
- GFCI outlets within 6 ft of water
- a **permanently installed heat source** in HUD-designated climate zones, with no unvented space heaters
- guardrails on any drop of 30 in or more
- fixed lights in the kitchen and bathrooms
- a safe path of egress from the unit door to the public way, (a)(2)

For HCV/PBV, passing does not depend on state or local code (f)(3). Under HQS, sleeping rooms and the living room need a window (982.401(f)). Most PHAs also expect a bedroom to have a window that opens for egress and a door, so check the local PHA guide.

**Lead-Safe Housing Rule** ([24 CFR 35 subpart M](https://www.ecfr.gov/current/title-24/part-35/subpart-M)): applies when the home was **built before 1978** and a **child under 6** will live there (0-bedroom and elderly-only units are exempt unless a child under 6 lives there, 35.115). A trained inspector visually assesses all painted surfaces. Deteriorated paint must be stabilized with clearance before move-in, or within 30 days if the family is already there (35.1215). If a child has an elevated blood lead level: environmental investigation within 15 days and hazard reduction within 30 days (35.1225).

**HAP contract basics:** the term matches the lease, usually a 1-year first lease (PHA may allow shorter, [982.309](https://www.ecfr.gov/current/title-24/part-982/section-982.309)). Payments stop if the unit fails inspection and is not fixed (abatement), or if the family moves out. Owners keep normal screening and eviction rights under state law plus the tenancy addendum.

**Landlord incentives HUD allows:** [Notice PIH 2022-18](https://www.hud.gov/sites/dfiles/OCHCO/documents/2022-18pihn.pdf) lets PHAs spend HCV admin fees on signing or retention bonuses, security and utility deposits, holding fees, application fees and landlord outreach. It is optional and per PHA (in the PHA's Administrative Plan). **No national list exists.** 2026 budget pressure makes these scarcer.

## 2. Other HUD programs that pay private landlords

| Program | Who pays the landlord / how rent is set | Notes and source |
|---|---|---|
| **HUD-VASH** | PHA, as an HCV, for homeless veterans referred by the VA, which provides case management | Same unit rules as HCV. Exempt from the 2026 issuance pause. [HUD-VASH](https://www.hud.gov/program_offices/public_indian_housing/programs/hcv/vash) |
| **Emergency Housing Vouchers** | PHA (HCV rules) | Ending: moving to TPVs by 12/31/2026 ([PIH 2026-25](https://www.hud.gov/sites/default/files/hudclips/documents/PIH-2026-25.pdf)). EHV/Stability payment-standard waivers rescinded by PIH 2026-20 |
| **Mainstream vouchers** | PHA (HCV rules) | Non-elderly people with disabilities (18–61) |
| **FUP / FYI** | PHA (HCV rules) | Families where housing is a factor in child-welfare cases, and youth 18–24 leaving foster care. FYI is exempt from the 2026 pause |
| **Section 811 PRA** | State housing agency, under a rental assistance contract with owners of multifamily properties | For people with disabilities, usually capped at 25% of units in a property. Single-family homes do not qualify. [811 PRA](https://www.hud.gov/program_offices/housing/mfh/grants/section811ptl) |
| **Project-Based Vouchers** | PHA attaches the voucher to the unit under a HAP contract after a **competitive solicitation** ([983.51](https://www.ecfr.gov/current/title-24/part-983/section-983.51)) | Rent no higher than 110% of FMR/SAFMR (less utility allowance) and rent-reasonable ([983.301](https://www.ecfr.gov/current/title-24/part-983/subpart-G)). Single-family homes can qualify. Watch PHA websites and procurement pages for RFPs. **2026: HUD asks PHAs to pause new PBV commitments.** |
| **CoC rental assistance / rapid re-housing** | CoC recipient (state, local government or PHA) pays the owner | Rent must be reasonable and no higher than the owner's comparable unassisted rents. Deposit up to 2 months, plus last month and up to 1 month for damages ([578.51](https://www.ecfr.gov/current/title-24/part-578/section-578.51)) |
| **ESG rapid re-housing** | Grantee pays the owner under a rental assistance agreement | **Rent no higher than FMR** and rent-reasonable; up to 24 months in 3 years ([576.106](https://www.ecfr.gov/current/title-24/part-576/section-576.106)) |
| **HOME TBRA** | Participating jurisdiction | Rent standard from local market or the HCV payment standard, rent-reasonable, contracts up to 24 months, deposits up to 2 months ([92.209](https://www.ecfr.gov/current/title-24/part-92/section-92.209)) |

## 3. USDA Rural Development

- **Section 515** direct loans finance rural apartment complexes. **Section 521 Rental Assistance** pays the owner the gap between the tenant's ~30% of adjusted income and the approved rent ([7 CFR 3560](https://www.ecfr.gov/current/title-7/subtitle-B/chapter-XXXV/part-3560); [RA fact sheet](https://www.rd.usda.gov/sites/default/files/fact-sheet/508_RD_FS_RHS_RentalAssistance.pdf)). It goes only to 515/514 properties, not single homes.
- **RD Vouchers (Section 542)**: tenants of 515 properties that prepay or leave the program get a voucher they can use at a private rental, including a single-family house. USDA's **Program Exit** file shows where these are appearing.

## 4. DoD Basic Allowance for Housing

BAH is **paid to the service member, not the landlord**. It is a rent benchmark, not a guarantee. It is set by Military Housing Area (ZIP → MHA), pay grade and dependency status ([37 U.S.C. 403](https://www.law.cornell.edu/uscode/text/37/403)). Annual files (Jan 1) are on the [DTMO BAH page](https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Lookup/), for example `.../Portals/119/Documents/BAH/BAH_Rates_All_Locations_All_Pay_Grades/ASCII/BAH-ASCII-2026.zip`. The zip contains `bahw26.txt` / `bahwo26.txt` (with/without dependents: MHA plus 27 pay-grade columns), `sorted_zipmha26.txt`, `mhanames26.txt` and a format PDF. travel.dod.mil blocks scripts (403), so fetch it yearly from a browser. The test copy came from the Internet Archive. Mid-year temporary increases happen (Abilene TX270, May 2026).

## 5. FEMA disaster housing

Under IHP direct assistance ([44 CFR 206.117](https://www.ecfr.gov/current/title-44/chapter-I/subchapter-D/part-206/subpart-D/section-206.117)), when a declaration authorises direct housing ([FEMA overview](https://www.fema.gov/blog/direct-lease-and-multi-family-lease-and-repair-programs)):
- **Direct Lease**: FEMA leases ready-to-rent homes (single-family, apartments, condos, townhouses) and pays the owner.
- **Multi-Family Lease and Repair**: FEMA pays repairs to **buildings with 3 or more units previously used as multifamily**, in return for leasing them to survivors. Repair cost is netted against lease value and cannot exceed it.
- Both require the property to be **outside the Special Flood Hazard Area**, complete and functional, and close to services.
- They are time-limited (usually up to 18 months from declaration).

There is **no open dataset of leased properties**. Use OpenFEMA declarations plus NFHL (both integrated) for a "could offer to FEMA" flag.

## 6. VA, SSA, HHS

- **VA SSVF** (Supportive Services for Veteran Families) grantees can pay rent and deposits directly to landlords for rapid re-housing ([38 CFR 62.34](https://www.ecfr.gov/current/title-38/chapter-I/part-62/subpart-D/section-62.34)). There is no property-level data, only the grantee list.
- **SSA**: no landlord program.
- **HHS** (TANF, Medicaid housing supports) is run by the states, so it is left to the state lane.

---

## Landlord qualification checklist (per house)

| # | Check | Rule | Data we can automate |
|---|---|---|---|
| 1 | **Rent ceiling**: gross rent (rent + tenant-paid utilities) ≤ payment standard for the smaller of voucher size and unit size | 982.503, 982.402(c), 982.508 (40% cap at first lease) | SAFMR (ZIP) or FMR × 0.9–1.1; SAFMR-mandatory flag (`SAFMR_Year` layer); 2026 advice to model ≤100% |
| 2 | **Rent reasonable**: no higher than comparable unassisted rents nearby | 982.507 | Our rent comps / ACS; Picture 2025 HUD spending per unit; project-based contract rents |
| 3 | **Bedrooms**: one bedroom or living/sleeping room per 2 persons; extra bedrooms earn nothing | 5.703(d)(5), 982.402 | Parcel bedroom count × ZIP-level demand for that voucher size (Picture) |
| 4 | **Bedroom definition**: a sleeping room with a window (HQS) and usually egress/door per the PHA | 982.401(d),(f); PHA inspection guide | Not automatable; flag for owner |
| 5 | **Condition** (NSPIRE/HQS): water, bathroom, kitchen and appliances, heat (no unvented heaters), outlets, GFCI, guardrails, fixed lights, egress path, no health and safety hazards | 5.703(d); 982.401 until 1/31/2027 | Year built and permit history as risk proxies only |
| 6 | **Smoke alarms** each level, each bedroom, within 21 ft of bedroom doors; **CO alarms** where fuel appliances or attached garage | 5.703(d)(3),(d)(6) | Owner checklist |
| 7 | **Lead**: built before 1978 and child under 6 → visual assessment, paint stabilization and clearance before move-in | 24 CFR 35.1215, 35.110/35.115 | Year built (assessor), HUD Deteriorated Paint Index (integrated) |
| 8 | **Location / jurisdiction**: inside a PHA service area (often 2–5 overlapping PHAs); voucher density | PHA admin plans | `Proposed_Housing_Authority_Service_Areas` + `Public_Housing_Authorities` + HCV by tract |
| 9 | **Owner eligibility**: not debarred and not related to the tenant (unless an accommodation); has the HAP contract and tenancy addendum | 982.306 | n/a |
| 10 | **Program-specific**: PBV needs a PHA solicitation win; 811 PRA and MLR need multifamily; FEMA needs outside SFHA plus a declaration; ESG needs rent ≤ FMR | 983.51; 811 PRA; 44 CFR 206.117; 576.106 | NFHL, OpenFEMA, PHA RFP pages (manual) |

**Per-house formula (HCV):** `max_contract_rent ≈ min(PS(PHA, ZIP, min(voucher_BR, unit_BR)), rent_reasonable_comp) − tenant_paid_utility_allowance`, where `PS = (SAFMR if mandatory/opt-in else FMR) × 0.90..1.10`. The PHA utility allowance schedule is not open as a national dataset (per-PHA PDFs).

---

## Top data sources to integrate per house

1. **SAFMR-mandatory flag** (`SAFMR_Year`, ZIP polygons, 61 metros) with the already-integrated SAFMR/FMR dollars → voucher rent ceiling band by bedroom.
2. **PHA service areas + PHA inventory** (eGIS) → which PHAs, their size and contact, joined on `PARTICIPANT_CODE`.
3. **Picture of Subsidized Households 2025** (ZIP/tract/PHA xlsx) → HUD spending per unit (typical HAP), waiting time, voucher counts by geography. Newer than the eGIS snapshot.
4. **Multifamily Assistance & Section 8 Contracts** (monthly xlsx) → local contract rents by bedroom and expiring contracts.
5. **DoD BAH** (annual ASCII zip) → ZIP → MHA → BAH benchmark near bases.
6. **CoC areas** (RRH/PSH bed counts) and **USDA 515 assets + Program Exit file** (rural).

## Not open, or no dataset

- **Exception payment standard approvals**: no national list. 2026 policy is to end them.
- **Each PHA's actual payment standard schedule and utility allowance schedule**: per-PHA PDFs and web pages, not a national file. This is the biggest gap. Capturing schedules for the largest PHAs by hand or by crawling would be the next step.
- **Landlord incentive programs**: rule only (PIH 2022-18); no PHA list.
- **PBV solicitations**: scattered across PHA procurement pages.
- **FEMA Direct Lease / MLR properties**: not published.
- **Voucher unit inspection results**: not public. Only REAC scores for multifamily are public.
- **DoD BAH**: open, but the site blocks scripts, so download manually.
- **HUD-USPS vacancy**: not usable (restricted licence, see federal_realestate).

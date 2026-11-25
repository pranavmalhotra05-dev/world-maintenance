# HOAs, occupancy, alternative living and places of worship (lane 4)

Last checked 2026-11-24. The structured records (67) are in `hoa_occupancy.json`. Each record has a `verified` flag. When it is `false`, the claim comes from background knowledge or a search snippet and was not read on the official page. Confirm it before showing it to users.

Model codes (IRC/IPMC) are copyrighted by the ICC. This file links to each city's adopted-code host and does not quote code text.

---

## 1. Is a house in an HOA? Data availability

### State registries

| State | Registry / source | Geometry | Licence / commercial | Tested |
|---|---|---|---|---|
| **TX** | TREC HOA Management Certificates, data.texas.gov `8auc-hzdi` (TREC filing required, Prop. Code 209.004 / 82.116) | none (name, county, city, ZIP, POA/COA, PDF) | no licence stated; state public info → *likely OK, verify* | ✓ 17,514 rows |
| **CO** | DRE HOA roster (Active/Expired, free) | none | public roster, no licence → *likely OK* | ◐ page OK; CSV needs ASP.NET postback |
| **CO** | SOS Business Entities `4ykn-tg5h`, used as a proxy for nonprofit HOA corporations | address only | Public Domain ✓ | ✓ 4,999 "HOMEOWNERS" entities in good standing |
| FL (ref) | DBPR condo public-records CSVs | address | FL public records → likely OK | ✓ Condo_CW.csv |
| VA (ref) | DPOR CIC Board registration / License Lookup | none | public | ✗ not tested |
| NV (ref) | NRED CIC lookup | none | public | ✗ not tested |
| MN, OH, MO, OK, AL | **No statewide HOA registry found.** Proxies are SOS nonprofit records and the county recorder | n/a | n/a | n/a |

### County and city layers (our counties)

| County / city | HOA layer? | Best proxy layer | Licence | Tested count |
|---|---|---|---|---|
| Dallas (city) | **✓ HOA_NA_Public (Assoc_Type HOA/NA)**, HomeownerAssociations (177) | NCTCOG subdivisions | disclaimer only | ✓ 301 |
| Travis / Austin | ✗ (Austin Community Registry has an HOA *category*, no polygons) | Travis subdivisions (3,672); Austin subdivision boundaries (16,742) | registry Public Domain; layers disclaimer only | ✓ |
| Boulder County | **◐ `HoaArcRef` Yes/No on platted areas** (unincorporated county) | same layer | **CC BY 4.0** | ✓ 1,208 (73 Yes) |
| Denver | ✗ (Aurora next door publishes HOA polygons, 298) | Denver RNOs are **not HOAs** | Public Domain | ✓ 187 RNOs |
| Hennepin | ✗ | MN subdivisions; Mapping Prejudice covenants (CC0, historic racial covenants only) | CC0 | ✓ 24,118 |
| Ramsey | ✗ | Ramsey subdivision plats | county open data | ✓ 5,833 |
| Franklin OH | ◐ **Condominium vs Subdivision** conveyance type | Auditor subdivision & condo boundaries | disclaimer | ✓ 14,067 |
| Hamilton OH | ✗ | CAGIS subdivisions | disclaimer | ✓ 11,455 |
| Tulsa | ✗ (city Neighborhoods layer returns 403) | Tulsa County subdivisions | none stated | ✓ 6,081 |
| Jackson MO / KCMO | ✗ not found | county parcel / plat | n/a | search only |
| Madison AL / Huntsville | ✗ not found | n/a | n/a | search only |
| Jefferson AL / Birmingham | ✗ not found | n/a | n/a | search only |

Civic bodies are **not HOAs** and must not be used as an HOA flag: Denver RNOs, Saint Paul District Councils, Minneapolis neighborhoods, Columbus Area Commissions, Cincinnati Community Councils, and Dallas "NA" polygons. They have no assessment or covenant power.

**Honest limits.** CC&Rs and declarations are private documents recorded at the county clerk or recorder. They are rarely available as bulk open text. A user can check one property this way:
1. Search the county recorder by subdivision name or developer (grantor).
2. Read the title commitment or the HOA resale certificate.
3. In Texas, search hoa.texas.gov by subdivision name or ZIP.
4. Ask the HOA. Owners have statutory record-access rights in TX, CO, MN, OH and AL.

Personal contact fields (Austin registry, Denver RNO, CO SOS agents) must be dropped on ingest.

## 2. HOA law by state (summary; see JSON for links)

| State | Statute | Notable HOA limits (owner protections) | Records access |
|---|---|---|---|
| TX | Prop. Code ch. 209, 202, 82 | solar, flags, drought landscaping/rain barrels, roofing, religious items, generators, pool fences, security measures. A rental/STR ban needs clear declaration language (Tarr 2018*) | 209.005 |
| CO | CCIOA 38-33.3* | flags/signs, xeriscape (no turf mandate), EV chargers, solar (38-30-168), ADUs (HB24-1152)* | 38-33.3-317 |
| MN | 515B; 500.215 / 500.216 / 500.217 | flags, solar (reasonable rules allowed), child care | 515B.3-118 |
| OH | ORC 5312 / 5311* | flag; fines need notice and a hearing | yes* |
| MO | RSMo 442.404; ch. 448 | political and for-sale signs, solar, chickens | condo only* |
| OK | 60 O.S. 501+ (condo)* | none confirmed | n/a* |
| AL | 35-20 (HOAs formed after 2016); 35-8A* | none confirmed | yes* |
| Federal | Flag Act 2005; FCC OTARD; FHA | flags, satellite dishes, no discriminatory covenants | n/a |

\* = unverified detail.

## 3. Alternative living by city

✓ legal · ◐ conditional or permit-only · ✗ not allowed as housing. Everything in this table is **unverified** unless the JSON record says otherwise. Check the code host before relying on it.

| City | Unrelated-adult cap | ADU | Tiny home on wheels | RV living on lot | Shed / tent living | Rooming / co-living |
|---|---|---|---|---|---|---|
| Minneapolis | ◐ district-based (3–5?) | ✓ | ✗ | ✗ | ✗ | ◐ zoned |
| Saint Paul | ◐ (~4?) | ✓ | ✗ | ✗ | ✗ | ◐ |
| Dallas | ◐ (4?) | ◐ special exception | ✗ | ✗ | ✗ | ◐ |
| Austin | ◐ (4–6?) | ✓ (HOME: up to 3 units) | ◐ RV parks / approved communities | ✗ | ✗ | ◐ |
| Denver | ✓ no relationship cap (HB24-1007) | ✓ | ◐ sanctioned villages | ✗ | ✗ | ◐ |
| Boulder | ✓ no relationship cap (HB24-1007); size-based | ✓ | ✗ | ✗ | ✗ | ◐ |
| Columbus | ◐ (3–4?) | ◐ (Zone In) | ✗ | ✗ | ✗ | ◐ |
| Cincinnati | ◐ (4?) | ✓ | ✗ | ✗ | ✗ | ◐ |
| Tulsa | ? | ◐ | ✗ | ✗ | ✗ | ◐ |
| Kansas City MO | ? | ◐ | ✗ | ✗ | ✗ | ◐ |
| Huntsville | ? | ◐ limited | ✗ | ✗ | ✗ | ◐ |
| Birmingham | ? | ◐ limited | ✗ | ✗ | ✗ | ◐ |

**Sleeping rooms, egress and bunk beds.** All 12 cities adopt ICC codes with amendments. The IPMC sets a minimum floor area per sleeping room, with a base area for one occupant and an added area for each extra occupant. IRC R310 requires an emergency escape opening in every sleeping room, including basements. No city rule specific to bunk beds was found. Occupancy is counted in people, so bunk beds don't raise the limit. Short-term-rental bunk rooms are governed by each city's STR occupancy caps.

Code hosts:
- Minneapolis: https://library.municode.com/mn/minneapolis
- Saint Paul: https://library.municode.com/mn/st._paul
- Dallas: https://codelibrary.amlegal.com/codes/dallas
- Austin: https://library.municode.com/tx/austin
- Denver: https://library.municode.com/co/denver
- Boulder: https://library.municode.com/co/boulder
- Columbus: https://library.municode.com/oh/columbus
- Cincinnati: https://library.municode.com/oh/cincinnati
- Tulsa: https://www.tulsaplanning.org/plans/TulsaZoningCode.pdf
- Kansas City: https://library.municode.com/mo/kansas_city
- Huntsville: https://library.municode.com/al/huntsville
- Birmingham: https://library.municode.com/al/birmingham

Municode and AmLegal return 403 to scripts but open in a browser.

## 4. Places of worship

- **RLUIPA (federal):** no substantial burden without a compelling interest; equal terms with secular assembly; no total or unreasonable exclusion. Enforced by DOJ: https://www.justice.gov/crt/religious-land-use-and-institutionalized-persons-act
- **City zoning:** religious assembly is generally permitted or conditional in residential and commercial districts. Housing on church land needs ordinary zoning approval (unverified per city).
- **"Yes in God's Backyard" status:**
  - **CO:** HB25-1169 was lost in the Senate in May 2025 (from a search summary, not read on leg.colorado.gov).
  - **TX:** SB 854 died in 2025 (verified on TLO). SB 840 (by-right multifamily on commercial land, effective 9/1/2025) is the usable route.
  - **MN:** SF 4915 was introduced in March 2026 and stayed in committee (verified on the Revisor's site).
  - **OH, OK, MO, AL:** none found.

## 5. What can realistically be a per-house flag

| Flag | Source | Confidence |
|---|---|---|
| `hoa_known` (inside a registered HOA polygon) | Dallas HOA_NA (Assoc_Type = HOA) | high where present; voluntary registry, so absence proves nothing |
| `hoa_plat_reference` | Boulder County `HoaArcRef` = Yes | medium-high, unincorporated Boulder only |
| `condo_association` | Franklin Auditor ConveyanceType = Condominium; FL DBPR style | high |
| `in_recorded_subdivision` (prior for CC&Rs) | Travis, Austin, Ramsey, Hamilton, Columbus and Tulsa subdivision polygons | weak: many old plats have no HOA |
| `tx_hoa_candidate` | TREC certificates matched by subdivision name + ZIP (fuzzy) | medium; needs name matching |
| `co_hoa_candidate` | DRE roster / SOS nonprofit names matched to subdivision name | low-medium |
| `historic_racial_covenant` | Mapping Prejudice (Hennepin, Ramsey) | high (historic fact only) |
| `occupancy_rule`, `adu_allowed` | city-level lookup table from section 3 | city-level only; parcel zoning still needed |
| `worship_site` | parcel land-use code + RLUIPA note | descriptive only |

Not integrable: actual CC&R text and rules (STR bans, fence or paint rules), current HOA dues, and whether a specific shed or RV is occupied.

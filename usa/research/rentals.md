# Rentals research: short-term and long-term rules and data (lane 3 of 4)

Last checked: 2026-11-24. Machine-readable records are in `rentals.json` (76 records). Each record has a `verified` field. Rules are summarised in my own words with links, so read the linked ordinance before relying on any number. This is not legal advice.

Verification key: **V** = read on an official page or tested by API this session. **P** = partly verified. **U** = from background knowledge or not opened, so re-check before production use.

## 1. Per-city STR comparison

| City | Permit/licence? | Primary residence required? | Caps / density | Max occupancy | Number on listing? | Hostel-style (per-bed) allowed? | Status |
|---|---|---|---|---|---|---|---|
| Minneapolis MN | Yes, for stays of 30 days or fewer (a room rented while the host stays home is exempt) | No, but non-homestead units are limited | 1 non-homestead licence per person; max 10% of units in buildings over 20 units (condos excluded) | 10 | Yes | No; room-sharing is exempt only when the host is present | V |
| Saint Paul MN | Yes, host and platform licences, renewed yearly (Ch. 379) | No | Units allowed per building depend on owner-occupancy (table on city page) | Zoning and building code | U | Not addressed; social events banned | V |
| Dallas TX | Registration ordinance (2023) is **enjoined** | No | Zoning ban in single-family districts (2023) is **enjoined**; Texas Supreme Court hears it Dec 3 2026 | U | U | U | P |
| Austin TX | Yes, 2-year licence (Ch. 4-23) | No (since 2025; tenants may operate with landlord's OK) | 2 per single-family site, otherwise 1,000 ft apart; greater of 1 unit or 10% (multifamily) / 25% (mixed-use) | 2 adults per bedroom + 2 (10pm-7am, LDC 25-2-795) (U: current text) | Yes, platform licence field since July 2026 | U | V |
| Denver CO | Yes, 1-29 days | **Yes** | Primary residence only | U | U (code requires it) | U | V |
| Boulder CO | Yes, 4-year rental licence plus annual affidavit | **Yes** (owner's principal residence) | Owner-occupied only; none on properties with an ADU; an accessory unit max 120 days a year | U | U | U | V |
| Columbus OH | Yes, STR permit (Ch. 598), BCI background check | U | U | U | U | U | P |
| Cincinnati OH | Yes, registration valid 3 years (Ch. 856) | No | None found | U | Yes | U | V |
| Tulsa OK | Yes, licence before advertising | No | None; allowed in all zones | **8 total**, can be lower under Title 55 s.404 | Yes, and posted in the unit | Events banned; bed & breakfast is a separate use | V |
| Kansas City MO | Yes, registration (Ch. 56 Art. VIII) | Resident type (270+ days a year) only, in residential zones | Non-resident STRs banned in residential zones unless grandfathered; 1,000 ft spacing for 1-2 unit buildings; 12.5% of units in 3+ unit buildings | U | Yes | U | V |
| Huntsville AL | Yes, business licence after zoning check (stays of 179 days or less) | No | Zoned like motels: mostly barred from residential zones (allowed in R2-B, C-3/4/5, industrial, research park, airport) | U | U | Only in commercial-type zones | V |
| Birmingham AL | **None yet**; draft ordinance pending (council vote set Oct 27 2026, outcome U) | U | Draft only | Draft only | Draft only | U | P |

Taxes (V unless marked): Austin hotel occupancy tax, collected by platforms with quarterly owner reports. Cincinnati 7% STR excise. Tulsa 5% lodging tax (5+ rooms). KCMO 7.5% transient tax. Huntsville 9% plus $2 per room-night. Denver lodger's tax and Boulder accommodations tax apply (rates U). State sales and lodging taxes come on top of these (U).

Penalties (V for Tulsa only): misdemeanor up to $1,200 per violation, civil penalty up to $1,000 a day, and revocation after 3 violations in 12 months. Other cities: U.

Minimum night stays: none of the target cities sets a minimum stay. The 30-day line (29 days in Denver and Boulder; 179 days in Huntsville) separates short-term from long-term rentals.

### State preemption (all U or from knowledge unless noted)

| State | STR preemption | Rent control | Other |
|---|---|---|---|
| TX | None by statute. *Zaatari v. Austin* (2019) struck Austin's non-owner-occupied ban; the Dallas case is pending at the Texas Supreme Court (V, docket) | Preempted (LGC 214.902) | Local voucher/source-of-income rules preempted (LGC 250.007) |
| CO | None | Preempted (CRS 38-12-301) | HB24-1007 bars family-based occupancy limits; HB24-1098 requires just cause for most evictions; HB20-1332 protects source of income |
| OH | None enacted | No enabling law | R.C. 5323: rental owners register with the county auditor |
| OK | None found | Reportedly preempted | — |
| MO | None enacted | Preempted (RSMo 441.043) | — |
| AL | None | Reportedly preempted | State lodging tax covers STRs |
| MN | None | Only by local ballot measure (471.9996); Saint Paul has rent stabilization | Cities may license rentals |

## 2. Data sources

| Source | Jurisdiction | Endpoint | Key fields | Licence | Commercial OK | Tested |
|---|---|---|---|---|---|---|
| Austin STR Locations | Austin | `datahub.austintexas.gov/resource/2fah-4p7e` | case_number, str_type, address, zip, district (3,813 rows) | Public Domain | **Yes** | V |
| Austin active STR counts | Austin | `datahub.austintexas.gov/resource/mydx-h5dy` | date, district, type, count | not stated | verify | V |
| Chicago Shared Housing Registrations | Chicago | `data.cityofchicago.org/resource/qfyy-956j` | reg number, **host_name**, address, status, ward (3,695) | Chicago portal terms | likely; verify | V |
| Chicago House-Share Prohibited Buildings | Chicago | `7bzs-jsyj` | buildings that opted out (2,414) | not stated | verify | V |
| Denver STR licences | Denver | `data.colorado.gov/resource/f3vc-vat3` (+ city ArcGIS layer 54) | licence, status, owner/lessee, address, parcel, dates (2,329) | Public Domain | **Yes** | V |
| Denver standalone lodging | Denver | `data.colorado.gov/resource/sshp-3kyq` | small lodging licences (166) | Public Domain | **Yes** | V |
| Nashville STR permits | Nashville | ArcGIS `Residential_Short_Term_Rental_Permits_view/0` | permit, owner-occupied or not, parcel, dates, lat/lon (17,669); **includes names and phone numbers** | Metro Open Data EO | likely; verify | V |
| Boulder Rental Housing Licenses | Boulder | `gis.bouldercolorado.gov/.../RentalHousingLicenses/MapServer/0` | RENTALTYPE (STR 1,021; festival 1,083; standard 9,278), units, parcel | not stated | U | V |
| Boulder County STR/VR | Unincorporated Boulder County | ArcGIS `Short_Term___Vacation_Rentals___Map_WFL1/0` | 153 records | County ToS | U | V |
| Dallas STR snapshot (Apr 2023) | Dallas | ArcGIS `MuniRevSTR/0` | zoning/council district only (3,187) | not stated | U | V |
| Tulsa licensed STR map | Tulsa | ArcGIS Experience | — | — | No (403) | V (blocked) |
| Minneapolis STR certificate lookup | Minneapolis | Tableau tool | single-address lookup | — | No (not bulk) | P |
| Saint Paul, KCMO, Columbus OH, Cincinnati, Birmingham, NYC | — | none found | — | — | — | not found |
| Huntsville | — | dashboards only | — | — | — | P |
| Seattle / New Orleans / Cambridge (outside target list) | — | `s7df-xba4` / `ufdg-ajws` / `wxgv-w968` | Seattle and Cambridge include bedroom/guest counts | PD / not stated / PDDL | Yes / verify / Yes | V |
| TX Hotel Tax Permits | Texas | `data.texas.gov/resource/hdzd-884n` | permit locations incl. STR operators (42,399) | Public Domain | **Yes** | V |
| TX Local HOT reports | Texas | `data.texas.gov/resource/er34-v24h` (FY22-25 series) | rate, revenue, spending by city/county | Public Domain | **Yes** | V |
| Inside Airbnb | global | insideairbnb.com | scraped listings | CC BY 4.0 stated, but guidelines say "do not republish"; claims fair use for non-commercial purposes; scraped against Airbnb terms; host names | **NO** | V (policy read) |
| AirDNA, Mashvisor, Key Data, AllTheRooms, PriceLabs, Rabbu | — | — | — | proprietary, paid | **NO** | — |
| HUD FMR / ACS B25064 | USA | huduser.gov | cross-reference only (federal lane) | US Gov PD | Yes | — |

**Watch-outs:** ArcGIS "Short Term Rentals <month>" layers by `todd.isaac_ccg` are **Columbus, Georgia**, not Ohio. `bhamgov.org` is **Birmingham, Michigan**, not Alabama. The Minneapolis rental licence layer was last updated in 2013. Nashville and Chicago include personal data, so drop name and phone fields.

## 3. Platform rules (company terms, not open data)

- **Airbnb.** Listing types are entire place, room, and shared room. The host resource now limits shared rooms to **professionally managed hostels with 24/7 staff** ([317](https://www.airbnb.com/help/article/317), [366](https://www.airbnb.com/resources/hosting-homes/a/how-to-select-the-type-of-space-guests-will-have-366)). The party/disturbance ban covers open-invite parties and "party-friendly" ads, and Airbnb may block risky bookings ([3345](https://www.airbnb.com/help/article/3345)). It strongly urges smoke and CO alarms and requires disclosing them in the listing; it gives one free alarm ([514](https://www.airbnb.com/help/article/514)). Hosts set max guests, describe each bed (bunks included), follow local law and enter registration numbers (U: exact article not re-read). **Hosts must comply:** accurate listing type and beds, guest cap, party ban, local registration.
- **Booking.com.** Partners are responsible for local licensing, permits, zoning, safety and tax, and licence/registration fields appear where required ([partner hub](https://partner.booking.com/en-us/compliance/accommodation-licensing/understanding-accommodation-licensing-including-short-term)). It supports a "Hostel" (bed-based) property type (U).
- **Vrbo.** Whole-home only (U). Registration numbers go in the dedicated dashboard field, and compliance is the host's job ([help](https://help.vrbo.com/articles/Add-or-update-your-property-registration-number)).
- **Effect for hostel-style or bunk-bed products:** per-bed rentals in an ordinary house conflict with both platform categories (Airbnb shared rooms are for staffed hostels; Vrbo is whole-home) and with STR licences, which cover a dwelling rented to one party. They usually need hostel or rooming-house zoning.

## 4. Long-term rental basics (U: from statute knowledge, links in JSON)

| State | Deposit cap / return deadline | Month-to-month notice | Rent control | Registration / inspection | Source-of-income protection |
|---|---|---|---|---|---|
| MN | none / 21 days + 1% interest | 1 rental period | ballot-only; Saint Paul stabilization | Minneapolis licence; Saint Paul Fire C of O | statewide (public assistance) |
| TX | none / 30 days | 1 month | preempted | Dallas registration; Austin Repeat Offender Program | local rules preempted |
| CO | none found / 1 month (up to 60 days by lease) | 21 days, plus just cause | preempted | Denver licence + inspection; Boulder licence + SmartRegs | statewide |
| OH | none / 30 days | 30 days | none | county auditor registry | no statewide rule |
| OK | none / 45 days | 30 days | reportedly preempted | none found | none |
| MO | 2 months / 30 days | 1 month | preempted | KCMO Healthy Homes | none statewide |
| AL | 1 month / 60 days | 30 days | reportedly preempted | none found | none |

## 5. Occupancy rules

- **IPMC s.404 (model):** a bedroom needs 70 sq ft for one occupant and 50 sq ft for each additional occupant. **IRC R310:** every sleeping room needs an egress window of at least 5.7 sq ft (5.0 at grade), 24 in high and 20 in wide, with the sill no higher than 44 in. Local adoptions differ (Minneapolis and Saint Paul use local housing codes; Tulsa uses Title 55, cited by its STR FAQ).
- **STR-specific caps:** Tulsa 8 total (V). Minneapolis 10 (V). Austin 2 adults per bedroom + 2 at night (snippet V; confirm current text).
- **Family / unrelated-persons limits:** Austin allows 4 unrelated adults (V, ordinance snippet). Colorado bans family-based caps statewide (HB24-1007, U). The other cities are U.
- **Bunk beds:** no target-city ordinance found with bunk-bed rules. The federal CPSC standard 16 CFR 1513 applies to the beds themselves.
- **Hostels / rooming houses:** separate uses, generally in commercial or mixed zones or needing special approval. Not covered by STR licences (Boulder County defines an STR as a "single booking party"). Per-city zoning tables were not checked (U).

## Could not verify (to do)

- Birmingham AL: result of the Oct 27 2026 council vote.
- Dallas: outcome after the Dec 3 2026 Texas Supreme Court argument.
- Exact occupancy formulas for Denver, Boulder, Columbus, Cincinnati, KCMO and Huntsville.
- Columbus Ch. 598 details: caps and owner-occupancy.
- Family definitions for every city except Austin.
- Locally adopted IPMC/IRC editions.
- Tax rates for Denver, Boulder, Minneapolis, Saint Paul, Columbus and Dallas.
- All long-term statute citations.
- Oklahoma and Alabama preemption citations.
- Licence terms for Chicago, Nashville and Boulder data.
- Whether NYC OSE registrations are published anywhere.

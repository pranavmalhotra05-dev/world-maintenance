# US federal real-estate open data: catalogue and integration plan

Generated 2026-11-24. Machine-readable version: `federal_realestate.json` (117 sources, every field per source).

**Licence basis.** Works made by the US federal government are not covered by US copyright (17 U.S.C. 105; see https://www.usa.gov/government-copyright). So the default for agency data is **commercial use OK**. Where an agency adds its own terms on top (an attribution line, use limits, registration), the JSON records them. The GSEs (Fannie Mae, Freddie Mac) and the St. Louis Fed's FRED are **not** federal agencies, and their data carries real restrictions.

**Counts by commercial_ok:** yes 102 · verify 12 · no 3
**Access test:** 109 of 117 returned data or a working landing or download page (script or built-in browser). 8 did not: HUD API without a token (401, as expected), three Census API endpoints that now require a key, FEMA IA Owners (timed out), EJScreen (taken down), the USGS landslide page (404 and ScienceBase 502), and VA REO (SSL error).

## Gotchas found while testing

- **Census API now needs a key.** Calls without a key return a "Missing Key" page. The key is free. The ACS table-based summary files on www2.census.gov still download without one.
- **HUD-USPS vacancy data cannot be used.** Only "governmental entities and non-profit organizations" may register, under a sublicense. → `no`. The HUD-USPS **ZIP crosswalk** is a separate product and is fine.
- **OpenFEMA terms are tighter than plain public domain.** They require a citation and a non-endorsement line. They also say data is "used solely for statistical research or as a reporting record" and must not be used to decide an individual's rights or benefits. NFIP claims and policies are therefore `verify`. Showing tract-level aggregates is the safe reading.
- **FRED is not a commercial channel.** Its terms FAQ allows "personal, non-commercial use". Pull BLS, Census and FHFA series from the source agency instead.
- **Freddie Mac loan-level data:** commercial redistribution needs a licence → `no`. PMMS rates "may be used with proper attribution" → `verify`.
- **NREL API host changed.** developer.nrel.gov no longer resolves. developer.nlr.gov serves PVWatts (DEMO_KEY worked). Its terms page has not been re-checked → `verify`.
- **EPA EJScreen is gone.** The page returns 404 since the 2025 takedown. Use only an archived copy of EPA's own files.
- **FCC broadband map:** the availability data is public, but the location Fabric is licensed from a contractor. Use county or tract summaries only → `verify`.
- **No national FAA noise contours are published.** Use the aviation layer of the BTS National Transportation Noise Map instead.
- **Bot blocks:** bts.gov, ffiec.gov, hazards.fema.gov (NRI downloads) and huduser.gov return 403/202 to plain scripts but load in a browser. Plan for a browser User-Agent or the ArcGIS mirrors.
- **HUD eGIS mirrors** (services.arcgis.com/VTyQ9soqVukalItT) carry about 300 layers, including QCT, DDA, FMR, SAFMR, LIHTC, HCV, CHAS, NMTC, OZ, conforming loan limits and radon. One ArcGIS query pattern covers most HUD needs. Layer IDs are not always 0 (for example CHAS=1, Promise Zones=14, R/ECAP=16, Permits=24); the JSON has the exact URLs.

## Top 15 to integrate first

(Census Geocoder, TIGER and the HUD-USPS crosswalk are join plumbing that come before all of these.)

| # | Source | Attach as | Why first |
|---|---|---|---|
| 1 | FEMA NFHL flood zones (+ LOMR layer) | per-house SFHA flag/zone/BFE; county % in SFHA | Matters to every buyer and lender, works at parcel scale, ArcGIS point query tested |
| 2 | FEMA National Risk Index | tract hail/wind/tornado expected loss; county choropleth | One dataset gives a roofer-relevant hazard score nationwide |
| 3 | NOAA SWDI NEXRAD hail signatures | per-house hail hits within 1 mi; county hail days | Core roofer lead signal, point-level, free JSON API |
| 4 | NOAA SPC hail/wind reports + NCEI Storm Events | per-house nearby reports; county damage $/yr | Verified ground reports and dollar damage that back up the radar signal |
| 5 | FEMA Disaster Declarations | county alert + 10-yr count | Real-time storm-event trigger for roofers and restoration firms |
| 6 | FHFA HPI tract + county (+ ZIP5) | tract/county appreciation 1/5/10 yr | The authoritative free price-trend series, down to tract level |
| 7 | FHFA UAD appraisal aggregates (tract) | tract median appraised value, condition/quality mix, age | Free tract-level home value and condition stats; little else like it is free |
| 8 | HUD FMR + Small Area FMR | ZIP rent by bedroom → per-house rent estimate | Rent baseline for investors and STR-vs-LTR comparison |
| 9 | Census ACS 5-yr housing tables | tract values: median value, rent, year built, owner-occupied % | Roof-age proxy (year built) plus owner-occupancy, both needed for targeting |
| 10 | Census Building Permits Survey (county/place) | county growth value | Supply and growth signal; plain text files, no key |
| 11 | CFPB HMDA | tract purchase/refi/home-improvement loan volume | Lender and agent demand. Home-improvement loans flag remodel activity |
| 12 | IRS SOI ZIP income | ZIP AGI, real-estate-tax deductions | Affluence targeting for roofers and investors |
| 13 | Incentive flags: HUD QCT/DDA + CDFI NMTC tracts + USDA RD eligibility (+ OZ already done) | tract/polygon flags; per-house USDA-eligible | Investor, developer and buyer financing hooks, all one ArcGIS pattern |
| 14 | USFS Wildfire Risk to Communities | county value + per-house raster sample | Insurance pressure in the West. Class-A roof upsell for roofers |
| 15 | BTS National Transportation Noise Map | per-house dB from road/rail/air | Buyer and STR quality signal at house level; fills the FAA noise gap |

Close runners-up: HUD Housing Choice Vouchers by tract, FHFA/HUD loan limits, NCES schools and district boundaries, EPA Superfund NPL proximity, and PVWatts per-roof-plane solar (best fit for the 3D roof block once its licence is confirmed).

## By agency (short)

**Treasury / IRS / CDFI Fund:** Opportunity Zones (reference only, integrated elsewhere); NMTC low-income-community tracts; CDFI Investment Areas and CIMS; CDFI locations; persistent-poverty tracts; IRS SOI ZIP income; IRS county migration. All `yes`.

**HUD:** FMR, SAFMR and Income Limits (files, or the HUD User API with a free token and the required notice "This product uses the HUD User Data API but is not endorsed or certified by HUD User"); QCT 2026; DDA 2026; LIHTC; Public Housing developments and buildings; HCV by tract; Multifamily Assisted; HUD-Insured Multifamily; FHA insurance-in-force by tract; HUD REO (SF_REO layer and HUDHomestore); CHAS; Promise Zones; LMISD; AFFH/R-ECAP indices (use the neutral indices only, to stay clear of fair-housing problems); Location Affordability Index; Deteriorated Paint Index; Picture of Subsidized Households; FHA Single-Family Snapshot; FHA loan limits; USPS crosswalk (`yes`); USPS vacancy (`no`).

**FHFA:** HPI master, county, tract and ZIP5; conforming loan limits (CSV); UAD aggregates (national to tract); NMDB aggregates; GSE Public Use Database. All `yes`, all tested.

**Census:** ACS (API key now required; bulk files keyless); Building Permits Survey; New Residential Construction; HVS; AHS; RHFS; Geocoder; TIGER; LODES; County Business Patterns (NAICS 238160 counts roofing contractors, a competitor-density measure); Planning Database; population estimates.

**BLS:** CPI rent and shelter; LAUS county unemployment; QCEW.

**USDA:** RD eligibility service (Eligibility/Eligibility MapServer); USDA REO listings; Forest Service Wildfire Risk to Communities; USDA rural rental assets (HUD mirror). **SBA:** HUBZones (map app; bulk link not pinned).

**FEMA:** NFHL with LOMR; NRI; NFIP claims, policies, penetration rates, multiple-loss and Community Status Book (all `verify` under OpenFEMA terms); disaster declarations; IA housing assistance; HMA projects; USA Structures footprints.

**EPA:** Brownfields (ACRES through Envirofacts FRS); Superfund NPL; UST/LUST Finder; Walkability Index; radon zones (HUD mirror); EJScreen (discontinued).

**DOT / FAA:** BTS noise map; HPMS AADT; NTAD (transit, airports, EV charging); FAA ADDS airports (no noise contours).

**USGS / NOAA:** seismic design maps API; earthquake catalog; landslide inventory (URL needs re-finding); 3DEP DEM; NOAA sea-level-rise inundation; SWDI hail; SPC reports; Storm Events; climate normals; NWS alerts API.

**DOE / NREL / EIA / FCC:** PVWatts/NSRDB (`verify`); OpenEI URDB (CC0 stated on page, `yes`); EIA electricity prices; LEAD energy burden; FCC broadband (`verify`).

**GSA / VA:** FRPP federal property; GSA real-estate sales and Auctions API; VA REO (contractor site, `verify`).

**Finance:** CFPB HMDA (`yes`); FFIEC census tract income levels (`yes`); FDIC branches (`yes`); FRED (`no`, non-commercial); Freddie PMMS (`verify`); Freddie loan-level (`no`); Fannie loan performance (`verify`).

**Other federal:** NCES school locations, district boundaries and SABS (2015-16, stale); BEA county income (free key); CDC SVI; CDC PLACES; FBI Crime Data Explorer (free api.data.gov key); NPS National Register (historic-district flag); USACE National Inventory of Dams.

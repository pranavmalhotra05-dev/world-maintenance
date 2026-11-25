# State, county and city real-estate open data (research lane 2 of 4)

Generated 2026-11-24. Machine-readable version: `state_local_realestate.json` (same folder).

Scope: government datasets for MN (Minneapolis, Saint Paul), TX (Dallas, Austin), CO (Denver, Boulder), OH (Columbus, Cincinnati), OK (Tulsa), MO (Kansas City), AL (Huntsville, Birmingham), plus a 50-state survey of open statewide parcel layers. Discovery used the Socrata catalog API, ArcGIS Online search (licenseInfo field), CKAN package_search (Birmingham) and direct REST walks (Huntsville, KCMO). Each 'tested' source answered one small request (count or 1 row).

## Summary counts

- Sources recorded: **147** (state 28, county 23, city 96)
- Tested live with one small request: **106**
- commercial_ok = yes: **71**; verify: **76**; no: **0**
- Already integrated (assessor / permits / 311 in `usa/adapters`): **16** (listed for completeness)
- States with an open statewide parcel layer: **23** yes, **11** partial (of 50)

Licence rule used: `yes` only when the dataset carries an explicit open licence (CC0, CC BY, Public Domain, PDDL, ODbL, ODC-BY) or the portal-wide open licence clearly applies. Disclaimer-only or 'no licence set' = `verify` (per project policy, do not ship until confirmed). Share-alike sources (Austin Affordable Housing CC BY-SA, KC demolished buildings ODbL) are flagged in notes.

## Key findings

- **Sales data is the biggest gap and has two open fixes:** Minneapolis republishes MN eCRV sales as CC0 (`Property_Sales_2021_to_2025`, 38.6k sales with price, downpayment, grantor/grantee), and Boulder County publishes daily bulk CSVs including *all* sales and mill levies. Texas remains a non-disclosure state (no sale prices).
- **Rental registries exist openly in 6 of our 12 cities:** Minneapolis (rental licences, CC0), Saint Paul (Fire CofO with A-E grade), Denver (39k rental licences, CC BY), Boulder (12k rental licences with energy compliance, CC0), Austin (Repeat Offender Program, PD), Kansas City and Austin/Denver STR permits.
- **Distress lists are open in most cities:** vacant/condemned registries (Minneapolis, Saint Paul, Cincinnati, Kansas City dangerous buildings + 26.9k vacant parcels), code cases (Minneapolis 381k, Cincinnati 774k, Austin 83k, KC 214k).
- **Statewide parcels** cover 4 of our 7 states openly or partially (CO, OH, MN opt-in, TX StratMap). OK, MO and AL have none; there we depend on county/city GIS.
- **Tulsa's code-enforcement / STR layers are referer-locked utility proxies (HTTP 403)** - not usable without the city's web app; request an open feed.
- **HOA registries:** Texas TREC HOA management certificates (17.5k rows, data.texas.gov) and KCMO Registered Homes Associations (87 polygons) are bulk; Colorado's DORA HOA registry is lookup-only (no bulk found).

## Per-city availability

Columns: OK = commercial_ok; T = tested; Int = integration grain. Status `already_integrated` rows are what the adapters pull today.

### Minneapolis, MN (Hennepin)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Minneapolis Property Sales 2021-2025 (eCRV)](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Property_Sales_2021_to_2025/FeatureServer/0) | city | sales history per house, comps, investor-buyer detection (LLC grantees), flips | yes | y | per-house | 1 | new |
| [Minneapolis Active Rental Licenses](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Active_Rental_Licenses/FeatureServer) | city | rental flag per house, landlord portfolio, licence tier = condition proxy | yes | n | per-house | 1 | new |
| [Minneapolis Vacant Building Registry properties](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/County_Parcels_VBR/FeatureServer/0) | city | distressed/vacant lead flag | verify | y | per-house | 1 | new |
| [Minneapolis Condemned by Boarding](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Condemned_by_Boarding/FeatureServer/0) | city | condemned flag (severe distress) | verify | y | per-house | 1 | new |
| [Minneapolis Primary Zoning](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Planning_Primary_Zoning/FeatureServer/0) | city | zoning per house; ADU / 3-unit eligibility (Minneapolis 2040 allows up to 3 units citywide) | yes | y | per-house | 1 | new |
| [Minneapolis code violation case inspections](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/CaseInspections/FeatureServer/0) | city | code-violation history per house (distress/rental quality) | yes | y | per-house | 1 | new |
| [Hennepin County Parcels (LAND_PROPERTY)](https://gis.hennepin.us/arcgis/rest/services/HennepinData/LAND_PROPERTY/MapServer/1) | county | homestead status (owner-occupied flag), school district, sale price/date, taxpayer address | verify | y | per-house | 1 | new |
| [Minneapolis Built Form overlay](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Planning_Zoning_Built_Form/FeatureServer/0) | city | development potential per lot | yes | n | per-house | 2 | new |
| [Minneapolis Public Works Special Assessments](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/PW_SpecialAssessments/FeatureServer/0) | city | pending/levied assessments per house (hidden cost for buyers) | yes | y | per-house | 2 | new |
| [Hennepin County tax-forfeited land auction lists](https://www.hennepin.us/residents/property/tax-forfeited-land) | county | tax-forfeit lead list | verify | n | per-house | 2 | new |
| [Minneapolis Zoning Overlay districts](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Planning_Zoning_Overlay/FeatureServer/0) | city | overlay constraints per house | yes | y | per-house | 3 | new |
| [Minneapolis HPC Local Landmarks](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/HPC_Landmarks/FeatureServer/0) | city | historic flag (renovation constraints, marketing) | yes | y | per-house | 3 | new |
| [Minneapolis Assessor parcel data 2026](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Assessing_Department_Parcel_Data_2026/FeatureServer/0) | city | core home attributes | yes | y | per-house | 1 | already_integrated |
| [Minneapolis Permits / 311](https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services) | city | renovation history | yes | y | per-house | 1 | already_integrated |

### Saint Paul, MN (Ramsey)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Saint Paul Vacant Buildings](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/VacantBuildings/FeatureServer/0) | city | vacant/distressed lead flag with severity | yes | y | per-house | 1 | new |
| [Saint Paul Fire Certificate of Occupancy - Residential](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/Certificate_of_Occupancy_-_Residential/FeatureServer/0) | city | rental flag + condition grade per house | verify | y | per-house | 1 | new |
| [Saint Paul Demolition Permits](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/Permit_Tppe_Demolition_Permit/FeatureServer/0) | city | teardown/redevelopment signal | yes | y | per-house | 2 | new |
| [Saint Paul General Overlay Zoning](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/General_Overlay_Zoning/FeatureServer/1) | city | overlay constraints | yes | y | per-house | 3 | new |
| [Saint Paul Historic Districts - Local](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/Historic_Districts_-_Local/FeatureServer/8) | city | historic flag | yes | y | per-house | 3 | new |
| [Tax Forfeit Properties 2022 via Ramsey County](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services/Tax_Forfeit_Properties_2022_via_Ramsey_County/FeatureServer/0) | county | tax-forfeit flag (stale snapshot; county list is live) | verify | y | per-house | 3 | new |
| [Ramsey County attributed parcels](https://gis.ramseycountymn.gov/server/rest/services/Cadastral/CAD_AttributedParcelPoly_ViewOnly/FeatureServer/0) | county | core attributes | verify | y | per-house | 1 | already_integrated |
| [Saint Paul Permits / 311](https://services1.arcgis.com/9meaaHE3uiba0zr8/arcgis/rest/services) | city | renovation history | yes | y | per-house | 1 | already_integrated |

### Dallas, TX

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Dallas Building Inspection Certificates of Occupancy](https://www.dallasopendata.com/resource/9qet-qt9e.json) | city | new construction / change-of-use per address | yes | y | per-house | 2 | new |
| [Dallas Code Violation Activities](https://www.dallasopendata.com/resource/hqes-3ct4.json) | city | code history (join via 311 SR id) | yes | y | per-house | 2 | new |
| [Dallas Base Zoning](https://www.dallasopendata.com/resource/f3t3-a5rd.json) | city | zoning per house; PD detection | verify | y | per-house | 2 | new |
| [Dallas Historic Overlay](https://www.dallasopendata.com/resource/5hqk-zir6.json) | city | historic flag | verify | y | per-house | 3 | new |
| [Dallas Demolition Delay Overlay](https://www.dallasopendata.com/resource/6isa-wxi3.json) | city | teardown constraint flag | verify | n | per-house | 3 | new |
| [Dallas Tax Parcels (city GIS)](https://www.dallasopendata.com/resource/rb52-np6d.json) | city | parcel geometry for DCAD join | verify | y | per-house | 3 | new |
| [Dallas County tax-foreclosure / sheriff sale lists](https://www.dallascounty.org/departments/tax/) | county | tax-foreclosure leads | verify | n | per-house | 3 | new |
| [DCAD certified appraisal data files](https://www.dallascad.org/DataProducts.aspx) | county | core attributes + homestead/senior exemption flags | verify | y | per-house | 1 | already_integrated |
| [Dallas permits / 311](https://www.dallasopendata.com/resource/e7gq-4sah.json) | city | renovation history | verify | y | per-house | 1 | already_integrated |

### Austin, TX (Travis)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [TCAD appraisal export (public information)](https://traviscad.org/publicinformation) | county | values + exemption flags | verify | n | per-house | 1 | new |
| [Austin Repeat Offender Program registrations](https://data.austintexas.gov/resource/86z9-i27i.json) | city | problem-landlord / distressed rental flag | yes | y | per-house | 1 | new |
| [Austin Code Complaint Cases](https://data.austintexas.gov/resource/6wtj-zbtb.json) | city | code-violation history per house | yes | y | per-house | 1 | new |
| [Austin Zoning By Address](https://data.austintexas.gov/resource/nbzi-qabm.json) | city | zoning per house (HOME initiative 3-unit eligibility on SF lots) | yes | y | per-house | 1 | new |
| [TCAD parcels (Travis County GIS)](https://gis.traviscountytx.gov/server1/rest/services/Boundaries_and_Jurisdictions/TCAD_public/MapServer/0) | county | parcel geometry + taxing entities per house | verify | y | per-house | 2 | new |
| [Austin Short Term Rental Locations](https://data.austintexas.gov/resource/2fah-4p7e.json) | city | STR flag per house | yes | y | per-house | 2 | new |
| [Austin Affordable Housing Inventory](https://data.austintexas.gov/resource/ifzc-3xz8.json) | city | affordable-unit flag, income-restricted housing map | yes | y | per-house | 2 | new |
| [Austin Future Land Use](https://data.austintexas.gov/resource/qu9j-w54e.json) | city | upzoning potential | verify | y | per-house | 3 | new |
| [Austin Local Historic District properties](https://services.arcgis.com/0L95CJ0VTaxqcmED/arcgis/rest/services/LHD_Properties/FeatureServer/0) | city | historic flag | verify | y | per-house | 3 | new |
| [Austin zoning review cases](https://services.arcgis.com/0L95CJ0VTaxqcmED/arcgis/rest/services/PLANNINGCADASTRE_zoning_review_case/FeatureServer/0) | city | pending rezoning near house | verify | y | block | 3 | new |
| [Austin permits / 311 / Land Database](https://data.austintexas.gov/resource/3syk-w9eu.json) | city | renovation history | yes | y | per-house | 1 | already_integrated |

### Denver, CO

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Denver Residential Rental Property Active Licenses](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_residential_rental_property_active_licenses/FeatureServer/52) | city | rental flag per house; landlord entity portfolios | yes | y | per-house | 1 | new |
| [Denver Zoning](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_ZONE_ZONING_A/FeatureServer) | city | zoning + ADU eligibility per house | yes | n | per-house | 1 | new |
| [City of Denver Short Term Rental Licenses (state portal copy)](https://data.colorado.gov/resource/f3vc-vat3.json) | city | STR flag per house (investor/rental signal) | yes | y | per-house | 2 | new |
| [Denver Short Term Rental Licenses (active/pending)](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_short_term_rental_licenses_active_or_pending_renewal/FeatureServer/54) | city | STR flag | yes | y | per-house | 2 | new |
| [Denver Demolition Permits](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_DEV_DEMOLITIONPERMIT_P/FeatureServer/318) | city | teardown/scrape-off signal | yes | y | per-house | 2 | new |
| [Denver Historic Landmark Districts](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_HIST_LANDMARKDISTRICT_A/FeatureServer/68) | city | historic flag | yes | y | per-house | 3 | new |
| [Denver Opportunity Zones 2026](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/Denver_Opportunity_Zones_2026/FeatureServer/0) | city | investor tax-incentive flag | verify | y | block | 3 | new |
| [Business Improvement Districts Denver](https://data.colorado.gov/resource/qtga-qgdf.json) | city | special-district assessment flag | yes | n | block | 3 | new |
| [Denver assessor parcels / permits / 311](https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services) | city | core | yes | y | per-house | 1 | already_integrated |

### Boulder, CO

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [City of Boulder Rental Housing Licenses](https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/RentalHousingLicenses/MapServer/0) | city | rental flag + energy compliance per house | yes | y | per-house | 1 | new |
| [City of Boulder Zoning Districts](https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/ZoningDistricts/MapServer/0) | city | zoning/ADU eligibility per house | yes | y | per-house | 1 | new |
| [City of Boulder Ownership Parcels](https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/Parcels/MapServer/0) | city | parcel geometry (CC0 alternative to county) | yes | n | per-house | 2 | new |
| [Zoning in Boulder County 2019 (state portal)](https://data.colorado.gov/resource/yvwz-kj9z.json) | county | zoning for unincorporated Boulder County homes | yes | y | per-house | 3 | new |
| [City of Boulder Future Land Use (BVCP)](https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/FutureLandUse/MapServer/0) | city | future land use per lot | yes | y | per-house | 3 | new |
| [City of Boulder Historic Districts and Landmarks](https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/HistoricLandmarks/MapServer/1) | city | historic flag | yes | n | per-house | 3 | new |
| [Boulder Permanently Affordable Rental Properties](https://services.arcgis.com/ePKBjXrBZ2vEEgWd/arcgis/rest/services/Permanently_Affordable_Rental_Property_Locations/FeatureServer/0) | city | deed-restricted housing flag | yes | y | per-house | 3 | new |
| [Boulder County Assessor bulk property data (CSV)](https://assessor.boco.solutions/ASR_PublicDataFiles/Sales.csv) | county | full sales history + mill levy per house; faster/more complete than CAMA views | verify | y | per-house | 1 | already_integrated (partially: sales via PropSearch_SALES view) |
| [Boulder CAMA / permits / code enforcement](https://maps.bouldercounty.org/arcgis/rest/services/CamaView/) | county | core | verify | y | per-house | 1 | already_integrated |

### Columbus, OH (Franklin)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Franklin County Auditor data downloads (parcel, CAMA, transfers, tax)](https://www.franklincountyauditor.com/real-estate/data-downloads) | county | owner-occupancy credit flag, sales, delinquency | verify | n | per-house | 1 | new |
| [Columbus Tax Increment Financing Areas](https://maps2.columbus.gov/arcgis/rest/services/Schemas/Development/MapServer/23) | city | TIF/abatement flag per house | yes | y | per-house | 2 | new |
| [Franklin County Auditor sales points](https://services1.arcgis.com/7r2Wl09a1Apy459r/arcgis/rest/services/ParcelSalePoints/FeatureServer/0) | county | sales/comps per house | verify | y | per-house | 2 | new |
| [Columbus Register of Historic Properties](https://maps2.columbus.gov/arcgis/rest/services/Schemas/Development/MapServer/0) | city | historic flag | yes | y | per-house | 3 | new |
| [Columbus Historic Districts](https://maps2.columbus.gov/arcgis/rest/services/Schemas/Development/MapServer/18) | city | historic flag | yes | y | per-house | 3 | new |
| [Columbus Subdivisions (plats)](https://maps2.columbus.gov/arcgis/rest/services/Schemas/PublicService/MapServer/10) | city | subdivision name per house; new-plat tracking | yes | y | per-house | 3 | new |
| [Columbus Proactive Code Enforcement Areas](https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/18) | city | neighbourhood distress flag | yes | y | block | 3 | new |
| [Columbus Zoning Variances / Planning & Commercial Overlays](https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/1) | city | overlay/variance flags per house | yes | n | per-house | 3 | new |
| [Central Ohio Community Improvement Corp (Franklin County Land Bank) inventory](https://www.coccic.org/) | county | land bank lead/flag | verify | n | per-house | 3 | new |
| [Columbus permits / code enforcement / zoning](https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/23) | city | core | yes | y | per-house | 1 | already_integrated |

### Cincinnati, OH (Hamilton)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Cincinnati Vacant & Foreclosed Property Registration](https://data.cincinnati-oh.gov/resource/w3jp-dfxy.json) | city | vacant/foreclosed lead flag | yes | y | per-house | 1 | new |
| [Cincinnati Code Enforcement](https://data.cincinnati-oh.gov/resource/cncm-znd6.json) | city | code history per house | yes | y | per-house | 1 | new |
| [Cincinnati Residential Tax Abatements (CRA)](https://data.cincinnati-oh.gov/resource/6ck4-ukva.json) | city | abatement flag (lower taxes for 10-15 yrs - big buyer value) | yes | y | per-house | 1 | new |
| [Hamilton County Auditor downloads (transfers, values, delinquent)](https://www.hamiltoncountyauditor.org/) | county | sales + delinquency per house | verify | n | per-house | 1 | new |
| [Cincinnati Inspections & Certificates (incl. COs)](https://data.cincinnati-oh.gov/resource/ivda-umw7.json) | city | CO / inspection history | yes | y | per-house | 2 | new |
| [Hamilton County Tax Districts](https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data_Feature_Collection/FeatureServer/39) | county | tax district per house -> effective rate | verify | y | per-house | 2 | new |
| [Cincinnati TIF Districts (Socrata)](https://data.cincinnati-oh.gov/resource/jy3n-t8je.json) | city | TIF flag | yes | n | per-house | 3 | new |
| [Cincinnati DCED Properties and RFPs](https://data.cincinnati-oh.gov/resource/gubx-p4qi.json) | city | city surplus property leads | yes | y | per-house | 3 | new |
| [Cincinnati Historic Districts](https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data_Feature_Collection/FeatureServer/5) | city | historic flag | verify | y | per-house | 3 | new |
| [Cincinnati/Hamilton Affordable Housing](https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Affordable_Housing/FeatureServer/0) | city | affordable housing flag | verify | y | per-house | 3 | new |
| [Hamilton County Land Reutilization Corp (Landbank) properties](https://cincinnatiport.org/) | county | land bank leads | verify | n | per-house | 3 | new |
| [Cincinnati permits / 311 / CAGIS parcels / zoning](https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Parcel_Polygons/FeatureServer/0) | city | core | verify | y | per-house | 1 | already_integrated |

### Tulsa, OK

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Tulsa Code Enforcement Cases / Short Term Rentals / Subdivisions](https://utility.arcgis.com/usrsvcs/servers/a3dbe5fc6bdb4cbc80f1431912f91565/rest/services/WIN/Code_Enforcement_Case_Status/MapServer/0) | city | code/STR flags | verify | y | per-house | 2 | new |
| [Tulsa HP (Historic Preservation) Zoning](https://services2.arcgis.com/XkZ90iCdbTJ9oNXl/arcgis/rest/services/HPZoning_OpenData/FeatureServer/0) | city | historic flag | verify | y | per-house | 3 | new |
| [Tulsa Special Area Overlays](https://services2.arcgis.com/XkZ90iCdbTJ9oNXl/arcgis/rest/services/SpecialAreaOverlay_OpenData/FeatureServer/0) | city | overlay flag | verify | y | per-house | 3 | new |
| [Tulsa County Treasurer resale / tax-sale list](https://www.tulsacounty.org/treasurer) | county | tax-sale leads | verify | n | per-house | 3 | new |
| [Tulsa Land Bank / Tulsa Development Authority properties](https://www.tulsadevelopmentauthority.org/) | city | land bank leads | verify | n | per-house | 3 | new |
| [Tulsa County Assessor / INCOG parcels + zoning](https://map11.incog.org/arcgis11wa/rest/services/Parcels_TulsaCo/FeatureServer/0) | county | core | verify | y | per-house | 1 | already_integrated |

### Kansas City, MO

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [KCMO Dangerous Buildings List](https://data.kcmo.org/resource/ax3m-jhxx.json) | city | severe distress / demolition-pending flag | yes | y | per-house | 1 | new |
| [Land Bank and Homesteading Authority Data](https://data.kcmo.org/resource/4257-6mtc.json) | city | land bank leads with asking price | verify | y | per-house | 1 | new |
| [KCMO Property Violations (EnerGov)](https://data.kcmo.org/resource/qvhx-kdqi.json) | city | code-violation history | yes | y | per-house | 1 | new |
| [KCMO Vacant Parcels](https://mapd.kcmo.org/kcgis/rest/services/DataLayers/MapServer/17) | city | vacant-lot leads + absentee owner | verify | y | per-house | 1 | new |
| [KCMO Code Cases - All (GIS)](https://mapd.kcmo.org/kcgis/rest/services/DataLayers/MapServer/9) | city | code history | verify | y | per-house | 2 | new |
| [KCMO Short Term Rental permits](https://mapd.kcmo.org/kcgis/rest/services/DataLayers/MapServer/34) | city | STR flag | verify | y | per-house | 2 | new |
| [KCMO Registered Homes Associations](https://mapd.kcmo.org/kcgis/rest/services/DataLayers/MapServer/5) | city | HOA flag per house | verify | y | per-house | 2 | new |
| [KCMO Incentive districts (TIF, urban renewal, CID, NID, enterprise, 353)](https://mapd.kcmo.org/kcgis/rest/services/DataLayers/MapServer/23) | city | TIF/abatement flag | verify | y | per-house | 2 | new |
| [KCMO Demolished Dangerous Buildings](https://data.kcmo.org/resource/u8q5-qug6.json) | city | vacant-lot-after-demo signal | yes | n | per-house | 3 | new |
| [KCMO Future Land Use / Zoning Overlays / Plats](https://mapd.kcmo.org/kcgis/rest/services/AGOL/MapServer/31) | city | future land use, overlays, subdivision names | verify | y | per-house | 3 | new |
| [KCMO historic registers (KC + National)](https://data.kcmo.org/resource/ygez-sude.json) | city | historic flag | verify | n | per-house | 3 | new |
| [Jackson County MO Land Trust parcels](https://services.arcgis.com/sbDzK061dd6DNPHv/arcgis/rest/services/Land_Trust_of_Jackson_County_Missouri_Parcels/FeatureServer/0) | county | tax-foreclosed inventory | verify | y | per-house | 3 | new |
| [KCMO parcels / zoning / permits / 311](https://mapd.kcmo.org/kcgis/rest/services/AGOL/MapServer/6) | city | core | verify | y | per-house | 1 | already_integrated |

### Huntsville, AL (Madison)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Huntsville Zoning Districts](https://maps.huntsvilleal.gov/server/rest/services/Zoning/ZoningDistricts/MapServer/0) | city | zoning per house | verify | y | per-house | 1 | new |
| [Huntsville New Subdivisions](https://maps.huntsvilleal.gov/server/rest/services/Housing/NewSubdivisions/MapServer/0) | city | new-construction pipeline per subdivision (builder leads) | verify | y | block | 1 | new |
| [Huntsville Elementary/Middle/High School Zones](https://maps.huntsvilleal.gov/server/rest/services/Education/ElementarySchoolZones/MapServer/0) | city | school zone per house | verify | y | per-house | 2 | new |
| [Huntsville Historic Districts & Local Landmarks](https://maps.huntsvilleal.gov/server/rest/services/Historic/HistoricDistricts/MapServer/0) | city | historic flag | verify | y | per-house | 3 | new |
| [Huntsville TIF Districts](https://maps.huntsvilleal.gov/server/rest/services/Districts/TaxIncrementFinancingDistricts/MapServer/0) | city | TIF flag | verify | y | per-house | 3 | new |
| [Huntsville Opportunity Zones / Priority Investment Neighborhoods](https://maps.huntsvilleal.gov/server/rest/services/Boundaries/OpportunityZones/MapServer/0) | city | investor flags | verify | n | block | 3 | new |
| [Madison County AL tax-delinquent / tax sale list](https://www.madisoncountyal.gov/departments/revenue-commissioner) | county | tax-lien leads | verify | n | per-house | 3 | new |
| [Huntsville assessor + permits (city GIS)](https://maps.huntsvilleal.gov/server/rest/services/Licenses/BuildingPermits/MapServer/0) | city | core | verify | y | per-house | 1 | already_integrated |

### Birmingham, AL (Jefferson)

| Dataset | Level | What it gives | OK | T | Int | Priority | Status |
|---|---|---|---|---|---|---|---|
| [Birmingham Zoning Boundaries](https://data.birminghamal.gov/dataset/birmingham-zoning-boundaries) | city | zoning per house | yes | n | per-house | 1 | new |
| [Birmingham Condemnation Notifications](https://data.birminghamal.gov/dataset/condemnation-notifications) | city | condemned flag | verify | n | per-house | 2 | new |
| [Birmingham Historic Districts](https://data.birminghamal.gov/dataset/birmingham-historic-districts) | city | historic flag | yes | n | per-house | 3 | new |
| [Birmingham Existing Land Use](https://data.birminghamal.gov/dataset/birmingham-existing-land-use) | city | vacant land / land-use per parcel | yes | n | per-house | 3 | new |
| [Birmingham Urban Renewal & Commercial Revitalization Districts](https://data.birminghamal.gov/dataset/birmingham-urban-renewal-districts) | city | incentive flags | yes | n | block | 3 | new |
| [Birmingham City Interest Real Property for Sale or Development](https://data.birminghamal.gov/dataset/city-interest-real-property-for-sale-or-developmen) | city | surplus property leads | yes | n | per-house | 3 | new |
| [Birmingham Opportunity Zones Property](https://data.birminghamal.gov/dataset/opportunity-zones-property) | city | OZ flag | yes | n | block | 3 | new |
| [Birmingham Land Bank Authority properties](https://birminghamlandbank.org/) | city | land bank leads | verify | n | per-house | 3 | new |
| [Jefferson County AL Tax Collector delinquent / tax sale list](https://www.jccal.org/Default.asp?ID=1128) | county | tax-lien leads | verify | n | per-house | 3 | new |
| [Jefferson County AL BOE parcels / Birmingham permits](https://jccgis.jccal.org/server/rest/services/BOE/JCParcelsWMS/MapServer/0) | county | core | verify | y | per-house | 1 | already_integrated |

## State-level sources (our 7 states)

| Dataset | State | What | OK | T | Priority |
|---|---|---|---|---|---|
| [Open Parcels in Minnesota (opt-in open data counties)](https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_mngeo/plan_parcels_open/FeatureServer/0) | MN | Statewide parcel polygons compiled by MnGeo from counties that opted in to open data (incl. Hennepin & Ramsey) | verify | y | 1 |
| [Government-Owned Lands, MN opt-in counties](https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_mngeo/plan_gov_own_open/FeatureServer/0) | MN | Parcels flagged as government owned (incl. tax-forfeited state-held land) for opt-in counties | verify | y | 3 |
| [Colorado Public Parcels (OIT composite)](https://gis.colorado.gov/public/rest/services/Address_and_Parcel/Colorado_Public_Parcels/FeatureServer/0) | CO | Statewide parcel composite from counties that share; owner, situs, zoning, land area, sub-division, county 'sh | verify | y | 1 |
| [Ohio Statewide Parcels Public View (OGRIP)](https://services2.arcgis.com/MlJ0G8iWUyC7jAmu/arcgis/rest/services/OhioStatewidePacels_full_view/FeatureServer/0) | OH | Statewide parcel polygons with state LUC, situs and mailing address, CAMA link | verify | y | 1 |
| [TxGIO StratMap Land Parcels](https://data.geographic.texas.gov/) | TX | Statewide parcels assembled from appraisal districts (CAD) by Texas Geographic Information Office; owner, situ | verify | n | 2 |
| [Texas Education Agency school district boundaries SY2024-25](https://services2.arcgis.com/5MVN2jsqIrNZD4tP/arcgis/rest/services/Map/FeatureServer/0) | TX | Polygons of all Texas ISDs | verify | y | 2 |
| [TREC Broker and Sales Agent License Holders](https://data.texas.gov/resource/s7ft-44qi.json) | TX | All Texas real estate license holders: type, number, status, county, sponsoring broker | verify | y | 3 |
| [TREC HOA Management Certificates](https://data.texas.gov/resource/8auc-hzdi.json) | TX | HOA management certificates filed with TREC: HOA name, county, city, zip, type | verify | y | 2 |
| [TALCB Appraiser File](https://data.texas.gov/resource/nmqp-zmi7.json) | TX | Licensed/certified real estate appraisers in Texas | verify | n | 3 |
| [TREC Inspector License Holders](https://data.texas.gov/resource/pkr9-9isv.json) | TX | Licensed home inspectors in Texas | verify | n | 3 |
| [Licensed Real Estate Professionals in Colorado (DORA)](https://data.colorado.gov/resource/4zse-6bnw.json) | CO | Brokers, appraisers, mortgage loan originators etc. with status and discipline fields | yes | y | 3 |
| [Colorado HOA Information & Resource Center registration](https://dre.colorado.gov/hoa-information-and-resource-center) | CO | State registry of HOAs (annual registration with DORA Division of Real Estate); searchable lookup, no bulk fee | verify | n | 2 |
| [Foreclosure Filings in Colorado (by county/quarter)](https://data.colorado.gov/resource/chpv-m4xq.json) | CO | Count of foreclosure filings per county per quarter (aggregate, ends 2016) | yes | y | 3 |
| [MN eCRV (electronic Certificate of Real Estate Value) public search](https://ecrvsearch.revenue.mn.gov/openCustomSearch) | MN | Every MN deed transfer >$3,000: sale price, date, buyer/seller type, property type; public custom search with  | verify | n | 1 |
| [MN Revenue property tax history & sales ratio data](https://www.revenue.state.mn.us/property-tax-history-data) | MN | County/city levies, tax rates, sales-ratio study files (xlsx) | verify | n | 3 |
| [Texas Comptroller PTAD tax rates & levies](https://comptroller.texas.gov/taxes/property-tax/rates/) | TX | Property tax rates for every county, city, school district, special district | verify | n | 2 |
| [Colorado DOLA mill levies / tax-area certifications](https://dlg.colorado.gov/property-tax-information) | CO | Mill levies by tax area & district (Division of Local Government) | verify | n | 2 |
| [Ohio Dept of Taxation property tax rate tables](https://tax.ohio.gov/researcher/tax-analysis/tax-data-series) | OH | Tax rates by taxing district, effective rates (tax data series) | verify | n | 3 |
| [MN Attorney General Landlords & Tenants handbook](https://www.ag.state.mn.us/consumer/handbooks/lt/default.asp) | MN | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [Texas AG Renter's Rights](https://www.texasattorneygeneral.gov/consumer-protection/home-real-estate-and-travel/renters-rights) | TX | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [Ohio Revised Code Ch. 5321 Landlords and Tenants](https://codes.ohio.gov/ohio-revised-code/chapter-5321) | OH | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [RSMo Chapter 441 Landlord and Tenant](https://revisor.mo.gov/main/OneChapter.aspx?chapter=441) | MO | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [Oklahoma Residential Landlord and Tenant Act (Title 41, OSCN)](https://www.oscn.net/applications/oscn/index.asp?ftdb=STOKST41) | OK | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [Alabama Uniform Residential Landlord and Tenant Act (Code of Alabama Title 35 Ch. 9A)](https://alison.legislature.state.al.us/code-of-alabama) | AL | Official landlord-tenant law text/summary (link only, do not copy) | yes | y | 3 |
| [Colorado landlord-tenant statutes (C.R.S. Title 38 Art. 12) via Colorado General Assembly](https://leg.colorado.gov/) | CO | Official landlord-tenant law text/summary (link only, do not copy) | yes | n | 3 |
| [Florida Statewide Parcels (DOR cadastral)](https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0) | FL | Statewide parcels with DOR NAL values, homestead values, use codes | verify | y | 3 |
| [Parcel Boundaries of Indiana Current (IndianaMap)](https://gisdata.in.gov/server/rest/services/Hosted/Parcel_Boundaries_of_Indiana_Current/FeatureServer/0) | IN | Statewide parcels w/ DLGF property class, address | yes | y | 3 |
| [Connecticut CAMA and Parcel Layer](https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer/0) | CT | Statewide parcels joined to CAMA (values, building attributes) | yes | y | 3 |

Landlord-tenant law rows are links only (official AG / legislature pages) - show as a link panel, never copy text.

## National survey: open statewide parcel layers

`yes` = one statewide (or near-statewide) layer free to download/query; `partial` = mosaic of only some counties; `no` = county-by-county. Rows marked T=y answered a live count query today. Licence column is what the item says; almost all are disclaimer-only, so treat as `verify` except CT (CC0) and IN (citation optional).

| State | Statewide open | Coverage / notes | Licence text | T | URL |
|---|---|---|---|---|---|
| AL | no | No statewide open parcel layer; counties run their own (often via vendors) | n/a | n |  |
| AK | no | Borough-level only | n/a | n |  |
| AZ | no | County assessors only | n/a | n |  |
| AR | yes | Arkansas GIS Office statewide parcels (assessor-sourced, most counties) | state disclaimer / public | n | https://gis.arkansas.gov/ |
| CA | no | County assessors only; many charge | n/a | n |  |
| CO | yes | Colorado Public Parcels (OIT) - counties that opt in; 2.6M parcels incl. Denver & Boulder | disclaimer only - verify | y | https://gis.colorado.gov/public/rest/services/Address_and_Parcel/Colorado_Public_Parcels/FeatureServer/0 |
| CT | yes | Statewide parcels + CAMA (1.32M) | CC0 | y | https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer/0 |
| DE | yes | FirstMap state parcels (Kent, New Castle, Sussex) | public, disclaimer | n | https://enterprise.firstmap.delaware.gov/arcgis/rest/services/PlanningCadastre/DE_StateParcels/FeatureServer |
| FL | yes | FDOR statewide cadastral with NAL values (10.9M) | disclaimer; county-owned - verify | y | https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0 |
| GA | no | County only | n/a | n |  |
| HI | yes | Statewide TMK parcels (384k) | State of Hawaii terms: public domain | y | https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer/25 |
| ID | partial | Some counties via INSIDE Idaho | varies | n |  |
| IL | no | County only (Cook etc. open) | n/a | n |  |
| IN | yes | IndianaMap statewide parcels (3.68M) w/ DLGF class | citation requested, not required | y | https://gisdata.in.gov/server/rest/services/Hosted/Parcel_Boundaries_of_Indiana_Current/FeatureServer/0 |
| IA | partial | Iowa Geospatial partial county mosaic | varies | n |  |
| KS | no | County only | n/a | n |  |
| KY | no | County/PVA only | n/a | n |  |
| LA | no | Parish assessors only | n/a | n |  |
| ME | partial | Town-level parcels via Maine GeoLibrary | varies | n |  |
| MD | yes | MD iMap parcel polygons + SDAT CAMA (statewide) | public | n | https://imap.maryland.gov/ |
| MA | yes | MassGIS L3 property tax parcels (2.56M) | MassGIS public (disclaimer) | y | https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0 |
| MI | no | County only | n/a | n |  |
| MN | partial | MnGeo Open Parcels - opt-in counties only (2.7M) incl. Hennepin & Ramsey | MnGeo disclaimer - verify | y | https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_mngeo/plan_parcels_open/FeatureServer/0 |
| MS | no | County only | n/a | n |  |
| MO | no | No statewide layer; MSDIS hosts a few counties | n/a | n |  |
| MT | yes | Montana Cadastral (MSL) statewide with CAMA | public, free | n | https://msl.mt.gov/geoinfo/cadastral/ |
| NE | partial | Some counties via NebraskaMAP | varies | n |  |
| NV | no | County only | n/a | n |  |
| NH | partial | NH GRANIT parcel mosaic (617k, not all towns) | planning use disclaimer | y | https://nhgeodata.unh.edu/hosting/rest/services/Hosted/CAD_ParcelMosaic/FeatureServer/1 |
| NJ | yes | Parcels + MOD-IV tax list composite (3.48M) | NJOGIS disclaimer (public) | y | https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels_Composite_NJ_WM/FeatureServer/0 |
| NM | partial | Some counties via RGIS | varies | n |  |
| NY | partial | NYS Tax Parcels Public - only counties that allow public sharing | NYS ITS disclaimer | y | https://services6.arcgis.com/EbVsqZ18sv1kVJ3k/arcgis/rest/services/NYS_Tax_Parcels_Public/FeatureServer/0 |
| NC | yes | NC OneMap statewide parcels (5.94M) | NC OneMap terms - verify | y | https://services.nconemap.gov/secure/rest/services/NC1Map_Parcels/MapServer/1 |
| ND | yes | NDGISHub parcels (719k) | state disclaimer | y | https://services1.arcgis.com/GOcSXpzwBHyk2nog/arcgis/rest/services/NDGISHUB_Parcels/FeatureServer/0 |
| OH | yes | OGRIP Ohio Statewide Parcels (6.31M) incl. Franklin & Hamilton | 'available for public use at own risk' - verify | y | https://services2.arcgis.com/MlJ0G8iWUyC7jAmu/arcgis/rest/services/OhioStatewidePacels_full_view/FeatureServer/0 |
| OK | no | No statewide layer; county assessors (Tulsa via INCOG) | n/a | n |  |
| OR | yes | ORMAP statewide taxlots | public | n | https://www.ormap.net/ |
| PA | partial | PASDA hosts some county parcels | varies | n |  |
| RI | partial | Town parcels via RIGIS (some) | varies | n |  |
| SC | no | County only | n/a | n |  |
| SD | no | County only | n/a | n |  |
| TN | yes | TN Comptroller property boundaries public use (2.14M; excludes Davidson/Shelby etc. self-maintained) | Comptroller disclaimer: graphical reference only - verify | y | https://services1.arcgis.com/YuVBSS7Y1of2Qud1/arcgis/rest/services/Tennessee_Property_Boundaries_Public_Use/FeatureServer/0 |
| TX | yes | TxGIO StratMap Land Parcels (CAD-sourced, download by county) | TxGIO public; CAD terms vary - verify | n | https://tnris.org/stratmap/land-parcels/ |
| UT | yes | UGRC LIR parcels by county (e.g. Salt Lake 399k) | UGRC 'as is' (public) | y | https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/Parcels_SaltLake_LIR/FeatureServer/0 |
| VT | yes | VCGI statewide parcels (VTPARCELS) | VCGI disclaimer (public) | y | https://services1.arcgis.com/BkFxaEFNwHqX3tAw/arcgis/rest/services/FS_VCGI_VTPARCELS_WM_NOCACHE_v2/FeatureServer/0 |
| VA | yes | VGIN statewide parcels (4.17M) | use at own risk disclaimer | y | https://vginmaps.vdem.virginia.gov/arcgis/rest/services/VA_Base_Layers/VA_Parcels/FeatureServer/0 |
| WA | yes | WA Geoportal Current Parcels (3.32M) | informational disclaimer | y | https://services.arcgis.com/jsIt88o09Q0r1j8h/arcgis/rest/services/Current_Parcels/FeatureServer/0 |
| WV | yes | WV statewide parcels (WVGISTC / Tax Dept) | public | n | https://www.mapwv.gov/ |
| WI | yes | Wisconsin Statewide Parcel Map (V-series, annual) | public, free | n | https://www.sco.wisc.edu/parcels/data/ |
| WY | partial | Some counties via WyGISC | varies | n |  |

## Top 20 to integrate next

| # | Source | Why |
|---|---|---|
| 1 | Minneapolis Property Sales 2021-2025 (eCRV) | CC0, 38.6k sales; first open sale-price feed in our set; also gives investor-buyer signal. |
| 2 | Boulder County Assessor bulk CSVs (Sales, Buildings, Values, Tax_Area_Auth_Mill) | Daily, complete sales history + mill levy; replaces slow CAMA views. Confirm CC BY 4.0 (AGOL item) vs 'internal use' page disclaimer. |
| 3 | Denver Residential Rental Property Active Licenses | CC BY, 39k licences with parcel number - direct per-house rental flag. |
| 4 | Minneapolis Active Rental Licenses (Hub CSV) | CC0; licence tier is a condition proxy. REST path broken - use Hub download. |
| 5 | Saint Paul Fire Certificate of Occupancy - Residential | 12.4k rental CofOs with A-E grade; confirm Public Domain on item. |
| 6 | City of Boulder Rental Housing Licenses | CC0, 12.2k, includes SmartRegs energy compliance (roof/insulation sales angle). |
| 7 | Cincinnati Vacant & Foreclosed Property Registration | Public Domain, daily; strong distress flag. |
| 8 | KCMO Dangerous Buildings List | Public Domain; demolition-pending flag. |
| 9 | Saint Paul Vacant Buildings | Public Domain; category 1-3 severity. |
| 10 | Minneapolis code violation CaseInspections | CC0, 381k inspections with APN. |
| 11 | Cincinnati Code Enforcement | Public Domain, 774k cases. |
| 12 | Austin Code Complaint Cases + Repeat Offender Program | Public Domain; code history + problem-landlord flag. |
| 13 | Cincinnati Residential Tax Abatements (CRA) | Public Domain; abatement = big buyer value signal. |
| 14 | Hennepin County Parcels (LAND_PROPERTY) | Homestead (owner-occupied) flag, school district, sale fields - verify licence. |
| 15 | Colorado Public Parcels (OIT) | One API for all CO counties that share; owner + zoning; verify licence. |
| 16 | Ohio Statewide Parcels (OGRIP) | Owner mailing address -> absentee-owner flag for Columbus/Cincinnati; verify licence. |
| 17 | Austin Zoning By Address + Minneapolis Primary Zoning + Boulder Zoning | Open zoning per house -> ADU / multi-unit eligibility (Austin HOME, Mpls 2040). |
| 18 | Denver + Austin + KCMO short-term rental permits | STR flag per parcel (Denver/Austin open; KC verify). |
| 19 | Minneapolis PW Special Assessments | CC0; pending assessments are a hidden buyer cost. |
| 20 | TREC HOA Management Certificates + KCMO Registered Homes Associations | HOA presence flags (verify licences). |

## Not usable / gaps

- Tulsa code enforcement, STR, historic preservation, subdivisions: utility.arcgis.com proxies return 403 (referer-locked).
- Columbus short-term rental licences: proxy 403.
- Metropolitan Council Parcels2023Hennepin service not started.
- Franklin County Auditor downloads and TX Comptroller tax-rate pages block scripted requests (403) - fetch manually in a browser.
- Texas: no sale prices (non-disclosure state). MN eCRV statewide is search/export only, no bulk open licence.
- Sheriff/tax sale and foreclosure lists (Dallas, Tulsa, Madison, Jefferson counties) are PDFs/notices without licences - low automation value; recorded as priority 3.
- Colorado HOA registry (DORA): lookup only; a bulk list probably needs a CORA records request.

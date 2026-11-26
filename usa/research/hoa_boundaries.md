# HOA boundaries: federal, state and county sources (lane HB)

Checked 2026-11-25. The records are in `hoa_boundaries.json`. This lane extends `hoa_occupancy.md` and `parcels_hoa.md`; it does not repeat their sources.

Verdicts:
- **green**: public domain, CC0, CC BY, ODbL, or an explicit statement that commercial use is allowed.
- **grey**: the only terms are a disclaimer, or no licence is stated.
- **red**: internal use only, non-commercial, no resale, or paid.

**Caveat:** the shared web-search budget ran out partway through. Rows marked *not searched* still need a pass.

## Direct answer

There is **no nationwide or federal database of HOAs or their boundaries.**

- **Federal:** the only federal list is the IRS EO BMF. It is public domain but has no geometry, and it misses most HOAs.
- **States:** about 8 states register associations: TX, VA, CO, NV, UT, CA, HI, and FL (condos only). **None of these registries publishes geometry.** All of them are grey or lookup-only.
- **True HOA polygons** exist only at city or county level, and almost all of those are grey or red.
- **Green HOA or condo polygons** found so far:
  - DC Condo Approval Lots (CC BY 4.0)
  - Boulder County HoaArcRef (CC BY 4.0)
  - Franklin and Hamilton OH condo polygons (Public Domain)
- **Green subdivision-plat proxies:** Sacramento County (CC0), Scottsdale (ODbL), Spokane County (CC BY), and Gilbert (no-limitations statement).

## 1. Federal

| Source | Geometry | How it identifies HOAs | Licence | Verdict |
|---|---|---|---|---|
| IRS EO BMF (monthly CSV by state, 1,984,891 records) | no (filing/HQ address only, often the management company) | ACTIVITY code **401** "Homeowners' association"; NTEE **L50** "Homeowners & Tenants Associations"; SUBSECTION 04 (and some 07) | No licence on the page. It is a US Government work, public domain under 17 U.S.C. 105. | **green** (drop the ICO "in care of" person-name field) |
| Census TIGER / ACS / AHS | no | AHS asks about HOA fees, but only as microdata | n/a | n/a |
| HUD | no | FHA condo approval lookup only | not checked | n/a |

**Coverage gap in the BMF:** HOAs that elect IRC section 528 file Form 1120-H without applying for exemption, so they never enter the BMF. The BMF therefore holds only the minority of HOAs with a 501(c) determination.

## 2. State registries

| State | Registry | Geometry | Format | Licence or terms | Verdict |
|---|---|---|---|---|---|
| TX | TREC HOA certificates, data.texas.gov `8auc-hzdi` (17,531 rows) | no | Socrata | metadata `license: null` | grey |
| VA | DPOR CIC Board list `0550__crnt.txt` (1.16 MB; condos are list 0517) | no | tab-delimited TXT, updated every ~5 business days | "Regulant lists* are provided free of charge in electronic format." No reuse terms. | grey (drop the email and individual-name columns) |
| FL | DBPR condo and co-op CSV extracts by region (Broward is in `condo_PB.csv`) | no | CSV | points to a "ReadMe/Disclaimer" only; no HOA (ch. 720) file | grey |
| CO | DRE HOA roster | no | page returned 403 to the fetch | none seen | grey |
| CO | SOS Business Entities `4ykn-tg5h` (proxy) | no | Socrata | Public Domain | green (proxy only) |
| NV | NRED CIC registration (Form 562) | no | lookup only; bulk list needs a records request | n/a | grey |
| UT | HOA Ombudsman registry (mandatory, Utah Code 57-8a-105; new system since Sept 2025) | no | search only; bulk list needs a GRAMA request | n/a | grey |
| CA | SOS Form SI-CID (Civil Code 5405) | no | bizfile per-entity lookup only | n/a | grey |
| HI | REC condo association registration (HRS 514B) | no | no roster found | n/a | grey |
| DE | CIC Ombudsperson | no | no registry; registration is still under study | n/a | n/a |
| CT, MD (statewide), IL, OR, AZ, WA, GA, MI, NY, PA, MA, MN, OH, MO, OK, AL, TN, LA, NE, AK | none found | n/a | proxy: SOS nonprofit records | n/a | n/a |

## 3. County and city layers

### Green

| Area | Layer | Kind | Count | Licence quote |
|---|---|---|---|---|
| DC | [Condo Approval Lots, MapServer/44](https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Property_and_Land/MapServer/44), plus the Condo Regime table (/72, 3,334 rows) | condo polygon | 4,863 | CC-BY-4.0, "Attribution 4.0 International" (opendata.dc.gov API) |
| Boulder County | LUC_PlattedAreas HoaArcRef (already in `parcels_hoa.py`) | plat with recorded-HOA flag | 1,208 (73 Yes) | CC BY 4.0 |
| Franklin and Hamilton OH | condo polygons and plats (already in `parcels_hoa.py`) | condo polygon + plat | n/a | Public Domain / CC0 |
| Sacramento County | [Filed Subdivision Maps](https://services1.arcgis.com/5NARefyPVtAeuJPU/arcgis/rest/services/RecordedSubdivisionMaps/FeatureServer/0) | plat | 8,113 | "This work is licensed under a Creative Commons Public Domain Dedication License" |
| Scottsdale | [OpenData/MapServer/7 Subdivisions](https://maps.scottsdaleaz.gov/arcgis/rest/services/OpenData/MapServer/7) | plat | 4,544 | "...made available here under the Open Database License." (share-alike applies) |
| Spokane County | [OpenData/Property/MapServer/3 Plats](https://gismo.spokanecounty.org/arcgis/rest/services/OpenData/Property/MapServer/3) | plat | 6,971 | "CC BY", plus an RCW 42.56 clause on lists of individuals (not relevant to plats) |
| Gilbert AZ | [Recorded_Plats](https://maps.gilbertaz.gov/arcgis/rest/services/OD/Growth_Development_Maps_1/MapServer/7) | plat | 2,307 | "There are no access and use limitations for this item." (green-leaning; owner sign-off needed) |

### Grey: real HOA polygons that we cannot use

These layers have real HOA polygons, but every one is disclaimer-only or has no licence. Several also include contact fields.

| Area | Layer | HOA count |
|---|---|---|
| Clark County NV | AdminServ/Clark_County_HOA | 2,123 |
| Las Vegas | CLV_NeighAreas, NTYPE='HOA' | 483 |
| Henderson NV | public/HOAs | 366 |
| Orange County FL | AGOL_Open_Data/34, TYPE='mandatory' | 1,222 |
| Hillsborough FL | HOA/NOA | 995 |
| Seminole FL | HOA.gdb (2013) | not counted |
| Montgomery County MD | general/hoa | 1,143 |
| Gilbert | GilbertNeighborhoods | 381 |
| Peoria AZ | HOAs_and_Neighborhoods/6 | 243 |
| Goodyear AZ | Catalis_Public_Engagement/1 | 405 |
| Tempe AZ | Residential Association (licence conflicting) | not counted |
| Tulsa | Neighborhoods_OpenData, type HOA | 79 |
| Kansas City MO | pvda-3rmd, Homes Association (data from 2021) | 124 |
| Travis County | Community Registry (self-drawn) | 535 |
| Houston | Civic Clubs/HOA's | 462 |
| Elk Grove CA | EG_HOA | 26 |
| Dallas | HOA_NA (from the prior lane) | n/a |

### Red

| Area | Layer | Licence quote |
|---|---|---|
| Mesa | HOAs (398) | "for official city use only" |
| Aurora CO | HOAs (298) | "solely for internal use only by City", plus an indemnity clause (grey-red) |
| King County | county GIS data | resale is barred without a written agreement |
| Huntsville | Data Depot | "the repackaging or reselling of this data is strictly prohibited." |

### Grey subdivision plats

| Area | Count |
|---|---|
| Maricopa Assessor | 31,953 |
| Chandler | 2,013 |
| Surprise | 1,220 |
| Henderson | 3,396 |
| SANDAG | 4,573 |
| Seattle legal subdivisions | 461k |
| Anchorage | 4,203 |
| Travis | 3,672 |
| Houston | 24,264 |
| Fort Bend | 8,086 |
| Montgomery County TX | 6,529 |
| Douglas County NE (portal says CC-BY-SA, but not on the item) | 8,341 |
| Nashville (there is an open-data executive order, not read) | 15,624 |
| Orange County FL | 14,374 |
| Montgomery County MD | 1,105 |

### Nothing found

- Salt Lake County
- Multnomah / Portland: the RLIS Neighborhood Organizations layer is civic, not HOAs
- Honolulu: TMK tax plats only
- Orleans Parish: the self-reported neighborhood organizations layer is civic
- Broward County
- Cook County: the clerk's Lots layer might serve as a plat proxy, but this is unverified
- Jefferson County AL: subdivision names exist only as a parcel attribute
- Phoenix: a table only, with no geometry

### Not searched (budget exhausted)

- Fulton, Gwinnett and Cobb GA
- Wayne and Oakland MI
- Nassau NY
- Philadelphia
- Middlesex MA
- Madison County AL
- Re-checks of Dallas, Hennepin and Ramsey

### Civic bodies are not HOAs

These layers are civic neighborhood organizations, not HOAs, and must never be used as an HOA flag:
- Denver RNOs
- Houston Super Neighborhoods
- Anchorage Community Councils
- Portland neighborhood organizations
- New Orleans neighborhood organizations
- Las Vegas NA/NW types
- Tulsa non-HOA types
- KCMO Neighborhood Associations
- Montgomery County MD Civic Associations

## 4. Method: building an HOA boundary from green parts

1. **Confirmed.** Use this level only when a green source asserts the association itself: Boulder HoaArcRef = Yes, DC condo regime lots, or Franklin and Hamilton CONDO polygons. The label is `HOA (recorded)` or `condo association`, with high confidence.
2. **Candidate.** Build these in four steps:
   - **Geometry:** take the polygon from a green plat layer (Sacramento, Scottsdale, Spokane, Gilbert, Columbus, Hamilton). Never draw a new line.
   - **Name:** take the association name from a public-domain registry: the IRS BMF (activity 401 or NTEE L50) or the CO SOS entity list.
   - **Match:** fuzzy-match the normalised registry name to the plat name, constrained by ZIP or city.
   - **Citation:** store the plat's book/page or instrument number (MCR, MAP_BOOK/PAGE, Vol_Page) as the reference for finding the recorded CC&R. The declaration itself stays at the recorder.

   The label is `possible HOA (name match)`, with medium confidence. The grey registries (TX TREC, VA DPOR, Utah, Nevada) may serve only as internal matching hints. They are never shipped or displayed until their licence is confirmed.
3. **These must stay labelled "possible":**
   - A plat with no name match: `subdivision plat (possible covenants - not confirmed HOA)`, low confidence.
   - Any name-only match.
   - Any multi-plat or master-association grouping. Do not infer nesting.
4. **Absence of data:** label it `no open HOA data`, never "non-HOA".
5. **Never use:**
   - grey or red HOA polygons
   - civic organization layers
   - OSM or hand-drawn boundaries
   - contact, email or person fields

**Next licence asks:** written reuse permission for the Clark County, Henderson and Orange County FL HOA layers would add the largest polygon sets. A GRAMA request for Utah, or a request to Nevada NRED, would add registry names.

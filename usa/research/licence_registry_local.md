# Licence registry, lane A: city, county, state and local sources

Checked 2026-11-24. The machine-readable version is `licence_registry_local.json` (one entry per source: endpoint, where it is used with its output keys, licence URL and quote, verdict, action). Federal, national and private sources are in `licence_registry_national.json` (lane B).

**Rule (strict).** A source is green only when its terms explicitly allow commercial use and redistribution: public domain, CC0, CC BY, CC BY-SA, ODbL, ODC-BY/PDDL, or a city/county licence page that says so. Anything with only a disclaimer, no stated terms, "use limitations: None", internal-use or do-not-share wording, all-rights-reserved, or a special commercial-purpose regime (A.R.S. 39-121.03, Texas appraisal districts) is grey or red. Facts restated in our own words with a link need no licence. That covers PHA payment-standard amounts and program descriptions.

**Method.** ArcGIS item `licenseInfo` (via sharing/rest), Socrata `/api/views/<id>.json`, CKAN `package_show`, and portal terms pages read with requests and the built-in browser. I did not use Firecrawl and did not accept any gate: the EDI login and the Phoenix bot check were left unpassed.

## Counts
107 sources: 58 green, 44 grey, 5 red. Actions: 58 keep, 17 replace, 32 remove.

## Red
- **DCAD** (Dallas appraisal files): the page says "All Rights Reserved" and gives no licence. This removes the whole `dallas/` lead pipeline (homes.parquet, leads/zips/storms.json, lots.json) and the Dallas assessor adapter.
- **Maricopa Assessor**: commercial use must go through the A.R.S. 39-121.03 commercial-purpose request. Remove the Maricopa assessor data, including pool and pool_area. Phoenix NSD cases (CC BY) need re-geocoding, because they are currently placed using the assessor parcels.
- **Met Council interceptors and sewersheds**: the terms say "do not transfer or share data". Remove.
- **Met Council TPP2050 transitways**: marked "FOR INTERNAL USE ONLY". Replace with Met Council *Transitway Stations and Alignments*, which is public domain.

## Replace (a green alternative exists)
| Source | Green alternative |
|---|---|
| Hennepin parcels (house3d, money, derive) and Ramsey parcels (Saint Paul assessor) | Met Council *Parcels_2025* layers 3 and 4: "public domain under the Minnesota Government Data Practices Act". Has EMV values, tax, year built, sale, homestead and square feet. |
| Hennepin aerial imagery (`img/<year>.jpg`, roof colour) | NAIP or USGS National Map orthoimagery (public domain) |
| Boulder County CamaView assessor services | Boulder County *Assessor's Property Data* bulk CSVs plus parcels item, both CC BY 4.0 |
| Boulder CodeEnforcementPublic and Zoning_2024 | City of Boulder *Code Enforcement Reports* and *Zoning Districts*, both CC0 |
| CAGIS parcels and subdivisions (Cincinnati) | Cincinnati Open Data listings g24g-2pi5 and rynu-bywd, both "Public Domain". Cincinnati zoning: only the county layer f25h-aupn. |
| MnDOT AADT ("Use limitations: None") | FHWA HPMS, which is already the base layer |
| Metro Transit GTFS (no terms) | Met Council *Metro Transit Stops* (public domain). Live departures are dropped. |
| Minneapolis speed limits and signals; Dallas roads | OpenStreetMap (ODbL) or TIGER |
| Minneapolis solar suitability | Our own LiDAR sun model |
| MSPtrees genus (an unlicensed re-upload; the EDI page needs a login) | LiDAR trees without genus, or OSM |

## Remove (grey, no green alternative found)
- Minneapolis: Vacant Building Registry, Condemned by Boarding, sanitary and storm mains
- MnDOT IRIS incidents
- Saint Paul Certificate of Occupancy
- Dallas: building permits and HOA areas
- Austin: Land Database (assessor) and subdivisions
- Franklin County ParcelSalePoints
- Franklin County Auditor tax parcels and Subdivision & Condominium Boundaries (owner: no clear legal standing)
- Tulsa: all four sources (parcels, zoning, Verint cases, subdivisions)
- Kansas City: parcels, zoning, land-use codes, CPD permits, STR, HOA, vacant parcels. The data.kcmo.org terms reserve the city's IP.
- Huntsville: parcels and permits
- Jefferson County, AL parcels
- Phoenix permits layer 1: no dataset licence, and the terms page sits behind a bot check

## Kept, with notes
- **Minneapolis Rental Licenses (Archive)** is CC BY-SA 4.0. `local_sources.json` wrongly lists it as "verify".
- **Denver** is CC BY 3.0 through the Open Data Catalog terms. The item text itself is only a disclaimer.
- **CDOT traffic counts** are CC BY-SA 4.0.
- **Columbus** items are CC0. Saint Paul items are Public Domain. Austin, Cincinnati and KC 311/dangerous-buildings/violations, Texas HOT, and the Colorado Denver STR feed are public domain on Socrata. Dallas 311 is PDDL. Birmingham permits and Phoenix NSD are CC BY. Boulder County platted areas are CC BY 4.0.
- **Removed by owner decision (no clear legal standing):** Franklin County Auditor parcels and condo layers. Only the Auditor's *Current Parcels Map* item says "Public Domain"; the feature-service item itself shows only a disclaimer. Columbus blocks now use the ACS tract median for money, carry no parcels, and get HOA hints from City of Columbus CC0 plats only.

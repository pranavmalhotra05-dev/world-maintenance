# Parcels West: open parcel polygon sources

Checked 2026-11-25. Full records, with licence quotes and URLs, are in parcels_west.json. Each count is an ArcGIS returnCountOnly query on a box about 200 m across, centred on the block.

| Block | Best source | Verdict | Count in 200 m box |
|---|---|---|---|
| phoenix-az | City of Phoenix - City Parcels (phoenixopendata.com / egishub-phoenix) | grey | 62 |
| scottsdale-az | City of Scottsdale GIS data sales (parcels shapefile) | red | not tested |
| las-vegas-nv | City of Las Vegas ArcGIS - CC_PARCELS_SHP (Clark County Assessor parcels) | red | 29 |
| salt-lake-city-ut | UGRC SGID - Utah Salt Lake County Parcels LIR | green | 52 |
| sacramento-ca | Sacramento County GIS (ArcGIS org 'sacramentocounty') - Parcels | green | 60 |
| san-diego-ca | SANDAG/SanGIS GeoServer - Hosted/Parcels | grey | 75 |
| seattle-wa | City of Seattle - Zoned Development Capacity Layers 2016 / Parcels (PDDL) | grey | 71 |
| spokane-wa | WA Geoportal - Current Parcels (statewide, Parcels_2026) | grey | 40 |
| portland-or | Oregon Metro RLIS Discovery - Taxlots (Public) | grey | 83 |
| honolulu-hi | Hawaii Statewide GIS Program - Oahu Parcels (from C&C Honolulu HOLIS) | grey | 70 |
| anchorage-ak | Municipality of Anchorage - PropertyInformation_Hosted (parcels + CAMA) | green | 14 |

## Sidewalk / right-of-way layers

| City | Layer | Verdict | Count |
|---|---|---|---|
| portland-or | Oregon Metro RLIS - Sidewalks (polylines) | green | 16 |
| san-diego-ca | City of San Diego open data - Right-of-way parcels (SanGIS ROW polygons) | grey | 10 |
| anchorage-ak | Municipality of Anchorage - Maintained Sidewalks (polylines) | green | 1 |
| seattle-wa | City of Seattle SDOT - Sidewalks (Active) | grey | not tested |

Notes: Green means an explicit grant covers commercial use or use for any purpose. Portland RLIS is close to green: its custom ODbL-style licence lets you put the data in your own product, but it is revocable and never mentions commercial use, so it needs sign-off. Seattle has a City licence (PDDL) on parcel lines from 2016, but those lines come from King County, whose terms say no one may sell them without a written agreement. Phoenix, AZ state data and the City of Spokane portal were only partly checked because the web-search budget ran out.

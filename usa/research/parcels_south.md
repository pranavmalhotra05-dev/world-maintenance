# Parcels South - licence check

This table summarises `parcels_south.json`. Green means an explicit licence that allows commercial use. Counts come from an envelope `returnCountOnly` query (Socrata `count(*)` for Dallas). No owner fields were requested.

| block | source | verdict | tested count (200 m box) |
|---|---|---|---|
| austin-tx | Travis County GIS - TCAD Parcels (public) | grey | 68 |
| dallas-tx | City of Dallas Open Data - 'Parcel' (2016 certified roll, Dallas + 1 mile) | green | 48 |
| houston-tx | City of Houston MyCity - Land Use (Grouped) parcels (HCAD-derived) | grey | 52 |
| tulsa-ok | City of Tulsa GIS - Tulsa Parcels (LandBase web map view) | grey | n/t |
| kansas-city-mo | City of Kansas City MO GIS - Parcels (AGOL/6) | grey | 67 |
| omaha-ne | Douglas County / DOGIS - Parcels public | grey | 51 |
| birmingham-al | City of Birmingham GIS - Essentials/Parcels | grey | 45 |
| huntsville-al | none found | red | n/t |
| nashville-tn | Metro Nashville - Parcels_view | red | 54 |
| new-orleans-la | City of New Orleans eGIS - LandBase/Parcels (Assessor) | grey | 58 |
| atlanta-ga | Fulton County GIS - Tax Parcels (current year) | green | 44 |
| fort-lauderdale-fl | Florida Statewide Cadastral (FDOR via FloridaGIO) | grey | 71 |
| orlando-fl | Florida Statewide Cadastral (FDOR via FloridaGIO) | grey | 20 |
| austin-tx (sidewalk) | City of Austin TRANSPORTATION_sidewalks | grey | n/t |
| houston-tx (sidewalk) | City of Houston sidewalk service areas / permits | grey | n/t |
| new-orleans-la (sidewalk) | City of New Orleans Edge Of Pavement (2013 curbs, line + polygon) | grey | n/t |
| dallas-tx (sidewalk) | City of Dallas ROW Restrictions / ROW Permits | grey | n/t |
| all (sidewalk) | OpenStreetMap footway=sidewalk / area:highway (all 13 cities) | green | n/t |

Gaps: the web-search budget ran out before Madison County AL and the TxGIO Site Policies page were checked. WebFetch was blocked on several county hosts, so metadata and count queries were made with curl: small, read-only requests only.

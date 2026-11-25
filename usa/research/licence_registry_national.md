# National licence registry (lane B) - 2026-11-24

STRICT clear-green only: terms must EXPLICITLY allow commercial use and redistribution (US federal work / public domain, CC0, CC BY, CC BY-SA, ODbL, CDLA-Permissive, ODC-BY/PDDL, MIT/BSD/OFL for code+fonts, open-government licences, Copernicus free-full-open). Disclaimer-only, terms-unstated, 'verify', non-commercial, notify/permission-required, fee-for-commercial, or platform-wrapped (e.g. Esri MLA) = grey/red -> remove or replace.

**87 sources: 73 green, 13 grey, 1 red** -> keep 73, replace 11, remove 3.
Full quotes, URLs and output keys: `licence_registry_national.json`.

## Remove / replace

| id | verdict | action | used in | alternative |
|---|---|---|---|---|
| overpass_public | grey | replace | mpls/extras.py, usa/build_block.py, usa/enrich_national.py, usa/tourism_events.py | Self-hosted Overpass (or osmium/DuckDB) on a Geofabrik US state extract or the OSM planet file (same ODbL data). |
| qlever_osm | grey | replace | usa/tourism_events.py | Query a local OSM planet/Geofabrik extract (osmium tags-filter / DuckDB) or a self-hosted QLever. |
| bts_noise_esri | grey | replace | mpls/extras.py, usa/enrich_national.py | BTS National Transportation Noise Map rasters downloaded from BTS directly (https://www.bts.gov/geospatial/national-transportation-noise-map), US federal work. |
| esri_fed_power_plants | grey | replace | mpls/infra.py, usa/enrich_national.py | EIA U.S. Energy Atlas 'Power Plants' layer served by EIA itself (atlas.eia.gov), US federal work. |
| esri_fed_transmission | grey | replace | mpls/infra.py, usa/enrich_national.py | OpenStreetMap power=line / power=minor_line with voltage (ODbL) from a local OSM extract. |
| esri_fed_pipelines | grey | replace | mpls/infra.py, usa/enrich_national.py | EIA U.S. Energy Atlas 'Petroleum Product Pipelines' layer (atlas.eia.gov), US federal work. |
| zillow_zhvi | grey | replace | mpls/feeds.py, mpls/money.py, usa/enrich_national.py | FHFA HPI annual ZIP5/tract (already fetched as fhfa_hpi) for the index; ACS B25077 for levels. For mpls/money.py use FHFA ZIP5 HPI 2025->latest instead of ZHVI-since-Jan-2026. |
| realtor_com_inventory | grey | remove | mpls/money.py | No clean equivalent for live listings; drop realtor_zip. Optional context: ACS B25004 for-sale vacancy and Census BPS permits (public domain). |
| freddie_mac_pmms | grey | replace | mpls/money.py, usa/enrich_national.py | FFIEC Average Prime Offer Rates (APOR) weekly tables published by the FFIEC/CFPB (ffiec.cfpb.gov rate-spread tool, US federal work) for 30-yr and 15-yr fixed; label as APOR, not PMMS. |
| usbr_recreation_areas | red | replace | usa/visitors.py | USGS PAD-US (Protected Areas Database, public domain) filtered to Mang_Name = BOR for Reclamation recreation lands. |
| d3_celestial_stars | grey | replace | usa/sky_catalog.py | Run usa/sky_catalog.py --strict (all star rows from the IAU Catalog of Star Names, CC BY) or source magnitudes from a US-government catalogue with stated public-domain status. |
| metro_transit_gtfs | grey | replace | mpls/feeds.py | BTS NTAD National Transit Map Stops (CC BY 3.0 US, includes Metro Transit stops) for stop locations; drop live departures. |
| msptrees_2023 | grey | remove | mpls/derive.py | Keep LiDAR-derived trees only (USGS 3DEP, public domain), or the City of Minneapolis public tree inventory if its portal states an open licence (lane A). |
| mndot_iris_incidents | grey | remove | mpls/extras.py, mpls/live.py | - |

## Kept with extra conditions

- **overture_addresses**: no mailing-list use of NAD addresses
- **fema_nri**: display FEMA NRI not-endorsed disclaimer; cite dataset version + access date; no FEMA/DHS seals; no reverse engineering; cease and destroy copies if FEMA requests
- **openfema**: display OpenFEMA not-endorsed disclaimer; cite endpoint, version, access date/time; no re-identification of individuals; do not claim modified data is FEMA's; cease and destroy copies if FEMA requests
- **nps_stats_boundaries**: commercial republication notice referencing US Government works

## Notes

- FHFA HPI 'verify' resolved: FHFA states agency materials are public domain -> green; change the licence strings in enrich_national.py.
- Planetary Computer is an access platform; its preview terms do not restrict commercial use, data licences govern. Kept.
- OSM/ODbL data is green, but the free public Overpass/QLever endpoints are not for commercial use -> self-host on an extract.
- Overture addresses (US = NAD) are public domain but NAD must not be used as a mailing list.
- Zillow -> FHFA HPI; Freddie Mac PMMS -> FFIEC APOR; Realtor.com -> drop.

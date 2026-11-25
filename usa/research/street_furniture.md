# Street furniture per block (usa/street_furniture.py)

Checked 2026-11-24. Output: `web/usa/blocks/<slug>/furniture.json` + `meta.furniture` summary.
Run: `.venv\Scripts\python -u -W ignore -m usa.street_furniture [slug ...] [--no-city] [--png slug]`.

## Owner rules applied (strict)
- An item exists only if a data record says so: an OSM tag or a licence-green city inventory. Nothing is inferred:
  no signals at intersections, no speed-limit signs from way `maxspeed`, no signs placed by rule.
- Positions are the source's exact coordinates (lon/lat kept on every item, plus `source_id`, and `coord_precision_dp`
  where the source states it as text). Items are never pushed off the road.
- `heading_deg` only from data: OSM `direction` / `camera:direction` given in degrees or compass points, or a city
  facing field (Austin `direction_facing`). Otherwise null. OSM `direction=forward|backward` is kept raw as
  `direction_tag` (it says which travel direction the sign applies to, not a compass heading).
- `kind_class`:
  - `physical_asset`: the object itself (lamps, hydrants, poles, city sign-inventory posts, OSM `traffic_sign` /
    `street_lamp` nodes not on a carriageway way, cameras, LiDAR poles).
  - `control_point`: OSM `highway=stop|give_way|traffic_signals|crossing` nodes that sit on a highway way, OSM sign
    nodes on a carriageway way, and city signal records (point at the intersection, not a pole).
- LiDAR: a detected pole is a measured location of unknown type, so `kind: unknown_pole`, `confirmed_by_lidar: true`.
  A data item with a LiDAR pole within 3 m gets `confirmed_by_lidar: true`, `lidar_pole {x, y, offset_m, height_m}`
  and `height_m` from the pole; its own position stays. That pole is not emitted again. Poles are never relabelled
  as lamps or signals without a data record within 3 m. Hydrants and crossings can't be confirmed by a 3 m+ pole.
- Cameras: public or government only, and locations only (no feeds, no screenshots). OSM: `surveillance=public|traffic`,
  `surveillance:zone=traffic`, `highway=speed_camera`, or a government `operator` with no `surveillance` value.
  `surveillance=outdoor|indoor` (private property) is always excluded.
- Duplicates: the same kind from two sources within 3 m counts as one item. The city inventory is primary. The other
  record goes in `also_recorded_by`, with its own coordinates.

## Sources: green (used)
| Source | Use | Licence | Licence evidence (quote + URL) |
|---|---|---|---|
| OpenStreetMap via Geofabrik state extracts (`usa/osm_extract.py`, read locally with pyosmium; no public Overpass/QLever) | lamps, stop/give_way, traffic_sign (incl. US:R2-1 + value), traffic_signals, crossings, public cameras, hydrants, power poles | ODbL 1.0 | "OpenStreetMap is open data, licensed under the Open Data Commons Open Database License (ODbL)" https://www.openstreetmap.org/copyright ; Geofabrik terms recorded in `osm_extract.GEOFABRIK_TERMS` |
| USGS 3DEP lidar (block points) | pole detection, ground z | Public domain | "USGS-authored or produced data and information are considered to be in the U.S. public domain" https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits |
| City of Austin Roadway Sign Assets `ggnk-3ykn` | signs with MUTCD code, message, `direction_facing`. Active rows only (asset_status 1). SIGN_POLE rows that share an `asset_location_id` with a sign are attached to it as `post_asset_ids`. | Public Domain | Socrata metadata `license: Public Domain` https://data.austintexas.gov/api/views/ggnk-3ykn.json |
| City of Austin Traffic Signals and Pedestrian Signals `p53x-x73x` | traffic_signal (control_point) | Public Domain | https://data.austintexas.gov/api/views/p53x-x73x.json (`licenseId PUBLIC_DOMAIN`) |
| City of Austin Traffic Cameras `b4k4-adkb` | camera_public (location only) | Public Domain | https://data.austintexas.gov/api/views/b4k4-adkb.json (`licenseId PUBLIC_DOMAIN`) |
| Greater Cincinnati Water Works Fire Hydrants `qhw6-ujsg` | fire_hydrant | Public Domain | https://data.cincinnati-oh.gov/api/views/qhw6-ujsg.json (`license: Public Domain`) |
| City of Minneapolis Hydrants (item 4d7fc1675df843c7bbf3b63f77bc19dc) | fire_hydrant | CC0-style waiver | "To the extent possible under law, City of Minneapolis has waived all copyright and related or neighboring rights to Open Data." (item licenseInfo) |
| City of Minneapolis PW_Planimetric_Points (item ae864fe030134ceb8115aa95b4e97378) | HYDRANT, POWER_POLE, STREET_LIGHT, STREET_SIGN (type unknown, so `other_sign`) | CC0-style waiver | same quote, item licenseInfo |
| Denver Traffic Signals (item b07e3baeaa37459981cb717e33ebab3a, layer 147) | traffic_signal | CC BY 3.0 via catalog terms | "The City and County of Denver Open Data Catalog provides open data licensed under the Creative Commons Attribution 3.0 license (CC BY 3.0)." https://opendata-geospatialdenver.hub.arcgis.com/ (Terms of Use). The item's own text is only a disclaimer. This is the same basis as the Denver entries in licence_registry_local.json. |
| Denver Police HALO Cameras (item c3c8ffc9f5a14c1b9859c4813f0bd565) | camera_public (government; location only) | CC BY 3.0 via catalog terms | as above |
| City of Boulder Contractor Streetlights (View), Signalized Intersection FINAL PUBLIC VIEW, Red Light Cameras PUBLIC VIEW | street_lamp, traffic_signal, camera_public | CC0 1.0 | "The City of Boulder Open Data Catalog provides open data licensed under the Creative Commons CC Zero license (CC0 1.0)." https://bouldercolorado.gov/services/open-data#section-4148 (read in the built-in browser; each item's licenseInfo links there) |

## Excluded (not explicitly licensed for commercial use)
- **City of Scottsdale Street Signs** (OpenData MapServer/8, item b13934951c984ad1b64a3e6858171031): the item's
  licenseInfo says ODbL, but the City of Scottsdale portal data licence (cos-gis.maps.arcgis.com item
  b056ccb8140b404a95fa7eb71d064151, §3.1) says "These rights explicitly exclude commercial use." The two conflict,
  so the layer is grey under the owner's rule and is removed. scottsdale-az was re-run with OSM only (2026-11-24).
- Dallas Traffic Signs / Speed Limit Signs View / Street Lights / TrafficControlDevices (DallasGIS): disclaimer only ("The accuracy is not to be taken / used as data produced by a Registered Professional Land Surveyor ...").
- Phoenix Street Lights / Street Light Poles / Signal LED Areas: "subject to the Terms of Use". Those terms sit behind a bot check and are grey in the licence registry.
- Kansas City Street Lights `n2wy-vqag`: no licence in its Socrata metadata, and the data.kcmo.org terms reserve IP (registry).
- Minneapolis Signs / Signalized Intersections / TRAFFICSignalHeads: no item licence (registry: Minneapolis signals grey).
- Denver Signs_from_Cartegraph / Signs_12022025 / Stop Signs & Trees: no licence text, and they look like staff working layers.
- Boulder County Electronic Traffic Control Devices (CC BY 4.0) is green, but its service sits behind an msappproxy host. Not used. The City of Boulder signal layer covers the city.
- MnDOT / TxDOT / CDOT camera feeds were not used. Feeds are out of scope, and no explicitly licensed location layer was found for these blocks.
- Saint Paul, Scottsdale (after the exclusion), Columbus (CC0 org), Tulsa, Huntsville, Birmingham, KC, Phoenix: the org searches found no licensed furniture layer, so these blocks use OSM only.

## LiDAR pole detector
The points are decoded from `points_*.txt` (build_block.py format; kinds 0 ground, 1 house, 2 multi-return,
3 other). The steps:
1. Take the "trunk slice": non-ground, non-house points 0.5 to 2.8 m above ground.
2. Find connected 0.3 m cells in that slice.
3. Keep a cluster only if all of these hold:
   - horizontal extent ≤ 0.6 m;
   - ≥ 2 returns (≥ 3 when the block has < 15 pts/m²);
   - no house return within 0.6 m;
   - the 0.5 m column reaches ≥ 3 m and has returns in ≥ 3 distinct 1 m height bins;
   - little low clutter within 2 m (≤ 3·n + 6 points);
   - no tree canopy (points above 3 m within 0.6 to 2 m ≤ 2·column + 15).

`height_m` is the highest return in the column, or on a thin arm within 1.5 m.
Validation on minneapolis-mn / -oz against the CC0 planimetric poles: most detections land within 3 m of a recorded
pole. Recall is low: a pole seen from above rarely returns from its side, and poles under trees are rejected.
Sparse blocks (KC 4.8, Denver/Dallas 8 pts/m²) give less reliable detections. They stay `unknown_pole` and carry
`lidar_points` and `lidar_extent_m`, so you can judge each one.

## Licence of the output
furniture.json holds OSM-derived records, so it is a Derivative Database of OSM: **ODbL 1.0 share-alike**, with
attribution "© OpenStreetMap contributors". City attributions (Denver CC BY 3.0) are in
`meta.attribution`. Public domain and CC0 sources need none, but are credited anyway.

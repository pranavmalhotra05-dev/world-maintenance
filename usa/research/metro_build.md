# Metro build (lane MX): every building in a metro, with green-licence attributes

Script: `usa/metro_build.py`. Output: `web/usa/metros/<slug>/index.json` + `tiles/<tx>_<ty>.json`.
Cache (re-runnable, stage by stage): `usa/_cache/metro/<slug>/`.

```
.venv\Scripts\python -u -W ignore -m usa.metro_build --metro phoenix-az --counties "04013,04021" --name "Phoenix metro, AZ"
   [--stages buildings,tracts,flood,hail,lst,tiles] [--force lst] [--release 2026-11-18.0]
   [--hail-months 24] [--hail-spc-km 150] [--lst-years 2026,2025,2024] [--lst-max-scenes 14]
```
In PowerShell, quote the county list. Unquoted `04013,04021` becomes an array of numbers and loses the leading zeros. The script zero-pads anyway.

Other metros use the same command with different counties, for example Dallas `48113,48085,48439,48121`, Minneapolis `27053,27123`, Denver `08031,08005,08059,08001,08035,08014`. A metro that crosses a state line works too, because the tract files are loaded per state from the county list.

## Phoenix build (2026-11-24)

| | |
|---|---|
| Counties | Maricopa 04013 (1,666,287 buildings), Pinal 04021 (215,271) |
| Overture release | 2026-11-18.0. 1,905,144 footprints intersect the bbox; 1,881,558 have a centroid inside the two counties (underground ones excluded) |
| Upstream datasets | OpenStreetMap 1,644,020 · Microsoft ML Buildings 207,827 · Esri Community Maps 29,711 |
| Tiles | 979 tiles of 0.05 deg, 65.8 MB in total, largest 0.81 MB / 23,013 buildings (Mesa); index.json 0.18 MB; **66.0 MB overall** |
| Timing | buildings 56 s (DuckDB over S3) · tracts 3 s · flood 20 s (after a 170 MB NFHL download) · hail 9 s (MRMS GRIBs already cached) · LST 41 s · tiles 5 s |

### Field coverage (share of all 1,881,558 buildings)
- Height: 93.8% (Overture `height` 93.65%, from `num_floors` x 3.2 m 0.13%, unknown 6.2%).
- Overture class: 39.1%. Subtype: 39.3%.
- Census tract: 99.99995%. 248 buildings fell on simplified tract edges and took the nearest tract within 200 m. 1 has no tract.
- FEMA NFHL zone: 99.995% fall inside a mapped polygon. X (0.2% annual chance) 1,548,463 · X (minimal) 249,197 · D 36,291 · AE 11,967 · X (levee-reduced) 9,870 · X (1% chance, < 1 ft deep) 9,194 · AO 7,511 · A 5,629 · AH 2,288 · AE floodway 837 · A floodway 6 · area not included 214 · outside any polygon 91. Most of the Phoenix valley floor really is mapped as shaded X (0.2%) on the effective FIRMs.
- MRMS hail: every building has a value. 30 storm days were checked (SPC hail within 150 km, 2024-11-24 to 2026-11-24). 20.1% of buildings saw MESH >= 0.75 in and 73.9% saw >= 0.25 in. The maximum was 2.98 in, on storm day 2026-10-21. The biggest days by buildings >= 0.75 in were 2026-10-21 (160,729), 2025-09-26 (94,677), 2026-10-20 (71,498) and 2025-10-13 (32,165).
- Landsat summer LST: 99.998%. Two near-cloudless Landsat 8 scenes were used: LC08 037/037 on 2026-08-09 (1,820,122 buildings, median 57.9 C) and LC08 036/037 on 2026-08-18 (61,396 buildings, median 57.3 C). 40 buildings have no clear pixel.
- County weather triggers (index.json `counties[].weather_triggers`): Maricopa has 27.9 dust days a year, Pinal 59.0.

## Tile format
Each tile is `{id, bbox, n, x, dy, area, h, hs, cls, sub, src, fz, hail, hd, lst, lsc, cty, tracts, tr}`. Every per-building column is either an int array or a single int, when the whole tile shares the value.
- lon = bbox[0] + x * 1e-5. lat = bbox[1] + cumsum(dy) * 1e-5. Buildings are sorted by lat, then lon.
- `tr` indexes the tile's `tracts`, which index `index.tracts` (a columnar table of GEOID plus income, home_value, year_built, owner_pct, sf_pct, pop, oz18, oz27, qct, nmtc, nri, nri_hail, nri_wind, nri_flood, hpi_1y, hpi_5y). Tract metrics are stored once per tract, not per building.
- Categorical codes resolve through `index.dicts` (cls, sub, src, fz). `hd` indexes `index.hail.days` (1-based). `lsc` indexes `index.lst.scenes` (1-based).
- Field docs are in `index.fields`, coverage in `index.coverage`, licences in `index.sources` / `index.licences` / `index.attribution`.

Bytes per field across all tiles (MB): x 9.0, lst 7.5, area 7.4, hail 5.8, h 5.5, hd 4.2, tr 4.1, cls 3.8, dy 3.8, hs 3.8, src 3.8, sub 3.7, fz ~3, cty 0.2. If a bigger metro goes over budget, drop `hs`, `sub` and `hd` first. Never drop buildings.

## Sources and licences (green only)
| Source | Licence | Use |
|---|---|---|
| Overture Maps buildings (s3://overturemaps-us-west-2, release 2026-11-18.0) | **ODbL 1.0**. The tiles are a derived database and are share-alike under ODbL. Attribution: "© OpenStreetMap contributors, Overture Maps Foundation" | footprint centroid, area, height, class |
| Census cartographic boundary counties 2023 (data/national/counties.zip) | US federal work, public domain | county filter |
| web/usa/data/tracts/<ST>.json: Census tracts + ACS 2020-2024, CDFI OZ 2018/2027, HUD QCT 2026, CDFI NMTC, FEMA NRI, FHFA HPI | US federal works, public domain (HUD/FHFA not endorsing) | tract attributes |
| FEMA NFHL county packages, Map Service Center (msc.fema.gov, NFHL_04013C 04013C_20261002.zip, NFHL_04021C 04021C_20261112.zip), layer S_FLD_HAZ_AR | US federal work, public domain. Not an official flood determination | flood zone at the centroid |
| NOAA MRMS MESH_Max_1440min (s3://noaa-mrms-pds) | NOAA open data, public domain (attribution requested) | per-building max hail |
| NOAA SPC storm reports | public domain | picks which days to read from MRMS |
| USGS Landsat 8/9 C2 L2 ST_B10 via Planetary Computer STAC | public domain: "There are no restrictions on the use of Landsat products." | summer LST |
| web/usa/data/weather_triggers.json (NOAA NCEI Storm Events, NWS warnings via IEM, EPA AQS PM10, GHCN-Daily) | public domain | county dust/wind/hail trigger stats |

No owner, occupant or other personal data is read or written. USGS 3DEP-derived heights were skipped: Overture already covers 93.8%, so they were not needed.

## Method notes and caveats
- Footprint area uses EPSG:6933 (global equal-area). Buildings are assigned by **centroid in county**, so a footprint that straddles a county line belongs to one county only.
- Flood: the zone at the centroid, taking the highest-hazard polygon where polygons overlap (SFHA first, then 0.2%, then the rest). The spatial join puts an STRtree over the points and queries it with each prepared polygon. The reverse join, with polygons in the tree, took minutes; this way takes seconds.
- Hail: nearest ~1 km MRMS pixel to the centroid. Only days with an SPC hail report within 150 km of the metro box are read, so a radar-only hail day with no report is missed. MESH is a radar estimate that tends to over-estimate. The storm date is the MRMS 12Z file date minus one day.
- LST: the 30 m pixel (100 m TIRS resampled by USGS) holding the centroid. It mixes roof, yard and street, so it is not a roof-only reading. Scene selection is greedy: newest summer first, near-cloudless first, maximum coverage of unassigned buildings, with a bonus for reusing an overpass date. A building is kept only when QA_PIXEL shows fill, cloud, dilated cloud, cirrus and shadow all clear. The two scenes are 9 days apart, so compare `lst` with that scene's `median_c` to get an anomaly.
- Dust triggers are county-level, not per building: `cty` points to `counties[]`.

# Thermal lane (TH): measured land-surface temperature per block

Script: `usa/thermal.py` (`.venv\Scripts\python -u -W ignore -m usa.thermal [slug ...]`). Run 2026-11-24 on all 16 blocks in `web/usa/blocks`. Re-runs are cheap because every scene read is cached in `usa/_cache/thermal/scenes/*.npz`.

Before this lane, the page's "Thermal" theme was only a palette plus modelled sun exposure (`h.sun`). It now has measured satellite skin temperature, with real dates, for every block.

## Sources and licences

| # | Source | Used? | Resolution | Licence (quoted from the source) |
|---|---|---|---|---|
| 1 | Landsat 8/9 Collection 2 Level-2 Surface Temperature, `ST_B10` (STAC asset `lwir11`, plus `qa_pixel` and `qa` = ST_QA), Planetary Computer `landsat-c2-l2`, anonymous SAS | **Yes, primary** | 30 m grid, resampled by USGS from 100 m TIRS | USGS public domain. "There are no restrictions on the use of Landsat products." (usgs.gov/landsat-missions/landsat-collection-2-surface-temperature). Citation: EROS (2020), doi:10.5066/P9OGBGM6 |
| 2 | ECOSTRESS `ECO_L2T_LSTE` v002, 70 m (LP DAAC) | **Skipped** | 70 m | NASA open data. CMR shows granules over the blocks (11 over Austin in July 2026), but each data URL returns a 302 to `urs.earthdata.nasa.gov`. A free Earthdata login is required and none is set up (no `.netrc`). Creating that account needs the user. |
| 3 | GOES-19 ABI L2 LST CONUS (`ABI-L2-LSTC`), hourly, NOAA NODD `s3://noaa-goes19` over anonymous HTTPS | **Yes, diurnal context** | 2 km | NOAA public domain (registry.opendata.aws/noaa-goes) |
| 4 | NWS/FAA ASOS routine METARs from the Iowa Environmental Mesonet archive | **Yes, air temperature** | point | IEM: "The materials found on this website are in the public domain and may be used freely by anyone for any lawful purpose." (mesonet.agron.iastate.edu/disclaimer.php) |

Why IEM and not NCEI: the NCEI `global-hourly` (ISD) access service returns nothing after about Aug 2025 for these stations, and `isd-history.csv` ends at 2025-08. IEM carries the same ASOS observations up to the present.

## Method

- **Scenes.** All Landsat 8/9 items from 2023-11-24 to 2026-11-24 that intersect the block. Filters:
  - Tier 1 only. T2 geolocation is too loose for a block about 100 m across.
  - Scene cloud cover ≤ 85 %.
  - One item per platform and date, because adjacent WRS rows overlap.
  - SR-only items that have no `lwir11` are dropped.
- **Clear test.** Uses QA_PIXEL bits 0–4 (fill, dilated cloud, cirrus, cloud, shadow). Snow is kept because it is a real surface. A scene passes only if all three of these hold:
  - every 30 m pixel touching the block is clear;
  - at least 80 % of the 2 km context window is clear (guards against cloud edges and shadows);
  - the mean ST_QA uncertainty over the block is ≤ 6 K.
- **Grid.** ST in °C = DN × 0.00341802 + 149 − 273.15. It is reprojected with nearest neighbour from UTM onto a 36 × 36 lon/lat grid over `meta.bounds`. That is about 3 m per cell, so each 30 m pixel shows as a block of roughly 10 × 10 cells. Values are not smoothed or invented.
- **Block statistics.** For each scene: block mean, max and median of the grid. The 2 km context median is computed from clear pixels outside the block. Air temperature is the nearest IEM routine observation within ±90 min of the overpass (about 10:30–11:00 local solar time).
- **Summer and winter scenes.** The summer grid is the hottest clear June–August scene. The winter grid is the coolest clear December–February scene.
- **Houses.** Each house is sampled at its `lon`/`lat`:
  - `summer_lst_c` and `winter_lst_c` come from those two scenes.
  - `rel_to_block_c` is the mean over all clear scenes of (house pixel − block median). `rel_to_block_summer_c` is the same over June–August scenes only.
  - `hot_roof_flag` is set when `rel_to_block_c` is in the block's top decile and > 0.
- **GOES.** The day 2026-08-19 was chosen automatically as the summer 2026 date with the most blocks clear in Landsat. The window is 24 hourly LSTC files from 06Z (about 80 MB, cached in `usa/_cache/thermal/goes/`). Only DQF high- or medium-quality retrievals are kept. The western blocks are all DQF = 1 (medium) because of view geometry, so keeping only DQF = 0 would have dropped them.

## Outputs

- `web/usa/blocks/<slug>/thermal/`, all files under 8 KB:
  - `lst_grid.json` (summer) and `lst_grid_winter.json`, each `{bounds, w, h, scale:0.1, offset:0, nodata:-9999, values:[°C×10 ints, row 0 = north], date, time_utc, scene_id, air_temp_c, png_vmin_c/png_vmax_c, context_p2_c/p98_c/median_c}`.
  - `lst_summer.png` and `lst_winter.png`: 288 px RGBA, inferno ramp, nearest neighbour. The colour stretch is block min to max, widened to at least a 4 °C span so that sub-degree noise does not fill the whole ramp. The page can re-colour from the json.
- `meta.thermal` contains:
  - source, resolution, period, counts and the clear rule;
  - hottest and coolest date with block mean and max;
  - summer and winter dates and scene counts;
  - `uhi_vs_2km_hottest_c` / `uhi_vs_2km_mean_c` and `urban_heat_note`;
  - `sun_context` (Pearson correlation of house `rel_to_block_c` with modelled `h.sun.shaded_pct`, given as context only);
  - `air` station block, the `ecostress` skip reason, licence, quote and citation;
  - `series` `[{date, time_utc, platform, block_mean_c, block_max_c, context_2km_median_c, air_temp_c, st_uncertainty_k}]`;
  - `goes_diurnal` `{points:[{t_utc, lst_c, qf}], max_c, min_c, clear_hours, …}`.
- `meta.files` has the 4 new paths appended. `houses[].thermal` is set as described under Method. All writes go through `usa.blocklock.block_lock` with a re-read inside the lock and tmp + `os.replace`.
- Visual check: `usa/_cache/thermal/samples/<slug>_<date>_context.jpg` is the 2 km context crop of the hottest summer scene, with the block outlined in cyan. I inspected Austin and Phoenix. Both show coherent urban LST fields: hot commercial and road corridors, and cool parks, greenbelts, golf courses and water in Phoenix. Neither shows striping or cloud artefacts.

## Per-block results

Columns, left to right:
- Block
- T1 acq: Tier 1 acquisitions over the block
- Clear: clear scenes
- JJA/DJF: clear summer / winter scenes
- Hottest: date of the hottest clear scene
- Mean / Max: block mean and max on that date, °C
- Air: ASOS air temperature at that overpass, °C
- Coolest: coolest clear scene, date (block mean °C)
- vs 2 km: block minus 2 km median, on the hottest scene / averaged over all scenes, °C
- ASOS: station, distance, and how many scenes got an air match
- Hot flags: houses flagged / houses
- r(rel, shaded%): correlation with modelled shading
- GOES max: GOES max on 2026-08-19, with clear hours

| Block | T1 acq | Clear | JJA/DJF | Hottest | Mean | Max | Air | Coolest | vs 2 km | ASOS | Hot flags | r(rel, shaded%) | GOES max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| austin-tx | 102 | 52 | 8/17 | 2025-08-08 | 46.8 | 47.2 | 34.4 | 2026-01-31 (10.8) | -3.0 / -1.9 | ATT 4.1 km (52/52) | 3/18 | -0.66 | 48.3 (18 h) |
| birmingham-al | 200 | 99 | 16/24 | 2024-06-24 | 47.1 | 48.1 | 34.4 | 2024-01-17 (-1.2) | +0.4 / -1.0 | BHM 4.6 km (99/99) | 1/4 | n<5 | 43.0 (11 h) |
| boulder-co | 237 | 147 | 41/35 | 2026-07-20 | 50.4 | 51.2 | 34.0 | 2026-01-24 (-9.8) | +1.0 / +1.2 | BJC 13.9 km (147/147) | 1/4 | n<5 | 42.7 (10 h) |
| cincinnati-oh | 94 | 51 | 14/7 | 2026-09-03 | 41.6 | 43.4 | 32.8 | 2025-02-20 (-7.5) | +0.6 / -0.3 | CVG 13.6 km (51/51) | 1/9 | 0.59 | 20.4 (5 h) |
| columbus-oh | 76 | 35 | 7/6 | 2026-07-02 | 41.6 | 43.3 | 32.2 | 2025-12-14 (-13.1) | -2.2 / -1.2 | OSU 7.5 km (35/35) | 3/28 | -0.40 | 19.7 (2 h) |
| columbus-oz | 76 | 31 | 6/7 | 2024-08-29 | 45.3 | 46.4 | 30.0 | 2025-12-14 (-13.8) | +0.0 / +0.1 | TZR 9.4 km (31/31) | 2/20 | 0.29 | 20.0 (3 h) |
| dallas-tx | 101 | 62 | 20/16 | 2026-08-19 | 51.6 | 52.0 | 36.1 | 2026-01-31 (4.1) | -0.3 / -0.3 | RBD 5.3 km (62/62) | 3/11 | -0.32 | 51.9 (18 h) |
| denver-co | 243 | 163 | 43/40 | 2026-07-20 | 50.0 | 50.9 | 35.3 | 2026-01-24 (-4.0) | -1.4 / -0.4 | BKF 17.2 km (162/163) | 2/18 | -0.40 | 42.6 (14 h) |
| huntsville-al | 194 | 105 | 23/22 | 2024-06-17 | 48.2 | 48.5 | n/a | 2024-01-17 (-2.9) | +3.4 / +2.5 | HUA 13.4 km (72/105) | 1/5 | -0.02 | 41.1 (14 h) |
| kansas-city-mo | 190 | 115 | 30/27 | 2025-08-17 | 44.7 | 45.6 | 33.9 | 2025-01-21 (-9.2) | +0.4 / -0.0 | MKC 13.2 km (115/115) | 2/14 | -0.00 | 30.5 (7 h) |
| minneapolis-mn | 89 | 49 | 11/6 | 2025-07-15 | 44.1 | 44.6 | 31.7 | 2025-12-14 (-18.4) | +0.7 / +0.5 | MSP 4.8 km (49/49) | 5/36 | -0.02 | 36.9 (18 h) |
| minneapolis-oz | 89 | 50 | 12/8 | 2025-07-15 | 42.2 | 43.6 | 32.2 | 2025-12-14 (-18.8) | -0.9 / -0.2 | MIC 5.8 km (50/50) | 2/17 | 0.68 | 36.9 (20 h) |
| phoenix-az | 126 | 105 | 30/22 | 2024-08-03 | 62.6 | 63.0 | 39.4 | 2024-01-08 (13.4) | +1.1 / +1.0 | SDL 8.4 km (105/105) | 2/13 | 0.32 | 64.7 (16 h) |
| saint-paul-mn | 89 | 51 | 11/8 | 2025-07-15 | 42.4 | 42.9 | 31.7 | 2025-12-14 (-18.1) | -0.0 / -0.0 | MSP 7.0 km (51/51) | 5/32 | -0.44 | 36.0 (17 h) |
| scottsdale-az | 194 | 124 | 29/25 | 2024-08-03 | 57.7 | 58.4 | 39.4 | 2024-01-08 (11.3) | +0.3 / +0.4 | SDL 5.0 km (124/124) | 1/7 | 0.46 | 61.9 (16 h) |
| tulsa-ok | 101 | 57 | 17/13 | 2026-08-11 | 45.3 | 46.6 | 35.0 | 2025-02-21 (-1.7) | -0.1 / -0.5 | RVS 8.3 km (57/57) | 2/6 | -0.74 | 46.8 (20 h) |

Totals: 2,201 Tier 1 acquisitions checked and 1,296 clear block scenes used.

Notes on the table:
- Cincinnati's hottest scene overall is 2026-09-03. Its summer grid uses the hottest June–August scene, 2025-06-28.
- On 2026-08-19 GOES found mostly cloud over Ohio and Kansas City, so those blocks have only 2–7 usable hours.

## Caveats

1. **30 m is not a roof.** One Landsat pixel (900 m², from a 100 m TIRS footprint) mixes several roofs, yards, trees and street. Each house value is that mixed pixel, which is why `pixel_note` is set on every house. Houses that share a pixel share a value and a flag. A ~100 m block contains only about 9–16 distinct pixels, so `hot_roof_flag` is a coarse "this corner of the block runs warm" signal, not a roof diagnosis.
2. **Skin temperature, not air temperature.** Overpasses are around 10:30–11:00 local solar time. Afternoon peaks run hotter; `goes_diurnal` shows the hourly shape for one day. The hottest-scene air temperatures (30–39 °C) are 10–23 °C below LST, which is normal for dry roofs and pavement.
3. **Emissivity.** The USGS ST algorithm uses ASTER GED emissivity at about 100 m. Metal roofs with low emissivity are under-corrected. ST_QA uncertainty for kept scenes is typically 1.5–3 K. Absolute values carry about ±2 K. Relative differences within one scene are better.
4. **The urban heat sign varies.** Austin (-3.0 °C) and Columbus OH (-2.2 °C) are cooler than their 2 km surroundings, which take in commercial and highway land. Huntsville (+3.4 °C) and Phoenix (+1.1 °C) run warmer. Phoenix's 2 km window includes irrigated parks, golf courses and water. Treat this as a comparison with the neighbourhood, not a formal UHI index.
5. **Air-temperature matching.** Stations are 4–17 km away. Huntsville HUA has gaps, with 72 of 105 scenes matched. For Phoenix the AWOS penalty picked SDL (8.4 km) over a farther ASOS.
6. **GOES pixel and quality.** A GOES pixel is 2 km. Western blocks only have medium-quality retrievals (DQF = 1). Use the curve only for diurnal shape.
7. **Sun context.** The correlation between measured relative LST and modelled shading is weak or inconsistent: from −0.74 (Tulsa, where more shade means cooler, as expected) to +0.68 (minneapolis-oz). That fits a 30 m mixed pixel, so it is kept as context only and never fused into a score.
8. **Coverage gaps.** Tier 2 scenes are excluded. Snow is kept, so the coolest winter scenes in MN and OH are snow surfaces (−13 to −19 °C).
9. **ECOSTRESS (70 m, variable time of day) is not ingested** because it needs an Earthdata login. If the user creates the free account and adds a `~/.netrc` entry, it could be added next. It would give afternoon passes.

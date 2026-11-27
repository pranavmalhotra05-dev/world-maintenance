# Swimming pools across the City of Sacramento: detection, water state per NAIP year, parcel ownership

Script: `usa/pools_area.py`. It reuses `usa/pools.py` (normalisation, water test, `site_stats`, `classify`) and
`usa/parcels_hoa.py` (`query`, `PARCEL_SRC`) without editing them.

```
python usa/pools_area.py prep                     # TIGER boundary, 1 km tiles, Overture buildings split per tile
python usa/pools_area.py detect --workers 6       # heavy, resumable: one cache file per finished tile
python usa/pools_area.py finalize                 # states with per-tile references -> web JSON (seconds)
python usa/pools_area.py render --tiles 630_4267  # local accuracy PNGs (needs detect --keep-img); never published
```

## Area and data (green licences only)

| what | source | licence |
|---|---|---|
| city limits | US Census TIGER/Line 2024 Places, TIGERweb `tigerWMS_ACS2024` layer 28, GEOID 0664000 "Sacramento city" (land 255.5 km²) | public domain |
| imagery | USDA NAIP via Planetary Computer STAC `naip`: 2012, 2014 (1 m), 2016–2024 (0.6 m), every year RGB+NIR, June–July flights | public domain |
| parcels | Sacramento County GIS Parcels (`PARCEL_SRC["Sacramento County"]`). Only `APN, STREET_NBR, STREET_NAM, CITY` are requested, never owner, mailing or sale fields. | CC0 1.0 |
| buildings | Overture Maps buildings (release 2026-11-18.0): 262,898 in the city bbox | ODbL / CDLA-Permissive-2.0 |

- OpenAerialMap has one CC BY drone image in the city (2019, about 170 × 170 m). It covers less than 5 % of a tile grid, so it is not used.
- The open-orthophoto catalogue (`web/usa/data/imagery_sources.json`) has no Sacramento entry.

**Tiles.** UTM 10N squares of 1 km. A tile id is `<E km>_<N km>`.
- 338 tiles touch the boundary.
- Each tile is analysed on a 0.5 m grid with a 40 m margin.
- A pool is reported once: by the tile that contains its centre, and only if the centre is inside the city boundary.

## Method (differences from `pools.py` and why)

1. **Imagery per tile.** There is one mosaic per NAIP year (the latest flight, with gaps filled from the others). It is stored as uint8 in a temporary tile folder, which is deleted when the tile is done.
2. **Co-registration.** Each year is shifted by a whole number of pixels onto 2024, using phase correlation of the high-passed brightness. In the four tuning tiles the shifts were 0–2 px (≤ 1 m), so NAIP years agree well here.
3. **Masks that replace the LiDAR "> 1.5 m above ground" test.** There is no LiDAR in this lane.
   - **Roofs:** the Overture footprints, *eroded* by 1.5 m, mask roof interiors only. A +1 m buffer was tried first. Footprints sit 1–3 m off NAIP (building lean and misregistration), so the buffer erased pools next to houses (see the accuracy notes).
   - **Trees:** the per-snapshot NDVI test inside the water rule (relative NDVI < −0.05; −0.15 for the shade rule). Leaf canopy is never water, so a pool under canopy is missed rather than misread.
4. **Water pixels.** The `pools.features` rule is used unchanged. Two additions in `load_snap`:
   - **Shade water:** relative NDWI > 0.55, relative NDVI < −0.15, b > 1.15 r, g > r, and brightness > 0.25 × the reference. Water keeps its NIR absorption in shade; roofs and asphalt do not reach NDWI 0.55. This catches pools in house or tree shade and pools with a dark finish.
   - **Pool-blue surface:** the RGB colour rule without NDWI. Blue solar or safety covers are NIR-bright, so they fail NDWI. In Pocket, several pools sat under such covers on 5–6 of 7 flights.
5. **Sites.** Blobs of (water | blue) from every year are accumulated, and the geometry is fixed as in `pools.py`.
   - Size: 5–1500 m² of detected pixels. NAIP at 0.6 m catches only about 40–70 % of a small pool's area, so 12 m² of *detected* pixels missed 3 × 5 m pools.
   - Shape: short side ≥ 1.8 m, aspect ≤ 5 (≤ 6 when large), fill ≥ 0.6 (≥ 0.5 when large).
   - A site is **kept** when one of these holds:
     - NDWI water on ≥ 2 years;
     - water once, plus pool-blue on another year;
     - water on part of the site once (≥ 10 %), plus pool-blue on ≥ 3 years (a pool under a solar cover);
     - water only on the newest year (a new pool).
   - Blue alone never counts, so tarps and trampolines are not kept.
6. **States per year.** These come from `pools.classify` on the fixed core pixels, with two extra steps:
   - The reference pools are the tile's own clean pools. When a tile has fewer than 4 clean pools on that snapshot, the 3 × 3 tile window is used instead.
   - The colour-cast guard runs per tile, again falling back to 3 × 3 when fewer than 6 pools were judged.
   - Then come the `pools.py` `not_present` (before the first water) and `filled_or_removed` (after it) rules.
   - **Extra rule:** a `not_present` verdict with pool-blue on at least 30 % of the site becomes `dark` (canopy overhang, colour not judged). Land Park renders showed visible pools under overhanging trees being called "filled".
7. **Ownership.**
   - **Parcel.** The pool centre is tested against the Sacramento County parcels. The smallest containing parcel gives the APN and the situs address (`STREET_NBR STREET_NAM, CITY`).
   - **Building.** The main building is the largest Overture building whose representative point lies in that parcel.
   - **kind = residential** needs all of: a pool of 150 m² or less, a parcel with a street number, a parcel of 4000 m² or less, and fewer than 3 buildings of 100 m² or more on the parcel.
   - **kind = community / common lot** covers everything else that sits in a parcel (HOA lots, apartments, schools, clubs).
   - **No parcel:** kept as `unknown` / "no parcel", or `community` when larger than 150 m².
   - Parcel rings are stored only for parcels that hold a pool.
8. **Confidence** is 0.35, plus 0.12 per water year (up to 4), plus 0.10 if fill > 0.8, plus 0.05 if aspect < 3.

## Output

- `web/app/data/pools_area/sacramento/index.json` holds:
  - the boundary polygon;
  - the tile list (bbox, file, counts);
  - the snapshots (`naip_<year>` with the median date and date range per year, res, NIR);
  - the sources, licence note and totals.
- `web/app/data/pools_area/sacramento/<tx>_<ty>.json` holds:
  - the pools in the schema of the brief: `id, ring, area_m2, centre, states{snapshot_id: state}, latest_state, green_years, first_seen, confidence, parcel{apn, address, ring, area_m2, building_id, building_area_m2}|null, building_ring, kind, ownership`;
  - the per-snapshot reference basis and colour-cast flags.

Rings are the detected consensus pixels. Only collinear staircase vertices are removed; there is no smoothing. Coordinates are WGS84 with 6 decimals.

## False-positive screen (finalize stage)

The first city finalize (128 tiles) gave 13,350 sites: 3,298 community and 1,645 no-parcel. Two checks showed most of those were not pools.
- **Stats.** Their water years were mostly dark (brightness below 0.55 × the reference) with a deep-blue hue of 225–240°. Residential pools have a cyan hue of 195–215° and are bright.
- **Chips.** On the tuning-tile imagery, 21 non-residential chips were mostly dark asphalt streets, parking lots and a pond. The shade-water rule fires on fresh asphalt and on river water on some flights.
- **Parcels.** River and wetland parcels each held hundreds of "sites". One example is APN 27401200060000 on the American River parkway, with 324 sites in one tile.

Rules, applied in `finalize` only:

- A **pool-like year** has water_frac ≥ 0.4, brightness ≥ 0.55, a hue of 185–222° and greenness < 0.
- **community / no-parcel:** the site is dropped if it has fewer than 2 pool-like years. It is also dropped if it has 2 or fewer pool-like years and its parcel holds 5 or more such weak sites (a pond, wetland or basin cluster). With exactly 2 pool-like years it is kept as `check: needs check`.
- **residential:** the site is kept, because chips showed real pools with dark or green finishes and pools in shade. With no pool-like year, water on at most 1 flight and no cover years, it is flagged `needs check` and confidence is capped at 0.5. With no pool-like year but more water years, it gets the note `dark / green finish or shaded`.
- Each pool now carries `pool_like_years` and `check`. Dropped counts go to `totals.dropped_not_pool_water`.

## Results (partial: 128 of 338 tiles done when the run was paused for the Scottsdale lane)

| | count |
|---|---|
| pools kept | 8,696 |
| residential (house parcel with address) | 8,407 (605 flagged `needs check` overall) |
| community / common lot | 273 |
| no parcel | 17 (16 `unknown` + 1 large) |
| dropped by the FP screen | 3,025 community-lot sites, 1,629 no-parcel sites |
| latest state | 5,333 clean_blue, 1,485 turquoise, 362 green_algae, 941 covered_or_empty, 261 dark, 312 filled_or_removed |
| green on at least one flight | 1,927 (by year: 2012 420, 2014 441, 2016 268, 2018 374, 2020 369, 2022 797, 2024 354) |
| ownership | 8,679 in a parcel (99.8 %); 8,655 with a situs address; 8,536 with a main Overture building |

The colour-cast guard fired on a few tiles in every year (2–10 tiles per year). In those tiles, green verdicts became `greenish_uncertain`.

9 tiles lack one NAIP year (download failure). They will be re-fetched on resume: delete their tile cache, then run `detect` again.

## Accuracy check (manual, local renders only)

- **Tuning.** Four tiles: Land Park 630_4267, Pocket 627_4261, East Sacramento 635_4269 and North Natomas 630_4278 (no pools).
- **Changes found by eye during tuning:**
  - the roof buffer erased pools next to houses; fixed by eroding the footprints;
  - small pools fell below 12 m²; the minimum is now 5 m²;
  - shaded pools were missed; fixed by the shade rule;
  - pools under solar covers were missed; fixed by the blue-cover rule;
  - canopy-overhung pools were called "filled"; they are now `dark`.
- **Hand count after tuning.** Ten windows of 125–135 m in the three residential tiles:
  - about 40 true pools detected;
  - 1 clear false positive (a wall shadow) plus about 4 uncertain;
  - about 4–5 missed: small or shaded pools, one bright pool partly cut by an offset footprint, and an above-ground pool.
  - **Residential tuning tiles:** precision about 0.90–0.97, recall about 0.88–0.92.
- **Residential sites with no pool-like year.** These are about 24 % of residential sites citywide. In a 48-chip sample, about 33 were real (dark or green finishes, shade), about 10 false (parking, paving, roof edges) and about 5 unclear.
  - **Overall residential precision:** about 0.90.
  - **`needs check` subset alone:** about 0.5.
- **Community after the screen.** Sites with 3 or more pool-like years looked real in the sampled chips (apartment or HOA pools, city pools such as the parks on Alhambra Blvd and Freeport Blvd). Sites with exactly 2 years are mixed. Estimated precision about 0.8.
- **No parcel (17).** These are mostly backyard pools in gaps of the parcel layer. Estimated precision about 0.6–0.8 (small sample).
- **Recall** outside residential areas was not measured.
- **Limit of the check.** Imagery was kept only for the tuning tiles, so the citywide screen was validated on those chips plus the per-site statistics, not on chips from the downtown or river tiles.

## Limitations

- **Snapshots on capture days only.** States describe a June or July day every 2 years, not how a pool is maintained.
- **What NAIP at 0.6 m misses:** spas, plunge pools, pools under canopy, and pools covered on every flight.
- **Area is underestimated.** `area_m2` counts detected water pixels, which are about 60–80 % of the true pool area.
- **Parcel quirks.** A pool on a lot line goes to the parcel that contains its centre. Rows without a street number in the parcel layer become `community`.

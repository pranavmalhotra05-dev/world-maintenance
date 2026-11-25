# Swimming pools: presence and clean / green state from open aerial imagery

Script: `usa/pools.py`. It writes `h.pool` (per house) and `meta.pools` (per block) into `web/usa/blocks/<slug>/`.
It writes only those two keys, under `usa.blocklock.block_lock`, using tmp + `os.replace`.
Imagery is cached in `usa/_cache/pools/<slug>/`. Visual check strips go to `usa/_cache/pools/samples/`.

```
python usa/pools.py --slug tulsa-ok --samples          # one block, plus RGB check strips
python usa/pools.py --all                              # every block with a meta.json
python usa/pools.py --slug _calib_x --calib LON LAT 50 --samples   # calibration anywhere, writes nothing
```

## Imagery: only free sources that allow commercial use

| source | resolution | cadence | bands | licence | role |
|---|---|---|---|---|---|
| USDA NAIP via Microsoft Planetary Computer STAC `naip` | 1 m (2010–2016), 0.6 m (2017+), 0.3 m (some 2022+ states such as AZ 2023) | about every 2 years, leaf-on summer | RGB + NIR (all years tested) | public domain (USDA FSA) | main per-pool source |
| OpenAerialMap (`api.openaerialmap.org/meta`) | drone 2–10 cm, aerial varies | irregular | RGB | CC BY 4.0 / CC BY-SA / CC0 only; NC licences are skipped and logged | extra snapshots when one covers the block |
| state / county orthophotos listed in `web/usa/data/imagery_sources.json` (from `usa/imagery.py`) | 0.076–0.6 m | often yearly | RGB, sometimes NIR | only `commercial_ok: true` aerial/drone entries | extra snapshots via ArcGIS ImageServer `exportImage`, with the extent checked first. In use now: TxGIO StratMap 2020 Capital Area (CC0 1.0, 7.6 cm, RGB+NIR, **leaf-off**) for austin-tx. Not yet wired: MnGeo/Met Council WMS (leaf-off spring) and DRAPP 2022 Denver (S3 GeoTIFF tiles plus a tile index). Leaf-off and spring flights often show pools covered or drained, so `covered_or_empty` on them is expected. |
| Sentinel-2 L2A via Planetary Computer `sentinel-2-l2a` | 10 m | every 5 days (one clear summer scene per year is used) | B03, B08 | Copernicus Sentinel data, free and open including commercial use | **neighbourhood-level signal only** |

We never use Google, Esri World Imagery, Nearmap, Planet, or Maxar.
Each per-snapshot record in `h.pool.by_year` carries its `source`, `date` and `gsd_m`. `meta.pools.imagery_dates` records the licence of each snapshot.

## Method

1. **One grid.** Every snapshot is resampled (bilinear) onto one 0.5 m grid. The grid covers the block plus a **100 m context ring**, in the block's local metres with row 0 at the south.
2. **Radiometric normalisation per snapshot.** NAIP digital numbers are not reflectance. Exposure, haze and colour balance change between flights, so the script normalises each snapshot in three steps:
   - **Dark-object subtraction:** it subtracts each band's 0.5th percentile. Haze adds a mostly blue offset, which otherwise makes asphalt and shadows look like water.
   - **Grey-world balance:** each band is scaled so that the snapshot's non-vegetated, mid-to-bright pixels (roofs, paving, bare ground) are neutral grey.
   - **Relative indices:** NDWI = (G−NIR)/(G+NIR) and NDVI are expressed *relative to* the same reference pixels. Absolute NDWI of plain roofs drifted from −0.15 to +0.15 between flights in our tests.
3. **Water pixels.** A pixel counts as water when all of these hold:
   - relative NDWI is above 0.40, and relative NDVI is below −0.05;
   - balanced blue is above 1.25 × red, green is above red, and (b−r)/brightness is above 0.40 (saturated cyan or blue);
   - brightness is at least 0.55 × the reference (not shadow);
   - the pixel is not within 0.6 m of a building footprint, and it is not under any LiDAR return more than 1.5 m above ground (roofs, trees). Footprints and LiDAR exist inside the block only.
   - On RGB-only snapshots the colour rule is stricter.
4. **Pool sites with fixed geometry.**
   - Blobs of 7–225 m² from every snapshot are accumulated into one site mask.
   - A site is kept if it meets three shape tests: aspect ratio up to 5 (lap pools included), at least 60 % of its convex hull filled (kidney shapes included), and a short side of at least 2.2 m.
   - The site must also be water on **at least 2 snapshots**, or only on the newest one (a new pool). This removes cars, glare and single bad flights.
   - A **consensus core** (pixels that were water on at least half as many snapshots as the best pixel) absorbs the 1–3 m misregistration between NAIP flights.
   - `area_m2` is the median area of water pixels on the snapshots that saw water. It must fall within 12–150 m².
   - Because the geometry is fixed, a pool is still measured on snapshots where it is green, covered, drained or shaded.
5. **State per snapshot,** judged on the core pixels:
   - `not_present`: vegetation (relative NDVI above 0.25 with no water, or strongly green hue) before the pool first held water.
   - `filled_or_removed`: the same signal *after* the pool had held water.
   - `dark`: brightness below 0.55 × the reference without a strong water signal (tree, house or cloud shadow). Colour is not judged.
   - `covered_or_empty`: weak or no water signal (relative NDWI below 0.15) and not green. This covers a cover, a drained shell, or a tarp.
   - `green_algae`: greener than the neighbourhood's clean pools on the same snapshot. The rule is: greenness (G−B)/(G+B) more than 0.07 above their median, at or above −0.01 in absolute terms, and above their 75th percentile + 0.03. A greenish site with a weak water signal also counts, because algae raise NIR. If fewer than 4 reference pools exist, absolute thresholds apply: greenness above 0.03 or hue below 165°.
   - `turquoise`: in between (relative greenness above 0.03 or hue below 180°).
   - `clean_blue`: none of the above.
   - **Reference pools:** these are all pool sites in the 300 m window that show clean water on that snapshot (at least 3). On weak-water flights, the script falls back to all known pool sites (at least 4). This is the relative "compared with the other pools that year" control for camera and illumination.
   - **Colour-cast guard:** if more than 25 % of the window's pools (at least 6 judged) turn green on the same flight, the flight is treated as a colour cast, not algae. Those verdicts become `greenish_uncertain` and do not count in `green_years`. Arizona NAIP 2013 did this: 54 % of the pools in the Phoenix calibration window looked olive.
6. **Assignment.** Each in-block site goes to the nearest non-garage footprint, if that footprint is within 20 m. If a house has several sites, the nearest is reported (`house_dist_m`) and the count goes in `note`. Sites with no house within 20 m are counted in `meta.pools.in_block_sites_unassigned`; these are usually yards of houses missing from the Overture footprints.
7. **Confidence** (0–1) is built up as follows:
   - base 0.35;
   - +0.12 per snapshot with water, up to 4 snapshots;
   - +0.10 if the site's fill ratio is above 0.8;
   - +0.05 if its aspect ratio is below 3;
   - +0.10 if more than 30 % of the site's 1 m cells had no LiDAR return (water often returns no laser pulse). This is supporting evidence only, because the LiDAR date usually differs from the imagery dates.
8. **Sentinel-2** (`meta.pools.sentinel2_neighbourhood`): for each summer since 2017, the script takes the least-cloudy scene (under 10 % cloud). It reports the mean NDWI of the 10 m pixels around the known pool sites minus the rest of the window. A rising contrast means more open water in the neighbourhood. A 10 m pixel mixes a 40 m² pool with its yard and deck, so this is never a per-pool colour or state.

## Output

`h.pool` (non-garage houses; garages get `null`):
```
{present, confidence, area_m2, first_seen_year, x, y (local m), house_dist_m,
 by_year: [{year, date, source, gsd_m, state, greenness, greenness_rel, hue, ndwi, ndvi, water_frac, rgb}],
 green_years, last_state, lidar_no_return_share, note}
```
In `by_year`, `ndwi` and `ndvi` are relative to the snapshot's paved/roof pixels, and `greenness_rel` is relative to the reference pools.

`meta.pools`:
```
{n_pools, houses, share_houses_with_pool, green_now, green_ever, context_pools, context_m, in_block_sites, in_block_sites_unassigned,
 imagery_dates: [{date, source, licence, gsd_m, nir, reference_pools, reference_basis, colour_cast}],
 method, licence, sentinel2_neighbourhood, limitations, generated}
```

## Validation (visual, `usa/_cache/pools/samples/`)
- **Phoenix calibration window** (Arcadia, about −111.955, 33.497; 300 m): 20 pool sites over 7 NAIP flights from 2010 to 2023. Every sampled "blue" strip is a real pool.
  - One pool shows green in 2013 and dark green in 2017, drained (`covered_or_empty`) in 2019, and blue again in 2021 and 2023. This matches the imagery by eye.
  - A second pool is green in 2017, filled in 2019, and lawn in 2021 and 2023 (`filled_or_removed`).
- **Tulsa block:** two pools built in about 2019 are detected as `not_present` (lawn) in 2010–2017 and `clean_blue` from 2019. A kidney pool is detected as present from 2015.
- **Austin and Dallas blocks:** tree-covered streets with no in-block pool water visible on NAIP. After the haze correction and the 2-snapshot rule, there are 0 in-block detections. Before those fixes there were up to 13 false sites, all on asphalt and shadows.

## Results on the current blocks (2026-11-24)
| block | houses | pools | green ever | context pools (300 m window) | notes |
|---|---|---|---|---|---|
| phoenix-az | 13 | 5 | 1 (2015) | 27 | AZ NAIP 2013 flagged as a colour cast |
| scottsdale-az | 6 | 4 | 0 | 50 | 9 in-block sites; 5 unassigned because Overture has only 6 of the block's houses |
| tulsa-ok | 5 | 2 | 0 | 4 | both pools built about 2017 (lawn before) |
| kansas-city-mo | 13 | 1 | 0 | 1 | 11 NAIP flights (2012–2023), blue on 10 |
| austin, dallas, birmingham, boulder, cincinnati, columbus x2, denver, huntsville, minneapolis x2, saint-paul | – | 0 | – | 0–7 | tree-covered streets or a northern climate |

## Limitations (read before using the output)
- **Snapshots, not monitoring.** NAIP flies each state about every 2 years, on one summer or autumn day. A state describes that day only.
  - The script cannot tell how often a pool is cleaned, or whether it is green most of the year.
  - A single `green_algae` may be a temporary algae bloom, a recent opening, or a just-shocked pool.
  - Yearly county orthophotos or OpenAerialMap drone images, where available, add snapshots but not continuity.
- **Old NAIP (2010–2015, 1 m) is weak.**
  - Small pools (under 20 m²) are only a few pixels, and their edges mix with the deck.
  - Water signals are low, so states on those flights lean to `covered_or_empty` or `dark`.
  - Expect most first detections in 2015–2017, even for older pools. `first_seen_year` is "first year detected", not "year built".
- **Missed pools:**
  - pools under tree canopy or in building shadow;
  - pools covered on every flight;
  - pools that are green on *every* flight, because sites are only found from snapshots with clean water;
  - pools below 12 m² (spas and plunge pools);
  - indoor pools.
- **False positives:**
  - blue tarps, blue-painted courts or roofs outside footprints, and sky reflections on glass in the context ring (no footprints or LiDAR there);
  - fountains and ponds.
  - Inside the block, footprints and LiDAR remove roofs.
- **Colour states are indicative.** Greenness depends on sun angle, depth, and pool finish (dark-bottom pools look teal). For these reasons the script judges colour relative to the neighbourhood's other pools on the same flight. With fewer than 4 reference pools nearby, it falls back to absolute thresholds, which are less reliable.
- **Assignment uses distance** to the nearest house (national parcels are not open). A pool on a shared lot line can go to the neighbour.
- **Thresholds were tuned by eye** on Phoenix, Tulsa, Austin and Dallas samples. This is not a labelled accuracy study, so treat `green_algae` as "worth a look", not as a code-enforcement finding.

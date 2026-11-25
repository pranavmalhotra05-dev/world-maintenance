# USA LiDAR viewer: every USGS 3DEP point, streamed on demand

**Page:** `web/lidar/index.html`. Serve `roof-intel/web` with `python -m http.server 8765` and open
`http://localhost:8765/lidar/`.
**Data:** USGS 3DEP LiDAR in the public USGS Entwine Point Tile (EPT) bucket
`https://s3-us-west-2.amazonaws.com/usgs-lidar-public/<project>/ept.json`. It is US Government work, so it is public domain. The bucket sends `Access-Control-Allow-Origin: *`, so the browser streams it directly. There is no proxy, no key and no sign-up.
**Viewer engine:** Potree 1.8.2 (BSD-2-Clause), vendored in `web/lidar/potree/` (about 38 MB).

## What it does

| Feature | How |
|---|---|
| Pick any US location | Census geocoder for addresses (public domain; JSONP because it sends no CORS headers). You can also type `lat, lon`, click the canvas overview map (state outlines from `web/usa/data/states.geojson`, shaded 3DEP coverage, our blocks drawn as squares, wheel to zoom, drag to pan), use the presets (Iowa farmland, Kansas wheat, Smokies forest, Olympic rainforest), or click a block button. |
| Find every project at that spot | `web/lidar/ept_boundaries.geojson` is a copy of `data/national/ept_boundaries.geojson`: 2,284 projects, coordinates rounded to 1e-5° (about 1 m), plus a bbox per feature (4.3 MB). Every project that intersects a square of the chosen radius (100 m to 10 km) is listed with year (parsed from the name), name and point count. Projects that contain the exact point are marked. |
| Overlapping projects and years | One checkbox per project. The project at the point (newest first) is shown by default. **Show all** and **Newest only** buttons are provided. A project's `ept.json` is fetched only when its box is first ticked. **Rescan at view** lists the projects again around the current 3D view centre. |
| Point budget | Slider from 1 to 30 M, default 4 M. The page says: *"display budget only — all points stream at full resolution as you zoom in; no data is removed"*. |
| Full-detail guarantee | See the section below. |
| Colouring | Elevation (Turbo gradient; the auto range is local LiDAR ground −15 to +30 m and follows the ground under the view centre; editable), intensity (adjustable max), ASPRS classification with a legend (every class always visible), RGB (falls back to elevation when the project has no Red/Green/Blue in its schema), return number, flight line. |
| Fine points | Points are **fixed-size screen dots, 1.5 px circles** by default, so they never swell into boxes up close. A "Point size" slider (0.5–4 px) and a circle/square "Point shape" picker are provided. EDL is on but subtle (strength 0.4, radius 1.0) and can be turned off. (Before this change, Potree's ADAPTIVE size and square shape rendered as stacked blocks.) |
| Firm ground | The camera can never go below the LiDAR ground. See the "Ground clamp" section. |
| True scale | EPT data is EPSG:3857, which stretches ground distances by 1/cos(lat). The default is to rescale x/y by cos(lat) around the chosen point, so one unit equals one ground metre and Potree's measuring tools read true distances. The toggle switches back to raw 3857. A Z× exaggeration slider is also provided. |
| Tools | The **Measure / tools** button opens Potree's own sidebar: distance, area, height, profile, clipping, scene tree. |
| Navigation | Links to `../usa/globe.html` (that file did not exist yet when this was written; another lane owns it), `../usa/index.html` and `../usa/view.html?block=<slug>`. Shift-clicking a block button opens the 3D block. `?lat=&lon=[&r=][&project=][&block=][&budget=]` deep-links into the viewer. This is the globe's entry point: `../lidar/index.html?lat=..&lon=..&r=<metres>` selects the spot, lists the projects within radius r (default **1000 m**; any value is accepted and added to the picker), and flies the camera in from high above (1.4 s ease). The URL keeps `lat/lon/r` updated as you move. |
| Streaming stats | Live readout: points drawn, nodes, deepest depth drawn, and whether the target column (±15–150 m around the view centre) is **FULL RES**. `window.lxStats()` returns the same as JSON. |

## Never thinning: how full resolution is guaranteed

EPT is an additive octree. Each recorded point is stored in exactly one node. Coarse nodes hold a spread-out sample, and deeper nodes hold the rest. Drawing every node from the root down to the deepest leaf over an area therefore draws every point in that area. Nothing is discarded at any stage.

* **Zoomed out:** Potree draws a level-of-detail subset that fits the display budget. This is a display choice; the data is untouched.
* **Zoomed in:** Potree refines by screen-space size, so the nodes near the camera are refined down to EPT leaves.
* **Patch 1** (`potree.js`, `updateVisibility`, marked `roof-intel patch`): the stock Potree loop did `break` on the first node that did not fit the budget. In dense forest, nodes hold 400–480 k points, so one big mid-level node would stop every smaller, nearer leaf from loading. The loop now does `continue`.
* **Patch 2** (page logic): if any EPT node over the target column is still missing after loading goes idle, and the camera is under 250 m above the target, the budget is raised ×1.5 per step, up to 40 M, until the column is complete. The stats show "(auto-raised for full detail)". The budget returns to the user's value when the camera goes back above 800 m or the location changes.
* **Patch 3** (`potree.js`, near plane, marked `roof-intel patch`): Potree set `camera.near = spacing × 10`, which is about 12 m at EPT leaves and clipped everything near an eye-level camera. It is now capped at 0.2 m.
* **Check:** "complete at target" means every drawn node over the target square either has no children (it is an EPT leaf) or all of its children over the square are drawn too.

## Ground clamp (camera never below the ground)

* **Ground model:** each drawn EPT node gets a lazily built grid. Cells are about 1.5 ground metres, built in the cloud's raw EPSG:3857 frame, so they survive the Mercator-scale and Z× toggles. Each cell stores the mean z of class-2 (ground) points and the minimum z of all non-noise points (classes 7 and 18 excluded).
* **Height lookup:** `groundAt(x, y)` looks only at drawn nodes over that xy, in cells within 3 m. It uses the class-2 mean, falls back to the lowest non-noise point, and takes the highest ground among the visible projects. A lookup costs about 0.1 ms. Before any data under the camera has loaded, the USGS EPQS ground at the picked point is used.
* **Clamp:** Potree's earth, orbit and first-person controls each have their `update()` wrapped. After every controls step, and before Potree copies the view into the camera, `camera.z` is held at least ground + 1.7 m × Z×. The vertical is true metres, since the Mercator correction only scales x/y.
* **Smoothing:** the floor rises instantly, so the camera never dips, and settles downward smoothly (15 % per frame), so walking downhill doesn't jitter.
* **Orbit pivot:** the earth-controls pivot is also clamped to at least the ground, so orbiting never swings the camera under the terrain.
* **Tests:** the camera was pushed 20–70 m under the ground, flown 400 m along the view ray into a mountainside, and dragged down 150 m, in both Smokies and Iowa. Every time it ended at 1.70–1.9 m above the LiDAR ground (5 consecutive pushes while moving: 1.83, 1.70, 1.70, 1.70, 1.70 m).

## Test results (built-in browser, 2026-11-24, localhost:8765)

The independent check below walks the project's own `ept-hierarchy/*.json` pages from S3. It lists every EPT node whose xy footprint contains the target point, then confirms that each node is loaded and drawn and that the point counts match the hierarchy.

| Area | Project(s) | Camera | EPT nodes over point | Drawn | Deepest depth | Points in column (hierarchy = drawn) |
|---|---|---|---|---|---|---|
| Central Iowa farmland 42.05, -93.45 | IA_NorthCentral_2_2020 (2 projects listed) | 40 m | 12 | 12 | 11 (leaf, 27,060 pts) | depth-11 leaf node verified; full column depth 0–11 drawn |
| Kansas wheat 38.36, -98.76 | KS_Statewide_B8A_2018 **and** KS_BartonCo_2010 overlaid | about 40 m | 10 / 11 | 10 / 11 | 9 / 8 | 263,065 = 263,065 and 674,052 = 674,052 |
| Smoky Mountains forest 35.61, -83.49 | USGS_LPC_TN_Eastern_2_16_B16_Del2_2016_LAS_2017 (3 projects listed, incl. 2011 Smoky Mtns) | about 20 m | 14 | 14 | 11 | 1,955,225 = 1,955,225 (budget auto-raised to 13.5 M) |
| Our Dallas TX block 32.726, -96.888 | TX_Pecos_Dallas_B3_2018 | about 40 m | 11 | 11 | 10 | 340,750 = 340,750 (no raise needed at 4 M) |
| Re-test after fine points and ground clamp: Smokies | same | 40 m | 14 | 14 | 11 | 1,956,592 = 1,956,592 at the **4 M** budget (no raise needed after the patches) |
| Re-test after fine points and ground clamp: Iowa via `?lat=42.05&lon=-93.45&r=1500` | IA_NorthCentral_2_2020 | 40 m | 12 | 12 | 11 | 265,369 = 265,369; fine 1.5 px dots with individual scan lines visible at 12 m and at 1.7 m eye level |
| Address search "1600 Pennsylvania Ave NW…" | USGS_LPC_MD_VA_Sandy_NCR_2014_LAS_2015 | — | — | — | — | Census geocoder JSONP works |

* Before patch 1 and the auto-raise, the forest test found one 3,923-point leaf, directly under the camera, that never loaded at the 4 M budget. That is why both fixes exist.
* There were no failed resource loads (no `performance` entries with status 4xx). The only console errors were 404s from my own debug fetches and the server's `/favicon.ico`; the page now has an inline empty favicon.
* Mobile width (375 px): the panel fits, there is no horizontal scroll, and the 3D view shows below the panel.

## Why it cannot run as a claude.ai artifact

Artifact pages run under a strict Content-Security-Policy. They cannot fetch from `s3-us-west-2.amazonaws.com`, `geocoding.geo.census.gov` (JSONP script) or `epqs.nationalmap.gov`. They also cannot spawn Potree's decoder Web Workers or load its WASM from sibling paths, and 38 MB of vendored files is impractical there. So the viewer runs **locally** (`python -m http.server` from `roof-intel/web`) **or on the owner's own static host** (any HTTPS static host; no server code is needed).

## External services used at runtime (all free, no key)

| Service | Use | Terms |
|---|---|---|
| USGS 3DEP EPT on AWS (`usgs-lidar-public`) | All point data | US public domain (USGS); AWS Open Data registry |
| US Census Geocoder | Address search | US public domain (Census Bureau) |
| USGS EPQS (`epqs.nationalmap.gov`) | Ground elevation at the picked point, used to aim the camera and set the elevation colour range | US public domain (USGS) |

## Software licences (everything vendored in `web/lidar/potree/`)

All are permissive and allow commercial use. Licence texts ship inside the folders.

| Component | Licence | Notes |
|---|---|---|
| Potree 1.8.2 (`build/potree`, `LICENSE`) | BSD-2-Clause | © 2011-2020 Markus Schütz. **Modified:** one-line budget patch (see above). `potree.js.map` removed. |
| three.js r124 (`libs/three.js`) | MIT | |
| copc.js (`libs/copc`), the EPT/COPC parser used by Potree's EPT loader | MIT | github.com/connormanning/copc.js |
| laz-perf WASM (`build/potree/workers/laz-perf.wasm`, `libs/copc/laz-perf.wasm`) | Apache-2.0 | github.com/hobuinc/laz-perf (current COPYING). Loaded as a separate WASM module. |
| plasio laslaz.js and laz-perf.js (`libs/plasio`) | MIT (plasio); the old laz-perf.js build inside it predates the Apache relicence (LGPL-2.1 era) | Only used for raw .las/.laz files, not EPT. It is a separate file loaded at runtime (dynamic linking), so LGPL is acceptable. Delete `libs/plasio` if you want zero LGPL bytes (it is not needed for EPT). |
| jQuery 3.1.1, jQuery UI | MIT | |
| d3 v4 | BSD-3-Clause | |
| proj4js | MIT | |
| OpenLayers 3 | BSD-2-Clause | Used by Potree's sidebar mini-map |
| i18next 1.8 | MIT | |
| jsTree | MIT | |
| spectrum (colour picker) | MIT | |
| tween.js | MIT | |
| BinaryHeap (Eloquent JavaScript) | MIT | |
| stats.js | MIT | |
| shapefile 0.6.2 (Mike Bostock) | BSD-3-Clause | |
| zstd-codec | MIT | Only for zstandard EPT; USGS uses laszip |
| brotli (JS decoder) | MIT | |
| json5 | MIT | |
| sql.js, geopackage-js (lazy libs) | MIT | Only loaded if a GeoPackage is exported or imported |
| matcap textures | CC0 / public domain | |
| navigation cube and camera icons | Freepik: "free for commercial use without attribution" | |

**Removed from the release because the licence was restrictive or unclear (and the files are unused):** `libs/Cesium`, `libs/geopackage` (duplicate), `libs/sql.js` (duplicate); textures `brick_pavement.jpg` (wildtextures terms forbid re-hosting), `skybox*` (provenance not stated, so the sidebar's "skybox" background option will 404); images `vr_controller_help.*` (CC-BY, VR only), `background.jpg`, `loading.jpg`; `models/` (Stanford bunny). `icons/map_icon.png` (from sitn PotreeViewer, licence not stated) was **replaced with our own drawn glyph**. Notes about these changes were added to the `resources/LICENSE` files.

## Known limits

* Z units are the project's native vertical units. Almost all 3DEP projects use metres (NAVD88), but a few older ones are in feet, which makes them look about 3.3× vertically stretched. Set the Z× slider to 0.3 for those projects. The EPT metadata has no vertical-unit field to detect this automatically.
* A very dense project at a very close, low-angle view can push the auto-raised budget to its 40 M cap. The stats then show "streaming / LOD … not drawn yet" honestly, rather than claiming full resolution. Moving the target or zooming in further completes it.
* Year is parsed from the project name. Some names, such as `IA_FullState`, have no year and show "—".

## Select an area: county or city at full resolution (added 2026-11-24)

**What it does.** The **Select area** button (panel) switches to picking mode. Click the overview map, or click a LiDAR point in the 3D view, and every US county and incorporated city (place) that contains the click is listed (both when both match). Hovering a choice outlines it on the map; clicking selects it. Inside the selected area every EPT node is loaded down to the deepest level (every recorded point). Outside it only a dimmed light preview is drawn. **Clear selection** returns to the normal behaviour. The URL keeps `?area=county:<5-digit FIPS>` or `?area=place:<7-digit GEOID>` (plus `&preview=1..6` when the preview spacing is not the default), so a selection can be deep-linked. `window.lxSelect("county:08013")` does the same from the console.

**Boundaries** (`web/lidar/boundaries/`, built by `usa/lidar_boundaries.py`):

* Source: US Census Bureau cartographic boundary files, 1:500k, latest vintage found (**2025**): `cb_2025_us_county_500k.zip` and `cb_2025_us_place_500k.zip` from `www2.census.gov/geo/tiger/GENZ2025/shp/`. Downloads are cached in `usa/_cache/lidar_boundaries/`.
* Licence: works of the US Government are public domain. *17 U.S.C. 105: "Copyright protection under this title is not available for any work of the United States Government"*. Commercial use is allowed; no key or sign-up. This is recorded in `boundaries/index.json`.
* Counties are built from the Census 500k file rather than `web/usa/data/counties.geojson`. That file is rounded to 0.001° (about 100 m) and carries no names, which is too coarse for an exact clip edge.
* Places: incorporated places only (LSAD ≠ 57). Census-designated places are excluded, as the owner asked for incorporated cities.
* Geometry is simplified with Shapely (topology preserved) to 0.0002° (about 20 m), rounded to 1e-5°, and split per state into `counties_<ST>.json` and `places_<ST>.json`. Each feature carries GEOID, name, NAMELSAD, state, ALAND, AWATER and bbox. There are 56 states and territories, 3,235 counties and 20,025 places, 48 MB in total. The largest file is `places_TX.json` at 2.5 MB, so every file is ≤ 3 MB. The script raises the tolerance automatically if a file would exceed 3 MB; none needed it.
* `index.json` holds, per state: FIPS code, bbox, file names, counts, plus the vintage and licence. The page loads only the index and then the one or two state files under a click.

**Projects and estimates.** The area is sampled on a 48×48 lattice. Every EPT project in `ept_boundaries.geojson` that contains samples (or touches the polygon) is listed with the share of the area it covers. The default display set is a greedy cover: the project covering most of the area is taken first, newest on ties. Further projects are added only while each adds ≥ 5 % of the area, since every overlaid project costs GPU time; slivers are left to the user to tick. Shown for the area:

* Census land and water area.
* A quick density estimate: project points ÷ project area × covered share.
* A hierarchy walk per displayed project. It fetches `ept-hierarchy/*.json` pages only below nodes that touch the polygon, at most 2 at a time, so the point data keeps the browser's ~6 connections per host. It counts every node touching the area (inside / on the edge), the deepest depth, and the points inside (edge nodes weighted by a 5×5 sample of the polygon). The walk stops at 1,500 pages; county-sized areas of dense 2020 projects hit that cap, and the page says so.
* Progress: *loaded in view: drawn / total selection nodes* with a bar.

**Rendering policy** (patched into Potree's traversal and point shader, all marked `roof-intel patch` in `potree.js`; the page's hooks are in `Potree.lxSel`):

* **Polygon grid.** The selection polygon (all rings, holes and multi-part areas) is projected to EPSG:3857, the EPT frame, and put on a cell grid. Each cell stores the inside/outside state of a fixed reference point plus the list of polygon edges crossing it. The grid is built fine enough that a cell holds at most 64 edges, typically 1–5. A point is inside if the reference state, flipped once per edge crossing the segment from the reference point to the point, says so. The test is **exact**, not rasterised. Prefix sums over "cell has edges" classify any box in O(1) as outside, inside or straddling.
* **Traversal.** EPT nodes that touch the polygon are refined to the deepest level regardless of screen size, ordered nearest-first by distance from the camera to the node, so big areas stream outward from the camera. Nodes entirely outside stop at the **preview level** and are queued first, since they are small.
* **Preview density.** A slider sets the preview point spacing: 64 / 32 / 16 / 8 / 4 / 2 m, default 16 m. For each project this becomes an EPT level cap, so projects with different cube sizes get the same light preview. (A raw level number gave 10 M+ preview points for small-cube projects.)
* **Shader.**
  * Nodes fully inside are drawn normally.
  * Nodes fully outside are drawn dimmed (×0.42).
  * Nodes straddling the boundary run the exact grid test per point. Outside points are dimmed if the node is within the preview level and hidden if it is deeper. The selection edge is therefore exact for full-resolution data, and everything outside looks like the preview.
  * Potree's own clip polygons were not usable: they allow 8 vertices at most.
* **Budget.** The patched traversal counts in-view selection nodes skipped because of the point budget. While any are skipped and loading is idle, the budget is raised ×1.5 per step up to **20 M**, which can be changed with `?selcap=`. 20 M rather than the normal 40 M because of the integrated-GPU result below. The existing target-column auto-raise is unchanged. The budget returns to the user value on Clear.
* **No data removed.** Hiding and dimming happen in the vertex shader for display only. EPT data and Potree's node geometry are untouched, and `groundAt`/clamp still see every loaded point.

**Outline in 3D.** The boundary is drawn as two fat lines (three.js `LineSegments2`, MIT, from the vendored `libs/three.js/lines`): an amber glow plus a bright core, drawn on top. It is draped on the LiDAR ground. Each vertex (≤ about 2,000 per area) takes its z from a cheap 16×16 per-node summary (class-2 mean, else lowest non-noise point; ≤ 4,000 sampled points per node), using the deepest drawn node there. The drape refreshes every 3 s, time-sliced at 4 ms per frame. (The first version used the 1.5 m clamp grids and caused 300–600 ms stalls at county scale.) The 2D map draws the selection in amber.

**GPU pick.** Potree's pick (`readPixels`) stalls the GPU pipeline, so `viewTarget()` (used by the stats and the auto-raise) now skips it when the camera is more than 3 km above the ground and uses the ground plane instead. At county altitude the 700 ms stats tick had been costing 300–900 ms per call.

### Tests (built-in browser, 2026-11-24)

* **Small city: Zearing, IA** (`?area=place:1987555`, 1.94 km², IA_NorthCentral_2_2020).
  * The hierarchy walk found **208 nodes** touching the town (119 inside, 89 on the edge, depth ≤ 11), about 6.5 M points.
  * After about 15 s: **208 / 208 drawn** (7.64 M points incl. edge nodes). The deepest drawn depth inside was 11. Outside nodes were capped (levels 1–5 only, with the old level-5 preview).
  * Independent check: for 3 points inside the town, every EPT node over the point was listed from the S3 hierarchy pages, and each one was drawn. The counts matched: 12/12 nodes, 247,479 = 247,479 points (depth 11); 11/11, 256,084 = 256,084 (depth 10); 12/12, 289,110 = 289,110 (depth 11).
  * For 2 points just outside the town, the depth 7–11 nodes over them were not drawn, as expected.
  * The clip edge renders as a sharp straight cut (screenshot), with the outline glowing around it.
  * Ground clamp: the camera was pushed 30 m under the ground and ended 2.37 m above the LiDAR ground. The clamp still works with the selection on.
  * 56 fps at eye level with 16 M points drawn. No console errors.
* **County: Boulder County, CO** (`?area=county:08013`; picked by clicking the map in Select mode; the picker listed "County: Boulder County, CO · 1,918 km²").
  * 9 EPT projects touch it.
  * The CO_DRCOG_1_2020 hierarchy walk completed: 1,347 pages, **347,246 nodes**, about 18.1 B points inside. The other 2020 project hit the walk cap.
  * Streaming started from the camera outward, and the outline glowed around the whole county.
  * **Problems found and fixed:**
    * Walk fetches at concurrency 6 starved the point loads. Fixed to 2.
    * The drape caused 0.3–0.6 s stalls. Replaced with coarse summaries.
    * The GPU pick at altitude stalled for 0.3–0.9 s. Now skipped above 3 km.
    * A level-based preview gave 10–14 M preview points. Switched to spacing-based.
    * Four overlapping projects were displayed by default. Now one or two (≥ 5 % rule).
    * The selection budget could rise to 40 M. Now capped at 20 M.
    * A broken `let userBudget` declaration (a comment swallowed it) was caught in test and fixed.
  * **Not finished:** on this machine (Intel UHD, ANGLE D3D11, WebGL 1) the WebGL context was **lost twice** while Boulder County streamed, on two origins. The built-in browser then disabled WebGL entirely for the session, so the county run could not be repeated after the fixes. Other lanes' WebGL pages (globe, 3D blocks) were open in the same browser at the same time, so the cause is not proven. The fixes above all reduce GPU and main-thread load. **Re-test Boulder County after restarting the app.**
* **CPU check of the exact test** (Node, 200,000 random points per area against a brute-force point-in-polygon on the same rings): 0 mismatches for Boulder County (284 segments), Nevada IA (43), Houston TX (5,865; grid built in 88 ms) and Aleutians West AK (11,347). Over 3,000 random boxes per area, none was classified inside or outside wrongly.

### Known limits (area selection)

* A whole county of dense 2020 LiDAR is tens of billions of points. Not all of it can be on screen at once. The page streams it nearest-first within the display budget, and the progress line shows honestly how much of the selection is drawn. Moving the camera brings in the rest. Nothing is thinned: every node over the camera's surroundings refines to the EPT leaves.
* Areas crossing the antimeridian (Aleutians West, AK) build a grid with up to 104 edges in one cell. The shader loop stops at 64, so the edge there can be inexact. No 3DEP EPT data is affected in practice.
* The hierarchy walk is capped at 1,500 pages per project; beyond that the totals are lower bounds and the density estimate is the guide.

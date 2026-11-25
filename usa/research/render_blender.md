# Cinematic block renders (Blender 5.3, Cycles)

Stills of every 3D block, rendered with photographic lighting and materials. Every visible object comes from the
block's own open data, and nothing is invented. The scripts are:

    .venv\Scripts\python usa\render_export.py <slug ...> | --all          # block data -> usa/_cache/renders/<slug>/scene.npz + scene.json
    "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe" -b --factory-startup -P usa\render_blender.py -- <slug>
         [--views hero,top,street,night] [--samples 384] [--res 1920x1080] [--out DIR] [--save-blend]
    .venv\Scripts\python usa\render_export.py --register <slug ...>       # renders/*.jpg -> meta.files + meta.renders (block_lock)

The output is `web/usa/blocks/<slug>/renders/{hero,top,street,night}.jpg`: 1920x1080 JPEGs of at most 1.5 MB each
(the register step re-compresses any larger file). `meta.renders` records the lighting, time, engine and seconds for
each view. Intermediate files stay in `usa/_cache/renders/<slug>/`.

## Where every visible element comes from

| Visible element | Data | Processing (no invention) |
|---|---|---|
| Ground surface (shape) | USGS 3DEP LiDAR ground-class returns (`points_*.txt`, k = 0) | Median z per 0.5 m cell. Cells with no ground return, such as under roofs, take the nearest measured cell. A 3x3 median filter follows. `mesh.txt` 1 m terrain is the fallback. |
| Ground colour: lawns, roads, paint, driveways, pools | Newest USDA NAIP flight (`img/<year>.jpg`, public domain). An older NAIP year or the USGS orthophoto (`aerial.jpg`) is used instead only when it is at least 1.4x sharper (Laplacian variance). Example: Texas NAIP 2022 is resampled and blurry, so Austin uses NAIP 2018. | The image is projected straight down: the image square equals the LiDAR square, north up. Saturation x1.25. The grass mask is derived from the photo (green excess G - max(R, B)) and drives procedural micro-bump and roughness only. The colour stays the photo's colour. |
| Road lane paint, crosswalks | The NAIP photo only | Synthetic markings are never drawn. streets.json lines and crosswalks are drawn only if their `source` names a measured marking dataset; none does so far. |
| Road surfaces (3D asphalt) | streets.json, only when `meta.validation.house_overlap_m2 == 0` | Constrained-Delaunay triangulation of the road polygons, clipped to the LiDAR square and draped on the LiDAR ground with a 3.5 cm lift. Asphalt colour = procedural base x NAIP pixel (real stains and patches). An untagged surface counts as concrete when its NAIP median brightness is above 140. **No block has a validated streets.json yet, so no roads are drawn as 3D; roads appear only through the aerial texture.** |
| Sidewalks, driveways, curbs | streets.json entries whose `source` is a measured dataset (for example the Minneapolis planimetrics), only once streets.json is validated | Draped on the ground. OSM-tag buffers are skipped. |
| Street lamps, signals, signs | streets.json `signals` / furniture.json items that have exact recorded coordinates and a source id | OSM highway=stop / give_way / traffic_signals nodes are centreline control points, not poles, so they are never drawn. Nothing is moved or inferred: an item recorded inside a road polygon is skipped. **None rendered so far.** |
| Building roofs (shape) | `mesh.txt` roof surfaces fitted by `build_block.py` to the block's own LiDAR | As built. Smooth shading. |
| Roof colour | NAIP | 45 % per-roof median NAIP colour (`render_export.py` samples inside the footprint shrunk 0.6 m, drops the darkest 25 % and brightest 5 % of pixels) and 55 % NAIP projected onto the roof. A low-frequency +-10 % value variation, roughness 0.55-0.88 and a granule bump are procedural texture only. |
| Building walls (shape) | `mesh.txt` walls (Overture footprint ring, from LiDAR ground up to the fitted roof edge) | Wall feet are sunk 0.3 m into the finer ground so no wall hovers over a few-cm terrain mismatch. |
| Wall material | Assessor `exteriorwall` in houses.json (city open data, e.g. Minneapolis CC0) | BRICK/MASONRY -> 225x75 mm brick. STUCCO/CONCRETE/BLOCK -> textured render. STONE -> stone. VINYL/METAL/WOOD/SIDING -> 0.2 m lap siding bump. Unknown -> a plain light neutral render with a small per-building tint. **No windows or doors**: no dataset gives their positions. |
| Trees, shrubs, wires, cars, fences | **Every** LiDAR return (k = 2 multi-return / k = 3 other); none thinned | Each return is a sphere: Geometry Nodes Mesh to Points, rendered as a native Cycles point cloud. The radius grows from 1.6 cm near the ground to 1.35 x the density-based spacing above 2 m. Colour = NAIP pixel above the point, darkened by depth under the canopy (0.55-1.0), plus +-2 % hue / 0.82-1.12 value leaf variation. 10 % transmission for foliage. |
| Ground and roof LiDAR returns | Every k = 0 / k = 1 return | Small spheres (1.6 cm / 2.2 cm) sitting on the fitted surfaces. Where the fitted model and the laser disagree, the deviation stays visible. |
| Sun | NOAA solar-position algorithm at the block's lon/lat | Date 2026-09-22 (equinox; `--date` changes it). Golden hour = first time after noon with elevation <= 7 deg. Noon = maximum elevation. Night = sun -5 deg (deep blue hour). Sun lamp angle 0.53 deg. The colour warms with air mass. |
| Sky | Blender multiple-scattering physical sky at the same sun position | rotation = azimuth, checked with test renders: 0 = north, -90 = west. |
| Dark plinth and floor | **Not data**: a neutral matte base | Keeps the block from floating against the sky. Clearly reads as a presentation base. |

## Views

- **hero**: real-estate drone framing. The camera sits about 50 m up and 62 m out (scaled with the block size), looks about 39 deg down at the block's own address (local origin), uses a 35 mm lens at f/5.6, and is lit at golden hour. The camera is 55 deg off the sun azimuth, so facades are side-lit and shadows run away from the lens.
- **top**: orthographic and straight down at solar noon. It shows the whole LiDAR square, with the plinth margin at the sides of the 16:9 frame.
- **street**: 1.7 m eye height on the main street, 35 mm at f/2.8, golden hour, looking away from the sun. The main street is found as follows:
  1. streets.json road polygon (only when validated), preferring the address's own street.
  2. Otherwise the LiDAR itself: ground returns in the darkest 35 % of laser intensity are asphalt. The script finds the straight 7 m band (2 deg x 1 m search) with the most dark-ground cells, never crossing a footprint.
  3. Otherwise the houses' `facing` street points.

  The first 30 m of the sight line must clear every footprint (buffered 1.2 m) and every low vegetation return (0.5-2.2 m). If it does not, the camera slides sideways or turns.
- **night**: blue hour, sun -5 deg, the drone framing slightly lower. Warm light comes only from street lamps that streets.json records with exact coordinates and an id. **No block has any yet, so the night views are lit by the twilight sky alone.** There is no invented window glow.

## Render settings

- Cycles on GPU (OptiX on the RTX 4050 laptop GPU, CUDA as fallback), 384 adaptive samples (threshold 0.008), OptiX denoiser.
- AgX view transform with the AgX - Punchy look, plus a compositor Bloom glare at strength 0.12.
- 1920x1080. Each view takes 8-30 s, well inside the 3-minute budget.

## Limits seen in the renders

- NAIP is about 0.6 m native resolution, shipped here as about 12 cm per pixel. At street level the ground is therefore soft. The procedural micro-bump hides this only partly. No sharper permanently-free, commercial-use aerial is in the project yet.
- LiDAR rarely hits thin trunks (palms in Phoenix and Scottsdale), so some canopies appear to hover. That is how the data looks, and trunks are not invented.
- Low-density blocks (Dallas, Denver, Kansas City, about 8-13 points/m²) show visibly round canopy points up close.
- Roof meshes inherit build_block fitting artefacts, for example torn roofs under overhanging trees.

## Tested facts (Blender 5.3)

- The sky node types are SINGLE_SCATTERING, MULTIPLE_SCATTERING, PREETHAM and HOSEK_WILKIE. Nishita was renamed to MULTIPLE_SCATTERING.
- `sun_rotation` = azimuth clockwise from north. `sun_direction` is not updated in Python.
- The compositor is a node group: `scene.compositing_node_group = bpy.data.node_groups.new(..., "CompositorNodeTree")` with a NodeGroupOutput. The Glare type is an input socket (`inputs["Type"] = "Bloom"`).
- An Ambient Occlusion shader node on about 1M point spheres made renders 5-10x slower for little visible gain, so it was removed.

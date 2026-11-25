# USA dot globe (web/usa/globe.html)

A 3D globe built with deck.gl 9.1.14 `_GlobeView`. By default the planet is made only of dots, and every dot is a real
sample. There is no sphere fill behind them, only space with the real IAU stars. The NASA Blue/Black Marble texture is
an optional skin.

## What is on it

| Layer | Data | Notes |
|---|---|---|
| World dots (170,808) | NOAA ETOPO 2022 60″ ice-surface elevation, sampled on an equal-area grid: land every ~0.30°, ocean every ~0.80° | Land is coloured by elevation through `Themes.ramp`. Ocean is dim and coloured by depth. Relief lifts the dots ×30 (toggle) and fades to true scale by zoom 10. |
| US dots (249,776) | Same ETOPO grid at ~0.055° inside the 50 states, DC and PR (3×3 mean of 60″ cells) | Each dot carries a precomputed county index, tract GEOID and 3DEP EPT flag, so no polygons are needed to colour it. |
| 3DEP LiDAR glow | `data/national/ept_boundaries.geojson`, as a per-dot flag | 185,893 US dots lie inside EPT coverage. |
| Night side | NASA Black Marble 2016 (0.1°) brightness, stored per dot | Lit in the theme's warm colour beyond the real terminator. |
| County metric | `counties.json` plus the optional inputs, using the same `METRICS` / `mergeOptional` code as index.html (copied) | 8 equal-count bands. |
| Tract metric | `data/tracts/<ST>.json`, loaded lazily per state (when a county is clicked, or when the view centre sits over a state at zoom ≥ 4.5) | 16 metrics: ACS, NRI, FHFA HPI, OZ, QCT, NMTC. |
| Stars | `data/stars.json` (IAU WGSN names, J2000) | Placed at their real positions for the chosen time: sub-star longitude = RA − GMST. |
| Sun | Low-precision solar ephemeris (NOAA/Meeus style) + GMST | A time slider, a date field and a NOW (live) button drive the sun, the night lights and the stars. |
| Block pins | `blocks/index.json` | The first click flies down and switches to a MapView at zoom ≥ 11.5. It then loads every LiDAR point of the block (decoded as view.html does) at its real height in metres around `meta.origin` / `z_base`. A second click opens `view.html?block=`. |
| OZ tracts | `oz_tracts.geojson` (lazy) | Same styling as index.html. |
| NASA texture (optional) | Blue Marble NG July 2004 by day, Black Marble 2016 by night | Composited on the CPU at 2048×1024 (~45 ms), blended at the real terminator, and recoloured per theme. |

| Metro buildings | `metros/<metro>/index.json` + `tiles/<id>.json` (another lane; Overture buildings, ODbL) | The page reads `metros/index.json`. If that is absent, it falls back to the static server's `metros/` folder listing; with neither, metros are skipped. When the view is under ~150 km tall, the visible tiles load nearest first, 6 at a time. Every building in a loaded tile is drawn as a dot at its centroid (decoded x/dy quantised coordinates) and lifted by height (×3+ at metro scale, true height in street view). It sits on the ETOPO elevation of its tile. The selector offers height, hail, LST, area, flood zone and 16 tract metrics, coloured by quantile through `Themes.ramp`. The tooltip shows all attributes, and a click opens the county card with that tract. ODbL attribution is shown in the legend. |
| "View all LiDAR here" | `../lidar/index.html?lat=&lon=&r=1000` | Shown when the view centre is over a US state's box. Labelled "full point streaming runs locally". |

Phoenix test: 1,723,264 of the 1,881,558 buildings were loaded while panning the metro. Spinning held about 59 fps with
the 420k dots, and the tooltip was correct (height, class, hail + date, LST vs scene median, flood zone D, tract income /
value / QCT / OZ / NRI). Merging the tiles costs about 110 ms per rebuild (throttled to 700 ms), recolouring 1.7M about
75 ms, and repositioning about 16 ms.

Themes: all 7 recolour the dots, legend, stars and texture. Night vision is green and thermal uses the ironbow ramp.
Intel is monochrome. Neon '80s and Techno add glowing state lines, and Techno adds a wireframe graticule. `?theme=` works.

## Files

- `usa/globe_dots.py` builds `web/usa/data/earth/dots_world.bin` (1.2 MB), `dots_usa.bin` (3.7 MB) and
  `dots_meta.json`. Each .bin is a little-endian struct-of-arrays, and the layout is given in the meta file.
  Lon/lat are int16 (×180/32767, ×90/32767) and elevation is int16 in metres. Light is uint8. The US file also holds
  county (uint16), tract (uint32 + uint8 state) and flags (uint8).
  - Run: `python usa/globe_dots.py ETOPO_2022_v1_60s_N90W180_surface.tif` (~15 s).
- `web/usa/data/earth/blue_marble_2004-07_5400.jpg` (2.3 MB, 5400×2700) and `black_marble_2016_3600.jpg` (0.8 MB, 3600×1800).

## Sources and licences (all explicit commercial-OK)

- **NOAA NCEI ETOPO 2022** (60 arc-second ice-surface GeoTIFF):
  https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif
  (DOI 10.25921/fd45-gt74).
  - The NOAA metadata view (data.noaa.gov metaview of etopo_2022.xml) says: "NOAA waives any potential copyright
    and related rights in these data worldwide through the Creative Commons Zero 1.0 Universal Public Domain
    Dedication (CC0-1.0)".
  - The ISO record also states "Not subject to copyright protection within the United States". It is not for navigation.
- **NASA Blue Marble Next Generation w/ Topography and Bathymetry, July 2004**:
  https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/july/world.topo.bathy.200407.3x5400x2700.jpg
  (page: https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-topography-bathymetry/).
  - The old eoimages.gsfc.nasa.gov host did not answer from here. The files now sit on assets.science.nasa.gov.
- **NASA Black Marble 2016** (VIIRS DNB, 0.1°):
  https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg
- **NASA image licence** (https://www.nasa.gov/nasa-brand-center/images-and-media/):
  - NASA imagery is "generally not subject to copyright in the United States". This covers texture maps used in
    "computer graphical simulations and Internet Web pages".
  - Commercial use is allowed if it does not "explicitly or implicitly convey NASA's endorsement".
  - Credit NASA. No NASA logos are used.
- **Other inputs**: Census boundaries, tracts, ACS; USGS 3DEP EPT boundaries; FEMA NRI; FHFA HPI; Treasury CDFI OZ;
  HUD QCT. All are US federal works in the public domain, as already recorded by their lanes. The IAU star names are
  already present in the project (`sky_licences.json`).

## Performance (Intel UHD integrated GPU, 1146×682 @1.25 dpr, other lanes sharing the GPU)

- It draws 420,584 dots plus 185,893 glow dots. CPU per frame is about 1 ms (layers() < 0.1 ms; sky canvas 0.1 ms).
- While spinning, deck reported 30–70 fps. The spread comes from other lanes using the same GPU at the same time. The
  globe idles at the display rate.
- Data load: about 5 MB of dots and first paint in about 0.5 s (warm cache). The optional county inputs (~20 MB) load
  after first paint.

## Known limits

- In `_GlobeView`, deck projects positions in fp32 (`project_globe_`). Because of this, a block's LiDAR is only drawn
  once the camera passes zoom 11.5, where the page switches to a MapView (fp64 offsets, pitch allowed). Below that
  zoom it shows the globe again.
- The NASA texture mode uses a BitmapLayer on the globe. Back-hemisphere dots fade with a small vertex-shader
  injection: alpha × 0.07 when they face away from the camera.
- index.html and view.html are owned by other lanes. globe.html links to both, but they still need a "Globe" link in
  their own nav.

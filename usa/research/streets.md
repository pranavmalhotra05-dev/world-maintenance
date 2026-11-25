# Street scene (usa/streets.py → web/usa/blocks/<slug>/streets.json)

Owner rules (2026-11-24), in force:
- **No prediction, exact data only.**
- Road surfaces are measured from the block's LiDAR and checked against the house rings.
- No lane lines, centre lines, crosswalk stripes or stop lines unless a licensed (green) dataset records the actual
  marking lines.
- OSM stop and signal nodes are emitted only as exact control points.
- OSM supplies the attributes (name, lanes, maxspeed, oneway) and the exact positions of crossings, lamps and footways.

Run: `.venv\Scripts\python -u -W ignore -m usa.streets [slug ...] [--refresh] [--render <slug>]`
The render PNG goes to `usa/_cache/streets/render_<slug>.png`: LiDAR ground intensity, house rings, road outlines,
curbs and control points.

## Sources and licences (clear-green only)

| Source | Access | Licence | Used for |
|---|---|---|---|
| USGS 3DEP LiDAR, the block's own `points_*.txt` (every return, ground class 2) | AWS open data | public domain | road surface (paved corridor), widths, curbs, every z |
| OpenStreetMap, from a **Geofabrik US-state `.osm.pbf` extract**, read locally with `usa/osm_extract.py` (pyosmium) | free bulk download; Geofabrik: "You may use the data for any purpose, but you have to acknowledge OpenStreetMap as the data source. Derived databases have to retain the same license." (geofabrik.de/data/download.html, checked 2026-11-24) | **ODbL 1.0** | which corridor belongs to which way; road properties (name, highway, lanes, maxspeed, oneway, surface); exact positions of crossings, `traffic_signals` / `stop` / `give_way` nodes, street lamps, footways |
| City of Minneapolis `PW_Planimetric_Lines` | public ArcGIS REST | copyright waived by the City (CC0-style statement in licenseInfo) | sidewalk, private-walk and driveway polygons (Minneapolis blocks) |

**Not used:**
- Public Overpass servers: their usage policy asks commercial users to self-host. QLever: states no terms. Both were
  used during development only and are gone from the code.
- The 3DEP DEM: everything is now clipped to the LiDAR square, so it is no longer needed.

**ODbL obligations.** streets.json contains a Derivative Database of OpenStreetMap. Anyone who ships it must:
- credit **"© OpenStreetMap contributors, ODbL"**;
- keep it under ODbL (share-alike), separate from the public-domain layers;
- be able to supply it, or this script, on request.

`meta.licence` and `meta.attribution` in streets.json carry this.

## Pavement-marking layers searched (meta.markings_source)

**No licensed marking-line layer exists for any of the 16 blocks, so `meta.markings_source = null` and
`lines`, `crosswalks` and `stop_lines` are empty.**

| City | Layer | Verdict |
|---|---|---|
| Austin | ATD *Markings Short Line*, *Markings Specialty Line*, *Markings Specialty Point* (services.arcgis.com/0L95CJ0VTaxqcmED). They hold real stop lines and crosswalk lines, including 1 stop line and 6 specialty lines in the Austin block. | **Not used.** The licenseInfo is a disclaimer only and grants no licence. Usable if the City confirms a licence, e.g. a public-domain statement on data.austintexas.gov. |
| Saint Paul | *Crosswalks* (services1.arcgis.com/9meaaHE3uiba0zr8; 28 features in the block, mostly "No Marking") | **Not used.** No licence stated. |
| Dallas | *Pavement Markings Maintenance Plan FY2023–25* (TrafficControlDevices) | **Not used.** It is a maintenance plan, not the marking geometry, and carries a disclaimer only. |
| Minneapolis, Denver, Boulder, Columbus, Cincinnati, Kansas City, Tulsa, Phoenix, Scottsdale, Birmingham, Huntsville | ArcGIS Online and Socrata catalogue searches for "pavement markings", "crosswalk markings" and "stop bar" | Nothing published. |

## Method (no prediction)

1. **Frame check.** OSM lon/lat go through the same transform as build_block.py and the houses: EPSG:3857 metres
   minus the origin, divided by k = 1/cos(lat). An overlay of OSM centrelines, house rings and LiDAR intensity
   confirmed the alignment: centrelines run down the middle of the dark asphalt strips and rings sit on the buildings.
2. **LiDAR rasters at 0.5 m**, from ground returns:
   - per-cell mean z, z std and mean intensity;
   - building cells (building class plus house rings buffered by 0.5 m);
   - canopy cells (returns more than 1.5 m above ground). Ground under canopy is dimmed, so its intensity is treated
     as unknown;
   - curb steps: a 6–35 cm height change across 1 m, with real ground on both sides.
3. **Paved corridor per OSM way.**
   - The way's own pavement signature is the median intensity on its centreline. Asphalt is dark and concrete bright;
     lawn is the median of ground more than 10 m from any road.
   - Ways whose contrast with lawn is under 30 are **not drawn**, since the pavement cannot be measured.
   - Paved cells are within tolerance of the signature (±max(18, 0.45 × contrast)) and smoother than 3 cm.
   - Flood-fill from the centreline through paved or unknown (canopy) cells. Curbs and buildings stop the fill.
4. **Profiles every 1 m**, perpendicular to the way, up to a class-dependent half-width (alley 5 m, residential 9 m,
   arterial 14–18 m).
   - The run must start within 3 m of the OSM line.
   - An edge counts only where it is measured: a curb step, a building, or open unpaved ground. Canopy cells at the
     run's end are trimmed back, and only if the trim is under 1 m.
   - Edge offsets are smoothed with a 25 m along-road median that needs 30 % valid samples. This rejects driveway
     aprons and bridges tree gaps up to about 17 m. Runs shorter than 4 m, or narrower than 2.5 m, are dropped.
   - Edges are Chaikin-smoothed, so roads follow their real curves and true widths. OSM ways with no measured
     corridor are not drawn.
5. **Intersections.** The LiDAR pavement inside a circle around a junction of at least 2 measured ways, kept only
   where it touches the measured strips. It is smoothed by a ±0.35 m and ±0.5 m morphological close.
6. **Curbs.** The road outline wherever the DTM shows a 6–35 cm rise from 0.75 m inside to 0.75 m outside, in runs
   of at least 2 m; `height_m` is the measured step.
7. **Hard constraint.** Everything is minus (house rings + 0.55 m). Validation measures the overlap with
   house rings + 0.5 m for roads, crosswalks and lines, and it must be 0. `meta.validation` stores the result for
   every block.
8. **Control points.** OSM `highway=traffic_signals`, `stop`, `give_way` and `street_lamp` nodes at their **exact**
   position, as `signal_control`, `stop_control`, `yield_control` and `street_lamp`.
   - `heading_deg` comes only from a `direction` / `traffic_signals:direction` tag: forward/backward is resolved to
     the travel bearing along the way; numeric and cardinal values are used as given. Otherwise it is null.
   - No offsets, no per-approach signs, no corner poles.
9. **Crossings.** OSM crossing nodes (x, y, z) and `footway=crossing` ways (path), as mapped, with their crossing
   tags. No stripes are drawn.

## Output (local metres east/north of meta.origin; z above meta.z_base; clipped to the LiDAR square)

```
roads[]      {poly, kind (highway | service:<type> | intersection), name, highway, lanes, maxspeed, oneway, surface,
              osm_id, width_m (measured median), source}
lines[] / crosswalks[] / stop_lines[]   empty unless meta.markings_source names a licensed layer
crossings[]  {x,y,z | path, osm_node | osm_way, tags, source}
sidewalks[]  {poly, kind sidewalk|walk_private, source}   (Minneapolis planimetric)
paths[]      {path, kind, width_m (tag only), osm_way, source}   (OSM footways/cycleways as mapped)
curbs[]      {path, height_m (measured), source}
driveways[]  {poly, source}   (Minneapolis planimetric)
signals[]    {x,y,z, kind signal_control|stop_control|yield_control|street_lamp, heading_deg|null, osm_node, direction_tag, tags, source}
meta         {sources, licence, attribution, markings_source (null), markings_search, method, counts, validation}
```

## Results

Full run 2026-11-24, all 16 blocks.
- `meta.validation.house_overlap_m2` = 0.0 everywhere: road, crosswalk and line area inside house rings + 0.5 m.
- `meta.markings_source` = null everywhere.
- `meta.json` carries `files` += streets.json and a `streets` summary (written under block_lock).

| block | KB | ways measured / in square | road polys | road m² | curbs | crossings | sidewalks | paths | controls | house_overlap_m2 | markings_source | meta |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| austin-tx | 21 | 1/1 | 1 | 735.4 | 4 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| birmingham-al | 18 | 1/1 | 1 | 397.1 | 1 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| boulder-co | 43 | 3/3 | 8 | 1417.5 | 2 | 4 | 0 | 6 | 0  | 0.0 | None | ok |
| cincinnati-oh | 2 | 0/1 | 0 | 0.0 | 0 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| columbus-oh | 48 | 1/3 | 1 | 1001.9 | 39 | 1 | 0 | 3 | 0  | 0.0 | None | ok |
| columbus-oz | 80 | 4/4 | 8 | 1651.8 | 58 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| dallas-tx | 3 | 0/1 | 0 | 0.0 | 0 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| denver-co | 37 | 4/5 | 11 | 585.4 | 5 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| huntsville-al | 8 | 1/1 | 1 | 137.2 | 1 | 0 | 0 | 0 | 0  | 0.0 | None | ok |
| kansas-city-mo | 23 | 1/1 | 1 | 453.0 | 0 | 0 | 0 | 3 | 0  | 0.0 | None | ok |
| minneapolis-mn | 138 | 3/4 | 9 | 2263.9 | 47 | 9 | 24 | 8 | 1 {'stop_control': 1} | 0.0 | None | ok |
| minneapolis-oz | 83 | 2/2 | 2 | 1062.9 | 18 | 0 | 17 | 2 | 0  | 0.0 | None | ok |
| phoenix-az | 51 | 2/2 | 2 | 1151.2 | 43 | 0 | 0 | 4 | 0  | 0.0 | None | ok |
| saint-paul-mn | 43 | 2/2 | 2 | 1072.8 | 15 | 0 | 0 | 2 | 0  | 0.0 | None | ok |
| scottsdale-az | 48 | 3/3 | 8 | 1216.7 | 17 | 2 | 0 | 3 | 0  | 0.0 | None | ok |
| tulsa-ok | 40 | 2/2 | 4 | 847.0 | 17 | 0 | 0 | 0 | 0  | 0.0 | None | ok |

**Not drawn (by rule, no guessing):**
- **Cincinnati:** the only way runs along the edge of the square, so its far edge cannot be measured.
- **Dallas:** only a concrete alley crosses the square, and its intensity contrast to lawn is 1.
- Some service ways and one Denver way have no measured corridor.

**Visual checks** (`usa/_cache/streets/render_<slug>.png`): Minneapolis-MN, Austin, Phoenix, Kansas City, Saint Paul,
Denver and Birmingham. Roads sit on the dark/paved LiDAR strip at their true angle, curves follow the pavement, and
curbs are detected along the edges.

**Known limits:**
- Under dense canopy with sparse points (Birmingham, 11.7 pts/m²) the measured width is conservative (about 4.5 m
  against roughly 6 m visible).
- Bright concrete streets with low lawn contrast are skipped rather than guessed.

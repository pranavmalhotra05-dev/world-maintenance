# Parcels + HOA boundaries (lane PH)

Script: `usa/parcels_hoa.py` (`.venv/Scripts/python -u -W ignore -m usa.parcels_hoa [slug ...] [--refresh]`).
Output per block: `web/usa/blocks/<slug>/parcels.json`, `meta.parcels` summary, `'parcels.json'` in `meta.files`,
`houses[].parcel_id`. Raw query responses are cached in `usa/_cache/parcels_hoa/`.

## Rules

- **Green only.** Every source has an explicit public-domain / CC0 / CC BY statement (verdicts from
  `usa/research/licence_registry_local.json`). A county without one gets `parcels: []` and
  `meta.parcels.status = 'no open parcel data'`. Lot lines are never synthesized, and nothing is drawn from OSM,
  setbacks or footprints.
- **Owner rule.** `outFields` names only parcel id + situs address fields. The script also drops any attribute it
  did not ask for before caching. Owner, care-of, mailing, tax-name and sale fields are never requested.
- **HOA wording.** Absence of data is `'no open HOA data'`, never "non-HOA". A subdivision plat only means
  covenants are possible: `'subdivision plat (possible covenants - not confirmed HOA)'`, confidence low.

## Sources

| County | Parcels | Licence |
|---|---|---|
| Hennepin MN (minneapolis-mn, minneapolis-oz) | Met Council Parcels_2025 layer 3 | Public domain (Minn. Stat. ch. 13) |
| Ramsey MN (saint-paul-mn) | Met Council Parcels_2025 layer 4 | Public domain (Minn. Stat. ch. 13) |
| Boulder CO (boulder-co) | Boulder County PARCELS_OWNER (item 90e3547382df4d498b779672d2685b0b; owner fields not read) | CC BY 4.0 |
| Denver CO (denver-co) | ODC_PROP_PARCELS_A layer 245 | CC BY 3.0 (Denver Open Data Catalog terms) |
| Franklin OH (columbus-oh, columbus-oz) | Franklin County Auditor Parcel_Features layer 0 | Public Domain, **borderline**: the statement sits on the Auditor's "Current Parcels Map" item; the feature-service item shows only a disclaimer. Owner sign-off suggested. |
| Hamilton OH (cincinnati-oh) | CAGIS HCE/Cadastral layer 0, listed as City of Cincinnati Open Data g24g-2pi5 | Public Domain |

| County | HOA / plat polygons | Kind | Licence |
|---|---|---|---|
| Boulder CO | LUC_PlattedAreas where `HoaArcRef = Yes` | `HOA (recorded)` | CC BY 4.0 (covers unincorporated county only) |
| Franklin OH | Auditor Parcel_Features layer 1, ConveyanceType CONDO, active | `condo association` (ORC 5311) | Public Domain, borderline (same caveat) |
| Franklin OH | City of Columbus Subdivisions (PublicService/MapServer/10) | subdivision plat (possible covenants) | CC0 |
| Hamilton OH | CAGIS HCE/Cadastral layer 3 (Open Data rynu-bywd), non-historical | CONDO -> `condo association`; SUBDIV -> subdivision plat | Public Domain |

Franklin layer-1 SUBDIV rows are used only as a fallback when the Columbus layer fails, so plats are not counted
twice. Texas TREC HOA registry has no geometry, so it is skipped. Grey and not used: Dallas HOA/NA, Austin
subdivisions, KCMO homes associations, Tulsa subdivisions, and the Jefferson, Madison, Tulsa, Jackson and Maricopa
parcel layers. Portland, Sacramento, San Diego, Seattle and Salt Lake City have no vetted parcel or HOA source in
the registry yet, so they report "no open parcel data" until a licence check adds one.

## Geometry

- The query box is the block half-size + 30 m (WGS84 envelope, `esriSpatialRelIntersects`).
- Polygons are projected to local metres of `meta.origin` with the same transform as `usa/streets.Frame`, then
  clipped to the box. Clipped polygons carry `clipped: true`: the cut edge is the box, not a lot line.
  `area_m2` is the unclipped area.
- Edges are densified to 2 m or less, and z comes from `usa/streets.Terrain` (the LiDAR terrain mesh inside the
  square, with the USGS 3DEP DEM offset to it outside), in metres above `meta.z_base`. This adds vertices along
  straight source edges only; the shape does not change.
- `houses[].parcel_id` is the parcel that contains the footprint centroid. Where stacked parcels overlap, the
  smallest polygon wins and the house is counted in `houses_ambiguous`. When no parcel contains the centroid or
  there is no data, the value is `null`.
- `hoa[].houses` lists the house ids whose footprint centroid falls inside each HOA or plat polygon.

## Results (2026-11-24 run)

| Block | Parcels | Houses linked | HOA polygons | Status |
|---|---|---|---|---|
| boulder-co | 40 | 4/4 | 0 (the block is inside city limits; the platted-area layer is county-only) | no open HOA data |
| denver-co | 39 | 18/18 | 0 | no open HOA data |
| minneapolis-mn | 55 | 36/36 | 0 | no open HOA data |
| minneapolis-oz | 42 | 17/17 | 0 | no open HOA data |
| saint-paul-mn | 60 | 32/32 | 0 | no open HOA data |
| columbus-oh | 40 | 28/28 | 5 subdivision plats | possible covenants |
| columbus-oz | 46 | 20/20 | 4 subdivision plats | possible covenants |
| cincinnati-oh | 35 | 9/9 | 2 subdivision plat pieces | possible covenants |
| other 13 blocks | 0 | 0 | 0 | no open parcel data / no open HOA data |

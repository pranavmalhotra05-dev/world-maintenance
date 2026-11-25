# Archive: early prototypes (not maintained)

Everything in this folder is superseded by the single World Maintenance globe app (`web/app/`). It is kept for reference only. Nothing here is linked from the live site, and nothing here is maintained or tested.

| Path | What it was |
|---|---|
| `web/index.html` | Rooftop Intel, the first San Francisco prototype page (the old site root) |
| `web/mpls/`, `mpls/` | Roofprint Minneapolis: the first per-house LiDAR 3D block page and its build scripts |
| `web/dallas/`, `dallas/` | Dallas Hail Desk: MRMS hail exposure page, hail backfill and watcher, lead scoring, 3D block scene |
| `legacy-pages/` | Copies of the separate pages that the globe app replaced, taken before they became redirects: `index.html` (USA county radar, was `web/usa/index.html`), `globe.html` (dot globe, was `web/usa/globe.html`), `view.html` (3D block viewer, was `web/usa/view.html`), `lidar-index.html` (LiDAR explorer, was `web/lidar/index.html`), `catalog.html` (data catalog, was `web/catalog/index.html`; now the app's catalog panel). Their relative asset paths (`theme.js`, `pack.js`, `data/`, `blocks/`, `potree/`) only resolve at the original locations. |

`dallas/hail.py` stays at the repo root (not archived): `usa/enrich_national.py` and `usa/metro_build.py` import it for MRMS hail decoding.

Page data (`data/` folders) is git-ignored here as everywhere else. The prototypes may still run from their archive paths, but nothing guarantees it.

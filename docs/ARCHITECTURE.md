# World Maintenance: one globe, area-based loading (free to run)

Goal: the whole world is one globe app (`web/app/index.html`), and the whole USA is browsable in it, but only
the area you select is loaded in full. Everything else shows as a light, blurred preview until you move there.
The browser only ever holds one area's detail, so it stays smooth on ordinary laptops.

The dot globe, the USA county map, the 3D LiDAR blocks and the LiDAR explorer are not separate pages any more:
they are zoom levels of the same globe. The old page URLs (`/`, `usa/index.html`, `usa/globe.html`,
`usa/view.html`, `lidar/index.html`, `catalog/`) are small redirects that map their parameters into the app's URL scheme:

```
app/index.html?lat=&lon=&z=&pitch=&bearing=&block=<slug>&house=<id>&area=county:<fips>|place:<geoid>&panel=catalog
```

The original pages are kept, unmaintained, in `archive/legacy-pages/`; the early prototypes (Rooftop Intel,
Roofprint Minneapolis, Dallas Hail Desk) are in `archive/`.

## Levels of detail (zoom and hover)

| Level | Zoom (approx.) | What you see | Loaded when | Size |
|---|---|---|---|---|
| 0. Globe | about 0-3 | Dot globe, stars, weather | Always | ~10 MB, once |
| 1. USA / counties | about 3-9 | County colours and scores, outlines | Zooming onto the USA | small, once |
| 2. Preview (blurred) | about 9-14 | Coarse LiDAR, tract colours, building dots | Area is on screen or hovered, but not selected | small tiles, on demand |
| 3. Selected area (unlocked) | about 14+ | Every LiDAR point, every building, 3D houses, per-house data, street photos | You select a county, city or block (`area=`, `block=`) | one area pack, ~5-50 MB |

Moving to another area unloads the previous one (memory is freed) and unlocks the new one.

## Where the data lives (all $0)

- **Code and site pages**: this GitHub repo, served by **GitHub Pages** (free for public repos):
  https://pranavmalhotra05-dev.github.io/world-maintenance/ (the root opens `app/`).
- **LiDAR**: streamed straight from the USGS 3DEP public bucket (EPT). We never copy or host it, so
  nationwide coverage costs nothing and no point is dropped.
- **Our derived data** (per-house scores, buildings, tracts, street photos): one **area pack per county**
  (`areas/<county-fips>/`), built by `usa/pack_site.py`. A pack is only downloaded when that county is selected.
- **If the packs outgrow GitHub Pages (1 GB site)**: move only the `areas/` folder to Cloudflare Pages
  (free tier: unlimited bandwidth, 20,000 files, 25 MB per file). Pages code does not change: just the base URL.

## Rules kept by this design

- Only green-licence data (explicit commercial use); sources and licences listed in the app's catalog panel (`app/?panel=catalog`).
- Never thin LiDAR: previews are display-only levels of the same survey; full detail loads on selection.
- Only real, exactly located objects; nothing inferred.
- No secrets in the repo (`usa/local_secrets.json` is git-ignored).

## Build flow

1. `python -m usa.pipeline --address ...` (or batch) builds blocks into `web/usa/blocks/`.
2. `python -m usa.metro_build --metro <slug> --counties <fips,...>` builds whole-metro building tiles.
3. `python usa/pack_site.py` packs everything into per-area packs in `dist/`.
4. GitHub Actions publishes `dist/` to GitHub Pages.

## Running locally

```powershell
python -m http.server 8765 --directory web
```

Then open http://localhost:8765/ (it redirects to `app/`).

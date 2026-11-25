# World Maintenance: area-based design (free to run)

Goal: the whole USA is browsable, but only the area you select is loaded in full.
Everything else shows as a light, blurred preview until you move there. The browser
only ever holds one area's detail, so it stays smooth on ordinary laptops.

## Three levels of detail

| Level | What you see | Loaded when | Size |
|---|---|---|---|
| 0. Overview | Dot globe, county colours, stars, weather | Always | ~10 MB, once |
| 1. Preview (blurred) | Coarse LiDAR, county outlines, tract colours, building dots | Area is on screen but not selected | small tiles, on demand |
| 2. Selected area (unlocked) | Every LiDAR point, every building, per-house data, street photos | You select a county or city | one area pack, ~5-50 MB |

Moving to another area unloads the previous one (memory is freed) and unlocks the new one.

## Where the data lives (all $0)

- **Code and site pages**: this GitHub repo, served by **GitHub Pages** (free for public repos).
- **LiDAR**: streamed straight from the USGS 3DEP public bucket (EPT). We never copy or host it, so
  nationwide coverage costs nothing and no point is dropped.
- **Our derived data** (per-house scores, buildings, tracts, street photos): one **area pack per county**
  (`areas/<county-fips>/`), built by `usa/pack_site.py`. A pack is only downloaded when that county is selected.
- **If the packs outgrow GitHub Pages (1 GB site)**: move only the `areas/` folder to Cloudflare Pages
  (free tier: unlimited bandwidth, 20,000 files, 25 MB per file). Pages code does not change: just the base URL.

## Rules kept by this design

- Only green-licence data (explicit commercial use); sources and licences listed in the catalog.
- Never thin LiDAR: previews are display-only levels of the same survey; full detail loads on selection.
- Only real, exactly located objects; nothing inferred.
- No secrets in the repo (`usa/local_secrets.json` is git-ignored).

## Build flow

1. `python -m usa.pipeline --address ...` (or batch) builds blocks into `web/usa/blocks/`.
2. `python -m usa.metro_build --metro <slug> --counties <fips,...>` builds whole-metro building tiles.
3. `python usa/pack_site.py` packs everything into per-area packs in `dist/`.
4. GitHub Actions publishes `dist/` to GitHub Pages.

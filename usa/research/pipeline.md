# One-command block pipeline (`usa/pipeline.py`)

Builds a complete 3D block for any US address and runs every data layer the project has on it, in one command.

    .venv\Scripts\python -u -W ignore -m usa.pipeline --address "1385 E Yale Ave, Salt Lake City, UT" --half-m 40 --slug slc-ut-test
    .venv\Scripts\python -u -W ignore -m usa.pipeline --lon -111.856 --lat 40.741 --half-m 60 --slug slc-ut-pt
    .venv\Scripts\python -u -W ignore -m usa.pipeline --batch blocks.csv --jobs 2
    .venv\Scripts\python -u -W ignore -m usa.pipeline --slug austin-tx --only streets,street_furniture   # re-run stages
    options: --skip a,b   --only a,b   --renders   --no-retry   --events   --list

Batch CSV, one block per line (header optional, `#` comments): `address,slug[,half_m]` or `lon,lat,slug[,half_m]`.
Blocks run one after another (every stage is network-heavy); `--jobs 2` runs two at a time and prints only the stage
lines on stdout (full output still goes to each block's log).

## Stages

Every stage is a subprocess `python -u -W ignore -m usa.<script>` with cwd = repo root (module mode, because some
scripts import `usa.blocklock` without adding the root to `sys.path`). The CLIs are those of each script's argparse /
`sys.argv` handling as of 2026-11:

| # | stage | command | notes |
|---|---|---|---|
| 1 | build_block | `--address A` or `--lon X --lat Y`, `--half-m M --slug S` | every later stage needs its meta.json; if it fails the rest are skipped |
| 2 | enrich_national | `S --hail` | NOAA MRMS hail is the slow part |
| 3 | city_enrich | `S` | only if `usa.adapters.for_point(meta.origin)` returns a city adapter, else skipped |
| 4 | oz_enrich | `S` | |
| 5 | federal_flags | `S` | |
| 6 | local_flags | `S` | |
| 7 | assistance_flags | `S` | |
| 8 | thermal | `S` | |
| 9 | imagery | `S` | |
| 10 | pools | `--slug S` | |
| 11 | weather_triggers | `--blocks-only S` | still loads its national context first |
| 12 | cleaning_market | `--blocks-only --slugs S` | |
| 13 | cleaning_leads | `--blocks S` | |
| 14 | streets | `S` | |
| 15 | street_furniture | `S` | |
| 16 | sky_weather | `S` | |
| 17 | street_imagery | `S` | |
| 18 | road_traffic | `blocks S` | blocks stage only (national file is reused) |
| 19 | str_score | `index_blocks`, then `--blocks S` | str_score already takes `--blocks`; index first so its city legal lookup sees the new block. In batch mode it is deferred and runs once at the end with `--blocks <all batch slugs>` |
| 20 | render_export | `S` | `--renders` only |
| 21 | render_blender | `blender.exe -b --factory-startup -P usa/render_blender.py -- S` | `--renders` and Blender 5.3 at `C:\Program Files\Blender Foundation\Blender 5.3\blender.exe` (override: env `ROOF_BLENDER`) |
| 22 | render_register | `render_export --register S` | only after a successful Blender run |
| 23 | index_blocks | `index_blocks` | then meta.files is refreshed from the folder (as serve.py's manifest did) |

## Behaviour

* **Timeout** per stage (`TIMEOUT_S`, default 20 min; build_block 45, enrich_national 90, Blender 120); on timeout the
  whole process tree is killed (`taskkill /T /F` on Windows).
* **One retry** on failure. "Failed" = non-zero exit, timeout, or a block-level failure in the output: the scripts
  catch per-block exceptions and still exit 0, so a `Traceback`, a `<slug>: FAILED` line or `FAIL _block` also counts.
  Per-source partial failures inside a script (e.g. one assistance dataset down) are not stage failures.
* **Independence**: a failed stage never stops later stages. Only a missing meta.json (build_block failed) skips the
  rest; render_blender is skipped if render_export failed, render_register if Blender did not run.
* **Log**: everything (commands, child output, stage results, timing table) is appended to
  `usa/_cache/pipeline/<slug>.log`; the batch tail logs to `_batch.log`.
* **meta.pipeline** = `{started, address, half_m, finished, seconds, summary, stages: {stage: {status, seconds,
  attempts, finished, error|reason}}}`, written under `usa.blocklock.block_lock` (re-read inside the lock, atomic
  replace, keeps the file's pretty/compact format). Stages skipped by the user's `--skip/--only` keep their earlier record.
* **--events** prints `@@pipeline {"slug","stage","status","seconds","i","n","note"}` per stage change; `usa/serve.py`
  turns these into job stages.

## Local server (`usa/serve.py`)

`/api/build` now queues `python -m usa.pipeline --address ... --half-m ... --slug ... --events` (optional `renders=1`,
`skip=a,b`). `/api/status` returns `stages` and the current `stage` next to the log lines (readable `[i/n] stage:
status` lines are added to the log, so build.html shows progress without changes). `/api/events?job=<id>` streams the
same as Server-Sent Events (`line`, `stage`, `end`). A job is "done" when build_block succeeded; failed later stages are
listed in `failed_stages`.

## Test run

2026-11-24: new block in a new state (Utah; 3DEP EPT `USGS_LPC_UT_Wasatch_L4_2013_LAS_2016`, box coverage 1.0):
`--address "1385 E Yale Ave, Salt Lake City, UT" --half-m 40 --slug slc-ut-test` took 1570 s in total, with 17 stages
ok, 2 failed and 1 skipped (3 houses, 35 files). The log is `usa/_cache/pipeline/slc-ut-test.log`.

| stage | status | s |
|---|---|---|
| build_block | ok | 85.2 |
| enrich_national (--hail) | ok | 268.0 |
| city_enrich | skipped (no adapter for SLC) | 0 |
| oz_enrich | ok | 3.7 |
| federal_flags | ok | 59.8 |
| local_flags | ok | 1.2 |
| assistance_flags | ok | 13.4 |
| thermal | ok | 55.5 |
| imagery | ok | 45.0 |
| pools | ok | 9.6 |
| weather_triggers | ok | 97.8 |
| cleaning_market | ok | 4.2 |
| cleaning_leads | ok | 0.2 |
| streets | **failed** (2 attempts) | 741.9 |
| street_furniture | **failed** (2 attempts) | 1.0 |
| sky_weather | ok | 4.5 |
| street_imagery | ok | 174.8 |
| road_traffic | ok | 2.7 |
| str_score | ok | 0.9 |
| index_blocks | ok | 0.1 |

**Known blocker for new states (not in this lane's files):** `usa/osm_extract.py` `STATES` and `usa/streets.py`
`GEOFABRIK_STATE` only map the 8 original states (MN TX CO OH OK MO AL AZ). For UT, osm_extract requests
`.../us/UT-latest.osm.pbf` (404). It retries 8 times with backoff, so streets spends about 6 min per attempt. It then
crashes on a missing `.part` file. street_furniture fails at once with `KeyError: 'UT'`. To fix it, both tables need
the full state-code to Geofabrik-name map that `build_block.py` already has. enrich_national downloaded
`utah-261124.osm.pbf` without trouble.

Batch path test (`--batch` with an unquoted comma address row and a lon/lat row, `--jobs 2`, `--only
oz_enrich,local_flags,str_score,index_blocks`): both blocks ran in parallel, and str_score ran once at the end with
`--blocks slc-ut-test,tulsa-ok`. The serve.py SSE and status endpoints were tested in-process with synthetic stage
events; no real build was started through the server.

# Cleaning lead engine (lane P5) - method

Code: `usa/cleaning_leads.py`. Output: `h.cleaning = {pool, window}` per house and `meta.cleaning` per block, written under
`usa.blocklock.block_lock` (files re-read inside the lock, only the `cleaning` keys touched, tmp + `os.replace`).
Garages are skipped. Re-runnable and idempotent: `--wait-min 75 --poll-s 300` polls and re-scores a block only when
its inputs (pool, weather, affluence, market row, assessor pool, value, eave) change.

## What the scores are (and are not)

A score is a 0-100 **outreach ranking** within an area, built from open data. It is not a probability that a household
buys service. Every pool lead carries the note: *imagery snapshots every ~2 yrs; cleaning frequency per house is not
observable from open data; area seasonality from QCEW*. We never claim to know when a household cleans.

## Normalisation

Each part is mapped to 0..1 by fixed linear anchors (clipped), so scores are comparable across blocks and do not
move when another block is added:

| input | 0 at | 1 at |
|---|---|---|
| dust_days_yr | 0 | 30 |
| high_wind_days_yr | 0 | 40 |
| dust_then_rain_yr | 0 | 12 |
| median_hh_income (area) | $45k | $160k |
| share_hh_200k (area) | 3% | 35% |
| est_value_now (house) | $200k | $1.2M |
| estimated glass area | 8 m2 | 60 m2 |

**Missing inputs.** Area-level parts with no data (dust/wind, dust-then-rain, affluence, seasonality, recent dust)
score a neutral 50% of their weight; house-level observations that are absent (pool state, tree data, glass, storeys)
score 0. Each lead reports `coverage` (share of parts with real data) and `meta.cleaning.inputs_present`.

## Pool cleaning lead (only if a pool is present: imagery `h.pool.present` or assessor `pool`)

| part | max pts | how |
|---|---|---|
| green_now | 30 | latest dated image state (pools.py vocabulary; `no_data` skipped): green_algae = 1.0; covered_or_empty = 0.35 (refill / start-up clean); not_present after earlier sightings = 0.2 (covered, drained or filled); turquoise = 0.15; clean_blue = 0; unknown (assessor-only) = 0. Detection confidence < 0.7 adds a "verify on imagery" reason |
| green_past | 15 | green in earlier images (latest excluded): 1 year = 0.67, 2+ = 1.0 |
| trees | 15 | debris pressure. Best: trees.json crowns (h >= 4 m) within crown radius + pool radius + 2 m of the pool centroid (if pools.py gives a position): 1 tree = 0.6, 2+ = 1. Fallback: `h.trees_near` with gap <= 2 m and h >= 4 m, 0.4 + 0.25 n (+0.15 if tallest >= 10 m), x0.8 because it is house- not pool-proximity. Last resort: canopy cover/60 x 0.6 |
| dust_wind | 15 | 0.6 x dust_days + 0.4 x high_wind_days (county stats, lane P3) |
| affluence | 15 | 0.5 x home value + 0.5 x area (0.5 income + 0.5 share $200k+); either alone if only one exists. Home value counts only when `money.value_level == "this house"` (a tract median would double-count the area part) |
| season | 10 | current month on the combined calendar (below) |

**Next likely trigger season**: months whose combined calendar value is >= 0.7 (i.e. in the top 30% of the area's
annual range), the window that contains the current month (`status: now`) or the next one ahead (`status: upcoming`);
"year-round" if every month qualifies.

**Combined trigger calendar** (12 months, in `meta.cleaning.trigger_calendar`): 0.6 x `monthly_trigger_profile`
+ 0.4 x QCEW pool-service quarterly employment seasonality `pool_svc_seasonality_q` (spread to its 3 months). Each
series is min-max scaled over the year (0 = quietest month, 1 = peak; a series varying < 2% is flat = 0.5), so the
season part rewards being in the area's busy months rather than raw volume. Either alone if the other is missing.

**Affluence geography**: lane P4 gives tract and block-group ACS values. Block group is used only when its income
MOE is < 50% of the estimate; otherwise tract (`meta.cleaning.area.affluence_geo`).

Vacant/condemned (`h.local.vacant_or_condemned`) does not change the score; it adds a reason (contact owner of record;
a neglected pool may be a code-enforcement matter).

## Window cleaning lead (every non-garage building)

| part | max pts | how |
|---|---|---|
| glass | 35 | glass m2 = footprint perimeter (LiDAR ring; fallback 4 x sqrt(area)) x wall height (eave_m, min 2.4 m; fallback 0.8 x height_m) x 15% glazing ratio |
| dust_rain | 20 | dust_then_rain_yr (spotting episodes) |
| affluence | 20 | as for pools |
| stories | 15 | 1 storey = 0.2, 2 = 0.7, 3+ = 1.0 (LiDAR estimate > Overture num_floors > eave/3 m; never assessor records, per the licence policy) - multi-storey glass is harder for owners to DIY, so higher job value |
| recent_dust | 10 | 1 if a dust or dust_rain event (P3 `events_since_2020_within_25km`, deduplicated by day) fell within 30 days before the reference date: the block's imagery/sky date if meta carries one (`sky_date`, `image_date`, `imagery_date`, `sky.date`), else the run date; 0 if events were checked and none; neutral if no events list |

Facade sun (front facade >= 1500 sun-h/yr) is listed as a reason only (streaks show) and does not score.

## Block summary `meta.cleaning`

houses scored, houses with pools, green pools now, top 5 pool and top 5 window leads (address, score, top 3 reasons),
12-month trigger calendar, next trigger season, area stats (dust, wind, dust-then-rain, events since 2020, income),
market context from `web/usa/data/cleaning_market.json` (pool service establishments, employment, nonemployers,
window/janitorial establishments, service establishments per 10k households), weights, inputs present, caveats.

## Sources

All inputs come from other lanes and are open data: USGS 3DEP LiDAR (footprints, heights, trees), NAIP/USGS imagery
(pool state, lane P1), NOAA Storm Events / weather (lane P3), Census ACS (affluence, lane P4), Census CBP/NES and
BLS QCEW (market and seasonality, lane P4), open-licence county assessors only where still present (house value, pool flag). Storeys never come from assessors.

## Limits

- Pool state is a snapshot from imagery roughly every 2 years; a green pool in 2022 may have been cleaned since.
- Tree debris uses house proximity unless the pool lane supplies a pool position.
- Glazing ratio is a national typical value; real glass share varies 8-25%.
- Anchors are judgement-based, not fitted to conversion data (none is open); revisit once real outcomes exist.

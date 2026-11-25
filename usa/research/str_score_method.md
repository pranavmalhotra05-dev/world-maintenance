# STR score: method

Script: `usa/str_score.py`. Output: `web/usa/data/str_score.json`, plus `meta.str` and `h.str` in each block under `web/usa/blocks/<slug>/`.

Run it with `.venv\Scripts\python -u -W ignore -m usa.str_score [--all | --blocks a,b]`. You can run it again at any time: it only replaces its own keys (`meta.str`, `h.str`). Each block is written under `usa.blocklock.block_lock`. The script fetches nothing inside the lock, re-reads both files inside it, and writes through a tmp file and `os.replace`.

## What this is, and what it is not

- **It is a relative index (0-100) built from aggregate public data.** It ranks US counties on signals that people travel there, on how cheap it is to buy in, and, as a separate score, on demand for buying or renting long-term.
- **There are no nightly rates, ADR, occupancy or revenue figures.** We know of no permanently free, commercial-use source for them. AirDNA, Airbnb scrapes and Inside Airbnb are either paid, against terms of service, or licensed non-commercially (CC BY-NC). So we do not estimate them, and every per-house record carries this note: *"Estimate from aggregate public data; no booking or occupancy data is used"*.
- **Legal status is never folded into a number.** A county with a ban can still score high on demand. `legal` sits next to the score and must be read first.

## 1. STR demand (0-100)

Each input is percentile-ranked across all counties that have a value for it.
- For zero-heavy count inputs, a value of 0 or less gets percentile 0. Ties share the average rank.
- For signed inputs (growth rates), every value is ranked; there is no zero floor.

Demand is then a weighted mean of the available percentiles, pulled toward the median:

```
demand = (sum_i w_i * pct_i + 50 * 0.15) / (sum_i w_i + 0.15)      # only over inputs present for that county
demand = null if sum_i w_i < 0.10
```

- **Missing inputs:** an input missing for a county drops out, and the remaining weights carry the score.
- **Shrinkage:** the 0.15 pseudo-weight at 50 keeps a county with one extreme input from topping the list.
- **Coverage:** `coverage` = (weight present) / (total weight 1.31).

Weights are relative. The core tourism inputs sum to 1.00. The flow and visitor extras add 0.31, and the blend re-normalises.

| key | weight | source file | absent county = | what |
|---|---|---|---|---|
| air_visitor_pax_60km | 0.25 | tourism_air | 0 | Visitor passengers, airports within 60 km, 12 months. Uses the lane's `visitor_pax_12m_within_60km`. If that is missing: sum of pax_12m x od_share x (1 - km/60). |
| tourism_intensity | 0.20 | tourism_econ | missing | Accommodation (NAICS 721) employment as % of private employment, QCEW |
| leisure_emp_per_cap | 0.15 | tourism_econ | missing | Leisure and hospitality employment (QCEW 1026, else 71+722) per resident (Census pop). If there is no pop, divided by owner-occupied units instead. |
| nps_visits_80km | 0.10 | tourism_air | 0 | NPS recreation visits within 80 km |
| venue_capacity | 0.10 | tourism_events | 0 | Total capacity of big venues in the county |
| wiki_views_12m | 0.10 | tourism_econ | missing | Wikipedia pageviews of the county-seat article(s), 12 months |
| hot_growth | 0.10 | tourism_econ | missing | Hotel-occupancy-tax receipts growth. When only levels exist (today: Texas FY2025), receipts per resident instead. Growth and levels are never ranked together. |
| in_commuters | 0.05 | jobs_flow | missing | Workers commuting into the county (flow signal) |
| vmt_daily | 0.05 | road_traffic | missing | Daily vehicle-miles travelled (flow signal) |
| federal_land_visits | 0.05 | visitors | 0 | Federal land recreation visits (NPS/USFS/BLM/USACE/FWS) |
| state_park_visits | 0.04 | visitors | missing | State park visits. State reporting is patchy, so an absent county counts as unknown. |
| cruise_pax | 0.03 | visitors | 0 | Cruise passengers at county ports |
| amtrak_riders | 0.03 | visitors | 0 | Amtrak riders at county stations |
| border_crossings_12m | 0.03 | visitors | 0 | Inbound land-border crossings, 12 months |
| state_overseas_visits | 0.03 | visitors | missing | State overseas visits. A weak state prior: every county in the state gets the same value. |

"0" means that when the source file exists but does not list a county, the true value is about zero: no airport within 60 km, no park, no venue. "Missing" means unknown.

**Seasonality:** a 12-month profile (1.0 = mean) is the average of whatever is present:
- the air lane's monthly `seasonality[12]`;
- QCEW quarterly employment `seasonality_q` spread over months;
- the Wikipedia monthly index `wiki_season_idx`.

`peak_months` = the top 3 months at 1.05 or above, else the single highest month. If no profile exists, it falls back to `wiki_peak_months`.

**STR supply (`str_supply`)** is context only and is not in any score. It shows registered STRs where a public registry exists: Texas hotel-tax permits with NAICS 531110/531311, and the Denver and Austin registries.

### 1b. Small-market corrections (market depth, credibility, intensity cap)

Without these corrections, small counties dominated the ranking. A single extreme percentile, such as QCEW tourism intensity in a 2,000-person county, carried too much weight. Three corrections address this.

1. **Intensity cap.** Where absolute accommodation employment (`acc_emp`, QCEW 721) is below 200 jobs, the `tourism_intensity` weight is scaled by `acc_emp / 200`. A county with 50 hotel jobs keeps a quarter of that weight.

2. **Credibility shrinkage** toward 50, by market size:
   ```
   households = owner_occ + renter_occ   (if counties.json gains a renter column)
              = owner_occ / 0.65          (today: national owner share about 65%)
              = sf_detached               (fallback)
   n = max(0, log10(households) - 2)^2
   w = n / (n + 4)
   demand = 50 + w * (demand_raw - 50)
   ```

   | Households | 1k | 3k | 10k | 30k | 100k | 1M |
   |---|---|---|---|---|---|---|
   | w | 0.20 | 0.36 | 0.50 | 0.60 | 0.69 | 0.80 |

   Counties under about 10k households keep half or less of their deviation from 50. `demand_raw` (after the coverage prior only) is stored next to `demand`, and `credibility` stores w.

3. **Market depth (0-100)** is the percentile of:
   ```
   mean( pct(households), mean of available pct(absolute visitor volume) )
   ```
   The visitor-volume inputs are:
   - air visitor pax within 60 km
   - venue capacity
   - NPS visits within 80 km
   - federal land visits
   - cruise passengers
   - Amtrak riders
   - accommodation employment, which is present today

   `depth_parts` = [households, households_pct, visitor_volume_pct, n_volume_inputs].

## 2. Cost (0-100, higher = cheaper entry)

```
cost = 0.75 * (100 - pct(ACS median home value)) + 0.25 * (100 - pct(FHFA HPI trend))
```

- The FHFA term is used only if `counties.json` gains a trend column (`fhfa_hpi_5y`, `hpi_5y`, ...). Today it has none, so cost = 100 - percentile of home value.
- `parts.cost.home_value` = [USD, percentile, ratio to the national county median]. The ACS 5-year values lag the market by 2-3 years.

## 3. County score

```
score = demand * (0.6 + 0.4 * cost / 100)
```

Demand drives the score. Cheap entry can lift it by up to 40% relative to the most expensive county. Score is null when demand is null.

Three scores are stored side by side:

| Field | What it is |
|---|---|
| `score_raw` | The same formula on `demand_raw`, with no credibility shrinkage. |
| `score` | The formula above, on the shrunk `demand`. |
| `score_invest` | Investment grade: `score * min(1, market_depth / 50)`. Counties below the median depth are scaled down linearly; at or above the median they are unchanged. Use this for "where could an investor actually operate several units". |

## 4. Legal (shown separately)

Sources:
- `rules.json` city STR group: permit, primary_residence, zones, caps, pending.
- `rules.json` state `str_preemption`.

Each verified flag is passed through unchanged (true / "partial" / false).

**City status:** keyword rules on our own summaries, applied in order, with the last match winning:
- *in litigation*: the permit text mentions an injunction.
- *ordinance pending*: "none in force" or a draft.
- *primary residence only*: primary_residence starts "Yes" or "Only".
- *zoning restricted*: zones mention "banned" or "not allowed", motel treatment, by-right-only districts, or no single-family.
- *permitted with licence*: everything else.

Every matched rule adds a plain-language reason to `legal_risk`, with the source text.

**Current statuses:**

| City | Status |
|---|---|
| Austin | permitted with licence |
| Minneapolis | permitted with licence |
| Saint Paul | permitted with licence |
| Columbus | permitted with licence |
| Cincinnati | permitted with licence |
| Tulsa | permitted with licence |
| Denver | primary residence only |
| Boulder | primary residence only |
| Kansas City | primary residence only (resident host only in residential zones) |
| Dallas | in litigation (Tex. Sup. Ct. No. 25-0748) |
| Huntsville | zoning restricted (treated as motels) |
| Birmingham | ordinance pending |

**County legal:**
- `scope: "city"`: a county that contains one of the 12 cities gets that city's status. This applies inside city limits only.
- `scope: "state"`: other counties in our 7 states (MN, TX, CO, OH, OK, MO, AL) get `unknown`; the state preemption note is in `meta.state_preemption`.
- `scope: null`: everywhere else is `unknown`.

## 5. Housing demand (separate score: buying or long-term renting)

`counties[fips].housing.score`, 0-100, built from `jobs_flow.json`. It is the same percentile blend with re-weighting over available inputs. Inputs where a lower value means more demand are inverted (100 - pct). It does not feed the STR score.

| key | weight | sign | what |
|---|---|---|---|
| emp_yoy | 0.20 | + | Employment growth, 1 yr |
| emp_5y | 0.10 | + | Employment growth, 5 yr |
| wage_yoy | 0.15 | + | Weekly wage growth, 1 yr |
| net_mig_rate | 0.20 | + | Net domestic migration ÷ population (or ÷ resident workers if no pop) |
| inflow_ratio | 0.10 | + | Commuter inflow ratio (jobs here ÷ residents employed) |
| unemp | 0.10 | - | Unemployment rate |
| unemp_chg | 0.05 | - | Change in unemployment rate |
| permits | 0.10 | + | Residential permits per 1,000 (`permits_per_1k`/`permits`), if the jobs lane provides it |

Also passed through for context:
- `top_origins`: in-migration origin counties, named from `counties.json`;
- `pop_change_pct`;
- the jobs lane's own `housing_demand_pressure`.

## 6. Per block / per house

**`meta.str`** holds:
- the county demand, cost and score parts, with names;
- `county_legal`, plus `city_legal`: status, reasons, and each field with value, verified flag and source URL;
- `airport`: the main gateway. This is the busiest airport within 60 km of the block, ranked by visitor passengers (pax × O&D share × visitor share). Airports under 100k passengers a year are excluded, so general-aviation fields are skipped. If none is within 60 km, it is the nearest airport with at least 100k passengers. It includes the arrival-hour peak, the busiest 3-hour window and the check-in window (that window shifted by 1 hour), plus the air lane's own `checkin_window`. Two more fields go with it: `nearest_airport`, and `airports_60km`, the list of all airports within 60 km;
- `venues_5km`: the top 5 venues by capacity, and the total count;
- `visitor_sites_25km`: the top 5 sites by visits;
- the nearest Amtrak station, measured from the block if station points exist, else the county figure;
- transit trips per capita and walkability;
- `housing_demand`, with top origins.

**`h.str`** is written for every house; garages are skipped. It holds:
- `legal_status`;
- `legal_risk`: the city permit, primary-residence, cap and pending reasons. If an HOA or subdivision is on record, it adds: "many HOAs restrict STRs - check CC&Rs";
- `str_license_on_record`, from `h.local`;
- `bedrooms`, from the assessor;
- `est_monthly_cost`: the 30-year payment at 20% down. That covers principal, interest and property tax; insurance is not included;
- `demand_score`, `county_score` and `county_score_invest`;
- `housing_demand`: score and origin names;
- `note`.

## Caveats

- **Inputs from several lanes.** The tourism, visitor, jobs and traffic files come from separate lanes. Each run records `inputs_present` and per-input `inputs_coverage`. If inputs are missing, rankings mostly reflect QCEW tourism employment; re-run when the inputs land.
- **Small counties.** Percentiles from small counties are noisy: a resort with 900 private jobs can show 19% tourism intensity. The intensity cap, credibility shrinkage and `score_invest` (section 1b) dampen this but do not remove it.
- **Puerto Rico.** Puerto Rico municipios are included. They rank high on tourism intensity and are cheap by ACS value, so filter on `st` if needed.

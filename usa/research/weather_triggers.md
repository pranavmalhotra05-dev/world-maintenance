# Rough-weather cleaning triggers (dust, mud rain, wind, hail) — 2015–2026

Script: `usa/weather_triggers.py` → `web/usa/data/weather_triggers.json` (per county) and
`meta.weather_triggers` in every `web/usa/blocks/<slug>/meta.json` (per block). Cache: `usa/_cache/weather/`.

Why: haboobs and blowing dust coat windows, solar panels and pools; a dust day followed by monsoon rain
("mud rain") leaves streaks and pool sludge; ≥ 50 mph wind throws debris into pools and gutters; hail
damages screens and roofs. These are the days a window-, pool-, solar- or gutter-cleaning business
should market into.

## Sources and licences (all permanently free, commercial use OK)

| Source | Use | Licence quote | Licence URL |
|---|---|---|---|
| NOAA NCEI Storm Events Database, bulk details CSVs 2015–2026 (`StormEvents_details-ftp_v1.0_dYYYY_c*.csv.gz`, newest c-date per year) | Dust Storm, Dust Devil, High Wind, Strong Wind, Thunderstorm Wind, Hail, Heavy Rain, Flash Flood; county (C) or forecast zone (Z) | US federal work, public domain (17 U.S.C. 105). NWS: "The information on National Weather Service (NWS) Web pages are in the public domain, unless specifically noted otherwise" | https://www.weather.gov/disclaimer |
| NWS VTEC warnings via Iowa Environmental Mesonet (IEM) `watchwarn.py?accept=csv` — DS.W Dust Storm Warning, DS.Y Dust Advisory, DU.W/DU.Y Blowing Dust Warning/Advisory, HW.W High Wind Warning (county/zone UGC rows) | dust + wind days by zone | IEM: "The materials found on this website are in the public domain and may be used freely by anyone for any lawful purpose." (attribution to IEM / Iowa State appreciated) | https://mesonet.agron.iastate.edu/disclaimer.php |
| IEM historical NWS UGC polygons `api/1/nws/ugcs.geojson?state=XX&valid=DATE` (snapshots 2015-06, 2018-06, 2021-06, 2024-06, 2026-11) | zone→county mapping that respects zone renumbering (e.g. Phoenix zones renumbered ~2021); block forecast-zone lookup | same IEM public-domain statement | https://mesonet.agron.iastate.edu/disclaimer.php |
| EPA AQS AirData pre-generated daily PM10 (81102) files 2015–2026 | dust days where a monitor's 24-h mean > 150 µg/m³ | EPA Standard Open Data License: "all data produced by the U.S EPA is by default in the public domain and is not subject to domestic copyright protection under 17 U.S.C. § 105" | https://edg.epa.gov/EPA_Data_License.html |
| NOAA NCEI GHCN-Daily PRCP via NCEI Access Data Service (`dataset=daily-summaries`) + `ghcnd-inventory.txt` / `ghcnd-stations.txt` | rain days (≥ 0.1 in) | US federal work (NOAA NCEI), public domain (17 U.S.C. 105) | https://www.weather.gov/disclaimer |

Not used: NASA MODIS/VIIRS AOD (optional; skipped — cloud-gapped during monsoon storms and not quick),
Firecrawl (never). No keys or sign-ups needed.

## Definitions (per county, per local calendar day)

* **dust** — Storm Events Dust Storm / Dust Devil, or an NWS dust warning/advisory on a zone overlapping the
  county, or any AQS PM10 monitor in the county with 24-h mean > 150 µg/m³ (the 24-h PM10 NAAQS level).
  PM10-only spikes on Jan 1 and Jul 4–5 are dropped (fireworks).
* **dust_rain** ("mud rain") — a dust day with rain on the same day or the next 3 days (72 h): ≥ 0.1 in at any of up to
  4 long-record GHCN-Daily stations inside the county (fallback: nearest 2 within 40 km), or a Storm Events Heavy
  Rain / Flash Flood in the county.
* **wind** — Storm Events High Wind (any), Strong Wind or Thunderstorm Wind with magnitude ≥ 43.4 kt (50 mph),
  or an NWS High Wind Warning.
* **hail** — Storm Events Hail.
* `*_yr` = mean days per year over full years 2015–2025; `years{}` also includes partial 2026.
  `monthly_trigger_profile[12]` = mean trigger days per month; a block's `season_months` = months ≥ 50 % of the peak month.
* Zone → county: a zone is assigned to every county covering ≥ 5 % of its polygon area (largest overlap if none),
  using the UGC snapshot nearest the event year.
* Warning UTC times are shifted to local standard time by zone longitude (round(lon/15) h) before taking the date.

Per block: county stats, NWS forecast zone (point-in-polygon), nearest PM10 monitor + km, nearest long-record
rain gauge + km, and every event day since 2020 within 25 km (point Storm Events within 25 km, PM10 > 150 at
monitors within 25 km, zone-/county-level Storm Events and warnings for the block's own zone or county — no km).
A block-level `dust_rain` uses the nearest 3 gauges within 25 km.

## Caveats

* Zone events (dust storms, high wind) are attributed to every overlapping county, so huge counties
  (Maricopa, San Bernardino, Pima) collect events from all their zones. The block list uses the block's own zone.
* PM10 > 150 also fires on wildfire smoke, agricultural burning and construction; Storm Events are human-reported and
  under-count dust in sparsely populated areas; warnings are forecasts.
* GHCN gauges are point measurements; convective monsoon rain is patchy. "Any gauge in county" is generous.
* Results and coverage numbers: see the run log summary below.

## Run results (2026-11-24)

The output `weather_triggers.json` is 3.97 MB and covers 3,159 counties. 727 counties had at least one dust day, 420 have an AQS PM10
monitor, and 820 had rain stations assigned. Rain gauges are only looked up for dust counties and block counties. The run used 988 PM10 monitors and 2,674 GHCN gauges. It kept 449,604 Storm
Events event-days and 83,954 warning zone-days. 41 zone events could not be mapped (Guam/CNMI, a few retired NC/AZ zones).
A cold run takes about 50 min (mostly the IEM yearly CSVs and the GHCN batches). A cached rerun takes about 6.5 min. The cache is about 500 MB.

Top 20 counties by dust_then_rain_yr (mean days/yr 2015–2025):

| # | County | dust_rain | dust | wind | hail |
|---|---|---|---|---|---|
| 1 | Pinal AZ 04021 | 21.4 | 59.0 | 7.4 | 1.9 |
| 2 | Maricopa AZ 04013 | 16.3 | 27.9 | 13.4 | 3.6 |
| 3 | Pima AZ 04019 | 13.6 | 17.1 | 17.1 | 4.3 |
| 4 | Mono CA 06051 | 11.9 | 16.8 | 18.7 | 0.2 |
| 5 | La Paz AZ 04012 | 6.8 | 13.4 | 1.6 | 0.1 |
| 6 | Cochise AZ 04003 | 6.4 | 10.9 | 9.1 | 2.7 |
| 7 | Hidalgo NM 35023 | 6.2 | 14.2 | 5.0 | 0.2 |
| 8 | Yuma AZ 04027 | 6.1 | 17.8 | 3.6 | 0.1 |
| 9 | Riverside CA 06065 | 5.6 | 26.9 | 33.6 | 0.6 |
| 10 | Dona Ana NM 35013 | 4.7 | 18.0 | 10.2 | 1.7 |
| 11 | Otero NM 35035 | 4.2 | 8.0 | 13.5 | 2.1 |
| 12 | Mohave AZ 04015 | 3.8 | 4.8 | 11.1 | 1.9 |
| 13 | Imperial CA 06025 | 3.6 | 25.9 | 5.0 | 0.1 |
| 14 | Inyo CA 06027 | 3.6 | 16.6 | 14.9 | 0.2 |
| 15 | Grant NM 35017 | 3.6 | 7.0 | 6.1 | 1.1 |
| 16 | Luna NM 35029 | 3.3 | 11.5 | 5.4 | 0.4 |
| 17 | Lander NV 32015 | 2.5 | 3.2 | 5.6 | 0.1 |
| 18 | Elko NV 32007 | 2.4 | 3.2 | 21.2 | 1.2 |
| 19 | Bernalillo NM 35001 | 2.2 | 5.7 | 15.9 | 3.1 |
| 20 | El Paso TX 48141 | 2.2 | 14.7 | 8.5 | 1.8 |

Maricopa has a strong monsoon peak, with mean trigger days by month Jul 8.6 and Aug 9.6 against 0.5–2.3 in the other months. Its season is Jul–Aug.
Pima is the same: Jul 8.9 and Aug 8.7.

Blocks written (16): phoenix-az and scottsdale-az are in Maricopa, zones AZZ544 and AZZ546. Since 2020 within 25 km they show 169 dust days and 87 dust-then-rain
days, and the nearest PM10 monitor is 8.6 km and 10.4 km away. The TX/OK blocks have almost no dust: austin-tx 3.7 wind and 3.8 hail per year, dallas-tx 5.9 wind and 6.0
hail, tulsa-ok 8.6 wind and 6.3 hail, all with an Apr/May–Jun season. Mono CA is driven by Mono Lake / Owens Valley PM10.

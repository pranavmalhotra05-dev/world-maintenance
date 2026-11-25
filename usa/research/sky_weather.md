# Sky weather (real clouds for the 3D sky) — lane SW

Script: `usa/sky_weather.py` → `web/usa/blocks/<slug>/sky_weather.json` (+ `meta.files`, `meta.sky_weather` summary).
Run: `.venv\Scripts\python -u -W ignore usa/sky_weather.py [slug ...] [--no-clim]`. Warm re-run of all 16 blocks ≈ 3–5 s
(first run ≈ 45 s, which builds the IEM climatology). Cache: `usa/_cache/sky_weather/` (NWS points/stations forever,
the latest GOES scan per product, climatology per station refreshed every 7 days). Safe to schedule every 30–60 min.

## Sources and licence (all green: federal / public domain)

| # | Source | What we take | Licence quote |
|---|--------|--------------|---------------|
| 1 | NWS API `api.weather.gov` — `/points/{lat},{lon}`, `/gridpoints/.../stations`, `/stations/{id}/observations/latest`, `/gridpoints/{wfo}/{x},{y}` | latest METAR at the nearest station with a fresh (< 3 h) report: cloud layers (FEW/SCT/BKN/OVC/VV/CLR + base m AGL), textDescription, visibility, raw METAR; gridpoint `skyCover` (% sky) expanded to hourly for the next 7 days | weather.gov/disclaimer: "The information on National Weather Service (NWS) Web pages are in the public domain, unless specifically noted otherwise, and may be used without charge for any lawful purpose so long as you do not: 1) claim it is your own ... 2) use it in a manner that implies an endorsement or affiliation with NOAA/NWS, or 3) modify its content and then present it as official" |
| 2 | NOAA GOES-19 (GOES-East) ABI L2 on NOAA NODD, bucket `noaa-goes19` (anonymous HTTPS `https://noaa-goes19.s3.amazonaws.com/`) — `ABI-L2-ACMC` (Clear Sky Mask, CONUS, 2 km, every 5 min) and `ABI-L2-ACHAC` (Cloud Top Height, CONUS, 10 km) | BCM (0 clear/probably clear, 1 cloudy/probably cloudy) → clear-sky fraction of pixels whose centres are within 5 km of the block; HT median/min/max (DQF ≤ 1) within 15 km | registry.opendata.aws/noaa-goes: "NOAA data disseminated through NODD are open to the public and can be used as desired." NOAA requests attribution for unaltered data and asks that derived products not imply NOAA endorsement. |
| 3 | Iowa Environmental Mesonet ASOS archive `mesonet.agron.iastate.edu/cgi-bin/request/asos.py` (routine reports only, `report_type=3`), last 3 years at the same station | skyc1–4 → typical cloud cover by local hour (24) and month (12), plus clear-fraction by month | mesonet.agron.iastate.edu/disclaimer.php: "The materials found on this website are in the public domain and may be used freely by anyone for any lawful purpose." (attribution to IEM / Iowa State University appreciated) |

Note: the GOES netCDF global attribute `license` reads "Unclassified data. Access is restricted to approved users only."
That is boilerplate left over from the ground-system template. The distribution terms that apply are NODD's (row 2), and
NODD publishes the bucket openly. We record both in the output, attribute NOAA/NESDIS, and do not imply endorsement.
User-Agent sent: `roof-intel-sky-weather/0.1 (open-data research script; python-requests)`, with no personal details.

## Method

* **Station**: NWS `/points` gives the station list for the grid cell. We sort it by great-circle distance from the
  block origin and take the first station whose latest observation is < 3 h old and has cloud layers or a
  textDescription (we try up to 6). Stations that were skipped are listed in `current.skipped`.
* **Station cover %**: max over layers using okta midpoints (FEW 18.75, SCT 43.75, BKN 75, OVC/VV 100, CLR/SKC 0).
  `cloud_base_m` = lowest base of any non-clear layer; `ceiling_m` = lowest BKN/OVC/VV. Bases are **AGL at the station**.
* **GOES projection**: lat/lon to fixed-grid scan angles and back use the GOES-R PUG geostationary equations with the
  file's own `goes_imager_projection` attributes. We take a pixel window around the block, inverse-project it, and keep
  the pixels within the radius. Space pixels (DQF 2) and fill values are dropped.
* **Fusion (`sky`)**: `cloud_cover_pct = max(station %, GOES cloud fraction × 100)`. ASOS/AWOS ceilometers only see
  to about 3,660 m (12,000 ft), so an automated `CLR` means "no cloud below 12,000 ft". When the station reports 0 %,
  GOES shows ≥ 50 % cloud and the cloud top is > 3.6 km, `high_cloud_above_ceilometer=true`, which means high or
  mid cloud such as cirrus. This happened at Cincinnati, Columbus, Kansas City and Tulsa on the first run.
* **Climatology**: mean cover per bucket over about 26k routine hourly METARs (3 years). A bucket needs ≥ 30 obs, and
  the whole block is `null` if there are < 500 obs.

## Output schema (`sky_weather.json`)

```
{generated, location{lat,lon,tz,city,state},
 sky{cloud_cover_pct, station_cover_pct, satellite_cover_pct, low_cloud_base_m, cloud_top_m,
     high_cloud_above_ceilometer, condition, basis, note},                       # <- what the renderer should use
 current{cloud_cover_pct, layers[{amount, base_m}], cloud_base_m, ceiling_m, base_reference, condition,
         visibility_m, observed_at, age_min, station{id,name,km}, raw_metar, source, skipped},
 hourly[{time (UTC, ...Z), cloud_cover_pct}]  (168 h), hourly_source{source, grid, updated, error},
 goes{clear_sky_fraction, cloud_fraction, pixels, radius_km, cloud_top_m, cloud_top_min_m, cloud_top_max_m,
      cloud_top_pixels, time, cloud_top_time, satellite, source, cloud_top_source},
 climatology{by_month_pct[12], by_hour_pct[24] (local hour), clear_frac_by_month[12], hour_basis, n_obs, period,
             station, method, source} | null,
 licence{summary, nws, goes, iem, attribution}}
```

## Caveats

* GOES cloud masks are less reliable near the day/night terminator and over snow. On the first run Huntsville
  reported OVC at 1 km while GOES gave 87 % clear at about 4 pm local time. The max() fusion keeps the station's
  overcast.
* We did not check the ACHA HT height reference (MSL or AGL) against the PUG. Treat `cloud_top_m` as approximate
  (± a few hundred m).
* The NWS gridpoint endpoint sometimes returns 5xx. The script retries 4 times, then writes `hourly=[]` with
  `hourly_source.error`.

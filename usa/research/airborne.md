# Airborne lane: open data from platforms below orbit

Scope: drones, crewed aircraft, NASA airborne science, ADS-B and street-level cameras. Weather balloons and ground
stations belong to other lanes. Every licence below was checked on 2026-11-26 in the built-in browser or with small keyless
API probes. The full records (access, resolution, coverage, licence quote and URL) are in `airborne.json` (33 records).

Rule used: GREEN only for public domain, CC0, CC BY, CC BY-SA, ODbL, or an explicit commercial-use statement, and the
data must stay free. Disclaimer-only terms, unstated terms, non-commercial terms or "contact us" count as GREY or RED.

## Table

| Source | Platform | Res. | Blocks / coverage | Licence (short) | Verdict | Per-house | In app? |
|---|---|---|---|---|---|---|---|
| NAIP | aircraft | 0.6 m | CONUS | Public Domain w/ attribution | GREEN | yes | yes |
| NOAA NGS ERI | aircraft | 0.25 m | disaster events | NODD "can be used as desired" | GREEN | yes | yes |
| OpenAerialMap | drone/aircraft | cm | sparse | CC BY 4.0 (check per image) | GREEN | yes | yes |
| USGS 3DEP LiDAR | aircraft | 2-8+ pts/m2 | CONUS | US Gov public domain | GREEN | yes | yes (core) |
| **NGA/HSIP 133 US Cities LiDAR** | aircraft | ~1-4 pts/m2 | 133 metros, 2 epochs 2006-2012 (Austin, Dallas, Denver, KC, Minneapolis, Phoenix...) | US Gov public domain | GREEN | yes | no |
| **NOAA Digital Coast coastal LiDAR** | aircraft | 1-20 pts/m2 | 13 coastal/Great Lakes blocks | NODD "can be used as desired" | GREEN | yes | no |
| **OpenTopography / NCALM** | aircraft | Austin 12.7 pts/m2 | austin, boulder, portland, SLC, san-diego | OT "commercial uses" OK; Austin set CC BY 4.0 | GREEN (per dataset) | yes | no |
| **Missouri MSDIS 2023/2024** | aircraft | 6 in | Jackson/Clay/Platte (kansas-city-mo) | CC BY 4.0 | GREEN | yes | no (wrongly rejected) |
| Indiana IGIO orthos | aircraft | 3-6 in | Indiana | CC0 | GREEN | yes | no |
| **DC OCTO orthos + LiDAR** | aircraft | 3 in (2025) | washington-dc | CC BY 4.0 / CC0 (Open Data DC) | GREEN | yes | no |
| NOAA NGS CMP DSS orthos | aircraft | ~0.15-0.35 m | Philadelphia 2026, Boston, Ft Lauderdale... | disclaimer only (federal work) | GREY | yes | no |
| NJ statewide imagery/LiDAR | aircraft | 0.15-0.3 m | NJ | "as is" disclaimer | GREY | yes | no |
| AVIRIS-Classic/NG/3 | NASA aircraft | 3-20 m | flight lines | CC BY 4.0 (AWS) / NASA CC0 | GREEN | neighbourhood | no |
| G-LiHT | NASA aircraft | <1 m | strips | NASA CC0 | GREEN | neighbourhood | no |
| UAVSAR | NASA aircraft | 2-7 m | campaign lines | NASA CC0 | GREEN | no | no |
| LVIS | NASA aircraft | 10-20 m | campaigns | NASA CC0 | GREEN | no | no |
| MASTER | NASA aircraft | 5-50 m | campaign lines | NASA CC0 | GREEN | neighbourhood | no |
| USGS UAS releases | USGS drones | cm | science sites | USGS public domain | GREEN | no | no |
| USGS Earth MRI | airborne geophysics | 100s m | regional | USGS public domain | GREEN | no | no |
| USGS HDDS | mixed | varies | disasters | login, mixed commercial licences | GREY | yes | no |
| CAP / FEMA photos | aircraft | obliques | disasters | not stated | GREY | yes | rejected |
| MADIS AMDAR/ACARS | airliners | profiles | airports | airline-proprietary, no web display | RED | no | no |
| **adsb.lol** | ADS-B | 1 s | global | ODbL 1.0 | GREEN | neighbourhood | no |
| OpenSky | ADS-B | s | global | non-commercial | RED | - | rejected |
| ADS-B Exchange | ADS-B | s | global | commercial licence needed | RED | - | rejected |
| airplanes.live | ADS-B | s | global | personal/non-commercial | RED | - | no |
| FAA UAS Facility Maps | FAA airspace data | grid | US | disclaimer + indemnity | GREY | yes (ops) | no |
| Mapillary | street cams | photo | patchy | CC BY-SA + logo | GREEN | yes | code ready, no token |
| KartaView | street cams | photo | patchy | CC BY-SA 4.0 | GREEN | yes | yes |
| Panoramax | street cams | photo | few US (Austin) | CC BY-SA 4.0 per item | GREEN | yes | no |
| GeoNadir / ODM | drones | cm | not US residential | per dataset, unverified | GREY | no | no |
| Nearmap/EagleView/Vexcel/Hover | aircraft | 5-10 cm | metros | proprietary | RED | - | rejected |
| City airborne thermal surveys | aircraft IR | 0.5-1 m | none open found | - | GREY | - | no |

## Findings that change existing decisions

* **Missouri MSDIS is now CC BY 4.0.** MSDIS published an AWS Open Data registry entry (bucket filled in Oct 2026)
  with the line "governed by Creative Commons BY 4.0". `imagery.md` and `imagery_sources.json` still reject MSDIS as
  "terms not explicit". Jackson County 2024 6-inch tiles cover the kansas-city-mo block.
* **The Austin block has a second, denser LiDAR.** NCALM's TX15_Rapid survey (June 2015, 12.7 pts/m2, CC BY 4.0) is on
  OpenTopography, and an OT catalog query on the block bounds returns it.
* **Most first-wave blocks have 2006-2012 LiDAR epochs.** The NGA 133 Cities collection is on free USGS rockyweb HTTPS.
  Do not use the AWS copy: it is Requester Pays.

## Top 5 recommendations for this app

1. **Multi-epoch LiDAR change per house.** Use NGA 133 Cities (2006-2012) + NOAA coastal LiDAR + OpenTopography/NCALM
   alongside today's 3DEP. Draw every point from each epoch on top of each other and colour the deviations: tree growth
   over the roof, new extensions or pools, removed trees. This is the strongest maintenance signal that is not in the
   app yet, and it follows the keep-all-points rule.
2. **Unblock Kansas City with MSDIS 2024 6-inch imagery** (CC BY 4.0, s3://mo-gis-imagery, use range reads) and add
   **DC 2025 3-inch orthos + 2026 snow ortho + 2022 LiDAR** (Open Data DC, CC BY 4.0) to `usa/imagery.py`.
3. **Read OpenTopography per block.** Query `otCatalog` on each block's bounds, store each dataset's licence, and keep
   only CC BY / public-domain ones. Austin TX15_Rapid is the first hit.
4. **adsb.lol survey-flight detector (ODbL).** Use the daily globe_history traces to flag low, gridded "lawnmower"
   flight patterns over a block. That shows when new aerial imagery or LiDAR was just flown, and it can track
   helicopter/low-level traffic. Keep it aggregate and attribute "adsb.lol, ODbL 1.0".
5. **Street-front photos from Panoramax** (CC BY-SA 4.0 per item, keyless STAC), added next to KartaView. Add the
   Mapillary token once the user has one.

Not recommended: GREY sources until they publish a licence (NOAA NGS CMP orthos: ask NGS for a statement, because the
2026 Philadelphia imagery would be valuable; FAA UAS facility maps: link out only), and all RED ADS-B and commercial
aerial sources.

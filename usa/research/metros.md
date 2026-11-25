# National expansion: 22 new metro blocks (lane MB, 2026-11-24)

Goal: spread the 3D residential blocks across every US region and the main housing types, on top of the 16
blocks already built (Minneapolis x2, Saint Paul, Dallas, Austin, Denver, Boulder, Columbus x2, Cincinnati, Tulsa,
Kansas City, Huntsville, Birmingham, Phoenix, Scottsdale). The batch file is `usa/metros.csv`. Build it with
`python -m usa.pipeline --batch usa/metros.csv --half-m 40 --jobs 2`.

## How each spot was checked
1. **Address**: a round hundred-block number on an ordinary residential street (it names a street segment, not
   anyone's home), matched by the **US Census geocoder** (`usa.common.geocode`, public domain). The geocoder
   interpolates the point along the street's TIGER address range.
2. **LiDAR**: `usa.common.pick_ept` (the same selection `build_block.py` uses). It does a point-in-polygon test against
   `data/national/ept_boundaries.geojson`, needs 100 % coverage of the 80 m x 80 m box, and prefers the newest year
   and then the highest density. The year comes from WESM collect_end or the project name. Density = EPT point count /
   project boundary area. That is a project-wide average with all returns counted, so the real ground density varies.
3. **Residential check**: ACS 2023 B25024 (units in structure) for the geocoded **block group**, read from
   `data/national/b25024.dat` (public domain). "SF det" = 1-unit detached share, "SF att" = 1-unit attached
   (rowhouse/townhouse), "2-4" = duplex to fourplex. I used this check instead of OSM: the local Geofabrik extracts
   exist only for the 8 original project states, and the public Overpass servers ask commercial users not to use them.

## Chosen blocks

| slug | address (Census match) | region | EPT project | year | pts/m2 | BG SF det / att / 2-4 | why |
|---|---|---|---|---|---|---|---|
| seattle-wa | 6500 37th Ave NE | Pacific NW | WA_KingCo_1_2021 | 2021 | 12.4 | 87 / 1 / 1 % | Bryant/Ravenna 1920s-50s bungalows, steep pitched roofs, mature tree canopy |
| portland-or | 5000 NE 30th Ave | Pacific NW | OR_OLCMetro_2019 | 2020 | 15.2 | 68 / 0 / 28 % | Concordia craftsman/Old Portland homes, some plexes |
| sacramento-ca | 2600 Portola Way | West Coast | USGS_LPC_CA_NoCAL_Wildfires_B5a_2018 | 2018 | 3.6 | 94 / 0 / 6 % | Land Park 1930s-40s storybook/ranch homes under heavy canopy |
| san-diego-ca | 3000 Kalmia St | West Coast | CA_SanDiegoQL2_2014 | 2015 | 3.8 | 43 / 13 / 29 % | North Park/South Park craftsman homes with granny flats; flat and low-slope roofs |
| las-vegas-nv | 2800 Burnham Ave | Mountain West | NV_ClarkCo_2_B22 | 2022 | 14.7 | 81 / 0 / 0 % | 1950s-60s desert ranch homes with tile/flat roofs and pools |
| salt-lake-city-ut | 1500 E Kensington Ave | Mountain West | USGS_LPC_UT_Wasatch_L4_2013_LAS_2016 | 2016 | 10.1 | 86 / 2 / 12 % | Sugar House brick bungalows (hail/snow roof market) |
| houston-tx | 1000 Arlington St | Gulf Coast | TX_Coastal_B1_2018 | 2018 | 10.8 | 66 / 3 / 6 % | Houston Heights: pier-and-beam bungalows next to new 2-storey infill (hurricane roof market) |
| new-orleans-la | 8000 Hampson St | Gulf Coast | LA_2021GNO_1_C22 | 2021 | 11.5 | 40 / 3 / 22 % | Uptown/Carrollton shotgun singles and doubles, raised houses |
| fort-lauderdale-fl | 1400 NE 15th Ave | Southeast | USGS_LPC_FL_Southeast_B1_2018_LAS_2019 | 2019 | 12.7 | 80 / 1 / 11 % | South Florida CBS ranch homes, barrel-tile roofs, pools |
| orlando-fl | 1500 Hillcrest St | Southeast | FL_Peninsular_FDEM_Orange_2018 | 2019 | 22.0 | 69 / 4 / 6 % | Lake Eola Heights / Colonialtown 1920s bungalows (central Florida) |
| atlanta-ga | 1200 Lanier Blvd NE | Southeast | GA_Statewide_B3_2018 | 2019 | 5.0 | 84 / 0 / 0 % | Virginia-Highland craftsman homes, hilly with dense canopy |
| ~~charlotte-nc~~ | 2300 Sherwood Ave | Southeast | USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019 | 2019 | 27.9 | 68 / 12 / 2 % | **Failed build_block twice: Overture returned 0 building footprints for the box, then ValueError in np.vstack (empty mesh). Replaced by spokane-wa.** |
| spokane-wa | 1000 E 29th Ave | Inland Northwest | WA_NorthEast_2_B22 | 2022 | 20.3 | 57 / 7 / 17 % | South Hill 1910s-50s homes; inland snow/hail climate (replacement for Charlotte) |
| nashville-tn | 1800 Ashwood Ave | South | TN_DavidsonCo_1_2022 | 2022 | 37.0 | 64 / 8 / 18 % | Belmont-Hillsboro bungalows plus "tall-skinny" infill |
| oak-park-il | 600 N East Ave | Midwest (Chicago suburb) | USGS_LPC_IL_4County_Cook_2017_LAS_2019 | 2019 | 26.8 | 100 / 0 / 0 % | Prairie/Victorian frame houses on a classic Chicago-area grid with alleys |
| detroit-mi | 16900 Prairie St | Midwest | USGS_LPC_MI_WayneCo_2017_LAS_2018 | 2018 | 3.8 | 75 / 19 / 3 % | Bagley/University District brick Tudors and colonials |
| omaha-ne | 2600 N 52nd St | Great Plains | USGS_LPC_NE_Eastern_UA_2016_LAS_2018 | 2018 | 5.6 | 99 / 0 / 1 % | Benson-area early 20th-c. frame homes in the Plains hail belt |
| philadelphia-pa | 2600 S Bancroft St | Mid-Atlantic | USGS_LPC_DE_DelawareValley_HD_2015_LAS_2017 | 2017 | 4.9 | 2 / 88 / 10 % | South Philly flat-roof brick rowhouses (attached housing type) |
| washington-dc | 1500 Jackson St NE | Mid-Atlantic | USGS_LPC_MD_VA_Sandy_NCR_2014_LAS_2015 | 2015 | 5.6 | 70 / 30 / 0 % | Brookland detached frame houses and rowhouses |
| levittown-ny | 15 Water Ln | Northeast (NYC suburb) | USGS_LPC_NY_LongIsland_Z18_2014_LAS_2015 | 2015 | 3.0 | 100 / 0 / 0 % | The original postwar mass-built Cape Cod/ranch suburb, Long Island |
| somerville-ma | 50 Highland Rd | New England (Boston area) | MA_CentralEastern_1_2021 | 2021 | 20.1 | 9 / 0 / 91 % | Triple-deckers and two-families (the Boston-area 2-4 unit housing type) |
| honolulu-hi | 1500 Ala Aolani St | Pacific | HI_NOAAMauiOahu_2_B20 | 2023 | 26.5 | 95 / 3 / 0 % | Moanalua/Salt Lake single-wall plantation-style and post-war homes, hip roofs |
| anchorage-ak | 8000 Sandlewood Pl | Alaska | USGS_LPC_AK_Anchorage_2015_LAS_2017 | 2017 | 5.2 | 59 / 8 / 22 % | Anchorage split-levels with snow-load roofs, high latitude (61 N) |

Every row has 100 % EPT coverage of the 80 m block box. The lowest densities are Levittown (3.0), Sacramento (3.6),
San Diego and Detroit (3.8). These are QL2-era projects. They still have every point, but roof planes will be
sparser than the 10-37 pts/m2 blocks.

## Candidates checked and dropped
- **Boise ID** (1800 N 17th St; 2500 S Owyhee St): no EPT project contains the point. Ada County is missing from the
  boundaries file.
- **Miami FL** (6100 SW 34th St; 4000 SW 4th St): no EPT project contains the point. Fort Lauderdale (Broward) and
  Orlando cover South and Central Florida instead.
- **Raleigh NC** (1500 Canterbury Rd) and **Durham NC** (1000 Watts St): no EPT coverage. Charlotte covers NC.
- **Tampa FL**: only FL_HillsboroughCo_2007 (2.6 pts/m2, 2007), which is too old and too sparse.
- **Albuquerque NM** (2.85 pts/m2), **Baltimore MD** (3.8, 2016), **Tucson AZ**, **San Antonio TX**, **Jacksonville FL**,
  **Milwaukee WI**, **Spokane WA**, **Montclair NJ**: all covered, but left out to keep the batch near 20 and avoid
  a third AZ or TX block. Any of them can be added later as a backup.
- **Geocoder misses** (no Census match, replaced): 4100 Mount Abernathy Ave San Diego, 1500 Robertson Way Sacramento,
  2600 S Garnet St Philadelphia, 500 Grange Rd Teaneck NJ, 2800 W 41st Ave Anchorage.
- First-try spots dropped for a low single-family share: Omaha 5000 Davenport St (15 % SF det), Anchorage
  2000 W 32nd Ave (13 %), Honolulu 1200 10th Ave (27 %).

## Licences
Census geocoder/TIGER and ACS: US government, public domain. USGS 3DEP LiDAR (EPT on AWS Open Data): public domain.
The downstream pipeline stages use the sources recorded in each block's sources.json.

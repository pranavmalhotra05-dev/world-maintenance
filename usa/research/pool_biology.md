# Pool biology, gases and what can be sensed from above

Research lane "pool-biology" for World Maintenance. Scope: what lives in backyard pools compared with ponds and lakes, which gases each state gives off, and whether any remote sensor (gas or optical) can judge one house's pool from the air or from orbit.

How the numbers are labelled:
- **[src]** means the number comes from the cited paper or agency page. I read it in the browser on 2026-11-26.
- **[pond-proxy]** means a measured value for small ponds or farm dams, applied to pools because nobody has measured pools.
- **[estimate]** means my own calculation. The working is shown.

Structured copy of this report: `usa/research/pool_biology.json`.

---

## 0. Verdict

1. **Gas sensing cannot rate a single pool, and it is not close.**
   - A badly neglected 40 m² pool behaving like the worst urban pond on record gives off about **9 g CH4/h**. A typical neglected pool gives off about **0.2 g/h**.
   - The most sensitive airborne methane imager (AVIRIS-NG / GAO class) needs about **5-10 kg/h**. That is roughly 1,000x more than the worst case.
   - EMIT needs about **1,060 kg/h** for 90% detection, roughly 100,000x more. TROPOMI only reports "ultra-emitters" above about 25 t/h.
   - Chlorinated pools give off chloroform and trichloramine. No satellite or public airborne product retrieves these gases.
   - The only thing that comes close is a **ground sniffer driven past the house**, and even that only in the worst case. Commercial leak-survey tests use 0.5 slpm (about 20 g CH4/h) as their smallest leak.
2. **Optical sensing does work.** NAIP 4-band already separates clear, green and dark pools. Published vector-control work used aerial NDWI to find pools (78-91% accuracy) and aerial surveys to find green pools (Reisen 2008: 17% of visible pools were green during the 2007 foreclosure wave).
3. **Hyperspectral data could separate cyanobacteria from green algae**, using the phycocyanin trough near 620 nm. But only airborne AVIRIS-class data at 1-5 m resolves a single pool, and it has no routine coverage.
4. **Ground-truth data worth using:** county code-enforcement "Standing Water" violations (Montgomery County MD, public domain), Bronx 311 standing-water complaints (public domain tag), the OSM `leisure=swimming_pool` layer (ODbL), and EPA CyAN, WQP-USGS and NLA for ponds and lakes.

---

## 1. Organisms

### 1a. Residential swimming pool, chlorinated or failing

| Group | Typical organisms | Appears when | Notes |
|---|---|---|---|
| Green algae (planktonic) | *Chlorella*, *Chlamydomonas*, *Scenedesmus* | Free chlorine (FC) below about 1 ppm, warm water, sun, phosphate from leaves, fertiliser or fill water | Turns water pea-green. Most common "green pool" cause [industry knowledge; species list is an estimate, not pool-surveyed] |
| "Mustard / yellow algae" | Chlorine-tolerant yellow-green or green forms on shaded walls | Low FC and shade | Trade term, not a taxon. No peer-reviewed pool survey found [GREY knowledge] |
| "Black algae" | Cyanobacteria (*Oscillatoria*, *Phormidium*-type filaments) in plaster pits | Rough plaster, low FC. Layered sheath shields them from chlorine | Trade term [industry]. Sheath protection is general cyanobacteria biology |
| "Pink slime" | *Methylobacterium* and other pigmented bacteria in biofilm | Low-flow corners, PVC, brushes | Chlorine-tolerant biofilm former [general microbiology; no pool flux data] |
| Biofilm bacteria | *Pseudomonas aeruginosa* (hot-tub rash), non-tuberculous *Mycobacterium* | Hot tubs and spas, low disinfectant | A public pool yielded a NTM *Mycobacterium* genome (MRA 2024, doi:10.1128/mra.00896-24). Metagenomes of 20 Shanghai pools found 24,035 species, 441 of them pathogenic (BMC Microbiol 2026, PMC13465383) [src] |
| *Legionella* | *L. pneumophila* | Hot tubs at 35-40 °C with poor disinfection. Spread by inhaled aerosol | CDC lists it among swimming-related illnesses [src, CDC] |
| *Cryptosporidium* | *C. parvum*, *C. hominis* | Faecal accidents. **Survives more than 7 days in properly chlorinated water** | CDC healthy-swimming page [src] |
| *Naegleria fowleri* | Thermophilic amoeba | Warm, unchlorinated or under-chlorinated water | Mostly a lake disease. A 2024 China PAM case was suspected to come from an indoor pool (Front Med 2025, PMC12440956) [src] |
| Mosquito larvae | *Culex quinquefasciatus* / *Cx. pipiens* complex, *Aedes* | Green or neglected pools | Kern County 2007: green pools drove a 276% rise in human West Nile virus cases (Reisen et al. 2008, EID, PMC2630753) [src] |

### 1b. Ornamental ponds, stormwater ponds and lakes

| Group | Organisms | Drivers |
|---|---|---|
| Cyanobacteria (HAB) | *Microcystis*, *Dolichospermum/Anabaena*, *Planktothrix*, *Aphanizomenon* | Warm, still water with high phosphorus and nitrogen. Portishead Lake (UK) blooms reached 5.6×10⁶ cells/mL, with microcystins up to about 18,000 µg/L (Toxins 2026, PMC13709399) [src] |
| Diatoms | *Asterionella*, *Fragilaria*, *Aulacoseira* | Cool, silica-rich spring water |
| Green algae and filamentous mats | *Spirogyra*, *Cladophora*, *Chlorella* | Nutrients and light. Shallow edges |
| Macrophytes and duckweed | *Lemna*, *Elodea*, *Typha* | Shallow, nutrient-rich water |
| Methanogenic archaea and sulfate reducers | Sediment microbes | Anoxic, organic-rich sediment. This is the source of the CH4 and H2S in §2 |
| Odour producers | Cyanobacteria and *Streptomyces* (geosmin, 2-MIB) | Blooms and sediments. These are the "muddy" off-flavour compounds in aquaculture ponds (PMC5636946) [src] |

### 1c. Conditions and timescales

| Factor | Pool target | Effect | Source |
|---|---|---|---|
| Free chlorine | **1-4 ppm** (bromine 3-8) | Kills most germs in minutes. Not *Cryptosporidium* | CDC [src] https://www.cdc.gov/healthy-swimming/safety/what-you-can-do-to-stay-healthy-in-swimming-pools.html |
| pH | **7.2-7.8** | Outside this range chlorine works less well | CDC [src] |
| Cyanuric acid (CYA) | Stabiliser | Slows UV loss of chlorine but makes it weaker. Health review: PMC10801151 | [src] |
| Temperature | — | Algal growth roughly doubles per +10 °C up to about 30 °C | [general phycology] |
| Phosphate | — | Usually the limiting nutrient. Comes from leaves, fertiliser and fill water | [general limnology] |

**How fast a pool goes green [estimate]:**
1. **Chlorine runs out.** Unstabilised chlorine in midday sun loses most of its strength within hours. With CYA it lasts about 1-3 days without dosing. This is general photolysis literature; I did not re-source a number in this lane.
2. **Algae grow.** Planktonic green algae divide about 1-2 times a day in warm, lit water. Going from a residual of about 10-100 cells/mL to a visibly green 10⁵-10⁶ cells/mL takes 10-17 doublings, which is **about 5-12 days**.
3. **Total from the last dose to a green pool: about 1-2 weeks in summer.** With a heavy nutrient load or a large surviving inoculum it can be quicker. Pool-trade anecdotes of "green overnight" describe this inoculum case.
4. **Mosquitoes follow.** A *Culex* generation takes about 7-10 days at 25-30 °C [general entomology, estimate]. So a pool abandoned at a foreclosure produces mosquitoes within about 2-3 weeks.

---

## 2. Gases and fluxes per m² per day

Pool-measured outdoor fluxes basically **do not exist** in the literature. The values below are pond or farm-dam proxies, or estimates from concentration × gas-transfer velocity k. I used k ≈ 1 m/d for a sheltered, low-wind small water body [estimate].

| State | Gas | Flux per m² per day | Type | Source / working |
|---|---|---|---|---|
| Chlorinated | CO2 (off-gassing) | **about 5-6 g CO2** | estimate | Total alkalinity 100 mg/L as CaCO3 at pH 7.5 gives dissolved CO2 ≈ 6.2 mg/L; air equilibrium is about 0.6 mg/L. Excess 5.6 g/m³ × k 1 m/d. This is why pool pH drifts up |
| Chlorinated | Chloroform (THM) | **about 10-100 mg** | estimate | Outdoor-pool CHCl3 about 10-100 µg/L × k 1 m/d. Water-concentration range is typical pool literature, not re-sourced here |
| Chlorinated | Trichloramine NCl3 | **about 10-100 mg** | estimate | Water NCl3 about 0.01-0.1 mg/L × k. Measured mainly in indoor-pool air (asthma studies, PMC5896097; box model, doi:10.1080/15459624.2024.2327370). No outdoor flux found |
| Algae-green, day | O2 (released) | **about 3-10 g O2** | estimate | Hypereutrophic gross primary production of about 1-4 g C/m²/d × 2.67 g O2/g C |
| Algae-green, night | CO2 (respired) | **about 2-8 g CO2** | estimate | Night respiration of about 0.5-2 g C/m²/d |
| Neglected "swamp" / small pond | CH4, diffusive only | **3.5 (fenced) / 8.05 (unfenced) mg mean; 1-164 mg range** | pond-proxy | 64 Australian farm dams, Malerba et al. 2022, GCB, PMC9327511 [src] |
| Neglected / small pond | CH4, total incl. bubbles | **urban ponds mean 129 mg; stock dams 168; weirs 730; maximum 5,425 mg** | pond-proxy | 22 Queensland ponds, Grinham et al. 2018, HESS 22:5281, doi:10.5194/hess-22-5281-2018 (CC BY) [src]. Bubbling (ebullition) dominated |
| Small pond | CO2 | **−1.7 to 14 g** | pond-proxy | Malerba 2022 [src] |
| Very small ponds (<0.001 km²) | CO2 and CH4 | 8.6% of global lake and pond area, but 15.1% of CO2 and **40.6% of diffusive CH4** | pond-proxy | Holgerson & Raymond 2016, Nat Geosci, doi:10.1038/ngeo2654 [src]. Forested ponds were 504× supersaturated in CH4 (Holgerson 2015, doi:10.1007/s10533-015-0099-y) [src] |
| Anaerobic extreme | H2S | **≈49 mg (0.57 µg/m²/s)** | proxy (swine waste lagoon) | Iowa CAFO lagoons, PMC3144569 [src]. A neglected pool should be far below this. Treat it as a ceiling |
| Neglected / small pond | N2O | about 0.1-1 mg | estimate | Driven by nitrate (boreal lakes, PMC7078959). No pool data |
| Algae-green, high-pH day | NH3 | about 1-50 mg | estimate | Daytime pH of 9-10 in blooms shifts ammonium to NH3. No pool data |
| Cyanobacteria / sediment | Geosmin, 2-MIB | ng-µg scale | estimate | People smell them in water at about 10 ng/L [general]. The amounts emitted are negligible for sensing |

Dissolved oxygen is the strongest control. In Malerba 2022, doubling DO from 5 to 10 mg/L cut CH4 by 74% [src]. A pool that is still circulating and photosynthesising stays mostly oxic. CH4 and H2S need a stagnant pool with a layer of leaves and sediment on the floor.

---

## 3. Detection math for a 40 m² pool (35-50 m²)

Per-pool emission = flux × 40 m² ÷ 24 h:

| Scenario | CH4 flux (mg/m²/d) | Per pool | g/h | kg/h |
|---|---|---|---|---|
| Diffusive, typical dam | 8 | 0.32 g/d | 0.013 | 1.3e-5 |
| Urban pond mean (with bubbles) | 129 | 5.2 g/d | **0.22** | 2.2e-4 |
| Worst pond on record | 5,425 | 217 g/d | **9.0** | **0.009** |
| H2S ceiling (swine lagoon) | 49 | 2 g/d | 0.08 | 8e-5 |
| Chloroform (chlorinated) | 10-100 | 0.4-4 g/d | 0.02-0.17 | <2e-4 |
| CO2 off-gas (chlorinated) | 5,600 | 224 g/d | 9 | 0.009 |

| Sensor | Pixel / footprint | Detection limit (CH4) | Worst-case pool ÷ limit | Detects one pool? |
|---|---|---|---|---|
| EMIT (ISS) | 60 m | 90% probability of detection at **1,060 kg/h** at 3 m/s wind (ES&T 2024, PMC11636213) [src]. Attribution paper: Thorpe 2023, Sci Adv, doi:10.1126/sciadv.adh2391 | 1e-5 | **No** |
| AVIRIS-NG / GAO airborne | about 1-5 m | "as low as **5 to 10 kg/h**" (Cusworth 2022 PNAS, PMC9499563, citing Duren 2019 Nature, doi:10.1038/s41586-019-1720-3) [src] | about 1e-3 | **No** |
| AVIRIS-3 | about 1-5 m airborne | Same order as AVIRIS-NG [estimate] | about 1e-3 | **No** |
| Carbon Mapper Tanager-1/2 | 30 m | About 100 kg/h class. 100 kg/h is the EU "super-emitter" threshold the sector reports against [estimate, not confirmed from a primary paper] | 1e-4 | **No** |
| TROPOMI / Sentinel-5P | 5.5×7 km | Ultra-emitters about 25 t/h and above (Lauvaux 2022 Science, doi:10.1126/science.abj4351) [src title; threshold from paper] | 4e-7 | **No** |
| GOSAT / MethaneSAT | 10 km sounding / about 100×400 m | Regional or area fluxes. Point limit in the hundreds of kg/h [estimate] | <1e-4 | **No** |
| TEMPO | about 2×4.75 km | **Does not retrieve CH4 or H2S.** It retrieves NO2, HCHO and O3 | — | **No** |
| Ground mobile CH4 sniffer (car or walking, ppb analyser) | Street level | Commercial leak-survey tests used 0.5, 5 and 10 slpm (**0.5 slpm = 0.334 g/min ≈ 20 g/h**) (ACS Omega 2026, PMC12902981) [src] | about 0.45 | **Marginal, worst case only** |
| Ground H2S sensor / human nose | Street level | ppb analysers; the nose detects about 0.5-1 ppb | Ceiling case gives about 2 ppb at 10 m downwind [estimate] | Only for an extreme anaerobic pool, from next door |

Column check, EMIT [estimate]: 9 g/h = 0.0025 g/s. In 3 m/s wind across a 60 m pixel, gas stays in the pixel about 20 s. That leaves about 0.05 g in 3,600 m², or 8.7e-7 mol/m², against an air column of about 3.45e5 mol/m². That is ΔXCH4 ≈ 0.003 ppb, while EMIT noise is tens of ppb.

Column check, AVIRIS at 3 m [estimate]: the same source gives about 0.4 ppm·m, against noise in the hundreds of ppm·m.

**Conclusion: gas remote sensing of individual pools is physically impossible with current or planned instruments.** The gap is 3-7 orders of magnitude. EMIT data in the app (`web/app/data/emit.json`) should stay as context (landfills, oil and gas). It should never be used as a pool-health signal.

---

## 4. Optical biology proxies that do work

| Proxy | Physics | Instruments | Pool-scale? |
|---|---|---|---|
| Chlorophyll-a | Absorbs blue (about 440 nm) and red (about 675 nm). NIR / red-edge peak (about 700-710 nm) in dense blooms | Sentinel-2 B5 at 705 nm (20 m), OLCI, hyperspectral | Pool-scale only with airborne hyperspectral. NAIP has no red-edge band |
| Phycocyanin (cyanobacteria) | **Absorption trough at about 620 nm** | OLCI Oa6 (620 nm, 300 m), EMIT (60 m), AVIRIS (1-5 m) | Only AVIRIS-class resolves a single pool |
| NIR / NDVI signal of algae | Dense surface algae and scum reflect NIR like vegetation | NAIP NIR (60 cm), Sentinel-2 (10 m) | **Yes, NAIP** (this is what `usa/pools.py` already uses) |
| NDWI (green vs NIR) | Clear water has high NDWI. Algae lower it | NAIP | **Yes.** McFeeters 2013 NDWI + parcels: 78-91% accuracy (Remote Sens 5:3544, CC BY) [src] |
| Turbidity / brightness | Suspended solids raise red and green reflectance | NAIP, Sentinel-2 | Yes on NAIP (cloudy vs clear) |
| CyAN cyanobacteria index | Sentinel-3 OLCI, 300 m, daily/weekly 2016 onward, more than 2,000-lake forecast beta | EPA / NASA / NOAA / USGS (EPA CyAN page) [src] | **No.** Lakes only; a 300 m pixel is about 2,000 pools |

**Separating states in a pool:**

| State | NAIP 4-band (R, G, B, NIR at 0.6 m) | Hyperspectral (AVIRIS 1-5 m) |
|---|---|---|
| Clean blue | High B, low R and NIR, high NDWI | Clean water spectrum, peak about 480 nm |
| Turquoise / cloudy | High G and B, raised R | Flat brightness boost, no pigment features |
| Green algae (chlorophyte) | Strong G, NIR rising, NDVI > 0 | Chl-a trough at 675 nm plus red-edge peak at about 700 nm, **no 620 nm trough** |
| Cyanobacteria | Similar to green. Often more olive or blue-green. **Cannot reliably separate from green algae** | **620 nm phycocyanin trough** plus chl-a features |
| Debris / leaves / covered / empty | High NIR from leaves; cover or plaster bright in all bands | Leaf or cellulose spectrum; SWIR features |
| Dark / stagnant | Low in all bands | Low albedo; tannin (CDOM) absorbs in the blue |

**Rule of thumb:** NAIP separates **clean / cloudy / green / covered / dark**. Telling **green algae from cyanobacteria** needs a band at 620 nm and pixels of about 5 m or finer, which in practice means airborne hyperspectral.

**Published aerial / satellite pool-health work:**
1. Reisen et al. 2008, *Emerg Infect Dis* 14:1747 (PMC2630753, US Gov public domain). An aerial photo survey of Bakersfield found 17% of 42 visible pools and hot tubs green. The aerial survey turned up previously unknown problem pools [src].
2. McFeeters 2013, *Remote Sens* 5:3544 (CC BY 3.0). NDWI on high-resolution multispectral imagery plus parcels in a GIS for mosquito abatement. 78% match to known pools; 91% overall accuracy [src].
3. PLOS ONE 2021, PMC8659416. Deep-learning detection of pools and water tanks from UAV images for dengue control (Brazil) [src].
4. MMWR 2023 (doi:10.15585/mmwr.mm7217a1). Maricopa County record West Nile virus outbreak in 2021. The vector index is the surveillance lever [src abstract].

---

## 5. Open datasets and licence verdicts

| Dataset | What it gives us | Access | Licence | Verdict |
|---|---|---|---|---|
| NAIP 4-band | 60 cm RGB+NIR pool colour (already in use) | USDA / AWS / Planetary Computer | USDA, public domain | **GREEN** |
| EPA CyAN (Sentinel-3 OLCI) | Cyanobacteria index for lakes and reservoirs, 300 m, 2016 onward | https://www.epa.gov/water-research/cyanobacteria-assessment-network-cyan ; NASA OceanColor; CyAN app | US federal work, public domain | **GREEN** |
| EPA National Lakes Assessment | Lake chl-a, microcystin, nutrients | https://www.epa.gov/national-aquatic-resource-surveys/nla | US federal, public domain | **GREEN** |
| Water Quality Portal (WQP) | Discrete samples, chl-a, nutrients | https://www.waterqualitydata.us | USGS / EPA records are public domain. State and other contributor records have mixed terms | **GREEN (USGS / EPA-supplied only)**, **GREY (others)** |
| Sentinel-2 / Sentinel-3 | 10-20 m and 300 m water colour | Copernicus / AWS | Copernicus "free, full and open", commercial use allowed | **GREEN** |
| EMIT L2B CH4 / reflectance | 60 m hyperspectral, plumes | NASA LP DAAC / Earthdata | NASA, no restrictions | **GREEN** (Earthdata login is free) |
| AVIRIS-NG / AVIRIS-3 | 1-5 m hyperspectral flight lines | https://avirisng.jpl.nasa.gov / ORNL DAAC | NASA, open | **GREEN** (sparse coverage) |
| Montgomery County MD "Standing Water" code violations | Address-level standing-water nuisance cases, 2013 onward | Socrata `data.montgomerycountymd.gov/resource/mx9q-5uj7` | Portal tag "Public Domain" [src] | **GREEN.** Not pool-specific; lat/lon often 0, so geocode the address |
| Bronx 311 Standing Water complaints (2010 onward) | Complaint points | Socrata `bronx.lehman.cuny.edu` id ma23-mzpm | Tag "Public Domain" [src] | **GREEN** (a CUNY mirror of NYC 311) |
| Dallas 311 code violations | Includes stagnant water / pool items | dallasopendata.com | No licence field | **GREY** |
| State HAB report maps (e.g. CA, NY, OR, OH) | Advisory points for lakes | State portals | Varies by state; usually no explicit licence | **GREY** (check each state) |
| County vector-control green-pool data (Kern, Maricopa, Coachella Valley, etc.) | Green-pool service requests | Mostly not published as open data; some ArcGIS hubs | No licence | **GREY / RED** (would need a request) |
| OpenStreetMap `leisure=swimming_pool` | Pool polygons (labels for training) | Overpass / Geofabrik | ODbL | **GREEN** (share-alike on derived database) |
| Kaggle "swimming pool / car detection" and similar | Labelled pool chips | Kaggle | Mixed, often no licence or CC BY-NC | **GREY / RED** |
| PlanetScope (3 m daily) | Pool colour time series | Planet | Commercial licence | **RED** |
| Carbon Mapper / Tanager data | 30 m CH4 plumes | Carbon Mapper portal | Terms not verified in this lane; not a known CC BY / PD release | **GREY** (and unneeded for pools) |

---

## 6. What to build on

1. Keep `usa/pools.py` NAIP states. Add a **"neglect" time signal**: a pool that was clean_blue in one NAIP vintage and green_algae or dark in the next.
2. Calibrate the green / dark labels against public-domain standing-water violations (Montgomery MD, Bronx 311) and OSM pool polygons.
3. Use CyAN, NLA and WQP only for **pond and lake context**, such as a HOA pond with a cyanobacteria bloom near the house. They cannot score individual pools.
4. Drop any idea of methane, H2S or chloroform sensing for pools. Section 3 shows the gap is at least 1,000x even airborne.
5. If an AVIRIS-3 flight line ever covers a metro, test the 620 nm phycocyanin trough on pools as a research layer only.

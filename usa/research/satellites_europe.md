# European Earth-observation data: free and commercial-use-allowed (SAT-2)

As of 2026-11-24. Machine-readable records are in `satellites_europe.json` (46 records, ids EU-01 to EU-46).
Method: Python requests plus the built-in browser against official ESA, EU Copernicus, EUMETSAT, ECMWF, DLR, ASI, CNES and ICEYE pages, plus the AWS and Planetary Computer catalogues. Licence wording is paraphrased with short quotes. The `tested` field on each record says what was actually fetched.

## Counts

| Measure | n |
|---|---|
| Total records | 46 |
| commercial_ok = yes (licence text confirms) | 28 (6 of them are expansion missions not launched yet) |
| commercial_ok = verify (free, but commercial terms not confirmed) | 9 |
| commercial_ok = no (commercial, research-only, paid or restricted) | 9 |
| Global or partly global coverage | 39 |
| Europe-only | 6 (Sentinel-4, Urban Atlas, CLMS HR Layers, EGMS, MSG, MTG) |
| Not yet launched (Copernicus Expansion) | 6 |

## Key licences (the legal basis)

- **Copernicus Sentinels and Copernicus services.** EU Regulation 377/2014 and Commission Delegated Regulation 1159/2013 give free, full and open access. Reproduction, distribution, adaptation and combination are allowed for any lawful use, commercial use included. Credit it as "Copernicus Sentinel data [Year]", or "Contains modified Copernicus Sentinel data [Year]" if you changed it. Sources: [Sentinel legal notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice), [Reg 1159/2013](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32013R1159). Note that the *CDSE web portal content* (its text and images) is non-commercial. That rule covers the website, not the data.
- **ECMWF-run Copernicus services (CAMS, C3S, CEMS early-warning).** The licence is "free of charge, worldwide, non-exclusive, royalty free and perpetual", for any lawful purpose, with attribution. Source: [ECMWF Copernicus licence](https://apps.ecmwf.int/datasets/licences/copernicus/).
- **EUMETSAT** (data policy guide V2.2, 4 Nov 2026). It has two tiers:
  - "Core" data, which is the vast majority, is CC-BY-4.0 and may be used "even commercially".
  - "Recommended" data is licensed. It covers MSG SEVIRI L1.5, MTG FCI L1c, IRS L1, global ASCAT L1 and some Metop-SG L1. Non-commercial use is free. Commercial use of real-time data (under 1 hour old) costs EUR 4,000/yr (End User) or EUR 8,000/yr (Service Provider), and redistributing the original numerical data is never allowed.
  - Source: [EUMETSAT licensing guide](https://user.eumetsat.int/resources/user-guides/data-registration-and-licensing).
- **ESA heritage and Earth Explorer missions.** ESA describes the data as "freely and openly available to everyone" once you accept the ESA Terms and Conditions (the "free dataset"). Commercial use is marked *verify*: it is not explicitly granted in the T&C we read. Source: [ESA T&C](https://earth.esa.int/eogateway/documents/20142/1564626/Terms-and-Conditions-for-the-use-of-ESA-Data.pdf).

## Summary table

| ID | Mission / product | Res (m) | Coverage | Access | Login | Commercial |
|---|---|---|---|---|---|---|
| EU-01 | Sentinel-1C/1D C-SAR (1A ended Jun 2026) | 5x20 (10 px) | global | CDSE, PC, AWS, ASF | free reg / none on mirrors | yes |
| EU-02 | Sentinel-2A/2B/2C MSI | 10/20/60 | global | CDSE, PC, AWS, GEE | free reg / none | yes |
| EU-03 | Sentinel-3A/3B/3C OLCI/SLSTR (3C launched 15 Sep 2026) | 300-1000 | global | CDSE, EUMETSAT, PC | free reg | yes |
| EU-04 | Sentinel-5P TROPOMI | 5500x3500 | global | CDSE, PC, GES DISC | free reg | yes |
| EU-05 | Sentinel-4 (MTG-S1, Jul 2025) | ~8000 | Europe only | EUMETSAT | free reg | yes |
| EU-06 | Sentinel-5A (Metop-SG-A1, Aug 2025) | ~7500 | global | EUMETSAT, CDSE | free reg | yes |
| EU-07 | Sentinel-6 MF / 6B (Nov 2025) | altimeter | oceans | EUMETSAT, PO.DAAC | free reg | yes |
| EU-08..13 | CO2M, CHIME, LSTM, ROSE-L, CIMR, CRISTAL | 30-60000 | global (planned) | future CDSE | - | yes (expected); **none launched** |
| EU-14 | CLMS Urban Atlas | vector | Europe only | land.copernicus.eu | free | yes |
| EU-15 | CLMS HR Layers (imperviousness, tree cover...) | 10-20 | Europe only | land.copernicus.eu, CDSE | free | yes |
| EU-16 | CLMS Global (burnt area, NDVI, LST) | 300-1000 | global | CDSE | free | yes |
| EU-17 | EGMS ground motion (InSAR) | points / 100 grid | **Europe only** | egms.land.copernicus.eu | free EU Login | yes |
| EU-18 | CEMS Rapid / Risk and Recovery Mapping | vector | global activations | mapping.emergency.copernicus.eu | none | yes |
| EU-19 | CEMS GloFAS / GWIS / EFFIS / GDO | 1000-5000 | global + EU | EWDS, JRC | free ECMWF | yes |
| EU-20 | GHSL built-up / building height | 10-1000 | global | JRC GHSL | none | verify (expected CC BY) |
| EU-21 | CAMS (air quality, solar radiation) | 10-40 km | global | ADS | free ECMWF | yes |
| EU-22 | C3S ERA5 / ERA5-Land | 9-31 km | global | CDS, AWS, PC | free ECMWF | yes |
| EU-23 | Copernicus Marine | 1-25 km | oceans | marine.copernicus.eu | free reg | yes |
| EU-24 | Copernicus DEM GLO-30/90 | 30/90 | global | AWS, PC, CDSE, OpenTopography | none | yes (EEA-10: no) |
| EU-25 | ESA WorldCover 2020/2021 | 10 | global | AWS, PC, Terrascope | none | yes (CC BY 4.0) |
| EU-26 | ESA CCI ECVs | 100-300+ | global | CEDA, CDS | free | verify |
| EU-27 | ERS-1/2 | 25 | global (sparse) | ESA Earth Online | free EO Sign In | verify |
| EU-28 | Envisat | 30-300 | global | ESA Earth Online | free | verify |
| EU-29 | PROBA-V | 100-1000 | global | Terrascope | free | verify |
| EU-30 | Earth Explorers (Biomass, EarthCARE, FLEX...) | varies | global (Biomass excludes N. America/Europe) | ESA Earth Online, MAAP | free | verify |
| EU-31 | ESA TPM / CCM commercial VHR | 0.3-10 | global | proposals | restricted | no |
| EU-32 | Meteosat MSG SEVIRI | 1000-3000 | Europe/Africa | EUMETSAT | free reg | no for L1.5 (paid licence); Core L2 yes |
| EU-33 | MTG-I1 / MTG-S1 | 500-2000 | Europe/Africa | EUMETSAT | free reg | no for FCI L1c; Core yes |
| EU-34 | Metop B/C, Metop-SG-A1 (SG-B1 launches 18 Dec 2026) | 500-12500 | global | EUMETSAT | free reg | yes for Core |
| EU-35 | EUMETSAT SAFs | 1-5 km | mostly Europe/Africa | SAF portals | free | verify |
| EU-36 | DLR EnMAP hyperspectral | 30 | global (tasked) | EnMAP portal, EOC Geoservice | free reg | **yes** (licence PDF) |
| EU-37 | DLR TanDEM-X 90 m DEM | 90 | global | DLR Geoservice | reg | **no** (scientific use only) |
| EU-38 | TerraSAR-X / WorldDEM | 0.25-40 | tasked | Airbus | paid | no |
| EU-39 | ASI PRISMA hyperspectral | 30 / 5 | tasked | prisma.asi.it | reg | verify (treat as no) |
| EU-40 | ASI COSMO-SkyMed | <1-100 | tasked | e-GEOS | paid | no |
| EU-41 | CNES SPOT World Heritage (SPOT 1-5) | 2.5-20 | global archive | REGARDS | free reg | verify (likely no) |
| EU-42 | Pléiades / Pléiades Neo / SPOT 6-7 | 0.3-1.5 | tasked | Airbus | paid | no |
| EU-43 | CNES/NASA SWOT | ~100 | global | PO.DAAC | free Earthdata | yes |
| EU-44 | ICEYE Open Data | 0.25-15 | sample sites | AWS, ICEYE STAC | none | yes (CC BY 4.0, open subset only) |
| EU-45 | UK NovaSAR-1 | 6-30 | tasked | partner hubs | eligible only | no |
| EU-46 | Spain PAZ | 0.25-40 | tasked | Hisdesat | paid | no |

## Top 10 for property intelligence (usable globally, including the US)

1. **Sentinel-2 L2A (EU-02).** 10 m multispectral every ~5 days, with no login needed on Planetary Computer and AWS. Uses: parcel vegetation/NDVI, new construction and demolition, burn scars, neighbourhood-scale storm damage, and change over time since 2015.
2. **Sentinel-1 GRD/SLC/RTC (EU-01).** All-weather SAR from S1C and S1D at a 6-day repeat. Uses: flood extent right after storms, and InSAR subsidence or heave screening for US parcels. EGMS does not cover the US, so the InSAR has to be self-processed or taken from NASA OPERA DISP-S1.
3. **Copernicus DEM GLO-30 (EU-24).** Global 30 m surface model, commercial use OK with attribution. Uses: slope and aspect for rooftop-solar and drainage scoring. It is the commercial-safe substitute for the TanDEM-X 90 m DEM, which is science-only.
4. **ESA WorldCover 10 m (EU-25).** CC BY 4.0 land cover per parcel: tree, built-up, grass, water. Uses: wildfire fuel proximity and tree overhang screening.
5. **CAMS solar radiation and atmosphere (EU-21).** Free worldwide licence. Uses: hourly irradiance for rooftop-solar yield estimates, and PM2.5/NO2 context.
6. **C3S ERA5 / ERA5-Land (EU-22).** Hourly climate data since 1940. Uses: hail, wind, precipitation and freeze-thaw climatology for roof-peril and roof-age models.
7. **GHSL built-up and building height (EU-20).** Global 100 m building height and volume, plus a 10 m built-up layer. Uses: urban density and growth timeline. The CC BY wording still needs confirming on each release page.
8. **Sentinel-3 SLSTR LST and fire (EU-03) / CLMS Global burnt area (EU-16).** Daily 1 km heat and fire data, 300 m burnt-area history. Uses: wildfire-history flags and urban heat islands.
9. **EnMAP hyperspectral (EU-36).** 30 m, with a licence that explicitly allows commercial use. Uses: block-scale roof and surface material spectra where archive scenes exist. It cannot resolve a single roof.
10. **CEMS Rapid Mapping (EU-18) and ICEYE Open Data (EU-44).** Both are free vectors or images under CC BY / Copernicus terms, but coverage is sparse. CEMS gives per-building damage grades for the few US activations. ICEYE gives VHR SAR sample scenes for building and damage model R&D.

Sentinel-5P/5A (air quality) and Metop Core products are also global and commercial-OK, but they matter less for individual parcels.

## What's NOT open (or not usable for a US commercial product)

- **Commercial imagery is paid.** This covers TerraSAR-X / WorldDEM (Airbus), Pléiades / Pléiades Neo / SPOT 6-7 (Airbus), COSMO-SkyMed (e-GEOS), PAZ (Hisdesat), and tasked or archive ICEYE outside the open subset.
- **Restricted to research or institutions:**
  - ESA Third Party Missions and Copernicus Contributing Missions VHR data, project proposals only. This includes the Copernicus VHR Europe mosaics and Copernicus DEM EEA-10.
  - NovaSAR-1, partner data shares only.
  - TanDEM-X 90 m DEM, under a licence "for Scientific Use".
- **EUMETSAT "Recommended" data** (MSG SEVIRI L1.5, MTG FCI L1c, IRS L1, global ASCAT L1, some Metop-SG L1). Commercial use of real-time data needs a EUR 4,000-8,000/yr licence, and the raw numerical data can never be redistributed. These geostationary satellites do not view the US anyway.
- **Unconfirmed, so treat as not open until verified:**
  - ASI PRISMA: the licence is only visible after login, and public framing is scientific/institutional.
  - CNES SPOT World Heritage: historically non-commercial, and the current terms were not retrieved.
- **Europe-only, so no US use:** EGMS (InSAR ground motion), Urban Atlas, CLMS High Resolution Layers (imperviousness, tree cover), Sentinel-4.
- **ESA Biomass** does not acquire over North America or Europe because of radar frequency restrictions.

## Status notes as of 2026-11-24

- **Sentinel-1:** 1A ended in June 2026. 1C (launched Dec 2024) and 1D (launched 4 Nov 2025) now carry the mission.
- **Sentinel-2:** 2C (Sep 2024) and 2B lead the mission, with 2A in extended use.
- **Sentinel-3:** 3C launched 15 Sep 2026.
- **Sentinel-6:** 6B launched 17 Nov 2025.
- **Sentinel-4 and 5:** Sentinel-4 launched 1 Jul 2025 and Sentinel-5A on 13 Aug 2025.
- **Metop:** Metop-SG-A1 launched 13 Aug 2025. Metop-SG-B1 is scheduled for 18 Dec 2026.
- **Earth Explorers:** FLEX launched 15 Sep 2026 and Biomass on 29 Apr 2025.
- **Copernicus Expansion missions:** none has launched. ESA news shows ROSE-L ground testing in Apr 2026 and the CO2M instrument vacuum test in Jun 2026. The target years in the JSON (about 2027-2029) come from earlier ESA plans and were not re-verified.

## Gaps / to verify

- land.copernicus.eu (CLMS) timed out from this machine. The CLMS licence here relies on the Copernicus legal notice, not on a live CLMS page.
- The Copernicus DEM licence annex PDF (spacedata.copernicus.eu) timed out. The commercial-use wording comes from the AWS registry and CDSE pages.
- Still to confirm: the GHSL CC BY line, the ESA CCI licence for each variable, the PRISMA end-user licence, and the SPOT World Heritage licence.

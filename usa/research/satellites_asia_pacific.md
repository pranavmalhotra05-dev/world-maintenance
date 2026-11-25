# Asia-Pacific government EO satellites - open + commercial-use check (lane SAT-3)

As of 2026-11-24. Data: `satellites_asia_pacific.json` (37 records). The rule applied: data counts only if it is permanently free and commercial use is allowed. Records marked **verify** are free, but we have not confirmed the licence wording for a foreign commercial user.

**Counts:** 37 records. 13 commercial_ok = **yes**, 11 = **verify**, 13 = **no**. 14 were tested live, 8 partly, 15 not tested.

**Method note:** JAXA EORC (`eorc.jaxa.jp`, which hosts the AW3D30, FNF mosaic and Himawari P-Tree pages) blocked both scripts and the built-in browser with a WAF "requested URL was rejected" page. The JAXA licence was confirmed instead on `earth.jaxa.jp` and on the G-Portal terms page.

## Key licence findings

- **JAXA research data** (AW3D30, PALSAR mosaics/FNF, GSMaP, etc.): free to use, modify and redistribute. Commercial use is allowed with no fee, but JAXA must be **notified in advance**, and JAXA must be credited. Source: https://earth.jaxa.jp/en/data/policy/
- **JAXA G-Portal** (GCOM-C, GCOM-W, GPM, ALOS-2 ScanSAR, AMSR3): anyone may use it free of charge, commercial purposes included. Conditions: peaceful use only, cite the source, and notify JAXA before commercial use. Source: https://www.gportal.jaxa.jp/gpr/index/eula?lang=en
- **Himawari (JMA) and GK-2A (KMA) mirrored by NOAA on AWS**: open under NOAA NODD terms. Attribution is requested and endorsement may not be implied. The JAXA P-Tree Himawari route is treated as **not commercial**, so use the NOAA copy.
- **India (Bhoonidhi/NRSC)**: under Indian Space Policy 2023, data at 5 m and coarser is free and open for all users, foreigners included, after registration and acceptance of the EULA. Data finer than 5 m (Cartosat, EOS-04 fine modes) is priced through NSIL. We did not read the EULA text, so India is marked **verify**.
- **China CMA Fengyun**: CMA Order No. 49 took effect on 2026-11-01. Public meteorological data used for industry or commercial purposes is **paid**, and passing data to third parties needs the holder's consent. Marked **no**.
- **Korea NIER GEMS**: data owned by the Environmental Satellite Center (ESC) may be used freely under KOGL Type 1, which only requires naming the source, so commercial use is OK. It covers Asia only.
- **Geoscience Australia / DEA**: CC BY 4.0. Australia only.
- **NISAR**: NASA full-and-open policy, so commercial use is OK. Validated L1/L2 products are in NASA CMR (41 collections, validated products dated from 2025-09-27). ISRO's S-band daily collections have been on Bhoonidhi since 2026-07-08.

## Summary table

| Mission | Operator | Res (m) | Coverage | US? | Access | commercial_ok | tested |
|---|---|---|---|---|---|---|---|
| NISAR L/S-SAR | NASA/ISRO | 3-20 | global | yes | ASF DAAC, Earthdata Login | yes | yes |
| ALOS-2 PALSAR-2 ScanSAR CARD4L | JAXA | 25 | global | verify | AWS s3://jaxaalos2, G-Portal | yes (notify) | yes |
| PALSAR mosaics / FNF | JAXA EORC | 25 | global | yes | EORC, GEE | yes (notify) | partial |
| AW3D30 DSM | JAXA EORC | 30 | global | yes | EORC, OpenTopography | yes (notify) | partial |
| ALOS-4 PALSAR-3 | JAXA | 3-25 | global | yes | priced | verify | no |
| GCOM-C SGLI | JAXA | 250 | global | yes | G-Portal | yes (notify) | yes |
| GCOM-W AMSR2 | JAXA | 5-50 km | global | yes | G-Portal, NASA (CC BY) | yes | yes |
| GPM / GSMaP | JAXA+NASA | ~10 km | 60N-60S | yes | G-Portal | yes | partial |
| GOSAT | JAXA/NIES | ~10 km | global | yes | NIES GDAS | verify | no |
| GOSAT-GW | JAXA/NIES | 1-50 km | global | yes | G-Portal/NIES | verify | no |
| Himawari-8/9 (NOAA AWS) | JMA | 500-2000 | Asia-Pacific | Guam/HI edge | s3://noaa-himawari9 | yes | yes |
| Himawari via P-Tree | JAXA | 500-5000 | Asia-Pacific | no | P-Tree | no | no |
| Resourcesat-2/2A | ISRO | 5.8-56 | India + some global | sparse | Bhoonidhi | verify | yes |
| EOS-04 (RISAT-1A) | ISRO | 18-50 open | India + tasked | taskable | Bhoonidhi | verify | yes |
| EOS-06 Oceansat-3 | ISRO | 360+ | global ocean | coasts | Bhoonidhi | verify | partial |
| Cartosat-1/2/3 | ISRO/NSIL | 0.28-2.5 | tasked | - | priced | no | yes |
| Bhuvan CartoDEM/LULC | NRSC | 30 | India | no | Bhuvan | verify | partial |
| Fengyun-3 | CMA NSMC | 250-1000 | global | yes | NSMC portal | no | yes |
| Fengyun-4 | CMA NSMC | 500-4000 | Asia | no | NSMC portal | no | yes |
| Gaofen | CNSA/CRESDA | 0.5-16 | tasked | - | CRESDA | no | no |
| HJ-2A/B | MEE/CRESDA | 16-48 | global | - | CRESDA | no | no |
| CBERS-4/4A (INPE) | INPE/CAST | 2-64 | mostly S. America | little | AWS s3://brazil-eosats | yes (CC BY-SA 3.0) | partial |
| GK-2A AMI (NOAA AWS) | KMA | 500-2000 | Asia-Pacific | no | s3://noaa-gk2a-pds | yes | yes |
| GK-2A NMSC L2 | KMA | 2000 | Asia-Pacific | no | NMSC portal | verify | no |
| GK-2B GEMS | NIER ESC | 3.5x8 km | Asia | no | NESC, Open-API | yes (KOGL-1) | yes |
| KOMPSAT / CAS500 | KARI/SIIS | 0.4-1 | tasked | - | priced | no | partial |
| FORMOSAT-5 | TASA | 2-4 | tasked | - | TASA | no | yes |
| FORMOSAT-7/COSMIC-2 | TASA+NOAA/UCAR | profiles | +/-45 deg | yes | UCAR CDAAC | verify | no |
| DEA (Landsat/S2 ARD + derived) | Geoscience Australia | 10-30 | Australia | no | s3://dea-public-data | yes (CC BY 4.0) | yes |
| GA elevation/LiDAR (ELVIS) | GA/ICSM | 1-30 | Australia | no | ELVIS | yes (CC BY 4.0) | partial |
| CSIRO / TERN | CSIRO | varies | Australia | no | TERN, AWS | verify | no |
| THEOS-2 | GISTDA | 0.5-15 | tasked | - | priced | no | no |
| VNREDSat-1 | VNSC | 2.5-10 | regional | - | agreement | no | no |
| LAPAN-A2/A3 | BRIN | ~15 | regional | - | agreement | no | no |
| TeLEOS / NeuSAR | ST Eng./DSTA | ~1 | equatorial | - | commercial | no | no |
| MethaneSAT | EDF/NZ | 100x400 | targeted | yes | EDF/GEE | verify | no |
| KhalifaSat / MBZ-SAT / Saudi | MBRSC / SSA | ~0.7-1 | tasked | - | agreement | no | no |

## Top items usable over the US for property intelligence

1. **NISAR (NASA/ISRO)**: the standout of this lane. It is free and has no restrictions. Its L-band InSAR (GUNW interferograms, GOFF offsets) can track ground subsidence and heave near buildings, sinkholes and landslides. It also gives cloud-proof flood and damage mapping, repeats every 12 days, and covers all US land. It needs an Earthdata Login.
2. **ALOS-2 PALSAR-2 ScanSAR (JAXA)**: 25 m L-band backscatter, free on AWS without a login. Good for broad change, flood and land-clearing signals. Confirm it covers the US (the AWS prefixes seen were Africa/, Asia/ and 2025/). Notify JAXA before commercial use.
3. **AW3D30 DSM (JAXA)**: a global 30 m surface model, licensed for commercial use once JAXA has been notified. In the US it is mainly a fallback or cross-check, because USGS 3DEP is much finer.
4. **GSMaP / GPM rainfall (JAXA)**: hourly global rain that can trigger storm and flood lead events per ZIP or county.
5. **GCOM-C SGLI LST/NDVI (JAXA)**: 250 m land-surface temperature and vegetation for neighbourhood heat-island and greenness scoring.
6. **PALSAR FNF / mosaics (JAXA)**: yearly tree-cover change around parcels and wildfire-fuel context.

Before commercial launch, one admin step is needed: send JAXA a single notice of commercial use, to `earth*ml.jaxa.jp` (replace `*` with `@`) for research data and to the G-Portal Support Desk for G-Portal data.

Himawari, GK-2A, GEMS, Fengyun-4, DEA and GA are open but do **not** cover CONUS. Himawari reaches only Guam and the edge of Hawaii.

## What is NOT open (exclude)

- **Cartosat-1/2/3 (sub-metre), EOS-04 fine modes, ISRO KOMPSAT resale**: priced through NSIL. Free only for Indian Government Entities.
- **Fengyun-3/4 (CMA)**: free registration is for research. Commercial use is paid under CMA Order 49 (from 2026-11-01), and sharing with third parties needs consent.
- **Gaofen, HJ-2, CRESDA-side CBERS**: no open international licence.
- **KOMPSAT / CAS500**: commercial only (SI Imaging Services).
- **FORMOSAT-5, THEOS-2, VNREDSat, LAPAN, TeLEOS/NeuSAR, KhalifaSat/MBZ-SAT, Saudi EO**: no public open-data programme.
- **ALOS-4 PALSAR-3 standard products, ALOS 2.5 m AW3D**: priced (PASCO/RESTEC/NTT DATA).
- **Himawari via JAXA P-Tree**: treated as research/non-profit only. Use the NOAA AWS copy instead.

## Open follow-ups

- Read the Bhoonidhi EULA, shown at registration, to confirm foreign commercial use of open Indian data at 5 m and coarser.
- Retry the JAXA EORC pages (AW3D30, FNF, P-Tree) from another network to quote their dataset-specific terms.
- Check the PALSAR-2 ScanSAR AWS STAC (`palsar2/L2.2/For_STAC/`) for US tiles.
- Check the NIES GOSAT GDAS terms (the host did not resolve) and the KMA NMSC data policy.

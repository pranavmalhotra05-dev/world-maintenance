# Satellites of all countries: green orbit sources

Rule: GREEN only (public domain, CC0, CC BY, CC BY-SA, ODbL or an explicit commercial-use statement, free forever). No Space-Track / 18 SDS / USSF catalogue (TLE/GP) data, and no mirrors of it.

## Used

**IGS multi-GNSS merged broadcast ephemerides** (RINEX 3 nav) from the BKG GNSS Data Center mirror, no login:
`https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{yyyy}/{doy}/BRDC00WRD_S_{yyyy}{doy}0000_01D_MN.rnx.gz` (today, updated every 15 min) and `BRDC00IGS_R_...` (final daily merge).

- What it is: the orbit each satellite broadcasts in its own navigation message, recorded by the independent IGS station network. It is not a catalogue product.
- Licence: [IGS Data and Product Disclaimer and Terms of Use, 5 Aug 2020](https://igs.org/wp-content/uploads/2020/09/IGS-Data-and-Product-Disclaimer-and-Terms-of-Use-200805.pdf). It says data and products are provided openly for "scientific, educational, and commercial users", for use "without restriction" and "free of cost or obligation". Attribution is required.

| System | Country | Operator | Sats | Altitude (km) |
|---|---|---|---|---|
| GPS | USA | US Space Force | 32 | 19,635-20,755 |
| GLONASS | Russia | Roscosmos | 26 | 19,076-19,208 |
| Galileo | EU | EUSPA / ESA | 30 | 23,209-23,253 (E14, E18 eccentric: 16,846-26,352) |
| BeiDou | China | CSNO | 38 | MEO ~21,500-21,600; GEO/IGSO ~35,786 |
| QZSS | Japan | Cabinet Office | 6 | QZO 32,605-38,976; GEO ~35,786 |
| NavIC | India | ISRO | 5 | GEO/IGSO ~35,786; I11 (NVS-02, stuck in transfer orbit) 9,470-27,982 |
| WAAS, EGNOS, MSAS, GAGAN, BDSBAS, KASS, SouthPAN | USA, EU, Japan, India, China, South Korea, Australia/NZ | FAA, EUSPA, JCAB, AAI/ISRO, CSNO, KASA, GA/LINZ | 15 | ~35,786 (GEO) |

Method: the standard ICD Keplerian algorithm for GPS, Galileo, BeiDou (including the GEO -5 degree rotation), QZSS and NavIC. GLONASS uses RK4 integration of its PZ-90 state vector (J2, Earth rotation, broadcast luni-solar acceleration). Each sample uses the record with the nearest epoch.

Extrapolation check: one record propagated 24 h and compared with the record broadcast 24 h later gave errors of GPS 0.3 km, Galileo 0.9 km, BeiDou 0.2-1.5 km, NavIC 2 km, QZSS 8 km and GLONASS 11 km. SBAS state vectors drift by 300-9,700 km over 24 h, so each SBAS sample is instead taken from the same point in the orbit one or more sidereal days earlier, from real records within 15 minutes. SBAS PRN 139 was skipped because it cannot be attributed to an operator.

## Excluded / not used

| Source | Verdict | Why |
|---|---|---|
| Space-Track.org GP/TLE | EXCLUDED | Agreement forbids transfer without approval (10 USC 2274) |
| CelesTrak, SatNOGS DB, N2YO, Heavens-Above | EXCLUDED | Redistribute Space-Track data, even if labelled CC BY-SA |
| AMSAT Keplerian elements | EXCLUDED | Derived from Space-Track TLEs |
| ILRS CPF predictions (EDC DGFI-TUM, CDDIS) | EXCLUDED | No explicit licence or commercial-use statement found on the EDC or ILRS pages; some predictions may be TLE-seeded. Best future add for LEO science satellites if a licence appears |
| IGS/MGEX precise SP3 orbits | GREEN, not used | Same IGS terms. Delivered days late; broadcast orbits give a current window |
| ESA Copernicus Sentinel POD orbits | NOT USED | Needs a Copernicus Data Space account; files describe past orbits only |
| EUMETSAT orbit predictions | NOT USED | Licence not checked within the time cap |
| NASA ISS OEM | GREEN, not added | ISS is already drawn by sky.js from NASA SSC |

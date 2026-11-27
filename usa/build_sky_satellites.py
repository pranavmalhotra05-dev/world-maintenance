"""Build web/app/data/sky/satellites.json: navigation satellites of every GNSS/SBAS country.

Source (GREEN): IGS multi-GNSS merged broadcast ephemerides (RINEX 3 mixed nav), BKG GNSS Data
Center mirror, no login:
  https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{yyyy}/{doy}/BRDC00WRD_S_{yyyy}{doy}0000_01D_MN.rnx.gz (today, 15-min updates)
  https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{yyyy}/{doy}/BRDC00IGS_R_{yyyy}{doy}0000_01D_MN.rnx.gz (yesterday, final merge)
These are the navigation messages each operator broadcasts (GPS, GLONASS, Galileo, BeiDou, QZSS,
NavIC, SBAS), received by IGS stations. Nothing here comes from Space-Track / 18 SDS catalogues.
Licence: IGS Data and Product Disclaimer and Terms of Use (2020-08-05): products are "provided
openly for the benefit of all scientific, educational, and commercial users", "for use without
restriction ... free of cost or obligation"; attribution required.

Positions: ICD Keplerian broadcast algorithm (GPS/Galileo/BeiDou incl. GEO rotation/QZSS/NavIC);
GLONASS and SBAS by RK4 integration of the PZ-90/WGS84 state vectors (J2 + broadcast accelerations).
For every sample the record with the nearest epoch is used. Window: today 00:00 UTC + 24 h,
2-minute steps. ECEF -> WGS84 geodetic.

Usage: .venv\\Scripts\\python -u -W ignore usa\\build_sky_satellites.py
"""
import datetime as dt
import gzip
import json
import math
import os
import re
import sys
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "usa", "_cache", "sats")
OUT = os.path.join(ROOT, "web", "app", "data", "sky", "satellites.json")
BASE = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{y}/{d:03d}/"
UA = {"User-Agent": "roof-intel-builder/1.0"}
STEP_S = 120
GPS0 = dt.datetime(1980, 1, 6)
LEAP = 18  # GPST - UTC (s)

SYS = {
    "G": ("GPS", "USA", "US Space Force (GPS)"),
    "R": ("GLONASS", "Russia", "Roscosmos (GLONASS)"),
    "E": ("Galileo", "European Union", "EU / EUSPA / ESA (Galileo)"),
    "C": ("BeiDou", "China", "China Satellite Navigation Office (BeiDou)"),
    "J": ("QZSS", "Japan", "Cabinet Office of Japan (QZSS)"),
    "I": ("NavIC", "India", "ISRO (NavIC/IRNSS)"),
}
# SBAS PRN = 100 + number in RINEX (S20 -> PRN 120)
SBAS = {
    131: ("WAAS", "USA", "FAA"), 133: ("WAAS", "USA", "FAA"), 135: ("WAAS", "USA", "FAA"),
    138: ("WAAS", "USA", "FAA"),
    121: ("EGNOS", "European Union", "EUSPA / ESA"), 123: ("EGNOS", "European Union", "EUSPA / ESA"),
    126: ("EGNOS", "European Union", "EUSPA / ESA"), 136: ("EGNOS", "European Union", "EUSPA / ESA"),
    129: ("MSAS", "Japan", "JCAB / Cabinet Office"), 137: ("MSAS", "Japan", "JCAB / Cabinet Office"),
    127: ("GAGAN", "India", "AAI / ISRO"), 128: ("GAGAN", "India", "AAI / ISRO"),
    132: ("GAGAN", "India", "AAI / ISRO"),
    125: ("SDCM", "Russia", "Roscosmos"), 140: ("SDCM", "Russia", "Roscosmos"),
    141: ("SDCM", "Russia", "Roscosmos"),
    130: ("BDSBAS", "China", "CSNO"), 143: ("BDSBAS", "China", "CSNO"), 144: ("BDSBAS", "China", "CSNO"),
    134: ("KASS", "South Korea", "KASA / MOLIT"),
    122: ("SouthPAN", "Australia / New Zealand", "Geoscience Australia / LINZ"),
    148: ("ASAL", "Algeria", "Algerian Space Agency"),
    120: ("EGNOS", "European Union", "EUSPA / ESA"), 124: ("EGNOS", "European Union", "EUSPA / ESA"),
}

LIC = "IGS Data and Product Terms of Use: open for scientific, educational and commercial use without restriction; attribution required"
LIC_URL = "https://igs.org/wp-content/uploads/2020/09/IGS-Data-and-Product-Disclaimer-and-Terms-of-Use-200805.pdf"
ATTR = "Broadcast ephemerides: International GNSS Service (IGS) and BKG GNSS Data Center; navigation messages of GPS, GLONASS, Galileo, BeiDou, QZSS, NavIC and SBAS operators"


def fetch(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as f:
        f.write(r.read())
    os.replace(path + ".part", path)
    return path


def num(s):
    s = s.strip().replace("D", "E").replace("d", "e")
    return float(s) if s else 0.0


def parse_nav(path):
    """Return list of (sat, epoch_datetime_in_system_time, [values])."""
    recs = []
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            if "END OF HEADER" in line:
                break
        cur = None
        start = re.compile(r"^[GRECJIS]\d\d \d{4} ")
        for line in f:
            line = line.rstrip("\n")
            if start.match(line):
                if cur:
                    recs.append(cur)
                y, mo, d, h, mi, s = (int(line[4:8]), int(line[9:11]), int(line[12:14]),
                                      int(line[15:17]), int(line[18:20]), int(line[21:23]))
                vals = [num(line[23 + 19 * k:42 + 19 * k]) for k in range(3)]
                cur = (line[:3], dt.datetime(y, mo, d, h, mi, s), vals)
            elif cur and line.startswith("    "):
                for k in range(4):
                    seg = line[4 + 19 * k:23 + 19 * k]
                    if seg.strip():
                        cur[2].append(num(seg))
                    else:
                        cur[2].append(0.0)
        if cur:
            recs.append(cur)
    return recs


def to_utc_s(t_sys, sysc):
    """System-time datetime -> UTC seconds since GPS0."""
    s = (t_sys - GPS0).total_seconds()
    if sysc in "GEJI":
        return s - LEAP
    if sysc == "C":
        return s - (LEAP - 14)
    return s  # R, S: UTC


# ---------- Keplerian (GPS-like) ----------
def kepler(rec_vals, toc_sys, sysc, prn, t_utc):
    v = rec_vals
    crs, dn, m0 = v[4], v[5], v[6]
    cuc, e, cus, sqa = v[7], v[8], v[9], v[10]
    toe, cic, om0, cis = v[11], v[12], v[13], v[14]
    i0, crc, w, omd = v[15], v[16], v[17], v[18]
    idot = v[19]
    if sysc in "EC":
        mu = 3.986004418e14
    else:
        mu = 3.986005e14
    we = 7.292115e-5 if sysc == "C" else 7.2921151467e-5
    # absolute toe (UTC seconds) from toc: wrap the seconds-of-week difference
    toc_abs = to_utc_s(toc_sys, sysc)
    sys_s = (toc_sys - GPS0).total_seconds()
    if sysc == "C":
        sow_toc = (toc_sys - dt.datetime(2006, 1, 1)).total_seconds() % 604800
    else:
        sow_toc = sys_s % 604800
    d = toe - sow_toc
    d = (d + 302400) % 604800 - 302400
    toe_abs = toc_abs + d
    tk = t_utc - toe_abs
    a = sqa * sqa
    n = math.sqrt(mu / a ** 3) + dn
    M = m0 + n * tk
    E = M.copy()
    for _ in range(12):
        E = M + e * np.sin(E)
    nu = np.arctan2(math.sqrt(1 - e * e) * np.sin(E), np.cos(E) - e)
    phi = nu + w
    s2, c2 = np.sin(2 * phi), np.cos(2 * phi)
    u = phi + cus * s2 + cuc * c2
    r = a * (1 - e * np.cos(E)) + crs * s2 + crc * c2
    inc = i0 + idot * tk + cis * s2 + cic * c2
    xp, yp = r * np.cos(u), r * np.sin(u)
    geo = sysc == "C" and i0 < 0.35  # BeiDou GEO
    if geo:
        Om = om0 + omd * tk - we * toe
        X = xp * np.cos(Om) - yp * np.cos(inc) * np.sin(Om)
        Y = xp * np.sin(Om) + yp * np.cos(inc) * np.cos(Om)
        Z = yp * np.sin(inc)
        f = math.radians(-5.0)
        cf, sf = math.cos(f), math.sin(f)
        Y2 = Y * cf + Z * sf
        Z2 = -Y * sf + Z * cf
        z = we * tk
        cz, sz = np.cos(z), np.sin(z)
        return X * cz + Y2 * sz, -X * sz + Y2 * cz, Z2
    Om = om0 + (omd - we) * tk - we * toe
    X = xp * np.cos(Om) - yp * np.cos(inc) * np.sin(Om)
    Y = xp * np.sin(Om) + yp * np.cos(inc) * np.cos(Om)
    Z = yp * np.sin(inc)
    return X, Y, Z


# ---------- GLONASS / SBAS state-vector integration ----------
MU_G, AE, J2, WE_G = 3.9860044e14, 6378136.0, 1.0826257e-3, 7.292115e-5


def deriv(s, acc):
    x, y, z, vx, vy, vz = s
    r2 = x * x + y * y + z * z
    r = math.sqrt(r2)
    m = MU_G / r2 / r
    k = 1.5 * J2 * MU_G * AE * AE / (r2 * r2 * r)
    zz = 5 * z * z / r2
    ax = -m * x - k * x * (1 - zz) + WE_G * WE_G * x + 2 * WE_G * vy + acc[0]
    ay = -m * y - k * y * (1 - zz) + WE_G * WE_G * y - 2 * WE_G * vx + acc[1]
    az = -m * z - k * z * (3 - zz) + acc[2]
    return np.array([vx, vy, vz, ax, ay, az])


def integrate(state, acc, t0, times, h=60.0):
    """state at t0; return positions at sorted times (may be before/after t0)."""
    out = {}
    for sign in (1, -1):
        ts = sorted([t for t in times if (t - t0) * sign >= 0], key=lambda t: abs(t - t0))
        s, t = state.copy(), t0
        for tt in ts:
            while abs(tt - t) > 1e-6:
                st = sign * min(h, abs(tt - t))
                k1 = deriv(s, acc)
                k2 = deriv(s + st / 2 * k1, acc)
                k3 = deriv(s + st / 2 * k2, acc)
                k4 = deriv(s + st * k3, acc)
                s = s + st / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
                t += st
            out[tt] = s[:3].copy()
    return [out[t] for t in times]


def ecef_to_geo(x, y, z):
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    b = a * (1 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = np.hypot(x, y)
    th = np.arctan2(z * a, p * b)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, p - e2 * a * np.cos(th) ** 3)
    lon = np.arctan2(y, x)
    N = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - N
    return np.degrees(lon), np.degrees(lat), h


def main():
    os.makedirs(CACHE, exist_ok=True)
    now = dt.datetime.utcnow()
    today = dt.datetime(now.year, now.month, now.day)
    yday = today - dt.timedelta(days=1)
    files = []
    cands = []
    for back, name in ((0, "BRDC00WRD_S_{y}{d:03d}0000_01D_MN.rnx.gz"),
                       (1, "BRDC00WRD_S_{y}{d:03d}0000_01D_MN.rnx.gz"),
                       (1, "BRDC00IGS_R_{y}{d:03d}0000_01D_MN.rnx.gz"),
                       (2, "BRDC00IGS_R_{y}{d:03d}0000_01D_MN.rnx.gz")):
        day = today - dt.timedelta(days=back)
        cands.append((day, name))
    for day, name in cands:
        y, d = day.year, day.timetuple().tm_yday
        fn = name.format(y=y, d=d)
        url = BASE.format(y=y, d=d) + fn
        p = os.path.join(CACHE, fn)
        if "WRD_S" in fn and os.path.exists(p):
            mtime = dt.datetime.utcfromtimestamp(os.path.getmtime(p))
            day_end = day + dt.timedelta(days=1, hours=1)
            if mtime < day_end and (now - mtime).total_seconds() > 1800:
                os.remove(p)  # file still growing when cached: refresh
        try:
            fetch(url, p)
            files.append((fn, url, p))
            print("ok", fn, os.path.getsize(p))
        except Exception as ex:
            print("skip", fn, ex)
    if not files:
        sys.exit("no broadcast ephemeris available")

    recs = {}
    for fn, url, p in files:
        for sat, toc, vals in parse_nav(p):
            recs.setdefault(sat, []).append((toc, vals))
    t0 = (today - GPS0).total_seconds()  # UTC seconds axis since GPS0 (UTC-labelled)
    times = np.arange(t0, t0 + 86400 + 1, STEP_S, dtype=float)
    ms0 = int((today - dt.datetime(1970, 1, 1)).total_seconds() * 1000)
    epoch_ms = [ms0 + int(round((t - t0) * 1000)) for t in times]

    sats, bad = [], []
    for sat in sorted(recs):
        sysc, prn = sat[0], int(sat[1:])
        lst = recs[sat]
        # dedupe by epoch; health filter for Keplerian systems
        seen = {}
        for toc, vals in lst:
            seen[toc] = vals
        lst = sorted(seen.items())
        if sysc in "GEJIC":
            lst = [(t, v) for t, v in lst if len(v) >= 20 and v[10] > 1000]
            if sysc in "GJ":
                good = [(t, v) for t, v in lst if len(v) > 24 and v[24] == 0]
                lst = good or lst
        else:
            lst = [(t, v) for t, v in lst if len(v) >= 15 and abs(v[3]) > 1000]
        if not lst:
            continue
        ep = np.array([to_utc_s(t, sysc) for t, _ in lst])
        tq = times.copy()
        if sysc == "S":
            # SBAS GEO state vectors cannot be extrapolated (km/h drift); GEO motion in ECEF
            # repeats each sidereal day, so beyond the data use the same phase k days earlier.
            SID = 86164.0905
            for j in range(len(tq)):
                for kk in range(0, 4):
                    tt = times[j] - kk * SID
                    if np.abs(ep - tt).min() <= 900:
                        tq[j] = tt
                        break
        idx = np.abs(tq[:, None] - ep[None, :]).argmin(axis=1)
        X = np.zeros(len(times)); Y = np.zeros(len(times)); Z = np.zeros(len(times))
        for k in np.unique(idx):
            m = idx == k
            toc, v = lst[k]
            if sysc in "GEJIC":
                x, y, z = kepler(v, toc, sysc, prn, tq[m])
            else:
                st = np.array([v[3], v[7], v[11], v[4], v[8], v[12]]) * 1000.0
                acc = np.array([v[5], v[9], v[13]]) * 1000.0
                pos = integrate(st, acc, ep[k], list(tq[m]))
                x, y, z = (np.array([p[i] for p in pos]) for i in range(3))
            X[m], Y[m], Z[m] = x, y, z
        lon, lat, h = ecef_to_geo(X, Y, Z)
        if sysc == "S":
            if 100 + prn not in SBAS:
                continue  # PRN not attributable to an operator: skip
            system, country, operator = SBAS[100 + prn]
            name = f"{system} PRN {100 + prn}"
        else:
            system, country, operator = SYS[sysc]
            name = f"{system} {sat}"
        if not np.all(np.isfinite(h)) or h.min() < 1e6 or h.max() > 45e6:
            bad.append((sat, float(np.nanmin(h)), float(np.nanmax(h))))
            continue
        pts = [[round(float(a), 3), round(float(b), 3), int(round(float(c), -2)), e]
               for a, b, c, e in zip(lon, lat, h, epoch_ms)]
        sats.append({"id": sat, "name": name, "country": country, "operator": operator,
                     "system": system, "source": "IGS broadcast ephemeris (BKG mirror)",
                     "licence": "IGS Terms of Use (open, commercial use allowed, attribution)",
                     "altKm": [round(float(h.min()) / 1000), round(float(h.max()) / 1000)],
                     "pts": pts})
    out = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "window": {"start": today.strftime("%Y-%m-%dT%H:%MZ"),
                   "end": (today + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ"),
                   "stepSeconds": STEP_S},
        "sources": [{"name": "IGS multi-GNSS merged broadcast ephemerides (RINEX 3), BKG GNSS Data Center",
                     "files": [u for _, u, _ in files], "licence": LIC, "licenceUrl": LIC_URL,
                     "attribution": ATTR}],
        "method": "ICD Keplerian broadcast orbits (GPS, Galileo, BeiDou incl. GEO, QZSS, NavIC); RK4 of GLONASS/SBAS state vectors; nearest ephemeris per sample; samples after the last broadcast epoch are short extrapolations",
        "fields": {"pts": ["lon_deg", "lat_deg", "alt_m (WGS84 ellipsoid)", "epoch_ms (UTC)"],
                   "altKm": "[min, max] altitude over window"},
        "satellites": sats,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("wrote", OUT, os.path.getsize(OUT), "bytes,", len(sats), "satellites; rejected", bad)
    summ = {}
    for s in sats:
        k = (s["system"], s["country"])
        a = summ.setdefault(k, [0, 1e9, 0])
        a[0] += 1; a[1] = min(a[1], s["altKm"][0]); a[2] = max(a[2], s["altKm"][1])
    for k, a in sorted(summ.items()):
        print(f"  {k[0]:9s} {k[1]:24s} n={a[0]:3d} alt {a[1]}-{a[2]} km")


if __name__ == "__main__":
    main()

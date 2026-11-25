"""Orientation + facade sun per house (run after house3d.py / osint.py / derive.py).

facing      compass direction the front of the house faces (toward the nearest street centreline)
roof_aspect area-weighted share of roof surface facing N / E / S / W / flat, from the LiDAR-fitted roof mesh
facade_sun  hours/year of direct sun on the front and back walls (2 m up), morning vs afternoon,
            ray-traced across the LiDAR surface so trees and neighbours cast shade
sky_block   block-level sun facts: noon sun height at solstices/equinoxes, sunrise/sunset azimuths
Writes houses.json (adds fields) and sky.json.
"""
import base64
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "mpls" / "data"
meta = json.load(open(OUT / "meta.json")); LON, LAT = meta["origin"]; HALF = meta["half_m"]
houses = json.load(open(OUT / "houses.json")); layers = json.load(open(OUT / "layers.json"))
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
compass = lambda az: COMPASS[int(((az % 360) + 22.5) // 45) % 8]

# ---- surface model from every laser point (0.5 m, highest return per cell)
parts = [np.frombuffer(base64.b64decode((OUT / f"points_{i}.txt").read_text()),
                       dtype=[("x", "<i2"), ("y", "<i2"), ("z", "<u2"), ("k", "u1"), ("h", "u1"), ("r", "i1"), ("i", "u1")]) for i in range(meta["point_parts"])]
p = np.concatenate(parts); u = meta.get("point_unit_m", 0.02)
px, py, pz = p["x"] * u, p["y"] * u, p["z"] / 100.0
R = 0.5; n = int(2 * HALF / R) + 1
ci = np.clip(((px + HALF) / R).astype(int), 0, n - 1); cj = np.clip(((py + HALF) / R).astype(int), 0, n - 1)
DSM = np.full(n * n, -np.inf); np.maximum.at(DSM, cj * n + ci, pz); DSM = DSM.reshape(n, n)
_, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DSM), return_indices=True); DSM = DSM[iy, ix]
g = p["k"] == 0
DTM = np.full(n * n, np.inf); np.minimum.at(DTM, (cj * n + ci)[g], pz[g]); DTM = DTM.reshape(n, n)
_, (iy, ix) = ndimage.distance_transform_edt(~np.isfinite(DTM), return_indices=True); DTM = ndimage.median_filter(DTM[iy, ix], 5)
cell = lambda x, y: (min(n - 1, max(0, int((y + HALF) / R))), min(n - 1, max(0, int((x + HALF) / R))))

# ---- a year of hourly sun positions
times = pd.date_range("2025-01-01 00:30", "2025-12-31 23:30", freq="1h", tz="America/Chicago")
loc = pvlib.location.Location(LAT, LON, tz="America/Chicago", altitude=250)
sp = loc.get_solarposition(times)
up = (sp.apparent_elevation > 2).values
az = np.radians(sp.azimuth.values[up]); el = np.radians(sp.apparent_elevation.values[up])
noon_side = (sp.azimuth.values[up] < 180)  # before solar noon the sun is in the east half
dx, dy, tan_el = np.sin(az), np.cos(az), np.tan(el)


def sunlit(x0, y0, z0, k):
    for s in np.arange(1.0, 140.0, 1.0):
        x1, y1 = x0 + dx[k] * s, y0 + dy[k] * s
        if abs(x1) >= HALF or abs(y1) >= HALF:
            return True
        r, c = cell(x1, y1)
        if DSM[r, c] > z0 + tan_el[k] * s + 0.25:
            return False
    return True


streets = [np.array(s["path"]) for s in layers["pavement"]] + [np.array(s["path"]) for s in layers.get("alleys", []) if False]


def nearest_street(x, y):
    best, bp = 1e9, None
    for pth in streets:
        a, b = pth[:-1], pth[1:]; ab = b - a
        t = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1]) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
        q = a + ab * t[:, None]; d = np.hypot(q[:, 0] - x, q[:, 1] - y); i = int(np.argmin(d))
        if d[i] < best:
            best, bp = d[i], q[i]
    return best, bp


# ---- roof mesh -> per-house aspect shares
mesh = json.load(open(OUT / "mesh.txt"))["houses"]
P = np.frombuffer(base64.b64decode(mesh["pos"]), np.float32).reshape(-1, 3)
C = np.frombuffer(base64.b64decode(mesh["col"]), np.uint8).reshape(-1, 4)
I = np.frombuffer(base64.b64decode(mesh["idx"]), np.uint32).reshape(-1, 3)
a, b, c = P[I[:, 0]], P[I[:, 1]], P[I[:, 2]]
nrm = np.cross(b - a, c - a); area = np.linalg.norm(nrm, axis=1) / 2
nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
nrm[nrm[:, 2] < 0] *= -1
tri_h = C[I[:, 0], 0].astype(int); tri_roof = C[I[:, 0], 2] == 1
slope = np.degrees(np.arccos(np.clip(nrm[:, 2], -1, 1)))
aspect = (np.degrees(np.arctan2(nrm[:, 0], nrm[:, 1])) + 360) % 360

for hi, h in enumerate(houses):
    ring = np.array(h["ring"]); cx_, cy_ = ring[:-1].mean(0)
    d, sp_ = nearest_street(cx_, cy_)
    if sp_ is None:
        continue
    face_az = (math.degrees(math.atan2(sp_[0] - cx_, sp_[1] - cy_)) + 360) % 360
    # snap to the footprint edge whose outward normal is closest to the street direction
    best = None
    for p0, p1 in zip(ring[:-1], ring[1:]):
        e = p1 - p0; L = np.hypot(*e)
        if L < 2: continue
        nx, ny = e[1] / L, -e[0] / L
        mid = (p0 + p1) / 2
        if (mid[0] + nx - cx_) * nx + (mid[1] + ny - cy_) * ny < 0:
            nx, ny = -nx, -ny
        ang = (math.degrees(math.atan2(nx, ny)) + 360) % 360
        diff = abs((ang - face_az + 180) % 360 - 180)
        if best is None or diff < best[0]:
            best = (diff, ang, mid, nx, ny)
    face = best[1] if best and best[0] < 50 else face_az
    h["facing"] = {"azimuth": round(face), "compass": compass(face), "street_m": round(float(d), 1)}
    # roof aspect shares (this house's roof triangles)
    m = (tri_h == hi % 256) & tri_roof
    if m.any():
        A = area[m]; flat = slope[m] < 10; asp = aspect[m]
        share = lambda lo, hi_: float(A[(~flat) & (((asp - lo) % 360) < (hi_ - lo) % 360)].sum() / A.sum())
        h["roof_aspect"] = {"N": round(share(315, 45) * 100), "E": round(share(45, 135) * 100), "S": round(share(135, 225) * 100),
                            "W": round(share(225, 315) * 100), "flat": round(float(A[flat].sum() / A.sum()) * 100),
                            "main_pitch_deg": round(float(np.median(slope[m][~flat])) if (~flat).any() else 0, 1)}
    # facade sun on the front and back walls
    if best:
        _, ang, mid, nx, ny = best
        out = {}
        for side, (sx, sy, fx, fy) in {"front": (mid[0] + nx * 0.4, mid[1] + ny * 0.4, nx, ny),
                                       "back": (cx_ - (mid[0] - cx_) - nx * 0.4, cy_ - (mid[1] - cy_) - ny * 0.4, -nx, -ny)}.items():
            r, c_ = cell(sx, sy); z0 = DTM[r, c_] + 2.0
            facing_sun = (dx * fx + dy * fy) > 0.05
            ks = np.flatnonzero(facing_sun)
            lit = np.array([sunlit(sx, sy, z0, k) for k in ks[::2]], bool)  # every 2nd hour, then x2
            mo = noon_side[ks[::2]]
            out[side] = {"compass": compass((math.degrees(math.atan2(fx, fy)) + 360) % 360),
                         "sun_hours_yr": int(lit.sum() * 2), "morning_h": int((lit & mo).sum() * 2), "afternoon_h": int((lit & ~mo).sum() * 2)}
        h["facade_sun"] = out
    print(f"{h.get('address')}: faces {h['facing']['compass']}, front sun {h.get('facade_sun', {}).get('front', {}).get('sun_hours_yr')} h/yr")

# ---- block-level sky facts
def noon_el(day):
    t = pd.date_range(f"{day} 10:00", f"{day} 15:00", freq="1min", tz="America/Chicago")
    s = loc.get_solarposition(t); i = int(s.apparent_elevation.values.argmax())
    return round(float(s.apparent_elevation.values[i]), 1), t[i].strftime("%H:%M")


sky = {"lat": LAT, "lon": LON,
       "noon_elevation": {k: noon_el(v) for k, v in {"winter solstice": "2025-12-21", "spring equinox": "2025-03-20",
                                                      "summer solstice": "2025-06-21", "fall equinox": "2025-09-22"}.items()},
       "note": "Elevation in degrees above the horizon at solar noon, with clock time (CST/CDT)."}
json.dump(houses, open(OUT / "houses.json", "w"), separators=(",", ":"))
json.dump(sky, open(OUT / "sky.json", "w"), indent=1)
from collections import Counter
print("facing:", Counter(h["facing"]["compass"] for h in houses if h.get("facing")))
print(sky["noon_elevation"])

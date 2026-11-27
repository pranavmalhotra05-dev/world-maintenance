"""Snapshot of aircraft positions over the US from adsb.lol, for the globe's sky layer.

    .venv/Scripts/python -u -W ignore -m usa.build_sky_aircraft

Source: adsb.lol API (https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}), community ADS-B receivers.
Licence: ODbL 1.0 ("license: ODbL 1.0" on https://www.adsb.lol/docs/open-data/api) - attribution + share-alike for
derived databases; this file is published under ODbL 1.0 with that attribution.
The API sends no CORS headers, so the browser cannot call it: this script writes a timestamped snapshot instead.
No key (500 requests/day); one run uses about 50.

Privacy: aircraft on the FAA LADD list (Limiting Aircraft Data Displayed) or using a PIA privacy address
(adsb.lol dbFlags bits 8 and 4) are dropped. Registration, ICAO hex and owner are NOT written - only
callsign, type, category, position, altitude, speed and track as broadcast. Ground traffic is dropped.

Output web/app/data/sky/aircraft.json:
  {generated, source, licence, licenceUrl, attribution, fields, aircraft: [[lon, lat, alt_m, gs_kt, track_deg, callsign, type, category], ...]}
"""
import json
import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "app" / "data" / "sky" / "aircraft.json"
API = "https://api.adsb.lol/v2/point/{lat}/{lon}/250"
UA = {"User-Agent": "world-maintenance-sky/1.0"}


socket.setdefaulttimeout(60)


def centres():
    out = []
    lat = 26.0
    while lat <= 49.5:
        lon = -124.0
        while lon <= -66.0:
            out.append((lat, lon)); lon += 7.0
        lat += 5.5
    out += [(61.2, -150.0), (64.8, -147.7), (58.4, -134.4), (21.3, -157.9), (19.7, -155.1)]   # Alaska, Hawaii
    return out


def get(lat, lon, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(API.format(lat=lat, lon=lon), headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(3 * (i + 1))
    print(f"  ({lat},{lon}) failed: {err!r}")
    return {"ac": []}


def sweep():
    seen, dropped = {}, 0
    for lat, lon in centres():
        for a in get(lat, lon).get("ac", []):
            if a.get("lat") is None or a.get("lon") is None or a.get("hex") is None:
                continue
            if int(a.get("dbFlags") or 0) & (4 | 8) or str(a["hex"]).startswith("~"):
                dropped += 1; continue
            alt = a.get("alt_geom", a.get("alt_baro"))
            if alt in (None, "ground"):
                continue
            t = datetime.now(timezone.utc).timestamp() - float(a.get("seen_pos") or 0)
            seen[a["hex"]] = [round(a["lon"], 4), round(a["lat"], 4), round(float(alt) * 0.3048), round(a.get("gs") or 0),
                              round(a.get("track") or 0), (a.get("flight") or "").strip(), a.get("t") or "", a.get("category") or "",
                              datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S")]
        time.sleep(1.6)
    return seen, dropped


def main():
    # two sweeps a few minutes apart: each aircraft seen in both gets a real trail (earlier position -> latest)
    first, _ = sweep()
    time.sleep(120)
    seen, dropped = sweep()
    for k, v in seen.items():
        p = first.get(k)
        v.append([p[0], p[1], p[2], p[8]] if p and (p[0], p[1]) != (v[0], v[1]) else None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "source": "adsb.lol community ADS-B network (api.adsb.lol v2/point, 250 nm circles over the US)",
           "licence": "ODbL 1.0", "licenceUrl": "https://www.adsb.lol/docs/open-data/api",
           "attribution": "Aircraft positions: adsb.lol contributors (ODbL 1.0)",
           "privacy": "FAA LADD and PIA aircraft removed; no registration, ICAO address or owner stored; airborne only",
           "fields": ["lon", "lat", "alt_m (geometric, else barometric)", "ground_speed_kt", "track_deg", "callsign", "type", "category", "time_utc",
                      "earlier [lon, lat, alt_m, time_utc] from the first sweep, a few minutes before | null"],
           "aircraft": list(seen.values())}
    OUT.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} - {len(seen)} aircraft, {dropped} LADD/PIA/anonymous dropped")


if __name__ == "__main__":
    main()

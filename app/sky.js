/* SkyTraffic: what is flying above the globe, from open sources only. Classic script; defines window.SkyTraffic.
   Needs the deck.gl UMD global `deck`. Nothing is on by default.

     aircraft   adsb.lol community ADS-B, ODbL 1.0. The API has no CORS, so this reads a timestamped snapshot
                (data/sky/aircraft.json, usa/build_sky_aircraft.py). FAA LADD / PIA aircraft removed; no
                registration or ICAO address. Positions are shown as captured, not moved forward in time.
     balloons   SondeHub radiosonde telemetry, CC BY-SA 2.0, fetched live in the browser
                (api.v2.sondehub.org, latest frame per balloon, last 3 h). Receiver/uploader details are not used.
     satellites NASA Satellite Situation Center web services (US public domain), fetched live in the browser:
                NASA's own ephemerides for the ~80 spacecraft it carries (ISS, Landsat, GOES, NOAA-20/21, Aqua,
                Aura, MMS ...). The full public satellite catalogue (Space-Track / CelesTrak) is NOT used: its terms
                forbid passing the data on without US Space Force approval.
     gnss       Navigation satellites of every country (GPS USA, GLONASS Russia, Galileo EU, BeiDou China, QZSS Japan,
                NavIC India, SBAS of 7 regions): positions computed from the orbits each satellite broadcasts itself,
                recorded by the IGS network (IGS multi-GNSS broadcast ephemerides, BKG mirror; IGS terms: open for
                commercial use, attribution). data/sky/satellites.json, 24 h window, usa/build_sky_satellites.py.
     shipsUs    MarineCadastre.gov AIS (BOEM + NOAA, US Coast Guard network), US public domain: one published day
                (data/sky/ships.json, usa/build_sky_ships.py) - last position per vessel + that day's real tracks in 6 ports.
     shipsFi    Fintraffic Digitraffic AIS (Finland / Baltic), CC BY 4.0, fetched live; trails are the positions this
                page has polled itself. Both: commercial vessel types only, no name / MMSI / IMO / call sign shown.
   Each object is drawn only where its source places it, at its reported altitude. */
(function () {
  "use strict";
  const SONDE = "https://api.v2.sondehub.org/sondes?last=10800";
  const DT = "https://meri.digitraffic.fi/api/ais/v1/";
  const SC = { cargo: [80, 200, 120], tanker: [230, 90, 70], passenger: [90, 160, 255], fishing: [250, 200, 60], tug: [200, 120, 255], "other commercial": [170, 170, 170] };
  const SYS = { GPS: [90, 170, 255], GLONASS: [255, 90, 90], Galileo: [255, 210, 60], BeiDou: [255, 140, 40], QZSS: [120, 230, 160], NavIC: [255, 120, 220], SBAS: [180, 180, 200] };
  const sysCol = (s) => SYS[s] || SYS[Object.keys(SYS).find((k) => String(s).includes(k))] || [200, 200, 200];
  // orbit classes from the satellite's own altitude range over the loaded window (km)
  const ORB = [["leo", "Low orbit (< 2,000 km)"], ["meo", "Medium orbit (2,000-30,000 km)"], ["geo", "Geostationary / geosynchronous (~36,000 km)"], ["heo", "Elliptical / far"]];
  const orbClass = (pts) => {
    let lo = Infinity, hi = -Infinity; for (const p of pts) { if (p[2] < lo) lo = p[2]; if (p[2] > hi) hi = p[2]; }
    lo /= 1000; hi /= 1000;
    return hi < 2000 ? "leo" : (lo >= 30000 && hi <= 42000) ? "geo" : (lo >= 2000 && hi < 30000) ? "meo" : "heo";
  };
  const aisGroup = (t) => t === 30 ? "fishing" : (t === 31 || t === 32 || t === 52) ? "tug" : (t >= 60 && t <= 69) ? "passenger" : (t >= 70 && t <= 79) ? "cargo"
    : (t >= 80 && t <= 89) ? "tanker" : ([33, 34, 50, 51, 53, 54, 56, 57, 58, 59].includes(t) ? "other commercial" : null);   // pleasure, sailing, military, police, unknown: dropped
  const SONDE_T = "https://api.v2.sondehub.org/sondes/telemetry?duration=3h&serial=";
  const SSC = "https://sscweb.gsfc.nasa.gov/WS/sscr/2/";
  const RE = 6371.2;                       // km
  const SRC = {
    aircraft: { name: "Aircraft (snapshot)", by: "adsb.lol contributors", lic: "ODbL 1.0", licUrl: "https://www.adsb.lol/docs/open-data/api",
      credit: "Aircraft: adsb.lol contributors (ODbL 1.0)" },
    balloons: { name: "Weather balloons (live)", by: "SondeHub / Project Horus receivers", lic: "CC BY-SA 2.0",
      licUrl: "https://github.com/projecthorus/sondehub-infra/wiki", credit: "Balloons: SondeHub (CC BY-SA 2.0)" },
    shipsUs: { name: "Ships, US waters (one day: 31 Dec 2024)", by: "MarineCadastre.gov (BOEM, NOAA, US Coast Guard)", lic: "US public domain",
      licUrl: "https://hub.marinecadastre.gov/documents/4a1d5c56ceb94fe2a656bf53eaece8d3", credit: "Ships (US): MarineCadastre.gov, BOEM/NOAA/USCG (public domain)" },
    shipsFi: { name: "Ships, Finland & Baltic (live)", by: "Fintraffic / digitraffic.fi", lic: "CC BY 4.0",
      licUrl: "https://www.digitraffic.fi/en/terms-of-service/", credit: "Ships (Baltic): Fintraffic / digitraffic.fi (CC BY 4.0)" },
    gnss: { name: "Navigation satellites, all countries", by: "IGS multi-GNSS broadcast ephemerides (BKG mirror)", lic: "IGS terms: open incl. commercial, attribution",
      licUrl: "https://igs.org/wp-content/uploads/2020/09/IGS-Data-and-Product-Disclaimer-and-Terms-of-Use-200805.pdf", credit: "Navigation satellites: International GNSS Service (IGS), BKG" },
    sats: { name: "Satellites, NASA-tracked (live)", by: "NASA Satellite Situation Center", lic: "US public domain",
      licUrl: "https://sscweb.gsfc.nasa.gov/", credit: "Satellites: NASA SSCWeb (public domain)" },
  };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const altCol = (m) => { const t = Math.max(0, Math.min(1, m / 12500)); return [Math.round(90 + 165 * t), Math.round(200 + 40 * t), Math.round(255 - 60 * t)]; };
  const iso = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const arr = (x) => (Array.isArray(x) && typeof x[0] === "string" && Array.isArray(x[1]) ? x[1] : x);   // SSC JSON: ["java.util.ArrayList", [...]]
  const obj = (x) => (Array.isArray(x) && typeof x[0] === "string" ? x[1] : x);

  class SkyTraffic {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {});
      this.aircraftUrl = opts.aircraftUrl || "data/sky/aircraft.json";
      this.shipsUrl = opts.shipsUrl || "data/sky/ships.json";
      this.gnssUrl = opts.gnssUrl || "data/sky/satellites.json";
      this.orb = new Set(ORB.map((o) => o[0])); this.selSat = null;
      this.on = new Set(); this.d = {}; this.panel = null; this._ver = 0; this._cache = null; this.timers = {};
    }
    getState() { return this.orb.size < ORB.length ? { on: [...this.on], orb: [...this.orb] } : { on: [...this.on] }; }
    setState(o = {}) { if (Array.isArray(o.orb)) this.orb = new Set(o.orb); if (Array.isArray(o.on)) { this.on = new Set(o.on.filter((k) => SRC[k])); for (const k of this.on) this._start(k); } this._dirty(); this._render(true); }
    credits() { return [...this.on].map((k) => SRC[k].credit).join(" · "); }
    _dirty() { this._ver++; this._cache = null; }

    // ---------- loading
    _start(k) {
      if (k === "aircraft" && !this.d.aircraft) this._aircraft();
      if (k === "shipsUs" && !this.d.shipsUs) this._shipsUs();
      if (k === "gnss") { if (!this.d.gnss) this._gnss(); clearInterval(this.timers.g); this.timers.g = setInterval(() => { if (this.on.has("gnss")) { this._dirty(); this.onUpdate(); } }, 5000); }
      if (k === "shipsFi") { if (!this.d.shipsFi) this._shipsFi(); clearInterval(this.timers.f); this.timers.f = setInterval(() => this.on.has("shipsFi") && this._shipsFi(), 60000); }
      if (k === "balloons") { if (!this.d.balloons) this._balloons(); clearInterval(this.timers.b); this.timers.b = setInterval(() => this.on.has("balloons") && this._balloons(), 60000); }
      if (k === "sats") {
        if (!this.d.sats) this._sats();
        clearInterval(this.timers.s); clearInterval(this.timers.t);
        this.timers.s = setInterval(() => this.on.has("sats") && this._sats(), 20 * 60000);    // new ephemeris window
        this.timers.t = setInterval(() => { if (this.on.has("sats")) { this._dirty(); this.onUpdate(); } }, 2000);   // move along it
      }
    }
    _stop(k) { if (k === "gnss") clearInterval(this.timers.g); if (k === "shipsFi") clearInterval(this.timers.f); if (k === "balloons") clearInterval(this.timers.b); if (k === "sats") { clearInterval(this.timers.s); clearInterval(this.timers.t); } }
    _set(k, v) { this.d[k] = v; this._dirty(); this._render(); this.onUpdate(); }
    async _aircraft() {
      this._set("aircraft", { state: "loading" });
      try {
        const j = await (await fetch(this.aircraftUrl)).json();
        this._set("aircraft", { state: "ok", when: j.generated, list: (j.aircraft || []).map(([lon, lat, alt, gs, trk, cs, ty, cat, t, prev]) => ({ src: "aircraft", lon, lat, alt, gs, trk, cs, ty, cat, t, prev })) });
      } catch (e) { this._set("aircraft", { state: "err", err: e.message }); }
    }
    async _gnss() {
      this._set("gnss", { state: "loading" });
      try {
        const j = await (await fetch(this.gnssUrl)).json();
        this._set("gnss", { state: "ok", when: j.window, list: (j.satellites || []).map((x) => ({ src: "gnss", id: x.id, name: x.name, country: x.country, operator: x.operator, system: x.system, pts: x.pts, orb: orbClass(x.pts) })) });
      } catch (e) { this._set("gnss", { state: "err", err: e.message }); }
    }
    async _shipsUs() {
      this._set("shipsUs", { state: "loading" });
      try {
        const j = await (await fetch(this.shipsUrl)).json();
        this._set("shipsUs", { state: "ok", when: j.data_date, list: (j.ships || []).map(([lon, lat, sog, cog, g, len, t]) => ({ src: "shipsUs", lon, lat, sog, cog, g, len, t })),
          tracks: (j.tracks || []).map((t) => ({ g: t.type_group, path: t.path.map((p) => [p[0], p[1], 0]) })) });
      } catch (e) { this._set("shipsUs", { state: "err", err: e.message }); }
    }
    async _shipsFi() {
      try {
        if (!this.fiTypes || Date.now() - this.fiTypes.t > 3600e3) {
          const v = await (await fetch(DT + "vessels")).json();
          this.fiTypes = { t: Date.now(), m: new Map(v.map((x) => [x.mmsi, aisGroup(x.shipType)])) };
        }
        const j = await (await fetch(DT + "locations")).json(), trail = this.fiTrail || (this.fiTrail = new Map()), list = [];
        for (const f of j.features || []) {
          const g = this.fiTypes.m.get(f.mmsi != null ? f.mmsi : f.properties && f.properties.mmsi); if (!g || !f.geometry) continue;
          const [lon, lat] = f.geometry.coordinates, pr = f.properties || {}, key = f.mmsi != null ? f.mmsi : pr.mmsi;
          const tr = trail.get(key) || []; const last = tr[tr.length - 1];
          if (!last || last[0] !== lon || last[1] !== lat) { tr.push([lon, lat, 0]); if (tr.length > 120) tr.shift(); trail.set(key, tr); }
          list.push({ src: "shipsFi", lon, lat, sog: pr.sog, cog: pr.cog, g, t: pr.timestampExternal ? new Date(pr.timestampExternal).toISOString().slice(11, 16) : "", tr });
        }
        this._set("shipsFi", { state: "ok", when: new Date().toISOString(), list });
      } catch (e) { this._set("shipsFi", { state: "err", err: e.message }); }
    }
    async _balloons() {
      if (!this.d.balloons) this._set("balloons", { state: "loading" });
      try {
        const j = await (await fetch(SONDE)).json();
        const list = Object.values(j || {}).filter((s) => isFinite(s.lat) && isFinite(s.lon) && isFinite(s.alt))
          .map((s) => ({ src: "balloons", lon: s.lon, lat: s.lat, alt: s.alt, vz: s.vel_v, vh: s.vel_h, type: [s.manufacturer, s.subtype || s.type].filter(Boolean).join(" "),
            serial: s.serial, time: s.datetime, temp: s.temp, rh: s.humidity, hpa: s.pressure }));
        const old = new Map(((this.d.balloons && this.d.balloons.list) || []).map((b) => [b.serial, b.path]));
        for (const b of list) b.path = old.get(b.serial) || null;
        this._set("balloons", { state: "ok", when: new Date().toISOString(), list });
        this._bt = (this._bt || 0) + 1;   // paths: new balloons every minute, all balloons every 10 minutes
        this._balloonTracks(this._bt % 10 === 1 ? list : list.filter((b) => !b.path));
      } catch (e) { this._set("balloons", { state: "err", err: e.message }); }
    }
    async _balloonTracks(list) {   // each balloon's reported positions over the last 3 h (SondeHub telemetry, as received)
      const q = list.slice();
      const one = async () => {
        for (let b; (b = q.shift());) {
          try {
            const j = await (await fetch(SONDE_T + encodeURIComponent(b.serial))).json();
            const fr = Object.values((j || {})[b.serial] || {}).filter((f) => isFinite(f.lat) && isFinite(f.lon) && isFinite(f.alt))
              .sort((a, c) => String(a.datetime).localeCompare(String(c.datetime)));
            if (fr.length > 1) { b.path = fr.map((f) => [f.lon, f.lat, f.alt]); this._dirty(); this.onUpdate(); }
          } catch (e) { /* keep the dot without a path */ }
        }
      };
      await Promise.all([one(), one(), one(), one()]);
    }
    async _sats() {
      if (!this.d.sats) this._set("sats", { state: "loading" });
      try {
        if (!this.obs) {
          const o = await (await fetch(SSC + "observatories", { headers: { Accept: "application/json" } })).json();
          const now = new Date().toISOString();
          this.obs = arr(obj(o).Observatory).map(obj).filter((x) => obj(x.StartTime) < now && obj(x.EndTime) > now && !/pred$|^(sun|moon|mercury|venus|mars|jupiter|saturn|uranus|neptune|pluto)$/.test(x.Id))
            .map((x) => ({ id: x.Id, name: x.Name }));
        }
        const t0 = new Date(Date.now() - 50 * 60000), t1 = new Date(Date.now() + 50 * 60000);
        // the service returns no data at all when asked for many spacecraft at once: ask in small batches
        const ids = this.obs.map((x) => x.id), groups = [];
        for (let i = 0; i < ids.length; i += 6) groups.push(ids.slice(i, i + 6));
        const all = (await Promise.all(groups.map((g) => fetch(SSC + "locations/" + g.join(",") + "/" + iso(t0) + "," + iso(t1) + "/geo/", { headers: { Accept: "application/json" } })
          .then((r) => r.json()).then((r) => arr(obj(obj(r).Result).Data) || []).catch(() => [])))).flat();
        const names = new Map(this.obs.map((x) => [x.id, x.name])), list = [];
        for (const sd of all) {
          const s = obj(sd), c = obj(arr(s.Coordinates)[0]); if (!c) continue;
          const X = arr(c.X), Y = arr(c.Y), Z = arr(c.Z), La = arr(c.Latitude), Lo = arr(c.Longitude), T = arr(s.Time).map((t) => Date.parse(obj(t)));
          const pts = La.map((la, i) => [((Lo[i] + 540) % 360) - 180, la, (Math.hypot(X[i], Y[i], Z[i]) - RE) * 1000, T[i]]);
          if (!pts.length || pts[0][2] > 1.0e8) continue;                  // beyond ~100,000 km (L1, deep space): not drawn on the globe
          list.push({ src: "sats", id: s.Id, name: names.get(s.Id) || s.Id, pts, orb: orbClass(pts) });
        }
        this._set("sats", { state: "ok", when: new Date().toISOString(), list });
      } catch (e) { this._set("sats", { state: e.name === "TypeError" ? "err" : "err", err: e.message }); }
    }
    _satNow(s) {   // NASA ephemeris samples are 1 min apart: place the dot between the two samples around now
      const t = Date.now(), p = s.pts; let i = p.findIndex((q) => q[3] > t);
      if (i <= 0) return null; const a = p[i - 1], b = p[i], f = (t - a[3]) / (b[3] - a[3]);
      let dl = b[0] - a[0]; if (dl > 180) dl -= 360; if (dl < -180) dl += 360;
      return [a[0] + dl * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    }

    // ---------- layers
    layers({ globe, modelMatrix } = {}) {
      const D = window.deck; if (!D || !this.on.size) return [];
      if (this._cache && this._cache.k === this._ver + "|" + globe && this._cache.mm === modelMatrix) return this._cache.L;
      const mm = modelMatrix ? { modelMatrix } : {}, L = [];
      const base = { ...mm, pickable: true, billboard: true, radiusUnits: "pixels", stroked: false, parameters: { depthTest: false } };
      const A = this.d.aircraft;
      if (this.on.has("aircraft") && A && A.state === "ok") {
        L.push(new D.LineLayer({ ...mm, id: "sky:ac-dir", data: A.list, pickable: false, widthUnits: "pixels", getWidth: 1, parameters: { depthTest: false },
          getSourcePosition: (d) => [d.lon, d.lat, d.alt],
          getTargetPosition: (d) => { const k = 0.02 * Math.max(0.3, d.gs / 400); const r = d.trk * Math.PI / 180; return [d.lon + k * Math.sin(r) / Math.max(0.2, Math.cos(d.lat * Math.PI / 180)), d.lat + k * Math.cos(r), d.alt]; },
          getColor: (d) => [...altCol(d.alt), 150] }));
        L.push(new D.LineLayer({ ...mm, id: "sky:ac-trail", data: A.list.filter((d) => d.prev), pickable: false, widthUnits: "pixels", getWidth: 1.2, parameters: { depthTest: false },
          getSourcePosition: (d) => [d.prev[0], d.prev[1], d.prev[2]], getTargetPosition: (d) => [d.lon, d.lat, d.alt], getColor: (d) => [...altCol(d.alt), 120] }));
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:aircraft", data: A.list, getPosition: (d) => [d.lon, d.lat, d.alt], getRadius: 1.8, getFillColor: (d) => [...altCol(d.alt), 235] }));
      }
      const G = this.d.gnss;
      if (this.on.has("gnss") && G && G.state === "ok") {
        L.push(new D.PathLayer({ ...mm, id: "sky:gnss-orbit", data: G.list.filter((d) => this._isSel(d)), pickable: false, widthUnits: "pixels", getWidth: 0.8, parameters: { depthTest: false },
          getPath: (d) => d.pts.map((p) => [p[0], p[1], p[2]]), wrapLongitude: !globe, getColor: (d) => [...sysCol(d.system), 200] }));
        const now = G.list.filter((d) => this.orb.has(d.orb)).map((s) => ({ ...s, at: this._satNow(s) })).filter((s) => s.at);
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:gnss", data: now, getPosition: (d) => d.at, getRadius: (d) => this._isSel(d) ? 6 : 3, updateTriggers: { getRadius: this.selSat }, getFillColor: (d) => [...sysCol(d.system), 250] }));
      }
      const U = this.d.shipsUs;
      if (this.on.has("shipsUs") && U && U.state === "ok") {
        L.push(new D.PathLayer({ ...mm, id: "sky:sh-trk", data: U.tracks, pickable: false, widthUnits: "pixels", getWidth: 0.8, parameters: { depthTest: false },
          getPath: (d) => d.path, getColor: (d) => [...(SC[d.g] || SC["other commercial"]), 90] }));
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:shipsUs", data: U.list, getPosition: (d) => [d.lon, d.lat, 0], getRadius: 1.7, getFillColor: (d) => [...(SC[d.g] || SC["other commercial"]), 230] }));
      }
      const F = this.d.shipsFi;
      if (this.on.has("shipsFi") && F && F.state === "ok") {
        L.push(new D.PathLayer({ ...mm, id: "sky:fi-trk", data: F.list.filter((d) => d.tr.length > 1), pickable: false, widthUnits: "pixels", getWidth: 1, parameters: { depthTest: false },
          getPath: (d) => d.tr, getColor: (d) => [...SC[d.g], 110] }));
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:shipsFi", data: F.list, getPosition: (d) => [d.lon, d.lat, 0], getRadius: 2, getFillColor: (d) => [...SC[d.g], 235] }));
      }
      const B = this.d.balloons;
      if (this.on.has("balloons") && B && B.state === "ok") {
        L.push(new D.PathLayer({ ...mm, id: "sky:bl-path", data: B.list.filter((d) => d.path), pickable: false, widthUnits: "pixels", getWidth: 1.4,
          parameters: { depthTest: false }, getPath: (d) => d.path, getColor: [255, 214, 120, 170] }));
        L.push(new D.LineLayer({ ...mm, id: "sky:bl-stalk", data: B.list, pickable: false, widthUnits: "pixels", getWidth: 1, parameters: { depthTest: false },
          getSourcePosition: (d) => [d.lon, d.lat, 0], getTargetPosition: (d) => [d.lon, d.lat, d.alt], getColor: [255, 214, 120, 90] }));
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:balloons", data: B.list, getPosition: (d) => [d.lon, d.lat, d.alt], getRadius: 3.2, stroked: true,
          lineWidthUnits: "pixels", getLineWidth: 1, getLineColor: [255, 255, 255, 200], getFillColor: (d) => d.vz < -2 ? [255, 120, 80, 240] : [255, 214, 120, 240] }));
      }
      const S = this.d.sats;
      if (this.on.has("sats") && S && S.state === "ok") {
        L.push(new D.PathLayer({ ...mm, id: "sky:orbits", data: S.list.filter((d) => this._isSel(d)), pickable: false, widthUnits: "pixels", getWidth: 1, parameters: { depthTest: false },
          getPath: (d) => d.pts.map((p) => [p[0], p[1], p[2]]), wrapLongitude: !globe, getColor: [200, 170, 255, 210] }));
        const now = S.list.filter((d) => this.orb.has(d.orb)).map((s) => ({ ...s, at: this._satNow(s) })).filter((s) => s.at);
        L.push(new D.ScatterplotLayer({ ...base, id: "sky:sats", data: now, getPosition: (d) => d.at, getRadius: (d) => this._isSel(d) ? 6 : 3, updateTriggers: { getRadius: this.selSat }, getFillColor: [200, 170, 255, 250] }));
      }
      this._cache = { k: this._ver + "|" + globe, mm: modelMatrix, L };
      return L;
    }

    _isSel(d) { return !!this.selSat && this.selSat === d.src + ":" + d.id; }
    // click a satellite: show only its orbit; click it again (or anything that is not a satellite) to clear
    pick(info) {
      const id = info && info.layer && String(info.layer.id), o = info && info.object;
      if ((id === "sky:sats" || id === "sky:gnss") && o) { const k = o.src + ":" + o.id; this.selSat = this.selSat === k ? null : k; this._dirty(); this._render(); this.onUpdate(); return true; }
      if (this.selSat) { this.selSat = null; this._dirty(); this._render(); this.onUpdate(); }
      return false;
    }
    tooltip(info) {
      if (!info || !info.object || !String(info.layer && info.layer.id).startsWith("sky:")) return null;
      const d = info.object, S = SRC[d.src]; let b = "";
      if (d.src === "aircraft") b = `<b>${esc(d.cs || "aircraft")}</b>${d.ty ? " · " + esc(d.ty) : ""}<br>${esc(Math.round(d.alt / 0.3048).toLocaleString())} ft · ${esc(d.gs)} kt · heading ${esc(d.trk)}°` +
        (d.prev ? `<br>trail: real position at ${esc(d.prev[3])} → ${esc(d.t)} UTC` : "") +
        `<br><span class="s">snapshot ${esc(this.d.aircraft.when)} UTC, not live</span>`;
      else if (d.src === "gnss") { const a = d.at || [0, 0, 0]; b = `<b>${esc(d.system)} ${esc(d.id)}</b> · ${esc(d.country)}<br>${esc(d.name || "")}${d.operator ? " · " + esc(d.operator) : ""}` +
        `<br>${esc(Math.round(a[2] / 1000).toLocaleString())} km up · ${esc(ORB.find((x) => x[0] === d.orb)[1])}<br><span class="s">from the orbit the satellite broadcasts (IGS), 2-min samples · click to ${this._isSel(d) ? "hide" : "show"} its orbit</span>`; }
      else if (d.src === "shipsUs" || d.src === "shipsFi") b = `<b>${esc(d.g[0].toUpperCase() + d.g.slice(1))} vessel</b>${d.len ? " · " + esc(d.len) + " m" : ""}` +
        `<br>${esc(d.sog ?? "?")} kn · course ${esc(d.cog ?? "?")}°<br><span class="s">` + (d.src === "shipsUs" ? `last report ${esc(d.t)} UTC on ${esc(this.d.shipsUs.when)}; not live`
          : `live, reported ${esc(d.t)} UTC · trail = positions polled while this page is open`) + `</span>`;
      else if (d.src === "balloons") b = `<b>Weather balloon</b> ${esc(d.type)}<br>${esc((d.alt / 1000).toFixed(1))} km · ${d.vz < -2 ? "falling (after burst)" : "rising"} ${esc(Math.abs(d.vz || 0).toFixed(1))} m/s` +
        (isFinite(d.temp) ? `<br>measured ${esc(Number(d.temp).toFixed(1))} °C` + (isFinite(d.rh) ? ` · ${esc(Math.round(d.rh))}% humidity` : "") + (isFinite(d.hpa) ? ` · ${esc(Math.round(d.hpa))} hPa` : "") : "") +
        (d.path ? `<br>path: ${esc(d.path.length)} reported positions, last 3 h` : "") +
        `<br><span class="s">sonde ${esc(d.serial)} · ${esc(String(d.time).slice(0, 16).replace("T", " "))} UTC</span>`;
      else if (d.src === "sats") { const a = d.at || [0, 0, 0]; b = `<b>${esc(d.name)}</b><br>${esc(Math.round(a[2] / 1000).toLocaleString())} km up<br>${esc(ORB.find((x) => x[0] === d.orb)[1])}<br><span class="s">NASA ephemeris, 1-min samples · click to ${this._isSel(d) ? "hide" : "show"} its orbit</span>`; }
      return { html: `<div class="pt-tip">${b}<br><span class="s">${esc(S.by)} · ${esc(S.lic)}</span></div>` };
    }

    // ---------- panel
    bindPanel(el) {
      this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => {
        const ob = e.target && e.target.dataset && e.target.dataset.orb;
        if (ob) { e.target.checked ? this.orb.add(ob) : this.orb.delete(ob); this._dirty(); this._render(); this.onUpdate(); return; }
        const k = e.target && e.target.dataset && e.target.dataset.sk; if (!k) return;
        if (e.target.checked) { this.on.add(k); this._start(k); } else { this.on.delete(k); this._stop(k); }
        this.onZoomRange && this.onZoomRange();
        this._dirty(); this._render(true); this.onUpdate();
      });
      this._render(true);
    }
    _render(full) {
      const el = this.panel; if (!el) return;
      if (full || !el.querySelector(".pt-st")) el.innerHTML = `<div class="tg">${Object.keys(SRC).map((k) =>
        `<label><input type="checkbox" data-sk="${k}" ${this.on.has(k) ? "checked" : ""}> ${esc(SRC[k].name)}</label>`).join("")}</div>` +
        `<div class="sky-orb" style="margin:6px 0 0 14px"></div><div class="note"></div><div class="pt-st"></div>`;
      const ob = el.querySelector(".sky-orb"), satOn = this.on.has("sats") || this.on.has("gnss");
      ob.hidden = !satOn;
      if (satOn) {
        const all = [...((this.d.sats && this.d.sats.list) || []), ...((this.d.gnss && this.d.gnss.list) || [])];
        ob.innerHTML = `<div class="lbl">Satellite orbits</div><div class="tg">` + ORB.map(([k, t]) =>
          `<label><input type="checkbox" data-orb="${k}" ${this.orb.has(k) ? "checked" : ""}> ${esc(t)} <span class="sub small">${all.filter((x) => x.orb === k).length}</span></label>`).join("") +
          `</div><div class="sub small">${this.selSat ? "Showing the orbit of " + esc(this.selSat.split(":")[1]) + ". Click it again to hide." : "Click a satellite to see its orbit."}</div>`;
      }
      el.querySelector(".note").innerHTML = this.on.size ? [...this.on].map((k) => `${esc(SRC[k].by)}: <a href="${SRC[k].licUrl}" target="_blank" rel="noopener">${esc(SRC[k].lic)}</a>`).join("<br>") +
        (this.on.has("sats") ? "<br>Only spacecraft NASA publishes orbits for. The full catalogue (Starlink, debris) comes from US Space Force data that may not be passed on." : "") +
        (this.on.has("aircraft") ? "<br>Privacy-listed aircraft (FAA LADD/PIA) are removed." : "") +
        (this.on.has("gnss") ? "<br>Navigation satellites: blue GPS (USA), red GLONASS (Russia), yellow Galileo (EU), orange BeiDou (China), green QZSS (Japan), pink NavIC (India), grey SBAS." : "") +
        (this.on.has("shipsUs") || this.on.has("shipsFi") ? "<br>Ships: green cargo, red tanker, blue passenger, yellow fishing, violet tug, grey other. Leisure boats left out; no names or IDs." : "")
        : "Planes, balloons and satellites from open sources. Pick one.";
      const st = [...this.on].map((k) => { const s = this.d[k]; return `${k}: ` + (!s || s.state === "loading" ? "loading" : s.state === "err" ? "failed (" + s.err + ")" :
        s.list.length + (k === "gnss" ? (s.list.some((x) => this._satNow(x)) ? " navigation satellites, 8 systems" : " (orbit window " + (s.when && s.when.start) + " has expired: rebuild)") : k === "shipsUs" ? " vessels on " + s.when + ", tracks in 6 ports" : k === "shipsFi" ? " commercial vessels now" : k === "aircraft" ? " in snapshot " + s.when + " UTC" : k === "balloons" ? " in the air, last 3 h" : " tracked now")); }).join("\n");
      const e = el.querySelector(".pt-st"); e.textContent = st; e.style.whiteSpace = "pre-line";
    }
  }
  window.SkyTraffic = SkyTraffic;
})();

/* EarthPoints: real measured point data fetched live for the area in view.
   Classic script; defines window.EarthPoints. Needs the deck.gl UMD global `deck`.

   Sources (none on by default):
     wells  USGS groundwater depth to water (param 72019), latest field measurement per well.
            Live, CORS OK: api.waterdata.usgs.gov OGC API. US public domain.
     gnss   GNSS station velocities (mm/yr). No CORS-enabled endpoint exists, so this reads a
            static JSON built offline (opts.gnssUrl). Format:
            { "source": "...", "licence": "...", "frame": "NAM14",
              "stations": [[id, lon, lat, ve, vn, vu, "YYYY-MM-DD"], ...] }   ve/vn/vu in mm/yr
     icesat2 ICESat-2 ATL08 ground and canopy heights. Static JSON only (opts.icesat2Url).
            { "points": [[lon, lat, h_te_m, h_canopy_m|null, "YYYY-MM-DD", track, "gt1l"], ...] }
   Each point is drawn only where the data places it. z = 0 unless the data gives a height. */
(function () {
  "use strict";
  const WELL_API = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-field-measurements/items";
  const WELL_MINZ = 9, TILE = 0.5, MAX_TILES = 16, CONC = 4, PAGE = 5000, MAX_PAGES = 3;
  const SRC = {
    wells: {
      name: "Groundwater wells", unit: "ft below land",
      by: "USGS Water Data", lic: "US public domain",
      licUrl: "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
      credit: "Groundwater levels: USGS (public domain)",
    },
    gnss: {
      name: "GNSS ground motion", unit: "mm/yr vertical",
      by: "EarthScope GAGE / USGS", lic: "CC BY 4.0 (EarthScope) or public domain (USGS)",
      licUrl: "https://www.unavco.org/data/policies_forms/data-policy/data-license.html",
      credit: "GNSS velocities: EarthScope Consortium GAGE Facility (CC BY 4.0)",
    },
    icesat2: {
      name: "ICESat-2 laser heights", unit: "m (WGS84 ellipsoid)",
      by: "NASA ICESat-2 ATL08 via NSIDC OpenAltimetry", lic: "NASA open data, no restrictions",
      licUrl: "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
      credit: "ICESat-2 ATL08: NASA / NSIDC",
    },
  };

  // colour ramps: stops [t, [r,g,b]]
  const RAMP_DEPTH = [[0, [90, 230, 255]], [0.15, [80, 200, 140]], [0.35, [240, 220, 80]], [0.6, [255, 140, 50]], [1, [230, 40, 90]]];
  const RAMP_DIV = [[0, [235, 50, 60]], [0.5, [235, 235, 235]], [1, [60, 150, 255]]];   // sinking red, rising blue
  const RAMP_H = [[0, [40, 90, 200]], [0.5, [120, 220, 160]], [1, [255, 240, 200]]];
  function ramp(stops, t) {
    t = Math.max(0, Math.min(1, t));
    for (let i = 1; i < stops.length; i++) if (t <= stops[i][0]) {
      const [t0, a] = stops[i - 1], [t1, b] = stops[i], f = (t - t0) / (t1 - t0 || 1);
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f].map(Math.round);
    }
    return stops[stops.length - 1][1];
  }
  const css = (stops) => "linear-gradient(90deg," + stops.map(([t, c]) => `rgb(${c}) ${t * 100}%`).join(",") + ")";
  const wellT = (ft) => Math.sqrt(Math.max(0, ft) / 600);            // 0..600 ft, sqrt stretch
  const gnssT = (vu) => 0.5 + Math.max(-1, Math.min(1, vu / 10)) / 2; // -10..+10 mm/yr
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let cssDone = false;
  function injectCss() {
    if (cssDone) return; cssDone = true;
    const s = document.createElement("style");
    s.textContent = `
.ptp .tg label.off{opacity:.45;cursor:default}
.ptp .leg{margin-top:6px}
.ptp .leg .bar{height:6px;border-radius:1px;margin:3px 0 1px}
.ptp .leg .ax{display:flex;justify-content:space-between;font:10px var(--f-mono,monospace);color:var(--dim)}
.ptp .pt-st{font:11px var(--f-mono,monospace);color:var(--amber);margin-top:5px}
.ptp .yr{display:flex;gap:4px;margin-top:6px;flex-wrap:wrap}
.ptp .yr .chip.on{color:var(--cyan);border-color:var(--cyan)}
.pt-tip b{color:var(--cyan)} .pt-tip .s{color:var(--dim);font-size:11px}`;
    document.head.appendChild(s);
  }

  class EarthPoints {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {});
      this.gnssUrl = opts.gnssUrl || null;
      this.icesat2Url = opts.icesat2Url || null;
      this.on = new Set();
      this.since = 0;                  // year filter for wells (0 = all years)
      this.view = null;
      this.tiles = new Map();          // key -> {state:'loading'|'ok'|'err', data:[], ctrl}
      this.statics = {};               // name -> {state, data}
      this.timer = 0;
      this.panel = null;
      this._ver = 0; this._layerCache = null;
    }
    available(k) { return k === "wells" || (k === "gnss" && !!this.gnssUrl) || (k === "icesat2" && !!this.icesat2Url); }

    // ---------- state ----------
    getState() { return { on: [...this.on], since: this.since }; }
    setState(o = {}) {
      if (Array.isArray(o.on)) this.on = new Set(o.on.filter((k) => SRC[k] && this.available(k)));
      if (o.since != null) this.since = +o.since || 0;
      this._dirty(); this._renderPanel(true);
      if (this.view) this._schedule(0);
    }
    credits() { return [...this.on].map((k) => SRC[k].credit).join(" · "); }

    // ---------- fetching ----------
    update(viewState, { globe, zoom, bounds } = {}) {
      const z = zoom != null ? zoom : viewState && viewState.zoom;
      this.view = { globe: !!globe, zoom: z, bounds };
      this._schedule(350);
    }
    _schedule(ms) { clearTimeout(this.timer); this.timer = setTimeout(() => this._load(), ms); }
    _wellTiles() {
      const v = this.view; if (!v || !v.bounds || v.zoom < WELL_MINZ) return [];
      let [w, s, e, n] = v.bounds; if (e < w) e += 360;
      const out = [];
      for (let y = Math.floor(s / TILE); y * TILE < n; y++)
        for (let x = Math.floor(w / TILE); x * TILE < e; x++) {
          const xx = ((x * TILE + 180) % 360 + 360) % 360 - 180;
          out.push(`${xx.toFixed(1)},${(y * TILE).toFixed(1)}`);
        }
      // nearest to centre first, capped
      const cx = (w + e) / 2, cy = (s + n) / 2;
      out.sort((a, b) => { const [ax, ay] = a.split(",").map(Number), [bx, by] = b.split(",").map(Number);
        return Math.hypot(ax + TILE / 2 - cx, ay + TILE / 2 - cy) - Math.hypot(bx + TILE / 2 - cx, by + TILE / 2 - cy); });
      return out.slice(0, MAX_TILES);
    }
    _load() {
      const want = new Set(this.on.has("wells") ? this._wellTiles() : []);
      for (const [k, t] of this.tiles) if (t.state === "loading" && !want.has(k)) { t.ctrl.abort(); this.tiles.delete(k); }
      const queue = [...want].filter((k) => !this.tiles.has(k));
      const active = () => [...this.tiles.values()].filter((t) => t.state === "loading").length;
      const pump = () => { while (queue.length && active() < CONC) this._fetchWellTile(queue.shift()).finally(pump); };
      pump();
      for (const k of ["gnss", "icesat2"]) if (this.on.has(k) && !this.statics[k]) this._fetchStatic(k);
      this._renderPanel();
    }
    async _fetchWellTile(key) {
      const [x, y] = key.split(",").map(Number), ctrl = new AbortController();
      const t = { state: "loading", data: [], ctrl }; this.tiles.set(key, t); this._renderPanel();
      let url = `${WELL_API}?f=json&parameter_code=72019&limit=${PAGE}` +
        `&bbox=${x},${y},${(x + TILE).toFixed(1)},${(y + TILE).toFixed(1)}` +
        `&properties=monitoring_location_id,value,unit_of_measure,time,qualifier`;
      const best = new Map();
      try {
        for (let p = 0; url && p < MAX_PAGES; p++) {
          const r = await fetch(url, { signal: ctrl.signal });
          if (!r.ok) throw new Error("HTTP " + r.status);
          const j = await r.json();
          for (const f of j.features || []) {
            const pr = f.properties || {}, g = f.geometry;
            const v = parseFloat(pr.value);
            if (!g || !g.coordinates || !isFinite(v)) continue;
            const id = pr.monitoring_location_id, old = best.get(id);
            if (old && old.time >= pr.time) continue;
            best.set(id, { src: "wells", id, lon: g.coordinates[0], lat: g.coordinates[1], v,
              unit: pr.unit_of_measure || "ft", time: pr.time || "", yr: parseInt(pr.time) || 0,
              q: (pr.qualifier || []).join(", ") });
          }
          const nx = (j.links || []).find((l) => l.rel === "next");
          url = nx ? nx.href : null;
        }
        t.state = "ok"; t.data = [...best.values()];
      } catch (e) {
        if (e.name === "AbortError") return;
        t.state = "err"; t.err = e.message;
        setTimeout(() => { if (this.tiles.get(key) === t) this.tiles.delete(key); }, 30000); // allow retry later
      }
      this._dirty(); this._renderPanel(); this.onUpdate();
    }
    async _fetchStatic(k) {
      const s = this.statics[k] = { state: "loading", data: [] };
      try {
        const r = await fetch(k === "gnss" ? this.gnssUrl : this.icesat2Url);
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        if (k === "gnss") s.data = (j.stations || []).map(([id, lon, lat, ve, vn, vu, date]) =>
          ({ src: "gnss", id, lon, lat, ve, vn, vu, time: date || "", frame: j.frame || "", by: j.source || "" }));
        else s.data = (j.points || []).map(([lon, lat, h, hc, date, track, beam]) =>
          ({ src: "icesat2", lon, lat, h, hc: hc != null && hc < 1e30 ? hc : null, time: date || "", track, beam }));
        s.state = "ok";
      } catch (e) { s.state = "err"; s.err = e.message; }
      this._dirty(); this._renderPanel(); this.onUpdate();
    }

    // ---------- data views ----------
    _inView(p) {
      const b = this.view && this.view.bounds; if (!b) return true;
      const [w, s, e, n] = b; if (p.lat < s || p.lat > n) return false;
      return e >= w ? p.lon >= w && p.lon <= e : p.lon >= w || p.lon <= e;
    }
    _wells() {
      const out = [], seen = new Set();
      for (const t of this.tiles.values()) for (const p of t.data)
        if (!seen.has(p.id) && p.yr >= this.since) { seen.add(p.id); out.push(p); }
      return out;
    }
    _dirty() { this._ver++; this._layerCache = null; }

    // ---------- layers ----------
    layers({ globe, zoom, modelMatrix } = {}) {
      const D = window.deck; if (!D || !this.on.size) return [];
      const z = zoom != null ? zoom : (this.view && this.view.zoom) || 0;
      const zk = Math.round(z * 4) / 4;
      const ck = `${this._ver}|${globe}|${zk}|${!!modelMatrix}`;
      if (this._layerCache && this._layerCache.k === ck && this._layerCache.mm === modelMatrix) return this._layerCache.L;
      const mm = modelMatrix ? { modelMatrix } : {};
      const base = { pickable: true, billboard: true, radiusUnits: "pixels", stroked: false, filled: true,
        antialiasing: true, parameters: { depthTest: false }, ...mm };
      const L = [];
      if (this.on.has("wells") && z >= WELL_MINZ - 0.5) {
        const data = this._wells();
        L.push(new D.ScatterplotLayer({ ...base, id: "pt:wells", data,
          getPosition: (d) => [d.lon, d.lat, 0], getRadius: z >= 12 ? 3.2 : 2.4,
          getFillColor: (d) => [...ramp(RAMP_DEPTH, wellT(d.v)), 235],
          updateTriggers: { getRadius: z >= 12 } }));
      }
      if (this.on.has("gnss") && this.statics.gnss && this.statics.gnss.state === "ok") {
        const data = this.statics.gnss.data;
        // horizontal velocity drawn as a line from the station; length in degrees scaled by zoom
        const k = 0.25 / Math.pow(2, Math.max(0, z - 3));  // deg per mm/yr
        L.push(new D.LineLayer({ ...mm, id: "pt:gnss-h", data, pickable: false, widthUnits: "pixels", getWidth: 1.2,
          parameters: { depthTest: false },
          getSourcePosition: (d) => [d.lon, d.lat, 0],
          getTargetPosition: (d) => [d.lon + d.ve * k / Math.max(0.2, Math.cos(d.lat * Math.PI / 180)), d.lat + d.vn * k, 0],
          getColor: [255, 210, 77, 200], updateTriggers: { getTargetPosition: k } }));
        L.push(new D.ScatterplotLayer({ ...base, id: "pt:gnss", data, stroked: true, lineWidthUnits: "pixels",
          getLineWidth: 0.8, getLineColor: [0, 0, 0, 180],
          getPosition: (d) => [d.lon, d.lat, 0], getRadius: 3.5,
          getFillColor: (d) => [...ramp(RAMP_DIV, gnssT(d.vu)), 240] }));
      }
      if (this.on.has("icesat2") && this.statics.icesat2 && this.statics.icesat2.state === "ok") {
        const data = this.statics.icesat2.data;
        let lo = Infinity, hi = -Infinity; for (const d of data) { if (d.h < lo) lo = d.h; if (d.h > hi) hi = d.h; }
        this._hRange = [lo, hi];
        L.push(new D.ScatterplotLayer({ ...base, id: "pt:icesat2", data,
          getPosition: (d) => [d.lon, d.lat, globe ? 0 : d.h], getRadius: 1.4,
          getFillColor: (d) => [...ramp(RAMP_H, (d.h - lo) / (hi - lo || 1)), 230] }));
      }
      this._layerCache = { k: ck, mm: modelMatrix, L };
      return L;
    }

    // ---------- tooltip ----------
    tooltip(info) {
      if (!info || !info.object || !info.layer || !String(info.layer.id).startsWith("pt:")) return null;
      const d = info.object, S = SRC[d.src]; if (!S) return null;
      const date = esc(String(d.time).slice(0, 10));
      let body = "";
      if (d.src === "wells") body =
        `<b>${esc(d.v.toFixed(2))} ${esc(d.unit)}</b> depth to water below land surface` +
        `<br>measured ${date}${d.q ? " · " + esc(d.q) : ""}<br><span class="s">well ${esc(d.id)}</span>`;
      else if (d.src === "gnss") body =
        `<b>${d.vu >= 0 ? "+" : ""}${esc(d.vu.toFixed(2))} mm/yr</b> vertical (${d.vu < 0 ? "sinking" : "rising"})` +
        `<br>E ${esc(d.ve.toFixed(2))} · N ${esc(d.vn.toFixed(2))} mm/yr${d.frame ? " (" + esc(d.frame) + ")" : ""}` +
        `<br><span class="s">station ${esc(d.id)}${date ? " · to " + date : ""}</span>`;
      else body =
        `<b>${esc(d.h.toFixed(2))} m</b> ground height${d.hc != null ? ` · canopy ${esc(d.hc.toFixed(2))} m` : ""}` +
        `<br>${date} · track ${esc(d.track)} ${esc(d.beam)}`;
      return { html: `<div class="pt-tip">${body}<br><span class="s">${esc(S.by)} · ${esc(S.lic)}</span></div>` };
    }

    // ---------- panel ----------
    bindPanel(el) {
      injectCss(); this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => {
        const k = e.target && e.target.dataset && e.target.dataset.k; if (!k) return;
        e.target.checked ? this.on.add(k) : this.on.delete(k);
        this._dirty(); this._renderPanel(true); this._schedule(0); this.onUpdate();
      });
      el.addEventListener("click", (e) => {
        const y = e.target && e.target.dataset && e.target.dataset.since; if (y == null) return;
        this.since = +y; this._dirty(); this._renderPanel(true); this.onUpdate();
      });
      this._renderPanel(true);
    }
    _status() {
      const parts = [], v = this.view;
      if (this.on.has("wells")) {
        if (!v || v.zoom < WELL_MINZ) parts.push(`wells: zoom in to load (≥ ${WELL_MINZ})`);
        else {
          const ts = [...this.tiles.values()], ld = ts.filter((t) => t.state === "loading").length,
            er = ts.filter((t) => t.state === "err").length, n = this._wells().filter((p) => this._inView(p)).length;
          parts.push(`wells: ${ld ? "loading " + ld + " tiles · " : ""}${n} points${er ? " · " + er + " tiles failed" : ""}`);
        }
      }
      for (const k of ["gnss", "icesat2"]) if (this.on.has(k)) {
        const s = this.statics[k];
        parts.push(`${k}: ` + (!s || s.state === "loading" ? "loading" : s.state === "err" ? "file not available (" + s.err + ")"
          : s.data.filter((p) => this._inView(p)).length + " points in view"));
      }
      return parts.join("\n") || "no source on";
    }
    _renderPanel(full) {
      const el = this.panel; if (!el) return;
      if (full || !el.querySelector(".pt-st")) {
        const box = (k) => {
          const ok = this.available(k);
          return `<label class="${ok ? "" : "off"}" title="${ok ? "" : "no CORS-enabled public endpoint; needs a static file"}">` +
            `<input type="checkbox" data-k="${k}" ${this.on.has(k) ? "checked" : ""} ${ok ? "" : "disabled"}> ${esc(SRC[k].name)}</label>`;
        };
        el.innerHTML = `<div class="lbl">Measured points</div>
<div class="tg">${Object.keys(SRC).map(box).join("")}</div>
<div class="yr" data-yr></div><div class="leg"></div><div class="note"></div><div class="pt-st"></div>`;
      }
      const yr = el.querySelector("[data-yr]");
      yr.hidden = !this.on.has("wells");
      yr.innerHTML = `<span class="lbl" style="align-self:center">wells measured</span>` +
        [[0, "any year"], [2000, "since 2000"], [2020, "since 2020"]].map(([y, t]) =>
          `<button class="chip${this.since === y ? " on" : ""}" data-since="${y}">${t}</button>`).join("");
      const leg = [];
      if (this.on.has("wells")) leg.push(["Depth to water, ft below land", RAMP_DEPTH, ["0", "150", "600+"]]);
      if (this.on.has("gnss")) leg.push(["Vertical velocity, mm/yr (line = horizontal)", RAMP_DIV, ["−10 sinking", "0", "+10 rising"]]);
      if (this.on.has("icesat2")) { const r = this._hRange; leg.push(["Ground height, m", RAMP_H, r ? [r[0].toFixed(0), "", r[1].toFixed(0)] : ["low", "", "high"]]); }
      el.querySelector(".leg").innerHTML = leg.map(([t, s, ax]) =>
        `<div class="lbl">${esc(t)}</div><div class="bar" style="background:${css(s)}"></div>` +
        `<div class="ax">${ax.map((a) => `<span>${esc(a)}</span>`).join("")}</div>`).join("");
      el.querySelector(".note").innerHTML = this.on.size
        ? [...this.on].map((k) => `${esc(SRC[k].by)}: <a href="${SRC[k].licUrl}" target="_blank" rel="noopener">${esc(SRC[k].lic)}</a>`).join("<br>") +
          (this.on.has("wells") ? "<br>Latest manual reading per well; dates vary, see tooltip." : "")
        : "Real located measurements only. Pick a source.";
      const st = el.querySelector(".pt-st"); st.textContent = this._status(); st.style.whiteSpace = "pre-line";
    }
  }
  EarthPoints.SOURCES = SRC;
  window.EarthPoints = EarthPoints;
})();

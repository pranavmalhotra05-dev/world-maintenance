/* PoolsArea: every detected swimming pool across a whole city (usa/pools_area.py, usa/pools_area_az.py) and the
   LiDAR water holes (usa/lidar_water.py). Classic script; defines window.PoolsArea. Needs the deck.gl UMD global `deck`.

   Data: data/pools_area/<area>/index.json (tiles with bbox + status) and <tile>.json per finished tile.
   Pool outlines are detected water pixels on public-domain NAIP; never synthesized. A selected pool lights its
   outline, its house (building footprint) and its official lot (where an open parcel exists) together, on the ground.
   States describe the photo day only, not how the pool is looked after. */
(function () {
  "use strict";
  const AREAS = [
    { slug: "sacramento", name: "Sacramento, CA", kind: "pools" },
    { slug: "scottsdale", name: "Scottsdale, AZ", kind: "pools" },
    { slug: "scottsdale_lidar", name: "Scottsdale, AZ · LiDAR water holes", kind: "holes" },
  ];
  const ST = {
    clean_blue: [[80, 170, 255], "clean blue"], turquoise: [[80, 230, 215], "turquoise"], green_algae: [[130, 225, 60], "green (algae)"],
    covered_or_empty: [[215, 190, 140], "covered or empty"], dark: [[120, 120, 150], "dark"], filled_or_removed: [[150, 150, 150], "filled in / removed"],
    not_present: [[110, 110, 110], "not present"],
  };
  const col = (s) => (ST[s] || [[200, 200, 200]])[0];
  const MINZ = 12.5, MAX_TILES = 40, CONC = 4;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  class PoolsArea {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {}); this.groundAt = opts.groundAt || (() => 0);
      this.on = new Set(); this.idx = {}; this.tiles = new Map(); this.view = null; this.sel = null; this.panel = null; this._ver = 0; this._cache = null; this.timer = 0;
    }
    getState() { return { on: [...this.on] }; }
    setState(o = {}) { if (Array.isArray(o.on)) this.on = new Set(o.on.filter((k) => AREAS.some((a) => a.slug === k))); for (const k of this.on) this._index(k); this._dirty(); this._render(); }
    credits() {
      if (!this.on.size) return "";
      return "Pools: USDA NAIP (public domain)" + ([...this.on].includes("sacramento") ? ", Sacramento County parcels (CC0)" : "") + ", Overture buildings (ODbL)" +
        ([...this.on].includes("scottsdale_lidar") ? ", USGS 3DEP LiDAR (public domain)" : "");
    }
    _dirty() { this._ver++; this._cache = null; }
    async _index(k) {
      if (this.idx[k]) return; this.idx[k] = { state: "loading" }; this._render();
      try { const r = await fetch(`data/pools_area/${k}/index.json`); if (!r.ok) throw new Error(r.status === 404 ? "not built yet" : "HTTP " + r.status); this.idx[k] = { state: "ok", j: await r.json() }; }
      catch (e) { this.idx[k] = { state: "err", err: e.message }; }
      this._render(); this._schedule(0);
    }
    update(viewState, { zoom, bounds, globe } = {}) { this.view = { zoom, bounds, globe }; this._schedule(300); }
    _schedule(ms) { clearTimeout(this.timer); this.timer = setTimeout(() => this._load(), ms); }
    _load() {
      const v = this.view; if (!v || !v.bounds || v.zoom < MINZ || v.globe) { this._render(); return; }
      let [w, s, e, n] = v.bounds; const want = [];
      for (const k of this.on) {
        const I = this.idx[k]; if (!I || I.state !== "ok") continue;
        for (const t of I.j.tiles || []) {
          const b = t.bbox; if (!b || b[2] < w || b[0] > e || b[3] < s || b[1] > n) continue;
          if (t.status && !/done|ok|final/.test(t.status)) continue;
          want.push([k, t.id, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, t.file || t.id + ".json"]);
        }
      }
      const cx = (w + e) / 2, cy = (s + n) / 2;
      want.sort((a, b) => Math.hypot(a[2] - cx, a[3] - cy) - Math.hypot(b[2] - cx, b[3] - cy));
      const q = want.slice(0, MAX_TILES).filter(([k, id]) => !this.tiles.has(k + "/" + id));
      const pump = () => { while (q.length && [...this.tiles.values()].filter((t) => t.state === "loading").length < CONC) this._tile(...q.shift()).finally(pump); };
      pump(); this._render();
    }
    async _tile(k, id, cx, cy, file) {
      const key = k + "/" + id, t = { state: "loading", list: [] }; this.tiles.set(key, t);
      try {
        const r = await fetch(`data/pools_area/${k}/${file}`); if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json(), area = AREAS.find((a) => a.slug === k);
        t.list = (area.kind === "holes" ? j.holes || [] : j.pools || []).filter((p) => p.ring && p.ring.length > 2).map((p) => ({ ...p, area: k, akind: area.kind, snaps: j.snapshots }));
        t.state = "ok";
      } catch (e) { t.state = "err"; t.err = e.message; }
      this._dirty(); this._render(); this.onUpdate();
    }
    _list() { const out = []; for (const [key, t] of this.tiles) if (t.state === "ok" && this.on.has(key.split("/")[0])) for (const p of t.list) out.push(p); return out; }
    _z(p) {   // ground under the pool: built block > streamed LiDAR ground > ETOPO (refreshed as LiDAR arrives)
      const now = performance.now();
      if (p._zt == null || (now - p._zt > 4000 && p._zn < 6)) { const c = p.centre || p.ring[0]; p._z = this.groundAt(c[0], c[1]) + 0.25; p._zt = now; p._zn = (p._zn || 0) + 1; }
      return p._z;
    }

    layers({ globe, zoom, modelMatrix } = {}) {
      const D = window.deck; if (!D || !this.on.size || globe || (zoom || 0) < MINZ - 0.5) return [];
      const mm = modelMatrix ? { modelMatrix } : {}, all = this._list(), L = [];
      const ring3 = (r, z) => r.map((q) => [q[0], q[1], z]);
      const pools = all.filter((p) => p.akind === "pools"), holes = all.filter((p) => p.akind === "holes");
      const sel = this.sel;
      if (sel) {   // the selected pool's house and official lot, on the ground, in the pool's own colour
        const c = col(sel.latest_state), z = this._z(sel);
        if (sel.parcel && sel.parcel.ring) L.push(new D.PolygonLayer({ ...mm, id: "pa:lot", data: [sel], pickable: false, stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
          parameters: { depthTest: false }, getPolygon: (d) => ring3(d.parcel.ring, z - 0.1), getFillColor: [...c, 40], getLineColor: [...c, 255] }));
        if (sel.building_ring) L.push(new D.PolygonLayer({ ...mm, id: "pa:house", data: [sel], pickable: false, stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 1.5,
          parameters: { depthTest: false }, getPolygon: (d) => ring3(d.building_ring, z), getFillColor: [...c, 70], getLineColor: [...c, 230] }));
      }
      if (pools.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:pools", data: pools, pickable: true, stroked: true, filled: true, lineWidthUnits: "pixels",
        getLineWidth: (d) => d === sel ? 2.5 : 1, parameters: { depthTest: false }, getPolygon: (d) => ring3(d.ring, this._z(d)),
        getFillColor: (d) => [...col(d.latest_state), d === sel ? 230 : 150], getLineColor: (d) => [...col(d.latest_state), 255],
        updateTriggers: { getLineWidth: sel && sel.id, getFillColor: sel && sel.id, getPolygon: this._ver } }));
      // a marker on each pool so it stays visible among the LiDAR colours when zoomed out (outline still drawn)
      if (pools.length && (zoom || 0) < 18) L.push(new D.ScatterplotLayer({ ...mm, id: "pa:dots", data: pools, pickable: true, billboard: true, radiusUnits: "pixels",
        getRadius: (zoom || 0) < 15 ? 3.5 : 5, stroked: true, lineWidthUnits: "pixels", getLineWidth: (d) => d.check ? 2 : 1.5, getLineColor: (d) => d.check ? [...col(d.latest_state), 255] : [255, 255, 255, 255], parameters: { depthTest: false },
        getPosition: (d) => [(d.centre || d.ring[0])[0], (d.centre || d.ring[0])[1], this._z(d) + 0.5], getFillColor: (d) => d.check ? [0, 0, 0, 0] : [...col(d.latest_state), 255],   // hollow ring = needs a look
        updateTriggers: { getPosition: this._ver, getRadius: (zoom || 0) < 15 } }));
      if (holes.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:holes", data: holes, pickable: true, stroked: true, filled: false, lineWidthUnits: "pixels", getLineWidth: 1.2,
        parameters: { depthTest: false }, getPolygon: (d) => ring3(d.ring, this._z(d) + 0.1),
        getLineColor: (d) => /confirmed/.test((d.photo && d.photo.verdict) || "") ? [255, 255, 255, 230] : [255, 150, 90, 220], updateTriggers: { getPolygon: this._ver } }));
      return L;
    }
    pick(info) {
      const id = info && info.layer && info.layer.id, o = info && info.object;
      if ((id === "pa:pools" || id === "pa:dots") && o) { this.sel = this.sel === o ? null : o; this._dirty(); this._render(); this.onUpdate(); return true; }
      return false;
    }
    tooltip(info) {
      const o = info && info.object, id = info && info.layer && info.layer.id; if (!o || !/^pa:/.test(id)) return null;
      if (o.akind === "holes") {
        const ph = o.photo || {}, li = o.lidar || {};
        return { html: `<div class="pt-tip"><b>LiDAR water hole</b> · ${esc(o.area_m2)} m²<br>${esc(ph.verdict || "no photo check")}${ph.ndwi != null ? " · NDWI " + esc(ph.ndwi) : ""}` +
          `<br><span class="s">${esc(li.project || "USGS 3DEP")} ${esc(li.year || "")} · laser returns missing over water · photo ${esc(ph.year || "")}</span></div>` };
      }
      const hist = Object.entries(o.states || {}).sort().map(([k, v]) => `<span style="color:rgb(${col(v)})">■</span>${esc(k.replace(/\D/g, ""))}`).join(" ");
      const own = o.parcel ? `${esc(o.parcel.address || "")}<br>inside its official lot (APN ${esc(o.parcel.apn)})` :
        o.owner ? `nearest building ${esc(o.owner.nearest_m)} m (next ${esc(o.owner.runner_up_m)} m) · no open lot lines · ${esc(o.owner.confidence)} confidence` :
        o.kind === "community" ? "community / shared pool" : "not inside any lot";
      return { html: `<div class="pt-tip"><b>Pool · ${esc((ST[o.latest_state] || [0, o.latest_state])[1])}</b> · ${esc(o.area_m2)} m²<br>${own}` +
        `<br>${hist}${o.green_years && o.green_years.length ? `<br>green in ${esc(o.green_years.join(", "))}` : ""}` +
        (o.check ? `<br><b style="color:#ffb347">needs a look:</b> ${esc(o.check)}` : "") +
        `<br><span class="s">NAIP aerial photos (public domain) · state = photo day only · click to light its house${o.parcel ? " and lot" : ""}</span></div>` };
    }

    bindPanel(el) {
      this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => { const k = e.target && e.target.dataset && e.target.dataset.pa; if (!k) return;
        if (e.target.checked) { this.on.add(k); this._index(k); } else { this.on.delete(k); if (this.sel && this.sel.area === k) this.sel = null; }
        this._dirty(); this._render(); this._schedule(0); this.onUpdate(); });
      this._render();
    }
    _render() {
      const el = this.panel; if (!el) return;
      if (!el.querySelector(".pt-st")) el.innerHTML = `<div class="tg">${AREAS.map((a) => `<label><input type="checkbox" data-pa="${a.slug}" ${this.on.has(a.slug) ? "checked" : ""}> ${esc(a.name)}</label>`).join("")}</div>` +
        `<div class="leg"></div><div class="note"></div><div class="pt-st"></div>`;
      for (const i of el.querySelectorAll("input[data-pa]")) i.checked = this.on.has(i.dataset.pa);
      el.querySelector(".leg").innerHTML = this.on.size ? `<div class="sub small">` + Object.values(ST).slice(0, 6).map(([c, t]) => `<span style="color:rgb(${c})">■</span> ${esc(t)}`).join(" · ") + " · ○ hollow ring = needs a look" +
        (this.on.has("scottsdale_lidar") ? ` · <span style="color:#fff">□</span> LiDAR hole, photo confirms water · <span style="color:rgb(255,150,90)">□</span> hole, photo says not water` : "") + `</div>` : "";
      el.querySelector(".note").textContent = this.on.size ? "Zoom in (street level) to load pools. Click a pool to light its house and official lot." : "Every pool found across a whole city.";
      const v = this.view, lines = [...this.on].map((k) => { const I = this.idx[k], a = AREAS.find((x) => x.slug === k);
        if (!I || I.state === "loading") return `${a.name}: loading`; if (I.state === "err") return `${a.name}: ${I.err}`;
        const tot = I.j.totals || {}, done = (I.j.tiles || []).filter((t) => !t.status || /done|ok|final/.test(t.status)).length;
        const n = tot.pools ?? tot.holes ?? tot.all ?? "?"; return `${a.name}: ${n} ${a.kind === "holes" ? "holes" : "pools"} · ${done}/${(I.j.tiles || []).length} tiles built`; });
      if (this.on.size && (!v || v.zoom < MINZ)) lines.push(`zoom in to street level (≥ ${MINZ}) to see them`);
      if (this.sel) lines.push(`selected: ${this.sel.parcel ? this.sel.parcel.address : "pool " + this.sel.id}`);
      const st = el.querySelector(".pt-st"); st.textContent = lines.join("\n"); st.style.whiteSpace = "pre-line";
    }
  }
  window.PoolsArea = PoolsArea;
})();

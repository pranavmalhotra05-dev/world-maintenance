/* Don't-knock zones (USA): gated and private-road communities from OpenStreetMap (usa/build_dont_knock.py).
   Ground outlines only. "gated" = a gate is mapped on its private roads; "private" = private-access roads, no gate mapped.
   A hint for door-to-door routes, not proof of an HOA no-soliciting rule. Classic script; defines window.DontKnock. */
(function () {
  "use strict";
  const COL = { gated: [255, 70, 70], private: [255, 175, 50] };
  const MINZ = 11, LABELZ = 15.5, ON_TOP = { depthWriteEnabled: false, depthCompare: "always" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hit = (a, b) => a && b && a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
  const km = (m) => (m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.round(m) + " m");
  const ha = (m2) => (m2 >= 1e6 ? (m2 / 1e6).toFixed(1) + " km²" : (m2 / 1e4).toFixed(1) + " ha");

  class DontKnock {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {}); this.on = false; this.idx = null; this.files = new Map(); this.zones = [];
      this.show = new Set(["gated", "private", "community", "complex"]); this.zoom = 0; this.bounds = null; this.state = "idle"; this.sel = null; this.panel = null;
    }
    getState() { return this.on ? { on: ["usa"], s: [...this.show] } : { on: [] }; }
    setState(o = {}) { if (Array.isArray(o.s)) this.show = new Set(o.s); if (Array.isArray(o.on) ? o.on.length : o.on) { this.on = true; this._index(); } this._render(); }
    credits() { return this.on ? "Don't-knock zones: © OpenStreetMap contributors (ODbL), extract via Geofabrik" : ""; }
    update(vs, { zoom, bounds, globe } = {}) { this.zoom = zoom || 0; this.bounds = globe ? null : bounds; this._load(); }
    async _index() {
      if (this.idx || this.state === "loading") return; this.state = "loading"; this._render();
      try { const r = await fetch("data/dontknock/index.json"); if (!r.ok) throw new Error("HTTP " + r.status); this.idx = await r.json(); this.state = "ok"; }
      catch (e) { this.state = "err: " + e.message; }
      this._render(); this._load();
    }
    _load() {
      if (!this.on || !this.idx || !this.bounds || this.zoom < MINZ - 0.5) return;
      for (const st of this.idx.states) {
        if (!hit(st.bbox, this.bounds)) continue;
        for (const t of st.tiles || [{ file: st.file, bbox: st.bbox }]) if (t.file && hit(t.bbox || st.bbox, this.bounds) && !this.files.has(t.file)) this._file(t.file);
      }
    }
    async _file(f) {
      this.files.set(f, "loading");
      try {
        const r = await fetch("data/dontknock/" + f); if (!r.ok) throw new Error("HTTP " + r.status); const j = await r.json();
        for (const z of j.zones) {
          let w = 180, s = 90, e = -180, n = -90, cx = 0, cy = 0;
          for (const [x, y] of z.ring) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; cx += x; cy += y; }
          z.bb = [w, s, e, n]; z.c = [cx / z.ring.length, cy / z.ring.length]; z.st = j.state; this.zones.push(z);
        }
        this.files.set(f, "ok");
      } catch (e) { this.files.set(f, "err"); console.warn("dontknock", f, e); }
      this._render(); this.onUpdate();
    }
    _vis() {
      const b = this.bounds; if (!b) return [];
      return this.zones.filter((z) => this.show.has(z.conf) && this.show.has(z.kind) && hit(z.bb, b));
    }
    layers({ globe, zoom, modelMatrix } = {}) {
      const D = window.deck; if (!D || !this.on || globe || (zoom || 0) < MINZ || !this.zones.length) return [];
      const mm = modelMatrix ? { modelMatrix } : {}, data = this._vis(), z = zoom || 0, L = [], sel = this.sel;
      const ring = (d) => d.ring.map(([x, y]) => [x, y, 1]);
      L.push(new D.PolygonLayer({ ...mm, id: "dk:zones", data, pickable: true, stroked: false, filled: true, parameters: ON_TOP,
        getPolygon: ring, getFillColor: (d) => [...COL[d.conf], d === sel ? 70 : 26], autoHighlight: true, highlightColor: [255, 255, 255, 40],
        updateTriggers: { getFillColor: sel && sel.id } }));
      L.push(new D.PathLayer({ ...mm, id: "dk:edge", data, pickable: false, parameters: ON_TOP, widthUnits: "pixels", jointRounded: true,
        getPath: ring, getWidth: (d) => (d === sel ? 3.5 : z < 14 ? 1.5 : 2.2), getColor: (d) => [...COL[d.conf], 235],
        getDashArray: (d) => (d.conf === "private" ? [5, 3] : [0, 0]), dashJustified: true,
        extensions: D.PathStyleExtension ? [new D.PathStyleExtension({ dash: true })] : [], updateTriggers: { getWidth: [sel && sel.id, z < 14] } }));
      if (z >= LABELZ) L.push(new D.TextLayer({ ...mm, id: "dk:names", data, billboard: true, parameters: ON_TOP, getPosition: (d) => [d.c[0], d.c[1], 1],
        getText: (d) => (d.conf === "gated" ? "GATED · DON'T KNOCK" : "PRIVATE ROADS · DON'T KNOCK"), getSize: 11, getColor: (d) => [...COL[d.conf], 240],
        fontFamily: "monospace", fontWeight: 700, outlineWidth: 2, outlineColor: [0, 0, 0, 230], fontSettings: { sdf: true } }));
      return L;
    }
    pick(info) {
      const id = info && info.layer && info.layer.id;
      if (id === "dk:zones" && info.object) { this.sel = this.sel === info.object ? null : info.object; this._render(); this.onUpdate(); return true; }
      return false;
    }
    _desc(d) {
      return `${d.conf === "gated" ? `Gated: ${d.gates} gate${d.gates === 1 ? "" : "s"} mapped on its private roads` : "Private roads (no gate mapped)"}` +
        `<br>${d.kind === "community" ? "Residential community" : "Gated complex (often apartments or condos)"} · ${ha(d.area_m2)} · ${km(d.road_m)} private road`;
    }
    tooltip(info) {
      const d = info && info.object; if (!d || !info.layer || info.layer.id !== "dk:zones") return null;
      return { html: `<div class="pt-tip"><b style="color:rgb(${COL[d.conf]})">Don't knock</b>${d.name ? " · " + esc(d.name) : ""}<br>${this._desc(d)}` +
        `<br><span class="s">OpenStreetMap ${esc(d.id)} · a hint, not proof of an HOA rule · click to pin</span></div>` };
    }
    bindPanel(el) {
      this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => {
        const ds = (e.target && e.target.dataset) || {};
        if (ds.dkon != null) { this.on = e.target.checked; if (this.on) this._index(); this._render(); this.onUpdate(); return; }
        if (ds.dks) { e.target.checked ? this.show.add(ds.dks) : this.show.delete(ds.dks); this._render(); this.onUpdate(); }
      });
      this._render();
    }
    _render() {
      const el = this.panel; if (!el) return;
      const t = this.idx && this.idx.totals, s = this.sel;
      const box = (k, label) => `<label><input type="checkbox" data-dks="${k}" ${this.show.has(k) ? "checked" : ""}> ${label}</label>`;
      el.innerHTML = `<div class="tg"><label><input type="checkbox" data-dkon ${this.on ? "checked" : ""}> Don't-knock zones (USA)</label></div>` +
        (this.on ? `<div class="tg" style="margin:4px 0 0 14px">` +
          box("gated", `<span style="color:rgb(${COL.gated})">■</span> Gated (gate mapped)`) + box("private", `<span style="color:rgb(${COL.private})">▪ ▪</span> Private roads, no gate`) +
          box("community", "Residential communities") + box("complex", "Gated complexes (apartments, condos)") + `</div>` : "") +
        `<div class="note">${this.on ? (this.state === "ok" ? `${t ? t.zones.toLocaleString() + " zones in 50 states + DC. " : ""}Zoom in to see them. Private roads are a strong hint, not proof of a no-soliciting rule.` : esc(this.state))
          : "Gated and private-road communities to skip when going door to door."}</div>` +
        (s ? `<div class="pt-st" style="white-space:normal"><b style="color:rgb(${COL[s.conf]})">Don't knock</b>${s.name ? " · " + esc(s.name) : ""}<br>${this._desc(s)}<br>` +
          `<a href="https://www.openstreetmap.org/${s.id[0] === "r" ? "relation" : "way"}/${esc(s.id.slice(1))}" target="_blank" rel="noopener noreferrer">OpenStreetMap ${esc(s.id)}</a></div>` : "");
    }
  }
  window.DontKnock = DontKnock;
})();

/* Partners: businesses around pools (Phoenix metro) from Overture Maps places (usa/build_partners.py).
   Competitors (pool service companies), pool stores, property managers / HOAs, vacation rentals, realtors, builders,
   landscapers. Business listings only. Classic script; defines window.Partners. Needs the deck.gl UMD global `deck`. */
(function () {
  "use strict";
  const COL = { competitor: [255, 90, 90], supply: [255, 160, 60], pm: [80, 200, 255], str: [190, 120, 255], realtor: [255, 220, 80], builder: [160, 220, 120], landscape: [90, 220, 150] };
  const DEF_ON = ["competitor", "pm", "str"];
  const MINZ = 10.5, ON_TOP = { depthWriteEnabled: false, depthCompare: "always" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const CAT = (c) => String(c || "").replace(/_/g, " ");

  class Partners {
    constructor(opts = {}) { this.onUpdate = opts.onUpdate || (() => {}); this.on = false; this.groups = new Set(DEF_ON); this.d = null; this.state = "idle"; this.sel = null; this.panel = null; this.zoom = 0; }
    getState() { return this.on ? { on: ["phoenix_metro"], g: [...this.groups] } : { on: [] }; }
    setState(o = {}) { if (Array.isArray(o.g)) this.groups = new Set(o.g); if (Array.isArray(o.on) ? o.on.length : o.on) { this.on = true; this._load(); } this._render(); }
    credits() { return this.on ? "Businesses: Overture Maps places (CDLA-Permissive-2.0 / Apache-2.0)" : ""; }
    update(vs, { zoom } = {}) { this.zoom = zoom || 0; }
    async _load() {
      if (this.d || this.state === "loading") return; this.state = "loading"; this._render();
      try { const r = await fetch("data/partners/phoenix_metro.json"); if (!r.ok) throw new Error("HTTP " + r.status); this.d = await r.json(); this.state = "ok"; }
      catch (e) { this.state = "err: " + e.message; }
      this._render(); this.onUpdate();
    }
    _rows() { return this.d ? this.d.rows.filter((r) => this.groups.has(r[2])) : []; }
    layers({ globe, zoom, modelMatrix } = {}) {
      const D = window.deck; if (!D || !this.on || !this.d || globe || (zoom || 0) < MINZ) return [];
      const mm = modelMatrix ? { modelMatrix } : {}, data = this._rows(), L = [], z = zoom || 0;
      L.push(new D.ScatterplotLayer({ ...mm, id: "bz:dots", data, pickable: true, billboard: true, radiusUnits: "pixels", parameters: ON_TOP,
        getPosition: (r) => [r[0], r[1], 2], getRadius: (r) => (r === this.sel ? 7 : z < 13 ? 2.5 : 4.5), stroked: true, lineWidthUnits: "pixels", getLineWidth: (r) => (r === this.sel ? 2 : 1),
        getFillColor: (r) => [...COL[r[2]], 235], getLineColor: [10, 10, 14, 220], updateTriggers: { getRadius: [this.sel && this.sel[3], z < 13], getLineWidth: this.sel && this.sel[3] } }));
      if (z >= 16) L.push(new D.TextLayer({ ...mm, id: "bz:names", data, billboard: true, parameters: ON_TOP, getPosition: (r) => [r[0], r[1], 2], getText: (r) => r[3],
        getSize: 11, getColor: (r) => [...COL[r[2]], 230], getPixelOffset: [0, -12], fontFamily: "monospace", outlineWidth: 2, outlineColor: [0, 0, 0, 220], fontSettings: { sdf: true },
        characterSet: "auto" }));
      return L;
    }
    pick(info) {
      const id = info && info.layer && info.layer.id;
      if (id === "bz:dots" && info.object) { this.sel = this.sel === info.object ? null : info.object; this._render(); this.onUpdate(); return true; }
      return false;
    }
    tooltip(info) {
      const r = info && info.object; if (!r || !info.layer || info.layer.id !== "bz:dots") return null;
      return { html: `<div class="pt-tip"><b>${esc(r[3])}</b><br><span style="color:rgb(${COL[r[2]]})">■</span> ${esc(this.d.groups[r[2]])} · ${esc(CAT(r[4]))}` +
        (r[5] ? `<br>${esc(r[5])}` : "") + (r[6] ? `<br>${esc(r[6])}` : "") + (r[7] ? `<br>${esc(r[7])}` : "") +
        `<br><span class="s">Overture Maps places (${esc((r[9] || []).filter((x) => !/^Overture/.test(x)).join(", ") || "Overture")}) · confidence ${esc(r[8])} · click to pin</span></div>` };
    }
    bindPanel(el) {
      this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => {
        const ds = (e.target && e.target.dataset) || {};
        if (ds.bzon != null) { this.on = e.target.checked; if (this.on) this._load(); this._render(); this.onUpdate(); return; }
        if (ds.bzg) { e.target.checked ? this.groups.add(ds.bzg) : this.groups.delete(ds.bzg); this._render(); this.onUpdate(); }
      });
      this._render();
    }
    _render() {
      const el = this.panel; if (!el) return;
      const g = this.d ? this.d.groups : { competitor: "Pool service companies (competitors)", supply: "Pool and spa stores", pm: "Property managers and HOAs", str: "Vacation rentals and rental agents", realtor: "Real estate agents and services", builder: "Home builders", landscape: "Landscapers (referral partners)" };
      const cnt = this.d ? this.d.counts : {}, s = this.sel;
      el.innerHTML = `<div class="tg"><label><input type="checkbox" data-bzon ${this.on ? "checked" : ""}> Phoenix metro businesses</label></div>` +
        (this.on ? `<div class="tg" style="margin:4px 0 0 14px">` + Object.keys(COL).map((k) =>
          `<label><input type="checkbox" data-bzg="${k}" ${this.groups.has(k) ? "checked" : ""}> <span style="color:rgb(${COL[k]})">■</span> ${esc(g[k])} <span class="sub small">${cnt[k] ?? ""}</span></label>`).join("") + `</div>` : "") +
        `<div class="note">${this.on ? (this.state === "ok" ? "Zoom in to see them. Click one to pin its contact details." : esc(this.state)) : "Pool companies, property managers, rentals and realtors: partners and competitors."}</div>` +
        (s ? `<div class="pt-st" style="white-space:normal"><b>${esc(s[3])}</b><br>${esc(CAT(s[4]))}${s[5] ? "<br>" + esc(s[5]) : ""}${s[6] ? "<br>" + esc(s[6]) : ""}` +
          `${s[7] ? `<br><a href="${esc(/^https?:/.test(s[7]) ? s[7] : "https://" + s[7])}" target="_blank" rel="noopener noreferrer">${esc(s[7])}</a>` : ""}</div>` : "");
    }
  }
  window.Partners = Partners;
})();

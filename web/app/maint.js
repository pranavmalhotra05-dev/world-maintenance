/* maint.js — "Maintenance" view for a loaded 3D block, one tab per trade (classic script; defines window.Maintenance).
   Needs the deck.gl 9.1.x UMD build on globalThis.deck. Styling: .bm-panel (block-panel.css) + .mnt-* rules injected once.

   const mt = new Maintenance({ onUpdate, baseUrl: "../usa/blocks/", pool })   // pool: optional PoolService instance
   mt.load(slug, meta)          -> Promise  fetches <slug>/maintenance.json, optional <slug>/snowmelt.json and (for pool shapes)
                                   <slug>/pool_service.json when no PoolService is present. Missing files are fine.
   mt.unload(slug)
   mt.setHouses(slug, houses)   houses.json array (rings, eave/height, addresses; also passed to PoolService)
   mt.setData(slug, doc, snow)  inject a maintenance.json (and snowmelt.json) object directly (tests / pre-fetched)
   mt.setTrade(id) / mt.trade   the active tab
   mt.highlight(slug, houseId, kind)  kind "hover" | "select"; houseId null clears. The house ring and its pool light up together.
   mt.layers({ slug, coordinateOrigin, zBase, houses, zOf }) -> deck Layer[]  ids "mnt:<slug>:*"
                                   coordinateOrigin = [lon, lat, zOffset] (block.js frame, METER_OFFSETS); no z -> zBase.
                                   zOf(x, y) = ground z in block-local metres (block.js Block.groundLocal); missing -> 0.
   mt.pick(info) / mt.tooltip(info)
   mt.houseHtml(slug, houseId)  dossier section: every trade as a compact row (score bar, top 2 signals, next), expandable
   mt.blockHtml(slug)           trade tabs, summary, top houses, climate, live NWS triggers, sources
   mt.bind(el)                  delegated clicks: [data-mnt-trade] -> setTrade, [data-mnt-house="slug|id"] -> onUpdate({type:"focusHouse"}),
                                   [data-mnt-live="slug"] -> live(slug)
   mt.live(slug, lat, lon)      -> Promise  NWS api.weather.gov alerts + gridpoint forecast -> trade triggers (cached 30 min)
   mt.credits()                 short attribution string
   mt.enabled / setEnabled(bool)
   onUpdate(evt)                evt.type: "load" | "unload" | "trade" | "live" | "focusHouse" | "highlight" | "enabled" | "pool"
*/
(function () {
  "use strict";
  const D = () => globalThis.deck;
  const DASH = "—";
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : typeof v === "string" && v.trim() !== "" && isFinite(+v) ? +v : null);
  const arr = (v) => (Array.isArray(v) ? v : []);
  const isObj = (o) => o && typeof o === "object" && !Array.isArray(o);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
  const val = (v, unit) => {
    if (v == null || v === "" || (typeof v === "number" && !isFinite(v))) return DASH;
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (Array.isArray(v)) return v.length ? esc(v.join(", ")) : DASH;
    if (isObj(v)) return esc(JSON.stringify(v));
    const s = typeof v === "number" ? fmt.format(v) : String(v);
    return esc(s) + (unit ? " " + esc(unit) : "");
  };
  const reasonText = (r) => (typeof r === "string" ? r : [r?.label, r?.text, r?.reason].filter(Boolean).join(": "));
  const okRing = (r) => Array.isArray(r) && r.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1])).length >= 3;
  const cleanRing = (r) => r.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1])).map((p) => [+p[0], +p[1]]);
  const hid = (h, i) => String(h?.house_id ?? h?.id ?? i);

  const TRADES = [
    ["roof", "Roof"], ["gutters", "Gutters"], ["trees", "Trees"], ["sidewalk_leaves", "Sidewalk & leaves"], ["snow_ice", "Snow & ice"],
    ["heating", "Heating"], ["cooling", "Cooling"], ["solar_clean", "Solar cleaning"], ["pressure_wash", "Pressure wash"], ["foundation", "Foundation"],
    ["flood_water", "Flood & water"], ["radon", "Radon"], ["pests", "Pests"], ["wildfire", "Wildfire"], ["pool", "Pool"],
  ];
  const TRADE_LABEL = Object.fromEntries(TRADES);

  // ---------------------------------------------------------------- colours (calm ramp: dim accent -> accent -> soft warn)
  const hex = (h) => { const m = /^#?([0-9a-f]{6})$/i.exec(String(h || "")); return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null; };
  function themeRGB(name, fb) {
    const T = globalThis.Themes;
    try { const c = T?.rgb?.(name); if (Array.isArray(c) && c.length >= 3) return c.slice(0, 3).map(Number); } catch {}
    try { const v = getComputedStyle(document.documentElement).getPropertyValue("--" + (name === "accent" ? "cyan" : name === "warn" ? "amber" : name)).trim(); const c = hex(v); if (c) return c; } catch {}
    return fb;
  }
  const mix = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
  function ramp(t) {   // t 0..1 (score / 100)
    const acc = themeRGB("accent", [79, 220, 255]), warm = themeRGB("warn", [255, 184, 77]), low = mix(acc, [40, 60, 90], 0.55);
    t = Math.max(0, Math.min(1, t));
    return t < 0.55 ? mix(low, acc, t / 0.55) : mix(acc, mix(warm, [255, 255, 255], 0.15), (t - 0.55) / 0.45);
  }
  const rgbCss = (c) => `rgb(${c.join(",")})`;
  const HI = { hover: [255, 255, 255], select: null };   // select uses --sel (#ffd24d) by default
  const selCol = () => { try { const c = hex(getComputedStyle(document.documentElement).getPropertyValue("--sel").trim()); if (c) return c; } catch {} return [255, 210, 77]; };

  // ---------------------------------------------------------------- CSS
  function injectCss() {
    if (typeof document === "undefined" || document.getElementById("maint-css")) return;
    const s = document.createElement("style"); s.id = "maint-css";
    s.textContent = `
.mnt{--mc:var(--bm-cyan,#4fdcff)}
.mnt .mnt-tabs{display:flex;flex-wrap:wrap;gap:3px;margin:6px 0 8px}
.mnt .mnt-tabs button{background:none;border:1px solid var(--bm-edge,rgba(120,190,255,.22));color:var(--bm-dim,#8b9bb0);font:10.5px var(--bm-mono,monospace);letter-spacing:.04em;padding:2px 7px;border-radius:99px;cursor:pointer}
.mnt .mnt-tabs button:hover{color:var(--bm-ink,#e2ebf5);border-color:var(--bm-cyan,#4fdcff)}
.mnt .mnt-tabs button.on{color:var(--bm-cyan,#4fdcff);border-color:var(--bm-cyan,#4fdcff);box-shadow:0 0 8px color-mix(in srgb,var(--bm-cyan,#4fdcff) 35%,transparent)}
.mnt .mnt-tabs button.nodata{opacity:.45}
.mnt .mnt-tabs button i{font-style:normal;margin-left:4px;color:var(--bm-amber,#ffb84d)}
.mnt .mnt-bar{display:inline-block;position:relative;width:64px;height:5px;vertical-align:middle;flex:none}
.mnt .mnt-bar::before,.mnt .mnt-bar::after{content:"";position:absolute;left:0;top:0;bottom:0;background-image:radial-gradient(circle,currentColor 0 1px,transparent 1.3px);background-size:4px 5px;background-repeat:repeat-x}
.mnt .mnt-bar::before{right:0;color:var(--bm-dim,#8b9bb0);opacity:.35}
.mnt .mnt-bar::after{width:var(--v,0%);color:var(--c,var(--bm-cyan));filter:drop-shadow(0 0 2px var(--c,var(--bm-cyan)))}
.mnt .mnt-sc{font:600 12px var(--bm-mono,monospace);min-width:22px;text-align:right;color:var(--c,var(--bm-dim))}
.mnt details.mnt-row{border-bottom:1px solid color-mix(in srgb,var(--bm-edge,rgba(120,190,255,.22)) 45%,transparent);padding:2px 0}
.mnt details.mnt-row>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:96px 64px 26px 1fr;gap:2px 8px;align-items:center;font-size:12px;padding:2px 0}
.mnt details.mnt-row>summary::-webkit-details-marker{display:none}
.mnt details.mnt-row>summary .t{color:var(--bm-ink,#e2ebf5);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mnt details.mnt-row>summary .t::before{content:"▸ ";color:var(--bm-dim)}
.mnt details.mnt-row[open]>summary .t::before{content:"▾ "}
.mnt details.mnt-row.on>summary .t{color:var(--bm-cyan,#4fdcff)}
.mnt details.mnt-row>summary .sg{font:11px var(--bm-mono,monospace);color:var(--bm-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mnt details.mnt-row>summary .nx{grid-column:2/-1;font-size:11.5px;color:var(--bm-ink)}
.mnt details.mnt-row>summary .nx b{font:500 10px var(--bm-mono,monospace);color:var(--bm-dim);letter-spacing:.12em;margin-right:5px}
.mnt .mnt-body{padding:4px 0 6px 12px;font-size:12px}
.mnt .mnt-body table{width:100%;border-collapse:collapse;font:11.5px var(--bm-mono,monospace)}
.mnt .mnt-body td{padding:2px 4px 2px 0;vertical-align:top;border-bottom:1px solid color-mix(in srgb,var(--bm-edge) 30%,transparent)}
.mnt .mnt-body td.v{text-align:right;color:var(--bm-ink);white-space:nowrap}
.mnt .mnt-body td.s{color:var(--bm-dim);font-size:10.5px}
.mnt .mnt-body ul{margin:4px 0;padding-left:16px}
.mnt .mnt-trig{list-style:none;margin:4px 0;padding:0;font-size:12px}
.mnt .mnt-trig li{padding:3px 0 3px 10px;border-bottom:1px solid color-mix(in srgb,var(--bm-edge) 40%,transparent);position:relative}
.mnt .mnt-trig li::before{content:"";position:absolute;left:0;top:9px;width:4px;height:4px;border-radius:50%;background:var(--bm-cyan);box-shadow:0 0 5px var(--bm-cyan)}
.mnt .mnt-trig li.warn::before{background:var(--bm-amber);box-shadow:0 0 5px var(--bm-amber)}
.mnt .mnt-trig li.bad::before{background:var(--bm-red);box-shadow:0 0 5px var(--bm-red)}
.mnt .mnt-trig li.off{opacity:.55}
.mnt .mnt-trig small{display:block;color:var(--bm-dim);font:10.5px var(--bm-mono,monospace)}
.mnt .mnt-btn{background:none;border:1px solid var(--bm-edge);color:var(--bm-cyan);font:10.5px var(--bm-mono,monospace);padding:1px 7px;border-radius:2px;cursor:pointer}
.mnt .mnt-btn:hover{border-color:var(--bm-cyan)}
.mnt .mnt-flag{font:10px var(--bm-mono,monospace);color:var(--bm-amber);margin-left:4px}
.mnt .mnt-legend{display:flex;align-items:center;gap:6px;font:10.5px var(--bm-mono,monospace);color:var(--bm-dim);margin:2px 0 6px}
.mnt .mnt-legend i{display:inline-block;width:90px;height:4px;border-radius:2px}
.mnt .mnt-pool{margin-top:6px;border-top:1px dashed color-mix(in srgb,var(--bm-edge) 60%,transparent);padding-top:4px}
`;
    document.head.appendChild(s);
  }

  // ---------------------------------------------------------------- NWS (api.weather.gov, US public domain)
  const NWS = "https://api.weather.gov";
  const getJSON = (u) => fetch(u, { headers: { Accept: "application/geo+json" } }).then((r) => { if (!r.ok) throw new Error(`${r.status} ${u}`); return r.json(); });
  function durH(s) { const m = /P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?/.exec(String(s || "")); return m ? (+m[1] || 0) * 24 + (+m[2] || 0) + ((+m[3] || 0) > 0 ? 1 : 0) : 1; }
  function series(prop, conv, expand) {
    const out = []; for (const e of arr(prop?.values)) { const [a, b] = String(e.validTime || "").split("/"), t0 = Date.parse(a), v = num(e.value); if (!isFinite(t0) || v == null) continue;
      const n = expand ? Math.min(48, Math.max(1, durH(b))) : 1; for (let k = 0; k < n; k++) out.push({ t: new Date(t0 + k * 3600e3), v: conv(v) }); }
    return out;
  }
  const toF = (uom) => (/degC/.test(uom || "") ? (v) => v * 9 / 5 + 32 : (v) => v);
  const toMph = (uom) => (/km_h/.test(uom || "") ? (v) => v * 0.621371 : /m_s/.test(uom || "") ? (v) => v * 2.23694 : /kt|knot/.test(uom || "") ? (v) => v * 1.15078 : (v) => v);
  const toIn = (uom) => (/mm/.test(uom || "") ? (v) => v / 25.4 : /cm/.test(uom || "") ? (v) => v / 2.54 : /:m$/.test(uom || "") ? (v) => v * 39.37 : (v) => v);
  // [event regex, level, trades, what it means]
  const ALERTS = [
    [/blizzard|winter storm|winter weather|ice storm|lake effect snow|heavy snow/i, "bad", ["snow_ice", "roof", "trees", "heating"], "snow and ice load: clear walks, watch ice dams and limbs"],
    [/freeze|frost|hard freeze|wind chill|extreme cold|cold weather/i, "warn", ["heating", "pool", "flood_water"], "freeze: check heat, protect pipes and pool equipment"],
    [/high wind|wind advisory|extreme wind|tropical storm|hurricane/i, "warn", ["trees", "gutters", "roof"], "high winds: limbs, gutters and loose roofing"],
    [/severe thunderstorm|tornado/i, "bad", ["roof", "trees", "gutters"], "storm damage likely: roof and tree checks after it passes"],
    [/excessive heat|extreme heat|heat advisory|heat warning/i, "warn", ["cooling", "pool"], "heat: AC demand and pool chemistry"],
    [/flood|flash flood|coastal flood/i, "bad", ["flood_water", "foundation", "gutters"], "flooding: drainage, sump and foundation checks"],
    [/red flag|fire weather/i, "bad", ["wildfire"], "fire weather: defensible space, clear gutters of dry debris"],
    [/smoke|air quality/i, "warn", ["cooling", "solar_clean"], "smoke: HVAC filters, panel soiling"],
    [/dust/i, "warn", ["solar_clean", "pressure_wash", "pool"], "dust: panel and surface cleaning, pool debris"],
  ];

  // =====================================================================================================================
  class Maintenance {
    constructor({ onUpdate, baseUrl = "../usa/blocks/", pool, enabled = true, trade = "roof" } = {}) {
      this.onUpdate = typeof onUpdate === "function" ? onUpdate : () => {};
      this.baseUrl = /\/$/.test(baseUrl) ? baseUrl : baseUrl + "/";
      this.enabled = !!enabled; this.trade = TRADE_LABEL[trade] ? trade : "roof";
      this.S = new Map();               // slug -> {meta, doc, snow, houses, poolDoc, byId, snowBy}
      this._liveCache = new Map();      // "lat,lon" -> {t, v} | {p}
      this._liveBy = new Map();         // slug -> {state, v, error}
      this._open = new Set();           // expanded rows "slug|house|trade"
      this.hi = { hover: null, select: null };
      this.usedNws = false;
      this.pool = pool || null; this._ownPool = false;
      if (!this.pool && typeof globalThis.PoolService === "function") {
        try { this.pool = new globalThis.PoolService({ baseUrl: this.baseUrl, onUpdate: (e) => this._poolEvt(e) }); this._ownPool = true; } catch (e) { console.warn("Maintenance: PoolService", e); this.pool = null; }
      }
      injectCss();
    }
    _emit(e) { try { this.onUpdate(e); } catch (err) { console.warn("Maintenance onUpdate:", err); } }
    _poolEvt(e) {
      if (!e) return;
      if (e.type === "selectHouse") return this._emit({ type: "focusHouse", slug: e.slug, houseId: String(e.houseId) });
      this._emit({ ...e, type: "pool", poolType: e.type });
    }
    _s(slug) { if (!this.S.has(slug)) this.S.set(slug, { meta: null, doc: null, snow: null, houses: null, poolDoc: null }); return this.S.get(slug); }
    setEnabled(on) { on = !!on; if (on === this.enabled) return; this.enabled = on; this._emit({ type: "enabled", enabled: on }); }
    setTrade(id) { id = String(id || ""); if (!TRADE_LABEL[id] && !this._tradeList().some((t) => t.id === id)) return; if (id === this.trade) return; this.trade = id; this._emit({ type: "trade", trade: id }); }
    highlight(slug, houseId, kind = "select") {
      const k = kind === "hover" ? "hover" : "select", v = houseId == null || houseId === "" ? null : { slug: String(slug), houseId: String(houseId) };
      const cur = this.hi[k]; if ((cur && v && cur.slug === v.slug && cur.houseId === v.houseId) || (!cur && !v)) return;
      this.hi[k] = v; this._emit({ type: "highlight", kind: k, slug, houseId: v?.houseId ?? null });
    }

    // ------------------------------------------------------------ data
    _fetchOpt(slug, f) {
      const s = this.S.get(slug), files = arr(s?.meta?.files).map(String);
      const url = this.baseUrl + encodeURIComponent(slug) + "/";
      const get = () => fetch(url + f).then((r) => (r.ok ? r.text() : "")).then((t) => { try { return t ? JSON.parse(t) : null; } catch { return null; } }).catch(() => null);
      if (files.includes(f)) return get();
      // a static folder listing (python http.server) tells us whether the file exists, so a missing file never 404s
      s._list ||= fetch(url).then((r) => (r.ok ? r.text() : "")).then((t) => (/<a\s[^>]*href=/i.test(t) ? t : null)).catch(() => null);
      return s._list.then((t) => (t != null && !t.includes(`href="${f}"`) ? null : get()));
    }
    load(slug, meta) {
      const s = this._s(slug); if (meta) s.meta = meta;
      if (this.pool && this._ownPool) { try { this.pool.load(slug, meta); } catch {} }
      if (s.loadP) return s.loadP;
      s.loadP = Promise.all([this._fetchOpt(slug, "maintenance.json"), this._fetchOpt(slug, "snowmelt.json"), this.pool ? null : this._fetchOpt(slug, "pool_service.json")])
        .then(([doc, snow, pd]) => {
          if (this.S.get(slug) !== s) return null;
          if (isObj(doc) && !s.doc) this._setDoc(s, doc);
          if (isObj(snow) && !s.snow) this._setSnow(s, snow);
          if (isObj(pd) && Array.isArray(pd.pools)) s.poolDoc = pd;
          this._emit({ type: "load", slug, found: !!s.doc }); return s.doc;
        });
      return s.loadP;
    }
    unload(slug) { if (this.pool && this._ownPool) { try { this.pool.unload(slug); } catch {} } if (!this.S.has(slug)) return; this.S.delete(slug); this._liveBy.delete(slug); this._emit({ type: "unload", slug }); }
    setHouses(slug, houses) { const s = this._s(slug); s.houses = Array.isArray(houses) ? houses : null; s._geo = null; if (this.pool?.setHouses) { try { this.pool.setHouses(slug, houses); } catch {} } }
    setData(slug, doc, snow) { const s = this._s(slug); this._setDoc(s, isObj(doc) ? doc : null); if (snow !== undefined) this._setSnow(s, isObj(snow) ? snow : null); s.loadP = s.loadP || Promise.resolve(s.doc); this._emit({ type: "load", slug, found: !!s.doc }); }
    _setDoc(s, doc) {
      s.doc = doc; s.byId = new Map();
      if (!doc) return;
      arr(doc.houses).forEach((h, i) => { if (isObj(h)) s.byId.set(hid(h, i), h); });
      // scores are 0..100; a doc written on 0..1 is scaled
      let mx = 0; for (const h of s.byId.values()) for (const t of Object.values(isObj(h.trades) ? h.trades : {})) { const v = num(t?.score); if (v != null) mx = Math.max(mx, v); }
      s.scale = mx > 0 && mx <= 1 ? 100 : 1;
    }
    _setSnow(s, snow) { s.snow = snow; s.snowBy = new Map(); if (snow) arr(snow.houses).forEach((h, i) => { if (isObj(h)) s.snowBy.set(hid(h, i), h); }); }
    _tradeList(slug) {
      const s = slug ? this.S.get(slug) : null, fromDoc = arr(s?.doc?.trades).filter((t) => isObj(t) && t.id);
      if (fromDoc.length) return fromDoc.map((t) => ({ id: String(t.id), label: t.label || TRADE_LABEL[t.id] || String(t.id).replace(/_/g, " "), blurb: t.blurb || "" }));
      return TRADES.map(([id, label]) => ({ id, label, blurb: "" }));
    }
    _ht(slug, houseId, trade) { const s = this.S.get(slug), h = s?.byId?.get(String(houseId)); const t = h && isObj(h.trades) ? h.trades[trade] : null; return isObj(t) ? t : null; }
    score(slug, houseId, trade = this.trade) { const s = this.S.get(slug), t = this._ht(slug, houseId, trade), v = num(t?.score); return v == null ? null : Math.max(0, Math.min(100, v * (s?.scale || 1))); }
    _houseRec(slug, houseId) { const s = this.S.get(slug); return arr(s?.houses).find((h, i) => hid(h, i) === String(houseId)) || null; }
    _addr(slug, houseId) { const s = this.S.get(slug); return s?.byId?.get(String(houseId))?.address || this._houseRec(slug, houseId)?.address || null; }

    // pool outline for a house: PoolService model > pool_service.json > houses.json pool.outline. Never a circle.
    _poolOutline(slug, houseId) {
      const id = String(houseId), s = this.S.get(slug);
      try { const M = this.pool?.model?.(slug); const p = M?.pools?.find((q) => String(q.house_id) === id && okRing(q.outline)); if (p) return cleanRing(p.outline); } catch {}
      const pd = arr(s?.poolDoc?.pools).find((q) => String(q?.house_id) === id && okRing(q.outline)); if (pd) return cleanRing(pd.outline);
      const h = this._houseRec(slug, id); if (okRing(h?.pool?.outline)) return cleanRing(h.pool.outline);
      return null;
    }

    // ------------------------------------------------------------ deck layers
    layers({ slug, coordinateOrigin, zBase, houses, zOf, lotOf } = {}) {
      const dk = D(), s = this.S.get(slug); if (!this.enabled || !dk || !s) return [];
      const o = arr(coordinateOrigin); if (o.length < 2) return [];
      const O = [+o[0], +o[1], num(o[2]) ?? num(zBase) ?? 0];
      const H = arr(houses).length ? houses : arr(s.houses); if (!H.length && !s.byId?.size) return [];
      if (houses && houses !== s.houses) { s.houses = houses; s._geo = null; }
      const gz = (x, y) => { try { return num(typeof zOf === "function" ? zOf(x, y) : null) ?? 0; } catch { return 0; } };
      const key = O.join(",") + "|" + (typeof zOf === "function" ? "z" : "0") + "|" + (typeof lotOf === "function" ? "l" : "") + "|" + H.length;
      if (s._geoKey !== key || !s._geo) {
        s._geoKey = key;
        s._geo = H.map((h, i) => {
          if (!okRing(h?.ring)) return null;
          // a property is the lot (building + yard + drive), drawn ON THE GROUND from real parcel lines only.
          // No parcel data -> no outline at all: a thin marker stands at the house instead (no invented lot shape).
          const lot = typeof lotOf === "function" ? (() => { try { const r = lotOf(h); return okRing(r) ? cleanRing(r) : null; } catch { return null; } })() : null;
          if (lot) { const path = groundPath(lot, gz, 0.15, 0.8); return { id: hid(h, i), path, dots: path.filter((_, k) => k % 2 === 0), fill: path, lot: true }; }
          const ring = cleanRing(h.ring), g0 = Math.min(...ring.map(([x, y]) => gz(x, y))), top = num(h.height_m) ?? num(h.eave_m) ?? 6;
          let cx = 0, cy = 0; for (const [x, y] of ring) { cx += x; cy += y; } cx /= ring.length; cy /= ring.length;
          const zt = g0 + top + 3;
          return { id: hid(h, i), path: [[cx, cy, gz(cx, cy) + 0.1], [cx, cy, zt]], dots: [[cx, cy, zt]], lot: false };
        }).filter(Boolean);
        s._pgeo = new Map();
      }
      const poolGeo = (id) => {
        if (!s._pgeo.has(id)) { const r = this._poolOutline(slug, id); s._pgeo.set(id, r ? (() => { const z = Math.min(...r.map(([x, y]) => gz(x, y))) + 0.3; return { path: closed(r, z), dots: dotsOf(r, z, 0.7) }; })() : null); }
        return s._pgeo.get(id);
      };
      const tr = this.trade, base = { coordinateSystem: dk.COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: O, mntSlug: slug, parameters: { depthWriteEnabled: false } };
      const id = (n) => `mnt:${slug}:${n}`, ly = [];
      // scored objects: the house ring plus its pool outline, in one colour
      const items = [];
      for (const g of s._geo) { const sc = this.score(slug, g.id, tr); if (sc == null) continue; const c = ramp(sc / 100); items.push({ houseId: g.id, score: sc, c, path: g.path, dots: g.dots, fill: g.fill, kind: "house" }); const p = poolGeo(g.id); if (p) items.push({ houseId: g.id, score: sc, c, path: p.path, dots: p.dots, kind: "pool" }); }
      // hover / selection highlight
      const hl = [];
      for (const k of ["select", "hover"]) { const v = this.hi[k]; if (!v || v.slug !== slug) continue; const g = s._geo.find((q) => q.id === v.houseId); if (!g) continue; const c = k === "hover" ? HI.hover : selCol();
        hl.push({ houseId: g.id, c, path: g.path, dots: g.dots, fill: g.fill, kind: "house", hl: k }); const p = poolGeo(g.id); if (p) hl.push({ houseId: g.id, c, path: p.path, dots: p.dots, kind: "pool", hl: k }); }
      const ut = { getColor: tr + "|" + (globalThis.Themes?.current?.().id || ""), getFillColor: tr + "|" + (globalThis.Themes?.current?.().id || "") };
      const stack = (data, tag, wide) => {
        if (!data.length) return;
        const lots = data.filter((d) => d.fill);
        if (lots.length) ly.push(new dk.PolygonLayer({ ...base, id: id(tag + "-lot"), data: lots, getPolygon: (d) => d.fill, filled: true, stroked: false, getFillColor: (d) => [...d.c, wide ? 46 : 30], pickable: true, mntRole: "house", updateTriggers: ut }));
        ly.push(new dk.PathLayer({ ...base, id: id(tag + "-halo"), data, getPath: (d) => d.path, getColor: (d) => [...d.c, wide ? 60 : 42], getWidth: wide ? 9 : 7, widthUnits: "pixels", jointRounded: true, capRounded: true, updateTriggers: ut }));
        ly.push(new dk.PathLayer({ ...base, id: id(tag + "-glow"), data, getPath: (d) => d.path, getColor: (d) => [...d.c, 110], getWidth: 2.5, widthUnits: "pixels", jointRounded: true, updateTriggers: ut }));
        ly.push(new dk.PathLayer({ ...base, id: id(tag + "-edge"), data, getPath: (d) => d.path, getColor: (d) => [...d.c.map((x) => Math.min(255, x + 50)), 235], getWidth: 1, widthUnits: "pixels", pickable: true, mntRole: "house", updateTriggers: ut }));
        ly.push(new dk.ScatterplotLayer({ ...base, id: id(tag + "-dots"), data: data.flatMap((d) => d.dots.map((pos) => ({ pos, c: d.c }))), getPosition: (d) => d.pos, getRadius: 1.2, radiusUnits: "pixels", billboard: true,
          getFillColor: (d) => [...mix(d.c, [255, 255, 255], 0.55), 220], updateTriggers: ut }));
      };
      stack(items, "score", false);
      stack(hl, "hl", true);
      // pool tab: PoolService draws its own lens on top
      if (tr === "pool" && this.pool?.layers) { try { ly.push(...(this.pool.layers({ slug, coordinateOrigin: O, zBase, zOf }) || [])); } catch (e) { console.warn("Maintenance pool layers", e); } }
      return ly;
    }
    pick(info) {
      const o = info?.object, L = info?.layer; if (!o || !L?.props?.mntRole || o.houseId == null) return null;
      const r = { type: "focusHouse", slug: L.props.mntSlug, houseId: String(o.houseId) }; this._emit(r); return r;
    }
    tooltip(info) {
      const o = info?.object, L = info?.layer; if (!o || !L?.props?.mntRole) return null;
      const slug = L.props.mntSlug, t = this._ht(slug, o.houseId, this.trade), lab = this._tradeList(slug).find((x) => x.id === this.trade)?.label || this.trade;
      const sg = arr(t?.signals).slice(0, 2).map((g) => `${g.label}: ${stripTags(val(g.value, g.unit))}`).join("\n");
      return `${this._addr(slug, o.houseId) || "House " + o.houseId}${o.kind === "pool" ? " · pool" : ""}\n${lab}: ${o.score != null ? Math.round(o.score) : DASH}${sg ? "\n" + sg : ""}`;
    }

    // ------------------------------------------------------------ house dossier
    houseHtml(slug, houseId) {
      const s = this.S.get(slug); if (!this.enabled || !s) return "";
      const id = String(houseId), list = this._tradeList(slug), L = this._liveBy.get(slug)?.v;
      if (!s.doc) return s.loadP ? `<section class="mnt"><h4>Maintenance</h4><div class="sub small">No maintenance file for this block yet.</div>${this._poolHouse(slug, id, true)}</section>` : "";
      const rows = [], none = [];
      const order = list.map((t) => ({ t, sc: this.score(slug, id, t.id), ht: this._ht(slug, id, t.id) }));
      order.sort((a, b) => (a.t.id === this.trade ? -1 : b.t.id === this.trade ? 1 : (b.sc ?? -1) - (a.sc ?? -1)));
      for (const { t, sc, ht } of order) {
        const poolHtml = t.id === "pool" ? this._poolHouse(slug, id, false) : "";
        if (!ht && !poolHtml && !(t.id === "snow_ice" && s.snowBy?.get(id))) { none.push(t.label); continue; }
        const c = sc != null ? rgbCss(ramp(sc / 100)) : "";
        const sig = arr(ht?.signals).filter(isObj), top2 = sig.slice(0, 2).map((g) => `${esc(g.label)} ${val(g.value, g.unit)}`).join(" · ");
        const trig = arr(L?.triggers).filter((x) => x.trades.includes(t.id));
        const key = `${slug}|${id}|${t.id}`;
        const body = [];
        if (sig.length) body.push(`<table>${sig.map((g) => `<tr><td>${esc(g.label)}</td><td class="v">${val(g.value, g.unit)}</td><td class="s">${esc(g.source || "")}</td></tr>`).join("")}</table>`);
        const rs = arr(ht?.reasons).map(reasonText).filter(Boolean); if (rs.length) body.push(`<ul>${rs.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`);
        if (t.id === "snow_ice") body.push(this._snowHtml(slug, id));
        if (trig.length) body.push(`<ul class="mnt-trig">${trig.map((x) => `<li class="${x.level}">${esc(x.text)}<small>${esc(x.source)}</small></li>`).join("")}</ul>`);
        if (poolHtml) body.push(`<div class="mnt-pool">${poolHtml}</div>`);
        rows.push(`<details class="mnt-row${t.id === this.trade ? " on" : ""}" data-mnt-key="${esc(key)}"${this._open.has(key) ? " open" : ""}><summary style="${c ? `--c:${c}` : ""}">
          <span class="t" title="${esc(t.blurb || t.label)}">${esc(t.label)}${trig.length ? `<span class="mnt-flag" title="NWS trigger">●</span>` : ""}</span>
          ${sc != null ? `<i class="mnt-bar" style="--v:${sc.toFixed(0)}%"></i><span class="mnt-sc">${Math.round(sc)}</span>` : `<i class="mnt-bar"></i><span class="mnt-sc">${DASH}</span>`}
          <span class="sg">${top2 || DASH}</span>
          <span class="nx"><b>NEXT</b>${ht?.next ? esc(ht.next) : DASH}</span></summary><div class="mnt-body">${body.join("") || `<span class="dim">${DASH}</span>`}</div></details>`);
      }
      return `<section class="mnt"><h4>Maintenance</h4>${rows.join("") || `<div class="sub small">${DASH}</div>`}${none.length ? `<div class="sub small">No signal: ${esc(none.join(", "))}</div>` : ""}</section>`;
    }
    _poolHouse(slug, id, standalone) {
      if (!this.pool?.houseHtml) return "";
      try { const h = this.pool.houseHtml(slug, id) || ""; return h && standalone ? `<div class="mnt-pool">${h}</div>` : h; } catch { return ""; }
    }
    _snowHtml(slug, id) {
      const r = this.S.get(slug)?.snowBy?.get(id); if (!r) return "";
      const scal = (v) => v == null || typeof v !== "object";
      const kv = (o, pre) => (isObj(o) ? Object.entries(o).filter(([k, v]) => k !== "flag" && scal(v)).map(([k, v]) => `<dt>${esc(pre + k.replace(/_/g, " "))}</dt><dd>${val(v)}</dd>`).join("") : "");
      const roof = isObj(r.roof) ? r.roof : null, ap = r.apron, notes = arr(r.notes).map(reasonText).filter(Boolean);
      return `<div class="lbl">Snowmelt (satellite)</div><dl><dt>Roof melt flag</dt><dd>${roof ? val(roof.flag) : DASH}</dd>${kv(roof, "")}${isObj(ap) ? kv(ap, "apron ") : `<dt>Apron</dt><dd>${val(ap)}</dd>`}</dl>${notes.length ? `<ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
    }

    // ------------------------------------------------------------ block panel
    blockHtml(slug) {
      const s = this.S.get(slug); if (!this.enabled || !s) return "";
      const list = this._tradeList(slug), tr = this.trade, T = list.find((t) => t.id === tr) || { id: tr, label: TRADE_LABEL[tr] || tr, blurb: "" };
      const L = this._liveBy.get(slug), trigOf = (tid) => arr(L?.v?.triggers).filter((x) => x.trades.includes(tid));
      const has = (tid) => !!s.doc && [...(s.byId?.values() || [])].some((h) => isObj(h.trades?.[tid])) || isObj(s.doc?.block?.trades?.[tid]);
      const out = [`<section class="mnt"><div class="lbl">Maintenance</div>`];
      out.push(`<div class="mnt-tabs" role="tablist">${list.map((t) => `<button role="tab" aria-selected="${t.id === tr}" class="${t.id === tr ? "on" : ""}${has(t.id) || (t.id === "pool" && this.pool) ? "" : " nodata"}" data-mnt-trade="${esc(t.id)}" title="${esc(t.blurb || t.label)}">${esc(t.label)}${trigOf(t.id).length ? "<i>●</i>" : ""}</button>`).join("")}</div>`);
      out.push(`<h4>${esc(T.label)}</h4>${T.blurb ? `<div class="sub small">${esc(T.blurb)}</div>` : ""}`);
      if (!s.doc) out.push(`<div class="sub small">${s.loadP ? "No maintenance file for this block yet." : "Loading…"}</div>`);
      const bt = isObj(s.doc?.block?.trades?.[tr]) ? s.doc.block.trades[tr] : null;
      if (bt?.summary) out.push(`<p class="sub">${esc(typeof bt.summary === "string" ? bt.summary : reasonText(bt.summary))}</p>`);
      if (s.doc) {
        const scored = [...s.byId.keys()].map((h) => ({ h, sc: this.score(slug, h, tr) })).filter((x) => x.sc != null);
        const topIds = arr(bt?.top).map(String), top = (topIds.length ? topIds.map((h) => ({ h, sc: this.score(slug, h, tr) })) : scored.sort((a, b) => b.sc - a.sc).slice(0, 8));
        out.push(`<div class="mnt-legend"><span>low</span><i style="background:linear-gradient(90deg,${[0, 0.25, 0.5, 0.75, 1].map((t) => rgbCss(ramp(t))).join(",")})"></i><span>high · ${scored.length} of ${s.byId.size} houses scored</span></div>`);
        if (top.length) out.push(`<div class="lbl">Top houses</div><ol class="hlist">${top.map(({ h, sc }) => { const ht = this._ht(slug, h, tr);
          return `<li><button data-mnt-house="${esc(slug)}|${esc(h)}" title="${esc(ht?.next || "")}"><span>${esc(this._addr(slug, h) || "House " + h)}</span><b style="${sc != null ? `color:${rgbCss(ramp(sc / 100))}` : ""}">${sc != null ? Math.round(sc) : DASH}</b></button></li>`; }).join("")}</ol>`);
        else out.push(`<div class="sub small">No house has a signal for this trade.</div>`);
      }
      if (tr === "snow_ice" && isObj(s.snow)) {
        const sm = isObj(s.snow.summary) ? Object.entries(s.snow.summary).filter(([, v]) => v == null || typeof v !== "object") : [];
        const fl = [...(s.snowBy?.entries() || [])].filter(([, h]) => h?.roof?.flag && !/^normal$/i.test(String(h.roof.flag)));
        out.push(`<div class="lbl">Snowmelt (satellite)</div>${sm.length ? `<dl>${sm.map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${val(v)}</dd>`).join("")}</dl>` : ""}`
          + (fl.length ? `<ol class="hlist">${fl.map(([h, r]) => `<li><button data-mnt-house="${esc(slug)}|${esc(h)}"><span>${esc(r.address || this._addr(slug, h) || "House " + h)}</span><b>${esc(r.roof.flag)}</b></button></li>`).join("")}</ol>` : `<div class="sub small">No roof flagged for early melt.</div>`));
      }
      if (tr === "pool" && this.pool?.blockHtml) { try { const h = this.pool.blockHtml(slug); if (h) out.push(`<div class="mnt-pool">${h}</div>`); } catch {} }
      // climate
      const cl = isObj(s.doc?.block?.climate) ? Object.entries(s.doc.block.climate).filter(([, v]) => v != null && !isObj(v)) : [];
      if (cl.length) out.push(`<div class="lbl">Climate</div><dl>${cl.map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${val(v)}</dd>`).join("")}</dl>`);
      // live triggers
      const canAsk = this._ll(slug) != null, btn = canAsk ? ` <button class="mnt-btn" data-mnt-live="${esc(slug)}">${L?.v ? "refresh" : "check NWS forecast"}</button>` : "";
      out.push(`<div class="lbl">Live triggers · next 7 days${btn}</div>`);
      if (L?.state === "loading") out.push(`<div class="sub small">Asking the National Weather Service…</div>`);
      else if (L?.state === "error") out.push(`<div class="sub small">NWS forecast unavailable (${esc(L.error)}).</div>`);
      if (L?.v) {
        const all = arr(L.v.triggers), mine = all.filter((x) => x.trades.includes(tr)), rest = all.filter((x) => !x.trades.includes(tr));
        if (!all.length) out.push(`<div class="sub small">No NWS alerts and no forecast triggers this week${L.v.office ? ` (${esc(L.v.office)})` : ""}.</div>`);
        else out.push(`<ul class="mnt-trig">${mine.concat(rest).map((x) => `<li class="${x.level}${x.trades.includes(tr) ? "" : " off"}">${esc(x.text)}<small>${esc(x.trades.map((t) => TRADE_LABEL[t] || t).join(" · "))} — ${esc(x.source)}</small></li>`).join("")}</ul>`);
      }
      const src = arr(s.doc?.sources).filter(isObj);
      if (src.length) out.push(`<div class="lbl">Sources</div><ul class="src">${src.map((x) => `<li><b>${esc(x.name)}</b>${x.licence ? " · " + esc(x.licence) : ""}${/^https?:/i.test(String(x.url || "")) ? ` <a href="${esc(x.url)}" target="_blank" rel="noopener">link</a>` : ""}</li>`).join("")}</ul>`);
      if (s.doc?.built) out.push(`<div class="sub small">Built ${esc(s.doc.built)}${s.doc.version ? " · v" + esc(s.doc.version) : ""}</div>`);
      out.push(`</section>`);
      return out.join("");
    }

    // ------------------------------------------------------------ live NWS triggers
    _ll(slug) { const o = this.S.get(slug)?.meta?.origin; return Array.isArray(o) && isFinite(+o[0]) && isFinite(+o[1]) ? [+o[1], +o[0]] : null; }
    live(slug, lat, lon) {
      if (!(isFinite(+lat) && isFinite(+lon) && lat !== null && lon !== null)) { const ll = this._ll(slug); if (!ll) return Promise.resolve(null); [lat, lon] = ll; }
      lat = +lat; lon = +lon; const prev = this._liveBy.get(slug);
      this._liveBy.set(slug, { state: "loading", v: prev?.v }); this._emit({ type: "live", slug, state: "loading" });
      return this._nws(lat, lon, this.S.get(slug)?.meta?.tz).then((v) => { this._liveBy.set(slug, { state: "ready", v }); this.usedNws = true; this._emit({ type: "live", slug, state: "ready" }); return v; })
        .catch((e) => { this._liveBy.set(slug, { state: "error", v: prev?.v, error: e.message || String(e) }); this._emit({ type: "live", slug, state: "error" }); return null; });
    }
    liveState(slug) { return this._liveBy.get(slug) || null; }
    async _nws(lat, lon, tzHint) {
      const ll = `${lat.toFixed(4)},${lon.toFixed(4)}`, c = this._liveCache.get(ll);
      if (c?.v && Date.now() - c.t < 30 * 60e3) return c.v;
      if (c?.p) return c.p;
      const p = (async () => {
        const pt = await getJSON(`${NWS}/points/${ll}`), pp = pt?.properties || {}, tz = pp.timeZone || tzHint || undefined;
        const [g, al] = await Promise.all([pp.forecastGridData ? getJSON(pp.forecastGridData).catch(() => null) : null, getJSON(`${NWS}/alerts/active?point=${ll}`).catch(() => null)]);
        const v = triggers(g?.properties || null, arr(al?.features), tz); v.office = pp.gridId || pp.cwa || ""; v.fetched = Date.now();
        this._liveCache.set(ll, { t: Date.now(), v }); return v;
      })();
      this._liveCache.set(ll, { t: 0, p });
      try { return await p; } catch (e) { this._liveCache.delete(ll); throw e; }
    }

    // ------------------------------------------------------------ wiring
    bind(el) {
      if (!el) return; if (this.pool?.bind) { try { this.pool.bind(el); } catch {} }
      if (el._mntBound) return; el._mntBound = true;
      el.addEventListener("toggle", (e) => { const d = e.target; if (!d?.matches?.("details[data-mnt-key]")) return; const k = d.getAttribute("data-mnt-key"); if (d.open) this._open.add(k); else this._open.delete(k); }, true);
      el.addEventListener("click", (e) => {
        const t = e.target.closest?.("[data-mnt-trade],[data-mnt-house],[data-mnt-live]"); if (!t || !el.contains(t)) return;
        e.stopPropagation();
        if (t.hasAttribute("data-mnt-trade")) return this.setTrade(t.getAttribute("data-mnt-trade"));
        if (t.hasAttribute("data-mnt-live")) return void this.live(t.getAttribute("data-mnt-live"));
        const [slug, houseId] = String(t.getAttribute("data-mnt-house")).split("|"); this._emit({ type: "focusHouse", slug, houseId });
      });
      el.addEventListener("mouseover", (e) => { const t = e.target.closest?.("[data-mnt-house]"); if (!t || !el.contains(t)) return; const [slug, houseId] = String(t.getAttribute("data-mnt-house")).split("|"); this.highlight(slug, houseId, "hover"); });
      el.addEventListener("mouseout", (e) => { const t = e.target.closest?.("[data-mnt-house]"); if (t && !t.contains(e.relatedTarget)) this.highlight(null, null, "hover"); });
    }
    credits() {
      if (!this.enabled) return "";
      const names = new Set(); for (const s of this.S.values()) [...arr(s.doc?.sources), ...arr(s.snow?.sources)].forEach((x) => x?.name && names.add(String(x.name).replace(/\s*\(.*$/, "")));
      const parts = [...names].slice(0, 4); if (this.usedNws) parts.push("NOAA/NWS api.weather.gov (public domain)");
      return parts.length ? "Maintenance: " + parts.join(" · ") : "";
    }
  }

  // ---------------------------------------------------------------- helpers
  function groundPath(ring, gz, up, step) {   // closed ring densified to `step` m, every vertex on the ground
    const p = [...ring]; if (p[0][0] !== p[p.length - 1][0] || p[0][1] !== p[p.length - 1][1]) p.push(p[0]);
    const out = [];
    for (let i = 0; i + 1 < p.length; i++) { const [ax, ay] = p[i], [bx, by] = p[i + 1], n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / step));
      for (let k = 0; k < n; k++) { const x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n; out.push([x, y, gz(x, y) + up]); } }
    const [ex, ey] = p[p.length - 1]; out.push([ex, ey, gz(ex, ey) + up]); return out;
  }
  function closed(ring, z) { const p = ring.map(([x, y]) => [x, y, z]); const a = p[0], b = p[p.length - 1]; if (a[0] !== b[0] || a[1] !== b[1]) p.push([a[0], a[1], z]); return p; }
  function dotsOf(ring, z, step) {
    const out = [];
    for (let i = 0; i < ring.length; i++) { const a = ring[i], b = ring[(i + 1) % ring.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]); if (!L) continue; const n = Math.max(1, Math.round(L / step));
      for (let k = 0; k < n; k++) out.push([a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n, z + 0.02]); }
    return out;
  }
  const stripTags = (h) => String(h).replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");

  function triggers(G, alerts, tz) {
    let dayKey, dowOf;
    try { const f = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }), w = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short" });
      dayKey = (t) => f.format(t); dowOf = (t) => w.format(t); } catch { dayKey = (t) => t.toISOString().slice(0, 10); dowOf = (t) => t.toUTCString().slice(0, 3); }
    const today = dayKey(new Date()), days = new Map();
    const day = (t) => { const k = dayKey(t); if (k < today) return null; if (!days.has(k)) days.set(k, { date: k, dow: dowOf(t), maxF: null, minF: null, gust: null, pop: null, snowIn: null, iceIn: null, rainIn: null }); return days.get(k); };
    const agg = (list, f, mode) => list.forEach(({ t, v }) => { const D_ = day(t); if (!D_) return; D_[f] = D_[f] == null ? v : mode === "min" ? Math.min(D_[f], v) : mode === "sum" ? D_[f] + v : Math.max(D_[f], v); });
    if (G) {
      agg(series(G.maxTemperature, toF(G.maxTemperature?.uom), false), "maxF", "max");
      agg(series(G.minTemperature, toF(G.minTemperature?.uom), false), "minF", "min");
      agg(series(G.windGust, toMph(G.windGust?.uom), true), "gust", "max");
      agg(series(G.probabilityOfPrecipitation, (x) => x, true), "pop", "max");
      agg(series(G.snowfallAmount, toIn(G.snowfallAmount?.uom), false), "snowIn", "sum");
      agg(series(G.iceAccumulation, toIn(G.iceAccumulation?.uom), false), "iceIn", "sum");
      agg(series(G.quantitativePrecipitation, toIn(G.quantitativePrecipitation?.uom), false), "rainIn", "sum");
    }
    const D7 = [...days.values()].sort((a, b) => a.date.localeCompare(b.date)).slice(0, 7), T = [];
    const dl = (xs) => xs.map((x) => x.dow).join(", ");
    const best = (xs, f, lo) => xs.reduce((a, b) => ((lo ? b[f] < a[f] : b[f] > a[f]) ? b : a));
    const seen = new Set();
    for (const f of alerts) { const a = f?.properties || {}, ev = String(a.event || ""); const rule = ALERTS.find(([re]) => re.test(ev)); if (!rule || seen.has(ev)) continue; seen.add(ev);
      let until = ""; const e = Date.parse(a.ends || a.expires || ""); if (isFinite(e)) { try { until = " until " + new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", hour: "numeric" }).format(new Date(e)); } catch {} }
      T.push({ level: rule[1], trades: rule[2], text: `${ev}${until}: ${rule[3]}`, note: a.headline || "", source: "NWS alert" }); }
    const snow = D7.filter((x) => x.snowIn != null && x.snowIn >= 0.1);
    if (snow.length) { const tot = snow.reduce((s, x) => s + x.snowIn, 0), top = best(snow, "snowIn"); T.push({ level: tot >= 4 ? "bad" : tot >= 1 ? "warn" : "", trades: tot >= 6 ? ["snow_ice", "roof", "trees"] : ["snow_ice"], text: `Snow ${tot.toFixed(1)} in this week (most ${top.dow}, ${top.snowIn.toFixed(1)} in; ${dl(snow)}): plough, shovel and de-ice`, source: "NWS forecast" }); }
    const ice = D7.filter((x) => x.iceIn != null && x.iceIn >= 0.01);
    if (ice.length) { const top = best(ice, "iceIn"); T.push({ level: top.iceIn >= 0.25 ? "bad" : "warn", trades: ["snow_ice", "trees", "gutters"], text: `Ice ${top.iceIn.toFixed(2)} in ${top.dow}: de-ice walks, limbs and gutters at risk`, source: "NWS forecast" }); }
    const gusty = D7.filter((x) => x.gust != null && x.gust >= 35);
    if (gusty.length) { const top = best(gusty, "gust"); T.push({ level: top.gust >= 50 ? "bad" : "warn", trades: top.gust >= 50 ? ["trees", "gutters", "roof"] : ["trees", "gutters"], text: `Wind gusts ${Math.round(top.gust)} mph ${top.dow}${gusty.length > 1 ? ` (gusty ${dl(gusty)})` : ""}: limbs and gutters`, source: "NWS forecast" }); }
    for (const t of [105, 100, 95, 90]) { const hot = D7.filter((x) => x.maxF != null && x.maxF >= t); if (hot.length) { T.push({ level: t >= 100 ? "warn" : "", trades: ["cooling", "pool"], text: `${hot.length} day${hot.length > 1 ? "s" : ""} ≥ ${t}°F (${dl(hot)}): AC load, pool chemistry`, source: "NWS forecast" }); break; } }
    const cold = D7.filter((x) => x.minF != null && x.minF <= 32);
    if (cold.length) { const top = best(cold, "minF", true); T.push({ level: top.minF <= 20 ? "bad" : "warn", trades: top.minF <= 20 ? ["heating", "flood_water", "pool"] : ["heating", "pool"], text: `Low ${Math.round(top.minF)}°F ${top.dow}${cold.length > 1 ? ` (freezing ${dl(cold)})` : ""}: heating check${top.minF <= 20 ? ", pipe freeze risk" : ""}`, source: "NWS forecast" }); }
    const wet = D7.filter((x) => x.rainIn != null && x.rainIn >= 0.75);
    if (wet.length) { const top = best(wet, "rainIn"); T.push({ level: top.rainIn >= 2 ? "bad" : "warn", trades: ["gutters", "flood_water", "foundation"], text: `Rain ${top.rainIn.toFixed(2)} in ${top.dow}${wet.length > 1 ? ` (${dl(wet)})` : ""}: gutters, drainage, sump`, source: "NWS forecast" }); }
    return { days: D7, alerts: alerts.map((f) => f?.properties).filter(Boolean).map((a) => ({ event: a.event, headline: a.headline, ends: a.ends, severity: a.severity })), triggers: T, tz: tz || null, hasGrid: !!G };
  }

  Maintenance.TRADES = TRADES.map(([id, label]) => ({ id, label }));
  Maintenance.ramp = ramp;
  Maintenance._triggers = triggers;
  globalThis.Maintenance = Maintenance;
})();

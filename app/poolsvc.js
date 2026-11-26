/* poolsvc.js — "Pool service" lens for a loaded 3D block (classic script; defines window.PoolService).
   Needs the deck.gl 9.1.x UMD build on globalThis.deck. Styling: the .bm-panel styles (block-panel.css) + a few
   .ps-* rules injected once by this script.

   const ps = new PoolService({ onUpdate, baseUrl: "../usa/blocks/" });
   ps.load(slug, meta)            -> Promise<model|null>  fetches <slug>/pool_service.json; on 404 / bad JSON falls back to
                                     the `pool` objects of houses.json passed with setHouses(slug, houses). Cached per slug.
   ps.unload(slug)
   ps.setHouses(slug, houses)     houses.json array (fallback source; also gives addresses)
   ps.setData(slug, doc)          inject a pool_service.json object directly (tests / pre-fetched)
   ps.layers({ slug, coordinateOrigin, zBase, zOf, houseColor, exceptHouseId, route })  -> deck Layer[]  ids "pool:<slug>:*"
                                     Always-on lens: faint pool outlines (ONLY pools with a traced `outline`; never a circle) in the
                                     house colour family, tinted toward the water state, plus the dashed service route.
                                     coordinateOrigin = [lon, lat, zOffset] (same as block.js); if it has no z, zBase is used.
                                     zOf(x, y) = ground z in block-local metres (block.js Block.groundLocal); null -> 0.
   ps.poolLayersForHouse(slug, houseId, { coordinateOrigin, zBase, zOf, glow })  -> deck Layer[]
                                     the pool part of a house highlight: glow true | {t, color} = block.js selection shimmer
                                     (same base / additive halo / travelling band, same t and colour); glow false = steady hover edge.
   ps.pick(info)                  pool shape picked -> {type:"selectHouse", slug, houseId, poolId} (also emitted). The pool layers carry
                                     blockSlug + blockRole "house" and objects whose id = house_id, so BlockManager.pick() selects the house too.
   ps.tooltip(info)               plain-text tooltip for a pool / route object | null
   ps.houseHtml(slug, houseId)    pool section for the house dossier ("" when the lens is off / no data)
   ps.blockHtml(slug)             block summary: pools per street, route order, this week's triggers, top leads, market
   ps.bind(el)                    delegated clicks: [data-ps-pool="slug|id"] -> onUpdate({type:"focusPool", slug, poolId, houseId, x, y})
                                                     [data-ps-house="slug|id"] -> onUpdate({type:"selectHouse", slug, houseId})
                                                     [data-ps-live="slug"] -> live(slug) using the stored meta.origin
   ps.live(slug, lat, lon)        -> Promise  NWS active alerts + 7-day gridpoint forecast -> plain triggers (cached 30 min)
   ps.credits()                   short attribution string
   ps.enabled / setEnabled(bool)  lens on/off; when off layers() returns [] and the html methods return ""
   onUpdate(evt)                  evt.type: "load" | "unload" | "live" | "focusPool" | "selectHouse" | "enabled" | "error"
*/
(function () {
  "use strict";
  const D = () => globalThis.deck;
  const DASH = "—";
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const arr = (v) => (Array.isArray(v) ? v : []);
  const isObj = (o) => o && typeof o === "object" && !Array.isArray(o);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = new Intl.NumberFormat("en-US");
  const d = (v, suf = "") => (v == null || v === "" || typeof v === "object" || (typeof v === "number" && !isFinite(v)) ? DASH : esc(typeof v === "number" ? fmt.format(Math.round(v * 10) / 10) : v) + suf);
  const pct = (v) => { const n = num(v); if (n == null) return DASH; return Math.round((Math.abs(n) <= 1 ? n * 100 : n)) + "%"; };
  const m2ft = (v) => (num(v) == null ? DASH : `${fmt.format(Math.round(v))} m² (${fmt.format(Math.round(v * 10.764))} ft²)`);
  const yesNo = (v) => (v === true ? "yes" : v === false ? "no" : d(v));
  const reasonText = (r) => (typeof r === "string" ? r : [r?.label, r?.text, r?.reason].filter(Boolean).join(": "));
  const scoreCls = (v) => (v == null ? "" : v >= 70 ? "ok" : v >= 40 ? "warn" : "bad");
  const mix = (a, b, f) => [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * f));
  const lift = (c, f) => mix(c, [255, 255, 255], f);
  function bandI(s, L, t, nb, v = 6.5) {   // travelling light band, same as block.js
    let I = 0; const sp = L / nb;
    for (let b = 0; b < nb; b++) { const head = (t * v + b * sp) % L, d = (((head - s) % L) + L) % L; I = Math.max(I, d < L / 2 ? Math.exp(-d / 3.2) : Math.exp(-((L - d) ** 2) / 0.5)); }
    return I;
  }
  function houseColor() {   // block.js glowPal().house for the default theme = theme "select" colour
    const T = globalThis.Themes; try { if (T?.rgb) return T.rgb("select").slice(0, 3); } catch {}
    return [255, 210, 77];
  }
  const okRing = (r) => Array.isArray(r) && r.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1])).length >= 3;
  const labelOf = (k) => String(k).replace(/_/g, " ").replace(/\byr\b/, "/ yr").replace(/\bdv\b/, "design value").replace(/\bpct\b/, "%");

  // ---------------------------------------------------------------- water state classes
  const STATES = {
    clean: { label: "clean (clear blue)", chip: "ok", col: [79, 220, 255] },
    cloudy: { label: "turquoise / cloudy", chip: "warn", col: [110, 232, 196] },
    green: { label: "green (algae)", chip: "bad", col: [196, 214, 70] },
    dry: { label: "empty / dry", chip: "", col: [138, 148, 162] },
    none: { label: "no water seen", chip: "", col: [110, 118, 130] },
    unk: { label: "no reading", chip: "", col: [110, 118, 130] },
  };
  function stateOf(s) {
    const t = String(s ?? "").toLowerCase(); if (!t) return "unk";
    return /green|algae|swamp/.test(t) ? "green" : /turq|cloud|teal|murk/.test(t) ? "cloudy" : /blue|clear|clean/.test(t) ? "clean"
      : /empty|dry|drain/.test(t) ? "dry" : /none|absent|no.?pool|not.?seen|cover/.test(t) ? "none" : "unk";
  }
  const themeCol = (k) => {   // follow theme.js accent / warn when present, keep the semantic hue otherwise
    const T = globalThis.Themes;
    try { if (T?.rgb && k === "clean") return T.rgb("accent").slice(0, 3); } catch {}
    return STATES[k].col;
  };

  // ---------------------------------------------------------------- CSS (scoped under .bm-panel)
  function injectCss() {
    if (typeof document === "undefined" || document.getElementById("poolsvc-css")) return;
    const s = document.createElement("style"); s.id = "poolsvc-css";
    s.textContent = `
.bm-panel .ps-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:1px; box-shadow:0 0 6px currentColor; background:currentColor; }
.bm-panel .ps-s-clean { color: var(--bm-cyan); } .bm-panel .ps-s-cloudy { color: #6ee8c4; } .bm-panel .ps-s-green { color: #c4d646; }
.bm-panel .ps-s-dry, .bm-panel .ps-s-none, .bm-panel .ps-s-unk { color: var(--bm-dim); }
.bm-panel ol.ps-route { counter-reset: ps; }
.bm-panel ol.ps-route button span::before { counter-increment: ps; content: counter(ps) "  "; font-family: var(--bm-mono); color: var(--bm-dim); }
.bm-panel ul.ps-trig { list-style:none; margin:2px 0 0; padding:0; font-size:12px; }
.bm-panel ul.ps-trig li { padding:3px 0 3px 10px; border-left:2px solid var(--bm-edge); margin:2px 0; }
.bm-panel ul.ps-trig li.warn { border-left-color: var(--bm-amber); } .bm-panel ul.ps-trig li.bad { border-left-color: var(--bm-red); }
.bm-panel ul.ps-trig li .small { color: var(--bm-dim); }
.bm-panel table.ps-wk { width:100%; border-collapse:collapse; font:11px var(--bm-mono); margin-top:4px; }
.bm-panel table.ps-wk th, .bm-panel table.ps-wk td { padding:2px 3px; text-align:center; border-bottom:1px solid color-mix(in srgb, var(--bm-edge) 40%, transparent); }
.bm-panel table.ps-wk th { color: var(--bm-dim); font-weight:500; } .bm-panel table.ps-wk td:first-child, .bm-panel table.ps-wk th:first-child { text-align:left; color: var(--bm-dim); }
.bm-panel table.ps-wk td.hot { color: var(--bm-amber); }
.bm-panel .ps-btn { background:none; border:1px solid var(--bm-edge); color: var(--bm-cyan); font:11px var(--bm-mono); padding:2px 8px; border-radius:3px; cursor:pointer; }
.bm-panel .ps-btn:hover { border-color: var(--bm-cyan); }
.bm-panel .ps-sec { border-top:1px solid color-mix(in srgb, var(--bm-cyan) 30%, transparent); margin-top:10px; padding-top:2px; }`;
    document.head.appendChild(s);
  }

  // ---------------------------------------------------------------- normalisation
  function normPool(p, i) {
    if (!isObj(p)) return null;
    const ol = okRing(p.outline) ? p.outline.filter((q) => Array.isArray(q) && isFinite(+q[0]) && isFinite(+q[1])).map((q) => [+q[0], +q[1]]) : null;
    const hist = arr(p.history).filter((h) => isObj(h) && h.year != null).map((h) => ({ year: h.year, state: h.state ?? null, date: h.date ?? null })).sort((a, b) => +a.year - +b.year);
    const now = p.state_now ?? (hist.length ? hist[hist.length - 1].state : null);
    return {
      id: String(p.id ?? p.house_id ?? i), house_id: p.house_id != null ? String(p.house_id) : null, address: p.address ?? null,
      x: num(+p.x), y: num(+p.y), area_m2: num(p.area_m2), confidence: num(p.confidence), first_seen_year: p.first_seen_year ?? null,
      outline: ol, outline_source: typeof p.outline_source === "string" ? p.outline_source : null, state_now: now, state_now_date: p.state_now_date ?? null, cls: stateOf(now), history: hist,
      trees: isObj(p.trees) ? p.trees : null, sun: isObj(p.sun) ? p.sun : null, ground: isObj(p.ground) ? p.ground : null,
      visit: isObj(p.visit) ? p.visit : null, lead: isObj(p.lead) ? p.lead : null,
    };
  }
  function streetOf(addr) { const s = String(addr || "").trim().replace(/^\d+[a-z]?\s+/i, ""); return s || null; }
  function nnRoute(pools) {   // fallback only: greedy nearest-neighbour order (labelled as computed in the panel)
    const P = pools.filter((p) => p.x != null && p.y != null); if (!P.length) return [];
    const left = P.slice().sort((a, b) => a.x - b.x || a.y - b.y), out = [left.shift()];
    while (left.length) { const c = out[out.length - 1]; let bi = 0, bd = Infinity;
      left.forEach((p, i) => { const dd = Math.hypot(p.x - c.x, p.y - c.y); if (dd < bd) { bd = dd; bi = i; } }); out.push(left.splice(bi, 1)[0]); }
    return out.map((p) => p.id);
  }
  function fromDoc(doc) {
    const pools = arr(doc.pools).map(normPool).filter(Boolean), B = isObj(doc.block) ? doc.block : {};
    const ids = new Set(pools.map((p) => p.id));
    let route = arr(B.route).map(String).filter((id) => ids.has(id)), routeComputed = false;
    if (!route.length) { route = nnRoute(pools); routeComputed = true; }   // no route in the file: computed order, labelled as such
    return { src: "pool_service.json", built: doc.built ?? null, version: doc.version ?? null, sources: arr(doc.sources).filter(isObj), pools,
      notAvailable: arr(doc.not_available).filter((x) => typeof x === "string"),
      block: { n_pools: num(B.n_pools) ?? null, n_houses: num(B.n_houses) ?? null, pools_within_100m: num(B.pools_within_100m) ?? null,
        streets: arr(B.streets).filter(isObj).map((s) => ({ name: s.name ?? null, pool_ids: arr(s.pool_ids).map(String).filter((id) => ids.has(id)) })),
        route, routeComputed, routeRule: typeof B.route_rule === "string" ? B.route_rule : null, climate: isObj(B.climate) ? B.climate : null, risks: isObj(B.risks) ? B.risks : null, market: isObj(B.market) ? B.market : null } };
  }
  function fromHouses(houses, meta) {
    const H = arr(houses).filter(isObj), pools = [];
    H.forEach((h, i) => { const p = isObj(h.pool) ? h.pool : null; if (!p || !(p.present === true || (p.present == null && p.likely === true))) return;
      const lead = isObj(h.cleaning?.pool) ? { score: num(h.cleaning.pool.score), reasons: arr(h.cleaning.pool.reasons) } : num(h.cleaning?.pool_lead) != null ? { score: h.cleaning.pool_lead, reasons: arr(h.cleaning.pool_reasons) } : null;
      const np = normPool({ id: h.id ?? i, house_id: h.id ?? i, address: h.address, x: p.x, y: p.y, area_m2: p.area_m2, confidence: p.confidence, first_seen_year: p.first_seen_year,
        outline: p.outline ?? null, state_now: p.last_state ?? null, history: arr(p.by_year).map((y) => ({ year: y.year, state: y.state, date: y.date })), lead }, i);
      if (np) pools.push(np); });
    const by = new Map(); pools.forEach((p) => { const s = streetOf(p.address); if (!s) return; if (!by.has(s)) by.set(s, []); by.get(s).push(p.id); });
    const wt = isObj(meta?.weather_triggers) ? (isObj(meta.weather_triggers.county) ? meta.weather_triggers.county : meta.weather_triggers) : null;
    const climate = wt ? { dust_days_yr: num(wt.dust_days_yr), high_wind_days_yr: num(wt.high_wind_days_yr), dust_then_rain_yr: num(wt.dust_then_rain_yr), hail_days_yr: num(wt.hail_days_yr) } : null;
    const mp = isObj(meta?.pools) ? meta.pools : {};
    const lic = typeof mp.licence === "string" ? mp.licence : "USDA NAIP: public domain (USDA FSA)";
    return { src: "houses.json", built: null, version: null, sources: [{ name: "Pool detection from open aerial imagery (houses.json)", licence: lic, url: null }], pools,
      block: { n_pools: pools.length, n_houses: H.length || null, pools_within_100m: num(mp.context_m) === 100 ? num(mp.context_pools) : null,
        streets: [...by].map(([name, pool_ids]) => ({ name, pool_ids })), route: nnRoute(pools), routeComputed: true, climate, risks: null, market: null } };
  }

  // ---------------------------------------------------------------- NWS (api.weather.gov, US public domain)
  const NWS = "https://api.weather.gov";
  const getJSON = (u) => fetch(u, { headers: { Accept: "application/geo+json" } }).then((r) => { if (!r.ok) throw new Error(`${r.status} ${u}`); return r.json(); });
  function durH(s) { const m = /P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?/.exec(String(s || "")); return m ? (+m[1] || 0) * 24 + (+m[2] || 0) + ((+m[3] || 0) > 0 ? 1 : 0) : 1; }
  function series(prop, conv, expand) {   // -> [{t: Date, v}] one entry per hour (expand) or per period start
    const out = []; for (const e of arr(prop?.values)) { const [a, b] = String(e.validTime || "").split("/"), t0 = Date.parse(a), v = num(e.value); if (!isFinite(t0) || v == null) continue;
      const n = expand ? Math.min(48, Math.max(1, durH(b))) : 1; for (let k = 0; k < n; k++) out.push({ t: new Date(t0 + k * 3600e3), v: conv(v) }); }
    return out;
  }
  const toF = (uom) => (/degC/.test(uom || "") ? (v) => v * 9 / 5 + 32 : (v) => v);
  const toMph = (uom) => (/km_h/.test(uom || "") ? (v) => v * 0.621371 : /m_s/.test(uom || "") ? (v) => v * 2.23694 : /kt|knot/.test(uom || "") ? (v) => v * 1.15078 : (v) => v);
  const ALERTS = [
    [/dust/i, "bad", "expect dust and debris in pools: plan post-storm cleans"],
    [/high wind|wind advisory|extreme wind/i, "warn", "debris blown into pools: plan post-storm cleans"],
    [/excessive heat|extreme heat|heat advisory|heat warning/i, "warn", "chlorine demand up, water loss from evaporation"],
    [/flood/i, "warn", "runoff can contaminate pools: check after it passes"],
    [/red flag|fire weather|smoke|air quality/i, "warn", "ash and soot likely: skim and check filters"],
  ];

  // =====================================================================================================================
  class PoolService {
    constructor({ onUpdate, baseUrl = "../usa/blocks/", enabled = true } = {}) {
      this.onUpdate = typeof onUpdate === "function" ? onUpdate : () => {};
      this.baseUrl = /\/$/.test(baseUrl) ? baseUrl : baseUrl + "/";
      this.enabled = !!enabled;
      this.S = new Map();          // slug -> {meta, doc, houses, model, loading}
      this._liveCache = new Map(); // "lat,lon" -> {t, v}
      this._liveBy = new Map();    // slug -> {state:"loading"|"ready"|"error", v, error}
      this.usedNws = false;
      injectCss();
    }
    _emit(e) { try { this.onUpdate(e); } catch (err) { console.warn("PoolService onUpdate:", err); } }
    _s(slug) { if (!this.S.has(slug)) this.S.set(slug, { meta: null, doc: null, houses: null, model: null }); return this.S.get(slug); }
    setEnabled(on) { on = !!on; if (on === this.enabled) return; this.enabled = on; this._emit({ type: "enabled", enabled: on }); }

    load(slug, meta) {
      const s = this._s(slug); if (meta) s.meta = meta;
      if (s.loadP) return s.loadP;
      s.loadP = fetch(this.baseUrl + encodeURIComponent(slug) + "/pool_service.json").then((r) => (r.ok ? r.text() : ""))
        .then((t) => { try { return t ? JSON.parse(t) : null; } catch { return null; } }).catch(() => null)
        .then((doc) => { if (this.S.get(slug) !== s) return null; if (isObj(doc) && Array.isArray(doc.pools)) s.doc = doc; s.model = null; this._emit({ type: "load", slug }); return this.model(slug); });
      return s.loadP;
    }
    unload(slug) { if (!this.S.has(slug)) return; this.S.delete(slug); this._liveBy.delete(slug); this._emit({ type: "unload", slug }); }
    setHouses(slug, houses) { const s = this._s(slug); s.houses = Array.isArray(houses) ? houses : null; s.model = null; }
    setData(slug, doc) { const s = this._s(slug); s.doc = isObj(doc) ? doc : null; s.model = null; s.loadP = s.loadP || Promise.resolve(); this._emit({ type: "load", slug }); }
    model(slug) {
      const s = this.S.get(slug); if (!s) return null;
      if (!s.model) { try { s.model = s.doc ? fromDoc(s.doc) : s.houses ? fromHouses(s.houses, s.meta) : null; } catch (e) { console.warn("PoolService model", slug, e); s.model = null; } }
      return s.model;
    }
    _pool(slug, id) { return this.model(slug)?.pools.find((p) => p.id === String(id)) || null; }

    // ------------------------------------------------------------ deck layers
    // ctx = { slug, coordinateOrigin:[lon,lat,zOffset], zBase, zOf:(x,y)=>localGroundZ, houseColor:[r,g,b], exceptHouseId, route }
    // Only pools with a traced `outline` get a shape (never a circle). Pool shapes belong to their house: the always-on lens
    // draws them faintly in the house colour family; poolLayersForHouse() lights one up with the house selection glow.
    _ctx(ctx) {
      const dk = D(), M = dk ? this.model(ctx?.slug) : null, o = arr(ctx?.coordinateOrigin); if (!M || o.length < 2) return null;
      const O = [+o[0], +o[1], num(+o[2]) ?? num(ctx.zBase) ?? 0], zOf = ctx.zOf;
      const gz = (x, y) => { try { return num(typeof zOf === "function" ? zOf(x, y) : null) ?? 0; } catch { return 0; } };
      const key = O.join(",") + "|" + (typeof zOf === "function" ? "z" : "0");
      if (M._key !== key) {   // geometry cache per origin / ground function
        M._key = key;
        M._geo = M.pools.filter((p) => p.x != null && p.y != null).map((p) => {
          const ring = p.outline;
          if (!ring) return { p, id: p.house_id, address: p.address, path: null, segs: [], L: 0, z: gz(p.x, p.y) };
          const z = Math.min(...ring.map(([x, y]) => gz(x, y))) + 0.2;   // flat, just above the water
          const path = [...ring, ring[0]].map(([x, y]) => [x, y, z]), segs = []; let L = 0;
          for (let i = 0; i + 1 < path.length; i++) { const a = path[i], b = path[i + 1], len = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.ceil(len / 0.4));
            for (let k = 0; k < n; k++) { const f0 = k / n, f1 = (k + 1) / n; segs.push({ path: [[a[0] + (b[0] - a[0]) * f0, a[1] + (b[1] - a[1]) * f0, z], [a[0] + (b[0] - a[0]) * f1, a[1] + (b[1] - a[1]) * f1, z]], s: L + len * (f0 + f1) / 2 }); }
            L += len; }
          segs.forEach((s) => (s.L = L));
          // `id` = house id, so BlockManager.pick() (blockRole "house" -> B.idOf(o) = o.id) selects the pool's house
          return { p, id: p.house_id, address: p.address, path, segs, L, z };
        });
      }
      const houseCol = Array.isArray(ctx.houseColor) ? ctx.houseColor.slice(0, 3) : houseColor();
      const base = { coordinateSystem: dk.COORDINATE_SYSTEM.METER_OFFSETS, coordinateOrigin: O, poolSlug: ctx.slug, blockSlug: ctx.slug, parameters: { depthWriteEnabled: false } };
      return { dk, M, O, gz, G: M._geo, base, houseCol, id: (s) => `pool:${ctx.slug}:${s}` };
    }
    // always-on lens: faint pool outlines in the house colour family (tinted toward the water state) + the service route
    layers(ctx = {}) {
      if (!this.enabled) return [];
      const C = this._ctx(ctx); if (!C) return [];
      const { dk, M, G, base, houseCol, id, gz } = C, ly = [], rings = G.filter((g) => g.path);
      const tint = (p) => mix(houseCol, themeCol(p.cls), p.cls === "clean" ? 0.25 : 0.55);
      const skip = ctx.exceptHouseId != null ? String(ctx.exceptHouseId) : null, show = rings.filter((g) => g.id !== skip);
      if (show.length) {
        ly.push(new dk.PolygonLayer({ ...base, id: id("fill"), data: show, getPolygon: (g) => g.path, filled: true, stroked: false, getFillColor: (g) => [...tint(g.p), 26],
          pickable: true, poolRole: "pool", blockRole: "house", updateTriggers: { getFillColor: houseCol.join() } }));
        ly.push(new dk.PathLayer({ ...base, id: id("edge"), data: show, getPath: (g) => g.path, getColor: (g) => [...tint(g.p), 120], getWidth: 1, widthUnits: "pixels", jointRounded: true,
          pickable: true, poolRole: "pool", blockRole: "house", updateTriggers: { getColor: houseCol.join() } }));
      }
      // service route (thin dashed line through pool positions in block.route order)
      const byId = new Map(G.map((g) => [g.p.id, g])), stops = M.block.route.map((rid) => byId.get(rid)).filter(Boolean);
      if (stops.length >= 2 && ctx.route !== false) {
        const path = []; for (let i = 0; i < stops.length; i++) { const a = stops[i]; path.push([a.p.x, a.p.y, a.z + 0.5]);
          const b = stops[i + 1]; if (b) for (let k = 1; k < 8; k++) { const x = a.p.x + ((b.p.x - a.p.x) * k) / 8, y = a.p.y + ((b.p.y - a.p.y) * k) / 8; path.push([x, y, gz(x, y) + 0.5]); } }
        const ext = dk.PathStyleExtension ? { getDashArray: [4, 3], dashJustified: true, extensions: [new dk.PathStyleExtension({ dash: true })] } : {};
        ly.push(new dk.PathLayer({ ...base, id: id("route"), data: [{ path, n: stops.length, computed: M.block.routeComputed }], getPath: (r) => r.path, getColor: [...houseCol, 120], getWidth: 1, widthUnits: "pixels", pickable: true, poolRole: "route", ...ext }));
        if (dk.TextLayer) ly.push(new dk.TextLayer({ ...base, id: id("route-n"), data: stops.map((g, i) => ({ g, n: i + 1 })), getPosition: (s) => [s.g.p.x, s.g.p.y, s.g.z + 1.2], getText: (s) => String(s.n),
          getSize: 11, sizeUnits: "pixels", getColor: [226, 235, 245, 220], fontFamily: "IBM Plex Mono, Consolas, monospace", outlineWidth: 2, outlineColor: [5, 7, 11, 255], fontSettings: { sdf: true }, billboard: true }));
      }
      return ly;
    }
    // the house highlight's pool part. opts.glow: true | {t: seconds since selection, color:[r,g,b]} -> animated shimmer like
    // block.js selectionLayers() (base + additive halo + travelling light band); false -> steady hover outline. [] when no outline.
    poolLayersForHouse(slug, houseId, opts = {}) {
      const C = this._ctx({ ...opts, slug }); if (!C) return [];
      const g = C.G.find((x) => x.id === String(houseId) && x.path); if (!g) return [];
      const { dk, base, id } = C, glow = opts.glow, gcol = (isObj(glow) && Array.isArray(glow.color) ? glow.color : C.houseCol).slice(0, 3);
      const out = [], tag = { ...base, poolRole: "pool", blockRole: "house" };
      out.push(new dk.PolygonLayer({ ...tag, id: id("sel-fill"), data: [g], getPolygon: (x) => x.path, filled: true, stroked: false, getFillColor: [...gcol, glow ? 46 : 34], pickable: true }));
      if (!glow) { out.push(new dk.PathLayer({ ...base, id: id("hover-edge"), data: [g], getPath: (x) => x.path, getColor: [...lift(gcol, 0.3), 230], getWidth: 2, widthUnits: "pixels", jointRounded: true })); return out; }
      if (!this._glowT0) this._glowT0 = performance.now();
      const t = isObj(glow) && num(glow.t) != null ? glow.t : (performance.now() - this._glowT0) / 1000, key = Math.round(t * 60);
      const nb = Math.max(1, Math.round(g.L / 30)), I = (d) => bandI(d.s, d.L, t, nb), w = 2.4;
      const blend = { depthWriteEnabled: false, blend: true, blendColorOperation: "add", blendColorSrcFactor: "src-alpha", blendColorDstFactor: "one", blendAlphaOperation: "add", blendAlphaSrcFactor: "one", blendAlphaDstFactor: "one-minus-src-alpha" };
      out.push(new dk.PathLayer({ ...base, id: id("sel-base"), data: [g], getPath: (x) => x.path, getColor: [...gcol, 95], getWidth: w * 0.8, widthUnits: "pixels", jointRounded: true }));
      out.push(new dk.PathLayer({ ...base, id: id("sel-halo"), data: g.segs, getPath: (d) => d.path, getColor: (d) => [...gcol, Math.round(150 * I(d) ** 1.5)], getWidth: w * 4.5, widthUnits: "pixels", parameters: blend, updateTriggers: { getColor: key } }));
      out.push(new dk.PathLayer({ ...base, id: id("sel-core"), data: g.segs, getPath: (d) => d.path, getColor: (d) => { const i = I(d); return [...lift(gcol, 0.65 * i), Math.round(60 + 195 * i)]; }, getWidth: w, widthUnits: "pixels", updateTriggers: { getColor: key } }));
      return out;
    }
    // pool shape picked -> select its house (emits selectHouse; BlockManager.pick() also handles these layers via blockRole "house")
    pick(info) {
      const ly = info?.layer, o = info?.object, slug = ly?.props?.poolSlug; if (!slug || !o || ly.props.poolRole !== "pool") return null;
      const p = o.p || o; if (p?.house_id == null) return null;
      const r = { type: "selectHouse", slug, houseId: p.house_id, poolId: p.id }; this._emit(r); return r;
    }
    tooltip(info) {
      const ly = info?.layer, o = info?.object; if (!ly?.props?.poolSlug || !o) return null;
      if (ly.props.poolRole === "route") return `Pool service route · ${o.n} stops${o.computed ? " (nearest-neighbour order, computed here)" : ""}`;
      const p = o.p || o; if (!p?.id) return null;
      const M = this.model(ly.props.poolSlug), stop = M ? M.block.route.indexOf(p.id) : -1;
      return [`${p.address || "House " + (p.house_id ?? DASH)} · pool`, `${STATES[p.cls].label}${p.area_m2 != null ? " · " + Math.round(p.area_m2) + " m²" : ""}`,
        p.lead?.score != null ? `pool-service lead ${Math.round(p.lead.score)} / 100` : "", stop >= 0 ? `route stop ${stop + 1} of ${M.block.route.length}` : ""].filter(Boolean).join("\n");
    }

    // ------------------------------------------------------------ panel: house
    houseHtml(slug, houseId) {
      if (!this.enabled) return "";
      const M = this.model(slug); if (!M) return "";
      const p = M.pools.find((q) => q.house_id === String(houseId));
      if (!p) return `<div class="ps-sec"><h4>Pool service</h4><div class="sub">No pool mapped for this house in ${esc(M.src)}.</div></div>`;
      const st = STATES[p.cls], stop = M.block.route.indexOf(p.id), T = p.trees || {}, S = p.sun || {}, Gd = p.ground || {}, V = p.visit || {}, L = p.lead || {};
      const rows = [];
      rows.push(`<dt>Water now</dt><dd><span class="ps-s-${p.cls}"><i class="ps-dot"></i></span><span class="chip ${st.chip}">${esc(st.label)}</span>${p.state_now_date ? ` <span class="sub">imagery ${esc(p.state_now_date)}</span>` : ""}</dd>`);
      rows.push(`<dt>Surface area</dt><dd>${m2ft(p.area_m2)}</dd>`, `<dt>Outline</dt><dd>${p.outline ? esc(p.outline_source || "traced in the data") : "no traced outline in the data, so no shape is drawn"}</dd>`, `<dt>Belongs to</dt><dd>${p.owner ? (p.owner.method === "parcel" ? "this house: the pool lies inside its parcel" + (p.owner.parcel_id ? " " + esc(p.owner.parcel_id) : "") : "this house: its nearest building of all nearby footprints (" + esc(p.owner.nearest_m) + " m; next other house " + (p.owner.runner_up_m != null ? esc(p.owner.runner_up_m) + " m" : DASH) + "), no open parcel lines here · " + esc(p.owner.confidence) + " confidence") : DASH}</dd>`, `<dt>Detection</dt><dd>${p.confidence != null ? pct(p.confidence) + " confidence" : DASH} · first seen ${p.first_seen_year != null && p.first_seen_year !== "" ? esc(p.first_seen_year) : DASH}</dd>`);
      if (p.history.length) rows.push(`<dt>By imagery year</dt><dd><div class="ystrip">${p.history.map((h) => { const k = stateOf(h.state), c = themeCol(k);
        return `<span title="${esc(`${h.year}${h.date ? " (" + h.date + ")" : ""}: ${STATES[k].label}`)}"><i style="${k === "unk" || k === "none" ? "background:transparent;border-style:dashed" : `background:rgb(${c.join(",")})`}"></i>’${esc(String(h.year).slice(-2))}</span>`; }).join("")}</div></dd>`);
      rows.push(`<dt>Route</dt><dd>${stop >= 0 ? `stop ${stop + 1} of ${M.block.route.length}${M.block.routeComputed ? " (computed order)" : ""} ` : DASH + " "}<button class="ps-btn" data-ps-pool="${esc(slug)}|${esc(p.id)}">show</button></dd>`);
      rows.push(`<dt>Trees</dt><dd>tallest ${d(num(T.max_h_m), " m")} · ${d(num(T.n_over_3m_within_10m))} over 3 m within 10 m · canopy over pool ${num(T.canopy_over_pool_share) != null ? pct(T.canopy_over_pool_share) : DASH} · overhang ${yesNo(T.overhang)}</dd>`);
      rows.push(`<dt>Sun</dt><dd>direct ${d(num(S.direct_hours_jun21), " h")} Jun 21 · ${d(num(S.direct_hours_dec21), " h")} Dec 21 · shade in June ${num(S.shade_share_jun) != null ? pct(S.shade_share_jun) : DASH}</dd>`);
      rows.push(`<dt>Ground</dt><dd>soil shrink-swell ${d(Gd.soil_shrink_swell)} · flood zone ${d(Gd.flood_zone)} · subsidence ${d(num(Gd.subsidence_vu_mm_yr), " mm/yr")}</dd>`);
      const vr = arr(V.reasons).map(reasonText).filter(Boolean);
      rows.push(`<dt>Visits</dt><dd>${num(V.per_week) != null ? esc(V.per_week) + " / week" : DASH}${vr.length ? `<ul class="log">${vr.slice(0, 5).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}</dd>`);
      const lr = arr(L.reasons).map(reasonText).filter(Boolean), sc = num(L.score);
      rows.push(`<dt>Lead</dt><dd>${sc != null ? `<b class="chip ${scoreCls(sc)}">${Math.round(sc)}</b> <span class="sub">0–100</span>` : DASH}${lr.length ? `<ul class="log">${lr.slice(0, 6).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}</dd>`);
      return `<div class="ps-sec"><h4>Pool service</h4><dl>${rows.join("")}</dl>${this._liveHtml(slug, true)}<div class="sub small">From ${esc(M.src)}${M.built ? " · built " + esc(M.built) : ""}. ${DASH} = no data.</div></div>`;
    }

    // ------------------------------------------------------------ panel: block
    blockHtml(slug) {
      if (!this.enabled) return "";
      const M = this.model(slug); if (!M) return "";
      const B = M.block, byId = new Map(M.pools.map((p) => [p.id, p])), out = [];
      const pb = (p, right) => `<li><button data-ps-pool="${esc(slug)}|${esc(p.id)}" title="${esc(STATES[p.cls].label)}"><span><i class="ps-dot" style="color:rgb(${themeCol(p.cls).join(",")})"></i>${esc(p.address || "pool " + p.id)}</span><b>${right}</b></button></li>`;
      const dotted = (p) => pb(p, esc(STATES[p.cls].label.split(" ")[0]));
      out.push(`<div class="ps-sec"><div class="lbl">Lens · pool service</div><h4 style="margin-top:2px">Pool service</h4>`);
      out.push(`<div class="stats"><div><span class="v">${d(B.n_pools)}</span><span class="l">pools on the block</span></div><div><span class="v">${d(B.n_houses)}</span><span class="l">houses</span></div>
        <div><span class="v">${d(B.pools_within_100m)}</span><span class="l">pools within 100 m</span></div><div><span class="v">${d(M.pools.filter((p) => p.cls === "green").length)}</span><span class="l">green now</span></div></div>`);
      // this week's triggers
      out.push(`<h4>This week's triggers</h4>${this._liveHtml(slug, false)}`);
      // route
      const route = B.route.map((id) => byId.get(id)).filter(Boolean);
      if (route.length) out.push(`<h4>Route order</h4>${B.routeComputed ? `<div class="sub small">No service route in the data: nearest-neighbour order computed here from pool positions.</div>` : B.routeRule ? `<div class="sub small">${esc(B.routeRule)}</div>` : ""}<ol class="hlist ps-route">${route.map((p) => pb(p, p.area_m2 != null ? Math.round(p.area_m2) + " m²" : DASH)).join("")}</ol>`);
      // per street
      const streets = B.streets.filter((s) => s.pool_ids.length);
      if (streets.length) out.push(`<h4>Pools per street</h4>${streets.map((s) => `<div class="sub small">${esc(s.name || "unnamed street")} · ${s.pool_ids.length}</div><ol class="hlist">${s.pool_ids.map((id) => byId.get(id)).filter(Boolean).map(dotted).join("")}</ol>`).join("")}`);
      // top leads
      const leads = M.pools.filter((p) => num(p.lead?.score) != null).sort((a, b) => b.lead.score - a.lead.score).slice(0, 5);
      if (leads.length) out.push(`<h4>Top leads</h4><ol class="hlist">${leads.map((p) => `<li><button data-ps-house="${esc(slug)}|${esc(p.house_id ?? "")}"><span>${esc(p.address || "pool " + p.id)}</span><b class="chip ${scoreCls(p.lead.score)}">${Math.round(p.lead.score)}</b></button></li>`).join("")}</ol>`);
      // climate + risks
      const C = B.climate || {}, R = B.risks || {}, sub = isObj(R.subsidence) ? R.subsidence : {};
      out.push(`<h4>Climate &amp; ground (area)</h4><dl>
        <dt>Dust days</dt><dd>${d(num(C.dust_days_yr), " / yr")}</dd><dt>High-wind days</dt><dd>${d(num(C.high_wind_days_yr), " / yr")}</dd>
        <dt>Dust then rain</dt><dd>${d(num(C.dust_then_rain_yr), " / yr")}</dd><dt>Hail days</dt><dd>${d(num(C.hail_days_yr), " / yr")}</dd>
        <dt>PM2.5 design value</dt><dd>${d(num(C.pm25_dv), " µg/m³")}</dd><dt>Ozone design value</dt><dd>${num(C.o3_dv) == null ? DASH : C.o3_dv > 1 ? esc(C.o3_dv) + " ppb" : esc(C.o3_dv) + " ppm"}</dd>
        <dt>Sun</dt><dd>${d(num(C.sun_kwh_m2_day), " kWh/m²/day")}${C.uv_note ? " · " + esc(C.uv_note) : ""}</dd>
        <dt>West Nile (county)</dt><dd>${this._wnv(R.wnv_county)}</dd><dt>Radon zone</dt><dd>${d(R.radon_zone)}</dd>
        <dt>Subsidence</dt><dd>${num(sub.vu_mm_yr) != null ? esc(sub.vu_mm_yr) + " mm/yr" : DASH}${sub.station ? " · station " + esc(sub.station) : ""}${num(sub.dist_km) != null ? " (" + esc(sub.dist_km) + " km)" : ""}</dd></dl>`);
      // market
      const mk = this._marketRows(B.market);
      out.push(`<h4>Market</h4>${mk.length ? `<dl>${mk.join("")}</dl>` : `<div class="sub">${DASH} no market data in ${esc(M.src)}</div>`}`);
      // sources
      out.push(`<h4>Sources</h4><ul class="src">${M.sources.map((s) => `<li><b>${esc(s.name || "source")}</b>${s.licence ? " · " + esc(s.licence) : ""}${/^https?:/i.test(String(s.url || "")) ? ` · <a href="${esc(s.url)}" target="_blank" rel="noopener">link</a>` : ""}</li>`).join("")}<li><b>NOAA / National Weather Service API</b> · alerts &amp; forecast · US public domain</li></ul>`);
      if (M.notAvailable?.length) out.push(`<div class="sub small">Not in any open data: ${M.notAvailable.map(esc).join("; ")}.</div>`);
      out.push(`<div class="sub small">From ${esc(M.src)}${M.built ? " · built " + esc(M.built) : ""}. ${DASH} = no data.</div></div>`);
      return out.join("\n");
    }
    _wnv(w) {
      if (w == null || w === "") return DASH;
      if (!isObj(w)) return yesNo(w);
      const bits = [num(w.years_with_human_cases) != null ? `human cases in ${w.years_with_human_cases} yrs${w.period ? " of " + w.period : ""}` : num(w.years_with_activity) != null ? `activity in ${w.years_with_activity} yrs${w.period ? " of " + w.period : ""}` : "",
        num(w.human_cases_total) != null ? `${fmt.format(w.human_cases_total)} human cases total` : "", w.last_year_human != null ? `last human case ${w.last_year_human}` : w.last_year_activity != null ? `last activity ${w.last_year_activity}` : ""].filter(Boolean);
      return bits.length ? esc(bits.join(" · ")) : DASH;
    }
    _marketRows(m) {
      if (!isObj(m)) return [];
      const rows = [], put = (k, v) => { if (rows.length >= 14) return; if (v == null || v === "") return;
        if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") rows.push(`<dt>${esc(labelOf(k))}</dt><dd>${typeof v === "number" ? esc(fmt.format(v)) : esc(String(v))}</dd>`);
        else if (Array.isArray(v)) { const s = v.filter((x) => typeof x !== "object").slice(0, 6).join(", "); if (s) rows.push(`<dt>${esc(labelOf(k))}</dt><dd>${esc(s)}</dd>`); } };
      for (const [k, v] of Object.entries(m)) { if (isObj(v)) { for (const [k2, v2] of Object.entries(v)) if (!isObj(v2)) put(k + " · " + k2, v2); } else put(k, v); }
      return rows;
    }
    _liveHtml(slug, compact) {
      const L = this._liveBy.get(slug), s = this.S.get(slug), canAsk = Array.isArray(s?.meta?.origin);
      const btn = canAsk ? ` <button class="ps-btn" data-ps-live="${esc(slug)}">${L ? "refresh" : "check NWS forecast"}</button>` : "";
      if (!L) return compact ? "" : `<div class="sub small">Live NWS alerts and forecast not loaded.${btn}</div>`;
      if (L.state === "loading") return `<div class="sub small">Checking NWS alerts and the 7-day forecast…</div>`;
      if (L.state === "error") return `<div class="sub small">NWS not reachable (${esc(L.error)}).${btn}</div>`;
      const v = L.v, trig = v.triggers;
      const list = `<ul class="ps-trig">${trig.length ? trig.map((t) => `<li class="${t.level}">${esc(t.text)}${t.note ? `<div class="small">${esc(t.note)}</div>` : ""}</li>`).join("") : `<li>No weather triggers in the NWS 7-day forecast or active alerts.</li>`}</ul>`;
      if (compact) return trig.length ? `<div class="sub small" style="margin-top:6px">This week (NWS)</div>${list}` : "";
      const days = v.days.slice(0, 7), hot = (x, t) => (x != null && x >= t ? ' class="hot"' : "");
      const tbl = days.length ? `<table class="ps-wk"><tr><th></th>${days.map((x) => `<th>${esc(x.dow)}</th>`).join("")}</tr>
        <tr><td>max °F</td>${days.map((x) => `<td${hot(x.maxF, 100)}>${x.maxF != null ? Math.round(x.maxF) : DASH}</td>`).join("")}</tr>
        <tr><td>gust mph</td>${days.map((x) => `<td${hot(x.gust, 30)}>${x.gust != null ? Math.round(x.gust) : DASH}</td>`).join("")}</tr>
        <tr><td>rain %</td>${days.map((x) => `<td${hot(x.pop, 40)}>${x.pop != null ? Math.round(x.pop) : DASH}</td>`).join("")}</tr></table>` : "";
      return `${list}${tbl}<div class="sub small">NWS ${esc(v.office || "")} · fetched ${esc(new Date(v.fetched).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}${btn}</div>`;
    }

    // ------------------------------------------------------------ live triggers (NWS)
    live(slug, lat, lon) {
      const s = this.S.get(slug), o = s?.meta?.origin;
      if (!(isFinite(+lat) && isFinite(+lon)) && Array.isArray(o)) { lon = +o[0]; lat = +o[1]; }
      if (!(isFinite(+lat) && isFinite(+lon))) return Promise.resolve(null);
      lat = +lat; lon = +lon;
      const prev = this._liveBy.get(slug);
      this._liveBy.set(slug, { state: "loading", v: prev?.v }); this._emit({ type: "live", slug, state: "loading" });
      return this._nws(lat, lon, s?.meta?.tz).then((v) => { this._liveBy.set(slug, { state: "ready", v }); this.usedNws = true; this._emit({ type: "live", slug, state: "ready" }); return v; })
        .catch((e) => { this._liveBy.set(slug, { state: "error", error: e.message || String(e) }); this._emit({ type: "live", slug, state: "error" }); return null; });
    }
    async _nws(lat, lon, tzHint) {
      const ll = `${lat.toFixed(4)},${lon.toFixed(4)}`, c = this._liveCache.get(ll);
      if (c && Date.now() - c.t < 30 * 60e3) return c.v;
      if (c?.p) return c.p;
      const p = (async () => {
        const pt = await getJSON(`${NWS}/points/${ll}`), pp = pt?.properties || {}, tz = pp.timeZone || tzHint || undefined;
        const [g, al] = await Promise.all([pp.forecastGridData ? getJSON(pp.forecastGridData).catch(() => null) : null, getJSON(`${NWS}/alerts/active?point=${ll}`).catch(() => null)]);
        const v = this._triggers(g?.properties || null, arr(al?.features), tz); v.office = pp.gridId || pp.cwa || ""; v.fetched = Date.now();
        this._liveCache.set(ll, { t: Date.now(), v }); return v;
      })();
      this._liveCache.set(ll, { t: 0, p });
      try { return await p; } catch (e) { this._liveCache.delete(ll); throw e; }
    }
    _triggers(G, alerts, tz) {
      let dayKey, dowOf;
      try { const f = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }), w = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short" });
        dayKey = (t) => f.format(t); dowOf = (t) => w.format(t); } catch { dayKey = (t) => t.toISOString().slice(0, 10); dowOf = (t) => t.toUTCString().slice(0, 3); }
      const today = dayKey(new Date()), days = new Map();
      const day = (t) => { const k = dayKey(t); if (k < today) return null; if (!days.has(k)) days.set(k, { date: k, dow: dowOf(t), maxF: null, minF: null, gust: null, pop: null }); return days.get(k); };
      const agg = (list, f, mode) => list.forEach(({ t, v }) => { const D_ = day(t); if (!D_) return; D_[f] = D_[f] == null ? v : mode === "min" ? Math.min(D_[f], v) : Math.max(D_[f], v); });
      if (G) {
        agg(series(G.maxTemperature, toF(G.maxTemperature?.uom), false), "maxF", "max");
        agg(series(G.minTemperature, toF(G.minTemperature?.uom), false), "minF", "min");
        agg(series(G.windGust, toMph(G.windGust?.uom), true), "gust", "max");
        agg(series(G.probabilityOfPrecipitation, (x) => x, true), "pop", "max");
      }
      const D7 = [...days.values()].sort((a, b) => a.date.localeCompare(b.date)).slice(0, 7), T = [];
      const dl = (xs) => xs.map((x) => x.dow).join(", ");
      // active alerts first
      const seen = new Set();
      for (const f of alerts) { const a = f?.properties || {}, ev = String(a.event || ""); const rule = ALERTS.find(([re]) => re.test(ev)); if (!rule || seen.has(ev)) continue; seen.add(ev);
        let until = ""; const e = Date.parse(a.ends || a.expires || ""); if (isFinite(e)) { try { until = " until " + new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", hour: "numeric" }).format(new Date(e)); } catch {} }
        T.push({ level: rule[1], text: `${ev}${until}: ${rule[2]}`, note: a.headline || "", source: "NWS alert" }); }
      // forecast
      const gusty = D7.filter((x) => x.gust != null && x.gust >= 30);
      if (gusty.length) { const top = gusty.reduce((a, b) => (b.gust > a.gust ? b : a)); T.push({ level: top.gust >= 40 ? "bad" : "warn", text: `Wind gusts ${Math.round(top.gust)} mph ${top.dow}${gusty.length > 1 ? ` (gusty ${dl(gusty)})` : ""}: plan post-storm cleans`, source: "NWS forecast" }); }
      for (const t of [105, 100, 95]) { const hot = D7.filter((x) => x.maxF != null && x.maxF >= t); if (hot.length) { T.push({ level: t >= 105 ? "warn" : "", text: `${hot.length} day${hot.length > 1 ? "s" : ""} ≥ ${t}°F (${dl(hot)}): chlorine demand up`, source: "NWS forecast" }); break; } }
      const wet = D7.filter((x) => x.pop != null && x.pop >= 40);
      if (wet.length) { const top = wet.reduce((a, b) => (b.pop > a.pop ? b : a)); T.push({ level: "", text: `Rain chance ${Math.round(top.pop)}% ${top.dow}${wet.length > 1 ? ` (${dl(wet)})` : ""}: rain-after-dust spotting, recheck chemistry after`, source: "NWS forecast" }); }
      const cold = D7.filter((x) => x.minF != null && x.minF <= 34);
      if (cold.length) { const top = cold.reduce((a, b) => (b.minF < a.minF ? b : a)); T.push({ level: "warn", text: `Low ${Math.round(top.minF)}°F ${top.dow}: freeze guard, keep pumps running overnight`, source: "NWS forecast" }); }
      return { days: D7, alerts: alerts.map((f) => f?.properties).filter(Boolean).map((a) => ({ event: a.event, headline: a.headline, ends: a.ends, severity: a.severity })), triggers: T, tz: tz || null, hasGrid: !!G };
    }

    // ------------------------------------------------------------ wiring
    bind(el) {
      if (!el || el._psBound) return; el._psBound = true;
      el.addEventListener("click", (e) => {
        const t = e.target.closest?.("[data-ps-pool],[data-ps-house],[data-ps-live]"); if (!t || !el.contains(t)) return;
        e.stopPropagation();
        if (t.hasAttribute("data-ps-live")) { this.live(t.getAttribute("data-ps-live")); return; }
        if (t.hasAttribute("data-ps-house")) { const [slug, houseId] = String(t.getAttribute("data-ps-house")).split("|"); this._emit({ type: "selectHouse", slug, houseId }); return; }
        const [slug, poolId] = String(t.getAttribute("data-ps-pool")).split("|"), p = this._pool(slug, poolId); if (!p) return;
        this._emit({ type: "focusPool", slug, poolId: p.id, houseId: p.house_id, x: p.x, y: p.y });
      });
    }
    credits() {
      const names = new Set(); for (const [slug] of this.S) { const M = this.model(slug); if (M) M.sources.forEach((s) => s.name && names.add(String(s.name).replace(/\s*\(.*$/, ""))); }
      const parts = [...names].slice(0, 3); if (this.usedNws) parts.push("NOAA/NWS (public domain)");
      return this.enabled && parts.length ? "Pool service: " + parts.join(" · ") : "";
    }
  }
  PoolService.STATES = STATES;
  PoolService.stateOf = stateOf;
  globalThis.PoolService = PoolService;
})();

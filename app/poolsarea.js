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
    { slug: "phoenix_metro_lidar", name: "Phoenix metro · LiDAR water holes", kind: "holes" },
  ];
  const ST = {
    clean_blue: [[30, 144, 255], "clear blue"], turquoise: [[0, 235, 215], "turquoise"], green_algae: [[18, 130, 50], "dark green (algae)"],
    covered_or_empty: [[235, 200, 130], "covered or empty"], dark: [[170, 140, 255], "dark"], filled_or_removed: [[150, 150, 150], "filled in / removed"],
    not_present: [[110, 110, 110], "not present"],
  };
  const col = (s) => (ST[s] || [[200, 200, 200]])[0];
  const rim = (c, k) => c.map((v) => Math.round(v + (255 - v) * (0.35 + 0.4 * k)));   // shimmering rim, towards white
  const lit = (c, k) => c.map((v) => Math.min(255, Math.round(v * (0.78 + 0.42 * k))));   // brighten / dim a colour for the shimmer
  const CARE = { clean_blue: "looks clean", turquoise: "looks clean (light / shallow water)", green_algae: "likely needs cleaning (algae green)",
    covered_or_empty: "covered or drained", dark: "dark water: check (shade, debris or algae)", filled_or_removed: "filled in", not_present: "not there that year" };
  // deck.gl 9: draw over the LiDAR points (depthTest is ignored there); the glow adds light like water catching the sun
  const ON_TOP = { depthWriteEnabled: false, depthCompare: "always" };
  const GLOW = { ...ON_TOP, blendColorSrcFactor: "src-alpha", blendColorDstFactor: "one", blendAlphaSrcFactor: "one", blendAlphaDstFactor: "one-minus-src-alpha" };
  const MINZ = 12.5, MAX_TILES = 40, CONC = 4, HOUSE_Z = 15.5, HOUSE_M = 40;
  // city-wide building footprints (usa/build_city_buildings.py) for the areas that have no block build
  // every building in these areas is clickable, pools or not (usa/build_metro_buildings.py)
  const HOUSE_AREAS = [{ slug: "phoenix_metro_buildings", bbox: [-112.8, 32.95, -111.4, 34.0] }];
  const BLD = { scottsdale: 1, scottsdale_lidar: 1, phoenix_metro_lidar: 1 };
  const M_LAT = 110540, mLon = (lat) => 111320 * Math.cos(lat * Math.PI / 180);
  const inRing = (x, y, r) => { let c = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const a = r[i], b = r[j]; if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
  // metres from a point to a footprint (0 inside)
  const distRing = (x, y, r) => {
    if (inRing(x, y, r)) return 0; const kx = mLon(y); let best = Infinity;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const ax = (r[j][0] - x) * kx, ay = (r[j][1] - y) * M_LAT, bx = (r[i][0] - x) * kx, by = (r[i][1] - y) * M_LAT, dx = bx - ax, dy = by - ay;
      const t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy || 1))); best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
    }
    return best;
  };
  const holeState = (o) => { const ph = o.photo || {}; return /water/.test(ph.verdict || "") || /confirmed/.test(o.label || "") ? (ph.state || "clean_blue") : null; };
  const pState = (o) => o.akind === "holes" ? holeState(o) : o.latest_state;
  // filter groups (latest photo): what a pool-service crew cares about
  const SHOW = [["clean", "Clear blue / turquoise (clean)", ["clean_blue", "turquoise"]], ["green", "Green (likely needs cleaning)", ["green_algae"]],
    ["dark", "Dark water (check)", ["dark"]], ["covered", "Covered, drained or filled", ["covered_or_empty", "filled_or_removed", "not_present"]], ["nowater", "LiDAR hole, photo says not water", [null]]];
  const groupOf = (o) => { const st = pState(o); const g = SHOW.find((x) => x[2].includes(st == null ? null : st)); return g ? g[0] : "covered"; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  class PoolsArea {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {}); this.groundAt = opts.groundAt || (() => 0); this.groundExact = opts.groundExact || (() => null); this.onFocus = opts.onFocus || (() => {}); this.focus = "";
      this.show = new Set(SHOW.map((x) => x[0])); this.onFrame = opts.onFrame || this.onUpdate;
      this.bidx = {}; this.btiles = new Map(); this.bgrid = new Map(); this.selHouse = null; this.selPools = new Set();
      this._zTimer = setInterval(() => this._zRefresh(), 1500);
      this._t = 0; this._shTimer = setInterval(() => { if (this.on.size && this.view && this.view.zoom >= 14.5 && !document.hidden && this.tiles.size) { this._t = performance.now() / 1000; this.onFrame(); } }, 110);
      this.on = new Set(); this.idx = {}; this.tiles = new Map(); this.view = null; this.sel = null; this.panel = null; this._ver = 0; this._cache = null; this.timer = 0;
    }
    getState() { const o = { on: [...this.on] }; if (this.focus) o.focus = this.focus; if (this.show.size < SHOW.length) o.show = [...this.show]; return o; }
    setState(o = {}) { if (Array.isArray(o.show)) this.show = new Set(o.show); if (o.focus) { this.focus = typeof o.focus === "string" ? o.focus : "focus"; setTimeout(() => this.onFocus(this.focus), 0); } if (Array.isArray(o.on)) this.on = new Set(o.on.filter((k) => AREAS.some((a) => a.slug === k))); for (const k of this.on) this._index(k); this._dirty(); this._render(); }
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
      const v = this.view; if (!v || !v.bounds || v.zoom < Math.min(MINZ, HOUSE_Z - 0.5) || v.globe) { this._render(); return; }
      let [w, s, e, n] = v.bounds; const want = [];
      for (const k of this.on) {
        const I = this.idx[k]; if (!I || I.state !== "ok") continue;
        for (const t of I.j.tiles || []) {
          const b = t.bbox; if (!b || b[2] < w || b[0] > e || b[3] < s || b[1] > n) continue;
          if (t.status && !/done|ok|final/.test(t.status)) continue;
          if (!t.id && t.file) t.id = t.file.replace(/\.json$/, "");
          want.push([k, t.id, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, t.file || t.id + ".json"]);
        }
      }
      if (v.zoom >= HOUSE_Z - 0.5) for (const k of HOUSE_AREAS.filter((a) => !(a.bbox[2] < w || a.bbox[0] > e || a.bbox[3] < s || a.bbox[1] > n)).map((a) => a.slug)) {
        const I = this.bidx[k]; if (!I) { this._bindex(k); continue; } if (I.state !== "ok") continue;
        for (const t of I.j.tiles || []) { const b = t.bbox; if (b[2] < w || b[0] > e || b[3] < s || b[1] > n) continue; want.push([k, t.id, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, t.file, true]); }
      }
      const cx = (w + e) / 2, cy = (s + n) / 2;
      want.sort((a, b) => Math.hypot(a[2] - cx, a[3] - cy) - Math.hypot(b[2] - cx, b[3] - cy));
      const q = want.slice(0, MAX_TILES + 12).filter(([k, id, , , , isB]) => !(isB ? this.btiles : this.tiles).has(k + "/" + id));
      const busy = () => [...this.tiles.values(), ...this.btiles.values()].filter((t) => t.state === "loading").length;
      const pump = () => { while (q.length && busy() < CONC) { const a = q.shift(); (a[5] ? this._btile(...a) : this._tile(...a)).finally(pump); } };
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
    async _bindex(k) {
      this.bidx[k] = { state: "loading" };
      try { const r = await fetch(`data/pools_area/${k}/index.json`); if (!r.ok) throw new Error("HTTP " + r.status); this.bidx[k] = { state: "ok", j: await r.json() }; }
      catch (e) { this.bidx[k] = { state: "err", err: e.message }; }
      this._schedule(0);
    }
    async _btile(k, id, cx, cy, file) {
      const key = k + "/" + id, t = { state: "loading", list: [] }; this.btiles.set(key, t);
      try {
        const r = await fetch(`data/pools_area/${k}/${file}`); if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json(), [ox, oy] = j.o, q = j.q || 1e-6;
        t.list = j.b.map((v, i) => { const ring = []; let sx = 0, sy = 0, ix = 0, iy = 0;
          for (let m = 1; m + 1 < v.length; m += 2) { if (j.d && m > 1) { ix += v[m]; iy += v[m + 1]; } else { ix = v[m]; iy = v[m + 1]; } const x = ox + ix * q, y = oy + iy * q; ring.push([x, y]); sx += x; sy += y; }
          return { id: id + ":" + i, ring, area_m2: v[0], centre: [sx / ring.length, sy / ring.length], akind: "house" }; });
        for (const h of t.list) for (const c of this._cells(h.ring)) { let a = this.bgrid.get(c); if (!a) this.bgrid.set(c, a = []); a.push(h); }
        t.state = "ok";
      } catch (e) { t.state = "err"; t.err = e.message; }
      for (const p of this._list()) if (p._house === null) p._house = undefined;   // retry pools that had no house yet
      this._dirty(); this._render(); this.onUpdate();
    }
    _cells(ring) { const out = new Set(); let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; for (const [x, y] of ring) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      for (let i = Math.floor(x0 / 5e-4); i <= Math.floor(x1 / 5e-4); i++) for (let j = Math.floor(y0 / 5e-4); j <= Math.floor(y1 / 5e-4); j++) out.add(i + ":" + j); return out; }
    _houses() { const out = []; for (const [key, t] of this.btiles) if (t.state === "ok") for (const h of t.list) out.push(h); return out; }
    // the house a pool belongs to: the nearest footprint within HOUSE_M metres (no open lot lines in Maricopa County)
    _houseOf(p) {
      if (p._house !== undefined) return p._house;
      const [x, y] = p.centre || p.ring[0], i0 = Math.floor(x / 5e-4), j0 = Math.floor(y / 5e-4); let best = null, bd = HOUSE_M; const seen = new Set();
      for (let i = i0 - 1; i <= i0 + 1; i++) for (let j = j0 - 1; j <= j0 + 1; j++) for (const h of this.bgrid.get(i + ":" + j) || []) {
        if (seen.has(h)) continue; seen.add(h); if (h.area_m2 < 40) continue;   // sheds and ramadas are not the house
        const d = distRing(x, y, h.ring); if (d < bd) { bd = d; best = h; }
      }
      p._house = best; if (best) p._houseM = Math.round(bd * 10) / 10; return best;
    }
    _poolsOf(h) { if (!h) return []; const [x, y] = h.centre; return this._list().filter((p) => { const c = p.centre || p.ring[0]; return Math.abs(c[0] - x) < 0.0012 && Math.abs(c[1] - y) < 0.0012 && this._houseOf(p) === h; }); }
    _select(h, p) {
      if (h && this.selHouse === h && (!p || this.selPools.has(p))) { h = null; p = null; }
      this.selHouse = h; this.selPools = new Set(h ? this._poolsOf(h) : []); if (p) this.selPools.add(p);
      this.sel = p && p.akind === "pools" ? p : [...this.selPools].find((q) => q.akind === "pools") || null;
      this._dirty(); this._render(); this.onUpdate();
    }
    // LiDAR ground arrives after the pools: keep trying until every drawn pool and house sits on measured ground
    _zRefresh() {
      if (!this.view || !this.view.bounds || this.view.zoom < MINZ || (!this.on.size && !this.btiles.size)) return;
      const [w, s, e, n] = this.view.bounds; let tries = 0, hit = 0;
      for (const p of [...this._list(), ...this._houses()]) {
        if (p._zok) continue; const c = p.centre || p.ring[0]; if (c[0] < w || c[0] > e || c[1] < s || c[1] > n) continue;
        if (++tries > 400) break;
        const g = this._gx(p); if (g != null) { p._z = g + 0.25; p._zok = true; hit++; }
      }
      if (hit) { this._dirty(); this.onUpdate(); }
    }
    _gx(p) {   // measured ground: at the centre, else (houses: no ground under the roof) just outside the walls
      const c = p.centre || p.ring[0]; let g = this.groundExact(c[0], c[1]); if (g != null && isFinite(g)) return g;
      if (p.akind !== "house") return null;
      const zs = []; for (let i = 0; i < p.ring.length && zs.length < 3; i += Math.max(1, p.ring.length >> 2)) {
        const v = p.ring[i], x = v[0] + (v[0] - c[0]) * 0.15, y = v[1] + (v[1] - c[1]) * 0.15; g = this.groundExact(x, y); if (g != null && isFinite(g)) zs.push(g); }
      return zs.length ? zs.sort((a, b) => a - b)[zs.length >> 1] : null;
    }
    _list() { const out = []; for (const [key, t] of this.tiles) if (t.state === "ok" && this.on.has(key.split("/")[0])) for (const p of t.list) out.push(p); return out; }
    _z(p) {   // ground under the pool: built block > streamed LiDAR ground (class 2 around the water); ETOPO only until LiDAR loads
      if (p._z == null) { const c = p.centre || p.ring[0], g = this._gx(p);
        if (g != null) { p._z = g + 0.25; p._zok = true; } else p._z = this.groundAt(c[0], c[1]) + 0.25; }
      return p._z;
    }

    layers({ globe, zoom, modelMatrix } = {}) {
      const D = window.deck; if (!D || globe || (zoom || 0) < MINZ - 0.5 || (!this.on.size && !this.btiles.size)) return [];
      const mm = modelMatrix ? { modelMatrix } : {}, all = this._list(), L = [];
      const ring3 = (r, z) => r.map((q) => [q[0], q[1], z]);
      const vis = (p) => this.show.has(groupOf(p)) || this.selPools.has(p);
      const pools = all.filter((p) => p.akind === "pools" && vis(p)), holes = all.filter((p) => p.akind === "holes" && vis(p));
      const sel = this.sel, sp = this.selPools, sh = this.selHouse, sv = this._ver, t = this._t;
      const far = (zoom || 0) < 16.5;   // zoomed out: a thicker shimmering rim so every pool stays findable (no markers)
      // shimmer: each pool flickers on its own phase, like sun on moving water; green (algae) pools pulse slower and deeper
      const ph = (d) => { if (d._ph == null) { const c = d.centre || d.ring[0]; d._ph = ((c[0] * 9973 + c[1] * 7919) % 1 + 1) % 1 * 6.283; } return d._ph; };
      const shim = (d) => { const st = pState(d), f = st === "green_algae" ? 1.3 : 2.6, a = Math.sin(t * f + ph(d)), b = Math.sin(t * f * 2.3 + ph(d) * 1.7);
        return 0.5 + 0.32 * a + 0.18 * b; };
      const houses = (zoom || 0) >= HOUSE_Z ? this._houses() : [];
      // every footprint is clickable (invisible until hovered); depth-tested so built 3D blocks keep their own houses
      if (houses.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:bld", data: houses, pickable: true, stroked: false, filled: true, autoHighlight: true, highlightColor: [255, 255, 255, 45],
        getPolygon: (d) => ring3(d.ring, this._z(d) + 0.05), getFillColor: [255, 255, 255, 1], updateTriggers: { getPolygon: sv } }));
      if (sel && sel.parcel && sel.parcel.ring) { const c = col(sel.latest_state), z = this._z(sel);
        L.push(new D.PolygonLayer({ ...mm, id: "pa:lot", data: [sel], pickable: false, stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
          parameters: ON_TOP, getPolygon: (d) => ring3(d.parcel.ring, z - 0.1), getFillColor: [...c, 40], getLineColor: [...c, 255] })); }
      const hr = sh ? sh : sel && sel.building_ring ? { ring: sel.building_ring, centre: sel.owner && sel.owner.building_centroid || sel.centre } : null;
      if (hr) {   // the selected house on the ground, in its pool's colour (white when no pool was found)
        const fp = [...sp][0], c = fp && pState(fp) ? col(pState(fp)) : [235, 235, 235];
        L.push(new D.PolygonLayer({ ...mm, id: "pa:house", data: [hr], pickable: false, stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
          parameters: ON_TOP, getPolygon: (d) => ring3(d.ring, this._z(d) + 0.1), getFillColor: [...c, 70], getLineColor: [...c, 240] }));
      }
      const cased = [...pools, ...holes.filter((h) => holeState(h))];
      if (cased.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:casing", data: cased, pickable: false, stroked: true, filled: false, lineWidthUnits: "pixels",
        getLineWidth: (d) => (sp.has(d) ? 2.5 : far ? 2.6 : 1.2) + 3, parameters: ON_TOP, getPolygon: (d) => ring3(d.ring, this._z(d) + 0.08),
        getLineColor: [0, 0, 0, 210], updateTriggers: { getLineWidth: [sv, far], getPolygon: sv } }));
      if (pools.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:pools", data: pools, pickable: true, stroked: true, filled: true, lineWidthUnits: "pixels",
        getLineWidth: (d) => sp.has(d) ? 2.5 : far ? 2.6 : 1, parameters: ON_TOP, getPolygon: (d) => ring3(d.ring, this._z(d) + 0.1),
        getFillColor: (d) => [...lit(col(d.latest_state), shim(d)), sp.has(d) ? 245 : 200], getLineColor: (d) => [...rim(col(d.latest_state), shim(d)), 255],
        updateTriggers: { getLineWidth: [sv, far], getFillColor: [sv, t], getLineColor: t, getPolygon: sv } }));
      // LiDAR holes: filled with the water's photo colour; white rim = photo confirms water, orange = photo says not water
      if (holes.length) L.push(new D.PolygonLayer({ ...mm, id: "pa:holes", data: holes, pickable: true, stroked: true, filled: true, lineWidthUnits: "pixels",
        getLineWidth: (d) => sp.has(d) ? 2.5 : far ? 2.6 : 1.2, parameters: ON_TOP, getPolygon: (d) => ring3(d.ring, this._z(d) + 0.1),
        getFillColor: (d) => holeState(d) ? [...lit(col(holeState(d)), shim(d)), sp.has(d) ? 240 : 190] : [255, 150, 90, 40],
        getLineColor: (d) => holeState(d) ? [...rim(col(holeState(d)), shim(d)), 255] : [255, 150, 90, 220], updateTriggers: { getPolygon: sv, getFillColor: [sv, t], getLineColor: t, getLineWidth: [sv, far] } }));
      return L;
    }
    pick(info) {
      const id = info && info.layer && info.layer.id, o = info && info.object;
      if (/^pa:(pools|holes)$/.test(id) && o) { const h = this._houseOf(o); if (!h && this.selPools.has(o) && this.selPools.size === 1) this._select(null, null); else this._select(h, o); return true; }
      if (id === "pa:bld" && o) { this._select(o, null); return true; }
      if (this.selHouse || this.sel) this._select(null, null);
      return false;
    }
    tooltip(info) {
      const o = info && info.object, id = info && info.layer && info.layer.id; if (!o || !/^pa:/.test(id)) return null;
      if (id === "pa:bld") {
        const ps = this._poolsOf(o), st = ps.map((p) => pState(p)).filter(Boolean);
        return { html: `<div class="pt-tip"><b>Building</b> · footprint ${esc(o.area_m2)} m²<br>` +
          (ps.length ? `${ps.length} pool${ps.length > 1 ? "s" : ""} found here: ` + st.map((x) => `<span style="color:rgb(${col(x)})">■</span> ${esc((ST[x] || [0, x])[1])}`).join(", ") : "no pool found here yet") +
          `<br><span class="s">Overture footprint (ODbL) · pools linked to the nearest building (no open lot lines) · click to light it and its pools</span></div>` };
      }
      const hs = this._houseOf(o), hl = hs ? `<br>belongs to the nearest building, ${esc(o._houseM)} m away · click to light it` : "";
      if (o.akind === "holes") {
        const ph = o.photo || {}, li = o.lidar || {};
        const hst = holeState(o);
        return { html: `<div class="pt-tip"><b>${hst ? "Pool · " + esc((ST[hst] || [0, hst])[1]) : "LiDAR water hole"}</b> · ${esc(Math.round(o.area_m2))} m²` +
          (hst ? `<br><span style="color:rgb(${col(hst)})">${esc(CARE[hst] || "")}</span> (photo ${esc(ph.year || "")})` : "") + `<br>${esc(o.label || ph.verdict || "no photo check")}` +
          `${ph.verdict ? "<br>photo " + esc(ph.date || ph.year) + ": " + esc(String(ph.verdict).replace(/_/g, " ")) + (ph.state ? " (" + esc(String(ph.state).replace(/_/g, " ")) + ")" : "") : ""}` +
          hl + `<br>${esc(li.empty_cells ?? "?")} empty 0.5 m cells · ${esc(li.density_ppm2_around ?? "?")} pts/m² around` +
          `<br><span class="s">${esc(li.project || "USGS 3DEP")} ${esc(li.year || "")} · laser returns missing over water · photo ${esc(ph.year || "")}</span></div>` };
      }
      const hist = Object.entries(o.states || {}).sort().map(([k, v]) => `<span style="color:rgb(${col(v)})">■</span>${esc(k.replace(/\D/g, ""))}`).join(" ");
      const own = o.parcel ? `${esc(o.parcel.address || "")}<br>inside its official lot (APN ${esc(o.parcel.apn)})` :
        o.owner ? `nearest building ${esc(o.owner.nearest_m)} m (next ${esc(o.owner.runner_up_m)} m) · no open lot lines · ${esc(o.owner.confidence)} confidence` :
        o.kind === "community" ? "community / shared pool" : "not inside any lot";
      return { html: `<div class="pt-tip"><b>Pool · ${esc((ST[o.latest_state] || [0, o.latest_state])[1])}</b> · ${esc(o.area_m2)} m²<br><span style="color:rgb(${col(o.latest_state)})">${esc(CARE[o.latest_state] || "")}</span> (latest photo)<br>${own}` +
        `<br>${hist}${o.green_years && o.green_years.length ? `<br>green in ${esc(o.green_years.join(", "))}` : ""}` +
        (o.check ? `<br><b style="color:#ffb347">needs a look:</b> ${esc(o.check)}` : "") + (o.parcel ? "" : hl) +
        `<br><span class="s">NAIP aerial photos (public domain) · state = photo day only · click to light its house${o.parcel ? " and lot" : ""}</span></div>` };
    }

    bindPanel(el) {
      this.panel = el; el.classList.add("ptp");
      el.addEventListener("change", (e) => {
        const ds = (e.target && e.target.dataset) || {};
        if (ds.pafocus != null) { this.focus = e.target.value; this.onFocus(this.focus); this._render(); this.onUpdate(); return; }
        if (ds.pashow) { e.target.checked ? this.show.add(ds.pashow) : this.show.delete(ds.pashow); this._dirty(); this._render(); this.onUpdate(); return; }
        const k = ds.pa; if (!k) return;
        if (e.target.checked) { this.on.add(k); this._index(k); } else { this.on.delete(k); if (this.sel && this.sel.area === k) this.sel = null; this.selHouse = null; this.selPools = new Set(); }
        this._dirty(); this._render(); this._schedule(0); this.onUpdate(); });
      this._render();
    }
    _render() {
      const el = this.panel; if (!el) return;
      if (!el.querySelector(".pt-st")) el.innerHTML = `<div class="tg">${AREAS.map((a) => `<label><input type="checkbox" data-pa="${a.slug}" ${this.on.has(a.slug) ? "checked" : ""}> ${esc(a.name)}</label>`).join("")}</div>` +
        `<div class="pa-flt" style="margin:6px 0 0 14px"></div><div class="leg"></div><div class="note"></div><div class="pt-st"></div>`;
      const fl = el.querySelector(".pa-flt"); fl.hidden = !this.on.size;
      if (this.on.size) {
        const cnt = {}; for (const p of this._list()) { const g = groupOf(p); cnt[g] = (cnt[g] || 0) + 1; }
        const sw = { clean: col("clean_blue"), green: col("green_algae"), dark: col("dark"), covered: col("covered_or_empty"), nowater: [255, 150, 90] };
        fl.innerHTML = `<label class="ctl"><span class="lbl">Pool contrast</span><select data-pafocus>` + [["", "Normal LiDAR colours"], ["focus", "Dim grey"], ["amber", "Amber (opposite of blue)"], ["violet", "Deep violet"], ["paper", "Light paper"], ["ink", "Night (near black)"]].map(([k, t]) =>
          `<option value="${k}" ${this.focus === k ? "selected" : ""}>${esc(t)}</option>`).join("") + `</select></label><div class="sub small">recolours the LiDAR only (every point kept); pools keep their water colour</div>` +
          `<div class="lbl" style="margin-top:4px">Show pools</div><div class="tg">` + SHOW.map(([k, t]) =>
          `<label><input type="checkbox" data-pashow="${k}" ${this.show.has(k) ? "checked" : ""}> <span style="color:rgb(${sw[k]})">■</span> ${esc(t)} <span class="sub small">${cnt[k] || 0}</span></label>`).join("") +
          `</div><div class="sub small">counts = pools loaded around the view</div>`;
      }
      for (const i of el.querySelectorAll("input[data-pa]")) i.checked = this.on.has(i.dataset.pa);
      el.querySelector(".leg").innerHTML = this.on.size ? `<div class="sub small">` + Object.entries(ST).slice(0, 6).map(([k, [c, t]]) => `<span style="color:rgb(${c})">■</span> ${esc(t)}${k === "green_algae" ? " = likely needs cleaning" : ""}`).join(" · ") + " · pools shimmer in their water colour from the latest photo" +
        (this.on.has("scottsdale_lidar") ? ` · <span style="color:#fff">□</span> LiDAR hole, photo confirms water · <span style="color:rgb(255,150,90)">□</span> hole, photo says not water` : "") + `</div>` : "";
      el.querySelector(".note").textContent = this.on.size ? "Zoom in (street level) to load pools. Click any pool or house to light the house and its pools" + ([...this.on].some((k) => BLD[k]) ? " (every building in the Phoenix metro)." : " and official lot.") : "Every pool found across a whole city. Any Phoenix-metro building is clickable at street zoom.";
      const v = this.view, lines = [...this.on].map((k) => { const I = this.idx[k], a = AREAS.find((x) => x.slug === k);
        if (!I || I.state === "loading") return `${a.name}: loading`; if (I.state === "err") return `${a.name}: ${I.err}`;
        const tot = I.j.totals || {}, done = (I.j.tiles || []).filter((t) => !t.status || /done|ok|final/.test(t.status)).length;
        const n = tot.pools ?? tot.holes ?? tot.all ?? "?"; return `${a.name}: ${n} ${a.kind === "holes" ? "holes" : "pools"} · ${done}/${tot.tiles_total || (I.j.tiles || []).length} tiles built`; });
      if (this.on.size && (!v || v.zoom < MINZ)) lines.push(`zoom in to street level (≥ ${MINZ}) to see them`);
      if (this.selHouse) lines.push(`selected: building ${this.selHouse.area_m2} m² with ${this.selPools.size} pool${this.selPools.size === 1 ? "" : "s"}`);
      else if (this.sel) lines.push(`selected: ${this.sel.parcel ? this.sel.parcel.address : "pool " + this.sel.id}`);
      const st = el.querySelector(".pt-st"); st.textContent = lines.join("\n"); st.style.whiteSpace = "pre-line";
    }
  }
  window.PoolsArea = PoolsArea;
})();

/* main.js — World Maintenance: ONE deck.gl canvas from the dot globe down to a walk at eye height.
   Zoom path (zoom = Web-Mercator-equivalent zoom everywhere: URL, thresholds, fly-to):
     < 5.6   GlobeView: ETOPO dot globe, real stars, sun/night lights, atmosphere glow (ported from the old usa/globe.html)
     ≥ 5.6   MapView (pitch allowed): county choropleth (County Radar) + US dots fading out by ~11
     ≥ 7     census tracts of the states in view (lazy per state, dropped when out of view)
     ≥ 11    USGS 3DEP LiDAR via app/ept.js (EptManager) · ≥ 12 metro building tiles · ≥ 14 built blocks via app/block.js (BlockManager)
     walk    first-person camera (MapView, eye 1.7 m above block / LiDAR / ETOPO ground)
   Back below 5.3 the map hands over to the globe again at the same centre and scale.
   Depends on globals from overview.js (county metrics, card, search) and catalog.js (window.CATALOG). */
const U = "../usa/";
// test aid: ?raf=timer drives animation with timers (for automated tests in a hidden/background tab where rAF is paused)
if (new URLSearchParams(location.search).get("raf") === "timer") { window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 16); window.cancelAnimationFrame = id => clearTimeout(id); }
const D2R = Math.PI / 180;
const clamp01 = v => v < 0 ? 0 : v > 1 ? 1 : v;
const smooth = (a, b, x) => { const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };
const norm180 = l => ((l + 540) % 360) - 180;
const vec = (lon, lat) => { const l = lon * D2R, p = lat * D2R, c = Math.cos(p); return [Math.sin(l) * c, -Math.cos(l) * c, Math.sin(p)]; };   // deck globe common axes
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const QS = new URLSearchParams(location.search);
const qn = k => { const v = parseFloat(QS.get(k)); return isFinite(v) ? v : null; };
const safe = (fn, d) => { try { const v = fn(); return v === undefined ? d : v; } catch (e) { console.warn(e); return d; } };

// ---------- thresholds (map-equivalent zoom)
const Z_TO_MAP = 5.6, Z_TO_GLOBE = 5.3, Z_TRACTS = 7, Z_EPT = 11, Z_METRO = 12, Z_BLOCKS = 14, Z_WALK = 13;
const maxPitchFor = z => Math.max(0, Math.min(80, (z - Z_TO_GLOBE) * 45));

// ---------- state
let CM = { kind: "c" };   // colour mode: "e" elevation · "c" county metric · "t" tract metric
let TRM = TR_METRICS[0], trBreaks = [];
let TRACT = new Map(), TR_LOADED = new Set(), TR_LOADING = new Set(), TR_INDEX = null, TR_FEAT = new Map(), pickedTract = null, TRACTS_ON = true;
let META = null, WD = null, UD = null, EPTI = null, WBIN = null, UBIN = null, EBIN = null, STATE_PATHS = [], STARS = [], GRATICULE = [];
let CO_BBOX = new Map(), STATE_BBOX = new Map(), CO_EL = null, CO_GRID = null, ELEV_GRID = null;
let LIFT = { e: 30, key: "" };
let RELIEF = true, EXAG = 30, SUN_ON = true, EPT_ON = true, STATES_ON = true, TEX_ON = false, SPIN = false, PINS_ON = true, STARS_ON = true, BLDG_ON = true, CLIP_ON = false;
let TIME = new Date(), LIVE = true, SUN = [0, 0, 1], SUN_LL = [0, 0], GMST = 0;
let DAY = null, NIGHT = null, EARTH_IMG = null, texBusy = false, texAgain = false;
let VS = { longitude: -98.35, latitude: 39.5, zoom: 1.5, pitch: 0, bearing: 0 };
let VIEW = "globe", WALK = null, FLY = null, LIFT_Z = 0, LIFT_T = 0, SWITCH_AT = 0, dragging = false;
let layersDirty = true, skyDirty = true, recolorDirty = false, layerKey = "";
let PA = null, BZ = null, SKY = null, EO = null, PT = null, MT = null, MT_SLUG = null, earthDirty = 0;   // earthDirty: frames left to force a redraw after an overlay tile lands (deck does not repaint on its own here)   // Earth-observation overlays (earthlayers.js) and measured points (points.js)
let EPT = null, BL = null, HOUSE = null, MGR_STUB = { ept: true, block: true };
let SECTION = "", catalogOpen = false;
const GLOBE_VIEW = new deck._GlobeView({ id: "globe", resolution: 10 });
// Globe zoom: our own wheel handler zooms smoothly about the globe centre (deck's anchors on the pointer, which makes the
// globe jump and spin when the pointer is in space beside it), and the globe never shrinks below ~half the canvas.
const GLOBE_CTRL = { doubleClickZoom: true, inertia: false, scrollZoom: false, dragPan: false, dragRotate: false };   // drag and wheel are ours (wireGlobeInput)
let GDRAG = null, GVEL = null;   // globe drag state and release glide (deg/ms)
let GZ_TARGET = null;
function globeMinZoom() { const el = document.getElementById("deck"), m = Math.max(200, Math.min(el.clientWidth || 800, el.clientHeight || 600));
  const far = SKY && SKY.on && (SKY.on.has("gnss") || SKY.on.has("sats")) ? 4 : 0;   // satellites up to ~36,000 km need room around the Earth
  return Math.max(-1 - far, Math.log2(0.55 * m * Math.PI / 256) - far); }
const MAP_VIEW = new deck.MapView({ id: "map", repeat: false, nearZMultiplier: 0.02, farZMultiplier: 10 });
// walk = the same MapView driven as a first-person camera (eye fixed at ground + 1.7 m, target ahead): every layer keeps
// the exact projection it has on the map (FirstPersonView mis-placed the METER_OFFSETS point clouds in testing)
const WALK_ZOOM = 21.8;
const WALK_VIEW = new deck.MapView({ id: "map", repeat: false, nearZMultiplier: 0.01, farZMultiplier: 120 });   // long far plane: the street to the horizon
let deckgl2 = null;

// ---------- globe <-> map zoom: same on-screen scale at the switch
const ZOFF = new Map();
function zoff(lat) {
  const k = Math.round(lat * 2); let v = ZOFF.get(k);
  if (v == null) {
    const la = Math.max(-85, Math.min(85, k / 2)), W = 800, H = 600, d = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    const g = new deck._GlobeViewport({ width: W, height: H, longitude: 0, latitude: la, zoom: 4 }), m = new deck.WebMercatorViewport({ width: W, height: H, longitude: 0, latitude: la, zoom: 4 });
    v = Math.log2(d(g.project([0, la - 0.05, 0]), g.project([0, la + 0.05, 0])) / d(m.project([0, la - 0.05]), m.project([0, la + 0.05])));
    if (!isFinite(v)) v = 0; ZOFF.set(k, v);
  }
  return v;
}

// ---------- managers (other lanes): ept.js and block.js, stubbed when missing
function stubEpt() { return { update() {}, layers: () => [], setColorMode() {}, setClip() {}, groundAt: () => null, stats: () => null }; }
function stubBlocks() { return { update() {}, layers: () => [], pick: () => false, panelHtml: () => "", list: () => [] }; }
async function importClass(file, name) {
  try { const h = await fetch(file, { method: "HEAD", cache: "no-store" }); if (!h.ok) return null; } catch { return null; }
  try { const m = await import(file); return m[name] || (m.default && (m.default[name] || m.default)) || window[name] || null; }
  catch (e) { console.warn(`${file}: ${e.message}`); return window[name] || null; }
}
async function loadManagers() {
  const [E, B] = await Promise.all([importClass("./ept.js?v=20261128a", "EptManager"), importClass("./block.js", "BlockManager")]);
  // zOffset 0: true absolute elevations (NAVD88 m); the camera is lifted to the ground instead (camLift), so blocks, buildings and LiDAR share one frame
  try { if (typeof E === "function") { EPT = new E({ onUpdate: onMgrUpdate, zOffset: 0 }); MGR_STUB.ept = false; } } catch (e) { console.warn("EptManager", e); }
  try { if (typeof B === "function") { BL = new B({ onUpdate: onMgrUpdate, onSelectHouse, groundAt }); MGR_STUB.block = false; } } catch (e) { console.warn("BlockManager", e); }
  EPT = EPT || stubEpt(); BL = BL || stubBlocks();
  initEarth(); initMaint();
  const modes = safe(() => EPT.colorModes, null) || [{ id: "elevation", label: "Elevation" }, { id: "classification", label: "Classification" }, { id: "intensity", label: "Intensity" }, { id: "rgb", label: "RGB (where flown)" }];
  for (const [id, label] of [["focus", "Dim grey"], ["amber", "Amber"], ["violet", "Deep violet"], ["paper", "Light paper"], ["ink", "Night (near black)"]]) if (!modes.some((m) => (m.id || m) === id)) modes.push({ id, label: label + " (overlays stand out)" });
  $("eptColor").innerHTML = (Array.isArray(modes) ? modes : Object.keys(modes)).map(m => `<option value="${esc(m.id || m)}">${esc(m.label || m)}</option>`).join("");
  $("eptColor").onchange = e => { safe(() => EPT.setColorMode(e.target.value)); layersDirty = true; };
  scheduleSettle();
}
// ---------- Earth data: live satellite/agency overlays and measured points (both optional modules)
function earthState(key) { try { const v = QS.get(key); return v ? JSON.parse(v) : null; } catch { return null; } }
function initEarth() {
  const onUpdate = () => { layersDirty = true; earthDirty = 2; updateCredits(); scheduleUrl(); };
  try { if (window.EarthLayers) { EO = new window.EarthLayers({ onUpdate }); const st = earthState("eo"); if (st) safe(() => EO.setState(st)); safe(() => EO.bindPanel($("eoPanel"))); } } catch (e) { console.warn("EarthLayers", e); EO = null; }
  try { if (window.EarthPoints) { PT = new window.EarthPoints({ onUpdate, gnssUrl: "data/gnss.json", icesat2Url: "data/icesat2.json", insarUrl: "data/insar.json", emitUrl: "data/emit.json", gediUrl: "data/gedi.json" }); const st = earthState("pt"); if (st) safe(() => PT.setState(st)); safe(() => PT.bindPanel($("ptPanel"))); } } catch (e) { console.warn("EarthPoints", e); PT = null; }
  if (!EO) $("eoPanel").innerHTML = `<div class="note">Overlays module not loaded.</div>`;
  try { if (window.SkyTraffic) { SKY = new window.SkyTraffic({ onUpdate, aircraftUrl: "data/sky/aircraft.json" }); const st = earthState("sky"); if (st) safe(() => SKY.setState(st)); safe(() => SKY.bindPanel($("skyPanel"))); } } catch (e) { console.warn("SkyTraffic", e); SKY = null; }
  try { if (window.PoolsArea) { PA = new window.PoolsArea({ onUpdate, onFrame: () => { layersDirty = true; }, groundAt, groundExact,
    onFocus: (look) => { const sel = $("eptColor"), looks = ["focus", "amber", "violet", "paper", "ink"]; if (look) { if (!looks.includes(sel.value)) PA._prevMode = sel.value; sel.value = look; } else sel.value = PA._prevMode || "elevation";
      safe(() => EPT.setColorMode(sel.value)); layersDirty = true; } }); const st = earthState("pa"); if (st) safe(() => PA.setState(st)); safe(() => PA.bindPanel($("paPanel"))); } } catch (e) { console.warn("PoolsArea", e); PA = null; }
  try { if (window.Partners) { BZ = new window.Partners({ onUpdate }); const st = earthState("bz"); if (st) safe(() => BZ.setState(st)); safe(() => BZ.bindPanel($("bzPanel"))); } } catch (e) { console.warn("Partners", e); BZ = null; }
  if (!PT) $("ptPanel").innerHTML = `<div class="note">Points module not loaded.</div>`;
  if ((earthState("eo") || earthState("pt")) && $("secEarth")) $("secEarth").open = true;
}
// ---------- maintenance view (maint.js, with poolsvc.js for pools): per-house trade signals on built blocks
function initMaint() {
  if (!window.Maintenance) return;
  try {
    MT = new window.Maintenance({ baseUrl: "../usa/blocks/", onUpdate: e => {
      layersDirty = true;
      if (e && (e.type === "focusHouse" || e.type === "selectHouse" || e.type === "focusPool") && e.slug && (e.houseId ?? e.house_id) != null) {
        const id = e.houseId ?? e.house_id; safe(() => BL.select(e.slug, id)); const t = safe(() => BL.houseFlyTarget(e.slug, id), null); if (t && typeof flyTo === "function") flyTo(t);
      }
      if (e && e.type !== "highlight") { if (HOUSE) showHouse(HOUSE.id, false); renderMaint(); }
    } });
    $("mntOn").onchange = ev => { safe(() => MT.setEnabled(ev.target.checked)); layersDirty = true; renderMaint(); };
  } catch (err) { console.warn("Maintenance", err); MT = null; }
}
function mtBlock(slug) { const B = BL && BL.blocks && BL.blocks.get(slug); return B && B.state === "ready" ? B : null; }
function renderMaint() {
  const el = $("mntPanel"); if (!el) return;
  const slug = BL && BL.focus && mtBlock(BL.focus) ? BL.focus : null;
  if (!MT || !slug) { el.innerHTML = `<div class="note">Zoom into a built block (the pins) to see its maintenance signals.</div>`; MT_SLUG = null; return; }
  const h = safe(() => MT.blockHtml(slug), ""); if (h === el._h && MT_SLUG === slug) return;
  el._h = h; MT_SLUG = slug; el.innerHTML = h || `<div class="note">Loading maintenance signals…</div>`; safe(() => MT.bind(el));
  if ($("secMaint") && !$("secMaint")._auto) { $("secMaint")._auto = true; $("secMaint").open = true; }
}
function mtLayers() {
  if (!MT || !BL || !BL.blocks) return [];
  const out = [];
  for (const [slug, B] of BL.blocks) { if (B.state !== "ready") continue; const v = safe(() => BL.verticalRef(slug), null); if (!v) continue;
    const ls = safe(() => MT.layers({ slug, coordinateOrigin: [B.lon0, B.lat0, v.zOffset || 0], zBase: v.zBase, houses: B.H, zOf: (x, y) => B.groundLow(x, y), lotOf: (h) => { const pr = B.parcelOf && B.parcelOf(h); return pr && pr.p && pr.p.ring || null; } }), []);
    for (const l of ls || []) if (l) out.push(l); }
  return out;
}
function earthActive(m) { const s = m && safe(() => m.getState(), null); return !!(s && Array.isArray(s.on) && s.on.length); }
let PENDING_HOUSE = null;
function onMgrUpdate(evt) {
  layersDirty = true;
  if (MT && evt && evt.type === "load" && evt.slug) { const B = mtBlock(evt.slug); if (B) { safe(() => MT.setHouses(evt.slug, B.RAW || B.H)); Promise.resolve(safe(() => MT.load(evt.slug, B.M))).then(() => { layersDirty = true; renderMaint(); if (HOUSE) showHouse(HOUSE.id, false); }); } }
  if (MT && evt && evt.type === "unload" && evt.slug) { safe(() => MT.unload(evt.slug)); renderMaint(); }
  if (MT && evt && (evt.type === "select" || evt.type === "index")) renderMaint();
  if (evt && evt.type === "frame") return;   // selection glow tick: layers only
  statsDirty = true;
  { const now = performance.now(); if (now - (onMgrUpdate.s || 0) > 400) { onMgrUpdate.s = now; renderStats(); } }
  if (VIEW === "map") { const now = performance.now(); if (now - (onMgrUpdate.t || 0) > 250) { onMgrUpdate.t = now; LIFT_T = camLift(); } }   // LiDAR ground arrived: re-seat the camera
  if (PENDING_HOUSE && BL && BL.select) { const r = safe(() => BL.select(PENDING_HOUSE.slug, PENDING_HOUSE.id), null); if (r) PENDING_HOUSE = null; }
  if (HOUSE) showHouse(HOUSE.id, false);
}
function onSelectHouse(sel) {
  if (MT) safe(() => MT.highlight(sel && sel.slug, sel ? (sel.houseId ?? null) : null, "select"));
  if (!sel) { if (HOUSE) { HOUSE = null; renderDossier(); scheduleUrl(); } return; }
  HOUSE = { id: String(sel.houseId), slug: sel.slug }; showHouse(HOUSE.id, true); scheduleUrl(sel.slug);
}
let statsDirty = true;

// ---------- ground: LiDAR first (ept.groundAt), else the ETOPO US dots (0.1° mean)
function etopoAt(lon, lat) {
  if (!UD) return 0;
  if (!ELEV_GRID) { ELEV_GRID = new Map(); const acc = new Map();
    for (let i = 0; i < UD.n; i++) { const k = Math.floor(UD.lng[i] * 10) + ":" + Math.floor(UD.lt[i] * 10); const a = acc.get(k); if (a) { a[0] += UD.elev[i]; a[1]++; } else acc.set(k, [UD.elev[i], 1]); }
    for (const [k, a] of acc) ELEV_GRID.set(k, a[0] / a[1]); }
  const x = Math.floor(lon * 10), y = Math.floor(lat * 10);
  for (const r of [0, 1, 2]) { let s = 0, n = 0; for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) { const v = ELEV_GRID.get((x + dx) + ":" + (y + dy)); if (v != null) { s += v; n++; } } if (n) return Math.max(0, s / n); }
  return 0;
}
function groundAt(lon, lat) {   // built block ground (terrain mesh) > streamed USGS LiDAR ground > ETOPO
  let g = BL && BL.groundAt ? safe(() => BL.groundAt(lon, lat), null) : null;
  if (g == null || !isFinite(g)) g = EPT ? safe(() => EPT.groundAt(lon, lat), null) : null;
  return g != null && isFinite(g) ? +g : etopoAt(lon, lat);
}
function groundExact(lon, lat) {   // measured ground only (built block, then streamed LiDAR); null until it has loaded
  let g = BL && BL.groundAt ? safe(() => BL.groundAt(lon, lat), null) : null;
  if (g == null || !isFinite(g)) g = EPT ? safe(() => EPT.groundAt(lon, lat), null) : null;
  return g != null && isFinite(g) ? +g : null;
}
// county under a point: nearest US dot in a 0.1° grid
function countyAt(lon, lat) {
  if (!UD) return null;
  if (!CO_GRID) { CO_GRID = new Map(); for (let i = 0; i < UD.n; i++) { const k = Math.floor(UD.lng[i] * 10) + ":" + Math.floor(UD.lt[i] * 10); let a = CO_GRID.get(k); if (!a) CO_GRID.set(k, a = []); a.push(i); } }
  const x = Math.floor(lon * 10), y = Math.floor(lat * 10); let best = -1, bd = Infinity;
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) { const a = CO_GRID.get((x + dx) + ":" + (y + dy)); if (a) for (const i of a) { const d = (UD.lng[i] - lon) ** 2 + (UD.lt[i] - lat) ** 2; if (d < bd) { bd = d; best = i; } } }
  if (best < 0 || UD.county[best] === 0xFFFF) return null;
  return META.counties[UD.county[best]];
}

// ---------- dots (ported from the dot globe): back-hemisphere dots fade
class DotLayer extends deck.ScatterplotLayer {
  getShaders() {
    const s = super.getShaders();
    s.inject = Object.assign({}, s.inject, { "vs:#main-end": `
      if (project.projectionMode == PROJECTION_MODE_GLOBE) {
        vec3 dn = normalize(geometry.position.xyz);
        vec3 dv = normalize(project.cameraPosition - geometry.position.xyz);
        vFillColor.a *= mix(0.04, 1.0, smoothstep(-0.03, 0.16, dot(dn, dv)));
      }` });
    return s;
  }
}
DotLayer.layerName = "DotLayer";
function decodeDots(buf, info) {
  const TA = { int16: Int16Array, uint16: Uint16Array, uint32: Uint32Array, uint8: Uint8Array };
  const o = { n: info.count };
  for (const [k, L] of Object.entries(info.layout)) o[k] = new TA[L.type](buf, L.offset, L.length);
  const n = o.n, ls = META.lon_scale, as = META.lat_scale;
  o.lng = new Float32Array(n); o.lt = new Float32Array(n); o.nrm = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const lo = o.lon[i] * ls, la = o.lat[i] * as, l = lo * D2R, p = la * D2R, cp = Math.cos(p);
    o.lng[i] = lo; o.lt[i] = la; o.nrm[i * 3] = Math.sin(l) * cp; o.nrm[i * 3 + 1] = -Math.cos(l) * cp; o.nrm[i * 3 + 2] = Math.sin(p);
  }
  return o;
}
// relief: exaggerated at planet scale, true scale (absolute metres) from the map hand-over on
function liftState() {
  let e = RELIEF ? EXAG : 1;
  if (VIEW !== "globe") e = 1; else e = Math.exp(Math.log(e) * (1 - smooth(2.5, Z_TO_MAP, VS.zoom)));
  e = Math.pow(2, Math.round(Math.log2(e) * 4) / 4);
  return { e, key: e.toFixed(3) };
}
function positions(D, e, us) {
  const p = new Float32Array(D.n * 3);
  for (let i = 0; i < D.n; i++) { const el = D.elev[i]; p[i * 3] = D.lng[i]; p[i * 3 + 1] = D.lt[i]; p[i * 3 + 2] = (us || el > 0) ? el * e : el * e * 0.12; }
  return p;
}
function checkLift(force) {
  if (!WD) return;
  const L = liftState(); if (!force && L.key === LIFT.key) return;
  LIFT = L;
  WD.pos = positions(WD, L.e, false); UD.pos = positions(UD, L.e, true);
  const ep = new Float32Array(EPTI.length * 3); EPTI.forEach((j, i) => { ep[i * 3] = UD.pos[j * 3]; ep[i * 3 + 1] = UD.pos[j * 3 + 1]; ep[i * 3 + 2] = UD.pos[j * 3 + 2]; });
  EBIN = { length: EPTI.length, attributes: { getPosition: { value: ep, size: 3 } } };
  OUTLINE_CACHE.clear(); rebin(); layersDirty = true;
}
const rebin = () => {
  if (WD.col) WBIN = { length: WD.n, attributes: { getPosition: { value: WD.pos, size: 3 }, getFillColor: { value: WD.col, size: 4, normalized: true } } };
  if (UD.col) UBIN = { length: UD.n, attributes: { getPosition: { value: UD.pos, size: 3 }, getFillColor: { value: UD.col, size: 4, normalized: true } } };
};

// ---------- colour
let LUT = null, LUT_TH = null, WARM = [255, 184, 77];
function lut() { const th = skin(); if (LUT_TH !== th) { LUT = new Uint8Array(256 * 3); for (let i = 0; i < 256; i++) { const c = T.ramp(i / 255); LUT[i * 3] = c[0]; LUT[i * 3 + 1] = c[1]; LUT[i * 3 + 2] = c[2]; } LUT_TH = th; } return LUT; }
function shadeInto(col, o, r, g, b, a, D, i, keep) {
  if (SUN_ON) {
    const f = D.nrm[i * 3] * SUN[0] + D.nrm[i * 3 + 1] * SUN[1] + D.nrm[i * 3 + 2] * SUN[2];
    const day = smooth(-0.09, 0.09, f), k = keep + (1 - keep) * day, L = D.light[i] / 255, glow = (1 - day) * L * L * 1.6;
    r = r * k + WARM[0] * glow; g = g * k + WARM[1] * glow; b = b * k + WARM[2] * glow; a = Math.min(255, a + glow * 120);
  }
  col[o] = r > 255 ? 255 : r; col[o + 1] = g > 255 ? 255 : g; col[o + 2] = b > 255 ? 255 : b; col[o + 3] = a;
}
function colorWorld() {
  const n = WD.n, col = new Uint8Array(n * 4), R = lut();
  for (let i = 0; i < n; i++) {
    const el = WD.elev[i]; let t, a;
    if (el > 0) { t = 0.34 + 0.62 * Math.pow(Math.min(1, el / 5200), 0.55); a = 230; } else { t = 0.04 + 0.22 * (1 - Math.min(1, -el / 7000)); a = 105; }
    const j = (t * 255 | 0) * 3; shadeInto(col, i * 4, R[j], R[j + 1], R[j + 2], a, WD, i, 0.3);
  }
  WD.col = col;
}
const BANDS = () => Array.from({ length: NBANDS }, (_, i) => bandColor(i));
function trBandOf(v) { if (v == null) return -1; let i = 0; while (i < trBreaks.length && v >= trBreaks[i]) i++; return i; }
function computeTrBreaks() {
  if (TRM.flag) { trBreaks = []; return; }
  const vals = []; for (const m of TRACT.values()) { const v = m[TRM.k]; if (v != null) vals.push(v); }
  vals.sort((a, b) => a - b); trBreaks = [];
  if (vals.length) for (let i = 1; i < NBANDS; i++) trBreaks.push(vals[Math.floor(vals.length * i / NBANDS)]);
  TRM._min = vals[0]; TRM._max = vals[vals.length - 1]; TRM._n = vals.length;
}
const trBand = v => v == null ? -1 : TRM.flag ? (v >= 2 ? NBANDS - 1 : v >= 1 ? NBANDS - 2 : 1) : trBandOf(v);
const trKey = i => UD.tract[i] === 0xFFFFFFFF ? -1 : UD.tract_st[i] * 1e9 + UD.tract[i];
function colorUSA() {
  const n = UD.n, col = new Uint8Array(n * 4), R = lut(), other = T.rgb("other"), sel = T.rgb("select"), bands = BANDS();
  const selIdx = selected ? META.counties.indexOf(selected) : -1;
  const cBand = new Int8Array(META.counties.length).fill(-1);
  if (CM.kind === "c") META.counties.forEach((f, ci) => { const r = COUNTY.get(f); cBand[ci] = bandOf(r && r[metric.k]); });
  for (let i = 0; i < n; i++) {
    let r, g, b, a = 245; const ci = UD.county[i];
    if (CM.kind === "e") { const t = 0.4 + 0.58 * Math.pow(Math.min(1, Math.max(0, UD.elev[i]) / 4300), 0.55), j = (t * 255 | 0) * 3; r = R[j]; g = R[j + 1]; b = R[j + 2]; }
    else {
      let bi = -1;
      if (CM.kind === "c") bi = cBand[ci];
      else { const k = trKey(i), m = k >= 0 ? TRACT.get(k) : null; bi = trBand(m ? m[TRM.k] : null); }
      if (bi < 0) { r = other[0]; g = other[1]; b = other[2]; a = 60; } else { const c = bands[bi]; r = c[0]; g = c[1]; b = c[2]; }
    }
    if (ci === selIdx) { r = sel[0]; g = sel[1]; b = sel[2]; a = 255; }
    shadeInto(col, i * 4, r, g, b, a, UD, i, 0.5);
  }
  UD.col = col;
}
function recolor() { if (!WD) return; WARM = T.rgb("warn").slice(0, 3); colorWorld(); colorUSA(); rebin(); layersDirty = true; }

// ---------- sun, sidereal time
function astro(d) {
  const n = d.getTime() / 86400000 + 2440587.5 - 2451545;
  GMST = ((280.46061837 + 360.98564736629 * n) % 360 + 360) % 360;
  const L = 280.46 + 0.9856474 * n, g = (357.528 + 0.9856003 * n) * D2R, lam = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D2R, eps = (23.439 - 4e-7 * n) * D2R;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam)) / D2R, dec = Math.asin(Math.sin(eps) * Math.sin(lam)) / D2R;
  SUN_LL = [norm180(ra - GMST), dec]; SUN = vec(SUN_LL[0], SUN_LL[1]);
}
function setTime(d, fromUser) {
  TIME = d; astro(d);
  if (fromUser !== "slider") { $("day").value = d.toISOString().slice(0, 10); $("tod").value = d.getUTCHours() * 60 + d.getUTCMinutes(); }
  const loc = d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  $("tlabel").textContent = `${d.toISOString().slice(0, 16).replace("T", " ")} UTC · ${loc} local · sun over ${Math.abs(SUN_LL[1]).toFixed(1)}°${SUN_LL[1] >= 0 ? "N" : "S"} ${Math.abs(SUN_LL[0]).toFixed(1)}°${SUN_LL[0] >= 0 ? "E" : "W"}${LIVE ? " · live" : ""}`;
  recolorDirty = true; skyDirty = true; if (TEX_ON) compose();
}
// ---------- NASA texture (optional skin)
async function loadTex() {
  const img = src => new Promise((ok, no) => { const im = new Image(); im.onload = () => ok(im); im.onerror = no; im.src = src; });
  const [d, nt] = await Promise.all([img(U + "data/earth/blue_marble_2004-07_5400.jpg"), img(U + "data/earth/black_marble_2016_3600.jpg")]);
  const W = 2048, H = 1024, cv = document.createElement("canvas"); cv.width = W; cv.height = H; const cx = cv.getContext("2d", { willReadFrequently: true });
  cx.drawImage(d, 0, 0, W, H); DAY = cx.getImageData(0, 0, W, H); cx.drawImage(nt, 0, 0, W, H); NIGHT = cx.getImageData(0, 0, W, H);
}
const TEX_MIX = { hud: 0, tactical: 0.35, nightvision: 1, thermal: 1, intel: 1, neon80s: 0.45, techno: 0.6 };
async function compose() {
  if (!DAY) return; if (texBusy) { texAgain = true; return; } texBusy = true;
  const W = DAY.width, H = DAY.height, out = new ImageData(W, H), o = out.data, d = DAY.data, nt = NIGHT.data, R = lut(), th = skin(), mx = TEX_MIX[th] ?? 0;
  const sl = new Float32Array(W), cl = new Float32Array(W);
  for (let x = 0; x < W; x++) { const l = (-180 + (x + 0.5) * 360 / W) * D2R; sl[x] = Math.sin(l); cl[x] = Math.cos(l); }
  for (let y = 0; y < H; y++) {
    const p = (90 - (y + 0.5) * 180 / H) * D2R, sp = Math.sin(p), cp = Math.cos(p);
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const day = SUN_ON ? smooth(-0.08, 0.08, sl[x] * cp * SUN[0] - cl[x] * cp * SUN[1] + sp * SUN[2]) : 1, k = 0.16 + 0.84 * day, nk = (1 - day) * 1.3;
      let r = d[i] * k + nt[i] * nk, g = d[i + 1] * k + nt[i + 1] * nk, b = d[i + 2] * k + nt[i + 2] * nk;
      if (mx) {
        const lum = Math.min(255, 0.3 * r + 0.59 * g + 0.11 * b), j = (th === "intel" ? 0 : (lum | 0)) * 3;
        if (th === "intel") { r = g = b = r * (1 - mx) + lum * mx; }
        else { r = r * (1 - mx) + R[j] * mx; g = g * (1 - mx) + R[j + 1] * mx; b = b * (1 - mx) + R[j + 2] * mx; }
      }
      o[i] = r; o[i + 1] = g; o[i + 2] = b; o[i + 3] = 255;
    }
  }
  EARTH_IMG = await createImageBitmap(out); texBusy = false; layersDirty = true;
  if (texAgain) { texAgain = false; compose(); }
}

// ---------- tracts: loaded per state while the state is in view at zoom ≥ 7, dropped when it leaves
async function loadTracts(st) {
  if (!st || TR_LOADED.has(st) || TR_LOADING.has(st) || !TR_INDEX || !TR_INDEX.states[st]) return;
  TR_LOADING.add(st); setStatus(`loading ${TR_INDEX.states[st].count.toLocaleString()} census tracts · ${st}…`);
  const d = await getJSON(U + "data/tracts/" + TR_INDEX.states[st].file);
  TR_LOADING.delete(st);
  if (!d || !Array.isArray(d.tracts) || !TR_WANT.has(st)) { setStatus(""); return; }
  const feats = [];
  d.tracts.forEach(t => { if (!t || !t.g) return; if (t.m) TRACT.set(+t.g, t.m);
    if (Array.isArray(t.geom)) feats.push({ type: "Feature", geometry: { type: "MultiPolygon", coordinates: t.geom }, properties: { g: t.g }, m: t.m || {} }); });
  TR_FEAT.set(st, feats); TR_LOADED.add(st);
  setStatus(`tracts in view: ${[...TR_LOADED].join(", ")} · ${TRACT.size.toLocaleString()} tracts`);
  if (CM.kind === "t") { computeTrBreaks(); renderLegend(); recolorDirty = true; }
  if (selected && !HOUSE) renderCard();
  layersDirty = true;
}
function unloadTracts(st) {
  const f = TR_INDEX && TR_INDEX.states[st] && +TR_INDEX.states[st].fips;
  TR_LOADED.delete(st); TR_FEAT.delete(st);
  if (f) for (const k of [...TRACT.keys()]) if (Math.floor(k / 1e9) === f) TRACT.delete(k);
  if (CM.kind === "t") { computeTrBreaks(); recolorDirty = true; }
  layersDirty = true;
}
let TR_WANT = new Set();
function tractTick(b) {
  if (!TR_INDEX) return;
  const want = new Set(), z = VS.zoom;
  if (TRACTS_ON && VIEW !== "globe" && z >= Z_TRACTS - 0.3 && b) {
    const cands = [];
    for (const [st, s] of Object.entries(TR_INDEX.states)) { const bb = s.bbox; if (bb[2] >= b[0] && bb[0] <= b[2] && bb[3] >= b[1] && bb[1] <= b[3]) cands.push([st, Math.hypot((bb[0] + bb[2]) / 2 - VS.longitude, (bb[1] + bb[3]) / 2 - VS.latitude)]); }
    cands.sort((a, c) => a[1] - c[1]).slice(0, 4).forEach(([st]) => want.add(st));
  } else if (CM.kind === "t" && VIEW === "globe" && z >= 4) { const st = stateAt(VS.longitude, VS.latitude); if (st) want.add(st); }
  if (selected && CM.kind === "t") { const r = COUNTY.get(selected); if (r) want.add(r.STUSPS); }
  TR_WANT = want;
  for (const st of [...TR_LOADED]) if (!want.has(st)) unloadTracts(st);
  for (const st of want) loadTracts(st);
}
function stateAt(lon, lat) {
  let best = null, ba = Infinity;
  for (const [st, b] of STATE_BBOX) { const x = lon < b[0] ? lon + 360 : lon; if (x >= b[0] && x <= b[2] && lat >= b[1] && lat <= b[3]) { const a = (b[2] - b[0]) * (b[3] - b[1]); if (a < ba) { ba = a; best = st; } } }
  return best;
}
function wrapBox(lons, lats) {
  let a0 = 999, a1 = -999, b0 = 999, b1 = -999, y0 = 99, y1 = -99;
  for (let i = 0; i < lons.length; i++) { const l = lons[i], s = l < 0 ? l + 360 : l; if (l < a0) a0 = l; if (l > a1) a1 = l; if (s < b0) b0 = s; if (s > b1) b1 = s; if (lats[i] < y0) y0 = lats[i]; if (lats[i] > y1) y1 = lats[i]; }
  return (a1 - a0) <= (b1 - b0) ? [a0, y0, a1, y1] : [b0 - 360, y0, b1 - 360, y1];
}

// ---------- metro buildings (usa/metros/<m>/index.json + tiles/<id>.json): per 0.05° tile in view at zoom ≥ 12, dropped when out
let METROS = [], MT_TILES = [], MT_LOADED = new Map(), MT_PENDING = new Set(), MVALS = null, MM = null, LAST_BOUNDS = null;
const MT_BASE = [
  { k: "h", label: "Building height", fmt: v => v.toFixed(1) + " m" },
  { k: "hail", label: "Max hail · 24 mo", fmt: v => v.toFixed(2) + " in" },
  { k: "lst", label: "Summer surface temp", fmt: v => v.toFixed(1) + " °C" },
  { k: "area", label: "Footprint area", fmt: v => Math.round(v).toLocaleString() + " m²" },
];
const MT_TRACT = ["income", "home_value", "year_built", "owner_pct", "sf_pct", "nri_hail", "hpi_5y", "oz27", "qct"];
function metroMetrics() {
  const out = MT_BASE.slice();
  if (METROS.some(m => m.dicts && (m.dicts.fz || m.dicts.flood))) out.push({ k: "fz", label: "FEMA flood zone", cat: true });
  MT_TRACT.forEach(k => { const tm = TR_METRICS.find(x => x.k === k); if (tm && METROS.some(m => m.tracts && Array.isArray(m.tracts[k]))) out.push({ k: "t:" + k, label: tm.label + " (tract)", fmt: tm.fmt, flag: tm.flag }); });
  return out;
}
async function loadMetroList() {
  let list = await getJSON(U + "metros/index.json");
  if (list && !Array.isArray(list)) list = list.metros ?? Object.keys(list);
  if (!Array.isArray(list)) return;
  const ids = list.map(x => typeof x === "string" ? x : pick(x, "metro", "id", "slug")).filter(Boolean);
  const metas = await Promise.all(ids.map(id => getJSON(`${U}metros/${encodeURIComponent(id)}/index.json`).then(m => m && Array.isArray(m.tiles) ? { ...m, _id: id } : null)));
  METROS = metas.filter(Boolean);
  METROS.forEach((m, mi) => m.tiles.forEach(t => MT_TILES.push({ ...t, mi, key: m._id + "/" + t.id })));
  if (METROS.length) {
    const sel = $("mmetric"), ms = metroMetrics(); sel.innerHTML = ms.map(m => `<option value="${esc(m.k)}">${esc(m.label)}</option>`).join("");
    sel.onchange = () => { MM = metroMetrics().find(m => m.k === sel.value) || MT_BASE[0]; MVALS = null; recolorMetro(); };
    MM = ms[0]; $("mmetricRow").hidden = false;
  }
}
function metroTick(b) {
  if (!MT_TILES.length) return;
  const on = BLDG_ON && VIEW !== "globe" && VS.zoom >= Z_METRO - 0.5 && b;
  if (!on) { if (MT_LOADED.size) { MT_LOADED.clear(); layersDirty = true; } return; }
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2, hx = (b[2] - b[0]) / 2, hy = (b[3] - b[1]) / 2;
  const keep = [cx - hx * 1.6, cy - hy * 1.6, cx + hx * 1.6, cy + hy * 1.6], hit = (t, q) => t.bbox[2] >= q[0] && t.bbox[0] <= q[2] && t.bbox[3] >= q[1] && t.bbox[1] <= q[3];
  for (const [k, t] of MT_LOADED) if (!hit(t, keep)) { MT_LOADED.delete(k); layersDirty = true; }
  const want = MT_TILES.filter(t => !MT_LOADED.has(t.key) && !MT_PENDING.has(t.key) && hit(t, b))
    .sort((a, c) => Math.hypot(a.bbox[0] - VS.longitude, a.bbox[1] - VS.latitude) - Math.hypot(c.bbox[0] - VS.longitude, c.bbox[1] - VS.latitude));
  want.slice(0, Math.max(0, 6 - MT_PENDING.size)).forEach(loadTile);
}
async function loadTile(t) {
  MT_PENDING.add(t.key);
  const m = METROS[t.mi], d = await getJSON(`${U}metros/${encodeURIComponent(m._id)}/tiles/${encodeURIComponent(t.id)}.json`);
  MT_PENDING.delete(t.key);
  if (d && d.n > 0) {
    const n = d.n, q = m.quantum_deg || 1e-5, bb = d.bbox || t.bbox, at = (k, i) => { const v = d[k]; return Array.isArray(v) ? v[i] : v; };
    const o = { n, mi: t.mi, lon: new Float64Array(n), lat: new Float64Array(n), h: new Float32Array(n), area: new Float32Array(n), hail: new Int16Array(n), hd: new Int16Array(n),
      lst: new Int16Array(n), lsc: new Uint8Array(n), cls: new Uint16Array(n), sub: new Uint16Array(n), src: new Uint8Array(n), hs: new Uint8Array(n), cty: new Uint8Array(n), tr: new Int32Array(n), fz: new Int16Array(n).fill(-1) };
    let cy = 0; const fzk = d.fz != null ? "fz" : d.flood != null ? "flood" : null;
    for (let i = 0; i < n; i++) {
      cy += at("dy", i) || 0; o.lon[i] = bb[0] + (at("x", i) || 0) * q; o.lat[i] = bb[1] + cy * q;
      o.h[i] = (at("h", i) || 0) / 10; o.area[i] = at("area", i) || 0; o.hail[i] = at("hail", i) ?? -1; o.hd[i] = at("hd", i) || 0;
      o.lst[i] = at("lst", i) ?? -9999; o.lsc[i] = at("lsc", i) || 0; o.cls[i] = at("cls", i) || 0; o.sub[i] = at("sub", i) || 0; o.src[i] = at("src", i) || 0; o.hs[i] = at("hs", i) || 0; o.cty[i] = at("cty", i) || 0;
      const tri = at("tr", i); o.tr[i] = tri == null || tri < 0 || !Array.isArray(d.tracts) ? (typeof d.tracts === "number" ? d.tracts : -1) : d.tracts[tri] ?? -1;
      if (fzk) o.fz[i] = at(fzk, i) ?? -1;
    }
    const el = groundAt((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { pos[i * 3] = o.lon[i]; pos[i * 3 + 1] = o.lat[i]; pos[i * 3 + 2] = el + o.h[i] + 1.5; }
    o.pos = pos; t.data = o;
    if (LAST_BOUNDS && VS.zoom >= Z_METRO - 0.5 && VIEW !== "globe") { if (!MVALS) metroVals(o); colorTile(t); MT_LOADED.set(t.key, t); layersDirty = true; statsDirty = true; }
  }
  if (LAST_BOUNDS) metroTick(LAST_BOUNDS);
}
function mval(o, i, k = MM.k) {
  if (k === "h") return o.h[i] > 0 ? o.h[i] : null;
  if (k === "hail") return o.hail[i] >= 0 ? o.hail[i] / 100 : null;
  if (k === "lst") return o.lst[i] > -9000 ? o.lst[i] / 10 : null;
  if (k === "area") return o.area[i] > 0 ? o.area[i] : null;
  if (k === "fz") return o.fz[i] >= 0 ? o.fz[i] : null;
  if (k.startsWith("t:")) { const tr = o.tr[i], col = tr >= 0 && METROS[o.mi].tracts ? METROS[o.mi].tracts[k.slice(2)] : null; return col ? col[tr] ?? null : null; }
  return null;
}
function metroVals(extra) {   // quantile scale from a sample of the loaded buildings (fixed until the metric or theme changes)
  const vals = [], src = [...MT_LOADED.values()].map(t => t.data); if (extra) src.push(extra);
  for (const o of src) { const step = Math.max(1, Math.floor(o.n / 4000)); for (let i = 0; i < o.n; i += step) { const v = mval(o, i); if (v != null) vals.push(v); } }
  vals.sort((a, b) => a - b); MVALS = vals; MM._min = vals[0]; MM._max = vals[vals.length - 1]; MM._mid = vals[vals.length >> 1];
}
function colorTile(t) {
  const o = t.data, n = o.n, col = new Uint8Array(n * 4), other = T.rgb("other"), R = lut(), V = MVALS || [];
  const tOf = v => { if (MM.flag) return v >= 2 ? 1 : v >= 1 ? 0.8 : 0.15; if (MM.cat) return 0.15 + 0.8 * ((v * 0.618) % 1);
    let lo = 0, hi = V.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (V[mid] < v) lo = mid + 1; else hi = mid; } return V.length ? lo / V.length : 0.5; };
  for (let i = 0; i < n; i++) {
    const v = mval(o, i), q = i * 4;
    if (v == null) { col[q] = other[0]; col[q + 1] = other[1]; col[q + 2] = other[2]; col[q + 3] = 80; continue; }
    const j = ((0.14 + 0.86 * tOf(v)) * 255 | 0) * 3; col[q] = R[j]; col[q + 1] = R[j + 1]; col[q + 2] = R[j + 2]; col[q + 3] = 235;
  }
  t.bin = { length: n, attributes: { getPosition: { value: o.pos, size: 3 }, getFillColor: { value: col, size: 4, normalized: true } } };
}
function recolorMetro() { if (!MT_LOADED.size) return; if (!MVALS) metroVals(); for (const t of MT_LOADED.values()) colorTile(t); layersDirty = true; renderLegend(); }
function metroTooltip(t, i) {
  const o = t.data, m = METROS[o.mi], c = m.counties && m.counties[o.cty[i]], dct = m.dicts || {}, rows = [];
  const tr = o.tr[i], geo = tr >= 0 && m.tracts && m.tracts.geoid ? m.tracts.geoid[tr] : null;
  rows.push(["Height", o.h[i] > 0 ? o.h[i].toFixed(1) + " m" + (o.hs[i] === 2 ? " (floors × 3.2)" : "") : "unknown"], ["Footprint", o.area[i] ? Math.round(o.area[i]).toLocaleString() + " m²" : "—"]);
  const cls = dct.cls && dct.cls[o.cls[i]], sub = dct.sub && dct.sub[o.sub[i]]; if (cls || sub) rows.push(["Class", [sub, cls].filter(Boolean).join(" · ")]);
  if (o.hail[i] >= 0) { const day = m.hail && m.hail.days && m.hail.days[o.hd[i] - 1]; rows.push(["Max hail · 24 mo", (o.hail[i] / 100).toFixed(2) + " in" + (day ? " · " + day.date : "")]); }
  if (o.lst[i] > -9000) rows.push(["Summer LST", (o.lst[i] / 10).toFixed(1) + " °C"]);
  if (o.fz[i] >= 0) { const fd = dct.fz || dct.flood; rows.push(["Flood zone", fd ? fd[o.fz[i]] : String(o.fz[i])]); }
  if (geo) { rows.push(["Tract", geo]); ["income", "home_value", "qct", "oz27", "nri_hail"].forEach(k => { const tm = TR_METRICS.find(x => x.k === k), v = m.tracts[k] && m.tracts[k][tr]; if (tm && v != null) rows.push([tm.label, trFmt(tm, v)]); }); }
  const srcN = dct.src && dct.src[o.src[i]];
  return { html: `<div class="lbl" style="color:var(--cyan)">Building · ${esc(m.name || m._id)}</div>${c ? `<b>${esc(c.name)} County, ${esc(c.state)}</b>` : ""}<dl class="kv">${rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl><div class="lbl" style="margin-top:3px">${esc(srcN || "")} · Overture ${esc(m.overture_release || "")} · ODbL</div>` };
}

// ---------- outlines (hover / selection), cached per county + relief
const OUTLINE_CACHE = new Map();
function outline(fips) {
  const f = fips && FEAT.get(fips); if (!f) return null;
  const globe = VIEW === "globe", key = fips + "|" + (globe ? LIFT.key : "m");
  if (OUTLINE_CACHE.has(key)) return OUTLINE_CACHE.get(key);
  const ci = META.counties.indexOf(fips), z = globe ? (CO_EL && ci >= 0 ? CO_EL[ci] : 0) * LIFT.e + 400 : 0, out = [];
  const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  polys.forEach(p => p.forEach(ring => out.push(ring.map(c => [c[0], c[1], z]))));
  if (OUTLINE_CACHE.size > 40) OUTLINE_CACHE.clear();
  OUTLINE_CACHE.set(key, out); return out;
}

// ---------- one copy of each LiDAR point: inside a loaded built block the block draws every point itself (same USGS source),
// so the streamed EPT points inside that block's box are not drawn twice (they are skipped in the vertex shader, not dropped)
class HideBoxExtension extends deck.LayerExtension {
  static componentName = "HideBoxExtension";
  static extensionName = "HideBoxExtension";
  getShaders() {
    return { modules: [{ name: "hidebox", vs: "uniform hideboxUniforms { vec4 b0; vec4 b1; vec4 b2; } hidebox;", uniformTypes: { b0: "vec4<f32>", b1: "vec4<f32>", b2: "vec4<f32>" } }],
      inject: { "vs:#main-end": `
        vec2 hbp = geometry.worldPosition.xy;
        if ((hbp.x > hidebox.b0.x && hbp.x < hidebox.b0.z && hbp.y > hidebox.b0.y && hbp.y < hidebox.b0.w) ||
            (hbp.x > hidebox.b1.x && hbp.x < hidebox.b1.z && hbp.y > hidebox.b1.y && hbp.y < hidebox.b1.w) ||
            (hbp.x > hidebox.b2.x && hbp.x < hidebox.b2.z && hbp.y > hidebox.b2.y && hbp.y < hidebox.b2.w)) gl_Position = vec4(0.0, 0.0, 2.0, 1.0);` } };
  }
  draw() {
    const b = this.props.hideBoxes || [], e = [1, 1, -1, -1], v = i => b[i] || e;
    for (const m of this.getModels()) m.shaderInputs.setProps({ hidebox: { b0: v(0), b1: v(1), b2: v(2) } });
  }
}
const HIDE_EXT = new HideBoxExtension();
let HIDE_CACHE = new WeakMap(), HIDE_KEY = "";
function blockBoxes() {
  const out = [];
  if (BL && BL.blocks) for (const B of BL.blocks.values()) if (B.state === "ready" && Array.isArray(B.bounds)) out.push(B.bounds);
  return out.slice(0, 3);
}
function eptLayersDeduped() {
  const L = safe(() => EPT.layers(), []) || [], boxes = blockBoxes(), key = JSON.stringify(boxes);
  if (key !== HIDE_KEY) { HIDE_KEY = key; HIDE_CACHE = new WeakMap(); }
  if (!boxes.length) return L;
  const RDm = 40.03e6 / (2 * Math.PI);   // deck / ept.js metre frame (METER_OFFSETS from coordinateOrigin)
  return L.map(l => {
    if (!l || l.props.coordinateSystem !== deck.COORDINATE_SYSTEM.METER_OFFSETS) return l;
    let c = HIDE_CACHE.get(l); if (c) return c;
    const [lng0, lat0] = l.props.coordinateOrigin, cp = Math.cos(lat0 * D2R), zo = l.props.modelMatrix ? 0 : 0;
    const hb = boxes.map(b => [(b[0] - lng0) * D2R * RDm * cp, (b[1] - lat0) * D2R * RDm, (b[2] - lng0) * D2R * RDm * cp, (b[3] - lat0) * D2R * RDm]);
    c = l.clone({ extensions: [...(l.props.extensions || []), HIDE_EXT], hideBoxes: hb }); HIDE_CACHE.set(l, c); return c;
  });
}

// ---------- layers: rebuilt only when something changed (data, theme, selection, or a quantised zoom fade step)
function fadeKey() {
  const z = VS.zoom, q = v => Math.round(v * 20) / 20;
  return [VIEW, LIFT.key, q(1 - smooth(8, 10, z)), q(1 - smooth(9.5, 11.5, z)), q(1 - smooth(10.5, 12, z)), q(smooth(Z_TRACTS - 0.3, Z_TRACTS + 0.3, z) * (1 - smooth(12, 13.2, z))),
    q(smooth(Z_METRO - 0.5, Z_METRO + 0.3, z)), z < 15.5, Math.round(LIFT_Z / 3)].join("|");
}
function liftMatrix() { const z = Math.round(LIFT_Z / 3) * 3; return z ? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, z, 1] : undefined; }
function buildLayers() {
  if (!WBIN) return [];
  const th = skin(), acc = T.rgb("accent"), sel = T.rgb("select"), pin = T.rgb("pin"), other = T.rgb("other"), globe = VIEW === "globe", z = VIEW === "walk" ? 20 : VS.zoom, L = [];
  const MMX = globe ? undefined : liftMatrix(), mm = MMX ? { modelMatrix: MMX } : {};
  if (globe && TEX_ON && EARTH_IMG) L.push(new deck.BitmapLayer({ id: "earth", image: EARTH_IMG, bounds: [-180, -90, 180, 90], _imageCoordinateSystem: deck.COORDINATE_SYSTEM.LNGLAT }));
  if (th === "techno" && globe) {
    L.push(new deck.PathLayer({ id: "grat-glow", data: GRATICULE, getPath: d => d, getColor: [...acc.slice(0, 3), 30], widthUnits: "pixels", getWidth: 4, updateTriggers: { getColor: th } }));
    L.push(new deck.PathLayer({ id: "grat", data: GRATICULE, getPath: d => d, getColor: [...acc.slice(0, 3), 110], widthUnits: "pixels", getWidth: 0.8, updateTriggers: { getColor: th } }));
  }
  // dots: radius in metres (clamped in pixels), so zooming never rebuilds them
  const dotBase = { billboard: true, radiusUnits: "meters", getRadius: 1, stroked: false, filled: true, antialiasing: false, parameters: { depthWriteEnabled: true } };
  const usStep = META.grid.usa_deg * 111000;
  const wA = globe ? 1 : 1 - smooth(8, 10, z);
  if ((!TEX_ON || !globe) && wA > 0.02) L.push(new DotLayer({ ...dotBase, id: "world", data: WBIN, radiusScale: 0.42 * 33000, radiusMinPixels: 0.6, radiusMaxPixels: 2.6, opacity: wA }));
  if (globe && EPT_ON && EBIN) L.push(new DotLayer({ ...dotBase, id: "ept", data: EBIN, radiusScale: 0.42 * usStep * 2.6, radiusMinPixels: 1.8, radiusMaxPixels: 11.7, getFillColor: [...acc.slice(0, 3), th === "intel" ? 22 : 30], updateTriggers: { getFillColor: th },
    parameters: { depthWriteEnabled: false, blendColorSrcFactor: "src-alpha", blendColorDstFactor: "one", blendAlphaSrcFactor: "one", blendAlphaDstFactor: "one" } }));
  const uA = globe ? 1 : 1 - smooth(9.5, 11.5, z);
  if (uA > 0.02) L.push(new DotLayer({ ...dotBase, id: "usa", data: UBIN, radiusScale: 0.42 * usStep, radiusMinPixels: 0.7, radiusMaxPixels: 4.5, opacity: uA, pickable: globe,
    onHover: globe ? (info => setHover(info.index >= 0 ? META.counties[UD.county[info.index]] : null)) : null }));
  if (!globe) {
    // county choropleth (County Radar): fades out under the LiDAR
    const cA = (1 - smooth(10.5, 12, z)) * (CM.kind === "t" && TR_LOADED.size ? 0.3 : 1) * (CM.kind === "e" ? 0 : 1);
    if (GEO && cA > 0.02) L.push(new deck.GeoJsonLayer({ id: "counties", data: GEO, pickable: true, stroked: true, filled: true, opacity: 0.62 * cA, ...mm,
      getFillColor: f => { const r = COUNTY.get(f.properties.f), b = CM.kind === "c" ? bandOf(r && r[metric.k]) : -1; return b < 0 ? [other[0], other[1], other[2], 60] : [...bandColor(b), 235]; },
      getLineColor: T.rgb("sky", 200), lineWidthUnits: "pixels", getLineWidth: 0.5, lineWidthMinPixels: 0.3,
      updateTriggers: { getFillColor: [metric.k, th, CM.kind], getLineColor: th },
      onHover: info => setHover(info.object ? info.object.properties.f : null) }));
    // census tracts of the states in view
    const tA = smooth(Z_TRACTS - 0.3, Z_TRACTS + 0.3, z) * (1 - smooth(12, 13.2, z));
    if (TRACTS_ON && tA > 0.02) for (const [st, feats] of TR_FEAT) L.push(new deck.GeoJsonLayer({ id: "tr:" + st, data: feats, pickable: true, stroked: true, filled: true, opacity: tA, ...mm,
      getFillColor: f => { if (CM.kind !== "t") return [0, 0, 0, 1]; const b = trBand(f.m[TRM.k]); return b < 0 ? [other[0], other[1], other[2], 40] : [...bandColor(b), 200]; },
      getLineColor: [...acc.slice(0, 3), CM.kind === "t" ? 90 : 60], lineWidthUnits: "pixels", getLineWidth: 0.6,
      updateTriggers: { getFillColor: [CM.kind, TRM.k, th, trBreaks.join(",")], getLineColor: [th, CM.kind] },
      onHover: info => setHover(info.object ? info.object.properties.g.slice(0, 5) : null) }));
  }
  if (STATES_ON) {
    const glow = th === "neon80s" || th === "techno";
    if (glow) L.push(new deck.PathLayer({ id: "state-glow", data: STATE_PATHS, getPath: d => d, getColor: [...T.rgb(th === "neon80s" ? "building" : "accent").slice(0, 3), 55], widthUnits: "pixels", getWidth: 5, updateTriggers: { getColor: th }, ...mm }));
    L.push(new deck.PathLayer({ id: "states", data: STATE_PATHS, getPath: d => d, getColor: [...acc.slice(0, 3), glow ? 230 : 120], widthUnits: "pixels", getWidth: glow ? 1.4 : 0.9, updateTriggers: { getColor: th }, ...mm }));
  }
  if (OZ_ON && OZ && !globe) {
    L.push(new deck.GeoJsonLayer({ id: "oz18", data: OZ.f18, stroked: true, filled: true, pickable: true, getFillColor: [255, 79, 168, 110], getLineColor: [255, 79, 168, 230], lineWidthUnits: "pixels", getLineWidth: 0.6, ...mm }));
    L.push(new deck.GeoJsonLayer({ id: "oz27", data: OZ.f27, stroked: true, filled: true, pickable: true, getFillColor: [255, 179, 217, 25], getLineColor: [255, 179, 217, 240], lineWidthUnits: "pixels", getLineWidth: 1.2, ...mm }));
  }
  const outA = VIEW === "walk" ? 0 : 1;
  const hp = hovered && hovered !== selected && outA ? outline(hovered) : null;
  if (hp) L.push(new deck.PathLayer({ id: "hover", data: hp, getPath: d => d, getColor: [...acc.slice(0, 3), 230], widthUnits: "pixels", getWidth: 1.6, parameters: { depthTest: !globe }, ...mm }));
  const sp = selected && outA ? outline(selected) : null;
  if (sp) {
    L.push(new deck.PathLayer({ id: "sel-glow", data: sp, getPath: d => d, getColor: [...sel.slice(0, 3), 80], widthUnits: "pixels", getWidth: 8, updateTriggers: { getColor: th }, parameters: { depthTest: !globe }, ...mm }));
    L.push(new deck.PathLayer({ id: "sel", data: sp, getPath: d => d, getColor: [...sel.slice(0, 3), 255], widthUnits: "pixels", getWidth: 1.8, updateTriggers: { getColor: th }, parameters: { depthTest: !globe }, ...mm }));
  }
  // Earth-observation overlays: flat on the ground, under buildings and LiDAR
  if (EO && VIEW !== "walk") for (const l of safe(() => EO.layers({ globe, zoom: z, modelMatrix: MMX, over: globe ? undefined : false }), []) || []) if (l) L.push(l);
  // metro buildings: one binary layer per loaded tile (stable ids, never rebuilt while panning)
  if (!globe && BLDG_ON) { const mA = smooth(Z_METRO - 0.5, Z_METRO + 0.3, z);
    if (mA > 0.02) for (const t of MT_LOADED.values()) if (t.bin) L.push(new deck.ScatterplotLayer({ id: "mt:" + t.key, data: t.bin, billboard: true, radiusUnits: "meters", getRadius: 1, radiusScale: 5.6,
      radiusMinPixels: 0.55, radiusMaxPixels: 3, stroked: false, antialiasing: false, opacity: mA, pickable: true, autoHighlight: true, highlightColor: [...sel.slice(0, 3), 255] })); }
  // LiDAR and built blocks (other lanes)
  if (!globe && EPT) for (const l of eptLayersDeduped()) if (l) L.push(l);
  if (!globe && BL) for (const l of safe(() => BL.layers(), []) || []) if (l) L.push(l);
  if (!globe && VS.zoom >= Z_BLOCKS - 1) for (const l of mtLayers()) L.push(l);
  if (PT) for (const l of safe(() => PT.layers({ globe, zoom: z, modelMatrix: MMX }), []) || []) if (l) L.push(l);
  if (EO && VIEW !== "walk" && !globe) for (const l of safe(() => EO.layers({ globe, zoom: z, modelMatrix: MMX, over: true }), []) || []) if (l) L.push(l);   // gas washes over the LiDAR
  if (PA && !globe) for (const l of safe(() => PA.layers({ globe, zoom: z }), []) || []) if (l) L.push(l);
  if (BZ && !globe) for (const l of safe(() => BZ.layers({ globe, zoom: z, modelMatrix: MMX }), []) || []) if (l) L.push(l);   // absolute ground heights, like the LiDAR: no camera-lift matrix
  if (SKY && VIEW !== "walk") for (const l of safe(() => SKY.layers({ globe, modelMatrix: MMX }), []) || []) if (l) L.push(l);
  // block markers below street zoom
  if (BLOCKS.length && PINS_ON && z < 15.5 && VIEW !== "walk") {
    const zs = Math.max(0.45, Math.min(1, (z + 1) / 6)), p3 = pin.slice(0, 3), pz = b => [b.lon, b.lat, globe ? b._el * LIFT.e + 2500 : b._el + 40];
    const base = { data: BLOCKS, billboard: true, getPosition: pz, radiusUnits: "pixels", parameters: { depthTest: false }, updateTriggers: { getPosition: [LIFT.key, VIEW], getFillColor: th, getLineColor: th } };
    L.push(new DotLayer({ ...base, id: "pin-halo", getRadius: 17 * zs, getFillColor: [...p3, 45] }));
    L.push(new DotLayer({ ...base, id: "pin-ring", getRadius: 9 * zs, stroked: true, filled: true, getFillColor: [...p3, 110], getLineColor: [...p3, 255], lineWidthUnits: "pixels", getLineWidth: 1.5 }));
    L.push(new DotLayer({ ...base, id: "pins", pickable: true, getRadius: Math.max(2.5, 4 * zs), getFillColor: [255, 255, 255, 255] }));
  }
  return L;
}
function setHover(f) { if (f !== hovered) { hovered = f; layersDirty = true; } }

// ---------- tooltip / click
function tooltip(info) {
  const id = info.layer && info.layer.id;
  if (!id) return null;
  if (/^(mnt|pool):/.test(id) && MT && MT.tooltip) { const t = safe(() => MT.tooltip(info), null); if (t) return t; }
  if (id.startsWith("pa:") && PA) return safe(() => PA.tooltip(info), null);
  if (id.startsWith("bz:") && BZ) return safe(() => BZ.tooltip(info), null);
  if (id.startsWith("sky:") && SKY) return safe(() => SKY.tooltip(info), null);
  if (id.startsWith("pt:") && PT && PT.tooltip) return safe(() => PT.tooltip(info), null);
  if (id.startsWith("eo:") && EO && EO.tooltip) return safe(() => EO.tooltip(info), null);
  if (id.startsWith("mt:") && info.index >= 0) { const t = MT_LOADED.get(id.slice(3)); return t && t.data ? metroTooltip(t, info.index) : null; }
  if (id === "usa" && info.index >= 0) {
    const i = info.index, r = COUNTY.get(META.counties[UD.county[i]]); if (!r) return null;
    const k = trKey(i), m = k >= 0 ? TRACT.get(k) : null;
    return { html: `<b>${esc(r.NAME)}, ${esc(r.STUSPS)}</b><br>${metricLine(r, m)}<span class="lbl">Elevation</span> <span class="mono">${UD.elev[i].toLocaleString()} m</span>${UD.flags[i] & 1 ? `<br><span class="lbl" style="color:var(--cyan)">◆ in 3DEP LiDAR coverage</span>` : ""}<br><span class="lbl">click to zoom in</span>` };
  }
  if (id === "counties" && info.object) { const r = COUNTY.get(info.object.properties.f); if (!r) return null;
    return { html: `<b>${esc(r.NAME)}, ${esc(r.STUSPS)}</b><br>${metricLine(r, null)}<span class="lbl">Overlap rank</span> <span class="mono">#${r.rank} of ${N_TOTAL.toLocaleString()}</span><br><span class="lbl">click to zoom in · dossier</span>` }; }
  if (id.startsWith("tr:") && info.object) { const f = info.object, r = COUNTY.get(f.properties.g.slice(0, 5)), m = f.m || {};
    const rows = TR_METRICS.filter(x => ["income", "home_value", "year_built", "owner_pct", "nri"].includes(x.k) || x.k === TRM.k).filter(x => m[x.k] != null).map(x => [x.label, trFmt(x, m[x.k])]);
    return { html: `<div class="lbl" style="color:var(--cyan)">Census tract ${esc(f.properties.g)}</div>${r ? `<b>${esc(r.NAME)}, ${esc(r.STUSPS)}</b>` : ""}<dl class="kv">${rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl>` }; }
  if (id === "pins" && info.object) { const b = info.object; return { html: `<div class="lbl" style="color:var(--pin)">Built block · LiDAR scan</div><b>${esc(b.city)}, ${esc(b.state)}</b><br>${b.houses ? esc(b.houses) + " houses · " : ""}${b.lidar_year ? "LiDAR " + esc(b.lidar_year) : ""}<br><span class="lbl">click to fly down</span>` }; }
  if ((id === "oz18" || id === "oz27") && info.object) { const p = info.object.properties, c = COUNTY.get(p.g.slice(0, 5));
    return { html: `<div class="lbl" style="color:#ff4fa8">Opportunity Zone · ${p.r === "2018" ? "2018 round (to 2028-12-31)" : "2027 round (2027–2036)"}</div><b>Tract ${esc(p.g)}</b>${c ? `<br>${esc(c.NAME)}, ${esc(c.STUSPS)}` : ""}` }; }
  const tt = BL && BL.tooltip ? safe(() => BL.tooltip(info), null) : null; if (tt) return typeof tt === "string" ? { text: tt } : tt;
  const te = EPT && EPT.tooltip ? safe(() => EPT.tooltip(info), null) : null; if (te) return te;
  return null;
}
function metricLine(r, m) {
  if (CM.kind === "c") return `<span class="lbl">${esc(metric.label)}</span> <span class="mono" style="color:var(--amber)">${esc(fmtVal(metric, r[metric.k]))}</span><br>`;
  if (CM.kind === "t") return `<span class="lbl">${esc(TRM.label)}</span> <span class="mono" style="color:var(--amber)">${esc(m ? trFmt(TRM, m[TRM.k]) : TR_LOADED.has(r.STUSPS) ? "—" : "zoom in for tracts")}</span><br>`;
  return "";
}
function onClick(info) {
  if (SKY && safe(() => SKY.pick(info), false)) return;
  if (BZ && safe(() => BZ.pick(info), false)) return;
  if (PA && safe(() => PA.pick(info), false)) return;
  if (MT && info && info.layer && /^(mnt|pool):/.test(info.layer.id) && safe(() => MT.pick(info), false)) return;
  if (BL && safe(() => BL.pick(info), false)) return;
  // a house under the cursor wins even when something else (points, a building dot) is on top
  if (BL && VIEW !== "globe" && VS.zoom >= Z_BLOCKS - 0.5) {
    const ids = (deckgl2.props.layers || []).filter(l => l && l.props.blockRole === "house").map(l => l.id);
    if (ids.length) { const h = deckgl2.pickObject({ x: info.x, y: info.y, radius: 4, layerIds: ids }); if (h && safe(() => BL.pick(h), false)) return; }
  }
  const id = info.layer && info.layer.id;
  if (!id) return;
  if (id === "pins" && info.object) return openBlock(info.object);
  if (id === "usa" && info.index >= 0) { const i = info.index; return select(META.counties[UD.county[i]], true, trKey(i)); }
  if (id === "counties" && info.object) return select(info.object.properties.f, true);
  if (id.startsWith("tr:") && info.object) return select(info.object.properties.g.slice(0, 5), VS.zoom < 9, +info.object.properties.g);
  if ((id === "oz18" || id === "oz27") && info.object) return select(info.object.properties.g.slice(0, 5), false, +info.object.properties.g);
  if (id.startsWith("mt:") && info.index >= 0) { const t = MT_LOADED.get(id.slice(3)); if (!t) return; const o = t.data, m = METROS[o.mi], c = m.counties && m.counties[o.cty[info.index]], tr = o.tr[info.index], geo = tr >= 0 && m.tracts && m.tracts.geoid ? m.tracts.geoid[tr] : null;
    if (c && c.fips) select(c.fips, false, geo ? +geo : null); }
}

// ---------- selection, dossier
function select(fips, fly = true, tract = null) {
  selected = fips || null; pickedTract = tract; if (HOUSE) { HOUSE = null; HOUSE_HTML = ""; safe(() => BL.clear()); }
  renderDossier(); recolorDirty = true; layersDirty = true;
  if (selected) { if (CLIP_ON) applyClip(); if (fly && CO_BBOX.has(selected)) flyTo(fitView(CO_BBOX.get(selected), 10.5)); }
  scheduleUrl();
}
function renderDossier() {
  if (HOUSE) return showHouse(HOUSE.id, false);
  renderCard();   // block badges are handled by the delegated click listener on #card (wireUi)
}
let HOUSE_HTML = "";
function showHouse(id, open) {
  let html = BL ? safe(() => BL.panelHtml(id), "") : "";
  if (html && MT && HOUSE && HOUSE.slug) { const mh = safe(() => MT.houseHtml(HOUSE.slug, HOUSE.id), ""); if (mh) html += mh; }
  const card = $("card");
  if (!html && !open) return;
  if (html === HOUSE_HTML && !card.hidden && !open) return;
  HOUSE_HTML = html;
  const st = card.scrollTop;
  card.innerHTML = `<button class="x" aria-label="Close" id="cardX"${html.includes("data-bm-close") ? " hidden" : ""}>×</button><div class="bm-panel" id="bmPanel">${html || `<div class="lbl">House ${esc(id)}</div><div class="caveat">Loading the block…</div>`}</div>${selected ? `<button class="badge" id="toCounty">◆ COUNTY DOSSIER</button>` : ""}`;
  card.hidden = false; if (!open) card.scrollTop = st;
  if (BL && BL.bindPanel) safe(() => BL.bindPanel($("bmPanel")));
  if (MT) safe(() => MT.bind($("bmPanel")));
  $("cardX").onclick = () => { HOUSE = null; HOUSE_HTML = ""; card.hidden = true; safe(() => BL.clear && BL.clear()); scheduleUrl(); };
  const tc = $("toCounty"); if (tc) tc.onclick = () => { HOUSE = null; renderDossier(); scheduleUrl(); };
}
function applyClip() {
  if (!EPT) return;
  if (!CLIP_ON || !selected || !FEAT.get(selected)) { safe(() => EPT.setClip(null)); layersDirty = true; return; }
  const g = FEAT.get(selected).geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  let best = polys[0][0]; polys.forEach(p => { if (p[0].length > best.length) best = p[0]; });
  safe(() => EPT.setClip(best)); layersDirty = true;
}
function openBlock(b) {
  if (b.fips && COUNTY.has(b.fips)) { selected = b.fips; pickedTract = null; recolorDirty = true; }
  const ft = BL && BL.flyTarget ? safe(() => BL.flyTarget(b.slug), null) : null;
  flyTo(ft && isFinite(ft.longitude) ? { ...ft, zoom: Math.min(19, ft.zoom ?? 17.2), pitch: Math.min(70, ft.pitch ?? 55) } : { longitude: b.lon, latitude: b.lat, zoom: 17.2, pitch: 55, bearing: -20 });
  scheduleUrl(b.slug);
}
function nearestBlock(km = 6) {
  let best = null, bd = Infinity;
  for (const b of BLOCKS) { const dx = norm180(b.lon - VS.longitude) * Math.cos(VS.latitude * D2R) * 111.32, dy = (b.lat - VS.latitude) * 110.57, d = Math.hypot(dx, dy); if (d < bd) { bd = d; best = b; } }
  return bd < km ? best : null;
}

// ---------- fly-to (zoom = map-equivalent; the hop zooms out over long distances)
function fitView(b, maxZoom = 10) {
  const el = $("deck"), w = el.clientWidth || 1200, h = el.clientHeight || 800, narrow = w < 760;
  const pad = narrow ? { left: 20, right: 20, top: 20, bottom: h * 0.5 } : { left: sideW() + 30, right: 400, top: 40, bottom: 40 };
  if (w - pad.left - pad.right < 100) { pad.left = pad.right = 20; }
  const west = b[0] < -180 ? b[0] + 360 : b[0], east = b[2] < west ? b[2] + 360 : b[2];
  try { const vp = new deck.WebMercatorViewport({ width: w, height: h }).fitBounds([[west, b[1]], [east, b[3]]], { padding: pad, maxZoom });
    return { longitude: norm180(vp.longitude), latitude: vp.latitude, zoom: Math.max(1, Math.min(maxZoom, vp.zoom)) }; }
  catch { return { longitude: norm180((b[0] + b[2]) / 2), latitude: (b[1] + b[3]) / 2, zoom: 6 }; }
}
const sideW = () => $("side").hidden ? 0 : $("side").offsetWidth;
function flyTo(t, opts = {}) {
  if (VIEW === "walk") exitWalk(true);
  const from = { ...VS }, dl = norm180(t.longitude - from.longitude), dist = Math.hypot(dl * Math.cos(((t.latitude + from.latitude) / 2) * D2R), t.latitude - from.latitude);
  const dur = opts.dur ?? Math.min(3600, 900 + dist * 18 + Math.abs(t.zoom - from.zoom) * 170);
  const zFit = Math.log2((($("deck").clientHeight || 800) * 0.6) / (Math.max(dist, 1e-6) * 512 / 360)), hop = Math.max(0, Math.min(from.zoom, t.zoom) - Math.max(0.9, zFit));
  FLY = { from, dl, to: t, hop, t0: performance.now(), dur, then: opts.then };
}
function flyStep(now) {
  const f = FLY, t = clamp01((now - f.t0) / f.dur), e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  VS = { longitude: norm180(f.from.longitude + f.dl * e), latitude: f.from.latitude + (f.to.latitude - f.from.latitude) * e,
    zoom: f.from.zoom + (f.to.zoom - f.from.zoom) * e - f.hop * Math.sin(Math.PI * t),
    pitch: f.to.pitch != null ? f.from.pitch + (f.to.pitch - f.from.pitch) * e : VS.pitch,
    bearing: f.to.bearing != null ? f.from.bearing + norm180(f.to.bearing - f.from.bearing) * e : VS.bearing };
  if (t >= 1) { FLY = null; f.then && f.then(); }
}

// ---------- camera: deck view state from VS, camera never under the ground
function camLift() {
  if (VIEW !== "map") return 0;
  const z = VS.zoom, b = smooth(9, 11.5, z);
  const gT = b > 0 ? groundAt(VS.longitude, VS.latitude) : 0;
  const H = $("deck").clientHeight || 800, mpp = 40075016 * Math.cos(VS.latitude * D2R) / (512 * 2 ** z), dist = 1.5 * H * mpp;
  const ch = dist * Math.cos(VS.pitch * D2R), back = dist * Math.sin(VS.pitch * D2R), br = VS.bearing * D2R;
  if (ch > 20000) return gT * b;
  const clon = VS.longitude - Math.sin(br) * back / (111320 * Math.cos(VS.latitude * D2R)), clat = VS.latitude - Math.cos(br) * back / 110540;
  return Math.max(gT * b, groundAt(clon, clat) + 3 - ch);
}
function deckViewState() {
  if (VIEW === "walk") {
    const H = $("deck").clientHeight || 800, mpp = 40075016 * Math.cos(WALK.lat * D2R) / (512 * 2 ** WALK_ZOOM), dist = 1.5 * H * mpp;
    const p = WALK.pitch * D2R, b = WALK.bearing * D2R, fwd = dist * Math.sin(p);
    return { longitude: WALK.lon + Math.sin(b) * fwd / (111320 * Math.cos(WALK.lat * D2R)), latitude: WALK.lat + Math.cos(b) * fwd / 110540, zoom: WALK_ZOOM,
      pitch: WALK.pitch, bearing: WALK.bearing, position: [0, 0, WALK.z - dist * Math.cos(p)], minZoom: WALK_ZOOM, maxZoom: WALK_ZOOM, maxPitch: 85, minPitch: 30 };
  }
  if (VIEW === "globe") return { longitude: VS.longitude, latitude: VS.latitude, zoom: VS.zoom - zoff(VS.latitude), pitch: 0, bearing: 0, minZoom: globeMinZoom() - zoff(VS.latitude), maxZoom: 8 };
  return { longitude: VS.longitude, latitude: VS.latitude, zoom: VS.zoom, pitch: VS.pitch, bearing: VS.bearing, position: [0, 0, LIFT_Z], minZoom: 4, maxZoom: 21.5, maxPitch: maxPitchFor(VS.zoom) };
}
function setView(v) {
  VIEW = v; SWITCH_AT = performance.now();
  const views = v === "globe" ? GLOBE_VIEW : v === "walk" ? WALK_VIEW : MAP_VIEW;
  const controller = v === "map" ? { dragRotate: true, touchRotate: true, doubleClickZoom: true, inertia: 300 }
    : v === "walk" ? { dragPan: false, dragRotate: true, scrollZoom: false, doubleClickZoom: false, touchZoom: false, keyboard: false, inertia: false } : GLOBE_CTRL;
  deckgl2.setProps({ views, controller });
  if (v === "globe") { VS = { ...VS, pitch: 0, bearing: 0 }; LIFT_Z = LIFT_T = 0; }
  checkLift(true); skyDirty = true; layersDirty = true; OUTLINE_CACHE.clear();
}
function applyVS() {
  if (VIEW !== "walk") {
    if (VIEW === "globe" && VS.zoom < globeMinZoom()) VS = { ...VS, zoom: globeMinZoom() };
    if (VIEW === "globe" && VS.zoom >= Z_TO_MAP) setView("map");
    else if (VIEW === "map" && VS.zoom < Z_TO_GLOBE) setView("globe");
  }
  if (VIEW === "map") { VS.pitch = Math.min(VS.pitch, maxPitchFor(VS.zoom)); LIFT_T = camLift(); if (LIFT_T > LIFT_Z) LIFT_Z = LIFT_T; }
  deckgl2.setProps({ viewState: deckViewState() });
  checkLift(); skyDirty = true;
  const k = fadeKey(); if (k !== layerKey) { layerKey = k; layersDirty = true; }
  scheduleSettle();
}

// ---------- walk mode: first-person camera, eye 1.7 m above the ground, never below it
const KEYS = new Set();
function enterWalk() {
  if (VIEW === "globe") return;
  FLY = null; const lon = VS.longitude, lat = VS.latitude;
  WALK = { lon, lat, bearing: VS.bearing, pitch: 82, z: groundAt(lon, lat) + 1.7, t: performance.now() };
  setView("walk"); $("walkHud").hidden = false; deckgl2.setProps({ viewState: deckViewState() }); scheduleSettle(); scheduleUrl();
}
function exitWalk(silent) {
  if (VIEW !== "walk") return;
  VS = { longitude: WALK.lon, latitude: WALK.lat, zoom: 18, pitch: 60, bearing: WALK.bearing };
  KEYS.clear(); $("walkHud").hidden = true; setView("map"); LIFT_Z = camLift();
  if (!silent) applyVS();
}
function walkStep(dt) {
  const fwd = (KEYS.has("ArrowUp") || KEYS.has("KeyW") ? 1 : 0) - (KEYS.has("ArrowDown") || KEYS.has("KeyS") ? 1 : 0);
  const turn = (KEYS.has("ArrowRight") ? 1 : 0) - (KEYS.has("ArrowLeft") ? 1 : 0), strafe = (KEYS.has("KeyD") ? 1 : 0) - (KEYS.has("KeyA") ? 1 : 0);
  const sp = (KEYS.has("ShiftLeft") || KEYS.has("ShiftRight") ? 6 : 1.5) * dt / 1000;
  let moved = false;
  if (turn) { WALK.bearing = (WALK.bearing + turn * 70 * dt / 1000 + 540) % 360 - 180; moved = true; }
  if (fwd || strafe) {
    const b = WALK.bearing * D2R, dE = (Math.sin(b) * fwd + Math.cos(b) * strafe) * sp, dN = (Math.cos(b) * fwd - Math.sin(b) * strafe) * sp;
    WALK.lon += dE / (111320 * Math.cos(WALK.lat * D2R)); WALK.lat += dN / 110540; moved = true;
  }
  const target = groundAt(WALK.lon, WALK.lat) + 1.7;
  if (Math.abs(target - WALK.z) > 0.01) { WALK.z = target > WALK.z ? target : WALK.z + (target - WALK.z) * Math.min(1, dt / 120); moved = true; }   // step up instantly, settle down smoothly
  if (moved) { deckgl2.setProps({ viewState: deckViewState() }); $("walkInfo").textContent = ` · ground ${(WALK.z - 1.7).toFixed(1)} m`; scheduleSettle(); }
}
addEventListener("keydown", e => {
  if (e.target.closest && e.target.closest("input,select,textarea")) return;
  if (VIEW === "walk") {
    if (e.code === "Escape") { exitWalk(); return; }
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "ShiftRight"].includes(e.code)) { KEYS.add(e.code); e.preventDefault(); }
    return;
  }
  if (e.code === "KeyW" && !e.ctrlKey && !e.metaKey && VS.zoom >= Z_WALK) { enterWalk(); e.preventDefault(); }
  if (e.code === "Escape") { if (catalogOpen) openCatalog(false); else if (HOUSE) { safe(() => BL.clear()); HOUSE = null; renderDossier(); scheduleUrl(); } else if (!$("card").hidden) select(null, false); }
});
addEventListener("keyup", e => KEYS.delete(e.code));
addEventListener("blur", () => KEYS.clear());

// ---------- sky: real IAU stars for the chosen time, atmosphere rim, the sun (globe); flat sky + horizon glow (map/walk)
function drawSky() {
  const cv = $("sky"), dpr = Math.min(2, devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const cx = cv.getContext("2d"); cx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const skyC = T.rgb("sky"); cx.fillStyle = `rgb(${skyC[0]},${skyC[1]},${skyC[2]})`; cx.fillRect(0, 0, w, h);
  if (!w) return;
  if (VIEW !== "globe") {
    const p = VIEW === "walk" ? WALK.pitch : VS.pitch; if (p < 35) return;
    const a = T.rgb("accent"), hy = h * (0.18 + (80 - p) / 160), g = cx.createLinearGradient(0, hy - h * 0.25, 0, hy + 20);
    g.addColorStop(0, `rgba(${a[0]},${a[1]},${a[2]},0)`); g.addColorStop(1, `rgba(${a[0]},${a[1]},${a[2]},${0.16 * smooth(35, 70, p)})`);
    cx.fillStyle = g; cx.fillRect(0, 0, w, hy + 20); return;
  }
  const gz = VS.zoom - zoff(VS.latitude);
  const vp = new deck._GlobeViewport({ width: w, height: h, longitude: VS.longitude, latitude: VS.latitude, zoom: gz });
  const F = h / 2 * vp.projectionMatrix[5], c0 = vp.project([VS.longitude, VS.latitude, 0]);
  const l0 = VS.longitude * D2R, p0 = VS.latitude * D2R, c = vec(VS.longitude, VS.latitude), f = [-c[0], -c[1], -c[2]];
  const r = [Math.cos(l0), Math.sin(l0), 0], u = [-Math.sin(p0) * Math.sin(l0), Math.sin(p0) * Math.cos(l0), Math.cos(p0)];
  const ctr = [c0[0], c0[1]]; let R = 0;
  for (const brg of [0, 90, 180, 270]) for (let a = 60; a <= 90; a += 1.5) {
    const ar = a * D2R, br = brg * D2R, lat2 = Math.asin(Math.sin(p0) * Math.cos(ar) + Math.cos(p0) * Math.sin(ar) * Math.cos(br));
    const lon2 = l0 + Math.atan2(Math.sin(br) * Math.sin(ar) * Math.cos(p0), Math.cos(ar) - Math.sin(p0) * Math.sin(lat2));
    const q = vp.project([lon2 / D2R, lat2 / D2R, 0]); R = Math.max(R, Math.hypot(q[0] - c0[0], q[1] - c0[1]));
  }
  const th = skin(), tint = th === "nightvision" ? [170, 255, 180] : th === "thermal" ? [255, 220, 170] : th === "intel" ? [235, 235, 235] : null;
  const pr = (d) => { const sf = dot3(d, f); if (sf <= 0.02) return null; return [w / 2 + F * dot3(d, r) / sf, h / 2 - F * dot3(d, u) / sf]; };
  cx.font = `10px ${getComputedStyle(document.documentElement).getPropertyValue("--f-mono")}`;
  if (STARS_ON) for (const s of STARS) {
    const p = pr(vec(s[0] - GMST, s[1])); if (!p) continue;
    if (p[0] < -5 || p[1] < -5 || p[0] > w + 5 || p[1] > h + 5 || Math.hypot(p[0] - ctr[0], p[1] - ctr[1]) < R) continue;
    const m = s[2], rad = Math.max(0.45, 2.4 - 0.36 * m), al = Math.max(0.25, Math.min(1, 1.25 - m * 0.14));
    const col = tint || (s[3] == null ? [235, 240, 255] : s[3] < 0 ? [190, 210, 255] : s[3] < 0.6 ? [240, 244, 255] : s[3] < 1.2 ? [255, 236, 200] : [255, 205, 160]);
    cx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${al})`; cx.beginPath(); cx.arc(p[0], p[1], rad, 0, 6.2832); cx.fill();
    if (m < 0.9 && gz < 2.6 && s[5]) { cx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},.55)`; cx.fillText(s[5], p[0] + 5, p[1] - 4); }
  }
  const sp = pr(SUN);
  if (sp && Math.hypot(sp[0] - ctr[0], sp[1] - ctr[1]) > R) { const g = cx.createRadialGradient(sp[0], sp[1], 0, sp[0], sp[1], 60); const wc = T.rgb("warn");
    g.addColorStop(0, "rgba(255,255,240,1)"); g.addColorStop(0.08, `rgba(${wc[0]},${wc[1]},${wc[2]},.85)`); g.addColorStop(1, "rgba(0,0,0,0)"); cx.fillStyle = g; cx.beginPath(); cx.arc(sp[0], sp[1], 60, 0, 6.2832); cx.fill(); }
  if (R > 20 && R < Math.hypot(w, h)) { const a = T.rgb(TEX_ON ? "water" : "accent");
    const g = cx.createRadialGradient(ctr[0], ctr[1], R * 0.9, ctr[0], ctr[1], R * 1.16);
    g.addColorStop(0, `rgba(${a[0]},${a[1]},${a[2]},0)`); g.addColorStop(0.36, `rgba(${a[0]},${a[1]},${a[2]},${TEX_ON ? 0.55 : 0.32})`); g.addColorStop(0.55, `rgba(${a[0]},${a[1]},${a[2]},.12)`); g.addColorStop(1, `rgba(${a[0]},${a[1]},${a[2]},0)`);
    cx.fillStyle = g; cx.beginPath(); cx.arc(ctr[0], ctr[1], R * 1.16, 0, 6.2832); cx.fill(); }
}

// ---------- legend
function renderLegend() {
  const ramp = (a, b) => Array.from({ length: 12 }, (_, i) => `<i style="background:rgb(${T.ramp(a + (b - a) * i / 11).slice(0, 3)})"></i>`).join("");
  const bands = Array.from({ length: NBANDS }, (_, i) => `<i style="background:rgb(${bandColor(i)})"></i>`).join("");
  const nd = `<div class="nd"><i style="background:${css(T.rgb("other", 60))}"></i>no data</div>`;
  let h = "";
  if (CM.kind === "e") h = `<div class="lbl lt">Elevation · NOAA ETOPO 2022</div><div class="ramp">${ramp(0.4, 0.98)}</div><div class="ticks"><span>0 m</span><span>1,000</span><span>4,300 m+</span></div>`;
  else if (CM.kind === "c") { const f = v => v == null ? "" : metric.fmt(v);
    h = `<div class="lbl lt">${esc(metric.label)} · county · 8 equal-count bands</div><div class="ramp">${bands}</div><div class="ticks"><span>${esc(f(metric._min))}</span><span>${esc(f(breaks[3]))}</span><span>${esc(f(metric._max))}${esc(metric.unit)}</span></div>${nd}<div class="note">${esc(metric.note)}</div>`; }
  else { const loaded = TR_LOADED.size ? `in view: ${[...TR_LOADED].join(", ")}` : "zoom past 7 over a state to load its tracts";
    if (TRM.flag) h = `<div class="lbl lt">${esc(TRM.label)} · tract</div><div class="nd"><i style="background:rgb(${bandColor(1)})"></i>${esc(TRM.flag[0])}</div><div class="nd"><i style="background:rgb(${bandColor(NBANDS - 2)})"></i>${esc(TRM.flag[1])}</div>${TRM.flag[2] ? `<div class="nd"><i style="background:rgb(${bandColor(NBANDS - 1)})"></i>${esc(TRM.flag[2])}</div>` : ""}`;
    else h = `<div class="lbl lt">${esc(TRM.label)} · tract · 8 equal-count bands</div><div class="ramp">${bands}</div><div class="ticks"><span>${esc(trFmt(TRM, TRM._min))}</span><span>${esc(trFmt(TRM, trBreaks[3]))}</span><span>${esc(trFmt(TRM, TRM._max))}</span></div>`;
    h += `${nd}<div class="note">${esc(TRM.note)} Tracts ${esc(loaded)}.</div>`; }
  if (MM && MT_LOADED.size && MM._min != null && !MM.flag && !MM.cat) h += `<div class="lbl lt" style="margin-top:8px">Buildings · ${esc(MM.label)}</div><div class="ramp">${ramp(0.14, 1)}</div><div class="ticks"><span>${esc(MM.fmt(MM._min))}</span><span>${esc(MM.fmt(MM._mid))}</span><span>${esc(MM.fmt(MM._max))}</span></div>`;
  $("legend").innerHTML = h;
}

// ---------- panel: sections follow the zoom, breadcrumb, credits, stats
function sectionFor() { if (VIEW === "walk" || VS.zoom >= Z_EPT) return "street"; if (VIEW === "globe") return "globe"; return "radar"; }
function updatePanel() {
  const s = sectionFor();
  if (s !== SECTION) { SECTION = s;
    [["globe", "secGlobe"], ["radar", "secRadar"], ["street", "secStreet"]].forEach(([k, id]) => { const d = $(id); d.open = k === s; d.classList.toggle("here", k === s); }); }
  // breadcrumb
  const c = VIEW === "globe" && VS.zoom < 3 ? null : countyAt(VS.longitude, VS.latitude), r = c && COUNTY.get(c);
  const parts = [`<button data-z="earth">Earth</button>`];
  if (r || stateAt(VS.longitude, VS.latitude)) parts.push(`<button data-z="usa">USA</button>`);
  if (r) { parts.push(`<button data-z="state" data-st="${esc(r.STUSPS)}">${esc(r.STATE_NAME || r.STUSPS)}</button>`); if (VS.zoom >= 7) parts.push(`<button data-z="county" data-f="${esc(c)}">${esc(r.NAME)}</button>`); }
  const what = VIEW === "walk" ? "walking" : VIEW === "globe" ? "globe" : VS.zoom >= Z_BLOCKS ? "street" : VS.zoom >= Z_EPT ? "LiDAR" : VS.zoom >= Z_TRACTS ? "tracts" : "counties";
  $("crumb").innerHTML = parts.join(" › ") + ` · <b>${what}</b> · z ${VS.zoom.toFixed(1)}`;
  $("walkBtn").disabled = VIEW === "globe" || (VIEW === "map" && VS.zoom < Z_WALK);
  if (VIEW === "walk") $("walkBtn").textContent = "◆ STOP WALKING (Esc)"; else $("walkBtn").textContent = VS.zoom >= Z_WALK ? "◆ WALK HERE (W) · arrows move, drag looks" : `◆ WALK · zoom past ${Z_WALK} first`;
  updateCredits(); renderStats(); renderBlockList(); updateHint();
}
function updateCredits() {
  const z = VS.zoom, globe = VIEW === "globe", c = ["Dots: NOAA ETOPO 2022 (CC0)"];
  if (globe) { if (SUN_ON) c.push("night lights NASA Black Marble (US public domain)"); if (STARS_ON) c.push("stars IAU Catalog of Star Names (CC BY 4.0)"); if (TEX_ON) c.push("NASA Blue Marble (public domain)"); }
  c.push("states/counties US Census (public domain)");
  if (!globe && z < 12.5) c.push("county radar: NOAA SPC, FEMA NRI, Census ACS, BLS, USGS 3DEP index (public domain)");
  if (TR_LOADED.size && z < 13.2) c.push("tracts: Census ACS, FEMA NRI, FHFA HPI, HUD, CDFI Fund (public domain)");
  if (OZ_ON) c.push("Opportunity Zones: Treasury CDFI Fund (public domain)");
  if (MT_LOADED.size) c.push(esc(METROS.map(m => m.attribution).filter(Boolean)[0] || "© OpenStreetMap contributors, Overture Maps Foundation (ODbL)"));
  if (!globe && z >= Z_EPT) { const ec = EPT && EPT.credits ? safe(() => EPT.credits(), null) : null; c.push(ec ? esc(ec) : "LiDAR: USGS 3DEP (public domain)"); }
  if (!globe && z >= Z_BLOCKS - 1) { const bc = BL && BL.credits ? safe(() => BL.credits(), null) : null; if (bc) c.push(esc(bc)); }
  if (MT && MT_SLUG) { const t = safe(() => MT.credits(), ""); if (t) c.push(esc(t)); }
  for (const m of [EO, PT, SKY, PA, BZ]) if (m && m.credits && earthActive(m)) { const t = safe(() => m.credits(), ""); if (t) c.push(esc(t)); }
  $("credits").innerHTML = c.join(" · ") + ` · <button id="credCat">all sources &amp; licences</button>`;
  $("credCat").onclick = () => openCatalog(true);
}
function renderStats() {
  if (!statsDirty && SECTION !== "street") return; statsDirty = false;
  const s = EPT ? safe(() => EPT.stats(), null) : null, rows = [];
  if (MGR_STUB.ept) rows.push(["LiDAR module", "not loaded (app/ept.js missing)"]);
  if (s && typeof s === "object") for (const [k, v] of Object.entries(s)) if (v != null && typeof v !== "object") rows.push([k.replace(/_/g, " "), typeof v === "number" ? v.toLocaleString() : String(v)]);
  if (MT_LOADED.size) { let n = 0; for (const t of MT_LOADED.values()) n += t.data.n; rows.push(["metro buildings in view", n.toLocaleString() + ` · ${MT_LOADED.size} tiles`]); }
  if (MGR_STUB.block) rows.push(["Block module", "not loaded (app/block.js missing)"]);
  $("eptStats").innerHTML = rows.length ? `<dl>${rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl>` : "";
}
let BLIST_KEY = "";
function renderBlockList() {
  let list = BL ? safe(() => BL.list(), []) : [];
  if (!Array.isArray(list) || !list.length) list = BLOCKS;
  const d = b => Math.hypot(norm180(b.lon - VS.longitude) * Math.cos(VS.latitude * D2R), b.lat - VS.latitude) * 111;
  const near = list.filter(b => b && isFinite(b.lon)).map(b => [b, d(b)]).sort((a, b) => a[1] - b[1]).slice(0, 8);
  const key = near.map(x => x[0].slug + (x[1] < 3 ? "*" : "")).join(",");
  if (key === BLIST_KEY) return; BLIST_KEY = key;
  $("blist").innerHTML = near.map(([b, km]) => `<button data-slug="${esc(b.slug)}">${esc(b.city || b.name || b.slug)}${b.state ? ", " + esc(b.state) : ""} <small>${km < 1 ? "here" : Math.round(km).toLocaleString() + " km"}</small></button>`).join("") || `<span class="note">No blocks listed.</span>`;
}
function updateHint() {
  let h = null;
  if (VIEW === "globe" && VS.zoom < 3) h = "Drag to spin · scroll to zoom into the USA · hover a county";
  else if (VIEW === "map" && VS.zoom < Z_TRACTS) h = "Hover a county for its numbers · click to zoom in and open the dossier";
  else if (VIEW === "map" && VS.zoom < Z_EPT) h = "Tracts load for the states in view · LiDAR streams from zoom 11";
  else if (VIEW === "map" && VS.zoom < Z_BLOCKS) h = `USGS LiDAR streaming${MGR_STUB.ept ? " (module not loaded)" : ""} · right-drag or Ctrl-drag to tilt · <b>W</b> to walk`;
  else if (VIEW === "map") { const b = nearestBlock(1.5); h = b ? `<b>${esc(b.city)}, ${esc(b.state)}</b> block · click a house for its dossier · <b>W</b> to walk` : "Every LiDAR point shown · <b>W</b> to walk at eye height"; }
  const el = $("hint"); if (!h || VIEW === "walk") { el.hidden = true; return; } el.innerHTML = h; el.hidden = false;
}
let statusTO = null;
function setStatus(t) { clearTimeout(statusTO); $("status").textContent = t || "ready"; if (t) statusTO = setTimeout(() => { $("status").textContent = "ready"; }, 6000); }

// ---------- view settled (debounced 150 ms): managers, tracts, metro tiles, URL, panel
let settleTO = null;
function scheduleSettle() { clearTimeout(settleTO); settleTO = setTimeout(settle, 150); }
function mgrViewport() {
  const el = $("deck"), width = el.clientWidth || 1200, height = el.clientHeight || 800;
  if (VIEW === "walk") return groundBounds(new deck.WebMercatorViewport({ width, height, longitude: WALK.lon, latitude: WALK.lat, zoom: 17.5, pitch: 60, bearing: WALK.bearing, position: [0, 0, WALK.z - 1.7] }), WALK.z - 1.7);
  if (VIEW === "map") { const vp = deckgl2.getViewports()[0]; if (vp && vp.getBounds) return groundBounds(vp, LIFT_Z); }
  return new deck.WebMercatorViewport({ width, height, longitude: VS.longitude, latitude: Math.max(-80, Math.min(80, VS.latitude)), zoom: Math.max(0, VS.zoom), pitch: VIEW === "map" ? VS.pitch : 0, bearing: VIEW === "map" ? VS.bearing : 0 });
}
// the camera is lifted to the ground (viewState.position z), so view bounds must be taken on the ground plane, not at z = 0
// (otherwise the box slides kilometres ahead in a pitched view). Same viewport object otherwise (still a WebMercatorViewport).
function groundBounds(vp, z) {
  if (!z) return vp;
  const w = Object.create(vp);
  w.getBounds = (o = {}) => vp.getBounds({ ...o, z });
  return w;
}
function viewBounds(vp) {
  let b = safe(() => vp.getBounds(), null); if (!b) return null;
  const lon = VIEW === "walk" ? WALK.lon : VS.longitude, lat = VIEW === "walk" ? WALK.lat : VS.latitude;
  // pitched views reach the horizon: cap the box around the centre
  const z = VIEW === "walk" ? 17.5 : VS.zoom, cap = 360 / 2 ** z * 3;
  return [Math.max(b[0], lon - cap), Math.max(b[1], lat - cap), Math.min(b[2], lon + cap), Math.min(b[3], lat + cap)];
}
function settle() {
  if (!deckgl2) return;
  const vp = mgrViewport(), b = VIEW === "globe" ? null : viewBounds(vp);
  LAST_BOUNDS = b;
  if (EPT) safe(() => EPT.update(vp)); if (BL) safe(() => BL.update(vp));
  if (PT) safe(() => PT.update(VS, { globe: VIEW === "globe", zoom: VIEW === "walk" ? 20 : VS.zoom, bounds: b }));
  if (BZ) safe(() => BZ.update(VS, { zoom: VIEW === "walk" ? 20 : VS.zoom }));
  if (PA) safe(() => PA.update(VS, { globe: VIEW === "globe", zoom: VIEW === "walk" ? 20 : VS.zoom, bounds: b }));
  tractTick(b); metroTick(b); updatePanel(); scheduleUrl();
}

// ---------- URL state (replaceState)
let urlTO = null, URL_BLOCK = QS.get("block");
function scheduleUrl(block) { if (block !== undefined) URL_BLOCK = block; clearTimeout(urlTO); urlTO = setTimeout(writeUrl, 400); }
function writeUrl() {
  const p = new URLSearchParams(), lon = VIEW === "walk" ? WALK.lon : VS.longitude, lat = VIEW === "walk" ? WALK.lat : VS.latitude;
  p.set("lat", lat.toFixed(5)); p.set("lon", norm180(lon).toFixed(5)); p.set("z", (VIEW === "walk" ? 20 : VS.zoom).toFixed(2));
  if (VIEW === "map" && VS.pitch > 0.5) p.set("pitch", VS.pitch.toFixed(0)); if (VIEW !== "globe" && Math.abs(VS.bearing) > 0.5) p.set("bearing", (VIEW === "walk" ? WALK.bearing : VS.bearing).toFixed(0));
  const nb = nearestBlock(2); if (HOUSE && HOUSE.slug) p.set("block", HOUSE.slug); else if (URL_BLOCK && nb && nb.slug === URL_BLOCK) p.set("block", URL_BLOCK); else if (nb && VS.zoom >= Z_BLOCKS) p.set("block", nb.slug);
  if (HOUSE) p.set("house", HOUSE.id);
  if (selected) p.set("area", "county:" + selected);
  if (VIEW === "walk") p.set("walk", "1");
  if (catalogOpen) p.set("panel", "catalog");
  for (const [k, m] of [["eo", EO], ["pt", PT], ["sky", SKY], ["pa", PA], ["bz", BZ]]) if (earthActive(m)) p.set(k, JSON.stringify(m.getState()));
  if (QS.get("theme")) p.set("theme", T.current().id);
  if (QS.get("raf")) p.set("raf", QS.get("raf"));
  history.replaceState(null, "", "?" + p.toString().replace(/%3A/g, ":"));
}

// ---------- search: local counties/states, "lat, lon", US Census geocoder (public domain) for addresses
let resIdx2 = -1, RES = [];
function localResults(q) {
  const out = [], m = q.trim().match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m) { const a = +m[1], b = +m[2]; if (Math.abs(a) <= 90 && Math.abs(b) <= 180) out.push({ ll: [b, a], label: `${a}, ${b}`, sub: "coordinates" }); }
  for (const x of search(q)) out.push(x);
  if (/\d/.test(q) && /[a-z]{3}/i.test(q) && !m) out.push({ geo: q.trim(), label: `Find address “${q.trim()}”`, sub: "US Census geocoder" });
  return out.slice(0, 16);
}
function showResults() {
  const ul = $("results");
  if (!RES.length) { ul.hidden = true; return; }
  ul.innerHTML = RES.map((x, i) => `<li><button data-i="${i}" class="${i === resIdx2 ? "on" : ""}"><span>${esc(x.label)}</span><small>${esc(x.sub)}</small></button></li>`).join("");
  ul.hidden = false;
}
async function geocode(q) {
  const base = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?benchmark=Public_AR_Current&address=" + encodeURIComponent(q);
  const parse = j => ((j && j.result && j.result.addressMatches) || []).map(a => ({ ll: [a.coordinates.x, a.coordinates.y], label: a.matchedAddress, sub: "address · US Census", addr: true }));
  // the Census geocoder sends no CORS headers, so it is read as JSONP (format=jsonp)
  return new Promise(ok => { const cb = "wmgeo" + Date.now(), s = document.createElement("script"); const done = v => { delete window[cb]; s.remove(); ok(v); };
    window[cb] = j => done(parse(j)); s.onerror = () => done([]); setTimeout(() => window[cb] && done([]), 9000);
    s.src = base + "&format=jsonp&callback=" + cb; document.head.appendChild(s); });
}
async function pickResult2(x) {
  $("results").hidden = true; resIdx2 = -1;
  if (x.geo) { setStatus("asking the US Census geocoder…"); const r = await geocode(x.geo); if (!r.length) { setStatus("no address match (US Census geocoder)"); RES = [{ label: "No address match", sub: "try street, city, state" }]; showResults(); return; }
    if (r.length === 1) return pickResult2(r[0]); RES = r; showResults(); return; }
  $("q").value = x.label;
  if (x.ll) { const c = countyAt(x.ll[0], x.ll[1]); if (c) { selected = c; pickedTract = null; HOUSE = null; renderDossier(); recolorDirty = true; }
    flyTo({ longitude: x.ll[0], latitude: x.ll[1], zoom: x.addr ? 17.5 : 14, pitch: x.addr ? 50 : 0, bearing: 0 }); scheduleUrl(); return; }
  if (x.state) { if (STATE_BBOX.has(x.state)) flyTo(fitView(STATE_BBOX.get(x.state), 7.5)); return; }
  if (x.fips) select(x.fips, true);
}

// ---------- sources & licences (catalog.js data, same filters as the old catalog page)
const CF = { s: "all", q: "" };
function drawCatalog() {
  const C = window.CATALOG; if (!C) { $("catBody").innerHTML = `<p class="note">catalog.js did not load.</p>`; return; }
  const { D, commercial, audit, LIVE: LV } = C; let shown = 0;
  const lbl = { in: "in use", ready: "ready to add", key: "account/key", no: "not usable" };
  $("catBody").innerHTML = D.map(([title, rows]) => {
    const r = rows.filter(([n, w, g, u, k, l, st]) => (CF.s === "all" || (CF.s === "live" ? LV.test(u) : CF.s === "comm" ? commercial(l, st, n)[0] === "ok" : CF.s === "no" ? commercial(l, st, n)[0] !== "ok" : st === CF.s && commercial(l, st, n)[0] === "ok")) &&
      (!CF.q || [n, w, g, u, k, l].join(" ").toLowerCase().includes(CF.q)));
    shown += r.length; if (!r.length) return "";
    return `<h4>${title}</h4><table><tbody>` + r.map(([n, w, g, u, k, l, st, url, grp], i) => {
      const [c, t] = commercial(l, st, n), a = audit(n);
      return (grp && grp !== (r[i - 1] || [])[8] ? `<tr class="grp"><td colspan="3">${grp}</td></tr>` : "") +
        `<tr><td class="src">${url ? `<a href="${url.replace(/"/g, "&quot;")}" target="_blank" rel="noopener">${n}</a>` : n}<small>${[g, u, k && k !== "no" ? "key: " + k : ""].filter(Boolean).join(" · ")}</small></td><td>${w}</td><td class="lic">${l}<br><span class="cm ${c}"${a ? ` title="${a[3].replace(/"/g, "&quot;")}"` : ""}>${t}</span> ${st !== "no" && c !== "ok" ? `<span class="st no" title="was: ${lbl[st]}">excluded</span>` : `<span class="st ${st}">${lbl[st]}</span>`}</td></tr>`; }).join("") + `</tbody></table>`;
  }).join("");
  $("catCount").textContent = `${shown} sources`;
}
function catalogTotals() {
  const C = window.CATALOG; if (!C) return;
  const all = C.D.flatMap(([, rows]) => rows), v = all.map(([n, , , , , l, st]) => C.commercial(l, st, n)[0]), g = all.filter((r, i) => v[i] === "ok");
  $("catTotals").innerHTML = `<b>${all.length}</b> sources in <b>${C.D.length}</b> sections · <b>${g.length}</b> green · <b>${v.filter(x => x === "verify").length}</b> excluded (terms not explicit) · <b>${v.filter(x => x === "no").length}</b> not allowed · <b>${C.REMOVED.length}</b> removed by the licence audit · of the green: <b>${g.filter(r => r[6] === "in").length}</b> in use`;
}
let catDrawn = false;
function openCatalog(on) {
  catalogOpen = on; $("catalog").hidden = !on;
  if (on && !catDrawn) { catDrawn = true; catalogTotals(); drawCatalog(); }
  scheduleUrl();
}

// ---------- frame loop: only fly / spin / walk / lift smoothing / dirty flags; deck redraws on its own
let lastFrame = 0, lastSlow = 0;
function frame(now) {
  const dt = Math.min(64, now - (lastFrame || now)); lastFrame = now;
  if (VIEW === "walk") { if (KEYS.size || Math.abs(groundAt(WALK.lon, WALK.lat) + 1.7 - WALK.z) > 0.02) walkStep(dt); }
  else if (FLY) { flyStep(now); applyVS(); }
  else if (GVEL && VIEW === "globe" && !GDRAG) { const f = Math.exp(-dt / 350); GVEL = { lon: GVEL.lon * f, lat: GVEL.lat * f };
    VS = { ...VS, longitude: norm180(VS.longitude + GVEL.lon * dt), latitude: Math.max(-85, Math.min(85, VS.latitude + GVEL.lat * dt)) }; applyVS();
    if (Math.abs(GVEL.lon) + Math.abs(GVEL.lat) < 2e-4) GVEL = null; }
  if (GZ_TARGET != null && VIEW === "globe") { const d = GZ_TARGET - VS.zoom; if (Math.abs(d) < 0.005) { VS = { ...VS, zoom: GZ_TARGET }; GZ_TARGET = null; } else VS = { ...VS, zoom: VS.zoom + d * Math.min(1, dt / 110) }; applyVS(); if (VIEW !== "globe") GZ_TARGET = null; }
  else if (SPIN && VIEW === "globe" && !dragging) { VS = { ...VS, longitude: norm180(VS.longitude + dt * 0.006) }; applyVS(); }
  else if (VIEW === "map" && Math.abs(LIFT_T - LIFT_Z) > 0.3) { LIFT_Z += (LIFT_T - LIFT_Z) * Math.min(1, dt / 200); deckgl2.setProps({ viewState: deckViewState() }); const k = fadeKey(); if (k !== layerKey) { layerKey = k; layersDirty = true; } }
  if (now - lastSlow > 1000) { lastSlow = now;
    if (LIVE && Date.now() - TIME.getTime() > 60000) setTime(new Date());
    const fps = deckgl2 && deckgl2.metrics ? deckgl2.metrics.fps : 0; $("fps").textContent = fps ? Math.round(fps) + " fps" : ""; }
  if (recolorDirty) { recolorDirty = false; recolor(); }
  if (layersDirty) { layersDirty = false; deckgl2.setProps({ layers: buildLayers() }); }
  if (earthDirty > 0) { earthDirty--; deckgl2.redraw("earth"); }
  if (skyDirty) { skyDirty = false; drawSky(); }
  requestAnimationFrame(frame);
}

// ---------- boot
async function main() {
  const need = u => fetch(u).then(r => { if (!r.ok) throw new Error(u + " " + r.status); return u.endsWith(".bin") ? r.arrayBuffer() : r.json(); });
  const [tbl, meta, st] = await Promise.all([U + "data/counties.json", U + "data/earth/dots_meta.json", U + "data/states.geojson"].map(need));
  META = meta;
  $("boot").textContent = `SAMPLING THE PLANET · ${(meta.world.count + meta.usa.count).toLocaleString()} DOTS…`;
  const [wbuf, ubuf] = await Promise.all([U + "data/earth/" + meta.world.file, U + "data/earth/" + meta.usa.file].map(need));
  ROWS = tbl.rows.map(a => Object.fromEntries(tbl.cols.map((c, i) => [c, a[i]])));
  ROWS.forEach(r => { COUNTY.set(r.fips, r); if (r.STATE_NAME) STATE_NAMES.set(r.STUSPS, r.STATE_NAME); });
  WD = decodeDots(wbuf, meta.world); UD = decodeDots(ubuf, meta.usa);
  const byC = new Map();
  for (let i = 0; i < UD.n; i++) { const c = UD.county[i]; let a = byC.get(c); if (!a) byC.set(c, a = { x: [], y: [], e: 0 }); a.x.push(UD.lng[i]); a.y.push(UD.lt[i]); a.e += UD.elev[i]; }
  CO_EL = new Float32Array(META.counties.length);
  const byS = new Map();
  for (const [ci, a] of byC) { const f = META.counties[ci], r = COUNTY.get(f); if (ci < CO_EL.length) CO_EL[ci] = a.e / a.x.length; CO_BBOX.set(f, wrapBox(a.x, a.y));
    if (r) { let s = byS.get(r.STUSPS); if (!s) byS.set(r.STUSPS, s = { x: [], y: [] }); for (let i = 0; i < a.x.length; i += 3) { s.x.push(a.x[i]); s.y.push(a.y[i]); } } }
  for (const [s, a] of byS) STATE_BBOX.set(s, wrapBox(a.x, a.y));
  EPTI = []; for (let i = 0; i < UD.n; i++) if (UD.flags[i] & 1) EPTI.push(i); EPTI = Uint32Array.from(EPTI);
  st.features.forEach(f => { const g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates; polys.forEach(p => p.forEach(ring => STATE_PATHS.push(ring))); });
  for (let lon = -180; lon < 180; lon += 15) { const p = []; for (let lat = -88; lat <= 88; lat += 2) p.push([lon, lat]); GRATICULE.push(p); }
  for (let lat = -75; lat <= 75; lat += 15) { const p = []; for (let lon = -180; lon <= 180; lon += 2) p.push([lon, lat]); GRATICULE.push(p); }

  // metric picker: elevation, county metrics, tract metrics
  const sel = $("metric");
  const fillMetrics = () => { const v = sel.value || "c0"; sel.innerHTML = `<option value="e">Elevation · NOAA ETOPO 2022</option><optgroup label="County">${METRICS.map((m, i) => `<option value="c${i}">${esc(m.label)}</option>`).join("")}</optgroup><optgroup label="Census tract (loads per state in view)">${TR_METRICS.map((m, i) => `<option value="t${i}">${esc(m.label)}</option>`).join("")}</optgroup>`; sel.value = v; };
  fillMetrics();
  sel.onchange = () => { const v = sel.value;
    if (v === "e") CM = { kind: "e" };
    else if (v[0] === "c") { CM = { kind: "c" }; metric = METRICS[+v.slice(1)]; computeBreaks(); }
    else { CM = { kind: "t" }; TRM = TR_METRICS[+v.slice(1)]; computeTrBreaks(); settle(); }
    renderLegend(); if (selected && !HOUSE) renderDossier(); recolorDirty = true; layersDirty = true; };
  computeBreaks();

  astro(TIME); setTime(new Date());
  $("now").onclick = () => { LIVE = true; setTime(new Date()); };
  const fromInputs = () => { const [y, mo, d] = ($("day").value || TIME.toISOString().slice(0, 10)).split("-").map(Number), m = +$("tod").value; LIVE = false; setTime(new Date(Date.UTC(y, mo - 1, d, Math.floor(m / 60), m % 60)), "slider"); };
  $("tod").oninput = fromInputs; $("day").onchange = fromInputs;
  [["reliefOn", true], ["sunOn", true], ["eptOn", true], ["statesOn", true], ["ozOn", false], ["texOn", false], ["spinOn", false], ["pinsOn", true], ["starsOn", true], ["tractsOn", true], ["bldgOn", true], ["clipOn", false]].forEach(([id, v]) => { $(id).checked = v; });
  const tog = (id, fn) => { $(id).onchange = e => { fn(e.target.checked); updateCredits(); }; };
  tog("reliefOn", v => { RELIEF = v; checkLift(true); });
  tog("sunOn", v => { SUN_ON = v; recolorDirty = true; if (TEX_ON) compose(); });
  tog("eptOn", v => { EPT_ON = v; layersDirty = true; });
  tog("statesOn", v => { STATES_ON = v; layersDirty = true; });
  tog("spinOn", v => { SPIN = v; });
  tog("pinsOn", v => { PINS_ON = v; layersDirty = true; });
  tog("starsOn", v => { STARS_ON = v; skyDirty = true; });
  tog("tractsOn", v => { TRACTS_ON = v; settle(); layersDirty = true; });
  tog("bldgOn", v => { BLDG_ON = v; settle(); layersDirty = true; });
  tog("clipOn", v => { CLIP_ON = v; applyClip(); });
  tog("texOn", async v => { TEX_ON = v; if (v && !DAY) { setStatus("loading NASA Blue Marble + Black Marble…"); await loadTex(); setStatus("NASA Blue Marble (July 2004) by day · Black Marble (2016) by night"); } if (v) compose(); skyDirty = true; layersDirty = true; });
  tog("ozOn", async v => { OZ_ON = v;
    if (v && !OZ) { setStatus("loading 15,340 Opportunity Zone tracts…");
      const g = await getJSON(U + "data/oz_tracts.geojson");
      if (g) { OZ = { f18: { type: "FeatureCollection", features: g.features.filter(f => f.properties.r === "2018") }, f27: { type: "FeatureCollection", features: g.features.filter(f => f.properties.r === "2027") } };
        setStatus(`${OZ.f18.features.length.toLocaleString()} zones (2018) · ${OZ.f27.features.length.toLocaleString()} zones (2027) · Treasury CDFI Fund`); } }
    layersDirty = true; });

  const sj = await getJSON(U + "data/stars.json");
  if (sj && Array.isArray(sj.rows)) { const fi = n => sj.fields.indexOf(n); STARS = sj.rows.map(r => [r[fi("ra")], r[fi("dec")], r[fi("mag")], r[fi("bv")], r[fi("hip")], r[fi("name")]]); }

  // initial view from the URL
  const lat = qn("lat"), lon = qn("lon"), z = qn("z");
  if (lat != null && lon != null) VS = { longitude: norm180(lon), latitude: Math.max(-85, Math.min(85, lat)), zoom: z ?? 12, pitch: qn("pitch") ?? 0, bearing: qn("bearing") ?? 0 };
  else VS = { longitude: -100, latitude: 44, zoom: 2.2, pitch: 0, bearing: 0 };
  VIEW = VS.zoom >= Z_TO_MAP ? "map" : "globe"; if (VIEW === "globe") VS.pitch = VS.bearing = 0;
  VS.pitch = Math.min(VS.pitch, maxPitchFor(VS.zoom));
  recolor(); checkLift(true); renderLegend();

  deckgl2 = deckgl = new deck.Deck({
    parent: $("deck"), views: VIEW === "map" ? MAP_VIEW : GLOBE_VIEW, pickingRadius: 6, viewState: deckViewState(),
    controller: VIEW === "map" ? { dragRotate: true, touchRotate: true, doubleClickZoom: true, inertia: 300 } : GLOBE_CTRL,
    useDevicePixels: Math.min(1.5, devicePixelRatio || 1),
    onViewStateChange: ({ viewState, interactionState }) => {
      const user = interactionState && (interactionState.isDragging || interactionState.isPanning || interactionState.isZooming || interactionState.isRotating);
      if (!user && performance.now() - SWITCH_AT < 800) return;   // a new view/controller echoes stale state right after a switch
      if (user) FLY = null;
      dragging = !!(interactionState && interactionState.isDragging);
      if (VIEW === "walk") { if (!user) return; WALK.bearing = viewState.bearing; WALK.pitch = Math.max(30, Math.min(85, viewState.pitch)); deckgl2.setProps({ viewState: deckViewState() }); skyDirty = true; scheduleSettle(); return; }
      if (VIEW === "globe") VS = { longitude: viewState.longitude, latitude: viewState.latitude, zoom: viewState.zoom + zoff(viewState.latitude), pitch: 0, bearing: 0 };
      else VS = { longitude: viewState.longitude, latitude: viewState.latitude, zoom: viewState.zoom, pitch: viewState.pitch || 0, bearing: viewState.bearing || 0 };
      applyVS();
    },
    layers: [], getTooltip: tooltip, onClick,
    getCursor: ({ isHovering, isDragging }) => isHovering ? "pointer" : isDragging ? "grabbing" : "grab",
  });
  $("boot").hidden = true;
  setStatus(`${(META.world.count + META.usa.count).toLocaleString()} real elevation dots · hover a US county`);
  requestAnimationFrame(frame);
  wireUi();
  applyVS();
  if (QS.get("panel") === "catalog") openCatalog(true);

  // lazy: county outlines (choropleth), optional county inputs, tract index, block pins, metros, other lanes' managers
  const pGeo = getJSON(U + "data/counties.geojson").then(g => { if (g) { GEO = g; g.features.forEach(f => FEAT.set(f.properties.f, f)); layersDirty = true; } });
  getJSON(U + "data/oz_counties.json").then(o => { OZ_C = o; if (o) ROWS.forEach(r => { const zz = o.counties[r.fips]; r.oz18_share = zz?.r2018 ? zz.r2018.share : 0; r.oz18_n = zz?.r2018?.n || 0; r.oz27_share = zz?.r2027 ? zz.r2027.share : 0; r.oz27_n = zz?.r2027?.n || 0; r.oz27_rural = zz?.r2027?.rural || 0; }); });
  getJSON(U + "data/tracts/index.json").then(t => { TR_INDEX = t; settle(); });
  const pBlocks = getJSON(U + "blocks/index.json").then(arr => {
    if (!Array.isArray(arr)) return;
    BLOCKS = arr.filter(b => b && b.slug && isFinite(b.lon) && isFinite(b.lat));
    BLOCKS.forEach(b => { b.fips = String(b.fips || "").padStart(5, "0"); BLOCK_BY_FIPS.set(b.fips, b); b._el = etopoAt(b.lon, b.lat); });
    layersDirty = true; BLIST_KEY = ""; renderBlockList();
  });
  loadManagers();
  [STR, AIR, ECON, EVT, JOBS, ROAD, VIS, WX, CLEAN, EARTH] = await Promise.all(["str_score", "tourism_air", "tourism_econ", "tourism_events", "jobs_flow", "road_traffic", "visitors", "weather_triggers", "cleaning_market", "earth_county"].map(n => getJSON(U + "data/" + n + ".json")));
  mergeOptional(); fillMetrics();
  await Promise.all([pGeo, pBlocks]);
  // honour area / block / house from the URL
  const area = QS.get("area");
  if (area && area.startsWith("county:") && COUNTY.has(area.slice(7))) select(area.slice(7), lat == null);
  const bs = QS.get("block"), bb = bs && BLOCKS.find(b => b.slug === bs);
  if (bb && lat == null) openBlock(bb);
  if (QS.get("house") && bs) { PENDING_HOUSE = { id: QS.get("house"), slug: bs }; onMgrUpdate({ type: "url" }); }
  if (QS.get("walk") === "1" && VIEW === "map") setTimeout(enterWalk, 600);
  await loadMetroList(); settle();
}

function wireGlobeInput() {
  // drag anywhere (on the sphere or in space beside it) spins the globe; moving the pointer one globe-diameter turns it 180°
  const el = $("deck");
  el.addEventListener("pointerdown", e => { if (VIEW !== "globe" || e.button !== 0 || !e.isPrimary) return;
    GDRAG = { x: e.clientX, y: e.clientY, t: performance.now(), moved: false, id: e.pointerId }; GVEL = null; FLY = null; }, true);
  addEventListener("pointermove", e => { if (!GDRAG || e.pointerId !== GDRAG.id) return;
    if (VIEW !== "globe") { GDRAG = null; return; }
    const dx = e.clientX - GDRAG.x, dy = e.clientY - GDRAG.y;
    if (!GDRAG.moved && Math.hypot(dx, dy) < 3) return;
    if (!GDRAG.moved) { GDRAG.moved = true; dragging = true; try { el.setPointerCapture(e.pointerId); } catch {} }
    const now = performance.now(), dt = Math.max(1, now - GDRAG.t), D = 256 * 2 ** VS.zoom / Math.PI, k = 180 / Math.max(120, D);
    const dLon = -dx * k / Math.max(0.25, Math.cos(VS.latitude * Math.PI / 180)) * 0.6 - dx * k * 0.4, dLat = dy * k;
    VS = { ...VS, longitude: norm180(VS.longitude + dLon), latitude: Math.max(-85, Math.min(85, VS.latitude + dLat)) };
    const cap = v => Math.max(-0.06, Math.min(0.06, v)), w = Math.max(16, dt), pv = GVEL || { lon: 0, lat: 0 };   // deg/ms, smoothed and capped
    GVEL = { lon: cap(0.5 * pv.lon + 0.5 * dLon / w), lat: cap(0.5 * pv.lat + 0.5 * dLat / w) }; GDRAG.x = e.clientX; GDRAG.y = e.clientY; GDRAG.t = now; applyVS(); });
  const up = e => { if (!GDRAG || (e.pointerId != null && e.pointerId !== GDRAG.id)) return;
    const moved = GDRAG.moved, idle = performance.now() - GDRAG.t; GDRAG = null; dragging = false;
    if (!moved || idle > 50) GVEL = null; };
  addEventListener("pointerup", up); addEventListener("pointercancel", up);
}
function wireGlobeWheel() {
  $("deck").addEventListener("wheel", e => {
    if (VIEW !== "globe") { GZ_TARGET = null; return; }
    e.preventDefault(); e.stopPropagation(); FLY = null;
    const px = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    const base = GZ_TARGET != null ? GZ_TARGET : VS.zoom;
    GZ_TARGET = Math.max(globeMinZoom(), Math.min(Z_TO_MAP + 0.4, base - Math.max(-240, Math.min(240, px)) * 0.0035));
  }, { passive: false, capture: true });
  addEventListener("resize", () => { if (VIEW === "globe" && VS.zoom < globeMinZoom()) { VS = { ...VS, zoom: globeMinZoom() }; applyVS(); } });
}
function wireUi() {
  wireGlobeWheel(); wireGlobeInput();
  const q = $("q");
  q.oninput = () => { resIdx2 = -1; RES = localResults(q.value); showResults(); };
  q.onkeydown = e => {
    if (e.key === "ArrowDown") { resIdx2 = Math.min(RES.length - 1, resIdx2 + 1); showResults(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { resIdx2 = Math.max(0, resIdx2 - 1); showResults(); e.preventDefault(); }
    else if (e.key === "Enter") { RES = RES.length ? RES : localResults(q.value); const x = RES[Math.max(0, resIdx2)]; if (x) pickResult2(x); else if (q.value.trim()) pickResult2({ geo: q.value.trim() }); }
    else if (e.key === "Escape") $("results").hidden = true; };
  $("results").onclick = e => { const b = e.target.closest("button[data-i]"); if (b && RES[+b.dataset.i]) pickResult2(RES[+b.dataset.i]); };
  document.addEventListener("click", e => { if (!e.target.closest(".srch")) $("results").hidden = true; });
  $("collapse").onclick = () => { $("side").hidden = true; $("reopen").hidden = false; };
  $("reopen").onclick = () => { $("side").hidden = false; $("reopen").hidden = true; };
  $("crumb").onclick = e => { const b = e.target.closest("button[data-z]"); if (!b) return; const k = b.dataset.z;
    if (k === "earth") flyTo({ longitude: VS.longitude, latitude: 30, zoom: 1.6 });
    else if (k === "usa") flyTo({ ...fitView([-125, 24.5, -66.9, 49.4], 4.6) });
    else if (k === "state" && STATE_BBOX.has(b.dataset.st)) flyTo(fitView(STATE_BBOX.get(b.dataset.st), 7.5));
    else if (k === "county") select(b.dataset.f, true); };
  $("walkBtn").onclick = () => { if (VIEW === "walk") exitWalk(); else enterWalk(); };
  $("blist").onclick = e => { const b = e.target.closest("button[data-slug]"); if (!b) return; const x = BLOCKS.find(k => k.slug === b.dataset.slug) || (safe(() => BL.list(), []) || []).find(k => k.slug === b.dataset.slug); if (x) openBlock(x); };
  $("catOpen").onclick = () => openCatalog(true);
  $("catX").onclick = () => openCatalog(false);
  $("catFilters").addEventListener("click", e => { const b = e.target.closest("button[data-f]"); if (!b) return; CF.s = b.dataset.f; document.querySelectorAll("#catFilters button").forEach(x => x.setAttribute("aria-pressed", x === b)); drawCatalog(); });
  let cq = null; $("catQ").addEventListener("input", e => { clearTimeout(cq); cq = setTimeout(() => { CF.q = e.target.value.trim().toLowerCase(); drawCatalog(); }, 150); });
  $("card").addEventListener("click", e => { const x = e.target.closest(".redact"); if (x) { x.classList.remove("redact"); x.removeAttribute("title"); }
    const bb = e.target.closest("button[data-block]"); if (bb) { const k = BLOCKS.find(q => q.slug === bb.dataset.block); if (k) openBlock(k); } });
  addEventListener("resize", () => { skyDirty = true; scheduleSettle(); });
}

// ---------- themes: recolour in place
T.mountPicker($("themePick"));
T.on(() => {
  const r = document.documentElement.style;
  r.setProperty("--sel", css(T.rgb("select"))); r.setProperty("--pin", css(T.rgb("pin")));
  if (!WD) return;
  LUT_TH = null; MVALS = null; renderLegend(); if (selected || HOUSE) renderDossier(); recolorDirty = true; skyDirty = true; layersDirty = true; recolorMetro(); if (TEX_ON) compose(); if (QS.get("theme")) scheduleUrl();
});
main().catch(e => { $("boot").textContent = "DATA LOAD FAILED · " + e.message; console.error(e); });

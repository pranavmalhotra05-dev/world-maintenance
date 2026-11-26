/* block.js — the per-house 3D blocks (web/usa/blocks/<slug>/) streamed into the shared globe/map, in place.
   Port of web/usa/view.html (which stays as the stand-alone viewer). ES module; needs the deck.gl 9.1.x UMD build on
   globalThis.deck (or pass {deck} to the constructor). Optional: window.Themes (usa/theme.js) for theme colours.

   import { BlockManager } from "./block.js";
   const bm = new BlockManager({ onUpdate, onSelectHouse, groundAt });
   deck.setProps({ onViewStateChange: ({viewState}) => bm.update(viewState), layers: [...yours, ...bm.layers()],
                   onClick: (info) => bm.pick(info) });

   API
     constructor({ onUpdate, onSelectHouse, groundAt, deck, blocksBase, usaBase, zMode, maxLoaded, minZoom, options })
       onUpdate(evt)        evt.type: "index" | "loading" | "load" | "unload" | "select" | "panel" | "photos" | "frame" | "options" | "error"
                            -> re-read layers() on every call; re-read panelHtml() unless type === "frame" (frame = animation tick
                               of the selection glow, ~60/s while a house is selected).
       onSelectHouse(sel)   sel = {slug, houseId, house} or null
       groundAt(lon, lat)   optional external ground height (absolute metres, e.g. from the streamed USGS EPT lane);
                            only used by zMode "aligned" and by elevationCheck().
     update(viewport)       deck Viewport or a viewState {longitude, latitude, zoom, width, height, pitch, bearing}.
                            zoom >= minZoom (14): blocks whose box is in view are loaded (max maxLoaded = 3, LRU unload).
                            zoom < minZoom: every block is unloaded, in-flight requests aborted, nothing is requested.
     layers()               deck Layer[] for a MapView (METER_OFFSETS, coordinateOrigin = [meta.origin lon, lat, zOffset]).
     pick(info)             deck pick info -> {slug, houseId} (and selects that house) | {slug, photo} | null
     select(slug, houseId) / clear()
     panelHtml()            HTML of the selected house dossier, or the focused block's summary when nothing is selected ("" if none).
                            Wrap it in an element with class "bm-panel" (block-panel.css). bindPanel(el) wires its buttons.
     bindPanel(el)          delegated clicks: [data-bm-close] -> clear(), [data-bm-house="slug|id"] -> select(...)
     groundAt(lon, lat)     block LiDAR ground (terrain mesh, else 2 m ground-return grid) in the SAME vertical frame as
                            layers() (see zMode), or null outside every loaded block.
     list()                 [{slug, name, lon, lat, houses, city, state, state_: "idle"|"loading"|"ready"|"error"}]
     flyTarget(slug)        {longitude, latitude, zoom, pitch, bearing}
     houseFlyTarget(slug, houseId)  {longitude, latitude, zoom, pitch, bearing} over one house
     setOptions(o) / options  layer switches (see DEFAULT_OPTIONS)
     verticalRef(slug)      {zMode, zBase, zOffset, datum, note}
     elevationCheck(slug)   compares block ground with the external groundAt at the block centre: {block, external, delta}

   VERTICAL REFERENCE (zMode)
     Every block file stores z as metres above meta.z_base (builder: z_base = 1st percentile of the block's LiDAR ground
     returns, taken from the same USGS 3DEP EPT the EPT lane streams; USGS EPT z = metres in the source project's vertical
     datum, normally NAVD88 orthometric). Local x/y = Web-Mercator metres / k (true ground metres east/north of meta.origin),
     which is exactly what deck's METER_OFFSETS expects.
       "absolute" (default): zOffset = z_base, so block geometry sits at true LiDAR elevation and lines up with raw EPT points.
       "relative": zOffset = 0 (block ground near z = 0; use with a flat basemap without terrain).
       "aligned":  zOffset = external groundAt(origin) - block ground at origin (falls back to absolute without groundAt).
*/
const D = () => globalThis.deck;
const MODULE_URL = import.meta.url;

// ---------------------------------------------------------------- small helpers (ported from view.html)
const fmt = new Intl.NumberFormat("en-US");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const isObj = (o) => o && typeof o === "object" && !Array.isArray(o);
const has = (o) => o && typeof o === "object" && Object.keys(o).length > 0;
const pick = (...v) => v.find((x) => x != null && x !== "");
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const RAD = Math.PI / 180, R_EARTH = 6378137;
const COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"], cmp = (a) => COMPASS8[Math.round(((a % 360) + 360) % 360 / 45) % 8];
const b64 = (s, T) => { const bin = Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); return new T(bin.buffer); };
const money0 = (v) => (v == null ? "–" : "$" + fmt.format(Math.round(v)));
const Rd = (s) => s;   // view.html's intel-skin redaction wrapper; the globe has no redacting skin
const dash = (v, suf = "") => (v == null || v === "" ? "–" : esc(v) + suf);
const centroid = (ring) => { let x = 0, y = 0; ring.forEach((p) => { x += p[0]; y += p[1]; }); return [x / ring.length, y / ring.length]; };
const ownLevel = (m) => !m?.value_level || /^this house/i.test(String(m.value_level));
const ownValue = (h) => { const m = h?.money; return m && num(m.est_value_now) && ownLevel(m) ? m.est_value_now : null; };
const medianOnly = (h) => { const m = h?.money; return m && !ownLevel(m) ? num(m.est_value_now) ?? num(m.neighbourhood_median) : null; };
const mercY = (lat) => R_EARTH * Math.log(Math.tan(Math.PI / 4 + (lat * RAD) / 2));
const invMercY = (y) => (2 * Math.atan(Math.exp(y / R_EARTH)) - Math.PI / 2) / RAD;

// ---------------------------------------------------------------- colours: theme.js when present, else its Default HUD palette
const HUD = { ground: "#4a5a6e", building: "#ffb84d", tree: "#5fd38a", other: "#9aa8bb", water: "#2f7fd0", road: "#3a4a60", roof: "#ffcf80",
  roofEdge: "#ffe2b0", select: "#ffd24d", pin: "#4fdcff", accent: "#4fdcff", warn: "#ffb84d", danger: "#ff5a5a", ok: "#6cdc8c", grid: "#1d3550", sky: "#05070b", magenta: "#ff3fb4" };
const HUD_RAMP = ["#071a33", "#0c3a66", "#126c99", "#1aa3c4", "#4fdcff", "#ffd27a", "#ffb84d", "#ffffff"];
const hex = (h) => { h = h.replace("#", ""); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; };
const TH = () => globalThis.Themes?.current?.().id || "hud";
const c3 = (n) => { const T = globalThis.Themes; if (T?.rgb && n !== "magenta") { try { return T.rgb(n).slice(0, 3); } catch {} } return hex(HUD[n] || "#ffffff"); };
const ramp = (t) => { const T = globalThis.Themes; if (T?.ramp) { try { return T.ramp(t).slice(0, 3); } catch {} }
  const s = HUD_RAMP.map(hex), x = clamp01(t) * (s.length - 1), i = Math.min(s.length - 2, Math.floor(x)), f = x - i; return [0, 1, 2].map((k) => Math.round(s[i][k] + (s[i + 1][k] - s[i][k]) * f)); };
const mix = (a, b, f) => [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * f));
const lift = (c, f) => mix(c, [255, 255, 255], f);
const shade = (c, f) => [0, 1, 2].map((i) => Math.round(c[i] * f));
const A_ = (c, a) => [c[0], c[1], c[2], a];
const rgbCss = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
function themeColors() {
  const ok = c3("ok"), warn = c3("warn"), danger = c3("danger"), accent = c3("accent"), road = c3("road"), tree = c3("tree"), other = c3("other"), w = c3("water");
  const water = w[2] > w[0] + 20 && w[2] >= w[1] ? lift(w, 0.25) : mix([70, 150, 255], accent, 0.35), mag = c3("magenta");
  return { ok, warn, danger, accent, road, tree, other, water, mag, sel: c3("select"), pin: c3("pin"), edge: c3("roofEdge"), sky: c3("sky"),
    alley: lift(road, 0.55), curb: lift(road, 0.85), sewer: mix(other, warn, 0.45), mid: mix(ok, warn, 0.6), power: lift(mag, 0.35), white: lift(c3("roofEdge"), 0.5) };
}
function devPal() {
  const w = c3("water"), lo = w[2] > w[0] + 20 && w[2] >= w[1] ? lift(w, 0.15) : [60, 110, 255], mid = lift(c3("roofEdge"), 0.45);
  let hi = c3("danger"); if (Math.abs(hi[0] - mid[0]) + Math.abs(hi[1] - mid[1]) + Math.abs(hi[2] - mid[2]) < 120) hi = c3("warn");
  return { lo, mid, hi };
}
function pointPal() {
  const g = c3("ground"), b = c3("building"), r = c3("roof"), t = c3("tree"), o = c3("other");
  return { g0: shade(g, 0.55), g1: lift(g, 0.75), b0: shade(b, 0.92), b1: lift(r, 0.2), t0: shade(t, 0.6), t1: lift(t, 0.15), o, dev: devPal(), devGround: shade(g, 0.45) };
}
const devColor = (m, d) => { const t = Math.max(-1, Math.min(1, m / 0.25)); return t < 0 ? mix(d.mid, d.lo, -t) : mix(d.mid, d.hi, t); };
function glowPal(K) {
  const th = TH();
  if (th === "nightvision") return { house: lift(K.accent, 0.15), parcel: shade(K.accent, 0.85), mesh: K.accent, pts: K.accent };
  if (th === "neon80s") return { house: K.mag, parcel: K.accent, mesh: lift(K.mag, 0.15), pts: lift(K.mag, 0.1) };
  if (th === "thermal") return { house: lift(K.warn, 0.35), parcel: K.warn, mesh: lift(K.warn, 0.25), pts: lift(K.warn, 0.4) };
  const close = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) < 90;
  return { house: K.sel, parcel: close(K.accent, K.sel) ? lift(K.accent, 0.4) : K.accent, mesh: K.sel, pts: K.sel };
}
const themeTone = (c, f = 0.6) => { if (TH() === "hud") return c; const l = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255; return mix(c, ramp(0.05 + 0.9 * l), f); };

// ---------------------------------------------------------------- geometry helpers
function inRing(x, y, ring) { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; }
function ringDist(x, y, ring) {
  let best = Infinity;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const ax = ring[j][0], ay = ring[j][1], dx = ring[i][0] - ax, dy = ring[i][1] - ay, L2 = dx * dx + dy * dy;
    const t = L2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2)) : 0, ex = ax + t * dx - x, ey = ay + t * dy - y;
    best = Math.min(best, ex * ex + ey * ey);
  }
  return Math.sqrt(best);
}
const inOrNear = (x, y, ring, tol) => inRing(x, y, ring) || ringDist(x, y, ring) < tol;
const ringBox = (ring, pad) => { const xs = ring.map((p) => p[0]), ys = ring.map((p) => p[1]); return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad]; };
const ringArea = (r) => { let a = 0; for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]); return Math.abs(a / 2); };
const okRing = (r) => Array.isArray(r) && r.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1]) && Math.abs(+p[0]) < 20000 && Math.abs(+p[1]) < 20000).length >= 3;
const cleanRing = (r) => r.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1])).map((p) => [+p[0], +p[1], isFinite(+p[2]) ? +p[2] : 0]);
function earcut2(pts) {
  const n = pts.length, idx = [...Array(n).keys()], out = []; if (n < 3) return out;
  let area = 0; for (let i = 0; i < n; i++) { const a = pts[i], b = pts[(i + 1) % n]; area += a[0] * b[1] - b[0] * a[1]; } if (area < 0) idx.reverse();
  const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const inside = (p, a, b, c) => cross(a, b, p) >= 0 && cross(b, c, p) >= 0 && cross(c, a, p) >= 0;
  let guard = 0;
  while (idx.length > 3 && guard++ < n * n) { let cut = false;
    for (let i = 0; i < idx.length; i++) { const ia = idx[(i + idx.length - 1) % idx.length], ib = idx[i], ic = idx[(i + 1) % idx.length], A = pts[ia], B_ = pts[ib], Cc = pts[ic];
      if (cross(A, B_, Cc) <= 1e-9) continue;
      if (idx.some((j) => j !== ia && j !== ib && j !== ic && inside(pts[j], A, B_, Cc))) continue;
      out.push(ia, ib, ic); idx.splice(i, 1); cut = true; break; }
    if (!cut) break; }
  if (idx.length === 3) out.push(idx[0], idx[1], idx[2]);
  return out;
}
const offsetPath = (p, d) => p.map((q, i) => { const a = p[Math.max(0, i - 1)], b = p[Math.min(p.length - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1; return [q[0] - (dy / l) * d, q[1] + (dx / l) * d, q[2]]; });
const segX = (a, b, c, d) => { const o = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]); const d1 = o(c, d, a), d2 = o(c, d, b), d3 = o(a, b, c), d4 = o(a, b, d); return d1 * d2 < 0 && d3 * d4 < 0; };
const ngon = (n, rad, cz, rot = Math.PI / n) => [...Array(n).keys()].map((i) => [rad * Math.cos(rot + (i * 2 * Math.PI) / n), cz + rad * Math.sin(rot + (i * 2 * Math.PI) / n)]);
function bandI(s, L, t, nb, v = 6.5) {
  let I = 0; const sp = L / nb;
  for (let b = 0; b < nb; b++) { const head = (t * v + b * sp) % L, d = (((head - s) % L) + L) % L; I = Math.max(I, d < L / 2 ? Math.exp(-d / 3.2) : Math.exp(-((L - d) ** 2) / 0.5)); }
  return I;
}

// ---------------------------------------------------------------- pools / cleaning / STR helpers (ported verbatim where possible)
const POOL_KINDS = { blue: "clear blue", turq: "turquoise / cloudy", green: "green (algae)", empty: "empty / drained", none: "no water seen", unk: "no reading" };
function poolKind(s) {
  const t = String(s ?? "").toLowerCase(); if (!t) return "unk";
  return /green|algae|swamp/.test(t) ? "green" : /turq|cloud|teal|murk/.test(t) ? "turq" : /blue|clear|clean/.test(t) ? "blue" : /empty|dry|drain/.test(t) ? "empty" : /none|absent|no.?pool|not.?seen|not.?present|cover/.test(t) ? "none" : "unk";
}
const poolOf = (h) => (h?.pool && typeof h.pool === "object" ? h.pool : null);
const poolPresent = (h) => { const p = poolOf(h); return !!p && (p.present === true || (p.present == null && p.likely === true)); };
const poolYears = (h) => arr(poolOf(h)?.by_year).filter((y) => y && num(+y.year) != null).slice().sort((a, b) => +a.year - +b.year);
const poolLast = (h) => { const p = poolOf(h); if (!p) return "unk"; if (p.last_state) return poolKind(p.last_state); const y = poolYears(h); return y.length ? poolKind(y[y.length - 1].state) : "unk"; };
const poolGreenNow = (h) => poolPresent(h) && poolLast(h) === "green";
function poolPal() { const K = themeColors(); return { blue: K.water, turq: lift(mix(K.water, K.tree, 0.5), 0.15), green: mix(K.tree, K.warn, 0.2), empty: null, none: null, unk: null, alarm: K.warn, house: K.water }; }
const affV = (x) => num(x) ?? num(x?.block_group) ?? num(x?.tract);
const vbadge = (v) => { const s = String(v ?? "").toLowerCase(); if (v == null || s === "") return "";
  const [c, t] = v === true || s === "true" || s === "verified" || s === "yes" ? ["ok", "VERIFIED"] : s === "partial" ? ["warn", "PARTIAL"] : ["bad", "UNVERIFIED"];
  return `<span class="chip ${c} badge">${t}</span>`; };
const kmOf = (o) => num(pick(o?.km, o?.distance_km, o?.dist_km, num(o?.distance_m) != null ? o.distance_m / 1000 : null, num(o?.m) != null ? o.m / 1000 : null));
const km1 = (v) => (num(v) == null ? "–" : (v < 10 ? Math.round(v * 10) / 10 : Math.round(v)) + " km");
const clip = (v, n) => { const t = String(v ?? ""); return t.length > n ? `<span title="${esc(t)}">${esc(t.slice(0, n).replace(/\s+\S*$/, ""))}…</span>` : esc(t); };
const lnk = (u, t) => (u && /^https?:/i.test(String(u)) ? ` <a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>` : "");
const MON = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"], MON3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthVals(p) {
  if (p == null) return null;
  if (Array.isArray(p) && p.length === 12 && p.every((x) => num(x) != null)) return p.map(Number);
  if (Array.isArray(p)) { const set = new Set(p.map((x) => (num(x) != null ? Number(x) - 1 : MON3.findIndex((n) => String(x).toLowerCase().startsWith(n.toLowerCase())))));
    return set.size && !set.has(-1) ? MON3.map((_, i) => (set.has(i) ? 1 : 0.25)) : null; }
  if (typeof p === "object") { const v = MON3.map((n, i) => num(pick(p[n], p[n.toLowerCase()], p[i + 1], p[String(i + 1).padStart(2, "0")]))); return v.some((x) => x != null) ? v.map((x) => x ?? 0) : null; }
  return null;
}
const top3 = (v) => v.slice().sort((a, b) => b - a)[2] ?? 0;
function bars12(v, aria, tip) {
  const mx = Math.max(...v, 1e-9), t3 = top3(v), W = 14, Hh = 30;
  return `<svg viewBox="0 0 ${12 * W} ${Hh + 11}" width="${12 * W}" height="${Hh + 11}" role="img" aria-label="${esc(aria)}" class="bars">${v.map((x, i) => { const bh = Math.max(1.5, (x / mx) * Hh), pk = x >= t3 && x > 0;
    return `<rect x="${i * W + 2}" y="${Hh - bh}" width="${W - 4}" height="${bh}" rx="1" class="${pk ? "pk" : ""}"><title>${MON3[i]}: ${esc(tip ? tip(x, i) : Math.round((x / mx) * 100) + "% of peak")}</title></rect><text x="${i * W + W / 2}" y="${Hh + 9}" text-anchor="middle">${MON[i]}</text>`; }).join("")}</svg>`;
}
const monthSpark = (v) => bars12(v, "Relative demand by month");
const pct0 = (v) => { const n = num(v); if (n == null) return "–"; return Math.round((Math.abs(n) <= 1 ? n * 100 : n) * 10) / 10 + "%"; };
const n1v = (v) => (num(v) == null ? "–" : String(Math.round(v * 10) / 10));
const m2ft = (v) => (num(v) == null ? "–" : `${fmt.format(Math.round(v))} m² (${fmt.format(Math.round(v * 10.764))} ft²)`);
const reasonText = (r) => (typeof r === "string" ? r : [r?.label, r?.text, r?.reason].filter(Boolean).join(": "));
const reasonList = (rs) => { const a = arr(rs).map(reasonText).filter(Boolean); return a.length ? `<ul class="log">${a.slice(0, 6).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""; };
const scoreCls = (v) => (v == null ? "" : v >= 70 ? "ok" : v >= 40 ? "warn" : "bad");
const phoneTxt = (p) => { const d = String(p ?? "").replace(/\D/g, ""); return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : String(p ?? ""); };
const aadtTxt = (v) => (num(v) == null ? "–" : fmt.format(Math.round(v)) + " vehicles/day");
const pctTxt = (v) => { const n = num(v); if (n == null) return "–"; const p = Math.abs(n) <= 1 && n !== 0 && !Number.isInteger(n) ? n * 100 : n; return (p > 0 ? "+" : "") + Math.round(p * 10) / 10 + "%"; };
const STR_TAGS = [[/^hoa|covenant/, "HOA"], [/permit|licen[cs]e/, "permit"], [/primary.?residence|owner.?occup/, "primary residence"], [/zoning/, "zoning"], [/^caps?\b|density/, "cap"], [/^pending|litigat|lawsuit/, "pending"], [/not checked/, "not checked"]];
function strTags(reasons) {
  const labels = arr(reasons).map((r) => { const t = reasonText(r).toLowerCase(), i = t.indexOf(":"); return i > 0 && i < 70 ? t.slice(0, i) : t.slice(0, 70); });
  return STR_TAGS.filter(([re]) => labels.some((l) => re.test(l))).map(([, t]) => t);
}
const statusCls = (s) => { s = String(s || "").toLowerCase(); return /ban|prohibit|not allowed/.test(s) ? "bad" : /unknown|not checked/.test(s) || !s ? "" : /restrict|primary|only|mixed/.test(s) ? "warn" : "ok"; };
const partLabel = (k) => String(k).replace(/_/g, " ").replace(/\bpax\b/, "passengers").replace(/\bemp\b/, "employment").replace(/\byoy\b/, "1 yr").replace(/\b(\d+)y\b/, "$1 yr").replace(/\bmig\b/, "migration").replace(/\bunemp\b/, "unemployment").replace(/\bnps\b/, "national park").replace(/\bvmt\b/, "vehicle-km").replace(/(\d+)km/, "$1 km").replace(/(\d+)m$/, "$1 mo");
const partsTxt = (p) => (p && typeof p === "object" ? Object.entries(p).map(([k, v]) => { const pc = Array.isArray(v) ? num(v[1]) : num(v?.pct ?? v?.percentile ?? v); return pc == null ? "" : `${esc(partLabel(k))} <b>${Math.round(pc)}</b>`; }).filter(Boolean).join(" · ") : "");
function arrivalWindow(sh) {
  const v = Array.isArray(sh) ? sh.map((x) => num(x) ?? 0) : sh && typeof sh === "object" ? [...Array(24).keys()].map((i) => num(pick(sh[i], sh[String(i).padStart(2, "0")])) ?? 0) : null;
  if (!v || v.length !== 24) return null; const tot = v.reduce((a, b) => a + b, 0); if (!(tot > 0)) return null;
  for (let len = 1; len <= 24; len++) { let best = 0, bs = -1;
    for (let s = 0; s < 24; s++) { let sum = 0; for (let k = 0; k < len; k++) sum += v[(s + k) % 24]; if (sum > bs) { bs = sum; best = s; } }
    if (bs >= tot / 2) { const p2 = (x) => String(x % 24).padStart(2, "0"); return { label: `${p2(best)}–${p2(best + len)}`, pct: Math.round((bs / tot) * 100) }; } }
  return null;
}
const HOA_KINDS = { hoa: "HOA (recorded)", condo: "condo association", plat: "subdivision plat (possible covenants — not confirmed HOA)", other: "other recorded area" };
const hoaKind = (k) => { const s = String(k ?? "").toLowerCase(); return /condo/.test(s) ? "condo" : /plat|subdiv/.test(s) ? "plat" : /hoa|homeowner|owners|recorded|association|covenant/.test(s) ? "hoa" : "other"; };
const hoaCol = (k, K) => ({ hoa: K.mag, condo: K.accent, plat: K.warn, other: lift(K.other, 0.3) }[k]);
// street furniture kinds (records only, exact positions; see prepFurniture)
const FKIND = (k) => { k = String(k || "").toLowerCase();
  return /lamp|street_?light|lighting/.test(k) ? "lamp" : /yield|give_?way/.test(k) ? "yield" : /speed/.test(k) ? "speed" : /stop/.test(k) && !/signal/.test(k) ? "stop"
    : /signal|traffic_light/.test(k) ? "signal" : /camera/.test(k) ? "camera" : /hydrant/.test(k) ? "hydrant" : /utility|power/.test(k) ? "upole" : /pole/.test(k) ? "pole" : ""; };
const FLABEL = { lamp: "Street lamp", yield: "Yield sign", speed: "Speed-limit sign", stop: "Stop sign", signal: "Traffic signal location (no live state data)", camera: "Public camera", hydrant: "Fire hydrant", upole: "Utility pole", pole: "Pole (unidentified)" };
const recId = (r) => String(r?.id ?? r?.osm_id ?? r?.record_id ?? r?.asset_id ?? r?.source_id ?? r?.objectid ?? (String(r?.source || "").match(/node\s*(\d+)/i) || [])[1] ?? "");
const isMly = (i) => /mapillary/i.test(String(i?.source || "")) || /^mly:/i.test(String(i?.id || ""));
const safeUrl = (u) => (/^https:\/\//i.test(String(u || "")) ? String(u) : "");
function imgCredit(i) {
  if (isMly(i)) return `<b>Mapillary</b> · <a href="https://www.mapillary.com" target="_blank" rel="noopener">mapillary.com</a> · ${esc(i.attribution || "© Mapillary contributors")}${i.licence ? " · " + esc(i.licence) : ""}`;
  if (/kartaview/i.test(String(i.source || ""))) return `© Grab and KartaView Contributors, CC BY-SA 4.0${i.attribution ? `<br>${esc(i.attribution)}` : ""}`;
  return `${esc(i.attribution || i.source || "")}${i.licence ? " · " + esc(i.licence) : ""}`;
}
const roadName = (r) => String(r?.name || r?.street || r?.STREET_O_NAME_1 || r?.ref || "");

export const DEFAULT_OPTIONS = {
  ground: true, house: true, veg: true, other: true,   // LiDAR point classes (ALL points of each class are drawn; never thinned)
  color: "class",          // "class" | "height" | "intensity" | "dev" | "photo"
  size: 1.4,               // point size, pixels (fine round points)
  model: false,            // roof / wall mesh from mesh.txt
  photo: false,            // aerial photo draped on the LiDAR terrain mesh
  streets: true,           // streets.json surfaces, only when meta.validation.house_overlap_m2 == 0; markings only with markings_source
  furniture: true,         // street furniture from records at exact positions (furniture.json, else streets.json signals)
  parcels: true,           // parcel rings from parcels.json (real boundary runs only; block-edge cuts not drawn)
  hoa: true,               // HOA / covenant areas from parcels.json
  footpaths: true,         // footpaths: city sidewalk layers (footpaths.json) + OpenStreetMap footways (streets.json paths)
  trees: false,            // trees.json crowns (inventory / LiDAR tree IDs)
  thermal: false,          // Landsat land-surface-temperature drape (thermal/lst_grid*.json)
  thermalScene: 0,
  permits: true,           // layers.json permits (re-roof / solar) at recorded points
  c311: false,             // layers.json 311 cases
  streetPhotos: true,      // street_imagery.json pins (Mapillary / KartaView)
  dimOthersOnSelect: true,
};

// =====================================================================================================================
export class BlockManager {
  constructor({ onUpdate, onSelectHouse, groundAt, deck, blocksBase, usaBase, zMode = "absolute", maxLoaded = 3, minZoom = 14, options } = {}) {
    this.onUpdate = typeof onUpdate === "function" ? onUpdate : () => {};
    this.onSelectHouse = typeof onSelectHouse === "function" ? onSelectHouse : () => {};
    this.extGround = typeof groundAt === "function" ? groundAt : null;
    if (deck) globalThis.deck = globalThis.deck || deck;
    this.usaBase = usaBase || new URL("../usa/", MODULE_URL).href;
    this.blocksBase = blocksBase || new URL("blocks/", this.usaBase).href;
    this.zMode = zMode; this.maxLoaded = maxLoaded; this.minZoom = minZoom;
    this.options = { ...DEFAULT_OPTIONS, ...(options || {}) };
    this.index = []; this.blocks = new Map(); this.sel = null; this.photoSel = null; this.focus = null; this.lastZoom = 0; this.viewCenter = null;
    this._raf = 0; this._frame = this._frame.bind(this);
    this.ready = fetch(this.blocksBase + "index.json").then((r) => (r.ok ? r.json() : [])).catch(() => []).then((j) => {
      this.index = arr(j).filter((b) => isObj(b) && /^[\w.-]+$/.test(String(b.slug || "")) && isFinite(+b.lon) && isFinite(+b.lat));
      this._emit("index"); if (this._pendingView) this.update(this._pendingView);
      return this.index;
    });
  }
  _emit(type, slug) { try { this.onUpdate({ type, slug }); } catch (e) { console.warn("BlockManager onUpdate:", e); } }

  // ------------------------------------------------------------ catalogue
  list() { return this.index.map((b) => ({ slug: b.slug, name: b.name, lon: +b.lon, lat: +b.lat, houses: b.houses ?? null, city: b.city, state: b.state, lidar_year: b.lidar_year, state_: this.blocks.get(b.slug)?.state || "idle" })); }
  _entry(slug) { return this.index.find((b) => b.slug === slug) || null; }
  flyTarget(slug) {
    const B = this.blocks.get(slug), e = this._entry(slug); if (!B && !e) return null;
    const lon = B ? B.lon0 : +e.lon, lat = B ? B.lat0 : +e.lat, half = B ? B.half : 50, k = half / 100, z = 18 + Math.log2(100 / half) * 0.9;
    return { longitude: lon + 0.0002 * k, latitude: lat - 0.0009 * k, zoom: Math.min(19.5, Math.max(16.5, z)), pitch: 60, bearing: -32 };
  }
  houseFlyTarget(slug, houseId) {
    const B = this.blocks.get(slug), h = B?.byId.get(String(houseId)); if (!h) return this.flyTarget(slug);
    const [cx, cy] = centroid(h.ring), [lon, lat] = B.toLngLat(cx, cy);
    return { longitude: lon, latitude: lat, zoom: 19.6, pitch: 60, bearing: -7 };
  }
  verticalRef(slug) {
    const B = this.blocks.get(slug); if (!B) return null;
    return { zMode: this.zMode, zBase: B.zBase, zOffset: this._zOff(B), datum: "USGS 3DEP EPT z (metres, source vertical datum, normally NAVD88)",
      note: "block z values are metres above meta.z_base; layer z = local z + zOffset; groundAt() returns the same frame" };
  }
  elevationCheck(slug) {
    const B = this.blocks.get(slug); if (!B || B.state !== "ready") return null;
    const local = B.groundLocal(0, 0), block = local + B.zBase, external = this.extGround ? num(this.extGround(B.lon0, B.lat0)) : null;
    return { block, external, delta: external != null ? external - block : null, local };
  }
  _zOff(B) {
    if (this.zMode === "relative") return 0;
    if (this.zMode === "aligned" && this.extGround && B.state === "ready") {
      if (B._aligned == null) { const g = num(this.extGround(B.lon0, B.lat0)); B._aligned = g != null ? g - B.groundLocal(0, 0) : B.zBase; }
      return B._aligned;
    }
    return B.zBase;
  }
  setOptions(o) { Object.assign(this.options, o || {}); for (const B of this.blocks.values()) B.colorKey = null; this._emit("options"); }

  // ------------------------------------------------------------ streaming
  update(viewport) {
    if (!viewport) return;
    if (!this.index.length) { this._pendingView = viewport; return; }
    const zoom = +viewport.zoom || 0; this.lastZoom = zoom;
    if (isFinite(+viewport.longitude)) this.viewCenter = [+viewport.longitude, +viewport.latitude];
    if (zoom < this.minZoom) { if (this.blocks.size) { for (const slug of [...this.blocks.keys()]) this._unload(slug); } return; }
    const bb = this._bounds(viewport); if (!bb) return;
    const [w, s, e, n] = bb, now = performance.now(), want = [];
    for (const b of this.index) {
      const B = this.blocks.get(b.slug), box = B?.bounds || this._entryBox(b);
      if (box[2] < w || box[0] > e || box[3] < s || box[1] > n) continue;
      want.push(b);
    }
    // nearest to the view centre first
    const c = this.viewCenter || [(w + e) / 2, (s + n) / 2];
    want.sort((a, b) => Math.hypot(+a.lon - c[0], +a.lat - c[1]) - Math.hypot(+b.lon - c[0], +b.lat - c[1]));
    for (const b of want.slice(0, this.maxLoaded)) { const B = this.blocks.get(b.slug); if (B) B.used = now; else this._load(b); }
    // LRU: keep at most maxLoaded; never drop one that is in view before one that is not
    if (this.blocks.size > this.maxLoaded) {
      const inView = new Set(want.slice(0, this.maxLoaded).map((b) => b.slug));
      const order = [...this.blocks.values()].sort((a, b) => (inView.has(a.slug) - inView.has(b.slug)) || a.used - b.used);
      while (this.blocks.size > this.maxLoaded && order.length) this._unload(order.shift().slug);
    }
    this.focus = want.find((b) => this.blocks.get(b.slug)?.state === "ready")?.slug || this.focus;
  }
  _entryBox(b) { const dLat = 90 / 110540, dLon = 90 / (111320 * Math.cos(+b.lat * RAD)); return [+b.lon - dLon, +b.lat - dLat, +b.lon + dLon, +b.lat + dLat]; }
  _bounds(v) {
    try { if (typeof v.getBounds === "function") return v.getBounds(); } catch {}
    const dk = D();
    if (dk?.WebMercatorViewport && v.width && v.height) { try { return new dk.WebMercatorViewport({ ...v, pitch: Math.min(+v.pitch || 0, 75) }).getBounds(); } catch {} }
    const lat = +v.latitude, lon = +v.longitude, mpp = (40075016.7 * Math.cos(lat * RAD)) / (512 * 2 ** (+v.zoom || 0)), f = 1 + (+v.pitch || 0) / 30;
    const hx = ((v.width || 1600) / 2) * mpp * f, hy = ((v.height || 1000) / 2) * mpp * f;
    return [lon - hx / (111320 * Math.cos(lat * RAD)), lat - hy / 110540, lon + hx / (111320 * Math.cos(lat * RAD)), lat + hy / 110540];
  }
  _unload(slug) {
    const B = this.blocks.get(slug); if (!B) return;
    try { B.ctrl.abort(); } catch {}
    this.blocks.delete(slug);
    if (this.sel?.slug === slug) { this.sel = null; this.onSelectHouse(null); this._stopAnim(); }
    if (this.photoSel?.slug === slug) this.photoSel = null;
    if (this.focus === slug) this.focus = null;
    this._emit("unload", slug);
  }
  _load(entry) {
    const B = new Block(entry, this); this.blocks.set(entry.slug, B); B.used = performance.now();
    this._emit("loading", entry.slug);
    B.load().then(() => { if (this.blocks.get(entry.slug) !== B) return; this.focus = this.focus || entry.slug; this._emit("load", entry.slug); })
      .catch((e) => { if (B.ctrl.signal.aborted || this.blocks.get(entry.slug) !== B) return; B.state = "error"; B.error = e.message; console.warn(`block ${entry.slug}: ${e.message}`); this._emit("error", entry.slug); });
  }

  // ------------------------------------------------------------ selection
  select(slug, houseId) {
    const B = this.blocks.get(slug); if (!B || B.state !== "ready") return null;
    const h = B.byId.get(String(houseId)); if (!h) return null;
    this.sel = { slug, houseId: String(houseId), h, X: B.selPrep(h) }; this.photoSel = null; this.focus = slug;
    B.ensurePhotos().then(() => { if (this.sel?.h === h) { this.sel.thumbs = B.roofThumbs(h); this._emit("panel", slug); } });
    this.onSelectHouse({ slug, houseId: String(houseId), house: h });
    this._emit("select", slug); this._startAnim();
    return { slug, houseId: String(houseId) };
  }
  clear() { const had = !!this.sel || !!this.photoSel; this.sel = null; this.photoSel = null; this._stopAnim(); if (had) { this.onSelectHouse(null); this._emit("select"); } }
  pick(info) {
    const ly = info?.layer, o = info?.object; if (!ly || !o) return null;
    const slug = ly.props?.blockSlug; if (!slug) return null;
    const B = this.blocks.get(slug); if (!B) return null;
    if (ly.props.blockRole === "house") { const id = B.idOf(o); return this.select(slug, id); }
    if (ly.props.blockRole === "photo") { this.photoSel = { slug, photo: o }; this.focus = slug; this._emit("panel", slug); return { slug, photo: o }; }
    return null;
  }
  tooltip(info) {   // optional: deck getTooltip helper (plain text)
    const ly = info?.layer, o = info?.object; if (!ly?.props?.blockSlug || !o) return null;
    const role = ly.props.blockRole;
    if (role === "house") { const own = ownValue(o), med = medianOnly(o); return `${o.address || "Building"}${o.garage ? " (garage)" : ""}${num(o.points) != null ? "\n" + fmt.format(o.points) + " laser points" : ""}${own ? "\n~" + money0(own) : med ? "\nneighbourhood median ~" + money0(med) + " (not this house)" : ""}`; }
    if (role === "photo") return `Street photo · ${o.source || ""}${o.captured_at ? " · " + String(o.captured_at).slice(0, 10) : ""}\n${isMly(o) ? "Mapillary · " : ""}${o.licence || ""}`;
    if (role === "furn") return o.note;
    if (role === "hoa") return `${o.name || "Unnamed area"}\n${HOA_KINDS[o.k]}`;
    if (role === "permit") return `${o.k === "roof" ? "Re-roof" : "Solar"} permit · ${o.date || ""}\n${o.comments || ""}`;
    if (role === "c311") return `311 · ${o.date || ""}\n${o.type || ""}\n${o.title || ""}`;
    if (role === "tree") return `${o.genus || "Tree (species not in inventory)"}\n${o.h ?? "?"} m tall · crown ~${((+o.r || 0) * 2).toFixed(0)} m wide`;
    if (role === "road") return `${roadName(o) || "Road"}${o.kind ? " · " + o.kind : ""}\n${o.source ? String(o.source) : "from streets.json"}`;
    if (role === "walk") return `${o.kind ? o.kind[0].toUpperCase() + o.kind.slice(1) : "Footpath"}${o.street ? " · " + o.street : ""}${o.area != null ? " · " + o.area + " m²" : ""}
${o.src || ""}`;
    if (role === "parcel") return `Parcel ${o.p?.id ?? ""}${o.p?.address ? "\n" + o.p.address : ""}`;
    return null;
  }
  _startAnim() { if (!this._raf) this._raf = requestAnimationFrame(this._frame); }
  _stopAnim() { if (this._raf) cancelAnimationFrame(this._raf); this._raf = 0; }
  _frame() { this._raf = 0; if (!this.sel) return; this._emit("frame", this.sel.slug); this._raf = requestAnimationFrame(this._frame); }

  groundAt(lon, lat) {
    for (const B of this.blocks.values()) {
      if (B.state !== "ready") continue;
      const [x, y] = B.toLocal(lon, lat); if (Math.abs(x) > B.half || Math.abs(y) > B.half) continue;
      return B.groundLocal(x, y) + this._zOff(B);
    }
    return null;
  }

  // ------------------------------------------------------------ layers
  layers() {
    const out = [];
    for (const B of this.blocks.values()) { if (B.state !== "ready") continue; try { out.push(...B.layers(this)); } catch (e) { console.warn(`block ${B.slug} layers:`, e); } }
    return out;
  }

  // ------------------------------------------------------------ panel
  panelHtml() {
    try {
      if (this.sel) { const B = this.blocks.get(this.sel.slug); if (B) return B.dossier(this.sel.h, this.sel.thumbs || []); }
      if (this.photoSel) { const B = this.blocks.get(this.photoSel.slug); if (B) return B.photoCard(this.photoSel.photo) + B.summary(); }
      const f = (this.focus && this.blocks.get(this.focus)?.state === "ready" && this.blocks.get(this.focus)) || [...this.blocks.values()].find((b) => b.state === "ready");
      if (f) return f.summary();
      const ld = [...this.blocks.values()].find((b) => b.state === "loading");
      if (ld) return `<div class="lbl">3D block</div><h3>${esc(ld.entry.name || ld.slug)}</h3><div class="sub">Streaming every laser point and every open record…</div>`;
    } catch (e) { console.warn("panelHtml:", e); return `<div class="sub">Some records for this block could not be shown.</div>`; }
    return "";
  }
  bindPanel(el) {
    if (!el || el._bmBound) return; el._bmBound = true;
    el.addEventListener("click", (e) => {
      const t = e.target.closest?.("[data-bm-close],[data-bm-house]"); if (!t || !el.contains(t)) return;
      if (t.hasAttribute("data-bm-close")) { this.clear(); return; }
      const [slug, id] = String(t.getAttribute("data-bm-house")).split("|"); this.select(slug, id);
    });
  }
}

// =====================================================================================================================
class Block {
  constructor(entry, mgr) {
    this.entry = entry; this.slug = entry.slug; this.mgr = mgr; this.state = "loading"; this.ctrl = new AbortController();
    this.base = mgr.blocksBase + encodeURIComponent(entry.slug) + "/"; this.cache = {}; this.imgs = {}; this.PHOTOS = []; this._photosP = null;
    this.lon0 = +entry.lon; this.lat0 = +entry.lat; this.half = 50; this.zBase = 0;
  }
  // local metres (Web-Mercator metres / k, as the builder writes them) <-> lon/lat
  toLocal(lon, lat) { const c = Math.cos(this.lat0 * RAD); return [R_EARTH * (lon - this.lon0) * RAD * c, (mercY(lat) - mercY(this.lat0)) * c]; }
  toLngLat(x, y) { const c = Math.cos(this.lat0 * RAD); return [this.lon0 + x / (R_EARTH * RAD * c), invMercY(mercY(this.lat0) + y / c)]; }
  _fetch(f) { return fetch(this.base + f, { signal: this.ctrl.signal }); }
  present(f) { return !this.FILES || this.FILES.has(f); }
  optJSON(f) {
    if (!this.present(f)) return Promise.resolve(null);
    return this._fetch(f).then((r) => (r.ok ? r.text() : "")).then((t) => { if (!t) return null; try { return JSON.parse(t); } catch { return null; } }).catch((e) => { if (e.name === "AbortError") throw e; return null; });
  }
  // optional files not in the manifest: checked in the static server's folder listing first, so a missing file never 404s
  listing(dir) { this._LIST ||= {}; return (this._LIST[dir] ||= this._fetch(dir).then((r) => (r.ok ? r.text() : "")).then((t) => (/<a\s[^>]*href=/i.test(t) ? t : null)).catch(() => null)); }
  async optLate(f) {
    if (this.FILES?.has(f)) return this.optJSON(f);
    const parts = f.split("/"); let dir = "";
    for (let i = 0; i < parts.length; i++) { const t = await this.listing(dir); const name = parts[i] + (i < parts.length - 1 ? "/" : ""); if (t == null || !t.includes(`href="${name}"`)) return null; dir += name; }
    return this._fetch(f).then((r) => (r.ok ? r.text() : "")).then((t) => { try { return t ? JSON.parse(t) : null; } catch { return null; } }).catch(() => null);
  }
  loadImg(rel) {
    if (!this.present(rel)) return Promise.resolve(null);
    return new Promise((res) => { const im = new Image(); im.crossOrigin = "anonymous"; im.onload = () => res(im); im.onerror = () => res(null); im.src = this.base + rel; });
  }

  async load() {
    const need = (f) => this._fetch(f).then((r) => { if (!r.ok) throw new Error(`${f} missing (HTTP ${r.status})`); return r.json(); });
    const meta = await need("meta.json"); const M = (this.M = meta);
    const ent = this.entry; for (const k of ["name", "city", "state", "fips"]) if (M[k] == null && ent[k] != null) M[k] = ent[k];
    if (!Array.isArray(M.origin)) M.origin = [+ent.lon, +ent.lat];
    this.lon0 = +M.origin[0]; this.lat0 = +M.origin[1];
    this.half = M.half_m = num(M.half_m) || 60; this.zBase = num(M.z_base) ?? 0;
    if (Array.isArray(M.files)) this.FILES = new Set(M.files.map(String));
    const bd = arr(M.bounds).map(Number);
    if (bd.length === 4 && bd.every(isFinite)) this.bounds = bd;
    else { const [w, s] = this.toLngLat(-this.half, -this.half), [e, n] = this.toLngLat(this.half, this.half); this.bounds = [w, s, e, n]; }
    const pfiles = arr(M.point_files).length ? M.point_files : [...Array(num(M.point_parts) ?? num(M.parts) ?? 1).keys()].map((i) => `points_${i}.txt`);
    const [houses, mesh, layers, sources, ...txt] = await Promise.all([need("houses.json"), need("mesh.txt"), this.optJSON("layers.json"), this.optJSON("sources.json"),
      ...pfiles.map((f) => this._fetch(f).then((r) => (r.ok ? r.text() : "")).catch((e) => { if (e.name === "AbortError") throw e; return ""; }))]);
    this.RAW = arr(houses); this.H = this.RAW.filter((h) => h && Array.isArray(h.ring) && h.ring.length >= 3);
    this.byId = new Map(); this.H.forEach((h, i) => this.byId.set(this.idOf(h, i), h));
    const L = { pavement: [], alleys: [], planimetric: [], cases311: [], permits: [], hail_days: [], img_years: [], ...(layers || {}) };
    for (const k of ["pavement", "alleys", "planimetric", "cases311", "permits", "hail_days", "img_years"]) L[k] = arr(L[k]);
    this.L = L; this.sources = arr(sources);
    const mh = mesh?.houses, t = mesh?.terrain;
    this.houseMesh = mh?.pos && mh?.idx ? { P: b64(mh.pos, Float32Array), N: b64(mh.nrm, Float32Array), C: b64(mh.col, Uint8Array), I: b64(mh.idx, Uint32Array) } : null;
    if (this.houseMesh && !this.houseMesh.I.length) this.houseMesh = null;
    this.terrainMesh = t?.pos && t?.idx ? { attributes: { POSITION: { value: b64(t.pos, Float32Array), size: 3 }, NORMAL: { value: b64(t.nrm, Float32Array), size: 3 }, TEXCOORD_0: { value: b64(t.uv, Float32Array), size: 2 } }, indices: { value: b64(t.idx, Uint32Array), size: 1 } } : null;
    const tx = txt.map((s) => s.trim()).filter(Boolean);
    if (!tx.length) throw new Error("no laser points");
    this.P = decodePoints(tx, M.point_unit_m || 0.02);
    this.gz = this.groundGrid(); this.TZS = this.terrainSampler();
    const [trees, infra, st, lst, lstW, fj, sim, pj, fpj] = await Promise.all([this.optJSON("trees.json"), this.optJSON("infra.json"), this.optLate("streets.json"), this.optLate("thermal/lst_grid.json"),
      this.optLate("thermal/lst_grid_winter.json"), this.optLate("furniture.json"), this.optLate("street_imagery.json"), this.optLate("parcels.json"), this.optLate("footpaths.json")]);
    if (this.ctrl.signal.aborted) throw new Error("aborted");
    this.T = arr(trees).filter((t) => isObj(t) && isFinite(+t.x) && isFinite(+t.y)); this.INF = infra; this.FURNJ = isObj(fj) ? fj : null;
    const safe = (f, name) => { try { return f(); } catch (e) { console.warn(`${this.slug} ${name} skipped:`, e.message); return null; } };
    this.PAR = safe(() => prepParcels(pj), "parcels.json");
    this.SIMG = arr(sim?.images).filter((i) => isObj(i) && isFinite(+i.x) && isFinite(+i.y)).map((i) => ({ ...i, x: +i.x, y: +i.y, heading_deg: num(+i.heading_deg), z: this.groundLocal(+i.x, +i.y) }));
    this.simgMeta = isObj(sim?.meta) ? sim.meta : null;
    this.STR = safe(() => this.prepStreets(st), "streets.json");
    this.WALK = safe(() => this.prepWalks(fpj, st), "footpaths");
    this.LST = safe(() => this.parseLST(lst && lstW && !arr(lst.scenes).length ? { scenes: [lst, lstW], source: lst.source } : lst || lstW), "lst_grid.json");
    this.FURN = safe(() => this.prepFurniture(), "street furniture");
    this.state = "ready";
    // the latest aerial is needed for the road photo surface / photo drape; load it in the background
    this.ensurePhotos(true).then(() => { if (this.mgr.blocks.get(this.slug) === this && this.latestImg()) this.mgr._emit("photos", this.slug); });
  }
  idOf(h, i) { return String(h?.id ?? (i != null ? i : this.H.indexOf(h))); }
  // per-year county aerials (img/<year>.jpg), else a single aerial.jpg. latestOnly: just the newest (road texture / photo drape);
  // all years load on the first house selection (roof-over-the-years thumbnails)
  ensurePhotos(latestOnly) {
    const key = latestOnly ? "_photosLatest" : "_photosAll";
    if (this[key]) return this[key];
    const M = this.M, years = (arr(this.L.img_years).length ? this.L.img_years : arr(M.img_years)).map(String);
    return (this[key] = (async () => {
      const list = latestOnly && years.length ? [years[years.length - 1]] : years;
      await Promise.all(list.map(async (y) => { if (!this.imgs[y]) { const im = await this.loadImg(`img/${y}.jpg`); if (im) this.imgs[y] = im; } }));
      if (!years.length && !this.imgs.aerial && this.present("aerial.jpg")) { const im = await this.loadImg("aerial.jpg"); if (im) this.imgs.aerial = im; }
      this.PHOTOS = Object.keys(this.imgs).sort().map((k) => ({ key: k, label: k === "aerial" ? (M.aerial_year ? String(M.aerial_year) : "Aerial") : k }));
    })());
  }
  latestImg() { const k = this.PHOTOS[this.PHOTOS.length - 1]?.key; return k ? this.imgs[k] : null; }

  // ------------------------------------------------------------ ground
  groundGrid() {
    const P = this.P, half = this.half, cs = 2, n = Math.ceil((2 * half) / cs), sum = new Float64Array(n * n), cnt = new Uint32Array(n * n);
    for (let i = 0; i < P.n; i++) if (P.kind[i] === 0) { const c = Math.min(n - 1, Math.max(0, Math.floor((P.pos[i * 3] + half) / cs))), r = Math.min(n - 1, Math.max(0, Math.floor((P.pos[i * 3 + 1] + half) / cs))); sum[r * n + c] += P.pos[i * 3 + 2]; cnt[r * n + c]++; }
    const g = new Float32Array(n * n); let last = 0; for (let i = 0; i < n * n; i++) { if (cnt[i]) last = sum[i] / cnt[i]; g[i] = cnt[i] ? sum[i] / cnt[i] : last; }
    return (x, y) => g[Math.min(n - 1, Math.max(0, Math.floor((y + half) / cs))) * n + Math.min(n - 1, Math.max(0, Math.floor((x + half) / cs)))];
  }
  terrainSampler() {
    const p = this.terrainMesh?.attributes?.POSITION?.value; if (!p || p.length < 12) return null;
    const n = Math.round(Math.sqrt(p.length / 3)); if (n * n * 3 !== p.length) return null;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < p.length; i += 3) { x0 = Math.min(x0, p[i]); x1 = Math.max(x1, p[i]); y0 = Math.min(y0, p[i + 1]); y1 = Math.max(y1, p[i + 1]); }
    const sx = (x1 - x0) / (n - 1), sy = (y1 - y0) / (n - 1); if (!(sx > 0 && sy > 0)) return null;
    const g = new Float32Array(n * n).fill(NaN);
    for (let i = 0; i < p.length; i += 3) { const c = Math.round((p[i] - x0) / sx), r = Math.round((p[i + 1] - y0) / sy); if (c >= 0 && r >= 0 && c < n && r < n) g[r * n + c] = p[i + 2]; }
    for (let i = 0; i < g.length; i++) if (g[i] !== g[i]) return null;
    return (x, y) => { const fx = Math.max(0, Math.min(n - 1.0001, (x - x0) / sx)), fy = Math.max(0, Math.min(n - 1.0001, (y - y0) / sy)), c = Math.floor(fx), r = Math.floor(fy), u = fx - c, v = fy - r;
      return g[r * n + c] * (1 - u) * (1 - v) + g[r * n + c + 1] * u * (1 - v) + g[(r + 1) * n + c] * (1 - u) * v + g[(r + 1) * n + c + 1] * u * v; };
  }
  groundLocal(x, y) { return this.TZS ? this.TZS(x, y) : this.gz(x, y); }
  groundLow(x, y) {   // lowest ground return in each 1 m cell (3x3): mapped surfaces sit under this so they never cover a laser point
    if (!this.GLOW) { const P = this.P, half = this.half, n = Math.ceil(2 * half), g = new Float32Array(n * n).fill(Infinity);
      for (let i = 0; i < P.n; i++) if (P.kind[i] === 0) { const c = Math.floor(P.pos[i * 3] + half), r = Math.floor(P.pos[i * 3 + 1] + half); if (c >= 0 && r >= 0 && c < n && r < n) g[r * n + c] = Math.min(g[r * n + c], P.pos[i * 3 + 2]); }
      this.GLOW = { g, n, half }; }
    const { g, n, half } = this.GLOW, c = Math.floor(x + half), r = Math.floor(y + half); let m = this.groundLocal(x, y);
    for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) { const cc = c + dc, rr = r + dr; if (cc >= 0 && rr >= 0 && cc < n && rr < n) m = Math.min(m, g[rr * n + cc]); }
    return m;
  }

  // ------------------------------------------------------------ data prep
  footprints() { return (this.FOOT ||= this.H.map((h) => { const xs = h.ring.map((p) => p[0]), ys = h.ring.map((p) => p[1]); return { ring: h.ring, x0: Math.min(...xs) - 1, x1: Math.max(...xs) + 1, y0: Math.min(...ys) - 1, y1: Math.max(...ys) + 1 }; })); }
  segHitsHouse(a, b) {
    const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
    for (const f of this.footprints()) { if (x1 < f.x0 || x0 > f.x1 || y1 < f.y0 || y0 > f.y1) continue; const R_ = f.ring;
      if (inRing(a[0], a[1], R_) || inRing(b[0], b[1], R_)) return true;
      for (let i = 0; i < R_.length; i++) if (segX(a, b, R_[i], R_[(i + 1) % R_.length])) return true; }
    return false;
  }
  ringHitsHouse(O) { for (let i = 0; i < O.length; i++) if (this.segHitsHouse(O[i], O[(i + 1) % O.length])) return true; return this.footprints().some((f) => f.ring.some(([x, y]) => inRing(x, y, O))); }
  zebraStripes(O) {
    if (O.length < 3) return [];
    let best = 0, ux = 1, uy = 0;
    for (let i = 0; i < O.length; i++) { const a = O[i], b = O[(i + 1) % O.length], l = Math.hypot(b[0] - a[0], b[1] - a[1]); if (l > best) { best = l; ux = (b[0] - a[0]) / l; uy = (b[1] - a[1]) / l; } }
    const vx = -uy, vy = ux, su = O.map((p) => p[0] * ux + p[1] * uy), sv = O.map((p) => p[0] * vx + p[1] * vy);
    const u0 = Math.min(...su), u1 = Math.max(...su), v0 = Math.min(...sv) + 0.1, v1 = Math.max(...sv) - 0.1, out = [];
    if (!(v1 > v0)) return out;
    for (let s = u0 + 0.3; s + 0.6 <= u1 - 0.1; s += 1.2) out.push([[s, v0], [s + 0.6, v0], [s + 0.6, v1], [s, v1]].map(([a, b]) => { const x = a * ux + b * vx, y = a * uy + b * vy; return [x, y, this.groundLocal(x, y) + 0.03]; }));
    return out;
  }
  prepStreets(st) {
    if (!isObj(st)) return null;
    const M = this.M, skipped = { roads: 0, lines: 0, crosswalks: 0, stop_lines: 0 };
    const cutPath = (path, kind) => { const out = []; let cur = [];
      for (let i = 0; i < path.length; i++) { if (i && this.segHitsHouse(path[i - 1], path[i])) { skipped[kind]++; if (cur.length >= 2) out.push(cur); cur = [path[i]]; } else cur.push(path[i]); }
      if (cur.length >= 2) out.push(cur); return out; };
    const ok = (p) => Array.isArray(p) && p.length >= 2 && isFinite(+p[0]) && isFinite(+p[1]);
    const fixP = (p, dz) => [+p[0], +p[1], (num(+p[2]) ?? this.groundLocal(+p[0], +p[1])) + dz];
    const outer = (poly) => (Array.isArray(poly?.[0]?.[0]) ? poly[0] : poly);
    const inLidar = (p) => Math.abs(+p[0]) <= M.half_m && Math.abs(+p[1]) <= M.half_m;
    const under = (p, dz) => { const z = num(+p[2]); if (!inLidar(p)) return [+p[0], +p[1], (z ?? this.groundLocal(+p[0], +p[1])) + dz]; const g = this.groundLow(+p[0], +p[1]); return [+p[0], +p[1], Math.min(z ?? g, g) + dz]; };
    const ringU = (poly, dz) => (Array.isArray(poly?.[0]?.[0]) ? poly.map((r) => arr(r).filter(ok).map((p) => under(p, dz))) : arr(poly).filter(ok).map((p) => under(p, dz)));
    const polys = (list, dz) => arr(list).filter((r) => isObj(r) && arr(outer(r.poly)).filter(ok).length >= 3).map((r) => ({ ...r, P: ringU(r.poly, dz), O: arr(outer(r.poly)).filter(ok).map((p) => [+p[0], +p[1]]) }));
    const roads = polys(st.roads, -0.06).filter((r) => !(this.ringHitsHouse(r.O) && ++skipped.roads)), sidewalks = polys(st.sidewalks, -0.04), drives = polys(st.driveways, -0.05), lines = [];
    const curbs = arr(st.curbs).filter((c) => arr(c?.path).filter(ok).length >= 2).map((c) => ({ path: arr(c.path).filter(ok).map((q) => under(q, 0.02)) }));
    for (const l of arr(st.lines).flatMap((l) => cutPath(arr(l?.path).filter(ok), "lines").map((path) => ({ ...l, path })))) { const path = l.path; if (path.length < 2) continue;
      const w = Math.max(0.05, Math.min(0.6, num(+l.width_m) ?? 0.12)), yel = /yel/i.test(String(l.color || "")), style = String(l.style || "solid").toLowerCase(), p3 = path.map((p) => fixP(p, 0.035));
      if (/double/.test(style)) { const o = w / 2 + 0.06; lines.push({ path: offsetPath(p3, o), w, yel, dashed: false, source: l.source }, { path: offsetPath(p3, -o), w, yel, dashed: false, source: l.source }); }
      else lines.push({ path: p3, w, yel, dashed: /dash/.test(style), source: l.source }); }
    const stops = arr(st.stop_lines).flatMap((s) => cutPath(arr(s?.path).filter(ok), "stop_lines").map((path) => ({ path: path.map((q) => fixP(q, 0.04)), w: Math.max(0.2, Math.min(0.8, num(+s.width_m) ?? 0.45)) })));
    const zebra = []; for (const c of polys(st.crosswalks, 0)) { if (this.ringHitsHouse(c.O)) { skipped.crosswalks++; continue; } zebra.push(...this.zebraStripes(c.O)); }
    const nSkip = skipped.roads + skipped.lines + skipped.crosswalks + skipped.stop_lines;
    if (nSkip) console.info(`${this.slug} streets.json: skipped ${nSkip} pieces that cross a house footprint`);
    const signals = arr(st.signals).filter((s) => isObj(s) && isFinite(+s.x) && isFinite(+s.y));
    if (!roads.length && !lines.length && !sidewalks.length && !zebra.length && !stops.length && !signals.length) return null;
    // markings only from a real marking dataset (meta.markings_source); never synthesised
    const mk = st.meta?.markings_source, marked = mk != null && mk !== "" && mk !== false, keep = (o) => marked && (o.source == null || !/synth|rule|inferred|generated|derived/i.test(String(o.source)));
    return { roads, sidewalks, drives, curbs, lines: lines.filter(keep), stops: marked ? stops : [], zebra: marked ? zebra : [], signals,
      markingsSource: marked ? String(typeof mk === "object" ? mk.name || JSON.stringify(mk) : mk) : "", attribution: String(st.meta?.attribution || ""), skipped,
      validated: num(st.meta?.validation?.house_overlap_m2) === 0 };
  }
  // footpaths: measured only. City sidewalk polygons/lines (footpaths.json, green licences) + OSM footways at their mapped lines.
  prepWalks(fp, st) {
    const ok = (p) => Array.isArray(p) && p.length >= 2 && isFinite(+p[0]) && isFinite(+p[1]), xy = (l) => arr(l).filter(ok).map((p) => [+p[0], +p[1]]);
    const m = isObj(fp?.meta) ? fp.meta : {}, cityTxt = m.attribution ? String(m.attribution) : "";
    const polys = arr(fp?.polys).filter(isObj).map((r) => ({ ring: xy(r.poly), kind: r.kind || "sidewalk", src: cityTxt, area: num(+r.area_m2), year: r.year })).filter((r) => r.ring.length >= 3);
    const lines = arr(fp?.lines).filter(isObj).map((l) => ({ path: xy(l.path), kind: l.kind || "sidewalk", street: l.street, src: cityTxt, city: true })).filter((l) => l.path.length >= 2);
    const osm = arr(st?.paths).filter(isObj).map((l) => ({ path: xy(l.path), kind: String(l.kind || "footway").replace(/^footway:/, "footway · "), src: "OpenStreetMap (ODbL), mapped line", city: false })).filter((l) => l.path.length >= 2);
    if (!polys.length && !lines.length && !osm.length) return null;
    return { polys, lines: [...lines, ...osm], cityAttribution: cityTxt, nOsm: osm.length };
  }
  prepFurniture() {
    const items = [], marks = [], lim = this.half + 5, fromFJ = arr(this.FURNJ?.items).length > 0;
    const add = (rec, file) => {
      const kind = FKIND(rec.kind), x = +rec.x, y = +rec.y; if (!kind || !isFinite(x) || !isFinite(y) || !(Math.abs(x) <= lim && Math.abs(y) <= lim)) return;
      const src = String(rec.source || ""), id = recId(rec), z = num(+rec.z) ?? this.groundLocal(x, y), where = `${file}${src ? " · " + src : ""}`;
      if ((kind === "stop" || kind === "yield") && /highway\s*=\s*(stop|give_?way)|control.?point|road node/i.test(src)) {
        const osm = (src.match(/node\s*(\d+)/i) || [])[1] || id;
        marks.push({ kind, x, y, z, note: `${kind === "stop" ? "Stop" : "Give-way"} control point (OSM node ${osm || "?"}) — sign pole location not recorded\nexact location from ${where}` });
        return;
      }
      if (kind === "speed" && /maxspeed/i.test(src)) return;
      const hd = num(+rec.heading_deg), o = { kind, x, y, z, heading: hd, oriented: hd != null, h: num(+rec.height_m), value: rec.value ?? null, lidar: typeof rec.confirmed_by_lidar === "boolean" ? rec.confirmed_by_lidar : null };
      o.note = [`${FLABEL[kind]}${kind === "speed" && o.value != null ? ": " + o.value : ""}`, id ? `record ${id}` : "record id not given", `exact location from ${where}`,
        o.lidar != null ? `LiDAR-confirmed: ${o.lidar ? "yes" : "no"}` : "LiDAR-confirmed: not checked", /stop|yield|speed|signal|camera|lamp/.test(kind) && !o.oriented ? "facing not recorded: drawn without an oriented panel / arm" : ""].filter(Boolean).join("\n");
      items.push(o);
    };
    if (fromFJ) { for (const it of this.FURNJ.items) if (isObj(it)) add(it, "furniture.json"); }
    else for (const s of arr(this.STR?.signals)) if (isObj(s)) add(s, "streets.json");
    if (!items.length && !marks.length) return null;
    const by = (k) => items.filter((o) => o.kind === k);
    return { items, marks, signals: by("signal"), signs: items.filter((o) => /stop|yield|speed/.test(o.kind)), lamps: by("lamp"), cams: by("camera"), hydrants: by("hydrant"), poles: items.filter((o) => o.kind === "upole" || o.kind === "pole"), fromFJ, mesh: null, lens: null, faces: null };
  }
  parseLST(j) {
    if (!isObj(j)) return null;
    const M = this.M, LONM = 111320 * Math.cos(this.lat0 * RAD), LATM = 110540;
    let list = [arr(j.scenes), arr(j.grids), arr(j.dates).filter(isObj), arr(j.images)].find((a) => a.length) || [];
    if (!list.length) for (const k of ["summer", "winter"]) if (isObj(j[k])) list.push({ season: k, ...j[k] });
    if (!list.length && [j.lst_c, j.grid, j.values].some(Array.isArray)) list = [j];
    const cell = num(+pick(j.cell_m, j.res_m, j.resolution_m, j.pixel_m, list[0]?.pixel_m)) ?? 30, scenes = [];
    for (const s of list) {
      const v = pick(s.lst_c, s.grid, s.values, s.data, s.lst, s.celsius); if (!Array.isArray(v) || !v.length) continue;
      let w = num(+pick(s.w, s.width, s.cols, s.ncols, j.w, j.width, j.cols, j.ncols)), h = num(+pick(s.h, s.height, s.rows, s.nrows, j.h, j.height, j.rows, j.nrows)), flat = v;
      if (Array.isArray(v[0])) { h = v.length; w = Math.max(...v.map((r) => arr(r).length)); flat = []; for (const row of v) for (let c = 0; c < w; c++) flat.push(arr(row)[c]); }
      if (!w || !h) { const n = Math.round(Math.sqrt(flat.length)); if (n * n !== flat.length) continue; w = h = n; }
      if (w * h > flat.length) continue;
      const scl = num(+pick(s.scale, j.scale)) ?? 1, off = num(+pick(s.offset, j.offset)) ?? 0, nod = num(+pick(s.nodata, j.nodata)), vals = new Float32Array(w * h);
      for (let i = 0; i < w * h; i++) { const x = flat[i]; vals[i] = typeof x === "number" && isFinite(x) && x !== nod ? x * scl + off : NaN; }
      let fin = [...vals].filter((x) => x === x).sort((a, b) => a - b); if (!fin.length) continue;
      const unit = String(pick(s.unit, s.units, j.unit, j.units) || "");
      if (/^k(elvin)?$/i.test(unit) || (!/c/i.test(unit) && fin[fin.length >> 1] > 150)) { for (let i = 0; i < vals.length; i++) vals[i] -= 273.15; fin = fin.map((x) => x - 273.15); }
      let b = pick(s.bounds_m, s.bbox_m, s.extent_m, s.bounds, j.bounds_m, j.bbox_m, j.extent_m, j.bounds, j.bbox);
      if (isObj(b)) b = [pick(b.xmin, b.west, b.x0, b.minx), pick(b.ymin, b.south, b.y0, b.miny), pick(b.xmax, b.east, b.x1, b.maxx), pick(b.ymax, b.north, b.y1, b.maxy)];
      b = arr(b).map(Number);
      if (b.length === 4 && b.every(isFinite)) { if (Math.abs(b[2] - b[0]) < 1 && Math.abs(b[1]) <= 90) b = [(b[0] - M.origin[0]) * LONM, (b[1] - M.origin[1]) * LATM, (b[2] - M.origin[0]) * LONM, (b[3] - M.origin[1]) * LATM]; }
      else { const om = arr(pick(s.origin_m, j.origin_m)).map(Number); b = om.length >= 2 && om.every(isFinite) ? [om[0], om[1] - h * cell, om[0] + w * cell, om[1]] : [(-w * cell) / 2, (-h * cell) / 2, (w * cell) / 2, (h * cell) / 2]; }
      b = [Math.min(b[0], b[2]), Math.min(b[1], b[3]), Math.max(b[0], b[2]), Math.max(b[1], b[3])];
      const ro = pick(s.row0, s.row_order, j.row0, j.row_order), north = !/south|bottom|lower|^up/i.test(typeof ro === "string" ? ro : "north");
      const date = String(pick(s.date, s.acquired, s.datetime, s.acq_date) || ""), mo = +((date.match(/^\d{4}-(\d{2})/) || [])[1] || 0);
      const season = String(s.season || (mo >= 5 && mo <= 9 ? "summer" : mo && (mo >= 11 || mo <= 3) ? "winter" : "")).toLowerCase();
      const v0 = num(+s.png_vmin_c), v1 = num(+s.png_vmax_c), lo = v0 != null && v1 > v0 ? v0 : fin[Math.floor(fin.length * 0.02)], hi = v0 != null && v1 > v0 ? v1 : fin[Math.min(fin.length - 1, Math.floor(fin.length * 0.98))];
      scenes.push({ w, h, v: vals, b, north, date, season, lo, hi: hi > lo ? hi : lo + 1, air: num(+s.air_temp_c), timeUtc: String(s.time_utc || ""), label: `${season ? season[0].toUpperCase() + season.slice(1) : "Scene"}${date ? " · " + date.slice(0, 10) : ""}` });
    }
    if (!scenes.length) return null;
    const rank = (s) => (s.season === "summer" ? 0 : s.season === "winter" ? 1 : 2); scenes.sort((a, b) => rank(a) - rank(b)); scenes.forEach((s, i) => (s.key = String(i)));
    return { scenes, cell, source: String(j.source || j.sensor || "Landsat 8/9 Collection 2 surface temperature (USGS, public domain)") };
  }

  // ------------------------------------------------------------ points (every return; never thinned)
  pointColor(i, mode, ph, pp) {
    const P = this.P, k = P.kind[i], it = P.inten[i] / 255;
    if (mode === "photo" && ph) return ph(P.pos[i * 3], P.pos[i * 3 + 1]);
    if (mode === "intensity") return ramp(0.08 + it * 0.92);
    if (mode === "height") return k === 0 ? mix(pp.g0, pp.g1, it * 0.6) : ramp(0.15 + P.hag[i] / 14);
    if (mode === "dev") return k === 1 ? devColor(P.dev[i], pp.dev) : k === 0 ? mix(pp.devGround, pp.g0, it) : shade(pp.t0, 0.5);
    if (k === 0) return mix(pp.g0, pp.g1, it);
    if (k === 1) return mix(pp.b0, pp.b1, Math.min(P.hag[i] / 10, 1));
    if (k === 2) return mix(pp.t0, pp.t1, Math.min(P.hag[i] / 18, 1));
    return pp.o;
  }
  photoSampler() {
    const im = this.latestImg(); if (!im) return null;
    if (this.cache.px?.im === im) return this.cache.px.f;
    const cv = document.createElement("canvas"); cv.width = im.width; cv.height = im.height; const cx = cv.getContext("2d"); cx.drawImage(im, 0, 0);
    const d = cx.getImageData(0, 0, cv.width, cv.height).data, w = cv.width, h = cv.height, half = this.half;
    const f = (x, y) => { const u = Math.min(w - 1, Math.max(0, Math.floor((x + half) / (2 * half) * w))), v = Math.min(h - 1, Math.max(0, Math.floor((half - y) / (2 * half) * h))); const o = (v * w + u) * 4; return [d[o], d[o + 1], d[o + 2]]; };
    this.cache.px = { im, f }; return f;
  }
  pointData(kindWanted, opt) {
    const ph = opt.color === "photo" ? this.photoSampler() : null, key = `pts${kindWanted}|${opt.color}|${TH()}|${ph ? "ph" : ""}`;
    if (!this.cache[key]) {
      for (const k of Object.keys(this.cache)) if (k.startsWith(`pts${kindWanted}|`)) delete this.cache[k];   // one colouring per class kept
      const P = this.P; let n = 0; for (let i = 0; i < P.n; i++) if (P.kind[i] === kindWanted) n++;
      const pos = new Float32Array(n * 3), col = new Uint8Array(n * 3), pp = pointPal(); let j = 0;
      for (let i = 0; i < P.n; i++) { if (P.kind[i] !== kindWanted) continue; pos[j * 3] = P.pos[i * 3]; pos[j * 3 + 1] = P.pos[i * 3 + 1]; pos[j * 3 + 2] = P.pos[i * 3 + 2];
        const c = this.pointColor(i, opt.color, ph, pp); col[j * 3] = c[0]; col[j * 3 + 1] = c[1]; col[j * 3 + 2] = c[2]; j++; }
      this.cache[key] = { length: n, attributes: { getPosition: { value: pos, size: 3 }, getColor: { value: col, size: 3, normalized: true } } };
    }
    return this.cache[key];
  }
  meshColors() {
    const key = "meshCol|" + TH(); if (this.cache[key]) return this.cache[key];
    const { C } = this.houseMesh, n = C.length / 4, c = new Float32Array(n * 3), roof = c3("roof"), wall = mix(shade(roof, 0.62), c3("sky"), 0.15);
    for (let i = 0; i < n; i++) { const v = C[i * 4 + 2] === 2 ? wall : roof; c[i * 3] = v[0] / 255; c[i * 3 + 1] = v[1] / 255; c[i * 3 + 2] = v[2] / 255; }
    return (this.cache[key] = c);
  }
  meshEdges() {
    if (this.cache.meshEdges) return this.cache.meshEdges;
    const { P: Pp, I } = this.houseMesh, map = new Map(), q = (i) => `${Math.round(Pp[i * 3] * 50)},${Math.round(Pp[i * 3 + 1] * 50)},${Math.round(Pp[i * 3 + 2] * 50)}`;
    for (let t = 0; t + 2 < I.length; t += 3) {
      const a = I[t], b = I[t + 1], c = I[t + 2];
      const ux = Pp[b * 3] - Pp[a * 3], uy = Pp[b * 3 + 1] - Pp[a * 3 + 1], uz = Pp[b * 3 + 2] - Pp[a * 3 + 2], vx = Pp[c * 3] - Pp[a * 3], vy = Pp[c * 3 + 1] - Pp[a * 3 + 1], vz = Pp[c * 3 + 2] - Pp[a * 3 + 2];
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx; const ln = Math.hypot(nx, ny, nz); if (!(ln > 1e-9)) continue; nx /= ln; ny /= ln; nz /= ln;
      for (const [u, v] of [[a, b], [b, c], [c, a]]) {
        const ku = q(u), kv = q(v); if (ku === kv) continue; const key = ku < kv ? ku + "|" + kv : kv + "|" + ku, e = map.get(key);
        if (!e) map.set(key, { u, v, n: [nx, ny, nz], cnt: 1, sharp: false }); else { e.cnt++; if (Math.abs(e.n[0] * nx + e.n[1] * ny + e.n[2] * nz) < 0.985) e.sharp = true; }
      }
    }
    const keep = [...map.values()].filter((e) => e.cnt === 1 || e.sharp), src = new Float32Array(keep.length * 3), tgt = new Float32Array(keep.length * 3);
    keep.forEach((e, j) => { src.set(Pp.subarray(e.u * 3, e.u * 3 + 3), j * 3); tgt.set(Pp.subarray(e.v * 3, e.v * 3 + 3), j * 3); });
    return (this.cache.meshEdges = { length: keep.length, attributes: { getSourcePosition: { value: src, size: 3 }, getTargetPosition: { value: tgt, size: 3 } } });
  }

  // ------------------------------------------------------------ selection geometry (whole-house highlight + boundary shimmer; no circle)
  housePoints(h) {
    const P = this.P, [x0, y0, x1, y1] = ringBox(h.ring, 0.7), idx = [];
    for (let i = 0; i < P.n; i++) { if (P.kind[i] !== 1) continue; const x = P.pos[i * 3], y = P.pos[i * 3 + 1]; if (x < x0 || x > x1 || y < y0 || y > y1) continue; if (inOrNear(x, y, h.ring, 0.6)) idx.push(i); }
    return idx;
  }
  ringRuns(ring, clipped) {
    const r = ring.filter((p) => Array.isArray(p) && isFinite(p[0]) && isFinite(p[1]));
    if (r.length < 3) return [];
    const pts = [...r]; if (pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
    const n = pts.length; if (!clipped) return [{ pts: [...pts, pts[0]], closed: true }];
    const lim = this.half - 0.5, inside = (p) => Math.abs(p[0]) <= lim && Math.abs(p[1]) <= lim, keep = pts.map((p, i) => inside(p) && inside(pts[(i + 1) % n]));
    if (keep.every(Boolean)) return [{ pts: [...pts, pts[0]], closed: true }];
    const start = keep.findIndex((k) => !k), runs = []; let cur = null;
    for (let k = 1; k <= n; k++) { const i = (start + k) % n;
      if (keep[i]) { if (!cur) cur = [pts[i]]; cur.push(pts[(i + 1) % n]); } else if (cur) { runs.push({ pts: cur, closed: false }); cur = null; } }
    if (cur) runs.push({ pts: cur, closed: false });
    return runs;
  }
  shimmerPath(ring, up, step = 0.4, clipped = false) {
    const runs = [], segs = []; let Ltot = 0;
    for (const run of this.ringRuns(ring, clipped)) {
      const dense = [], s = []; let L = 0; const p = run.pts;
      for (let i = 0; i + 1 < p.length; i++) {
        const [ax, ay] = p[i], [bx, by] = p[i + 1], len = Math.hypot(bx - ax, by - ay), n = Math.max(1, Math.ceil(len / step));
        for (let k = 0; k < n; k++) { const f = k / n, x = ax + (bx - ax) * f, y = ay + (by - ay) * f; dense.push([x, y, this.groundLow(x, y) + up]); s.push(L + len * f); }
        L += len;
      }
      const [ex, ey] = p[p.length - 1]; dense.push([ex, ey, this.groundLow(ex, ey) + up]); s.push(L);
      if (!(L > 0.5)) continue;
      const R = { full: dense, L, Lw: run.closed ? L : L + 10, nb: Math.max(1, Math.round(L / 30)) }; runs.push(R); Ltot += L;
      for (let i = 0; i + 1 < dense.length; i++) segs.push({ path: [dense[i], dense[i + 1]], s: (s[i] + s[i + 1]) / 2, R });
    }
    return runs.length ? { runs, segs, L: Ltot } : null;
  }
  houseMeshPart(h, gp) {
    if (!this.houseMesh) return null;
    const { P: Pp, N, C, I } = this.houseMesh, [x0, y0, x1, y1] = ringBox(h.ring, 0.5), tri = [];
    for (let t = 0; t + 2 < I.length; t += 3) {
      const a = I[t], b = I[t + 1], c = I[t + 2], cx = (Pp[a * 3] + Pp[b * 3] + Pp[c * 3]) / 3, cy = (Pp[a * 3 + 1] + Pp[b * 3 + 1] + Pp[c * 3 + 1]) / 3;
      if (cx < x0 || cx > x1 || cy < y0 || cy > y1) continue;
      if (inOrNear(cx, cy, h.ring, 0.3)) tri.push(a, b, c);
    }
    if (!tri.length) return null;
    const map = new Map(), pos = [], nrm = [], col = [], idx = new Uint32Array(tri.length), roof = lift(gp.mesh, 0.25), wall = shade(gp.mesh, 0.8);
    let zTop = -Infinity;
    tri.forEach((v, j) => {
      let k = map.get(v);
      if (k == null) { k = map.size; map.set(v, k); pos.push(Pp[v * 3], Pp[v * 3 + 1], Pp[v * 3 + 2]); nrm.push(N[v * 3], N[v * 3 + 1], N[v * 3 + 2]); const c = C[v * 4 + 2] === 2 ? wall : roof; col.push(c[0] / 255, c[1] / 255, c[2] / 255); zTop = Math.max(zTop, Pp[v * 3 + 2]); }
      idx[j] = k;
    });
    const me = this.meshEdges(), S = me.attributes.getSourcePosition.value, T_ = me.attributes.getTargetPosition.value, es = [], et = [];
    for (let e = 0; e < me.length; e++) { const mx = (S[e * 3] + T_[e * 3]) / 2, my = (S[e * 3 + 1] + T_[e * 3 + 1]) / 2; if (mx < x0 || mx > x1 || my < y0 || my > y1) continue;
      if (inOrNear(mx, my, h.ring, 0.3)) { es.push(S[e * 3], S[e * 3 + 1], S[e * 3 + 2]); et.push(T_[e * 3], T_[e * 3 + 1], T_[e * 3 + 2]); } }
    return { zTop, mesh: { attributes: { POSITION: { value: new Float32Array(pos), size: 3 }, NORMAL: { value: new Float32Array(nrm), size: 3 }, COLOR_0: { value: new Float32Array(col), size: 3 } }, indices: { value: idx, size: 1 } },
      edges: { length: es.length / 3, attributes: { getSourcePosition: { value: new Float32Array(es), size: 3 }, getTargetPosition: { value: new Float32Array(et), size: 3 } } } };
  }
  parcelOf(h) {
    const PAR = this.PAR; if (!PAR?.parcels.length || !h) return null;
    if (h.parcel_id != null && PAR.byId.has(String(h.parcel_id))) return { p: PAR.byId.get(String(h.parcel_id)), how: "id" };
    if (h.parcel_id != null) return null;
    const [cx, cy] = centroid(h.ring), hit = PAR.parcels.filter((p) => inRing(cx, cy, p.ring));
    return hit.length === 1 ? { p: hit[0], how: "loc" } : null;
  }
  hoaOf(h) { const PAR = this.PAR; if (!PAR?.hoa.length || !h) return []; const [cx, cy] = centroid(h.ring);
    return PAR.hoa.filter((a) => (Array.isArray(a.houses) && h.id != null ? a.houses.some((v) => String(v) === String(h.id)) : inRing(cx, cy, a.ring))).sort((a, b) => "hcpo".indexOf(a.k[0]) - "hcpo".indexOf(b.k[0])); }
  srcName(code) { const l = arr(this.PAR?.meta?.licences).find((x) => x?.source === code); return l ? `${l.name || code}${l.licence ? " (" + l.licence + ")" : ""}` : String(code || ""); }
  selPrep(h) {
    const P = this.P, K = themeColors(), gp = glowPal(K), idx = this.housePoints(h), n = idx.length, pos = new Float32Array(n * 3), base = new Uint8Array(n * 3), tz = new Float32Array(n);
    let zl = Infinity, zh = -Infinity; for (const i of idx) { const z = P.pos[i * 3 + 2]; zl = Math.min(zl, z); zh = Math.max(zh, z); }
    const g0 = Math.min(zl, ...h.ring.map(([x, y]) => this.gz(x, y))), lo = shade(gp.pts, 0.78), hi = lift(gp.pts, 0.5);
    idx.forEach((i, j) => { pos.set(P.pos.subarray(i * 3, i * 3 + 3), j * 3); const t = zh > g0 ? (P.pos[i * 3 + 2] - g0) / (zh - g0) : 0.5; tz[j] = t;
      const c = mix(lo, hi, 0.25 + 0.75 * t); base[j * 3] = c[0]; base[j * 3 + 1] = c[1]; base[j * 3 + 2] = c[2]; });
    const part = this.houseMeshPart(h, gp), pr = this.parcelOf(h);
    return { h, gp, n, pos, base, col: new Uint8Array(n * 3), tz, zTop: Math.max(zh, part?.zTop ?? -Infinity), part, parcel: pr ? this.shimmerPath(pr.p.ring, 0.12, 0.6, !!pr.p.clipped) : null, lot: pr ? pr.p.ring.map(([x, y]) => [x, y, this.groundLow(x, y) + 0.06]) : null, t0: performance.now() };
  }
  selectionLayers(base, X_, opt) {
    const dk = D(), t = (performance.now() - X_.t0) / 1000, gp = X_.gp, out = [], id = (s) => `blk:${this.slug}:${s}`;
    const pulse = 0.5 + 0.5 * Math.sin(t * 2.4);
    if (X_.n) {
      const col = X_.col, b = ((t / 2.6) % 1) * 1.5 - 0.25, B = X_.base, Z = X_.tz;
      for (let j = 0; j < X_.n; j++) { const d = (Z[j] - b) / 0.09, f = 0.6 * Math.exp(-d * d); const o = j * 3;
        col[o] = B[o] + (255 - B[o]) * f; col[o + 1] = B[o + 1] + (255 - B[o + 1]) * f; col[o + 2] = B[o + 2] + (255 - B[o + 2]) * f; }
      out.push(new dk.PointCloudLayer({ ...base, id: id("selpts"), data: { length: X_.n, attributes: { getPosition: { value: X_.pos, size: 3 }, getColor: { value: col, size: 3, normalized: true } } },
        pointSize: opt.size + 1, sizeUnits: "pixels", updateTriggers: { getColor: Math.round(t * 60) } }));
    }
    if (X_.part) {
      out.push(new dk.SimpleMeshLayer({ ...base, id: id("selmesh"), data: [0], mesh: X_.part.mesh, getPosition: [0, 0, 0.03], getColor: [255, 255, 255, 255],
        opacity: 0.3 + 0.18 * pulse, material: { ambient: 0.95, diffuse: 0.35, shininess: 40, specularColor: [255, 255, 255] }, parameters: { depthWriteEnabled: false } }));
      if (X_.part.edges.length) out.push(new dk.LineLayer({ ...base, id: id("seledges"), data: X_.part.edges, getColor: A_(lift(gp.mesh, 0.55), 150 + 90 * pulse), getWidth: 1.6, widthUnits: "pixels", updateTriggers: { getColor: Math.round(pulse * 40) } }));
    }
    const glowBlend = { depthWriteEnabled: false, blend: true, blendColorOperation: "add", blendColorSrcFactor: "src-alpha", blendColorDstFactor: "one", blendAlphaOperation: "add", blendAlphaSrcFactor: "one", blendAlphaDstFactor: "one-minus-src-alpha" };
    const shimmer = (nm, sp, col, w) => {
      if (!sp) return;
      const key = Math.round(t * 60), I = (d) => bandI(d.s, d.R.Lw, t, d.R.nb);
      out.push(new dk.PathLayer({ ...base, id: id(nm + "-base"), data: sp.runs, getPath: (d) => d.full, getColor: A_(col, 95), getWidth: w * 0.8, widthUnits: "pixels", jointRounded: true, parameters: { depthWriteEnabled: false } }));
      out.push(new dk.PathLayer({ ...base, id: id(nm + "-halo"), data: sp.segs, getPath: (d) => d.path, getColor: (d) => A_(col, Math.round(150 * I(d) ** 1.5)), getWidth: w * 4.5, widthUnits: "pixels", parameters: glowBlend, updateTriggers: { getColor: key } }));
      out.push(new dk.PathLayer({ ...base, id: id(nm + "-core"), data: sp.segs, getPath: (d) => d.path, getColor: (d) => { const i = I(d); return A_(lift(col, 0.65 * i), Math.round(60 + 195 * i)); }, getWidth: w, widthUnits: "pixels", parameters: { depthWriteEnabled: false }, updateTriggers: { getColor: key } }));
    };
    // the property = the whole lot (building, yard, drive, parking), shaded on the ground; only from real parcel lines
    if (X_.lot) out.push(new dk.PolygonLayer({ ...base, id: id("sellot"), data: [X_.lot], getPolygon: (r) => r, filled: true, stroked: false,
      getFillColor: A_(gp.house, Math.round(34 + 16 * pulse)), parameters: { depthWriteEnabled: false }, updateTriggers: { getFillColor: Math.round(pulse * 20) } }));
    shimmer("selparcel", X_.parcel, gp.house, 2.2);
    // pin label: only this house's own value (never a neighbourhood median)
    const h = X_.h, g0 = Math.min(...h.ring.map(([x, y]) => this.gz(x, y))), top = Math.max(g0 + (num(h.height_m) ?? 6), X_.zTop), [cx, cy] = centroid(h.ring), K = themeColors();
    out.push(new dk.LineLayer({ ...base, id: id("pinline"), data: [0], getSourcePosition: [cx, cy, top + 0.5], getTargetPosition: [cx, cy, top + 9], getColor: A_(K.pin, 230), getWidth: 2 }));
    const own = ownValue(h), price = own ? "$" + fmt.format(own) : "";
    out.push(new dk.TextLayer({ ...base, id: id("pin"), data: [0], getPosition: [cx, cy, top + 9.5], getText: () => `${h.address || "Building"}${price ? "  ·  " + price : ""}`,
      getSize: 15, sizeUnits: "pixels", characterSet: "auto", getColor: A_(K.sky, 255), background: true, getBackgroundColor: A_(K.sel, 245), backgroundPadding: [8, 5],
      fontFamily: globalThis.Themes?.current?.().css?.["f-display"] || "sans-serif", fontWeight: 700, getPixelOffset: [0, -6], updateTriggers: { getText: h } }));
    return out;
  }

  // ------------------------------------------------------------ street furniture meshes (records only)
  buildFurnMesh() {
    const B = meshBuilder(), Lb = meshBuilder(), pole = [112, 114, 118], dark = [26, 28, 29], F_ = this.FURN;
    const housing = (x, zt) => { B.box(x - 0.31, x + 0.31, -0.2, -0.16, zt - 1.2, zt + 0.06, [16, 16, 16]).box(x - 0.18, x + 0.18, -0.16, 0.12, zt - 1.12, zt - 0.02, dark);
      [[zt - 0.22, [92, 22, 20]], [zt - 0.57, [96, 70, 16]], [zt - 0.92, [18, 74, 44]]].forEach(([z, col]) => { B.prismY(ngon(12, 0.11, z), 0.12, 0.14, col).box(x - 0.13, x + 0.13, 0.12, 0.32, z + 0.11, z + 0.13, dark); }); };
    for (const s of F_.signals) {   // pole at the recorded point; housing with all lamps dim (no live state data)
      const top = Math.max(3.5, Math.min(9, s.h || 5));
      B.at({ ...s, heading: s.heading ?? 0 }).cylZ(0, 0, 0, top, 0.12, 12, pole).box(-0.22, 0.22, -0.22, 0.22, 0, 0.45, pole);
      if (s.oriented) housing(0, top + 1.15);
      else for (const hd of [0, 90, 180, 270]) { B.at({ ...s, heading: hd }); housing(0, top + 1.15); }
    }
    const panels = F_.signs.filter((s) => s.oriented), cells = [...new Set(panels.map((s) => (s.kind === "speed" ? "speed:" + (s.value ?? "") : s.kind)))], P_ = [], N_ = [], U_ = [], I_ = [], nC = cells.length;
    const face = (o, shape, cz, cell) => {
      const ci = cells.indexOf(cell), h = o.heading * RAD, f = [Math.sin(h), Math.cos(h)], r = [Math.cos(h), -Math.sin(h)], y = 0.075, ext = shape.reduce((m, [a, b]) => Math.max(m, Math.abs(a), Math.abs(b)), 0);
      const W = (lx, lz) => [o.x + lx * r[0] + y * f[0], o.y + lx * r[1] + y * f[1], o.z + lz], k0 = P_.length / 3;
      const push = (p, u, v) => { P_.push(p[0], p[1], p[2]); N_.push(f[0], f[1], 0); U_.push((ci + u) / nC, v); };
      push(W(0, cz), 0.5, 0.5); for (const [a, b] of shape) push(W(a, cz + b), 0.5 - a / (2 * ext), 0.5 - b / (2 * ext));
      for (let i = 0; i < shape.length; i++) I_.push(k0, k0 + 1 + i, k0 + 1 + ((i + 1) % shape.length));
    };
    for (const s of F_.signs) {
      const zc = s.kind === "speed" ? 2.25 : 2.1, back = [170, 172, 176];
      B.at({ ...s, heading: s.heading ?? 0 }).cylZ(0, 0, 0, s.oriented ? zc + 0.3 : Math.max(2, s.h || 2.4), 0.035, 8, [150, 152, 156]);
      if (!s.oriented) continue;
      if (s.kind === "stop") { B.prismY(ngon(8, 0.41, zc), 0.03, 0.07, back); face(s, ngon(8, 0.41, 0), zc, "stop"); }
      else if (s.kind === "yield") { const sh = [[-0.46, 0.3], [0.46, 0.3], [0, -0.5]]; B.prismY(sh.map(([a, b]) => [a, zc + b]), 0.03, 0.07, back); face(s, sh, zc, "yield"); }
      else { const sh = [[-0.3, 0.38], [0.3, 0.38], [0.3, -0.38], [-0.3, -0.38]]; B.prismY(sh.map(([a, b]) => [a, zc + b]), 0.03, 0.07, back); face(s, sh, zc, "speed:" + (s.value ?? "")); }
    }
    F_.faces = I_.length ? { mesh: { attributes: { POSITION: { value: new Float32Array(P_), size: 3 }, NORMAL: { value: new Float32Array(N_), size: 3 }, TEXCOORD_0: { value: new Float32Array(U_), size: 2 } }, indices: { value: new Uint32Array(I_), size: 1 } }, tex: faceAtlas(cells) } : null;
    for (const l of F_.lamps) {
      const t = Math.max(3, Math.min(14, l.h || 7)), o = { ...l, heading: l.heading ?? 0 };
      B.at(o).box(-0.16, 0.16, -0.16, 0.16, 0, 0.55, [84, 86, 90]).cylZ(0, 0, 0, t, 0.085, 10, [96, 98, 102]);
      if (l.oriented) { B.box(-0.045, 0.045, 0, 1.9, t - 0.22, t - 0.12, [96, 98, 102]).box(-0.2, 0.2, 1.45, 2.25, t - 0.36, t - 0.2, [58, 60, 64]); Lb.at(o).box(-0.16, 0.16, 1.5, 2.2, t - 0.4, t - 0.36, [255, 255, 255]); }
      else { B.box(-0.22, 0.22, -0.22, 0.22, t + 0.38, t + 0.46, [58, 60, 64]); Lb.at(o).box(-0.17, 0.17, -0.17, 0.17, t, t + 0.38, [255, 255, 255]); }
    }
    for (const c of F_.cams) {
      const t = Math.max(2.5, Math.min(12, c.h || 5)), arm = c.oriented ? 0.6 : 0;
      B.at({ ...c, heading: c.heading ?? 0 }).cylZ(0, 0, 0, t, 0.07, 10, [120, 122, 126]); if (arm) B.box(-0.04, 0.04, 0, arm, t - 0.12, t - 0.04, [120, 122, 126]);
      B.box(-0.1, 0.1, arm - 0.1, arm + 0.1, t - 0.16, t - 0.04, [200, 200, 200]);
      for (let k = 0; k < 4; k++) { const a0 = (k / 4) * (Math.PI / 2), a1 = ((k + 1) / 4) * (Math.PI / 2); B.cylZ(0, arm, t - 0.16 - 0.13 * Math.sin(a1), t - 0.16 - 0.13 * Math.sin(a0), Math.max(0.03, 0.13 * Math.cos(a0)), 12, [30, 32, 36]); }
    }
    for (const hy of F_.hydrants) B.at({ ...hy, heading: hy.heading ?? 0 }).cylZ(0, 0, 0, 0.12, 0.16, 12, [150, 20, 20]).cylZ(0, 0, 0.12, 0.62, 0.11, 12, [196, 30, 28]).cylZ(0, 0, 0.62, 0.74, 0.13, 12, [176, 26, 24]).cylZ(0, 0, 0.74, 0.82, 0.05, 8, [176, 26, 24]);
    for (const p of F_.poles) {
      const t = Math.max(2, Math.min(22, p.h || (p.kind === "upole" ? 10 : 4))), o = { ...p, heading: p.heading ?? 0 };
      if (p.kind === "upole") { B.at(o).cylZ(0, 0, 0, t, 0.14, 10, [104, 84, 62]); if (p.oriented) B.box(-1.1, 1.1, -0.06, 0.06, t - 0.7, t - 0.58, [96, 78, 58]); }
      else B.at(o).cylZ(0, 0, 0, t, 0.08, 10, [128, 130, 134]);
    }
    F_.mesh = B.mesh(); F_.lens = Lb.mesh(); F_.built = true;
  }
  roadMesh() {
    const img = this.latestImg(); if (!img) return null;
    if (this.cache.roadMesh !== undefined && this.cache.roadMeshImg === img) return this.cache.roadMesh;
    const half = this.half, P_ = [], N_ = [], U_ = [], I_ = [];
    for (const r of this.STR.roads) { const ring = Array.isArray(r.P[0]?.[0]) ? r.P[0] : r.P; r.inPhoto = ring.every((p) => Math.abs(p[0]) <= half && Math.abs(p[1]) <= half); if (!r.inPhoto) continue;
      const k0 = P_.length / 3, tri = earcut2(ring); if (!tri.length) { r.inPhoto = false; continue; }
      for (const p of ring) { P_.push(p[0], p[1], p[2]); N_.push(0, 0, 1); U_.push((p[0] + half) / (2 * half), (half - p[1]) / (2 * half)); }
      for (const t of tri) I_.push(k0 + t); }
    this.cache.roadMeshImg = img;
    return (this.cache.roadMesh = I_.length ? { attributes: { POSITION: { value: new Float32Array(P_), size: 3 }, NORMAL: { value: new Float32Array(N_), size: 3 }, TEXCOORD_0: { value: new Float32Array(U_), size: 2 } }, indices: { value: new Uint32Array(I_), size: 1 } } : null);
  }
  lstCanvas(sc, crop) {
    const key = `lstcv|${TH()}|${sc.key}|${crop ? 1 : 0}`; if (this.cache[key]) return this.cache[key];
    const lstRamp = (v) => ramp(0.08 + 0.9 * clamp01((v - sc.lo) / (sc.hi - sc.lo || 1)));
    const lstAt = (x, y) => { const [x0, y0, x1, y1] = sc.b; if (x < x0 || x >= x1 || y < y0 || y >= y1) return NaN;
      const c = Math.min(sc.w - 1, Math.floor(((x - x0) / (x1 - x0)) * sc.w)), r = Math.min(sc.h - 1, Math.floor((sc.north ? (y1 - y) : (y - y0)) / (y1 - y0) * sc.h)); return sc.v[r * sc.w + c]; };
    const cv = document.createElement("canvas"), n = crop ? 240 : 0; cv.width = crop ? n : sc.w; cv.height = crop ? n : sc.h;
    const cx = cv.getContext("2d"), im = cx.createImageData(cv.width, cv.height), half = this.half;
    for (let r = 0; r < cv.height; r++) for (let c = 0; c < cv.width; c++) {
      const v = crop ? lstAt(-half + ((c + 0.5) / n) * 2 * half, half - ((r + 0.5) / n) * 2 * half) : sc.v[(sc.north ? r : sc.h - 1 - r) * sc.w + c], o = (r * cv.width + c) * 4;
      if (v === v) { const k = lstRamp(v); im.data[o] = k[0]; im.data[o + 1] = k[1]; im.data[o + 2] = k[2]; im.data[o + 3] = 235; }
    }
    cx.putImageData(im, 0, 0); return (this.cache[key] = cv);
  }

  // ------------------------------------------------------------ deck layers for this block
  layers(mgr) {
    const dk = D(), opt = mgr.options, CS = dk.COORDINATE_SYSTEM.METER_OFFSETS, O = [this.lon0, this.lat0, mgr._zOff(this)], K = themeColors(), th = TH();
    const meta = { blockSlug: this.slug }, base = { coordinateSystem: CS, coordinateOrigin: O, ...meta }, id = (s) => `blk:${this.slug}:${s}`, ly = [];
    const ut = { getColor: th, getFillColor: th, getLineColor: th };
    const sel = mgr.sel?.slug === this.slug ? mgr.sel : null, dim = (mgr.sel && opt.dimOthersOnSelect);
    const gz = (x, y) => this.groundLocal(x, y), gl = (x, y) => this.groundLow(x, y), half = this.half, clipB = [-half, -half, half, half];
    const thermalOn = opt.thermal && this.LST;
    // 1. surfaces under the points
    if (thermalOn) {
      const sc = this.LST.scenes[Math.min(this.LST.scenes.length - 1, opt.thermalScene | 0)], [x0, y0, x1, y1] = sc.b, tp = { minFilter: "nearest", magFilter: "nearest" };
      if (this.cache.gmin == null) { let m = Infinity; const P = this.P; for (let i = 0; i < P.n; i++) if (P.kind[i] === 0 && P.pos[i * 3 + 2] < m) m = P.pos[i * 3 + 2]; this.cache.gmin = m === Infinity ? 0 : m; }
      const zb = this.cache.gmin - 0.35;
      ly.push(new dk.BitmapLayer({ ...base, id: id("lst-bmp-" + sc.key), image: this.lstCanvas(sc, false), bounds: [[x0, y0, zb], [x0, y1, zb], [x1, y1, zb], [x1, y0, zb]], opacity: 0.9, textureParameters: tp }));
      if (this.terrainMesh) ly.push(new dk.SimpleMeshLayer({ ...base, id: id("lst-drape-" + sc.key), data: [0], mesh: this.terrainMesh, texture: this.lstCanvas(sc, true), textureParameters: tp, getPosition: [0, 0, -0.12], material: { ambient: 0.85, diffuse: 0.2 }, getColor: [255, 255, 255], opacity: 0.92 }));
    } else if (opt.photo && this.terrainMesh && this.latestImg()) {
      ly.push(new dk.SimpleMeshLayer({ ...base, id: id("terrain-photo"), data: [0], mesh: this.terrainMesh, texture: this.latestImg(), getPosition: [0, 0, -0.15], material: { ambient: 0.8, diffuse: 0.3 }, getColor: [255, 255, 255] }));
    }
    const S = this.STR;
    if (S && S.validated && opt.streets) {   // only streets the producer validated against the LiDAR footprints
      const mat = { ambient: 0.75, diffuse: 0.35 }, b2 = { ...base, updateTriggers: ut };
      const asph = themeTone([47, 49, 53], 0.55), asphL = themeTone([68, 68, 70], 0.55), walkC = themeTone([150, 150, 145], 0.55), paint = themeTone([226, 226, 220], 0.6), yel = themeTone([232, 182, 34], 0.6);
      if (S.sidewalks.length) ly.push(new dk.SolidPolygonLayer({ ...b2, id: id("st-walks"), data: S.sidewalks, getPolygon: (s) => s.P, getFillColor: walkC, material: mat }));
      if (S.drives.length) ly.push(new dk.SolidPolygonLayer({ ...b2, id: id("st-drives"), data: S.drives, getPolygon: (s) => s.P, getFillColor: mix(walkC, asph, 0.35), material: mat }));
      if (S.curbs.length) ly.push(new dk.PathLayer({ ...b2, id: id("st-curbs"), data: S.curbs, getPath: (c) => c.path, getWidth: 0.18, widthUnits: "meters", widthMinPixels: 0.5, getColor: lift(walkC, 0.2) }));
      const rm = this.roadMesh(), img = this.latestImg();
      if (rm) ly.push(new dk.SimpleMeshLayer({ ...b2, id: id("st-roads-photo"), data: [0], mesh: rm, texture: img, getPosition: [0, 0, 0], getColor: th === "hud" ? [255, 255, 255] : lift(c3("road"), 0.6), material: mat }));
      const roads = rm ? S.roads.filter((r) => !r.inPhoto) : S.roads;
      if (roads.length) ly.push(new dk.SolidPolygonLayer({ ...b2, id: id("st-roads"), data: roads, getPolygon: (r) => r.P, getFillColor: (r) => (/alley|service|parking|drive/i.test(String(r.kind || "")) ? asphL : asph), material: mat, pickable: true, blockRole: "road" }));
      if (S.zebra.length) ly.push(new dk.SolidPolygonLayer({ ...b2, id: id("st-zebra"), data: S.zebra, getPolygon: (p) => p, getFillColor: paint, material: mat }));
      const solid = S.lines.filter((l) => !l.dashed), dashed = S.lines.filter((l) => l.dashed);
      if (solid.length) ly.push(new dk.PathLayer({ ...b2, id: id("st-lines"), data: solid, getPath: (l) => l.path, getWidth: (l) => l.w, widthUnits: "meters", getColor: (l) => (l.yel ? yel : paint) }));
      if (dashed.length) ly.push(new dk.PathLayer({ ...b2, id: id("st-dash"), data: dashed, getPath: (l) => l.path, getWidth: (l) => l.w, widthUnits: "meters", getColor: (l) => (l.yel ? yel : paint), getDashArray: (l) => [3 / l.w, 9 / l.w], dashJustified: false, extensions: [new dk.PathStyleExtension({ dash: true })] }));
      if (S.stops.length) ly.push(new dk.PathLayer({ ...b2, id: id("st-stops"), data: S.stops, getPath: (s) => s.path, getWidth: (s) => s.w, widthUnits: "meters", getColor: paint }));
    }
    // 2. parcels + HOA (draped on the LiDAR ground)
    const PAR = this.PAR;
    if (PAR && opt.parcels && PAR.parcels.length) {
      if (!this.cache.parcelRuns) this.cache.parcelRuns = PAR.parcels.flatMap((p) => this.ringRuns(p.ring, !!p.clipped).map((r) => ({ p, path: r.pts.map(([x, y]) => [x, y, gl(x, y) + 0.1]) })));
      ly.push(new dk.PathLayer({ ...base, id: id("parcels"), data: this.cache.parcelRuns, getPath: (d) => d.path, getColor: A_(lift(K.accent, 0.3), 120), getWidth: 1, widthUnits: "pixels", parameters: { depthWriteEnabled: false }, pickable: false, updateTriggers: ut }));
    }
    if (PAR && opt.hoa && PAR.hoa.length) {
      if (!this.cache.hoaRuns) this.cache.hoaRuns = PAR.hoa.flatMap((a) => this.ringRuns(a.ring, !!a.clipped).map((r) => ({ a, path: r.pts.map(([x, y]) => [x, y, gl(x, y) + 0.15]) })));
      ly.push(new dk.PolygonLayer({ ...base, id: id("hoa"), data: PAR.hoa, getPolygon: (a) => a.ring.map(([x, y]) => [x, y, gl(x, y) + 0.12]), filled: true, stroked: false,
        getFillColor: (a) => A_(hoaCol(a.k, K), a.k === "plat" ? 26 : 42), pickable: true, blockRole: "hoa", parameters: { depthWriteEnabled: false }, updateTriggers: ut, extensions: [new dk.ClipExtension()], clipBounds: clipB }));
      ly.push(new dk.PathLayer({ ...base, id: id("hoa-line"), data: this.cache.hoaRuns, getPath: (d) => d.path, getColor: (d) => A_(hoaCol(d.a.k, K), 210), getWidth: (d) => (d.a.k === "plat" ? 1.5 : 2.2), widthUnits: "pixels", parameters: { depthWriteEnabled: false }, updateTriggers: ut }));
    }
    // 2b. footpaths (draped on the LiDAR ground; measured outlines and mapped lines only, no guessed widths)
    const WK = this.WALK;
    if (WK && opt.footpaths) {
      const wc = lift(K.accent, 0.55);
      if (WK.polys.length) {
        ly.push(new dk.PolygonLayer({ ...base, id: id("walk-area"), data: WK.polys, getPolygon: (w) => w.ring.map(([x, y]) => [x, y, gl(x, y) + 0.08]), filled: true, stroked: false,
          getFillColor: (w) => A_(/cross/.test(w.kind) ? lift(K.accent, 0.2) : wc, 55), pickable: true, blockRole: "walk", parameters: { depthWriteEnabled: false }, updateTriggers: ut }));
        ly.push(new dk.PathLayer({ ...base, id: id("walk-edge"), data: WK.polys, getPath: (w) => [...w.ring, w.ring[0]].map(([x, y]) => [x, y, gl(x, y) + 0.1]), getColor: A_(wc, 200), getWidth: 1.2, widthUnits: "pixels", parameters: { depthWriteEnabled: false }, updateTriggers: ut }));
      }
      if (WK.lines.length) ly.push(new dk.PathLayer({ ...base, id: id("walk-line"), data: WK.lines, getPath: (l) => l.path.map(([x, y]) => [x, y, gl(x, y) + 0.12]), getColor: (l) => A_(wc, l.city ? 230 : 150), getWidth: (l) => (l.city ? 2.4 : 1.6), widthUnits: "pixels",
        pickable: true, blockRole: "walk", parameters: { depthWriteEnabled: false }, updateTriggers: ut }));
    }
    // 3. furniture (records only)
    if (this.FURN && opt.furniture) {
      const F_ = this.FURN; if (!F_.built) this.buildFurnMesh();
      const tint = th === "hud" ? [255, 255, 255] : lift(c3("accent"), 0.45);
      if (F_.mesh) ly.push(new dk.SimpleMeshLayer({ ...base, id: id("furn"), data: [0], mesh: F_.mesh, getPosition: [0, 0, 0], getColor: tint, material: { ambient: 0.62, diffuse: 0.5 }, updateTriggers: ut }));
      if (F_.faces) ly.push(new dk.SimpleMeshLayer({ ...base, id: id("furn-faces"), data: [0], mesh: F_.faces.mesh, texture: F_.faces.tex, getPosition: [0, 0, 0], getColor: tint, material: { ambient: 0.8, diffuse: 0.35, shininess: 8, specularColor: [20, 20, 20] }, updateTriggers: ut }));
      if (F_.lens) ly.push(new dk.SimpleMeshLayer({ ...base, id: id("furn-lens"), data: [0], mesh: F_.lens, getPosition: [0, 0, 0], getColor: [150, 150, 146] }));
      if (F_.marks.length) ly.push(new dk.PathLayer({ ...base, id: id("furn-marks"), data: F_.marks, getPath: (m) => { const sh = m.kind === "stop" ? ngon(8, 0.6, 0) : [[-0.6, 0.35], [0.6, 0.35], [0, -0.7]]; return [...sh, sh[0]].map(([a, b]) => [m.x + a, m.y + b, m.z + 0.05]); },
        getColor: (m) => (m.kind === "stop" ? [200, 30, 36, 230] : [235, 235, 235, 230]), getWidth: 0.08, widthUnits: "meters", widthMinPixels: 1, pickable: true, blockRole: "furn" }));
      const pk = [...F_.items, ...F_.marks];
      if (pk.length) ly.push(new dk.ScatterplotLayer({ ...base, id: id("furn-pick"), data: pk, getPosition: (o) => [o.x, o.y, o.z + (o.note.startsWith("Stop control") || o.note.startsWith("Give-way control") ? 0.2 : o.kind === "hydrant" ? 0.5 : 1.8)], radiusUnits: "meters", getRadius: 0.5, billboard: true, getFillColor: [0, 0, 0, 1], pickable: true, blockRole: "furn" }));
    }
    // 4. every LiDAR point (all classes the user keeps on), fine round points
    const popa = dim ? (sel ? 0.28 : 0.6) : 1;
    for (const [k, on, nm] of [[0, opt.ground, "pg"], [1, opt.house, "ph"], [2, opt.veg, "pv"], [3, opt.other, "po"]]) {
      if (!on) continue; const data = this.pointData(k, opt); if (!data.length) continue;
      ly.push(new dk.PointCloudLayer({ ...base, id: id(nm), data, pointSize: opt.size, sizeUnits: "pixels", opacity: popa, material: false }));
    }
    // 5. roof models
    if (opt.model && this.houseMesh) {
      const { P: Pp, N, I } = this.houseMesh;
      ly.push(new dk.SimpleMeshLayer({ ...base, id: id("models"), data: [0], getPosition: [0, 0, 0], getColor: [255, 255, 255, 150], opacity: 0.55,
        mesh: { attributes: { POSITION: { value: Pp, size: 3 }, NORMAL: { value: N, size: 3 }, COLOR_0: { value: this.meshColors(), size: 3 } }, indices: { value: I, size: 1 } }, material: { ambient: 0.45, diffuse: 0.7 }, updateTriggers: ut }));
      ly.push(new dk.LineLayer({ ...base, id: id("modeledges"), data: this.meshEdges(), getColor: A_(K.edge, 170), getWidth: 1, widthUnits: "pixels", updateTriggers: ut }));
    }
    // 6. records at their recorded points
    const L = this.L;
    if (opt.permits && L.permits.length) {
      const rp = L.permits.filter((p) => /reroof|re-roof|re roof|tear off|shingle|new roof/i.test((p.work || "") + " " + (p.comments || "")) && !/solar/i.test(p.comments || ""));
      const sp = L.permits.filter((p) => /solar/i.test(p.comments || ""));
      const data = [...rp.map((p) => ({ ...p, k: "roof" })), ...sp.map((p) => ({ ...p, k: "solar" }))].filter((p) => isFinite(+p.x) && isFinite(+p.y));
      if (data.length) ly.push(new dk.ScatterplotLayer({ ...base, id: id("permits"), data, getPosition: (c) => [c.x, c.y, gz(c.x, c.y) + 9], getRadius: 6, radiusUnits: "pixels", getFillColor: (c) => A_(c.k === "roof" ? K.accent : K.ok, 240), stroked: true, getLineColor: A_(K.sky, 255), lineWidthUnits: "pixels", getLineWidth: 1, lineWidthMinPixels: 1, pickable: true, blockRole: "permit", updateTriggers: ut }));
    }
    if (opt.c311 && L.cases311.length) ly.push(new dk.ScatterplotLayer({ ...base, id: id("c311"), data: L.cases311.filter((c) => isFinite(+c.x) && isFinite(+c.y)), getPosition: (c) => [c.x, c.y, gz(c.x, c.y) + 5], getRadius: 4, radiusUnits: "pixels", getFillColor: A_(K.warn, 230), stroked: true, getLineColor: A_(K.sky, 255), lineWidthUnits: "pixels", getLineWidth: 1, pickable: true, blockRole: "c311", updateTriggers: ut }));
    if (opt.trees && this.T.length) ly.push(new dk.ScatterplotLayer({ ...base, id: id("trees"), data: this.T, getPosition: (t) => [t.x, t.y, gz(t.x, t.y) + (+t.h || 0) + 0.5],
      getRadius: (t) => +t.r || 2, radiusUnits: "meters", filled: true, getFillColor: (t) => (/^fraxinus/i.test(t.genus || "") ? A_(K.danger, 70) : A_(lift(K.tree, 0.2), 40)), stroked: true,
      getLineColor: (t) => (t.genus ? A_(lift(K.tree, 0.55), 220) : A_(lift(K.tree, 0.3), 120)), lineWidthUnits: "pixels", getLineWidth: 1, lineWidthMinPixels: 1, pickable: true, blockRole: "tree", updateTriggers: ut }));
    // 7. street photos (Mapillary / KartaView) at their capture points
    if (opt.streetPhotos && this.SIMG.length) {
      const top = (i) => [i.x, i.y, i.z + 2.6];
      ly.push(new dk.LineLayer({ ...base, id: id("simg-stems"), data: this.SIMG, getSourcePosition: (i) => [i.x, i.y, i.z + 0.1], getTargetPosition: top, getColor: A_(K.accent, 140), getWidth: 1, widthUnits: "pixels", updateTriggers: ut }));
      ly.push(new dk.LineLayer({ ...base, id: id("simg-dir"), data: this.SIMG.filter((i) => i.heading_deg != null), getSourcePosition: top, getTargetPosition: (i) => [i.x + 1.4 * Math.sin(i.heading_deg * RAD), i.y + 1.4 * Math.cos(i.heading_deg * RAD), i.z + 2.6], getColor: A_(K.accent, 220), getWidth: 2, widthUnits: "pixels", updateTriggers: ut }));
      ly.push(new dk.ScatterplotLayer({ ...base, id: id("simg-pins"), data: this.SIMG, getPosition: top, radiusUnits: "pixels", getRadius: 4, billboard: true,
        getFillColor: (i) => A_(i.thumb ? K.accent : shade(K.accent, 0.45), 235), stroked: true, getLineColor: [255, 255, 255, 230], lineWidthUnits: "pixels", getLineWidth: 1, lineWidthMinPixels: 1, pickable: true, blockRole: "photo", updateTriggers: ut }));
    }
    // 8. invisible house volumes for picking
    if (this.H.length) {
      if (!this.cache.pickRings) this.cache.pickRings = new Map(this.H.map((h) => { const g0 = Math.min(...h.ring.map(([x, y]) => gz(x, y))); return [h, h.ring.map(([x, y]) => [x, y, g0])]; }));
      const pr = this.cache.pickRings;
      ly.push(new dk.PolygonLayer({ ...base, id: id("pick"), data: this.H, getPolygon: (h) => pr.get(h), pickable: true, extruded: true, getElevation: (h) => (num(h.height_m) ?? 6) + 2,
        getFillColor: [0, 0, 0, 0], stroked: false, parameters: { depthWriteEnabled: false }, blockRole: "house" }));
    }
    // 9. selection: the house's own points tinted with a rising light sweep, its mesh glowing, a light band along the footprint and parcel
    if (sel) ly.push(...this.selectionLayers(base, sel.X, opt));
    return ly;
  }

  // ------------------------------------------------------------ panel: block summary
  lidarYear() { const M = this.M; return M.lidar_year || (String(M.lidar || "").match(/(19|20)\d\d/) || [])[0] || ""; }
  photoSrc() { const M = this.M; return M.photo_source || (arr(M.img_years).length ? "NAIP" : M.aerial ? String(M.aerial).replace(/\s*USGSImageryOnly/, "") : "") || "aerial"; }
  leadOf(h) {
    const M = this.M, c = h?.cleaning && typeof h.cleaning === "object" ? h.cleaning : null; if (!c) return null;
    const p = c.pool && typeof c.pool === "object" ? c.pool : {}, w = c.window && typeof c.window === "object" ? c.window : {};
    return { pool: num(c.pool_lead) ?? num(p.score), poolReasons: arr(c.pool_reasons).length ? c.pool_reasons : arr(p.reasons), window: num(c.window_lead) ?? num(w.score),
      windowReasons: arr(c.window_reasons).length ? c.window_reasons : arr(w.reasons), glass: num(c.glass_m2_est) ?? num(w.glass?.glass_m2) ?? num(w.glass_m2), glassBasis: w.glass?.basis || "",
      stories: num(w.stories), next: c.next_trigger_season || M?.cleaning?.next_trigger_season || null };
  }
  wtStats() {
    const M = this.M, w = isObj(M?.weather_triggers) ? M.weather_triggers : null, area = isObj(M?.cleaning?.area) ? M.cleaning.area : {};
    const base = !w ? {} : isObj(w.county) ? { ...w, ...w.county } : isObj(w.county_stats) ? { ...w, ...w.county_stats } : w;
    const out = { ...base }; for (const k of ["dust_days_yr", "dust_then_rain_yr", "high_wind_days_yr", "hail_days_yr"]) if (num(out[k]) == null && num(area[k]) != null) out[k] = area[k];
    return out;
  }
  wtEvents() { const w = this.M?.weather_triggers || {}; return arr(pick(w.events, w.events_since_2020, w.events_25km)).filter(isObj).slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))); }
  triggerCal() {
    const tc = this.M?.cleaning?.trigger_calendar;
    if (Array.isArray(tc) && tc.length === 12 && tc.every(isObj)) { const v = tc.map((m) => num(m.combined) ?? num(m.trigger) ?? num(m.service)); if (v.some((x) => x != null)) return v.map((x) => x ?? 0); }
    else { const v = monthVals(tc); if (v) return v; }
    return monthVals(this.wtStats().monthly_trigger_profile);
  }
  strCtx(h) {
    const M = this.M, MS = M.str && typeof M.str === "object" ? M.str : null, S = h?.str && typeof h.str === "object" ? h.str : {};
    if (!MS && !has(S)) return null;
    const m = MS || {}, co = m.county && typeof m.county === "object" ? m.county : {}, ci = m.city_legal && typeof m.city_legal === "object" ? m.city_legal : {};
    const risk = Array.isArray(S.legal_risk) ? S.legal_risk : arr(S.legal_risk?.reasons).length ? S.legal_risk.reasons : arr(ci.reasons);
    return { m, S, co, ci, risk, status: pick(S.legal_status, ci.status, m.county_legal?.status), demand: num(pick(co.demand, co.demand_score, S.demand_score)), invest: num(pick(co.score, co.investment_score, S.county_score)), cost: num(co.cost) };
  }
  hdCtx(h) { const a = this.M.str?.housing_demand, b = h?.str?.housing_demand; if (!has(a) && !has(b)) return null; return { ...(has(b) ? b : {}), ...(has(a) ? a : {}) }; }
  cityKey() { const M = this.M; return (M.city_data?.adapter || M.city || "").toLowerCase().replace(/[^a-z]+/g, "_").replace(/^_|_$/g, "").replace(/_(mn|tx|co|oh|ok|mo|al)$/, ""); }
  rulesUrl(ck) { return new URL(`rules.html?city=${encodeURIComponent(ck)}`, this.mgr.usaBase).href; }
  affluenceLine() {
    const a = isObj(this.M?.affluence) ? this.M.affluence : null; if (!a) return "";
    const lvl = (x) => (num(x) == null && num(x?.block_group) != null ? " (block group)" : num(x) == null && num(x?.tract) != null ? " (tract)" : "");
    const mi = affV(a.median_hh_income), sh = affV(a.share_hh_200k), pc = num(a.county_percentile?.block_group) ?? num(a.county_percentile?.tract);
    const parts = [mi != null ? `median household income ${money0(mi)}${lvl(a.median_hh_income)}` : "", sh != null ? `${pct0(sh)} of households ≥ $200k` : "",
      num(a.zip_agi_per_return) != null ? `ZIP${a.zip ? " " + esc(a.zip) : ""} income per tax return ${money0(a.zip_agi_per_return)}` : "", pc != null ? `${Math.round(pc)}${((n) => (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th"))(Math.round(pc))} percentile in the county` : ""].filter(Boolean);
    if (!parts.length && !a.label) return "";
    return `<div class="sub">Area affluence${a.label ? `: <b>${esc(a.label)}</b>` : ""}${parts.length ? " · " + parts.join(" · ") : ""} <span class="small">(Census ACS / IRS, area aggregates, not this house)</span></div>`;
  }
  summary() {
    const M = this.M, H = this.H, P = this.P, ly = this.lidarYear(), houses = H.filter((h) => !h.garage).length, gar = H.filter((h) => h.garage).length, L = this.L;
    const where = [M.city, M.state].filter(Boolean).join(", ");
    const stats = [[fmt.format(P.n), "laser points, all shown"], [num(M.points_per_m2) != null ? M.points_per_m2 + "/m²" : "–", `${M.lidar_source || "USGS 3DEP"}${ly ? " " + ly : ""} scan`],
      [fmt.format(houses), `houses${gar ? " + " + gar + " garages" : ""}`]];
    if (H.some((h) => "roof_permit" in h) && (L.permits.length || H.some((h) => h.roof_permit))) stats.push([fmt.format(H.filter((h) => h.roof_permit).length), "roofs with a re-roof permit"]);
    if (H.some((h) => h.roof_color)) stats.push([fmt.format(H.filter((h) => h.roof_color_change).length), "roofs whose colour changed in photos"]);
    if (L.hail_days.length || H.some((h) => h.hail_history)) stats.push([L.hail_days.length + " days", `radar hail ≥0.5 in over the block${M.hail_since ? " since " + M.hail_since : ""}`]);
    const out = [`<div class="lbl">3D block · ${esc(where || this.slug)}</div><h3>${esc(M.name || where || this.slug)}</h3>`,
      `<div class="stats">${stats.map(([v, l]) => `<div><span class="v">${v}</span><span class="l">${l}</span></div>`).join("")}</div>`];
    // block chips: STR / housing demand
    const ch = [], c = M.str && typeof M.str === "object" ? this.strCtx({}) : null;
    if (c) { const tags = strTags(c.ci.reasons).filter((t) => t !== "not checked");
      ch.push(`<span class="chip ${scoreCls(c.demand)}" title="County short-term-rental demand score (0–100, estimate)">STR demand ${c.demand != null ? Math.round(c.demand) : "–"}</span>`);
      if (c.invest != null) ch.push(`<span class="chip ${scoreCls(c.invest)}" title="Investment score (0–100, estimate)">invest ${Math.round(c.invest)}</span>`);
      ch.push(`<span class="chip ${statusCls(c.status)}">legal: ${tags.length ? esc(tags.join(" + ")) : esc(c.status || "unknown")}</span>`, vbadge(c.ci.verified)); }
    const hs = num(pick(this.hdCtx({})?.score, this.hdCtx({})?.demand_score));
    if (hs != null) ch.push(`<span class="chip ${scoreCls(hs)}" title="Buy / long-term-rent housing demand score (0–100, estimate)">housing demand ${Math.round(hs)}</span>`);
    if (ch.length) out.push(`<div class="chips">${ch.join("")}</div>`);
    const s = this.wtStats(), bits = [[s.dust_days_yr, "dust days"], [s.dust_then_rain_yr, "dust-then-rain"], [s.high_wind_days_yr, "high-wind"], [s.hail_days_yr, "hail days"]].filter(([v]) => num(v) != null).map(([v, l]) => `${n1v(v)} ${l}`);
    if (bits.length) out.push(`<div class="sub small"><b>Weather triggers:</b> ${bits.join(" · ")} per year (county)</div>`);
    const sg = arr(M.traffic?.segments).filter((x) => num(x?.aadt) != null).sort((a, b) => b.aadt - a.aadt);
    if (sg.length) out.push(`<div class="sub small"><b>Traffic:</b> busiest road ${esc(sg[0].name || "unnamed")} ${aadtTxt(sg[0].aadt)}${sg[0].year ? " (" + esc(sg[0].year) + ")" : ""}</div>`);
    out.push(this.affluenceLine());
    // houses + cleaning leads
    if (H.length) {
      const hb = (h) => `<li><button data-bm-house="${esc(this.slug)}|${esc(this.idOf(h))}"><span>${esc(h.address || "Building #" + this.idOf(h))}${h.garage ? " (garage)" : ""}</span><b>${ownValue(h) ? money0(ownValue(h)) : ""}</b></button></li>`;
      out.push(`<h4>Houses (${H.length})</h4><ol class="hlist">${H.map(hb).join("")}</ol>`);
    } else out.push(`<div class="sub">No houses mapped on this block (LiDAR points and street records only).</div>`);
    const lead = (k, rk) => H.filter((h) => this.leadOf(h)?.[k] != null).sort((a, b) => this.leadOf(b)[k] - this.leadOf(a)[k]).slice(0, 5);
    const pl = lead("pool"), wl = lead("window"), li = (h, k) => `<li><button data-bm-house="${esc(this.slug)}|${esc(this.idOf(h))}"><span>${esc(h.address || "building")}</span><b class="chip ${scoreCls(this.leadOf(h)[k])}">${Math.round(this.leadOf(h)[k])}</b></button></li>`;
    if (pl.length || wl.length) out.push(`<h4>Cleaning leads</h4>${pl.length ? `<div class="sub small">Pool service · top ${pl.length}</div><ol class="hlist">${pl.map((h) => li(h, "pool")).join("")}</ol>` : ""}${wl.length ? `<div class="sub small">Window cleaning · top ${wl.length}</div><ol class="hlist">${wl.map((h) => li(h, "window")).join("")}</ol>` : ""}`);
    out.push(this.layerNotes());
    out.push(this.sourcesHtml());
    return out.filter(Boolean).join("\n");
  }
  layerNotes() {
    const n = [], S = this.STR, F_ = this.FURN, opt = this.mgr.options;
    if (this.WALK) n.push(`Footpaths: ${this.WALK.polys.length ? this.WALK.polys.length + " measured sidewalk/stair/crosswalk outlines" : ""}${this.WALK.lines.some((l) => l.city) ? " surveyed sidewalk lines" : ""}${this.WALK.cityAttribution ? " from " + esc(this.WALK.cityAttribution) : ""}${this.WALK.nOsm ? (this.WALK.cityAttribution ? "; " : "") + this.WALK.nOsm + " OpenStreetMap footways (ODbL)" : ""}`);
    if (S) n.push(S.validated ? `Road surface from streets.json (validated: 0 m² overlap with house footprints); ${S.markingsSource ? "lane markings from " + esc(S.markingsSource) : "no lane-marking dataset, so no markings drawn"}${S.attribution ? " · " + esc(S.attribution) : ""}` : "streets.json present but not validated against the footprints: not drawn");
    if (F_) n.push(`Street furniture at recorded positions only (${F_.items.length} items${F_.marks.length ? ", " + F_.marks.length + " stop/give-way control points" : ""}) from ${F_.fromFJ ? "furniture.json" : "streets.json"}${F_.signals.length ? " · signals shown as locations only (no live state data)" : ""}`);
    if (this.PAR?.parcels.length) n.push(`${this.PAR.parcels.length} parcel boundaries (block-edge cuts not drawn)`);
    if (this.PAR?.hoa.length) n.push(`${this.PAR.hoa.length} HOA / covenant areas${this.M.parcels?.hoa_status ? " (" + esc(this.M.parcels.hoa_status) + ")" : ""}; elsewhere no open HOA data`);
    if (this.SIMG.length) n.push(`${this.SIMG.length} street photos at capture points (${this.SIMG.filter((i) => i.thumb).length} with a local thumbnail)`);
    if (this.LST) n.push(`Landsat land-surface temperature available (${this.LST.scenes.map((s) => esc(s.label)).join(", ")})${opt.thermal ? " — draped" : ""}: measured 30 m pixels, several houses each`);
    return n.length ? `<h4>In this view</h4><ul class="log">${n.map((t) => `<li>${t}</li>`).join("")}</ul>` : "";
  }
  sourcesHtml() {
    const src = this.sources;
    return `<h4>Sources &amp; licences</h4><ul class="src">${src.length ? src.map((s) => `<li><b>${esc(s.name)}</b>${s.detail ? " · " + esc(s.detail) : ""}${s.licence ? " · " + esc(s.licence) : ""}</li>`).join("") : `<li>${esc(this.M.lidar || "USGS 3DEP LiDAR")}</li>`}</ul>`;
  }
  photoCard(i) {
    const u = safeUrl(i.url), when = String(i.captured_at || "").slice(0, 10), th = this.localThumb(i.thumb);
    return `<div class="photo"><div class="lbl">Street photo${i.heading_deg != null ? ` · faces ${cmp(i.heading_deg)} ${Math.round(i.heading_deg)}°` : ""}</div>
      ${th ? `<img src="${esc(th)}" alt="Street-level photo${when ? ", taken " + esc(when) : ""}" loading="lazy">` : `<div class="sub">No local thumbnail stored with the block.</div>`}
      <div class="cr">${when ? "Taken " + esc(when) + " · " : ""}${imgCredit(i)}${u ? ` · <a href="${esc(u)}" target="_blank" rel="noopener">view original</a>` : ""}</div></div>`;
  }
  localThumb(t) { return typeof t === "string" && t && !/^[a-z]+:|^\/\/|\.\./i.test(t) && this.present(t) ? this.base + t.split("/").map(encodeURIComponent).join("/") : ""; }
  roofThumbs(h) {
    const xs = h.ring.map((p) => p[0]), ys = h.ring.map((p) => p[1]), pad = 3, half = this.half;
    const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
    return this.PHOTOS.map(({ key, label }) => { const im = this.imgs[key]; if (!im) return null;
      const sx = (x0 + half) / (2 * half) * im.width, sy = (half - y1) / (2 * half) * im.height, sw = (x1 - x0) / (2 * half) * im.width, sh = (y1 - y0) / (2 * half) * im.height;
      try { const c = document.createElement("canvas"); c.width = c.height = 120; c.getContext("2d").drawImage(im, sx, sy, sw, sh, 0, 0, 120, 120); return { label, src: c.toDataURL("image/jpeg", 0.85) }; } catch { return null; }
    }).filter(Boolean);
  }

  // ------------------------------------------------------------ panel: house dossier (port of view.html dossier())
  boundarySection(h) {
    const pr = this.parcelOf(h), hz = this.hoaOf(h), rows = [];
    if (pr) { const p = pr.p;
      rows.push(`<dt>Property boundary</dt><dd>parcel ${esc(p.id ?? "–")}${pr.how === "loc" ? " (matched by location)" : ""} · outlined with a light band${p.clipped ? " (lot continues past the block edge; cut edge not drawn)" : ""}</dd>`);
      rows.push(`<dt>Parcel area</dt><dd>${fmt.format(Math.round(ringArea(p.ring)))} m² (${fmt.format(Math.round(ringArea(p.ring) * 10.764))} ft²)</dd>`);
      if (p.address && p.address !== h.address) rows.push(`<dt>Parcel address</dt><dd>${esc(p.address)}</dd>`);
      if (p.source) rows.push(`<dt>Parcel source</dt><dd>${esc(this.srcName(p.source))}</dd>`);
    } else rows.push(`<dt>Property boundary</dt><dd>property boundary: no open parcel data</dd>`);
    if (hz.length) hz.forEach((a) => rows.push(`<dt>${a.k === "plat" ? "Covenants" : "HOA"}</dt><dd>${esc(a.name || "unnamed")} · ${esc(HOA_KINDS[a.k])}${a.note ? "<br>" + esc(a.note) : ""}${a.source ? "<br>source: " + esc(this.srcName(a.source)) : ""}${a.confidence != null ? " · confidence " + esc(a.confidence) : ""}</dd>`));
    else rows.push(`<dt>HOA</dt><dd>no open HOA data${this.PAR?.hoa.length ? " for this spot" : ""} (not the same as no HOA)</dd>`);
    return `<h4>Property boundary &amp; HOA</h4><dl>${rows.join("")}</dl>`;
  }
  poolSection(h) {
    const p = poolOf(h), c = this.leadOf(h), cal = this.triggerCal();
    if (!p && !c) return "";
    const rows = [], pal = poolPal();
    if (p) { const pres = poolPresent(h), conf = num(p.confidence);
      rows.push(`<dt>Pool</dt><dd>${pres ? `<b class="hl">yes</b>` : p.present === false || p.likely === false ? "none seen" : "unclear"}${conf != null ? ` <span class="sub">(${pct0(conf)} confidence)</span>` : ""}${pres && num(p.area_m2) != null ? ` · ${m2ft(p.area_m2)}` : ""}</dd>`);
      if (pres && p.first_seen_year) rows.push(`<dt>First seen</dt><dd>${esc(p.first_seen_year)}</dd>`);
      const pl = poolLast(h);
      if (pres) rows.push(`<dt>Latest state</dt><dd><span class="chip ${pl === "green" ? "bad" : pl === "blue" ? "ok" : pl === "turq" || pl === "empty" ? "warn" : ""}">${esc(POOL_KINDS[pl])}</span>${arr(p.green_years).length ? ` <span class="sub">green in ${arr(p.green_years).map(esc).join(", ")}</span>` : ""}</dd>`);
      const yrs = poolYears(h);
      if (yrs.length) rows.push(`<dt>By imagery year</dt><dd><div class="ystrip">${yrs.map((y) => { const k = poolKind(y.state), cc = pal[k];
        return `<span title="${esc(`${y.year}${y.date ? " (" + y.date + ")" : ""}: ${POOL_KINDS[k]}`)}"><i style="${cc ? `background:${rgbCss(cc)}` : "background:transparent;border-style:dashed"}"></i>’${esc(String(y.year).slice(-2))}</span>`; }).join("")}</div></dd>`); }
    if (c) {
      if (c.pool != null) rows.push(`<dt>Pool-service lead</dt><dd><b class="chip ${scoreCls(c.pool)}">${Math.round(c.pool)}</b> <span class="sub">0–100</span>${reasonList(c.poolReasons)}</dd>`);
      if (c.window != null || c.glass != null) rows.push(`<dt>Window-cleaning lead</dt><dd>${c.window != null ? `<b class="chip ${scoreCls(c.window)}">${Math.round(c.window)}</b> ` : ""}${c.glass != null ? `<span class="sub" title="${esc(c.glassBasis)}">~${m2ft(c.glass)} of glass (est.)${c.stories != null ? ` · ${esc(c.stories)} storey${c.stories === 1 ? "" : "s"}` : ""}</span>` : ""}${reasonList(c.windowReasons)}</dd>`);
      if (c.next) rows.push(`<dt>Next trigger season</dt><dd>${esc(typeof c.next === "object" ? c.next.label || c.next.name || arr(c.next.months).join(", ") : c.next)}${c.next?.status === "now" ? ' <b class="warn">in season now</b>' : ""}</dd>`);
    }
    if (cal) rows.push(`<dt>Trigger calendar</dt><dd>${bars12(cal, "Cleaning triggers by month", (x) => `${Math.round((x / Math.max(...cal, 1e-9)) * 100)}% of the busiest month`)}<span class="sub small">dust, wind, rain-after-dust and hail by month (county)</span></dd>`);
    const ev = this.wtEvents();
    if (ev.length) rows.push(`<dt>Weather events ≤25 km since 2020 (${ev.length})</dt><dd>${ev.slice(0, 5).map((e) => `${esc(e.date || "")} ${esc(e.type || "event")}${e.source ? ` <span class="sub">${esc(e.source)}</span>` : ""}`).join("<br>")}</dd>`);
    return rows.length ? `<h4>Pool &amp; cleaning</h4><dl>${rows.join("")}</dl><div class="sub small">Pool state read from aerial and satellite imagery (estimate). Lead scores rank houses on this block; they are not a quote.</div>` : "";
  }
  strSection(h, ck) {
    const c = this.strCtx(h); if (!c) return "";
    const { m, S, co, ci } = c, rows = [];
    const pd = partsTxt(m.county_parts?.demand);
    rows.push(`<dt>${esc(co.name ? co.name + " County" : "County")} scores</dt><dd>demand <b class="chip ${scoreCls(c.demand)}">${c.demand != null ? Math.round(c.demand) : "–"}</b> · investment <b class="chip ${scoreCls(c.invest)}">${c.invest != null ? Math.round(c.invest) : "–"}</b>${c.cost != null ? ` · affordability ${Math.round(c.cost)}` : ""}${num(co.coverage) != null ? ` <span class="sub">(${Math.round(co.coverage * 100)}% of inputs)</span>` : ""}
      <div class="sub small">${pd ? "Percentiles: " + pd : c.demand == null ? "Tourism inputs not available for this county yet." : ""}</div></dd>`);
    const mv = monthVals(pick(co.seasonality, m.seasonality, co.peak_months, m.peak_months));
    if (mv) { const pk = arr(co.peak_months).length ? arr(co.peak_months).map(esc).join(", ") : MON3.filter((_, i) => mv[i] >= top3(mv) && mv[i] > 0).join(", ");
      rows.push(`<dt>Peak months</dt><dd>${monthSpark(mv)}<span class="sub small">${pk}</span></dd>`); }
    if (c.status || ci.city || ck) rows.push(`<dt>City rules${ci.city ? " · " + esc(ci.city) : ""}</dt><dd><b class="chip ${statusCls(c.status)}">${esc(c.status || "unknown")}</b> ${vbadge(ci.verified)}${ck ? ` <a href="${esc(this.rulesUrl(ck))}" target="_blank" rel="noopener">full rules ▸</a>` : ""}
      ${has(ci.fields) ? `<div class="sub small">${[["permit", "Permit"], ["primary_residence", "Primary residence"], ["caps", "Caps"], ["taxes", "Taxes"]].filter(([k]) => ci.fields[k]?.value).map(([k, l]) => `${l}: ${esc(String(ci.fields[k].value).slice(0, 110))}${String(ci.fields[k].value).length > 110 ? "…" : ""} ${vbadge(ci.fields[k].verified)}${lnk(ci.fields[k].source_url, "source")}`).join("<br>")}</div>` : ""}
      ${ci.state_preemption?.value ? `<div class="sub small">State: ${esc(ci.state_preemption.value)} ${vbadge(ci.state_preemption.verified)}${lnk(ci.state_preemption.source_url, "source")}</div>` : m.county_legal?.note ? `<div class="sub small">${esc(m.county_legal.note)}</div>` : ""}</dd>`);
    if (c.risk.length) rows.push(`<dt>Legal risk (${c.risk.length})</dt><dd>${strTags(c.risk).map((t) => `<span class="chip warn">${esc(t)}</span>`).join(" ")}<ul class="log">${c.risk.map((r) => { const t = reasonText(r), i = t.indexOf(":");
      return `<li>${i > 0 && i < 70 ? `<b>${esc(t.slice(0, i))}</b>${esc(t.slice(i + 1).trim())}` : esc(t)}</li>`; }).join("")}</ul></dd>`);
    if ("str_license_on_record" in S || h.local?.str_license) { const lic = pick(S.str_license_on_record, h.local?.str_license);
      rows.push(`<dt>STR licence on record</dt><dd>${lic ? (typeof lic === "object" ? esc([lic.status, lic.number || lic.id, lic.type, lic.expires || lic.since].filter(Boolean).join(" · ") || "yes") + (lic.source ? ` <span class="sub">(${esc(lic.source)})</span>` : "") : esc(lic === true ? "yes" : lic)) : "none found in city records"}</dd>`); }
    if (num(S.est_monthly_cost) != null) rows.push(`<dt>Owning cost (est.)</dt><dd>${money0(S.est_monthly_cost)}/mo <span class="sub">${esc(S.est_monthly_cost_basis || "")}</span></dd>`);
    const ap = m.airport;
    if (ap && typeof ap === "object") { const a = ap.arrivals, w = a ? null : arrivalWindow(ap.arrival_hour_share);
      const win = a?.busiest_3h_window ? `most flights land <b>${esc(String(a.busiest_3h_window).replace(/:00/g, "").replace("-", "–"))}</b>${num(a.share_in_window) != null ? ` (${Math.round(a.share_in_window * 100)}% of arrivals)` : ""}${a.checkin_window ? ` · plan check-in ${esc(String(a.checkin_window).replace(/:00/g, "").replace("-", "–"))}` : ""}` : w ? `most flights land <b>${w.label}</b> (${w.pct}% of arrivals)` : "";
      rows.push(`<dt>Nearest airport</dt><dd>${esc(ap.name || "")}${ap.iata || ap.code ? ` (${esc(ap.iata || ap.code)})` : ""} · ${km1(kmOf(ap))}${num(ap.pax_12m) != null ? ` <span class="sub">· ${fmt.format(Math.round(ap.pax_12m))} passengers/yr</span>` : ""}${win ? `<div class="sub small">Check-in window: ${win}</div>` : ""}</dd>`); }
    const vn = arr(pick(m.venues_5km, m.venues, S.venues)).filter(Boolean);
    if (vn.length) rows.push(`<dt>Top venues within 5 km${num(m.venues_5km_total) != null ? ` (${esc(m.venues_5km_total)})` : ""}</dt><dd>${vn.slice(0, 5).map((v) => `${esc(v.name || "venue")}${v.kind ? ` <span class="sub">${esc(v.kind)}</span>` : ""}${num(v.cap) != null ? ` <span class="sub">${fmt.format(v.cap)} seats</span>` : ""} · ${km1(kmOf(v))}`).join("<br>")}</dd>`);
    else if (m.venues_5km_total === 0) rows.push(`<dt>Top venues within 5 km</dt><dd>none mapped</dd>`);
    const vs = arr(m.visitor_sites_25km);
    if (vs.length) rows.push(`<dt>Visitor sites within 25 km</dt><dd>${vs.slice(0, 4).map((v) => `${esc(v.name || "")}${num(v.visits) != null ? ` <span class="sub">${fmt.format(Math.round(v.visits))} visits</span>` : ""} · ${km1(kmOf(v))}`).join("<br>")}</dd>`);
    const am = m.amtrak;
    if (am && typeof am === "object") rows.push(`<dt>Nearest Amtrak</dt><dd>${esc(am.station || am.name || "station")} · ${km1(kmOf(am))}</dd>`);
    if (num(m.walkability_median) != null || num(m.transit_trips_per_capita) != null) rows.push(`<dt>Getting around</dt><dd>${num(m.walkability_median) != null ? `walkability ${esc(m.walkability_median)}/20 (EPA)` : ""}${num(m.transit_trips_per_capita) != null ? ` · ${esc(Math.round(m.transit_trips_per_capita * 10) / 10)} transit trips per resident/yr` : ""}</dd>`);
    return `<h4>Short-term rental outlook · estimate</h4><dl>${rows.join("")}</dl><div class="sub small">Aggregate public data only — no booking, occupancy or nightly-rate data is used (AirDNA and similar are paid and not open).</div>`;
  }
  housingDemandSection(h) {
    const d = this.hdCtx(h); if (!d) return "";
    const s = num(pick(d?.score, d?.demand_score)), rows = [], P = d.parts && typeof d.parts === "object" ? d.parts : {};
    const pv = (k) => (Array.isArray(P[k]) ? num(P[k][0]) : num(P[k]));
    rows.push(`<dt>Demand score</dt><dd><b class="chip ${scoreCls(s)}">${s != null ? Math.round(s) : "–"}</b> <span class="sub">buy / long-term rent, 0–100${num(d.coverage) != null ? ` · ${Math.round(d.coverage * 100)}% of inputs` : ""}</span>${partsTxt(P) ? `<div class="sub small">Percentiles: ${partsTxt(P)}</div>` : ""}</dd>`);
    if (pv("emp_yoy") != null || pv("emp_5y") != null) rows.push(`<dt>Job growth</dt><dd>${pv("emp_yoy") != null ? pctTxt(pv("emp_yoy")) + " in 1 yr" : ""}${pv("emp_5y") != null ? ` · ${pctTxt(pv("emp_5y"))} in 5 yrs` : ""}${pv("wage_yoy") != null ? ` <span class="sub">· wages ${pctTxt(pv("wage_yoy"))}</span>` : ""}</dd>`);
    if (pv("net_mig_rate") != null || num(d.pop_change_pct) != null) rows.push(`<dt>Net in-migration</dt><dd>${pv("net_mig_rate") != null ? `<b class="${pv("net_mig_rate") >= 0 ? "good" : "warn"}">${pv("net_mig_rate") > 0 ? "+" : ""}${Math.round(pv("net_mig_rate") * 10000) / 10}</b> per 1,000 residents` : ""}${num(d.pop_change_pct) != null ? ` <span class="sub">· population ${pctTxt(d.pop_change_pct)}</span>` : ""}</dd>`);
    const or = arr(d.top_origins);
    if (or.length) rows.push(`<dt>Top origin counties</dt><dd>${or.slice(0, 5).map((o) => (typeof o === "string" ? esc(o) : `${esc(o?.name || o?.fips || "")}${num(o?.n) != null ? ` <span class="sub">${fmt.format(Math.round(o.n))} movers</span>` : ""}`)).join("<br>")}</dd>`);
    return `<h4>Jobs &amp; people flow · housing demand</h4><dl>${rows.join("")}</dl><div class="sub small">${esc(d.note || "County-level aggregates (estimate), not this house.")}</div>`;
  }
  trafficSection(h) {
    const t = h.traffic && typeof h.traffic === "object" ? h.traffic : null; if (!t) return "";
    const nr = t.nearest_road && typeof t.nearest_road === "object" ? t.nearest_road : { name: t.nearest_road };
    const rows = [`<dt>Nearest counted road</dt><dd>${esc(nr.name || "–")} · ${aadtTxt(pick(t.nearest_aadt, nr.aadt))}${num(t.distance_m) != null ? ` <span class="sub">${fmt.format(Math.round(t.distance_m))} m away</span>` : ""}${pick(t.year, nr.year) ? ` <span class="sub">(${esc(pick(t.year, nr.year))})</span>` : ""}</dd>`];
    const bz = t.busiest_within_150m_aadt;
    if (bz != null) rows.push(`<dt>Busiest within 150 m</dt><dd>${bz && typeof bz === "object" ? `${esc(bz.name || "")} · ${aadtTxt(bz.aadt)}` : aadtTxt(bz)}${t.busiest_within_150m_name ? " · " + esc(t.busiest_within_150m_name) : ""}</dd>`);
    return `<h4>Road traffic</h4><dl>${rows.join("")}</dl><div class="sub small">AADT = annual average daily traffic from state DOT counts (public).</div>`;
  }
  assistanceSection(h) {
    const as = h.assistance, MA = this.M.assistance; if (!as) return "";
    const vc = as.voucher_rent_ceiling || {}, ps = vc.pha_payment_standard && typeof vc.pha_payment_standard === "object" ? vc.pha_payment_standard : null, sf = vc.if_pha_uses_safmr;
    const st = { "likely ok": "good", check: "warn", unknown: "dim" };
    const soi = MA?.source_of_income, tv = MA?.tract_vouchers, hcv = tv?.picture_2025?.housing_choice_vouchers, bah = as.bah_benchmark && typeof as.bah_benchmark === "object" ? as.bah_benchmark : null;
    const pn = arr(MA?.policy_notes).map((n) => (typeof n === "string" ? { note: n } : n || {})).filter((n) => n.note);
    const soiTxt = (s) => `<b class="${s.protected === true ? "good" : s.protected === false ? "warn" : "dim"}">${s.protected === true ? "No — source of income is protected" : s.protected === false ? "Yes — no protection / preempted" : "Unclear"}</b> ${vbadge(s.verified)}${lnk(s.source_url, "source")}<div class="sub small">${esc(s.jurisdiction ? s.jurisdiction + ": " : "")}${clip(s.law, 170)}</div>`;
    const derived = '<span class="chip warn badge">DERIVED</span>';
    return `<h4>Government rental programs · estimate</h4><dl>
      <dt>Bedrooms</dt><dd>${dash(as.bedrooms)}${as.bedrooms_basis ? ` <span class="sub">(${esc(as.bedrooms_basis)})</span>` : ""}${as.bedrooms_note ? `<div class="sub small">${esc(as.bedrooms_note)}</div>` : ""}</dd>
      ${num(vc.low) != null ? `<dt>Voucher rent ceiling${vc.year ? " · " + esc(vc.year) : ""}</dt><dd><b>${money0(vc.low)}</b> · ${money0(vc.mid_100pct)} · <b>${money0(vc.high)}</b>/mo gross <span class="sub">(90 / 100 / 110%${vc.base_source ? " of " + esc(vc.base_source) : ""})</span><div class="sub small">${clip(vc.basis, 90)}</div></dd>`
        : vc.basis ? `<dt>Voucher rent ceiling</dt><dd><span class="sub">${esc(vc.basis)}</span></dd>` : ""}
      ${sf && num(sf.low) != null ? `<dt>If the PHA uses ZIP rents</dt><dd>${money0(sf.low)}–${money0(sf.high)}/mo${sf.zip ? ` <span class="sub">ZIP ${esc(sf.zip)}</span>` : ""}${sf.note ? `<div class="sub small">${clip(sf.note, 80)}</div>` : ""}</dd>` : ""}
      ${ps ? `<dt>Housing authority standard</dt><dd>${num(ps.value) != null ? `<b>${money0(ps.value)}</b>/mo ` : ""}${ps.derived ? derived + " " : ""}${vbadge(ps.verified)}${lnk(ps.source, "source")}<div class="sub small">${num(ps.value) == null ? esc(ps.value_basis || "no value captured") : clip(ps.value_basis, 70)}${ps.pha ? " · " + clip(ps.pha, 60) : ""}${ps.effective ? " · effective " + clip(ps.effective, 40) : ""}</div></dd>`
        : vc.pha_payment_standard_note ? `<dt>Housing authority standard</dt><dd><span class="sub">${esc(vc.pha_payment_standard_note)}</span></dd>` : ""}
      ${as.occupancy_by_bedrooms ? `<dt>Household size</dt><dd>${esc(as.occupancy_by_bedrooms)}</dd>` : ""}
      ${as.lead_rule_applies_if_child_under_6 != null ? `<dt>Lead-safe rule (child under 6)</dt><dd>${as.lead_rule_applies_if_child_under_6 ? '<b class="warn">applies — built before 1978</b>' : "no (built 1978 or later)"}</dd>` : ""}
      ${bah ? `<dt>Military housing allowance${bah.year ? " · BAH " + esc(bah.year) : ""}</dt><dd>${num(bah.e5_with_dependents) != null ? `E-5 with dependents <b>${money0(bah.e5_with_dependents)}</b>/mo` : "–"} <span class="sub">${esc(bah.mha_name || bah.mha || "")}</span></dd>` : as.bah_note ? `<dt>Military housing allowance</dt><dd><span class="sub">${esc(as.bah_note)}</span></dd>` : ""}
      ${soi ? `<dt>Can landlords refuse vouchers?</dt><dd>${soiTxt(soi)}${soi.also ? `<div class="sub small">Also: ${soiTxt(soi.also)}</div>` : ""}</dd>` : ""}
      ${arr(MA?.phas).length ? `<dt>Housing authorit${arr(MA.phas).length > 1 ? "ies" : "y"}</dt><dd>${arr(MA.phas).slice(0, 3).map((p) => `${esc(p?.name || p?.code || "")}${p?.phone ? ` <span class="sub">${esc(phoneTxt(p.phone))}</span>` : ""}${num(p?.section8_units) != null ? ` <span class="sub">· ${fmt.format(p.section8_units)} vouchers</span>` : ""}`).join("<br>")}</dd>` : ""}
      ${tv ? `<dt>Vouchers in this tract${tv.tract ? ` <span class="sub">${esc(tv.tract)}</span>` : ""}</dt><dd>${tv.voucher_households != null ? `${fmt.format(tv.voucher_households)} households${tv.pct_of_renter_units != null ? ` (${esc(tv.pct_of_renter_units)}% of renter units)` : ""}` : "–"}${hcv ? `<div class="sub small">${num(hcv.avg_hud_spending_per_unit_month) != null ? `HUD pays ~${money0(hcv.avg_hud_spending_per_unit_month)}/mo per unit` : ""}${num(hcv.months_waiting) != null ? ` · wait list ~${esc(hcv.months_waiting)} months` : ""}${hcv.as_of ? ` · ${esc(hcv.as_of)}` : ""}</div>` : ""}${tv.suppressed_note ? `<div class="sub small">${esc(tv.suppressed_note)}</div>` : ""}</dd>` : ""}
      ${arr(MA?.hud_multifamily_nearby).length ? `<dt>HUD-assisted properties nearby</dt><dd>${arr(MA.hud_multifamily_nearby).slice().sort((a, b) => (num(a?.distance_m) ?? 1e9) - (num(b?.distance_m) ?? 1e9)).slice(0, 4).map((p) => `${esc(p?.name || "property")} · ${dash(p?.assisted_units)} assisted units · ${num(p?.distance_m) != null ? fmt.format(Math.round(p.distance_m)) + " m" : "–"}${p?.contract_expiration ? ` <span class="sub${p.expires_within_2y ? " warn" : ""}">contract to ${esc(p.contract_expiration)}</span>` : ""}`).join("<br>")}</dd>` : ""}</dl>
      ${vc.year_note ? `<div class="sub small">${esc(vc.year_note)}</div>` : ""}
      ${arr(as.checklist).length ? `<div class="lbl">Voucher-ready checklist (${arr(as.checklist).length})</div><ul class="log">${arr(as.checklist).map((c) => `<li><b class="${st[c?.status] || "dim"}">${esc(c?.status)}</b>${esc(c?.item)}${c?.why ? " – " + esc(c.why) : ""}${lnk(c?.rule_url, "rule")}</li>`).join("")}</ul>` : ""}
      ${arr(MA?.local_programs).length ? `<div class="lbl">Local landlord programs (${arr(MA.local_programs).length})</div><ul class="log">${arr(MA.local_programs).map((p) => `<li>${esc(p?.name || p?.program || "program")} ${vbadge(p?.verified)}${arr(p?.amounts).length ? " · " + arr(p.amounts).map((a) => esc(a)).join(" / ") : ""}${p?.landlord_gets ? ` – ${esc(p.landlord_gets)}` : ""}${lnk(p?.claim_url || p?.source_url, "details")}</li>`).join("")}</ul>` : ""}
      ${pn.length ? `<div class="lbl">Policy notes</div><ul class="log">${pn.map((n) => `<li>${n.date ? "<b>" + esc(n.date) + "</b>" : ""}${esc(n.note)} ${vbadge(n.verified)}${lnk(n.url, "source")}</li>`).join("")}</ul>` : ""}
      <div class="sub small">Estimate from HUD fair market rents and rules (public domain). Only the housing authority's inspection and rent-reasonableness review decide eligibility and rent.</div>`;
  }
  streetPhotosSection(h) {
    if (!this.SIMG.length) return "";
    const [cx, cy] = centroid(h.ring);
    const near = this.SIMG.map((i) => ({ i, d: Math.hypot(i.x - cx, i.y - cy) })).filter((o) => o.d <= 45).sort((a, b) => (!!this.localThumb(b.i.thumb) - !!this.localThumb(a.i.thumb)) || a.d - b.d).slice(0, 4);
    if (!near.length) return `<h4>Street photos</h4><div class="sub">No street-level photo within 45 m of this house.</div>`;
    return `<h4>Street photos near this house (${near.length})</h4><div class="sphotos">${near.map(({ i, d }) => { const th = this.localThumb(i.thumb), u = safeUrl(i.url), when = String(i.captured_at || "").slice(0, 10);
      return `<figure>${th ? `<img src="${esc(th)}" alt="Street-level photo ${Math.round(d)} m from the house${when ? ", " + esc(when) : ""}" loading="lazy">` : ""}<figcaption>${Math.round(d)} m${i.heading_deg != null ? " · faces " + cmp(i.heading_deg) : ""}${when ? " · " + esc(when) : ""}<br>${imgCredit(i)}${u ? ` · <a href="${esc(u)}" target="_blank" rel="noopener">original</a>` : ""}</figcaption></figure>`; }).join("")}</div>`;
  }
  dossier(h, thumbs) {
    const M = this.M, H = this.H, A = h.assessor || {}, d = h.roof_dev || {}, cc = h.roof_color_change, sun = h.sun || {}, y = h.yard || {}, pk = h.parking || {}, sc = h.stories_check || {}, m = h.money || {}, pay = m.payments_20pct_down;
    const prevScan = M.lidar_prev_year || "earlier", yr4 = (v) => String(v).slice(0, 4);
    const chips = [];
    if (h.roof_permit) chips.push(`<span class="chip ok">re-roof permit ${esc(yr4(h.roof_permit))}</span>`);
    if (cc) chips.push(`<span class="chip warn">roof colour changed${cc.year ? " ~" + esc(cc.year) : ""}</span>`);
    if (d.spread_cm > 12.8) chips.push(`<span class="chip bad">irregular roof surface</span>`);
    if (num(h.height_change_m) != null && Math.abs(h.height_change_m) > 1.5) chips.push(`<span class="chip warn">height ${h.height_change_m > 0 ? "+" : ""}${h.height_change_m} m since ${esc(prevScan)} scan</span>`);
    if (h.hail_max_in >= 1) chips.push(`<span class="chip bad">hail ${h.hail_max_in}" (radar)</span>`);
    if (h.solar_permit) chips.push(`<span class="chip ok">solar ${esc(yr4(h.solar_permit))}</span>`);
    if (sun.solar_kw >= 3 && !h.solar_permit) chips.push(`<span class="chip ok">solar fit ~${sun.solar_kw} kW</span>`);
    if (sun.shaded_pct > 50) chips.push(`<span class="chip warn">roof ${Math.round(sun.shaded_pct)}% shaded</span>`);
    if (h.ash_nearby) chips.push(`<span class="chip bad">ash tree near (EAB)</span>`);
    if (arr(h.trees_near).some((t) => t.gap_m < 0)) chips.push(`<span class="chip warn">tree over roof</span>`);
    if (y.lawn_m2 > 150) chips.push(`<span class="chip ok">big lawn ${Math.round(y.lawn_m2)} m²</span>`);
    if (num(y.front_lawn_m2) != null && y.front_lawn_m2 < 5) chips.push(`<span class="chip">no front lawn</span>`);
    if (poolGreenNow(h)) chips.push(`<span class="chip bad">green pool</span>`); else if (poolPresent(h)) chips.push(`<span class="chip ok">pool</span>`);
    { const ld = this.leadOf(h); if (ld?.pool >= 70) chips.push(`<span class="chip warn">pool-service lead ${Math.round(ld.pool)}</span>`); if (ld?.window >= 70) chips.push(`<span class="chip warn">window lead ${Math.round(ld.window)}</span>`); }
    if (h.thermal?.hot_roof_flag) chips.push(`<span class="chip warn">hot surface pixel (Landsat)</span>`);
    if (sc.agrees === false) chips.push(`<span class="chip warn">stories mismatch</span>`);
    if (arr(h.violations).some((v) => /open|active/i.test(v.status || ""))) chips.push(`<span class="chip bad">open code violation</span>`);
    if (h.flood_zone && !/^X/.test(h.flood_zone)) chips.push(`<span class="chip bad">flood zone ${esc(h.flood_zone)}</span>`);
    const out = [`<button class="x" data-bm-close aria-label="Close">×</button><div class="lbl">${h.garage ? "Garage" : "Property"} dossier · ${esc([M.city, M.state].filter(Boolean).join(", "))}</div><h3>${esc(h.address || "Building")}</h3>`];
    // value: this house's own assessor value, or a clearly labelled tract median (never shown as this home's price)
    const own = ownValue(h), nmed = medianOnly(h), basis = m.value_basis || m.basis;
    if (own) out.push(`<div class="bigv">${money0(own)}</div><div class="sub">est. value today${m.index_adj != null ? ` (${esc(basis || "assessed value")} × ZIP home-value index ${esc(m.index_adj)})` : ""}</div>${basis ? `<div class="sub">Source: ${esc(basis)}</div>` : ""}`);
    else if (nmed) out.push(`<div class="sub">Neighbourhood median ~${money0(nmed)} (not this house)${basis ? " · " + esc(basis) : ""}</div>`);
    else if (num(h.value) != null && !h.money) out.push(`<div class="bigv">${money0(h.value)}</div><div class="sub">assessed value</div>`);
    if (m.last_sale?.price) out.push(`<div class="sub">Last sale: ${money0(m.last_sale.price)}${m.last_sale.date ? " (" + esc(m.last_sale.date) + ")" : ""}</div>`);
    out.push(this.affluenceLine());
    out.push(`<div class="chips">${chips.join("") || '<span class="chip">no flags</span>'}</div>`);
    if (pay?.rates) out.push(`<h4>Monthly payment, 20% down (${money0(pay.down)})${own ? "" : " · priced at the neighbourhood median"}</h4><table class="pay"><tr><th>Term</th><th>Rate</th><th>Monthly</th></tr>
      <tr><td>10 yr</td><td>${dash(pay.rates.r10 ?? pay.rates.r15)}%${pay.rates.r10 == null ? "*" : ""}</td><td>${money0(pay.y10)}</td></tr><tr><td>15 yr</td><td>${dash(pay.rates.r15)}%</td><td>${money0(pay.y15)}</td></tr><tr><td>30 yr</td><td>${dash(pay.rates.r30)}%</td><td>${money0(pay.y30)}</td></tr></table>
      <div class="sub">Principal + interest${m.tax_yr ? ` + property tax (${money0(m.tax_yr)}/yr${m.tax_basis ? ", " + esc(m.tax_basis) : ""})` : ""}. APOR rates (FFIEC/CFPB, public domain) week of ${dash(pay.rates.date)}. Insurance not included.</div>`);
    if (m.land_value != null || m.building_value != null) out.push(`<dl><dt>Land / building</dt><dd>${money0(m.land_value)} / ${money0(m.building_value)}</dd></dl>`);
    if (thumbs.length) out.push(`<h4>${thumbs.length > 1 ? "Roof over the years" : "Roof from above"} (${esc(this.photoSrc())})</h4><div class="thumbs">${thumbs.map((t) => `<figure><img src="${t.src}" alt="Roof in ${esc(t.label)}"><figcaption>${esc(t.label)}</figcaption></figure>`).join("")}</div>`);
    else if (arr(M.img_years).length) out.push(`<h4>Roof over the years</h4><div class="sub">loading aerial photos…</div>`);
    const ly = this.lidarYear(), sv = H.map((x) => num(x.roof_dev?.spread_cm)).filter((x) => x != null).sort((a, b) => a - b), med = sv.length ? sv[Math.floor(sv.length / 2)] : null;
    out.push(`<h4>From the laser scan${ly ? " (" + esc(ly) + ")" : ""}</h4><dl>
      <dt>Points on this house</dt><dd>${num(h.points) != null ? fmt.format(h.points) : "–"}</dd><dt>Roof planes</dt><dd>${dash(h.planes)}</dd>
      <dt>Ridge / eave</dt><dd>${dash(h.height_m, " m")} / ${dash(h.eave_m, " m")}</dd><dt>Footprint</dt><dd>${num(h.area_m2) != null ? `${h.area_m2} m² (${fmt.format(Math.round(h.area_m2 * 10.764))} ft²)` : "–"}</dd>
      ${has(h.stories_check) ? `<dt>Stories</dt><dd>${dash(sc.assessor)} assessor · ${dash(sc.lidar_estimate)} from LiDAR ${sc.agrees === false ? "⚠" : sc.agrees ? "✓" : ""}</dd>` : ""}
      ${d.spread_cm != null ? `<dt>Surface scatter</dt><dd>${esc(d.spread_cm)} cm${med != null ? " (block median " + med + ")" : ""}</dd>` : ""}
      ${h.height_2011_m != null ? `<dt>Height in ${esc(prevScan)} scan</dt><dd>${esc(h.height_2011_m)} m</dd>` : ""}</dl>`);
    try { out.push(this.boundarySection(h)); } catch (e) { console.warn("boundary section skipped:", e.message); }
    const fs = h.facade_sun, ra = h.roof_aspect;
    if (h.facing || ra || fs) out.push(`<h4>Orientation</h4><dl><dt>Front faces</dt><dd>${h.facing ? esc(h.facing.compass) + " (" + esc(h.facing.azimuth) + "°)" : "–"}</dd>
      <dt>Roof slopes face</dt><dd>${ra ? ["N", "E", "S", "W"].map((k) => k + " " + (ra[k] ?? 0) + "%").join(" · ") + (ra.flat ? " · flat " + ra.flat + "%" : "") : "–"}</dd>
      <dt>Main roof pitch</dt><dd>${ra?.main_pitch_deg ? ra.main_pitch_deg + "° (" + Math.round(Math.tan(ra.main_pitch_deg * Math.PI / 180) * 12) + "/12)" : "–"}</dd>
      ${fs?.front ? `<dt>Front wall sun</dt><dd>${num(fs.front.sun_hours_yr) != null ? fmt.format(fs.front.sun_hours_yr) : "–"} h/yr · ${dash(fs.front.morning_h)} morning / ${dash(fs.front.afternoon_h)} afternoon</dd>` : ""}
      ${fs?.back ? `<dt>Back wall sun</dt><dd>${num(fs.back.sun_hours_yr) != null ? fmt.format(fs.back.sun_hours_yr) : "–"} h/yr (faces ${dash(fs.back.compass)})</dd>` : ""}</dl>`);
    // roof / solar
    if (has(h.sun)) out.push(`<h4>Sun on the roof (full-year trace)</h4><dl><dt>Direct sun</dt><dd>${sun.direct_sun_hours_yr ? fmt.format(sun.direct_sun_hours_yr) + " h/yr" : "–"}</dd>
      <dt>Clear-sky energy</dt><dd>${dash(sun.clear_sky_kwh_m2_yr)} kWh/m²/yr</dd><dt>Shaded by trees/neighbours</dt><dd>${dash(sun.shaded_pct)}%</dd><dt>Good solar area</dt><dd>${dash(sun.good_solar_m2)} m² · ~${dash(sun.solar_kw)} kW</dd></dl>`);
    const tm = h.thermal;
    if (has(tm)) { const c1 = (v) => (num(v) == null ? "–" : (Math.round(v * 10) / 10).toFixed(1) + " °C"), sg = (v) => (num(v) == null ? "–" : (v > 0 ? "+" : "") + (Math.round(v * 10) / 10).toFixed(1) + " °C");
      out.push(`<h4>Surface temperature (satellite)</h4><div class="sub small warn">Landsat 30 m land-surface temperature (measured), pixel covers several houses</div><dl>
      <dt>Summer surface</dt><dd>${c1(tm.summer_lst_c)}${tm.summer_date ? ` <span class="sub">${esc(tm.summer_date)}</span>` : ""}</dd><dt>Winter surface</dt><dd>${c1(tm.winter_lst_c)}${tm.winter_date ? ` <span class="sub">${esc(tm.winter_date)}</span>` : ""}</dd><dt>vs block average</dt><dd>${sg(tm.rel_to_block_c)}</dd>
      <dt>Hot-roof flag</dt><dd>${tm.hot_roof_flag ? '<span class="chip warn">hot pixel</span>' : tm.hot_roof_flag === false ? "no" : "–"}</dd></dl>${tm.pixel_note ? `<div class="sub small">${esc(tm.pixel_note)}</div>` : ""}<div class="sub small">Ground-surface temperature from the satellite's thermal band, not the roof alone and not indoor or air temperature.</div>`); }
    if (has(h.yard) || h.pool || h.meta_canopy) out.push(`<h4>Yard &amp; lot</h4><dl><dt>Lot</dt><dd>${dash(y.lot_m2)} m²</dd><dt>Lawn</dt><dd>${dash(y.lawn_m2)} m² (${dash(y.lawn_pct)}%) · front ${dash(y.front_lawn_m2)} / back ${dash(y.back_lawn_m2)}</dd>
      <dt>Tree canopy</dt><dd>${dash(y.canopy_pct)}% of lot${h.meta_canopy ? ` · Meta/WRI ${dash(h.meta_canopy.cover_pct)}% cover, trees to ${dash(h.meta_canopy.max_m)} m` : ""}</dd>
      <dt>Lawn health (NAIP infrared)</dt><dd>${y.lawn_health ? esc(y.lawn_health) + " · NDVI " + dash(y.naip_ndvi) : "–"}</dd><dt>Hard surface</dt><dd>${dash(y.hard_m2)} m²</dd><dt>Pool</dt><dd>${poolPresent(h) ? "yes (" + dash(h.pool.area_m2) + " m²)" : h.pool ? "none seen" : "–"}</dd></dl>`);
    out.push(this.poolSection(h));
    if (has(h.parking)) out.push(`<h4>Parking</h4><dl><dt>Garage stalls</dt><dd>${dash(pk.garage_stalls)} (assessor)</dd><dt>Detached garage</dt><dd>${pk.detached_garages ? dash(pk.garage_m2) + " m²" : "none mapped"}</dd><dt>Est. off-street spots</dt><dd>${dash(pk.est_spots)}</dd></dl>`);
    if (Array.isArray(h.trees_near)) out.push(`<h4>Trees near the house</h4><ul class="log">${h.trees_near.map((t) => `<li><b>${dash(t.h)} m</b>${esc(t.genus || "unidentified")} · crown ${num(t.crown_r) != null ? t.crown_r * 2 : "?"} m · ${t.gap_m < 0 ? "overhangs roof" : dash(t.gap_m) + " m from wall"}</li>`).join("") || "<li>none within reach</li>"}</ul>`);
    const sc_ = h.solar_city;
    if (has(sc_)) out.push(`<h4>City solar model${M.city ? " (" + esc(M.city) + ")" : ""}</h4><dl><dt>Suitable roof</dt><dd>${fmt.format(Math.round(sc_.SUIT_SQFT || 0))} ft²</dd><dt>Max panels</dt><dd>${dash(sc_.Max_Panels)} (${fmt.format(Math.round((sc_.Max_Watts || 0) / 1000 * 10) / 10)} kW)</dd><dt>Yearly output</dt><dd>${fmt.format(Math.round(sc_.Total_kWh || 0))} kWh · worth ${money0(sc_.Value_Elec)}/yr</dd><dt>Install cost (city est.)</dt><dd>${money0(sc_.Install_Cost)}</dd></dl>`);
    const nb = h.nearby || {}, u = h.utility || {};
    const NB = [["Fire hydrant", "hydrants"], ["Storm drain", "catch_basins"], ["Transit stop", "transit_stops"], ["Public school", "schools_public"], ["Private school", "schools_private"], ["Fire / EMS", "fire_ems"], ["Police", "police"],
      ["Hospital", "hospitals"], ["Post office", "post_offices"], ["Park", "parks"], ["Rail line", "rail"], ["Rail crossing", "crossings"], ["Power transmission line", "transmission"], ["Sewer interceptor", "interceptors"], ["Bridge", "bridges"]].filter(([, k]) => k in nb);
    if (NB.length || has(h.utility)) out.push(`<h4>Services &amp; infrastructure</h4><dl>${NB.map(([l, k]) => `<dt>${l}</dt><dd>${nb[k] ? `${dash(nb[k].m)} m${nb[k].name ? " · " + esc(nb[k].name) : ""}` : "–"}${k === "transmission" && nb.transmission?.kv ? " (" + esc(nb.transmission.kv) + " kV)" : ""}</dd>`).join("")}
      ${has(h.utility) ? `<dt>Water utility</dt><dd>${dash(u.water_system)} ${u.pwsid ? "(" + esc(u.pwsid) + ")" : ""}</dd><dt>Sewershed</dt><dd>${dash(u.sewershed)}</dd>` : ""}</dl>`);
    const en = h.energy_est;
    if (has(en)) out.push(`<h4>Energy (typical for this home)</h4><dl><dt>Electricity</dt><dd>${num(en.elec_kwh_yr) != null ? fmt.format(en.elec_kwh_yr) : "–"} kWh/yr</dd><dt>Natural gas</dt><dd>${num(en.gas_therms_yr) != null ? fmt.format(en.gas_therms_yr) : "–"} therms/yr</dd></dl><div class="sub">NREL ResStock: ${dash(en.basis)} (${dash(en.vintage)}).</div>`);
    if (h.zoning || Array.isArray(h.violations) || Array.isArray(h.inspections)) out.push(`<h4>Zoning &amp; code</h4><dl><dt>Zoning</dt><dd>${h.zoning ? esc(h.zoning.code || "") + (h.zoning.use ? " · " + esc(h.zoning.use) : "") : "–"}</dd>
      ${Array.isArray(h.violations) ? `<dt>Code violations</dt><dd>${h.violations.length}</dd>` : ""}${Array.isArray(h.inspections) ? `<dt>Inspections</dt><dd>${h.inspections.length}</dd>` : ""}</dl>
      <ul class="log">${arr(h.violations).slice(0, 5).map((v) => `<li><b>${esc(v.date || "")}</b>${esc(v.what || v.type || "")} · ${esc(v.status || "")}</li>`).join("")}</ul>`);
    if (has(h.assessor) || h.year_built != null || h.homestead != null || h.type) out.push(`<h4>${esc(M.assessor_label || "Assessor")}</h4><dl>
      ${has(h.assessor) ? `<dt>Roof</dt><dd>${dash(A.roof)}</dd><dt>Exterior wall</dt><dd>${dash(A.exteriorwall)}</dd><dt>Construction</dt><dd>${dash(A.constructiontype)}</dd>` : ""}
      ${h.type ? `<dt>Type</dt><dd>${esc(h.type)}</dd>` : ""}<dt>Built</dt><dd>${dash(A.yearbuilt || h.year_built)}</dd>
      ${has(h.assessor) ? `<dt>Heating</dt><dd>${dash(A.primaryheating)}</dd><dt>Beds / baths</dt><dd>${dash(A.totalbedrooms)} / ${dash(A.totalbathrooms)}</dd>` : ""}
      ${A.homestead != null || h.homestead != null ? `<dt>Homestead</dt><dd>${A.homestead != null ? esc(A.homestead) : h.homestead ? "yes" : "no"}</dd>` : ""}</dl>`);
    const inc = h.incentives, rent = M.rent, lc = h.local;
    if (inc) { const yn = (v) => v == null ? "—" : v ? '<b class="good">yes</b>' : "no";
      out.push(`<h4>Federal incentive areas · HUD / Treasury / USDA</h4><dl><dt>Qualified Census Tract (LIHTC)</dt><dd>${yn(inc.qct)}</dd><dt>Difficult Development Area</dt><dd>${yn(inc.dda)}</dd><dt>New Markets Tax Credit tract</dt><dd>${yn(inc.nmtc)}</dd><dt>USDA rural loan eligible</dt><dd>${yn(inc.usda_rural_eligible)}</dd><dt>SBA HUBZone</dt><dd>${yn(inc.hubzone)}</dd>${inc.lihtc_within_400m != null ? `<dt>LIHTC properties within 400 m</dt><dd>${esc(inc.lihtc_within_400m)}</dd>` : ""}</dl>`); }
    if (rent && rent.safmr) out.push(`<h4>Fair market rent · HUD ${esc(rent.year || "")} · ZIP ${esc(rent.zip || "")}</h4><dl>${["br0", "br1", "br2", "br3", "br4"].filter((k) => rent.safmr[k] != null).map((k) => `<dt>${k === "br0" ? "Studio" : k.slice(2) + " bedroom"}</dt><dd>${money0(rent.safmr[k])}/mo</dd>`).join("")}</dl><div class="sub small">HUD Small Area Fair Market Rent (40th percentile gross rent, used for vouchers) — a rent benchmark, not this home's rent.</div>`);
    if (lc) { const row = (l, v) => v ? `<dt>${l}</dt><dd>${esc([v.status, v.type || v.kind, v.since || v.expires].filter(Boolean).join(" · ") || "yes")}${v.source ? ` <span class="sub">(${esc(v.source)})</span>` : ""}</dd>` : "";
      const sales = arr(lc.sales).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
      const body = [row("Rental licence", lc.rental_license), row("Short-term rental licence", lc.str_license), row("Vacant / condemned registry", lc.vacant_or_condemned), row("Dangerous building", lc.dangerous_building),
        lc.hoa ? `<dt>HOA / association</dt><dd>${esc(lc.hoa.name || "")} ${esc(lc.hoa.kind || "")} <span class="sub">(${esc(lc.hoa.confidence || "")} confidence · ${esc(lc.hoa.source || "")})</span></dd>` : "",
        row("Tax abatement", lc.tax_abatement), lc.code_cases_5y != null ? `<dt>Code cases, 5 yrs</dt><dd>${esc(lc.code_cases_5y)}</dd>` : "",
        sales.length ? `<dt>Recorded sales</dt><dd>${sales.slice(0, 5).map((x) => `${esc(x.date)} ${money0(x.price)}`).join("<br>")}</dd>` : ""].join("");
      if (body) out.push(`<h4>City &amp; county registries</h4><dl>${body}</dl>`); }
    const ck = this.cityKey();
    if (ck) out.push(`<div class="sub"><a href="${esc(this.rulesUrl(ck))}" target="_blank" rel="noopener">Rental, short-term rental, occupancy &amp; HOA rules for ${esc(M.city || ck)} ▸</a></div>`);
    out.push(this.assistanceSection(h));
    out.push(this.strSection(h, ck), this.housingDemandSection(h), this.trafficSection(h));
    const oz = h.opportunity_zone;
    if (oz) out.push(`<h4>Opportunity Zone · Treasury</h4><dl><dt>2018 round (to 2028)</dt><dd>${oz.in_zone_2018 ? '<b class="mag">IN ZONE</b>' : "no"}</dd><dt>2027 round (2027–2036)</dt><dd>${oz.in_zone_2027 ? '<b class="mag">IN ZONE</b>' : "no"}</dd>${oz.geoid ? `<dt>Census tract</dt><dd>${esc(oz.geoid)}</dd>` : ""}</dl><div class="sub small">Capital-gains tax incentive for investing in designated low-income tracts. Source: IRS Notice 2018-48, Rev. Proc. 2026-14 via CDFI Fund (public domain).</div>`);
    const hh = h.hail_history;
    if (Array.isArray(hh) || h.flood_zone) out.push(`<h4>Hazards</h4><dl>${Array.isArray(hh) ? `<dt>Radar hail days ≥0.5"</dt><dd>${hh.length ? hh.map(([dd, v]) => `${esc(dd)} ${esc(v)}"`).join(", ") : "none on radar record"}</dd>` : ""}
      ${h.flood_zone ? `<dt>FEMA flood zone</dt><dd>${esc(h.flood_zone)}</dd>` : ""}</dl>`);
    if (Array.isArray(h.window_permits) && h.window_permits.length) {
      const W = h.windows || {};
      out.push(`<h4>Windows · city permits</h4><dl><dt>Window jobs on record</dt><dd>${esc(W.window_permits ?? h.window_permits.length)}${W.last_date ? " · latest " + esc(W.last_date) : ""}</dd>` +
        `<dt>Windows named, all permits added up</dt><dd>${W.stated_windows ? esc(W.stated_windows) + " (a window replaced twice counts twice)" : "number not written"}</dd></dl><ul class="log">` +
        h.window_permits.slice(0, 8).map((w) => `<li><b>${esc(w.date || "")}</b>${esc(w.kind)}${w.count ? " · " + esc(w.count) + " window" + (w.count > 1 ? "s" : "") : ""}` +
          `${w.same_openings ? " · same openings" : ""}${w.maybe_not_done ? ' · <span class="mag">' + esc(w.status || "voided/expired") + ", may not have been done</span>" : ""}` +
          `<br><span class="sub small">${esc(w.text)}${w.also_filed_as ? " (also filed as " + esc(w.also_filed_as.length) + " trade permit" + (w.also_filed_as.length > 1 ? "s" : "") + ")" : ""}</span></li>`).join("") +
        `</ul><div class="sub small">Only what the city permit says. No open record holds a house's total number of windows.</div>`);
    }
    if (Array.isArray(h.permits)) out.push(`<h4>Permits (${h.permits.length})</h4><ul class="log">${h.permits.slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))).slice(0, 8).map((p) => `<li><b>${esc(p.date || "")}</b>${esc(p.type || "")} ${esc(p.work || "")}: ${esc(p.comments || "")}</li>`).join("") || "<li>none on record</li>"}</ul>`);
    if (Array.isArray(h.cases311)) out.push(`<h4>311 cases (${h.cases311.length})</h4><ul class="log">${h.cases311.slice(0, 6).map((c) => `<li><b>${esc(c.date || "")}</b>${esc(c.type || "")}${c.title ? " – " + esc(c.title) : ""}</li>`).join("") || "<li>none on record</li>"}</ul>`);
    out.push(this.streetPhotosSection(h));
    out.push(this.sourcesHtml());
    return out.filter(Boolean).join("\n");
  }
}

// ---------------------------------------------------------------- points_*.txt: base64 records of 10 bytes
// int16 x, int16 y (x point_unit_m, metres east/north of meta.origin), uint16 z (cm above meta.z_base), uint8 class (0 ground, 1 house, 2 tree, 3 other),
// uint8 height above ground (/8 m), int8 roof-plane deviation (x point_unit_m), uint8 intensity
function decodePoints(txts, unit) {
  let n = 0; const bins = txts.map((t) => { const b = Uint8Array.from(atob(t.replace(/\s+/g, "")), (c) => c.charCodeAt(0)); n += Math.floor(b.length / 10); return b; });
  const pos = new Float32Array(n * 3), kind = new Uint8Array(n), hag = new Float32Array(n), dev = new Float32Array(n), inten = new Uint8Array(n); let i = 0;
  for (const b of bins) { const dv = new DataView(b.buffer); for (let o = 0; o + 10 <= b.length; o += 10, i++) {
    pos[i * 3] = dv.getInt16(o, true) * unit; pos[i * 3 + 1] = dv.getInt16(o + 2, true) * unit; pos[i * 3 + 2] = dv.getUint16(o + 4, true) / 100;
    kind[i] = dv.getUint8(o + 6); hag[i] = dv.getUint8(o + 7) / 8; dev[i] = dv.getInt8(o + 8) * unit; inten[i] = dv.getUint8(o + 9); } }
  return { n, pos, kind, hag, dev, inten };
}
function prepParcels(pj) {
  if (!isObj(pj)) return null;
  const parcels = arr(pj.parcels).filter((p) => isObj(p) && okRing(p.ring)).map((p) => ({ ...p, ring: cleanRing(p.ring) }));
  const hoa = arr(pj.hoa).filter((a) => isObj(a) && okRing(a.ring)).map((a) => ({ ...a, ring: cleanRing(a.ring), k: hoaKind(a.kind) }));
  if (!parcels.length && !hoa.length) return null;
  const byId = new Map(parcels.filter((p) => p.id != null).map((p) => [String(p.id), p]));
  return { parcels, hoa, byId, meta: isObj(pj.meta) ? pj.meta : {} };
}
function meshBuilder() {
  const P_ = [], N_ = [], C_ = [], I_ = []; let o = { x: 0, y: 0, z: 0 }, r = [1, 0], f = [0, 1];
  const W = (lx, ly, lz) => [o.x + lx * r[0] + ly * f[0], o.y + lx * r[1] + ly * f[1], o.z + lz], Nn = (nx, ny, nz) => [nx * r[0] + ny * f[0], nx * r[1] + ny * f[1], nz];
  const vert = (p, n, c) => { P_.push(p[0], p[1], p[2]); N_.push(n[0], n[1], n[2]); C_.push(c[0] / 255, c[1] / 255, c[2] / 255); return P_.length / 3 - 1; };
  const quad = (a, b, c, d, n, col) => { const k = vert(a, n, col); vert(b, n, col); vert(c, n, col); vert(d, n, col); I_.push(k, k + 1, k + 2, k, k + 2, k + 3); };
  const api = {
    at(obj) { o = obj; const h = (obj.heading || 0) * RAD; f = [Math.sin(h), Math.cos(h)]; r = [Math.cos(h), -Math.sin(h)]; return api; },
    W,
    box(x0, x1, y0, y1, z0, z1, col) {
      quad(W(x0, y0, z1), W(x1, y0, z1), W(x1, y1, z1), W(x0, y1, z1), Nn(0, 0, 1), col); quad(W(x0, y0, z0), W(x0, y1, z0), W(x1, y1, z0), W(x1, y0, z0), Nn(0, 0, -1), col);
      quad(W(x0, y1, z0), W(x0, y1, z1), W(x1, y1, z1), W(x1, y1, z0), Nn(0, 1, 0), col); quad(W(x0, y0, z0), W(x1, y0, z0), W(x1, y0, z1), W(x0, y0, z1), Nn(0, -1, 0), col);
      quad(W(x1, y0, z0), W(x1, y1, z0), W(x1, y1, z1), W(x1, y0, z1), Nn(1, 0, 0), col); quad(W(x0, y0, z0), W(x0, y0, z1), W(x0, y1, z1), W(x0, y1, z0), Nn(-1, 0, 0), col); return api; },
    cylZ(cx, cy, z0, z1, rad, seg, col) {
      for (let i = 0; i < seg; i++) { const a0 = (i / seg) * 2 * Math.PI, a1 = ((i + 1) / seg) * 2 * Math.PI, am = (a0 + a1) / 2, c0 = [cx + rad * Math.cos(a0), cy + rad * Math.sin(a0)], c1 = [cx + rad * Math.cos(a1), cy + rad * Math.sin(a1)];
        quad(W(c0[0], c0[1], z0), W(c1[0], c1[1], z0), W(c1[0], c1[1], z1), W(c0[0], c0[1], z1), Nn(Math.cos(am), Math.sin(am), 0), col);
        const k = vert(W(cx, cy, z1), Nn(0, 0, 1), col); vert(W(c0[0], c0[1], z1), Nn(0, 0, 1), col); vert(W(c1[0], c1[1], z1), Nn(0, 0, 1), col); I_.push(k, k + 1, k + 2); } return api; },
    prismY(outline, y0, y1, col) {
      const n = outline.length, cxm = outline.reduce((s, p) => s + p[0], 0) / n, czm = outline.reduce((s, p) => s + p[1], 0) / n;
      const cf = vert(W(cxm, y1, czm), Nn(0, 1, 0), col), cb = vert(W(cxm, y0, czm), Nn(0, -1, 0), col);
      for (let i = 0; i < n; i++) { const a = outline[i], b = outline[(i + 1) % n];
        const kf = vert(W(a[0], y1, a[1]), Nn(0, 1, 0), col); vert(W(b[0], y1, b[1]), Nn(0, 1, 0), col); I_.push(cf, kf, kf + 1);
        const kb = vert(W(a[0], y0, a[1]), Nn(0, -1, 0), col); vert(W(b[0], y0, b[1]), Nn(0, -1, 0), col); I_.push(cb, kb + 1, kb);
        const ex = b[0] - a[0], ez = b[1] - a[1], el = Math.hypot(ex, ez) || 1; quad(W(a[0], y0, a[1]), W(b[0], y0, b[1]), W(b[0], y1, b[1]), W(a[0], y1, a[1]), Nn(ez / el, 0, -ex / el), col); } return api; },
    mesh: () => (I_.length ? { attributes: { POSITION: { value: new Float32Array(P_), size: 3 }, NORMAL: { value: new Float32Array(N_), size: 3 }, COLOR_0: { value: new Float32Array(C_), size: 3 } }, indices: { value: new Uint32Array(I_), size: 1 } } : null),
  };
  return api;
}
function faceAtlas(cells) {
  const S = 256, cv = document.createElement("canvas"); cv.width = S * cells.length; cv.height = S; const g = cv.getContext("2d");
  const oct = (cx, cy, r) => { g.beginPath(); for (let i = 0; i < 8; i++) { const a = Math.PI / 8 + (i * Math.PI) / 4; if (i) g.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a)); else g.moveTo(cx + r * Math.cos(a), cy + r * Math.sin(a)); } g.closePath(); };
  cells.forEach((c, i) => {
    const x0 = i * S, cx = x0 + S / 2, cy = S / 2; g.textAlign = "center"; g.textBaseline = "middle";
    if (c === "stop") { g.fillStyle = "#ffffff"; g.fillRect(x0, 0, S, S); g.fillStyle = "#b5121b"; oct(cx, cy, (S / 2 - 11) / Math.cos(Math.PI / 8)); g.fill(); g.fillStyle = "#ffffff"; g.font = "bold 86px Arial, Helvetica, sans-serif"; g.fillText("STOP", cx, cy + 4); }
    else if (c === "yield") { g.fillStyle = "#c8102e"; g.fillRect(x0, 0, S, S); g.fillStyle = "#ffffff"; g.beginPath(); g.moveTo(x0 + 44, 38); g.lineTo(x0 + S - 44, 38); g.lineTo(cx, S - 48); g.closePath(); g.fill(); g.fillStyle = "#c8102e"; g.font = "bold 34px Arial, Helvetica, sans-serif"; g.fillText("YIELD", cx, 90); }
    else { g.fillStyle = "#ffffff"; g.fillRect(x0, 0, S, S); g.strokeStyle = "#111111"; g.lineWidth = 9; g.strokeRect(x0 + 14, 12, S - 28, S - 24); g.fillStyle = "#111111";
      g.font = "bold 42px Arial, Helvetica, sans-serif"; g.fillText("SPEED", cx, 52); g.fillText("LIMIT", cx, 98); g.font = "bold 108px Arial, Helvetica, sans-serif"; g.fillText(String(c.slice(6) || "?"), cx, 182); }
  });
  return cv;
}

// ept.js - stream USGS 3DEP EPT point clouds (s3 usgs-lidar-public) into deck.gl 9.1 (window.deck).
// Only what is on screen is loaded; nodes are chosen by screen-space error, nearest first,
// within a point budget; what leaves the view is unloaded (LRU). Coarse EPT levels are only
// a display LOD: EPT is additive, so the union of a node and all its descendants is every
// point, and zooming in refines down to the deepest nodes.
// LiDAR: USGS 3D Elevation Program (public domain).

export const EPT_CREDIT = 'LiDAR: USGS 3D Elevation Program (public domain)';
const BUCKET = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/';
const RM = 6378137;                      // EPSG:3857 sphere radius
const RD = 40.03e6 / (2 * Math.PI);      // deck.gl / math.gl earth radius (matches worker)
const D2R = Math.PI / 180;
const C_PER_M = 512 / (2 * Math.PI * RM); // deck common units per EPSG:3857 metre
const US_FT = 1200 / 3937;

const CLASS_COLORS = {
  0: [170, 170, 170], 1: [175, 175, 175], 2: [176, 128, 82], 3: [160, 210, 110], 4: [90, 180, 70],
  5: [35, 130, 50], 6: [235, 95, 75], 7: [255, 0, 200], 8: [255, 255, 0], 9: [60, 130, 235],
  10: [150, 100, 200], 11: [130, 130, 130], 12: [255, 200, 0], 13: [255, 255, 100], 14: [255, 230, 150],
  15: [255, 160, 0], 16: [255, 210, 80], 17: [220, 220, 90], 18: [255, 0, 120], 20: [200, 200, 255],
};
// perceptual ramp (turbo-like, trimmed) for elevation
const RAMP = [[48, 18, 59], [65, 69, 171], [70, 117, 237], [57, 162, 252], [27, 207, 212], [36, 236, 166],
  [97, 252, 108], [164, 252, 59], [209, 232, 52], [243, 198, 58], [254, 155, 45], [243, 99, 21], [217, 56, 6], [177, 25, 1]];

function rampColor(t, out, o) {
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const f = t * (RAMP.length - 1); const i = Math.min(RAMP.length - 2, f | 0); const u = f - i;
  const a = RAMP[i], b = RAMP[i + 1];
  out[o] = a[0] + (b[0] - a[0]) * u; out[o + 1] = a[1] + (b[1] - a[1]) * u; out[o + 2] = a[2] + (b[2] - a[2]) * u;
}

class Heap { // max-heap on .pri
  constructor() { this.a = []; }
  get size() { return this.a.length; }
  push(v) { const a = this.a; a.push(v); let i = a.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (a[p].pri >= a[i].pri) break; [a[p], a[i]] = [a[i], a[p]]; i = p; } }
  pop() {
    const a = this.a; const top = a[0]; const last = a.pop();
    if (a.length) { a[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < a.length && a[l].pri > a[m].pri) m = l; if (r < a.length && a[r].pri > a[m].pri) m = r; if (m === i) break; [a[m], a[i]] = [a[i], a[m]]; i = m; } }
    return top;
  }
}

function mercToLng(x) { return x / RM / D2R; }
function mercToLat(y) { return (2 * Math.atan(Math.exp(y / RM)) - Math.PI / 2) / D2R; }
function lngToMerc(l) { return l * D2R * RM; }
function latToMerc(l) { return RM * Math.log(Math.tan(Math.PI / 4 + l * D2R / 2)); }

function pointInRings(x, y, ring, ringOff, r0, r1) { // even-odd over rings [r0,r1)
  let inside = false;
  for (let r = r0; r < r1; r++) {
    const s = ringOff[r], e = ringOff[r + 1];
    for (let i = s, j = e - 1; i < e; j = i++) {
      const xi = ring[2 * i], yi = ring[2 * i + 1], xj = ring[2 * j], yj = ring[2 * j + 1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}
function pointInRing(x, y, pts) { // pts flat [x,y,...]
  let inside = false; const n = pts.length / 2;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = pts[2 * i], yi = pts[2 * i + 1], xj = pts[2 * j], yj = pts[2 * j + 1];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function projectYear(name) { const m = /(?:^|[_-])((?:19|20)\d{2})(?=[_-]|$)/.exec(name); return m ? +m[1] : 0; }

const FT_EPSG = (c) => (c >= 2222 && c <= 2289) || (c >= 2867 && c <= 2924);
function verticalScale(meta) {
  const srs = meta.srs || {};
  const txt = [srs.vertical, srs.wkt && /VERT_CS[\s\S]*$/.exec(srs.wkt || '')?.[0]].filter(Boolean).join(' ').toLowerCase();
  if (/foot|feet|\bft\b|us survey/.test(txt)) return US_FT;
  const rin = meta.reprojection && meta.reprojection.in;
  if (rin) {
    const s = String(rin).toLowerCase();
    if (/\+units=(us-)?ft|foot|feet/.test(s)) return US_FT;
    const m = /epsg:(\d+)/.exec(s); if (m && FT_EPSG(+m[1])) return US_FT;
  }
  return 1;
}

export class EptManager {
  constructor({ onUpdate = () => {}, boundariesUrl = '../lidar/ept_boundaries.geojson', pointBudget = 4_000_000,
    pointSize = 1.5, minZoom = 11, maxRequests = 4, sseThreshold = 1.25, maxProjects = 6, zOffset = 'auto',
    workerUrl = new URL('./vendor/ept/ept-worker.js', import.meta.url).href, workers = 0 } = {}) {
    this.onUpdate = onUpdate; this.boundariesUrl = new URL(boundariesUrl, location.href).href;
    this.pointBudget = pointBudget; this.cacheBudget = Math.round(pointBudget * 1.5);
    this.pointSize = pointSize; this.minZoom = minZoom; this.maxRequests = maxRequests;
    this.sseThreshold = sseThreshold; this.maxProjects = maxProjects;
    this.colorMode = 'elevation'; this.clip = null; this.clipVer = 0;
    this.zMode = zOffset === 'auto' ? 'auto' : 'fixed'; this.zOffset = zOffset === 'auto' ? 0 : +zOffset || 0;
    this.datumLock = null;
    this.elevRange = null; this.projects = new Map(); this.bounds = null; this.boundsState = 'none';
    this.displayed = []; this.wanted = new Set(); this.inflight = new Map(); this.reqSeq = 1;
    this.active = false; this.viewport = null; this.colorQueue = []; this.destroyed = false;
    this.loadedPoints = 0; this._layerCache = new Map(); this._dirty = false; this._travTimer = null;
    const nw = workers || Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 4) - 1));
    this.workers = [];
    for (let i = 0; i < nw; i++) {
      const w = new Worker(workerUrl); w.jobs = 0;
      w.onmessage = (e) => this._onWorker(w, e.data);
      w.onerror = (e) => console.warn('[ept] worker error', e.message);
      this.workers.push(w);
    }
    this._colorTick = this._colorTick.bind(this);
  }

  // ---------------------------------------------------------------- public API
  update(viewport) {
    if (this.destroyed) return;
    this.viewport = viewport;
    const D = window.deck;
    const merc = !D || !D.WebMercatorViewport || viewport instanceof D.WebMercatorViewport;
    const act = merc && viewport && viewport.zoom >= this.minZoom;
    if (!act) {
      if (this.active) { this.active = false; this.wanted = new Set(); this._abortUnwanted(); this._setDisplayed([]); }
      return;
    }
    this.active = true;
    if (this.boundsState === 'none') this._loadBounds();
    if (!this._travTimer) this._travTimer = setTimeout(() => { this._travTimer = null; this._traverse(); }, 60);
  }

  layers() {
    if (!this.active || !window.deck) return [];
    const D = window.deck; const out = [];
    const mm = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -this.zOffset, 1];
    for (const n of this.displayed) {
      const key = n.dataObj;
      let L = this._layerCache.get(n.id);
      if (!L || L.props.data !== key || L.props.pointSize !== this.pointSize || L.props.modelMatrix[14] !== -this.zOffset) {
        L = new D.PointCloudLayer({
          id: n.id, data: key, coordinateSystem: D.COORDINATE_SYSTEM.METER_OFFSETS,
          coordinateOrigin: [n.lng0, n.lat0, 0], modelMatrix: mm, pointSize: this.pointSize,
          sizeUnits: 'pixels', material: false, pickable: false,
        });
        this._layerCache.set(n.id, L);
      }
      out.push(L);
    }
    return out;
  }

  setColorMode(m) {
    if (!['elevation', 'classification', 'intensity', 'rgb'].includes(m) || m === this.colorMode) return;
    this.colorMode = m; this._queueRecolorAll();
  }
  setPointSize(px) { this.pointSize = px; this._changed(); }
  setPointBudget(n) { this.pointBudget = n; this.cacheBudget = Math.round(n * 1.5); this._kick(); }
  /** 'auto' = lock datum to ground at view centre so points sit on a flat basemap; number = metres subtracted (0 = true absolute elevation). */
  setZOffset(v) { if (v === 'auto') { this.zMode = 'auto'; this.datumLock = null; this._autoDatum(); } else { this.zMode = 'fixed'; this.zOffset = +v || 0; } this._changed(); }
  getZOffset() { return this.zOffset; }
  hasRgb() { for (const n of this.displayed) if (n.data && n.data.rgb) return true; return false; }

  setClip(poly) {
    let ring = null;
    if (poly) {
      let c = poly.type === 'Feature' ? poly.geometry : poly;
      if (c.type === 'Polygon') c = c.coordinates[0]; else if (c.type === 'MultiPolygon') c = c.coordinates[0][0];
      ring = c;
    }
    if (ring && ring.length >= 3) {
      const flat = []; let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      for (const [x, y] of ring) { flat.push(x, y); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      this.clip = { flat, bbox: [x0, y0, x1, y1] };
    } else this.clip = null;
    this.clipVer++; this._queueRecolorAll(); this._kick();
  }

  groundAt(lon, lat) {
    const r = this._groundSearch(lon, lat, [1.5, 4, 10]);
    return r;
  }

  stats() {
    let pts = 0; for (const n of this.displayed) pts += n.count;
    let act = 0; for (const p of this.projects.values()) if (p.activeNow) act++;
    let queued = 0; for (const n of this.wanted) if (n.state === 'none') queued++;
    return { projects: act, nodesLoaded: this.displayed.length, points: pts, pending: this.inflight.size + queued,
      inflight: this.inflight.size, cachedPoints: this.loadedPoints, zOffset: this.zOffset, active: this.active,
      projectNames: [...this.projects.values()].filter((p) => p.activeNow).map((p) => p.name),
      maxDepth: this.displayed.reduce((m, n) => Math.max(m, n.d), 0) };
  }

  destroy() {
    this.destroyed = true;
    for (const w of this.workers) w.terminate();
    this.workers = []; this.inflight.clear(); this.projects.clear(); this.displayed = []; this._layerCache.clear();
    clearTimeout(this._travTimer);
  }

  // ---------------------------------------------------------------- loading plumbing
  _post(msg, onDone) {
    const id = this.reqSeq++;
    let w = this.workers[0]; for (const x of this.workers) if (x.jobs < w.jobs) w = x;
    w.jobs++; this.inflight.set(id, { w, onDone, msg });
    w.postMessage({ ...msg, id });
    return id;
  }
  _onWorker(w, d) {
    const job = this.inflight.get(d.id); if (!job) return;
    this.inflight.delete(d.id); w.jobs--;
    if (this.destroyed) return;
    try { job.onDone(d); } catch (e) { console.warn('[ept]', e); }
    this._kick();
  }
  _abort(id) { const j = this.inflight.get(id); if (j) { j.w.postMessage({ type: 'abort', id }); } }
  _kick() { if (this.active && !this._travTimer) this._travTimer = setTimeout(() => { this._travTimer = null; this._traverse(); }, 30); }
  _changed() { if (this._dirty) return; this._dirty = true; requestAnimationFrame(() => { this._dirty = false; if (!this.destroyed) this.onUpdate(); }); }

  _loadBounds() {
    this.boundsState = 'loading';
    this._post({ type: 'bounds', url: this.boundariesUrl }, (d) => {
      if (!d.ok) { this.boundsState = 'none'; console.warn('[ept] boundaries failed', d.error); return; }
      this.bounds = d; this.boundsState = 'ok';
    });
  }

  _project(i) {
    const B = this.bounds; const name = B.names[i];
    let p = this.projects.get(name);
    if (!p) {
      const bb = B.bbox.subarray(i * 4, i * 4 + 4);
      const area = Math.max(1e-6, (bb[2] - bb[0]) * (bb[3] - bb[1]));
      p = { name, idx: i, id: 'ept-' + name, bbox: [...bb], year: projectYear(name), density: B.counts[i] / area,
        state: 'none', meta: null, hier: new Map(), hierReq: new Set(), hierDone: new Set(), nodes: new Map(), imax: 0, activeNow: false };
      this.projects.set(name, p);
    }
    return p;
  }

  _loadMeta(p) {
    p.state = 'loading';
    this._post({ type: 'json', url: BUCKET + p.name + '/ept.json' }, (d) => {
      if (!d.ok) { p.state = d.aborted ? 'none' : 'failed'; if (!d.aborted) console.warn('[ept] ept.json failed', p.name, d.error); return; }
      const m = d.json; const hz = (m.srs && m.srs.horizontal) + '';
      if (hz !== '3857') { p.state = 'failed'; console.warn('[ept] unsupported srs', p.name, hz); return; }
      p.meta = m; p.span = m.span || 128; p.cube = m.bounds; p.conf = m.boundsConforming || m.bounds;
      p.vscale = verticalScale(m); p.dataType = m.dataType; p.schema = m.schema;
      p.ext = m.dataType === 'laszip' ? '.laz' : m.dataType === 'zstandard' ? '.zst' : '.bin';
      p.state = 'ok';
    });
  }

  _loadHier(p, key) {
    p.hierReq.add(key);
    return this._post({ type: 'hier', url: BUCKET + p.name + '/ept-hierarchy/' + key + '.json' }, (d) => {
      if (!d.ok) { p.hierReq.delete(key); if (!d.aborted) { p.hierDone.add(key); console.warn('[ept] hierarchy failed', p.name, key, d.error); } return; }
      // insert in ~6 ms slices so big state-wide hierarchies never block a frame
      const a = d.packed, N = a.length / 5; let i = 0;
      const step = () => {
        if (this.destroyed) return;
        const t0 = performance.now();
        for (; i < N && (i & 1023 || performance.now() - t0 < 6); i++) {
          const o = 5 * i, k = a[o] + '-' + a[o + 1] + '-' + a[o + 2] + '-' + a[o + 3], v = a[o + 4];
          if (v === -1 && p.hier.get(k) > 0) continue;
          p.hier.set(k, v);
        }
        if (i < N) { setTimeout(step, 0); return; }
        p.hierReq.delete(key); p.hierDone.add(key); this._kick();
      };
      step();
    });
  }

  _node(p, key) {
    let n = p.nodes.get(key);
    if (n) return n;
    const [d, x, y, z] = key.split('-').map(Number);
    const c = p.cube, s = 2 ** d;
    const sx = (c[3] - c[0]) / s, sy = (c[4] - c[1]) / s, sz = (c[5] - c[2]) / s;
    const mx0 = c[0] + x * sx, my0 = c[1] + y * sy;
    const vz = p.vscale;
    const z0 = Math.max(c[2] + z * sz, p.conf[2]) * vz, z1 = Math.min(c[2] + (z + 1) * sz, p.conf[5]) * vz;
    n = { id: p.id + '-' + key, key, d, x, y, z, p, count: p.hier.get(key) || 0,
      mb: [mx0, my0, mx0 + sx, my0 + sy], zr: [Math.min(z0, z1), Math.max(z0, z1)],
      lng0: mercToLng(mx0 + sx / 2), lat0: mercToLat(my0 + sy / 2),
      ll: [mercToLng(mx0), mercToLat(my0), mercToLng(mx0 + sx), mercToLat(my0 + sy)],
      spacing: sx / p.span * C_PER_M, state: 'none', data: null, dataObj: null, lastUsed: 0, reqId: 0, colorKey: '' };
    p.nodes.set(key, n);
    return n;
  }

  // ---------------------------------------------------------------- traversal
  _frustum(vp) {
    const M = vp.viewProjectionMatrix; const r = (i) => [M[i], M[4 + i], M[8 + i], M[12 + i]];
    const r0 = r(0), r1 = r(1), r2 = r(2), r3 = r(3);
    const add = (a, b, s) => [a[0] + s * b[0], a[1] + s * b[1], a[2] + s * b[2], a[3] + s * b[3]];
    return [add(r3, r0, 1), add(r3, r0, -1), add(r3, r1, 1), add(r3, r1, -1), add(r3, r2, 1), add(r3, r2, -1)];
  }
  // returns -1 outside, else min distance (common units) from camera to box
  _boxTest(planes, cam, x0, y0, z0, x1, y1, z1) {
    for (const P of planes) {
      const px = P[0] > 0 ? x1 : x0, py = P[1] > 0 ? y1 : y0, pz = P[2] > 0 ? z1 : z0;
      if (P[0] * px + P[1] * py + P[2] * pz + P[3] < 0) return -1;
    }
    const dx = Math.max(x0 - cam[0], 0, cam[0] - x1), dy = Math.max(y0 - cam[1], 0, cam[1] - y1), dz = Math.max(z0 - cam[2], 0, cam[2] - z1);
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  _traverse() {
    if (!this.active || this.destroyed || !this.viewport || this.boundsState !== 'ok') return;
    const t0 = performance.now();
    const vp = this.viewport; const planes = this._frustum(vp); const cam = vp.cameraPosition;
    const upmZ = vp.distanceScales.unitsPerMeter[2];
    const focal = vp.projectionMatrix[5] * vp.height / 2;
    const zo = this.zOffset; const now = t0;
    const minDist = 5 * upmZ; // camera inside a node box: treat as 5 m away
    const zFree = this.zMode === 'auto' && !this.datumLock; // datum unknown yet: don't cull by height
    const cx = vp.longitude, cy = vp.latitude;
    const B = this.bounds; const nb = B.names.length;
    // 1. projects in frustum
    const cands = [];
    for (let i = 0; i < nb; i++) {
      const b = B.bbox;
      const x0 = 256 + lngToMerc(b[i * 4]) * C_PER_M, x1 = 256 + lngToMerc(b[i * 4 + 2]) * C_PER_M;
      const y0 = 256 + latToMerc(b[i * 4 + 1]) * C_PER_M, y1 = 256 + latToMerc(b[i * 4 + 3]) * C_PER_M;
      const dist = this._boxTest(planes, cam, x0, y0, zFree ? -1e9 : (-500 - zo) * upmZ, x1, y1, zFree ? 1e9 : (9000 - zo) * upmZ);
      if (dist < 0) continue;
      const inC = cx >= b[i * 4] && cx <= b[i * 4 + 2] && cy >= b[i * 4 + 1] && cy <= b[i * 4 + 3] &&
        pointInRings(cx, cy, B.ring, B.ringOff, B.featRing[i], B.featRing[i + 1]);
      cands.push({ i, dist, inC });
    }
    cands.sort((a, b) => (b.inC - a.inC) || (a.dist - b.dist));
    const chosen = cands.slice(0, this.maxProjects).map((c) => this._project(c.i));
    // rank: newest survey first, then density
    chosen.sort((a, b) => (b.year - a.year) || (b.density - a.density));
    for (const p of this.projects.values()) p.activeNow = false;
    for (const p of chosen) { p.activeNow = true; if (p.state === 'none') this._loadMeta(p); }
    const ready = chosen.filter((p) => p.state === 'ok');

    // 2. SSE priority traversal
    const heap = new Heap(); const wanted = new Set(); const hierNeed = [];
    const clip = this.clip;
    const nodeSSE = (n, parent) => {
      const x0 = 256 + n.mb[0] * C_PER_M, y0 = 256 + n.mb[1] * C_PER_M, x1 = 256 + n.mb[2] * C_PER_M, y1 = 256 + n.mb[3] * C_PER_M;
      let dist = zFree ? this._boxTest(planes, cam, x0, y0, -1e9, x1, y1, 1e9)
        : this._boxTest(planes, cam, x0, y0, (n.zr[0] - zo) * upmZ, x1, y1, (n.zr[1] - zo) * upmZ);
      if (dist < 0) return -1;
      // EPT cubes are tall; for the error metric use the real z extent of loaded data (own or parent's)
      const tz = n.tz || (parent && (parent.tz || parent.tzi));
      if (tz && !zFree) {
        n.tzi = tz;
        const dx = Math.max(x0 - cam[0], 0, cam[0] - x1), dy = Math.max(y0 - cam[1], 0, cam[1] - y1);
        const z0 = (tz[0] - zo) * upmZ, z1 = (tz[1] - zo) * upmZ, dz = Math.max(z0 - cam[2], 0, cam[2] - z1);
        dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      }
      let sse = n.spacing * focal / Math.max(dist, minDist);
      n._sse = sse; n._clipHit = false;
      if (clip) {
        const b = clip.bbox, l = n.ll;
        const hit = !(l[2] < b[0] || l[0] > b[2] || l[3] < b[1] || l[1] > b[3]);
        sse = hit ? sse * 4 + 1e6 : sse * 0.5;
        n._clipHit = hit;
      }
      return sse;
    };
    ready.forEach((p, rank) => {
      p.rank = rank;
      if (!p.hierDone.has('0-0-0-0')) { if (!p.hierReq.has('0-0-0-0')) hierNeed.push({ p, key: '0-0-0-0', pri: 1e9 }); return; }
      if (!p.hier.has('0-0-0-0')) return;
      const n = this._node(p, '0-0-0-0'); const s = nodeSSE(n);
      if (s >= 0) heap.push({ n, pri: s });
    });
    let budget = 0; let visited = 0;
    while (heap.size && visited < 20000) {
      const { n, pri } = heap.pop(); visited++;
      if (n.count === -1) { // subtree page
        if (!n.p.hierDone.has(n.key) && !n.p.hierReq.has(n.key)) hierNeed.push({ p: n.p, key: n.key, pri });
        n.count = n.p.hier.get(n.key);
        if (n.count === -1) continue;
      }
      if (budget + n.count > this.pointBudget) continue;
      const covered = this._covered(n, ready);
      if (!covered) { wanted.add(n); budget += n.count; n.lastUsed = now; n.sse = pri; }
      // refine while the node's point spacing is still larger than ~sseThreshold px on screen;
      // inside a clip area always refine to the deepest level (every point)
      if (!(clip && n._clipHit) && n._sse <= this.sseThreshold) continue;
      const p = n.p, d1 = n.d + 1;
      for (let i = 0; i < 8; i++) {
        const k = d1 + '-' + (2 * n.x + (i & 1)) + '-' + (2 * n.y + ((i >> 1) & 1)) + '-' + (2 * n.z + ((i >> 2) & 1));
        const c = p.hier.get(k); if (c === undefined) continue;
        const ch = this._node(p, k); ch.count = c;
        const s = nodeSSE(ch, n); if (s >= 0) heap.push({ n: ch, pri: s });
      }
    }
    this.wanted = wanted;
    this._abortUnwanted();
    // 3. schedule requests: hierarchy first (cheap, unblocks), then nodes by priority
    hierNeed.sort((a, b) => b.pri - a.pri);
    for (const h of hierNeed) { if (this.inflight.size >= this.maxRequests) break; this._loadHier(h.p, h.key); }
    const todo = [...wanted].filter((n) => n.state === 'none').sort((a, b) => b.sse - a.sse);
    for (const n of todo) { if (this.inflight.size >= this.maxRequests) break; this._loadNode(n); }
    // 4. display + evict
    const disp = [...wanted].filter((n) => n.state === 'loaded');
    this._setDisplayed(disp);
    this._evict();
    this._updateElevRange();
    if (this.zMode === 'auto') this._autoDatum();
    this.lastTraverseMs = performance.now() - t0;
  }

  _covered(n, ready) {
    if (n.p.rank === 0) return false;
    const ck = ready.map((p) => p.name).join('|');
    if (n._covKey === ck) return n._cov;
    const B = this.bounds; let cov = false;
    // A node of a lower-ranked (older / sparser) survey is skipped when its centre lies inside a
    // higher-ranked survey's footprint: overlapping surveys of the same ground are not stacked.
    // Its descendants are still visited, so areas only the older survey covers still refine.
    const l = n.ll; const x = (l[0] + l[2]) / 2, y = (l[1] + l[3]) / 2;
    for (const q of ready) {
      if (q.rank >= n.p.rank) break;
      const i = q.idx, b = q.bbox;
      if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
      if (pointInRings(x, y, B.ring, B.ringOff, B.featRing[i], B.featRing[i + 1])) { cov = true; break; }
    }
    n._covKey = ck; n._cov = cov; return cov;
  }

  _abortUnwanted() {
    for (const [id, j] of this.inflight) {
      if (j.node && !this.wanted.has(j.node)) { this._abort(id); }
    }
  }

  _loadNode(n) {
    const p = n.p; n.state = 'loading';
    n.reqId = this._post({ type: 'node', url: BUCKET + p.name + '/ept-data/' + n.key + p.ext, dataType: p.dataType,
      schema: p.schema, origin: [n.lng0, n.lat0], vscale: p.vscale }, (d) => {
      if (!d.ok) { n.state = d.aborted ? 'none' : 'failed'; if (!d.aborted) console.warn('[ept] node failed', p.name, n.key, d.error); return; }
      n.data = { pos: d.pos, cls: d.cls, inten: d.inten, rgb: d.rgb, zp: d.zp };
      if (d.n) n.tz = [d.zmin, d.zmax];
      n.count = d.n; n.state = 'loaded'; this.loadedPoints += d.n;
      if (n.d <= 3 && d.ip98 > p.imax) p.imax = d.ip98;
      this._colorize(n);
    });
    this.inflight.get(n.reqId).node = n;
  }

  _setDisplayed(list) {
    const same = list.length === this.displayed.length && list.every((n, i) => n === this.displayed[i]);
    if (same) return;
    const keep = new Set(list.map((n) => n.id));
    for (const id of this._layerCache.keys()) if (!keep.has(id)) this._layerCache.delete(id);
    this.displayed = list; this._changed();
  }

  _evict() {
    if (this.loadedPoints <= this.cacheBudget) return;
    const disp = new Set(this.displayed);
    const cand = [];
    for (const p of this.projects.values()) for (const n of p.nodes.values()) if (n.state === 'loaded' && !disp.has(n) && !this.wanted.has(n)) cand.push(n);
    cand.sort((a, b) => a.lastUsed - b.lastUsed);
    for (const n of cand) {
      if (this.loadedPoints <= this.cacheBudget) break;
      this.loadedPoints -= n.count; n.data = null; n.dataObj = null; n.state = 'none'; n.colorKey = '';
    }
    // drop node records of inactive projects that hold nothing (keeps maps small)
    for (const p of this.projects.values()) {
      if (p.activeNow || p.nodes.size < 5000) continue;
      for (const [k, n] of p.nodes) if (n.state === 'none') p.nodes.delete(k);
    }
  }

  // ---------------------------------------------------------------- colour
  _colorKeyFor(n) {
    const m = this.colorMode === 'rgb' && !(n.data && n.data.rgb) ? 'elevation' : this.colorMode;
    const er = this.elevRange || [0, 1];
    return m + '|' + (m === 'elevation' ? er[0] + ',' + er[1] : '') + '|' + (m === 'intensity' ? n.p.imax : '') + '|' + (this.clip ? this.clipVer : 0);
  }
  _colorize(n) {
    if (!n.data) return;
    const D = n.data, N = n.count, pos = D.pos;
    const key = this._colorKeyFor(n); const mode = key.split('|')[0];
    const col = new Uint8Array(N * 4);
    if (!this.elevRange) this.elevRange = [D.zp[0], Math.max(D.zp[2], D.zp[0] + 10)];
    const [lo, hi] = this.elevRange; const inv = 1 / Math.max(1e-3, hi - lo);
    const imax = Math.max(1, n.p.imax || 255);
    for (let i = 0, o = 0; i < N; i++, o += 4) {
      if (mode === 'elevation') rampColor((pos[3 * i + 2] - lo) * inv, col, o);
      else if (mode === 'classification') { const c = CLASS_COLORS[D.cls[i]] || CLASS_COLORS[1]; col[o] = c[0]; col[o + 1] = c[1]; col[o + 2] = c[2]; }
      else if (mode === 'intensity') { const v = Math.min(1, D.inten[i] / imax); const g = 30 + 225 * Math.sqrt(v); col[o] = g; col[o + 1] = g; col[o + 2] = g; }
      else { col[o] = D.rgb[3 * i]; col[o + 1] = D.rgb[3 * i + 1]; col[o + 2] = D.rgb[3 * i + 2]; }
      col[o + 3] = 255;
    }
    if (this.clip) this._applyClip(n, col);
    n.colorKey = key;
    n.dataObj = { length: N, attributes: { getPosition: { value: pos, size: 3 }, getColor: { value: col, size: 4, normalized: true } } };
    if (this.displayed.includes(n)) this._changed();
  }
  _applyClip(n, col) {
    const c = this.clip, l = n.ll, b = c.bbox; const N = n.count, pos = n.data.pos;
    const dim = (o) => { col[o] *= 0.28; col[o + 1] *= 0.28; col[o + 2] *= 0.28; };
    if (l[2] < b[0] || l[0] > b[2] || l[3] < b[1] || l[1] > b[3]) { for (let o = 0; o < N * 4; o += 4) dim(o); return; }
    // polygon into node metre frame (local linearisation at node origin; fine at node scale)
    const phi0 = n.lat0 * D2R, cosp = Math.cos(phi0);
    const fl = new Float64Array(c.flat.length);
    for (let i = 0; i < c.flat.length; i += 2) { fl[i] = (c.flat[i] - n.lng0) * D2R * RD * cosp; fl[i + 1] = (c.flat[i + 1] - n.lat0) * D2R * RD; }
    for (let i = 0; i < N; i++) if (!pointInRing(pos[3 * i], pos[3 * i + 1], fl)) dim(i * 4);
  }
  _queueRecolorAll() {
    for (const p of this.projects.values()) for (const n of p.nodes.values()) if (n.state === 'loaded' && !this.colorQueue.includes(n)) this.colorQueue.push(n);
    if (!this._colorRaf) this._colorRaf = requestAnimationFrame(this._colorTick);
  }
  _colorTick() {
    this._colorRaf = 0; const t0 = performance.now();
    // displayed first
    const disp = new Set(this.displayed);
    this.colorQueue.sort((a, b) => disp.has(b) - disp.has(a));
    while (this.colorQueue.length && performance.now() - t0 < 10) {
      const n = this.colorQueue.shift();
      if (n.state === 'loaded' && n.colorKey !== this._colorKeyFor(n)) this._colorize(n);
    }
    this._changed();
    if (this.colorQueue.length) this._colorRaf = requestAnimationFrame(this._colorTick);
  }
  _updateElevRange() {
    if (this.colorMode !== 'elevation' || !this.displayed.length) return;
    // range from the finest (closest, on-screen) nodes so a city is not washed out by far mountains
    const md = this.displayed.reduce((m, n) => Math.max(m, n.d), 0);
    const fine = this.displayed.filter((n) => n.d >= md - 2);
    const lo = fine.map((n) => n.data.zp[0]).sort((a, b) => a - b);
    const hi = fine.map((n) => n.data.zp[2]).sort((a, b) => a - b);
    const nl = lo[Math.floor(lo.length * 0.1)], nh = Math.max(hi[Math.min(hi.length - 1, Math.floor(hi.length * 0.9))], nl + 10);
    const cur = this.elevRange; const span = cur ? cur[1] - cur[0] : 0;
    if (!cur || Math.abs(nl - cur[0]) > 0.15 * span || Math.abs(nh - cur[1]) > 0.15 * span) {
      this.elevRange = [Math.round(nl), Math.round(nh)];
      clearTimeout(this._erTimer);
      this._erTimer = setTimeout(() => this._queueRecolorAll(), 400);
    }
  }

  // ---------------------------------------------------------------- ground / datum
  // sorted elevations of loaded ground-class (2) points within r metres of lon/lat
  _groundZs(lon, lat, r) {
    const phi = lat * D2R, lam = lon * D2R, r2 = r * r, zs = [];
    for (const p of this.projects.values()) {
      if (!p.activeNow) continue;
      for (const n of p.nodes.values()) {
        if (n.state !== 'loaded' || !n.data) continue;
        const l = n.ll, mlat = r / 111000, mlon = mlat / Math.cos(phi);
        if (lon < l[0] - mlon || lon > l[2] + mlon || lat < l[1] - mlat || lat > l[3] + mlat) continue;
        const tx = (lam - n.lng0 * D2R) * RD * Math.cos(phi), ty = (phi - n.lat0 * D2R) * RD;
        const pos = n.data.pos, cls = n.data.cls, N = n.count;
        for (let i = 0; i < N; i++) {
          if (cls[i] !== 2) continue;
          const dx = pos[3 * i] - tx, dy = pos[3 * i + 1] - ty;
          if (dx * dx + dy * dy <= r2) zs.push(pos[3 * i + 2]);
        }
      }
    }
    return zs.sort((x, y) => x - y);
  }
  _groundSearch(lon, lat, radii) {
    for (const r of radii) {
      const zs = this._groundZs(lon, lat, r);
      if (zs.length >= 3 || (zs.length && r === radii[radii.length - 1])) return zs[zs.length >> 1];
    }
    return null;
  }
  // Auto datum: a low ground quantile around the view centre becomes z=0 on the flat map, so
  // ground never sinks under the basemap / far plane. Locked until the view moves > 5 km.
  _autoDatum() {
    const vp = this.viewport; if (!vp) return;
    const lk = this.datumLock;
    const far = lk && (Math.abs(vp.longitude - lk.lon) * Math.cos(vp.latitude * D2R) * 111 > 5 || Math.abs(vp.latitude - lk.lat) * 111 > 5);
    if (lk && lk.solid && !far) return;
    if (far) { this.datumLock = null; this._kick(); return; } // new area: re-derive datum with height-agnostic culling
    const zs = this._groundZs(vp.longitude, vp.latitude, 120);
    let g, solid = false;
    if (zs.length >= 30) { g = zs[Math.floor(zs.length * 0.03)]; solid = zs.length >= 3000; }
    else {
      const c = this.displayed.filter((n) => n.d <= 4).map((n) => n.data.zp[0]);
      if (!c.length || lk) return;
      c.sort((x, y) => x - y); g = c[c.length >> 1];
    }
    g = Math.round(g * 10) / 10;
    if (Math.abs(g - this.zOffset) > 0.05) { this.zOffset = g; this._changed(); }
    this.datumLock = { lon: vp.longitude, lat: vp.latitude, solid };
  }
}

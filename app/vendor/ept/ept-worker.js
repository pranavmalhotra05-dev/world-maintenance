// EPT node fetch + decode worker (project code). Classic worker so copc.js / laz-perf.wasm
// resolve relative to this file. Messages:
//   {type:'json', id, url}                         -> {id, ok, json}
//   {type:'node', id, url, dataType, schema, origin:[lng0,lat0], vscale}
//        -> {id, ok, n, pos(Float32 xyz metres offsets), cls(Uint8), inten(Uint16), rgb(Uint8x3|null), zp:[p5,p50,p95], ip98, bytes}
//   {type:'abort', id}
/* global Copc, ZstdCodec */
'use strict';
importScripts('copc.js');

// deck.gl (math.gl web-mercator) earth: circumference 40.03e6 m. Using the same radius makes
// METER_OFFSETS land exactly on the mercator grid deck uses.
const RD = 40.03e6 / (2 * Math.PI);
const RM = 6378137; // EPSG:3857 sphere
const D2R = Math.PI / 180;

const aborts = new Map();
let zstdPromise = null;

function getZstd() {
  if (!zstdPromise) {
    zstdPromise = new Promise((resolve, reject) => {
      try {
        self.window = self.window || {};
        self.document = self.document || {};
        importScripts('zstd-codec.js');
        self.window.ZstdCodec.run((z) => resolve(z));
      } catch (e) { reject(e); }
    });
  }
  return zstdPromise;
}

async function fetchBuf(id, url) {
  const ac = new AbortController();
  aborts.set(id, ac);
  try {
    const r = await fetch(url, { signal: ac.signal, mode: 'cors', credentials: 'omit' });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
    return await r.arrayBuffer();
  } finally { aborts.delete(id); }
}

// Build getters for binary EPT (schema order, little-endian, packed).
function schemaReader(schema, dv) {
  let off = 0; const dims = {};
  for (const d of schema) {
    const size = d.size, type = d.type, o = off;
    let get;
    if (type === 'float') get = size === 8 ? (b) => dv.getFloat64(b + o, true) : (b) => dv.getFloat32(b + o, true);
    else if (type === 'signed') get = size === 1 ? (b) => dv.getInt8(b + o) : size === 2 ? (b) => dv.getInt16(b + o, true) : size === 4 ? (b) => dv.getInt32(b + o, true) : (b) => Number(dv.getBigInt64(b + o, true));
    else get = size === 1 ? (b) => dv.getUint8(b + o) : size === 2 ? (b) => dv.getUint16(b + o, true) : size === 4 ? (b) => dv.getUint32(b + o, true) : (b) => Number(dv.getBigUint64(b + o, true));
    if (d.scale !== undefined) { const s = d.scale, of = d.offset || 0, g = get; get = (b) => g(b) * s + of; }
    dims[d.name] = get; off += size;
  }
  return { dims, stride: off };
}

function pct(arr, n, qs) {
  // sampled percentiles
  const step = Math.max(1, Math.floor(n / 4000)); const s = [];
  for (let i = 0; i < n; i += step) s.push(arr[i]);
  s.sort((a, b) => a - b);
  return qs.map((q) => s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0);
}

async function decodeNode(msg) {
  const buf = await fetchBuf(msg.id, msg.url);
  const t0 = performance.now();
  let n, gx, gy, gz, gi, gc, gr, gg, gb;
  if (msg.dataType === 'laszip') {
    const u = new Uint8Array(buf);
    const header = Copc.Las.Header.parse(u.subarray(0, Math.min(u.length, 375)));
    const pd = await Copc.Las.PointData.decompressFile(u);
    const view = Copc.Las.View.create(pd, header, []);
    n = view.pointCount;
    const D = view.dimensions;
    gx = view.getter('X'); gy = view.getter('Y'); gz = view.getter('Z');
    gi = D.Intensity ? view.getter('Intensity') : null;
    gc = D.Classification ? view.getter('Classification') : null;
    if (D.Red) { gr = view.getter('Red'); gg = view.getter('Green'); gb = view.getter('Blue'); }
  } else {
    let ab = buf;
    if (msg.dataType === 'zstandard') {
      const z = await getZstd();
      const out = new z.Streaming().decompress(new Uint8Array(buf));
      ab = out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength);
    }
    const dv = new DataView(ab);
    const { dims, stride } = schemaReader(msg.schema, dv);
    n = Math.floor(ab.byteLength / stride);
    const w = (f) => f ? (i) => f(i * stride) : null;
    gx = w(dims.X); gy = w(dims.Y); gz = w(dims.Z);
    gi = w(dims.Intensity); gc = w(dims.Classification);
    if (dims.Red) { gr = w(dims.Red); gg = w(dims.Green); gb = w(dims.Blue); }
  }

  const [lng0, lat0] = msg.origin; const lam0 = lng0 * D2R, phi0 = lat0 * D2R;
  const vs = msg.vscale || 1;
  const pos = new Float32Array(n * 3);
  const cls = new Uint8Array(n);
  const inten = new Uint16Array(n);
  const rgb16 = gr ? new Uint16Array(n * 3) : null;
  const zs = new Float32Array(n);
  let rgbMax = 0;
  for (let i = 0; i < n; i++) {
    const x = gx(i), y = gy(i);
    const lam = x / RM;
    const phi = 2 * Math.atan(Math.exp(y / RM)) - Math.PI / 2;
    const z = gz(i) * vs;
    pos[3 * i] = (lam - lam0) * RD * Math.cos(phi);
    pos[3 * i + 1] = (phi - phi0) * RD;
    pos[3 * i + 2] = z;
    zs[i] = z;
    if (gc) cls[i] = gc(i);
    if (gi) inten[i] = gi(i);
    if (rgb16) {
      const r = gr(i), g = gg(i), b = gb(i);
      rgb16[3 * i] = r; rgb16[3 * i + 1] = g; rgb16[3 * i + 2] = b;
      if (r > rgbMax) rgbMax = r; if (g > rgbMax) rgbMax = g; if (b > rgbMax) rgbMax = b;
    }
  }
  let rgb = null;
  if (rgb16 && rgbMax > 0) {
    rgb = new Uint8Array(n * 3);
    const sh = rgbMax > 255 ? 1 / 256 : 1;
    for (let i = 0; i < n * 3; i++) rgb[i] = rgb16[i] * sh;
  }
  const zp = pct(zs, n, [0.05, 0.5, 0.95]);
  let zmin = Infinity, zmax = -Infinity;
  for (let i = 0; i < n; i++) { const z = zs[i]; if (z < zmin) zmin = z; if (z > zmax) zmax = z; }
  const ip98 = gi ? pct(inten, n, [0.98])[0] : 0;
  return { n, pos, cls, inten, rgb, zp, zmin, zmax, ip98, bytes: buf.byteLength, ms: performance.now() - t0 };
}

self.onmessage = async (ev) => {
  const msg = ev.data;
  if (msg.type === 'abort') { const a = aborts.get(msg.id); if (a) a.abort(); return; }
  try {
    if (msg.type === 'bounds') {
      // Compact project footprints so the main thread never parses the 4 MB GeoJSON.
      const buf = await fetchBuf(msg.id, msg.url);
      const gj = JSON.parse(new TextDecoder().decode(buf));
      const F = gj.features || [];
      const names = [], bbox = new Float64Array(F.length * 4), counts = new Float64Array(F.length);
      const featRing = new Int32Array(F.length + 1); const ringOff = [0]; const pts = [];
      let ri = 0;
      F.forEach((f, i) => {
        const p = f.properties || {}; names.push(p.name); counts[i] = p.count || 0;
        featRing[i] = ri;
        const g = f.geometry || {}; const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
        let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
        for (const poly of polys) for (const ring of poly) {
          for (const c of ring) { pts.push(c[0], c[1]); if (c[0] < x0) x0 = c[0]; if (c[0] > x1) x1 = c[0]; if (c[1] < y0) y0 = c[1]; if (c[1] > y1) y1 = c[1]; }
          ringOff.push(pts.length / 2); ri++;
        }
        const bb = p.bbox || [x0, y0, x1, y1];
        bbox.set(bb, i * 4);
      });
      featRing[F.length] = ri;
      const ring = new Float64Array(pts), ro = new Int32Array(ringOff);
      self.postMessage({ id: msg.id, ok: true, names, bbox, counts, featRing, ringOff: ro, ring }, [bbox.buffer, counts.buffer, featRing.buffer, ro.buffer, ring.buffer]);
    } else if (msg.type === 'hier') {
      // EPT hierarchy page -> packed Int32 [d,x,y,z,count]* (avoids cloning a 50k-key object to the main thread)
      const buf = await fetchBuf(msg.id, msg.url);
      const h = JSON.parse(new TextDecoder().decode(buf));
      const keys = Object.keys(h); const out = new Int32Array(keys.length * 5);
      keys.forEach((k, i) => { const a = k.split('-'); out[5 * i] = +a[0]; out[5 * i + 1] = +a[1]; out[5 * i + 2] = +a[2]; out[5 * i + 3] = +a[3]; out[5 * i + 4] = h[k]; });
      self.postMessage({ id: msg.id, ok: true, packed: out }, [out.buffer]);
    } else if (msg.type === 'json') {
      const buf = await fetchBuf(msg.id, msg.url);
      const json = JSON.parse(new TextDecoder().decode(buf));
      self.postMessage({ id: msg.id, ok: true, json });
    } else if (msg.type === 'node') {
      const r = await decodeNode(msg);
      const tr = [r.pos.buffer, r.cls.buffer, r.inten.buffer];
      if (r.rgb) tr.push(r.rgb.buffer);
      self.postMessage({ id: msg.id, ok: true, ...r }, tr);
    }
  } catch (e) {
    self.postMessage({ id: msg.id, ok: false, aborted: e && e.name === 'AbortError', error: String(e && e.message || e) });
  }
};

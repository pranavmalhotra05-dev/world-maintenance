/* Shared visual themes for the USA map (index.html) and the 3D block viewer (view.html).

   <script src="theme.js"></script>   (load before the page's own script)

   window.Themes
     .list                     [{id, label}]
     .current()                the active theme object (see THEMES below)
     .set(id)                  switch theme (persists in localStorage, fires listeners)
     .on(fn)                   fn(theme) on every change (also called once immediately)
     .ramp(t, alpha?)          [r,g,b,a] on the theme's sequential ramp, t in 0..1 (choropleths, height colouring)
     .rgb(name, alpha?)        [r,g,b,a] for a named palette colour (ground, building, tree, other, water, road,
                               roof, roofEdge, select, pin, accent, warn, danger, ok, grid, sky)
     .mountPicker(el)          renders a labelled <select> into el
   CSS: sets the page's CSS variables (--void --glass --edge --ink --dim --cyan --amber --red --green --magenta --lime
   --f-display --f-body --f-mono) on :root, sets html[data-skin=<id>], and maintains one fixed overlay
   <div id="theme-fx"> above the map (pointer-events: none) for scanlines / grain / vignette / tint / bloom.
   Themes are generic styles. None reproduces a specific game, agency or brand (no logos, seals or trade dress).
*/
(function () {
  const hex = (h) => { h = h.replace("#", ""); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; };

  const THEMES = {
    hud: {
      label: "Default HUD",
      fonts: "family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500",
      css: { void: "#05070b", glass: "rgba(9,13,20,.86)", edge: "rgba(120,190,255,.22)", ink: "#e2ebf5", dim: "#8b9bb0",
        cyan: "#4fdcff", amber: "#ffb84d", red: "#ff5a5a", green: "#6cdc8c", magenta: "#ff3fb4", lime: "#6cff9e",
        "f-display": '"Chakra Petch", "Bahnschrift", sans-serif', "f-body": '"IBM Plex Sans", "Segoe UI", sans-serif', "f-mono": '"IBM Plex Mono", Consolas, monospace' },
      pal: { ground: "#4a5a6e", building: "#ffb84d", tree: "#5fd38a", other: "#9aa8bb", water: "#2f7fd0", road: "#3a4a60", roof: "#ffcf80",
        roofEdge: "#ffe2b0", select: "#ffd24d", pin: "#4fdcff", accent: "#4fdcff", warn: "#ffb84d", danger: "#ff5a5a", ok: "#6cdc8c", grid: "#1d3550", sky: "#05070b" },
      ramp: ["#071a33", "#0c3a66", "#126c99", "#1aa3c4", "#4fdcff", "#ffd27a", "#ffb84d", "#ffffff"],
      fx: {},
    },
    tactical: {
      label: "Tactical",
      fonts: "family=Black+Ops+One&family=Share+Tech+Mono&family=Barlow+Condensed:wght@400;500;600",
      css: { void: "#0b0d08", glass: "rgba(20,23,14,.9)", edge: "rgba(190,180,120,.28)", ink: "#e4dfc8", dim: "#9a9478",
        cyan: "#c8b560", amber: "#ff9f1c", red: "#e4572e", green: "#8fae5a", magenta: "#d98c3f", lime: "#b5c96a",
        "f-display": '"Black Ops One", "Impact", sans-serif', "f-body": '"Barlow Condensed", "Arial Narrow", sans-serif', "f-mono": '"Share Tech Mono", Consolas, monospace' },
      pal: { ground: "#5b5a45", building: "#d9c27a", tree: "#6f8f3a", other: "#8a8670", water: "#3f5f6a", road: "#3e3d2e", roof: "#e6d08a",
        roofEdge: "#fff0b0", select: "#ff9f1c", pin: "#ff9f1c", accent: "#c8b560", warn: "#ff9f1c", danger: "#e4572e", ok: "#8fae5a", grid: "#3a3a26", sky: "#0b0d08" },
      ramp: ["#1a1c10", "#2f3418", "#4b5524", "#6f7f34", "#a3a550", "#d4c36a", "#ff9f1c", "#fff3c4"],
      fx: { grid: "rgba(200,181,96,.07)", vignette: 0.55, reticle: true },
    },
    nightvision: {
      label: "Night Vision",
      fonts: "family=VT323&family=Share+Tech+Mono",
      css: { void: "#010601", glass: "rgba(2,14,4,.88)", edge: "rgba(90,255,120,.3)", ink: "#b8ffbf", dim: "#5fae68",
        cyan: "#7dff8a", amber: "#d7ff7a", red: "#ffffff", green: "#7dff8a", magenta: "#c6ff9e", lime: "#d7ff7a",
        "f-display": '"VT323", "Share Tech Mono", monospace', "f-body": '"Share Tech Mono", Consolas, monospace', "f-mono": '"Share Tech Mono", Consolas, monospace' },
      pal: { ground: "#1f6b2a", building: "#b6ffb0", tree: "#4fd162", other: "#3d9a48", water: "#0e3a14", road: "#164d1f", roof: "#d8ffd0",
        roofEdge: "#ffffff", select: "#ffffff", pin: "#e6ffe0", accent: "#7dff8a", warn: "#d7ff7a", danger: "#ffffff", ok: "#7dff8a", grid: "#0f3a16", sky: "#010601" },
      ramp: ["#000800", "#03250a", "#0a4a16", "#167a26", "#2fb043", "#6fe07a", "#b8ffbf", "#ffffff"],
      fx: { tint: "rgba(40,255,80,.10)", scanlines: 0.16, grain: 0.22, vignette: 0.85, bloom: true, flicker: true },
    },
    thermal: {
      label: "Thermal",
      fonts: "family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono",
      css: { void: "#05020a", glass: "rgba(12,6,18,.88)", edge: "rgba(255,140,60,.28)", ink: "#f3e6dc", dim: "#a08c86",
        cyan: "#ffb347", amber: "#ff7a1a", red: "#ffffff", green: "#9b59ff", magenta: "#e0287a", lime: "#ffe066",
        "f-display": '"Rajdhani", "Bahnschrift", sans-serif', "f-body": '"Rajdhani", "Segoe UI", sans-serif', "f-mono": '"Share Tech Mono", Consolas, monospace' },
      // "ironbow": cold = black/indigo, warm = orange, hottest = white. Height and roof-sun colouring use this ramp.
      pal: { ground: "#2a1446", building: "#ff8a2a", tree: "#3b1f7a", other: "#6a2a7a", water: "#0a0420", road: "#1c0d33", roof: "#ffb347",
        roofEdge: "#fff3c0", select: "#ffffff", pin: "#ffe066", accent: "#ffb347", warn: "#ff7a1a", danger: "#ffffff", ok: "#9b59ff", grid: "#1d0e2e", sky: "#05020a" },
      ramp: ["#000004", "#1d0b4a", "#55137a", "#9c1f6e", "#d9453a", "#f6861d", "#fcd34a", "#ffffe0"],
      fx: { vignette: 0.6, grain: 0.08, heat: true },
    },
    intel: {
      label: "Intel Ops",
      fonts: "family=Special+Elite&family=Courier+Prime:wght@400;700&family=IBM+Plex+Sans+Condensed:wght@400;600",
      css: { void: "#0a0a0a", glass: "rgba(16,16,16,.93)", edge: "rgba(255,255,255,.22)", ink: "#ececec", dim: "#9a9a9a",
        cyan: "#ffffff", amber: "#d32f2f", red: "#ff3b3b", green: "#bdbdbd", magenta: "#d32f2f", lime: "#ffffff",
        "f-display": '"Special Elite", "Courier New", monospace', "f-body": '"IBM Plex Sans Condensed", "Arial Narrow", sans-serif', "f-mono": '"Courier Prime", "Courier New", monospace' },
      pal: { ground: "#4d4d4d", building: "#f2f2f2", tree: "#8a8a8a", other: "#6b6b6b", water: "#262626", road: "#333333", roof: "#ffffff",
        roofEdge: "#ffffff", select: "#e53935", pin: "#e53935", accent: "#ffffff", warn: "#e53935", danger: "#ff3b3b", ok: "#bdbdbd", grid: "#222222", sky: "#0a0a0a" },
      ramp: ["#0d0d0d", "#262626", "#404040", "#5e5e5e", "#808080", "#a8a8a8", "#d32f2f", "#ff5252"],
      fx: { grain: 0.14, vignette: 0.5, stamp: "RESTRICTED · OPEN SOURCE", redact: true },
    },
    neon80s: {
      label: "Neon '80s",
      fonts: "family=Monoton&family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono",
      css: { void: "#0c0418", glass: "rgba(26,8,40,.84)", edge: "rgba(255,90,200,.35)", ink: "#ffe9f7", dim: "#c69ac0",
        cyan: "#29f3ff", amber: "#ff5fc8", red: "#ff3d6e", green: "#29f3ff", magenta: "#ff5fc8", lime: "#ffd66b",
        "f-display": '"Monoton", "Rajdhani", sans-serif', "f-body": '"Rajdhani", "Segoe UI", sans-serif', "f-mono": '"Share Tech Mono", Consolas, monospace' },
      pal: { ground: "#4a2a6a", building: "#ff5fc8", tree: "#29f3ff", other: "#8a5ab0", water: "#1b2b8a", road: "#2a1450", roof: "#ff8ad8",
        roofEdge: "#ffe0f6", select: "#ffd66b", pin: "#29f3ff", accent: "#29f3ff", warn: "#ffd66b", danger: "#ff3d6e", ok: "#29f3ff", grid: "#3a1260", sky: "#0c0418" },
      ramp: ["#120428", "#2b0b5c", "#5a1287", "#9b1fa8", "#e0329e", "#ff6f91", "#ffb36b", "#fff1a8"],
      fx: { sunset: true, vignette: 0.4, scanlines: 0.06, glow: true },
    },
    techno: {
      label: "Techno / Cyber",
      fonts: "family=Orbitron:wght@500;700;900&family=Share+Tech+Mono&family=Exo+2:wght@400;500;600",
      css: { void: "#03020a", glass: "rgba(8,6,22,.86)", edge: "rgba(120,80,255,.4)", ink: "#e8e6ff", dim: "#8d86c0",
        cyan: "#00e5ff", amber: "#ff2bd6", red: "#ff2b5e", green: "#39ff14", magenta: "#ff2bd6", lime: "#39ff14",
        "f-display": '"Orbitron", "Bahnschrift", sans-serif', "f-body": '"Exo 2", "Segoe UI", sans-serif', "f-mono": '"Share Tech Mono", Consolas, monospace' },
      pal: { ground: "#2a2470", building: "#ff2bd6", tree: "#39ff14", other: "#5a4ab0", water: "#0a1a6a", road: "#18144a", roof: "#00e5ff",
        roofEdge: "#ffffff", select: "#39ff14", pin: "#ff2bd6", accent: "#00e5ff", warn: "#ff2bd6", danger: "#ff2b5e", ok: "#39ff14", grid: "#1a1250", sky: "#03020a" },
      ramp: ["#05021a", "#1a0b5e", "#3d10a8", "#7a12d6", "#c214d9", "#ff2bd6", "#00e5ff", "#eaffff"],
      fx: { grid: "rgba(0,229,255,.06)", scanlines: 0.05, glow: true, glitch: true, wireframe: true },
    },
  };

  const KEY = "wmosint.theme";
  let cur = THEMES[localStorage.getItem(KEY)] ? localStorage.getItem(KEY) : "hud";
  const subs = [];
  const loaded = new Set();

  function loadFonts(t) {
    if (!t.fonts || loaded.has(t.fonts)) return;
    loaded.add(t.fonts);
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = `https://fonts.googleapis.com/css2?${t.fonts}&display=swap`;
    document.head.appendChild(l);
  }

  function styleTag() {
    let s = document.getElementById("theme-fx-style");
    if (s) return s;
    s = document.createElement("style"); s.id = "theme-fx-style";
    s.textContent = `
#theme-fx{position:fixed;inset:0;pointer-events:none;z-index:1;overflow:hidden}
#theme-fx>div{position:absolute;inset:0}
#theme-fx .scan{background:repeating-linear-gradient(0deg,rgba(0,0,0,var(--fx-scan,0)) 0 1px,transparent 1px 3px)}
#theme-fx .grain{opacity:var(--fx-grain,0);background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .55 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");animation:fxgrain .6s steps(4) infinite;mix-blend-mode:overlay}
#theme-fx .vig{background:radial-gradient(ellipse at center,transparent 45%,rgba(0,0,0,var(--fx-vig,0)) 100%)}
#theme-fx .tint{background:var(--fx-tint,transparent);mix-blend-mode:screen}
#theme-fx .grid{background-image:linear-gradient(var(--fx-grid,transparent) 1px,transparent 1px),linear-gradient(90deg,var(--fx-grid,transparent) 1px,transparent 1px);background-size:48px 48px}
#theme-fx .sunset{background:linear-gradient(180deg,rgba(255,95,200,.10) 0%,transparent 35%,transparent 70%,rgba(41,243,255,.08) 100%);mix-blend-mode:screen}
#theme-fx .reticle{background:
  linear-gradient(var(--amber),var(--amber)) center/1px 26px no-repeat,
  linear-gradient(var(--amber),var(--amber)) center/26px 1px no-repeat;opacity:.55}
#theme-fx .stamp{inset:auto 18px 14px auto;font:400 13px var(--f-display);letter-spacing:.3em;color:var(--red);border:2px solid var(--red);padding:3px 10px;transform:rotate(-4deg);opacity:.7}
#theme-fx .flick{animation:fxflick 4s infinite}
@keyframes fxgrain{0%{transform:translate(0,0)}25%{transform:translate(-3%,2%)}50%{transform:translate(2%,-3%)}75%{transform:translate(-2%,-1%)}100%{transform:translate(0,0)}}
@keyframes fxflick{0%,96%,100%{opacity:1}97%{opacity:.86}98%{opacity:1}99%{opacity:.9}}
@keyframes fxglitch{0%{transform:translate(0)}20%{transform:translate(-2px,1px)}40%{transform:translate(2px,-1px)}60%{transform:translate(-1px,0)}80%{transform:translate(1px,1px)}100%{transform:translate(0)}}
html[data-skin="nightvision"] #deck{filter:saturate(.25) brightness(1.15) contrast(1.15)}
html[data-skin="nightvision"] #deck.fx-bloom, html[data-skin="nightvision"] #deck{filter:saturate(.2) brightness(1.2) contrast(1.15) drop-shadow(0 0 2px rgba(125,255,138,.5))}
html[data-skin="neon80s"] h1, html[data-skin="techno"] h1{text-shadow:0 0 8px var(--magenta),0 0 22px var(--magenta)}
html[data-skin="neon80s"] h4, html[data-skin="techno"] h4{text-shadow:0 0 6px currentColor}
html[data-skin="nightvision"] *{text-shadow:0 0 4px rgba(125,255,138,.45)}
html[data-skin="intel"] .redact{background:#000;color:transparent;border-radius:1px;box-shadow:0 0 0 1px #000}
html[data-skin="techno"] .glitch{animation:fxglitch .25s 3}
.theme-pick{display:flex;align-items:center;gap:8px}
.theme-pick select{background:var(--glass);color:var(--ink);border:1px solid var(--edge);border-radius:4px;padding:3px 6px;font:500 12px var(--f-mono)}
`;
    document.head.appendChild(s);
    return s;
  }

  function overlay(t) {
    styleTag();
    let o = document.getElementById("theme-fx");
    if (!o) { o = document.createElement("div"); o.id = "theme-fx"; o.setAttribute("aria-hidden", "true"); document.body.appendChild(o); }
    const f = t.fx || {};
    const r = document.documentElement.style;
    r.setProperty("--fx-scan", f.scanlines || 0); r.setProperty("--fx-grain", f.grain || 0); r.setProperty("--fx-vig", f.vignette || 0);
    r.setProperty("--fx-tint", f.tint || "transparent"); r.setProperty("--fx-grid", f.grid || "transparent");
    o.innerHTML = [
      f.tint ? '<div class="tint"></div>' : "", f.grid ? '<div class="grid"></div>' : "", f.sunset ? '<div class="sunset"></div>' : "",
      f.scanlines ? '<div class="scan"></div>' : "", f.grain ? '<div class="grain"></div>' : "", f.vignette ? '<div class="vig"></div>' : "",
      f.reticle ? '<div class="reticle"></div>' : "", f.stamp ? `<div class="stamp">${f.stamp}</div>` : "",
    ].join("");
    o.className = f.flicker ? "flick" : "";
  }

  function apply() {
    const t = THEMES[cur];
    loadFonts(t);
    const r = document.documentElement;
    for (const [k, v] of Object.entries(t.css)) r.style.setProperty(`--${k}`, v);
    r.dataset.skin = cur;
    if (document.body) overlay(t); else document.addEventListener("DOMContentLoaded", () => overlay(t), { once: true });
    const obj = api.current();
    subs.forEach((fn) => { try { fn(obj); } catch (e) { console.error(e); } });
  }

  const rampCache = {};
  function ramp(t, a = 255) {
    const stops = (rampCache[cur] ||= THEMES[cur].ramp.map(hex));
    t = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(t)), f = t - i, A = stops[i], B = stops[i + 1];
    return [Math.round(A[0] + (B[0] - A[0]) * f), Math.round(A[1] + (B[1] - A[1]) * f), Math.round(A[2] + (B[2] - A[2]) * f), a];
  }

  const api = {
    list: Object.entries(THEMES).map(([id, t]) => ({ id, label: t.label })),
    current: () => ({ id: cur, ...THEMES[cur] }),
    set(id) { if (!THEMES[id] || id === cur) return; cur = id; localStorage.setItem(KEY, id); apply(); },
    on(fn) { subs.push(fn); try { fn(api.current()); } catch (e) { console.error(e); } },
    ramp,
    rgb(name, a = 255) { const h = THEMES[cur].pal[name] || THEMES.hud.pal[name] || "#ffffff"; return [...hex(h), a]; },
    mountPicker(el) {
      if (!el) return;
      el.classList.add("theme-pick");
      el.innerHTML = `<label class="lbl" for="theme-select">Theme</label><select id="theme-select">${api.list.map((t) => `<option value="${t.id}">${t.label}</option>`).join("")}</select>`;
      const s = el.querySelector("select"); s.value = cur;
      s.addEventListener("change", () => api.set(s.value));
      api.on((t) => { s.value = t.id; });
    },
  };
  window.Themes = api;
  apply();
})();

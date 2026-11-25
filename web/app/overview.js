/* overview.js — county radar data for the World Maintenance app: metrics, optional-input merge, county dossier card, local search, tract metrics.
   Ported from web/usa/index.html + web/usa/globe.html (copied code kept close to the original so fixes can be mirrored).
   Classic script: shares globals with main.js (loaded after theme.js, before main.js). */


// ===================== copied from index.html (metrics, optional inputs, helpers) =====================
const METRICS = [
  { k: "overlap", label: "Overlap score", unit: "/100", fmt: v => v.toFixed(1), note: "How many free data layers stack up: LiDAR recency, density, re-flights, hail 2015–24, homes. Geometric mean, 0–100." },
  { k: "lidar_density", label: "LiDAR density", unit: " pts/m²", fmt: v => v.toFixed(1), note: "Laser points per m² in the newest USGS 3DEP project covering ≥30% of the county." },
  { k: "lidar_year", label: "LiDAR newest year", unit: "", fmt: v => String(v), note: "Year of the newest USGS 3DEP LiDAR flight covering ≥30% of the county." },
  { k: "hail24", label: "Hail reports · 24 mo", unit: "", fmt: v => v.toLocaleString(), note: "NOAA SPC hail reports (all sizes) in the last 24 months." },
  { k: "hail24_max", label: "Max hail size · 24 mo", unit: " in", fmt: v => v.toFixed(2), note: "Largest hailstone reported to NOAA SPC in the last 24 months." },
  { k: "wind24", label: "Wind reports · 24 mo", unit: "", fmt: v => v.toLocaleString(), note: "NOAA SPC damaging-wind reports in the last 24 months." },
  { k: "torn24", label: "Tornado reports · 24 mo", unit: "", fmt: v => v.toLocaleString(), note: "NOAA SPC tornado reports in the last 24 months." },
  { k: "nri_hail", label: "FEMA NRI hail risk", unit: "", fmt: v => v.toFixed(1), note: "FEMA National Risk Index hail risk score (0–100, national percentile)." },
  { k: "nri_risk", label: "FEMA NRI overall risk", unit: "", fmt: v => v.toFixed(1), note: "FEMA National Risk Index composite risk score, all 18 hazards (0–100)." },
  { k: "home_value", label: "Median home value", unit: "", fmt: v => "$" + Math.round(v / 1000).toLocaleString() + "k", note: "Census ACS 2020–24 5-year, B25077 owner-occupied median value." },
  { k: "year_built", label: "Median year built", unit: "", fmt: v => String(v), note: "Census ACS 2020–24 5-year, B25035 median year structure built." },
  { k: "oz18_share", label: "Opportunity Zones · 2018 round", unit: "", fmt: v => (v * 100).toFixed(1) + "% of tracts", note: "Share of the county's census tracts designated Qualified Opportunity Zones (IRS Notice 2018-48 / 2019-42; in force to 2028-12-31). Treasury · CDFI Fund, public domain." },
  { k: "oz27_share", label: "Opportunity Zones · 2027 round", unit: "", fmt: v => (v * 100).toFixed(1) + "% of tracts", note: "Share of tracts in the new round announced 2026-11-05 (Rev. Proc. 2026-14), effective 2027-01-01 to 2036-12-31. Treasury · CDFI Fund, public domain." },
  { k: "sf_detached", label: "Single-family homes", unit: "", fmt: v => v.toLocaleString(), note: "Detached single-family homes, Census ACS 2019–23 B25024." },
];
// 8 equal-count bands; colours come from the active theme's ramp (theme.js)
const NBANDS = 8;
const T = window.Themes;
const skin = () => T.current().id;
const bandColor = i => T.ramp(i / (NBANDS - 1)).slice(0, 3);
const NODATA_A = 46;   // no-data counties: theme 'other' at low alpha
const css = c => `rgba(${c[0]},${c[1]},${c[2]},${(c[3] ?? 255) / 255})`;
const N_TOTAL = 3222;

let OZ = null, OZ_ON = false, OZ_C = null;

// ---------- optional visitor / short-term-rental inputs (produced by other pipelines; every one may be missing)
// Each loads with .catch → null; a missing file just hides its metrics, layers and card lines.
let STR = null, AIR = null, ECON = null, EVT = null;
let AIRPORTS = [], VENUES = [], AIR_ON = false, VEN_ON = false, HOLIDAYS = [];
const getJSON = u => fetch(u).then(r => (r.ok ? r.json() : null)).catch(() => null);
const pick = (o, ...ks) => { if (!o || typeof o !== "object") return undefined; for (const k of ks) if (o[k] != null && o[k] !== "") return o[k]; return undefined; };
const num = v => (v == null || v === "" || !isFinite(+v) ? undefined : +v);
// counties may arrive as {fips: {...}}, [{fips, ...}] or {cols, rows}; normalise to a Map keyed by 5-digit FIPS
function countyMap(src) {
  const m = new Map(); if (!src) return m;
  const put = (f, o) => { if (f != null && o && typeof o === "object") m.set(String(f).padStart(5, "0"), o); };
  if (Array.isArray(src)) src.forEach(o => put(pick(o, "fips", "geoid", "f"), o));
  else if (Array.isArray(src.cols) && Array.isArray(src.rows)) src.rows.forEach(a => { const o = Object.fromEntries(src.cols.map((c, i) => [c, a[i]])); put(pick(o, "fips", "geoid", "f"), o); });
  else if (typeof src === "object") for (const [f, o] of Object.entries(src)) put(f, o);
  return m;
}
const points = arr => (Array.isArray(arr) ? arr : []).map(p => {
  if (!p || typeof p !== "object") return null;
  const lon = num(pick(p, "lon", "lng", "longitude", "x")), lat = num(pick(p, "lat", "latitude", "y"));
  return lon == null || lat == null ? null : { ...p, lon, lat };
}).filter(Boolean);
const compact = v => v == null ? "—" : Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(1) + "B" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : Math.abs(v) >= 1e4 ? Math.round(v / 1e3) + "k" : Math.round(v).toLocaleString();
const pct = v => v == null ? "—" : (v <= 1 ? v * 100 : v).toFixed(v <= 1 && v * 100 < 10 ? 2 : 1) + "%";
const sgnPct = v => v == null ? "—" : (v > 0 ? "+" : "") + (Math.abs(v) <= 1 ? v * 100 : v).toFixed(1) + "%";
let JOBS = null, ROAD = null, VIS = null, SITES = [], SITE_ON = false, VIS_STATE = new Map(), VIS_CITIES = [];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthName = m => (typeof m === "number" || /^\d+$/.test(m)) ? (MON[+m - 1] || String(m)) : String(m).slice(0, 3);
const listText = v => v == null ? undefined : Array.isArray(v) ? v.map(x => typeof x === "object" ? (pick(x, "name", "label", "h", "hour") ?? JSON.stringify(x)) : x).join(", ") : typeof v === "object" ? Object.entries(v).map(([a, b]) => `${a} ${b}`).join(", ") : String(v);
const hoursText = v => v == null ? undefined : Array.isArray(v) ? v.map(h => typeof h === "number" ? String(h).padStart(2, "0") + ":00" : (typeof h === "object" ? listText([h]) : h)).join(", ") : String(v);
const OPT_METRICS = [
  { src: "str", k: "str_demand", label: "STR demand (visitor flow)", unit: "", fmt: v => v.toFixed(1), note: "Short-term-rental demand index built from public visitor-flow signals (air passengers, lodging, attractions, events). Higher = more visitors. Aggregate public data; no booking or occupancy data." },
  { src: "str", k: "str_score", label: "STR investment score", unit: "", fmt: v => v.toFixed(1), note: "Demand balanced against entry cost (home prices) and known local rules. A screening index, not an underwriting model; no booking or occupancy data." },
  { src: "str", k: "str_cost", label: "Entry cost (cheaper = higher)", unit: "", fmt: v => v.toFixed(1), note: "Entry-cost index from public home values: a higher score means it is cheaper to buy in." },
  { src: "str", k: "str_housing", label: "Housing demand score (buy/rent)", unit: "", fmt: v => v.toFixed(1), note: "Housing demand score (buy and rent) from public population, jobs and housing data. Higher = stronger demand." },
  { src: "jobs", k: "emp_yoy", label: "Job growth (1 yr)", unit: "", fmt: v => sgnPct(v), note: "Year-over-year change in county employment (public BLS / Census series)." },
  { src: "jobs", k: "net_mig", label: "Net domestic in-migration", unit: "", fmt: v => (v > 0 ? "+" : "") + Math.round(v).toLocaleString(), note: "People moving in from other US counties minus people moving out (public Census / IRS migration data)." },
  { src: "jobs", k: "inflow_ratio", label: "Commuter inflow ratio", unit: "", fmt: v => v.toFixed(2), note: "Workers commuting in per worker living in the county (public Census LEHD flows). Above 1 = a job hub." },
  { src: "jobs", k: "hd_pressure", label: "Housing demand pressure", unit: "", fmt: v => v.toFixed(1), note: "Jobs and population growth relative to housing supply. Higher = more pressure on homes." },
  { src: "road", k: "vmt_daily", label: "Road traffic (daily vehicle-miles)", unit: "", fmt: v => compact(v), note: "Daily vehicle-miles travelled on county roads (public FHWA / state DOT traffic counts)." },
  { src: "vis", k: "fed_visits", label: "Visits to federal lands & parks", unit: "", fmt: v => compact(v), note: "Recreation visits to national parks, forests and other federal lands in the county (public NPS / USFS / BLM visitor counts)." },
  { src: "vis", k: "border_12m", label: "Border crossings (12 mo)", unit: "", fmt: v => compact(v), note: "Inbound crossings at land ports of entry in the county over the last 12 months (public BTS border-crossing data)." },
  { src: "vis", k: "cruise_pax", label: "Cruise passengers", unit: "", fmt: v => compact(v), note: "Cruise passengers through ports in the county (latest public port statistics)." },
  { src: "air", k: "air60", label: "Air visitors within 60 km (12 mo)", unit: "", fmt: v => compact(v), note: "Passengers at commercial airports within 60 km of the county over the last 12 months (public airport traffic statistics)." },
  { src: "econ", k: "lodging_share", label: "Tourism intensity (lodging jobs share)", unit: "", fmt: v => pct(v), note: "Accommodation jobs as a share of all county employment (public County Business Patterns / QCEW-style counts)." },
  { src: "wx", k: "dust_days_yr", label: "Dust storm days / yr", unit: "", fmt: v => v.toFixed(1), note: "Average days a year with a dust storm or blowing dust in the county (NOAA Storm Events / NWS, public domain). Dust coats pools, windows and solar panels." },
  { src: "wx", k: "dust_then_rain_yr", label: "Dust-then-rain days / yr", unit: "", fmt: v => v.toFixed(1), note: "Days a year when rain follows a dust day within ~48 h — the mud-spotting events that drive window and pool cleaning calls (public NOAA data)." },
  { src: "wx", k: "high_wind_days_yr", label: "High wind days / yr", unit: "", fmt: v => v.toFixed(1), note: "Average days a year with high or damaging wind reports in the county (NOAA SPC / Storm Events, public domain)." },
  { src: "clean", k: "svc_per_10k_households", label: "Pool service businesses per 10k households", unit: "", fmt: v => v.toFixed(1), note: "Pool-cleaning and related service establishments (employer + nonemployer) per 10,000 households — Census County Business Patterns / Nonemployer Statistics, public domain." },
  { src: "clean", k: "median_hh_income", label: "Median household income", unit: "", fmt: v => "$" + Math.round(v / 1000).toLocaleString() + "k", note: "Census ACS 5-year median household income (B19013), public domain." },
  { src: "clean", k: "share_hh_200k", label: "Share of households ≥ $200k", unit: "", fmt: v => pct(v), note: "Share of households with income of $200,000 or more, Census ACS 5-year (B19001), public domain." },
  { src: "econ", k: "wiki_views", label: "Travel interest (Wikipedia views)", unit: "", fmt: v => compact(v), note: "Wikipedia page views for the county and its main places — a public proxy for travel interest." },
];
let COUNTY = new Map(), FEAT = new Map(), ROWS = [], GEO = null, STATES = null, BLOCKS = [], BLOCK_BY_FIPS = new Map();
let metric = METRICS[0], breaks = [], selected = null, hovered = null, stateFilter = null, deckgl = null, pulse = 0;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function fmtVal(m, v) { return v == null ? "—" : m.fmt(v) + m.unit; }
function computeBreaks() {
  const vals = ROWS.map(r => r[metric.k]).filter(v => v != null).sort((a, b) => a - b);
  const n = NBANDS; breaks = [];
  for (let i = 1; i < n; i++) breaks.push(vals[Math.floor(vals.length * i / n)]);
  metric._min = vals[0]; metric._max = vals[vals.length - 1];
  // rank order for the metric (desc)
  const sorted = ROWS.filter(r => r[metric.k] != null).sort((a, b) => b[metric.k] - a[metric.k]);
  sorted.forEach((r, i) => r._mrank = i + 1);
  ROWS.filter(r => r[metric.k] == null).forEach(r => r._mrank = null);
  metric._ranked = sorted;
}
function bandOf(v) {
  if (v == null) return -1;
  let i = 0; while (i < breaks.length && v >= breaks[i]) i++;
  return i;
}
// ===================== copied from index.html (county card) =====================
// a value of {usd: "..."} is a dollar figure: rendered with class "redact" (blacked out in the intel theme, click to reveal)
const usd = v => ({ usd: v });
const val = b => b && typeof b === "object" && "usd" in b ? (b.usd === "—" ? "—" : `<span class="redact" title="click to reveal">${esc(b.usd)}</span>`) : esc(b);
function kv(pairs) { return "<dl class='kv'>" + pairs.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${val(b)}</dd>`).join("") + "</dl>"; }
const n0 = v => v == null ? "—" : Math.round(v).toLocaleString();
const n1 = v => v == null ? "—" : (+v).toFixed(1);

// ---------- card sections for the optional visitor / STR inputs (each returns "" when its input is missing)
function strHtml(r) {
  const s = r._str; if (!s) return "";
  const rows = [["STR investment score", r.str_score == null ? "—" : n1(r.str_score)], ["Demand (visitor flow)", r.str_demand == null ? "—" : n1(r.str_demand)], ["Entry cost (cheaper = higher)", r.str_cost == null ? "—" : n1(r.str_cost)]];
  const pm = pick(s, "peak_months", "peak");
  if (pm != null) rows.push(["Peak months", Array.isArray(pm) ? pm.map(monthName).join(", ") : String(pm)]);
  const parts = pick(s, "parts", "components");
  // parts: flat {input: value} or grouped {demand: {input: [raw, percentile, ...]}, cost: {...}}; only inputs with data are listed
  const partRow = (a, b, grp) => {
    const lab = `· ${grp ? grp + ": " : ""}${a.replace(/_/g, " ")}`;
    if (typeof b === "number") rows.push([lab, n1(b)]);
    else if (Array.isArray(b) && b[0] != null) rows.push([lab, `${compact(+b[0])}${b[1] != null ? ` · p${Math.round(b[1])}` : ""}`]);
  };
  if (parts && typeof parts === "object" && !Array.isArray(parts))
    Object.entries(parts).forEach(([a, b]) => { if (b && typeof b === "object" && !Array.isArray(b)) Object.entries(b).forEach(([k, x]) => partRow(k, x, a)); else partRow(a, b); });
  const cov = num(pick(s, "coverage"));
  if (cov != null) rows.push(["Demand inputs available", pct(cov)]);
  let legal = pick(s, "legal", "rules");
  let legalHtml = "";
  if (legal != null) {
    if (typeof legal === "string") legalHtml = `<div class="caveat"><b>Local rules:</b> ${esc(legal)}</div>`;
    else if (Array.isArray(legal)) legalHtml = `<div class="caveat"><b>Local rules:</b> ${legal.map(x => esc(typeof x === "object" ? [pick(x, "city", "place", "name"), pick(x, "note", "rule", "summary", "status")].filter(Boolean).join(": ") : x)).join(" · ")}</div>`;
    else if (typeof legal === "object") {
      const note = pick(legal, "note", "summary", "status", "text");
      const cities = pick(legal, "cities", "city_rules", "places");
      const cityTxt = Array.isArray(cities) ? cities.map(x => typeof x === "object" ? [pick(x, "city", "place", "name"), pick(x, "note", "rule", "summary", "status")].filter(Boolean).join(": ") : x).join(" · ")
        : cities && typeof cities === "object" ? Object.entries(cities).map(([a, b]) => `${a}: ${typeof b === "object" ? (pick(b, "note", "rule", "summary", "status") ?? "") : b}`).join(" · ") : "";
      const status = pick(legal, "status");
      legalHtml = `<div class="caveat"><b>Local rules${status ? ` (${esc(status)})` : ""}:</b> ${esc(note || (cityTxt ? "" : "no rule on file"))}${cityTxt ? `<br><b>City rules on file:</b> ${esc(cityTxt)}` : ""}</div>`;
    }
  }
  if (r.str_housing != null) rows.splice(3, 0, ["Housing demand score (buy/rent)", n1(r.str_housing)]);
  return `<h4>Short-term rental outlook</h4>${kv(rows)}${legalHtml}<div class="caveat">Aggregate public data only; no booking, occupancy or nightly-rate data. A screening signal, not an underwriting model.</div>`;
}
function visitorHtml(r) {
  const a = r._air, e = r._econ, v = r._evt, rows = [];
  if (a) {
    if (r.air60 != null) rows.push(["Air passengers ≤ 60 km · 12 mo", compact(r.air60)]);
    const near = pick(a, "nearest", "airports", "near");
    if (Array.isArray(near)) near.slice(0, 3).forEach(n => {
      if (typeof n !== "object") { rows.push(["Nearby airport", String(n)]); return; }
      const ap = AIR_BY_CODE.get(String(pick(n, "code", "iata", "id") || "")) || {};
      const nm = pick(n, "code", "iata", "name") ?? ap._code ?? "airport", km = num(pick(n, "km", "dist_km", "distance_km")), px = num(pick(n, "pax_12m", "pax")) ?? ap._pax;
      rows.push([`  ${nm}${km != null ? ` · ${Math.round(km)} km` : ""}`, px != null ? compact(px) + " pax" : "—"]);
    });
    const intl = num(pick(AIR_STATE.get(r.STUSPS) || {}, "intl_arrivals", "international_arrivals", "intl"));
    if (intl != null) rows.push([`International arrivals · ${r.STUSPS}`, compact(intl)]);
  }
  const nps = num(pick(a || {}, "nps_visits", "nps")) ?? num(pick(e || {}, "nps_visits", "nps")) ?? num(pick(v || {}, "nps_visits", "nps"));
  if (nps != null) rows.push(["National Park visits / yr", compact(nps)]);
  if (e) {
    if (r.lodging_share != null) rows.push(["Lodging jobs share", pct(r.lodging_share)]);
    const est = num(pick(e, "lodging_estab", "lodging_establishments", "estab")), emp = num(pick(e, "lodging_emp", "lodging_employment", "emp"));
    if (est != null || emp != null) rows.push(["Lodging establishments / jobs", `${n0(est)} / ${n0(emp)}`]);
    const hot = num(pick(e, "hot_receipts", "hot", "lodging_tax"));
    if (hot != null) rows.push(["Hotel-occupancy tax receipts", usd("$" + compact(hot))]);
    const reg = num(pick(e, "str_registered", "registered_str", "str_count"));
    if (reg != null) rows.push(["Registered short-term rentals", n0(reg)]);
    if (r.wiki_views != null) rows.push(["Wikipedia views · travel interest", compact(r.wiki_views)]);
  }
  if (v) {
    const vn = num(pick(v, "venue_n", "venues", "n_venues")), vc = num(pick(v, "venue_cap", "capacity", "venue_capacity"));
    if (vn != null || vc != null) rows.push(["Venues · total capacity", `${n0(vn)} · ${compact(vc)}`]);
    const am = pick(v, "amtrak", "nearest_amtrak");
    if (am != null) rows.push(["Nearest Amtrak", typeof am === "object" ? `${pick(am, "name", "code", "station") ?? "station"}${num(pick(am, "km", "dist_km")) != null ? ` · ${Math.round(num(pick(am, "km", "dist_km")))} km` : ""}` : String(am)]);
    const wk = pick(v, "walkability", "walk", "walk_index");
    if (wk != null) rows.push(["Walkability", typeof wk === "number" ? n1(wk) : String(wk)]);
  }
  return rows.length ? `<h4>Tourism &amp; travel access</h4>${kv(rows)}` : "";
}
function jobsHtml(r) {
  const j = r._jobs, rows = [];
  if (j) {
    const e5 = num(pick(j, "emp_5y")), wy = num(pick(j, "wage_yoy")), un = num(pick(j, "unemp", "unemployment"));
    rows.push(["Job growth · 1 yr", sgnPct(r.emp_yoy)], ["Job growth · 5 yr", sgnPct(e5)], ["Wage growth · 1 yr", sgnPct(wy)],
      ["Unemployment rate", un == null ? "—" : (un <= 1 ? un * 100 : un).toFixed(1) + "%"],
      ["Net domestic in-migration", r.net_mig == null ? "—" : (r.net_mig > 0 ? "+" : "") + n0(r.net_mig)],
      ["Commuter inflow ratio", r.inflow_ratio == null ? "—" : r.inflow_ratio.toFixed(2)],
      ["Housing demand pressure", r.hd_pressure == null ? "—" : n1(r.hd_pressure)]);
    const nameOf = x => { if (typeof x !== "object" || !x) { const c = COUNTY.get(String(x).padStart(5, "0")); return c ? `${c.NAME}, ${c.STUSPS}` : String(x); }
      const f = pick(x, "fips", "geoid"), c = f != null && COUNTY.get(String(f).padStart(5, "0"));
      const nm = pick(x, "name", "county", "sector", "label") ?? (c ? `${c.NAME}, ${c.STUSPS}` : f ?? "");
      const n = num(pick(x, "n", "movers", "people", "count", "growth", "yoy", "change"));
      return n != null ? `${nm} (${Math.abs(n) <= 1 && !Number.isInteger(n) ? sgnPct(n) : (n > 0 && pick(x, "growth", "yoy", "change") != null ? "+" : "") + compact(n)})` : String(nm); };
    const to = pick(j, "top_origins"), tg = pick(j, "top_growth_sectors");
    const lst = v => Array.isArray(v) ? v.slice(0, 3).map(nameOf).join(" · ") : typeof v === "object" && v ? Object.entries(v).slice(0, 3).map(([a, b]) => `${a} (${typeof b === "number" ? compact(b) : b})`).join(" · ") : v ? String(v) : "";
    const toT = lst(to), tgT = lst(tg);
    const extra = (toT ? `<div class="caveat"><b>Top origin counties:</b> ${esc(toT)}</div>` : "") + (tgT ? `<div class="caveat"><b>Fastest-growing sectors:</b> ${esc(tgT)}</div>` : "");
    return `<h4>Jobs &amp; people flow</h4>${kv(rows)}${extra}${roadLine(r)}`;
  }
  const rl = roadLine(r); return rl ? `<h4>Road traffic</h4>${rl}` : "";
}
function roadLine(r) {
  const o = r._road; if (!o) return "";
  const aadt = num(pick(o, "max_aadt", "aadt"));
  return kv([["Daily vehicle-miles (VMT)", compact(r.vmt_daily)], ["Busiest road · AADT", aadt == null ? "—" : n0(aadt) + " vehicles/day"]]);
}
function visitorsHtml(r) {
  const v = r._vis, rows = [];
  if (v) {
    rows.push(["Federal lands & parks visits", compact(r.fed_visits)], ["Border crossings · 12 mo", compact(r.border_12m)], ["Cruise passengers", compact(r.cruise_pax)]);
    const am = num(pick(v, "amtrak_riders")), sp = num(pick(v, "state_park_visits"));
    if (am != null) rows.push(["Amtrak riders", compact(am)]);
    if (sp != null) rows.push(["State park visits", compact(sp)]);
  }
  const s = VIS_STATE.get(r.STUSPS);
  if (s && num(s.overseas_visits) != null) rows.push([`Overseas visits · ${r.STUSPS}`, `${compact(+s.overseas_visits)}${s._rank ? ` · #${s._rank} of ${s._of}` : ""}`]);
  if (s && num(s.tourism_output) != null) rows.push([`Tourism output · ${r.STUSPS}`, usd("$" + compact(+s.tourism_output))]);
  if (!rows.length) return "";
  const cities = VIS_CITIES.filter(c => c.state === r.STUSPS && num(c.overseas_visits) != null).sort((a, b) => b.overseas_visits - a.overseas_visits).slice(0, 3);
  const cityT = cities.length ? `<div class="caveat"><b>Top overseas-visitor cities in ${esc(r.STUSPS)}:</b> ${cities.map(c => esc(`${c.name} ${compact(+c.overseas_visits)}${c.year ? ` (${c.year})` : ""}`)).join(" · ")}</div>` : "";
  return `<h4>Visitors</h4>${kv(rows)}${cityT}`;
}
function holidayHtml() {
  if (!HOLIDAYS.length) return "";
  const today = new Date().toISOString().slice(0, 10);
  const up = HOLIDAYS.filter(h => h._end >= today).slice(0, 6);
  if (!up.length) return "";
  return `<h4>Upcoming holidays &amp; long weekends</h4><ul class="hol">${up.map(h => `<li><span>${esc(h._name)}${h._long ? " · long weekend" : ""}</span><span>${esc(h._date)}${h._end !== h._date ? " → " + esc(h._end.slice(5)) : ""}</span></li>`).join("")}</ul>`;
}
let AIR_BY_CODE = new Map(), AIR_STATE = new Map(), WX = null, CLEAN = null;
// 12 numbers or a {Jan:..}/{1:..} map -> 12 values (null when unusable)
function months12(p) {
  if (Array.isArray(p) && p.length === 12 && p.every(x => num(x) != null)) return p.map(Number);
  if (p && typeof p === "object" && !Array.isArray(p)) { const v = MON.map((n, i) => num(pick(p, n, n.toLowerCase(), String(i + 1), String(i + 1).padStart(2, "0")))); return v.some(x => x != null) ? v.map(x => x ?? 0) : null; }
  return null;
}
// 12-bar mini chart; colours are the theme's CSS variables (top 3 months amber, the rest cyan)
function monthBars(v, aria) {
  const mx = Math.max(...v, 1e-9), t3 = v.slice().sort((a, b) => b - a)[2] ?? 0, W = 22, Hh = 34;
  return `<svg viewBox="0 0 ${12 * W} ${Hh + 12}" width="100%" height="${Hh + 12}" role="img" aria-label="${esc(aria)}" style="display:block;margin:4px 0 2px">${v.map((x, i) => { const bh = Math.max(1.5, x / mx * Hh), pk = x >= t3 && x > 0;
    return `<rect x="${i * W + 3}" y="${Hh - bh}" width="${W - 6}" height="${bh}" rx="1" style="fill:var(${pk ? "--amber" : "--cyan"});opacity:${pk ? 1 : .45}"><title>${MON[i]}: ${Math.round(x / mx * 100)}% of the busiest month${Number.isInteger(x) || x > 1 ? ` (${(+x).toFixed(1)})` : ""}</title></rect><text x="${i * W + W / 2}" y="${Hh + 10}" text-anchor="middle" style="fill:var(--dim);font:9px var(--f-mono)">${MON[i][0]}</text>`; }).join("")}</svg>`;
}
function wxHtml(r) {
  const o = r._wx; if (!o) return "";
  const rows = [["Dust storm days / yr", n1(r.dust_days_yr)], ["Dust-then-rain days / yr", n1(r.dust_then_rain_yr)], ["High wind days / yr", n1(r.high_wind_days_yr)], ["Hail days / yr", n1(r.hail_days_yr)]];
  const mv = months12(pick(o, "monthly_trigger_profile", "monthly"));
  return `<h4>Weather cleaning triggers</h4>${kv(rows)}${mv ? `<div class="caveat">Trigger days by month (dust, wind, rain-after-dust, hail)</div>${monthBars(mv, "Cleaning trigger days by month")}` : ""}<div class="caveat">Public NOAA / NWS records. Each trigger dirties pools, windows and panels.</div>`;
}
function cleanHtml(r) {
  const o = r._clean; if (!o) return "";
  const rows = [["Pool service businesses", n0(num(pick(o, "pool_svc_estabs")))], ["  employees", n0(num(pick(o, "pool_svc_emp")))], ["  self-employed operators", n0(num(pick(o, "pool_svc_nonemployers")))],
    ["Businesses per 10k households", n1(r.svc_per_10k_households)]];
  const wj = num(pick(o, "window_janitorial_estabs")), nj = num(pick(o, "nonemployer_janitorial")), ls = num(pick(o, "landscaping_estabs"));
  if (wj != null || nj != null) rows.push(["Window & janitorial firms / self-employed", `${n0(wj)} / ${n0(nj)}`]);
  if (ls != null) rows.push(["Landscaping firms", n0(ls)]);
  const sq = pick(o, "pool_svc_seasonality_q");
  if (Array.isArray(sq) && sq.length === 4 && sq.every(x => num(x) != null)) rows.push(["Pool-service payroll by quarter", sq.map((x, i) => `Q${i + 1} ${(+x).toFixed(2)}`).join(" ")]);
  rows.push(["Households", n0(num(pick(o, "households")))], ["Median household income", usd(r.median_hh_income == null ? "—" : "$" + n0(r.median_hh_income))], ["Households ≥ $150k / ≥ $200k", `${pct(num(pick(o, "share_hh_150k")))} / ${pct(r.share_hh_200k)}`]);
  return `<h4>Cleaning services market</h4>${kv(rows)}<div class="caveat">Census County Business Patterns, Nonemployer Statistics and ACS (public domain). Fewer businesses per household in an affluent county suggests room for a new service.</div>`;
}
function renderCard() {
  const card = $("card"); const r = selected && COUNTY.get(selected);
  if (!r) { card.hidden = true; return; }
  const blocks = BLOCKS.filter(b => b.fips === r.fips);
  let layerHtml = blocks.map(b => `<button class="badge" data-block="${esc(b.slug)}">◆ FLY TO BLOCK · ${esc(b.name)}</button> `).join("");
  if (!layerHtml) layerHtml = r.layers ? `<span class="badge">◆ ${esc(r.layers)}</span>` : `<span class="badge off">County layers only · no city block yet</span>`;
  const m = metric, mv = r[m.k];
  card.innerHTML = `<button class="x" aria-label="Close" id="cardX">×</button>
    ${skin() === "intel" ? `<div class="fileno">FILE NO. ${esc(r.fips.slice(0, 2))}-${esc(r.fips.slice(2))}</div>` : ""}
    <div class="lbl">County ${esc(r.fips)} · ${esc(r.STATE_NAME || r.STUSPS)}</div>
    <h3>${esc(r.NAME)}, ${esc(r.STUSPS)}</h3>
    <div class="rk">Ranked #${r.rank} of ${N_TOTAL.toLocaleString()} <span>on overlap score (${n1(r.overlap)})</span></div>
    ${m.k !== "overlap" ? `<div class="rk" style="color:var(--cyan)">#${r._mrank ?? "—"} <span>on ${esc(m.label)} (${m.k === "home_value" || m.k === "median_hh_income" ? val(usd(fmtVal(m, mv))) : esc(fmtVal(m, mv))})</span></div>` : ""}
    ${layerHtml}
    ${tractHtml(r)}
    <h4>LiDAR · USGS 3DEP</h4>${kv([["Newest flight", r.lidar_year ?? "none ≥30% cover"], ["Density", r.lidar_density ? n1(r.lidar_density) + " pts/m²" : "—"], ["Flights since 2010", r.lidar_flights_since_2010 ?? 0], ["All years", (r.lidar_years || []).join(", ") || "—"]])}
    <h4>Storms · NOAA SPC · last 24 mo</h4>${kv([["Hail reports", n0(r.hail24)], ["  ≥ 1 in / ≥ 2 in", `${r.hail24_1in} / ${r.hail24_2in}`], ["Max hail size", r.hail24_max ? r.hail24_max.toFixed(2) + " in" : "—"], ["Last hail report", r.hail24_last || "—"], ["Wind reports", n0(r.wind24)], ["Tornado reports", n0(r.torn24)]])}
    <h4>Hail history · SPC 2015–24</h4>${kv([["≥ 1 in reports / yr", n1(r.hail_1in_per_yr)], ["≥ 2 in reports / yr", n1(r.hail_2in_per_yr)]])}
    <h4>FEMA National Risk Index</h4>${kv([["Overall risk", r.nri_risk == null ? (r.nri_rating || "—") : `${n1(r.nri_risk)} · ${r.nri_rating}`], ["Expected annual loss", usd(r.nri_eal == null ? "—" : "$" + (r.nri_eal / 1e6).toFixed(1) + "M")], ["Hail", n1(r.nri_hail)], ["Strong wind", n1(r.nri_wind)], ["Tornado", n1(r.nri_torn)], ["Inland flood", n1(r.nri_rflood)], ["Coastal flood", n1(r.nri_cflood)], ["Wildfire", n1(r.nri_fire)], ["Hurricane", n1(r.nri_hurr)], ["Winter weather", n1(r.nri_winter)], ["Social vulnerability", n1(r.nri_sovi)], ["Community resilience", n1(r.nri_resl)]])}
    ${OZ_C ? `<h4>Opportunity Zones · Treasury</h4>${kv([["2018 round (to 2028)", `${r.oz18_n || 0} tracts · ${((r.oz18_share || 0) * 100).toFixed(1)}%`], ["2027 round (2027–2036)", `${r.oz27_n || 0} tracts · ${((r.oz27_share || 0) * 100).toFixed(1)}%${r.oz27_rural ? ` · ${r.oz27_rural} rural` : ""}`]])}` : ""}
    <h4>Housing · Census ACS</h4>${kv([["Median home value", usd(r.home_value ? "$" + n0(r.home_value) : "—")], ["Median year built", r.year_built ?? "—"], ["Single-family detached", n0(r.sf_detached)], ["Owner-occupied homes", n0(r.owner_occ)]])}
    ${wxHtml(r)}${cleanHtml(r)}${jobsHtml(r)}${strHtml(r)}${visitorsHtml(r)}${visitorHtml(r)}${holidayHtml()}`;
  card.hidden = false; card.scrollTop = 0;
  $("cardX").onclick = () => select(null, false);
}
// ===================== copied from index.html (optional-input merge) =====================
// merge the optional inputs into the county rows; add a metric only when it has values for some counties
function mergeOptional() {
  const byF = new Map(ROWS.map(r => [r.fips, r]));
  const str = countyMap(STR && (STR.counties ?? STR)), air = countyMap(AIR && AIR.counties), econ = countyMap(ECON && (ECON.counties ?? ECON)), evt = countyMap(EVT && EVT.counties);
  for (const [f, o] of str) { const r = byF.get(f); if (!r) continue; r._str = o;
    r.str_demand = num(pick(o, "demand")); r.str_score = num(pick(o, "score")); r.str_cost = num(pick(o, "cost")); }
  for (const [f, o] of air) { const r = byF.get(f); if (!r) continue; r._air = o;
    r.air60 = num(pick(o, "pax_60km", "air_pax_60km", "air60", "pax60", "pax_12m_60km", "pax")); }
  for (const [f, o] of econ) { const r = byF.get(f); if (!r) continue; r._econ = o;
    r.lodging_share = num(pick(o, "lodging_share", "lodging_jobs_share", "tourism_intensity", "intensity"));
    r.wiki_views = num(pick(o, "wiki_views", "wikipedia_views", "wiki")); }
  for (const [f, o] of evt) { const r = byF.get(f); if (r) r._evt = o; }
  for (const r of ROWS) if (r._str) r.str_housing = num(pick(r._str, "housing", "housing_score"));
  const jobs = countyMap(JOBS && (JOBS.counties ?? JOBS)), road = countyMap(ROAD && (ROAD.counties ?? ROAD));
  for (const [f, o] of jobs) { const r = byF.get(f); if (!r) continue; r._jobs = o;
    r.emp_yoy = num(pick(o, "emp_yoy")); r.net_mig = num(pick(o, "net_domestic_mig", "net_mig"));
    r.inflow_ratio = num(pick(o, "inflow_ratio")); r.hd_pressure = num(pick(o, "housing_demand_pressure", "hd_pressure")); }
  for (const [f, o] of road) { const r = byF.get(f); if (!r) continue; r._road = o;
    r.vmt_daily = num(pick(o, "vmt_daily", "vmt")); }
  const vis = countyMap(VIS && VIS.counties);
  for (const [f, o] of vis) { const r = byF.get(f); if (!r) continue; r._vis = o;
    r.fed_visits = num(pick(o, "federal_land_visits")); r.border_12m = num(pick(o, "border_crossings_12m")); r.cruise_pax = num(pick(o, "cruise_pax")); }
  // states with overseas visits, ranked
  const vs = Object.entries((VIS && VIS.states) || {}).filter(([, v]) => v && typeof v === "object");
  vs.filter(([, v]) => num(v.overseas_visits) != null).sort((a, b) => b[1].overseas_visits - a[1].overseas_visits).forEach(([, v], i, arr) => { v._rank = i + 1; v._of = arr.length; });
  VIS_STATE = new Map(vs);
  VIS_CITIES = Array.isArray(VIS && VIS.cities) ? VIS.cities.filter(c => c && c.name) : [];
  SITES = points(VIS && VIS.sites).map(p => ({ ...p, _vis: num(pick(p, "visits")) || 0, _kind: pick(p, "kind", "type"), _name: pick(p, "name") ?? "Visitor site", _year: pick(p, "year"),
    _fips: pick(p, "fips") != null ? String(p.fips).padStart(5, "0").slice(0, 5) : null })).sort((a, b) => a._vis - b._vis);
  // cleaning triggers (weather_triggers.json) and cleaning-services market (cleaning_market.json)
  const wx = countyMap(WX && (WX.counties ?? WX)), clean = countyMap(CLEAN && (CLEAN.counties ?? CLEAN));
  for (const [f, o] of wx) { const r = byF.get(f); if (!r) continue; r._wx = o;
    r.dust_days_yr = num(pick(o, "dust_days_yr")); r.dust_then_rain_yr = num(pick(o, "dust_then_rain_yr")); r.high_wind_days_yr = num(pick(o, "high_wind_days_yr")); r.hail_days_yr = num(pick(o, "hail_days_yr")); }
  for (const [f, o] of clean) { const r = byF.get(f); if (!r) continue; r._clean = o;
    r.svc_per_10k_households = num(pick(o, "svc_per_10k_households")); r.median_hh_income = num(pick(o, "median_hh_income")); r.share_hh_200k = num(pick(o, "share_hh_200k")); }
  const src = { str: str.size, air: air.size, econ: econ.size, jobs: jobs.size, road: road.size, vis: vis.size, wx: wx.size, clean: clean.size };
  OPT_METRICS.forEach(m => { if (src[m.src] && ROWS.some(r => r[m.k] != null) && !METRICS.includes(m)) METRICS.push(m); });

  // points: airports and venues (county FIPS optional, used for click-to-select)
  const fipsOf = p => { const f = pick(p, "fips", "county_fips", "geoid"); return f != null ? String(f).padStart(5, "0").slice(0, 5) : null; };
  AIRPORTS = points(AIR && (AIR.airports ?? AIR.points)).map(p => ({ ...p, _pax: num(pick(p, "pax_12m", "pax", "passengers")) || 0, _code: pick(p, "code", "iata", "faa", "id"),
    _name: pick(p, "name", "airport") ?? pick(p, "code", "iata") ?? "Airport", _where: [pick(p, "city"), pick(p, "state")].filter(Boolean).join(", "), _fips: fipsOf(p) }))
    .sort((a, b) => a._pax - b._pax);   // big ones drawn last (on top)
  AIR_BY_CODE = new Map(AIRPORTS.filter(a => a._code != null).map(a => [String(a._code), a]));
  AIR_STATE = new Map(Object.entries((AIR && (AIR.states ?? AIR.state)) || {}).filter(([, v]) => v && typeof v === "object"));
  VENUES = points(EVT && (EVT.venues ?? EVT.points)).map(p => ({ ...p, _cap: num(pick(p, "capacity", "cap", "seats")) || 0, _kind: pick(p, "kind", "type", "category"),
    _name: pick(p, "name") ?? "Venue", _where: [pick(p, "city"), pick(p, "state")].filter(Boolean).join(", "), _fips: fipsOf(p) }))
    .sort((a, b) => a._cap - b._cap);
  HOLIDAYS = (Array.isArray(EVT && EVT.holidays) ? EVT.holidays : []).map(h => {
    if (!h || typeof h !== "object") return null;
    const d = pick(h, "date", "start"); if (!d || !/^\d{4}-\d{2}-\d{2}/.test(String(d))) return null;
    const end = String(pick(h, "end", "end_date") ?? d).slice(0, 10);
    return { _date: String(d).slice(0, 10), _end: end, _name: pick(h, "name", "label", "holiday") ?? "Holiday", _long: !!pick(h, "long_weekend", "long", "longWeekend") };
  }).filter(Boolean).sort((a, b) => a._date < b._date ? -1 : 1);
}
// ===================== copied from index.html (search) =====================
// ---------- search
let STATE_NAMES = new Map(); let resIdx = -1;
function search(q) {
  q = q.trim().toLowerCase(); const out = [];
  if (!q) return out;
  for (const [ab, nm] of STATE_NAMES) if (ab.toLowerCase() === q || nm.toLowerCase().startsWith(q)) out.push({ state: ab, label: nm, sub: `state · ${ROWS.filter(r => r.STUSPS === ab).length} counties` });
  const m = q.match(/^(.*?)[,\s]+([a-z]{2})$/); const nq = m ? m[1] : q, sq = m ? m[2].toUpperCase() : null;
  const hits = ROWS.filter(r => { const n = r.NAME.toLowerCase(); return (n.startsWith(nq) || (nq.length > 2 && n.includes(nq))) && (!sq || r.STUSPS === sq); })
    .sort((a, b) => (b.NAME.toLowerCase().startsWith(nq)) - (a.NAME.toLowerCase().startsWith(nq)) || a.rank - b.rank).slice(0, 14);
  hits.forEach(r => out.push({ fips: r.fips, label: `${r.NAME}, ${r.STUSPS}`, sub: `#${r.rank}` }));
  return out.slice(0, 16);
}
function renderList() {}   // the old Top-25 list has no place in the app; copied card code calls it
// ---------- tract metrics (tracts/<ST>.json, loaded lazily per state)
const TR_METRICS = [
  { k: "income", label: "Median household income", fmt: v => "$" + Math.round(v / 1000).toLocaleString() + "k", note: "Census ACS 2020–24 5-year B19013, by census tract." },
  { k: "home_value", label: "Median home value", fmt: v => "$" + Math.round(v / 1000).toLocaleString() + "k", note: "Census ACS B25077 owner-occupied median value, by tract." },
  { k: "year_built", label: "Median year built", fmt: v => String(v), note: "Census ACS B25035 (1939 = 1939 or earlier), by tract." },
  { k: "owner_pct", label: "Owner-occupied share", fmt: v => (+v).toFixed(1) + "%", note: "Census ACS B25003 owner-occupied / occupied units, by tract." },
  { k: "sf_pct", label: "Single-family detached share", fmt: v => Math.round(v) + "%", note: "Census ACS B25024 1-unit detached / all units, by tract." },
  { k: "pop", label: "Population", fmt: v => Math.round(v).toLocaleString(), note: "Census ACS B01003 total population, by tract." },
  { k: "nri", label: "FEMA NRI risk", fmt: v => (+v).toFixed(1), note: "FEMA National Risk Index composite risk score by tract (0–100)." },
  { k: "nri_hail", label: "FEMA NRI hail risk", fmt: v => (+v).toFixed(1), note: "FEMA NRI hail risk score by tract (0–100)." },
  { k: "nri_wind", label: "FEMA NRI strong-wind risk", fmt: v => (+v).toFixed(1), note: "FEMA NRI strong-wind risk score by tract (0–100)." },
  { k: "nri_flood", label: "FEMA NRI flood risk", fmt: v => (+v).toFixed(1), note: "Max of FEMA NRI riverine and coastal flood risk scores by tract (0–100)." },
  { k: "hpi_1y", label: "House prices · 1 yr", fmt: v => (v > 0 ? "+" : "") + (+v).toFixed(1) + "%", note: "FHFA tract House Price Index, latest annual change." },
  { k: "hpi_5y", label: "House prices · 5 yr", fmt: v => (v > 0 ? "+" : "") + (+v).toFixed(1) + "%", note: "FHFA tract House Price Index, change over 5 years." },
  { k: "oz18", label: "Opportunity Zone · 2018 round", flag: ["no", "yes"], note: "≥ half the tract's land in a 2018-round Qualified Opportunity Zone (in force to 2028-12-31)." },
  { k: "oz27", label: "Opportunity Zone · 2027 round", flag: ["no", "yes", "yes · rural"], note: "2027-round Qualified Opportunity Zone (2027-01-01 to 2036-12-31); rural zones marked." },
  { k: "qct", label: "HUD Qualified Census Tract", flag: ["no", "yes"], note: "HUD 2026 Qualified Census Tract." },
  { k: "nmtc", label: "NMTC low-income community", flag: ["no", "yes"], note: "New Markets Tax Credit low-income community (ACS 2016–20)." },
];
const trFmt = (m, v) => v == null ? "—" : m.flag ? (m.flag[v] ?? String(v)) : m.fmt(v);
function tractHtml(r) {
  if (!pickedTract || pickedTract < 0) return "";
  const g = String(pickedTract).padStart(11, "0"), m = TRACT.get(pickedTract);
  if (!m) return `<h4>Census tract ${esc(g)}</h4><div class="caveat">${TR_LOADED.has(r.STUSPS) ? "No tract figures on file." : "Loading tract figures…"}</div>`;
  return `<h4>Census tract ${esc(g)}</h4>${kv(TR_METRICS.filter(x => m[x.k] != null).map(x => [x.label, ["income", "home_value"].includes(x.k) ? usd(trFmt(x, m[x.k])) : trFmt(x, m[x.k])]))}<div class="caveat">Tract figures: Census ACS 2020–24, FEMA NRI, FHFA HPI, Treasury CDFI Fund, HUD — all public domain.</div>`;
}

// earthlayers.js : live Earth-observation overlays streamed from free public tile / image services.
// Classic script; defines window.EarthLayers. Nothing hosted, no keys, $0.
// Every source is US-federal public domain or NASA open data (no restrictions, commercial use OK).
(function () {
  "use strict";
  const R = 6378137, HALF = Math.PI * R;               // EPSG:3857 half-extent (m)
  const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/";
  const GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi";
  const GLEG = "https://gibs.earthdata.nasa.gov/legends/";
  const MRLC = "https://www.mrlc.gov/geoserver/mrlc_display/wms";
  const CONUS = [-125.5, 24, -66.5, 49.6], USA = [-179.5, 17.5, -64.5, 71.6];
  const LIC = {
    nasa: { c: "NASA GIBS / EOSDIS (CC0)", u: "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance" },
    fema: { c: "FEMA NFHL (US public domain)", u: "https://www.fema.gov/flood-maps/national-flood-hazard-layer" },
    usgs: { c: "USGS (US public domain)", u: "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits" },
    fws: { c: "USFWS NWI (US public domain)", u: "https://www.fws.gov/program/national-wetlands-inventory" },
    mrlc: { c: "MRLC NLCD / USFS (US public domain)", u: "https://www.mrlc.gov/data" },
    lf: { c: "LANDFIRE / USGS-USFS (US public domain)", u: "https://landfire.gov/" },
    s5p: { c: "Contains modified Copernicus Sentinel-5P data (ESA), via NASA GIBS", u: "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice" }
  };

  // kind: wmts (GIBS tiles), wms (GetMap), ags (ArcGIS MapServer export), agi (ArcGIS ImageServer exportImage)
  // z: display range [min,max]; tz: native tile max zoom (overzoomed above); cov: lng/lat extent (null = global)
  // time: "day" (YYYY-MM-DD, lag = default days back) | "hour" (TEMPO, ISO hour) | fixed string | null
  const DEFS = [
    { id: "fires", g: "Hazards", name: "Active fires (VIIRS, daily)", kind: "wms", url: GIBS_WMS, wms: "1.3.0",
      layer: "VIIRS_NOAA20_Thermal_Anomalies_375m_All,VIIRS_NOAA21_Thermal_Anomalies_375m_All", time: "day", lag: 1, px: 512,
      z: [0, 22], tz: 12, lic: "nasa", op: 0.95, top: true, legend: { ramp: ["#ffef5a", "#ff8c1a", "#e8261c"], lo: "thermal anomaly", hi: "375 m pixel" },
      d: "VIIRS NOAA-20 + NOAA-21 375 m thermal anomalies (FIRMS) for the chosen UTC day." },
    { id: "flood", g: "Hazards", name: "FEMA flood zones (US)", kind: "ags", url: "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer",
      layers: "show:28", minScale: 36000, z: [11, 22], tz: 18, cov: USA, lic: "fema", op: 0.7, agsLegend: 28, top: true,
      d: "National Flood Hazard Layer: effective 1%/0.2% annual-chance flood zones (A, AE, VE, X)." },
    { id: "faults", g: "Hazards", name: "Quaternary faults (US)", kind: "ags", url: "https://earthquake.usgs.gov/arcgis/rest/services/haz/Qfaults/MapServer",
      layers: "show:21,22", z: [4, 22], tz: 16, cov: USA, lic: "usgs", op: 0.9, agsLegend: 21, top: true,
      d: "USGS Quaternary Fault and Fold Database: faults active in the last 1.6 million years." },
    { id: "fuels", g: "Hazards", name: "Wildfire fuels (LANDFIRE)", kind: "agi", url: "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2024/LF2024_FBFM40_CONUS/ImageServer",
      z: [7, 22], tz: 15, cov: CONUS, lic: "lf", op: 0.7, agsLegend: 0,
      d: "LANDFIRE 2024 Scott & Burgan 40 fire-behaviour fuel models, 30 m, CONUS." },
    { id: "dist", g: "Hazards", name: "Land disturbance alerts (OPERA)", kind: "wmts", layer: "OPERA_L3_DIST-ALERT-HLS_Color_Index", tms: "GoogleMapsCompatible_Level12",
      time: "day", lag: 2, z: [4, 22], tz: 12, lic: "nasa", op: 0.85, legend: { img: GLEG + "OPERA_Vegetation_Disturbance_Status_H.svg" },
      d: "OPERA DIST-ALERT (HLS, 30 m): vegetation loss / land disturbance incl. clearing for construction." },

    { id: "ndvi", g: "Vegetation & land", name: "Vegetation index NDVI", kind: "wmts", layer: "MODIS_Terra_NDVI_8Day", tms: "GoogleMapsCompatible_Level9",
      time: "day", lag: 2, z: [0, 22], tz: 9, lic: "nasa", op: 0.75, legend: { img: GLEG + "MODIS_NDVI_H.svg" },
      d: "MODIS Terra 8-day NDVI, 250 m: greenness of vegetation." },
    { id: "canopy", g: "Vegetation & land", name: "Tree canopy cover (NLCD)", kind: "wms", url: MRLC, wms: "1.3.0", layer: "nlcd_tcc_conus_2021_v2021-4",
      z: [6, 22], tz: 16, cov: CONUS, lic: "mrlc", op: 0.75, legend: { wmsLegend: true },
      d: "USFS / NLCD 2021 tree canopy cover, percent per 30 m cell, CONUS." },
    { id: "landcover", g: "Vegetation & land", name: "Land cover (NLCD 2021)", kind: "wms", url: MRLC, wms: "1.3.0", layer: "NLCD_2021_Land_Cover_L48",
      z: [6, 22], tz: 16, cov: CONUS, lic: "mrlc", op: 0.7, legend: { wmsLegend: true },
      d: "NLCD 2021 land cover classes (developed, forest, crops, wetland ...), 30 m, CONUS." },
    { id: "geology", g: "Vegetation & land", name: "Surface geology (SGMC)", kind: "wms", url: "https://mrdata.usgs.gov/services/sgmc2", wms: "1.3.0", layer: "sgmc2",
      z: [5, 22], tz: 15, cov: CONUS, lic: "usgs", op: 0.6,
      d: "USGS State Geologic Map Compilation: bedrock / surficial map units, CONUS." },

    { id: "no2", g: "Air & heat", name: "NO₂ pollution (TEMPO, hourly)", kind: "wmts", layer: "TEMPO_L3_NO2_Vertical_Column_Troposphere", tms: "GoogleMapsCompatible_Level7",
      time: "hour", z: [0, 22], tz: 7, lic: "nasa", op: 0.75, legend: { img: GLEG + "TEMPO_NO2_Vertical_Column_Troposphere_H.svg" },
      d: "TEMPO tropospheric NO₂ column, hourly daytime scans over North America (snaps to the latest scan at or before the hour)." },
    { id: "lstday", g: "Air & heat", name: "Surface temperature, day", kind: "wmts", layer: "MODIS_Terra_Land_Surface_Temp_Day", tms: "GoogleMapsCompatible_Level7",
      time: "day", lag: 2, z: [0, 22], tz: 7, lic: "nasa", op: 0.75, legend: { img: GLEG + "MODIS_Land_Surface_Temp_H.svg" },
      d: "MODIS Terra land surface temperature, ~10:30 local daytime pass, 1 km (urban heat islands)." },
    { id: "lstnight", g: "Air & heat", name: "Surface temperature, night", kind: "wmts", layer: "MODIS_Terra_Land_Surface_Temp_Night", tms: "GoogleMapsCompatible_Level7",
      time: "day", lag: 2, z: [0, 22], tz: 7, lic: "nasa", op: 0.75, legend: { img: GLEG + "MODIS_Land_Surface_Temp_H.svg" },
      d: "MODIS Terra land surface temperature, ~22:30 local night pass, 1 km." },
    { id: "lights", g: "Air & heat", name: "Night lights (Black Marble)", kind: "wmts", layer: "VIIRS_Black_Marble", tms: "GoogleMapsCompatible_Level8",
      time: "2016-01-01", z: [0, 22], tz: 8, lic: "nasa", op: 0.85, blend: true,
      d: "VIIRS Black Marble 2016 cloud-free night-lights composite, 500 m." },

    { id: "water", g: "Water", name: "Surface water now (OPERA DSWx)", kind: "wmts", layer: "OPERA_L3_Dynamic_Surface_Water_Extent-Sentinel-1", tms: "GoogleMapsCompatible_Level12",
      time: "day", lag: 2, z: [4, 22], tz: 12, lic: "nasa", op: 0.85, legend: { img: GLEG + "OPERA_Dynamic_Surface_Water_Extent_S1_H.svg" },
      d: "OPERA DSWx from Sentinel-1 radar (sees through cloud), 30 m: open water and flooding for the chosen day." },
    { id: "wetlands", g: "Water", name: "Wetlands (NWI)", kind: "ags", url: "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer",
      minScale: 240000, z: [10, 22], tz: 17, cov: USA, lic: "fws", op: 0.7, agsLegend: 0,
      d: "USFWS National Wetlands Inventory: mapped wetland and deep-water habitat polygons." },
    { id: "soil", g: "Water", name: "Soil moisture (SMAP)", kind: "wmts", layer: "SMAP_L4_Analyzed_Surface_Soil_Moisture", tms: "GoogleMapsCompatible_Level6",
      time: "day", lag: 3, z: [0, 22], tz: 6, lic: "nasa", op: 0.7, legend: { img: GLEG + "SMAP_Analyzed_Soil_Moisture_H.svg" },
      d: "SMAP L4 analysed surface soil moisture (top 5 cm), daily, 9 km." },
    { id: "rain", g: "Water", name: "Precipitation (GPM IMERG)", kind: "wmts", layer: "IMERG_Precipitation_Rate", tms: "GoogleMapsCompatible_Level6",
      time: "day", lag: 1, z: [0, 22], tz: 6, lic: "nasa", op: 0.75, legend: { img: GLEG + "GPM_Precipitation_Rate_H.svg" },
      d: "GPM IMERG daily precipitation rate, 10 km." }
  ];
  // gases: every pixel covers kilometres (many houses / pools); shown for looking, not as a per-pool measurement
  DEFS.push(
    { id: "hcho", g: "Gases (city scale)", over: true, name: "Formaldehyde HCHO (TEMPO, hourly)", kind: "wmts", layer: "TEMPO_L3_Formaldehyde_Vertical_Column", tms: "GoogleMapsCompatible_Level7",
      time: "hour", z: [0, 22], tz: 7, lic: "nasa", op: 0.35,
      d: "TEMPO formaldehyde column, hourly daytime, ~2 x 5 km pixels. Formed from vegetation and traffic VOCs; one pixel covers thousands of homes." },
    { id: "no2s5p", g: "Gases (city scale)", over: true, name: "NO₂ (Sentinel-5P TROPOMI, daily)", kind: "wmts", layer: "TROPOMI_L2_Nitrogen_Dioxide_Tropospheric_Column", tms: "GoogleMapsCompatible_Level6",
      time: "day", lag: 1, z: [0, 22], tz: 6, lic: "s5p", op: 0.35,
      d: "TROPOMI tropospheric NO₂, one early-afternoon pass a day, 3.5 x 5.5 km pixels (traffic, power plants)." },
    { id: "so2s5p", g: "Gases (city scale)", over: true, name: "SO₂ (Sentinel-5P TROPOMI, latest)", kind: "wmts", layer: "TROPOMI_L2_Sulfur_Dioxide_Total_Vertical_Column", tms: "GoogleMapsCompatible_Level6",
      time: null, z: [0, 22], tz: 6, lic: "s5p", op: 0.35,
      d: "TROPOMI sulfur dioxide column, latest day NASA has published, 3.5 x 5.5 km pixels (volcanoes, smelters, ships, refineries)." },
    { id: "ch4airs", g: "Gases (city scale)", over: true, name: "Methane CH₄ at ~7 km height (AIRS, daily)", kind: "wmts", layer: "AIRS_L2_Methane_400hPa_Volume_Mixing_Ratio_Day", tms: "GoogleMapsCompatible_Level6",
      time: "day", lag: 1, z: [0, 22], tz: 6, lic: "nasa", op: 0.35,
      d: "AIRS methane mixing ratio at 400 hPa (~7 km up), daytime pass, ~45 km footprints: regional background, not ground sources." },
    { id: "coairs", g: "Gases (city scale)", over: true, name: "Carbon monoxide CO at ~5 km (AIRS, daily)", kind: "wmts", layer: "AIRS_L2_Carbon_Monoxide_500hPa_Volume_Mixing_Ratio_Day", tms: "GoogleMapsCompatible_Level6",
      time: "day", lag: 1, z: [0, 22], tz: 6, lic: "nasa", op: 0.35,
      d: "AIRS carbon monoxide at 500 hPa (~5.5 km up), daytime pass, ~45 km footprints (wildfire smoke, pollution transport)." });
  const BY = new Map(DEFS.map(d => [d.id, d]));
  const DRAW = DEFS.filter(d => !d.top).concat(DEFS.filter(d => d.top));   // rasters first, thin vector-style overlays on top
  const GROUPS = ["Hazards", "Vegetation & land", "Air & heat", "Gases (city scale)", "Water"];

  // ---------- helpers
  const ymd = d => d.toISOString().slice(0, 10);
  const daysAgo = n => ymd(new Date(Date.now() - n * 864e5));
  function tileBox(x, y, z) {               // EPSG:3857 bbox of an XYZ tile
    const s = 2 * HALF / (1 << z);
    return [-HALF + x * s, HALF - (y + 1) * s, -HALF + (x + 1) * s, HALF - y * s];
  }
  function lng2x(l) { return l * Math.PI / 180 * R; }
  function lat2y(a) { a = Math.max(-85.05, Math.min(85.05, a)); return R * Math.log(Math.tan(Math.PI / 4 + a * Math.PI / 360)); }
  function hits(b, cov) {                    // b: 3857 bbox, cov: lng/lat
    if (!cov) return true;
    return !(b[2] < lng2x(cov[0]) || b[0] > lng2x(cov[2]) || b[3] < lat2y(cov[1]) || b[1] > lat2y(cov[3]));
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function tileUrl(d, x, y, z, time) {
    const b = tileBox(x, y, z), bb = b.map(v => v.toFixed(2)).join(",");
    if (d.kind === "wmts") return GIBS + d.layer + "/default/" + (time ? time + "/" : "") + d.tms + "/" + z + "/" + y + "/" + x + ".png";
    if (d.kind === "wms") {
      const v13 = d.wms === "1.3.0";
      return d.url + "?SERVICE=WMS&REQUEST=GetMap&VERSION=" + d.wms + "&LAYERS=" + encodeURIComponent(d.layer) + "&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE" +
        (v13 ? "&CRS=EPSG:3857" : "&SRS=EPSG:3857") + "&BBOX=" + bb + "&WIDTH=" + (d.px || 256) + "&HEIGHT=" + (d.px || 256) + (time ? "&TIME=" + time : "");
    }
    // ArcGIS: scale-dependent layers render only below minScale; lower the dpi so the server's computed scale qualifies
    let dpi = 96;
    if (d.minScale) { const sc = (b[2] - b[0]) / (256 / 96 * 0.0254); if (sc > d.minScale) dpi = Math.max(6, Math.floor(96 * d.minScale / sc)); }
    const common = "bbox=" + bb + "&bboxSR=3857&imageSR=3857&size=256,256&f=image";
    if (d.kind === "agi") return d.url + "/exportImage?" + common + "&format=png&interpolation=RSP_NearestNeighbor";
    return d.url + "/export?" + common + "&dpi=" + dpi + "&format=png32&transparent=true" + (d.layers ? "&layers=" + encodeURIComponent(d.layers) : "");
  }

  async function fetchImage(url, signal) {
    const r = await fetch(url, { signal, mode: "cors", credentials: "omit" });
    if (!r.ok) return null;
    const ct = r.headers.get("content-type") || "";
    if (!ct.startsWith("image/")) return null;          // WMS/ArcGIS error documents
    const blob = await r.blob();
    return createImageBitmap(blob);
  }

  const CSS = `
.eo-g { margin-top: 8px; }
.eo-g > .lbl { display: block; margin-bottom: 2px; }
.eo-row { display: flex; gap: 6px; align-items: center; cursor: pointer; font-size: 12.5px; margin: 2px 0; }
.eo-row input { margin: 0; }
.eo-row .eo-z { margin-left: auto; font: 10px var(--f-mono, monospace); color: var(--dim); }
.eo-row.eo-off { opacity: .55; }
.eo-x { margin: 2px 0 7px 20px; padding-left: 7px; border-left: 1px solid var(--edge, #333); }
.eo-x input[type=range] { width: 100%; margin: 2px 0; }
.eo-x .note { margin: 2px 0; }
.eo-x img.eo-leg { display: block; max-width: 100%; max-height: 64px; margin: 3px 0; background: rgba(255,255,255,.06); }
.eo-x .eo-ramp { height: 8px; border-radius: 1px; margin: 4px 0 1px; }
.eo-x .eo-rl { display: flex; justify-content: space-between; font: 10px var(--f-mono, monospace); color: var(--dim); }
.eo-x .eo-al { max-height: 120px; overflow: auto; font-size: 10.5px; color: var(--dim); }
.eo-x .eo-al div { display: flex; gap: 5px; align-items: center; line-height: 1.25; }
.eo-x .eo-al img { width: 14px; height: 14px; flex: none; }
.eo-time { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-top: 4px; }
.eo-time input[type=date] { flex: 1 1 110px; min-width: 0; width: auto !important; }
.eo-time select { width: auto !important; }
`;
  function injectCss() {
    if (document.getElementById("eo-css")) return;
    const s = document.createElement("style"); s.id = "eo-css"; s.textContent = CSS; document.head.appendChild(s);
  }

  class EarthLayers {
    constructor(opts = {}) {
      this.onUpdate = opts.onUpdate || (() => {});
      this.on = new Set(); this.op = {}; this.t = ""; this.h = "";   // t: YYYY-MM-DD ('' = each layer's latest), h: UTC hour '' | '0'..'23'
      this.el = null; this.cache = new Map(); this._agsLeg = new Map(); this._pend = 0;
      this.defs = DEFS;
    }
    // time string for a layer, or null for "service default / static"
    timeFor(d) {
      if (!d.time) return null;
      if (d.time === "hour") {
        if (!this.t && this.h === "") return null;                // GIBS default = latest scan
        const day = this.t || ymd(new Date());
        const hh = this.h === "" ? 23 : +this.h;
        return day + "T" + String(hh).padStart(2, "0") + ":59:59Z";   // GIBS snaps back to the latest scan at/before this instant
      }
      if (d.time === "day") return this.t || daysAgo(d.lag || 1);
      return d.time;
    }
    _kick() { if (this._pend) return; this._pend = requestAnimationFrame(() => { this._pend = 0; this.onUpdate(); }); }

    // over: undefined = every layer; false = ground overlays only; true = the see-through "over" layers (gases), drawn after LiDAR
    layers({ globe = false, zoom = 0, modelMatrix, over } = {}) {
      if (!window.deck || !this.on.size) return [];
      const out = [];
      for (const d of DRAW) {
        if (!this.on.has(d.id)) continue;
        if (over !== undefined && !!d.over !== over) continue;
        if (zoom < d.z[0] - 0.5 || zoom > d.z[1]) continue;
        if (globe && d.cov) continue;                              // only global (GIBS) overlays on the globe
        const time = this.timeFor(d), key = d.id + "|" + (time || "") + "|" + (globe ? "g" : "m");
        const mm = !globe && modelMatrix ? { modelMatrix } : {};
        const self = this;
        out.push(new deck.TileLayer({
          id: "eo:" + d.id + (globe ? ":g" : ""), data: key,
          minZoom: 0, maxZoom: d.tz, tileSize: 256, extent: d.cov || undefined,
          maxRequests: 8, refinementStrategy: "best-available", opacity: this.op[d.id] ?? d.op,
          getTileData: async tile => {
            const { x, y, z } = tile.index;
            if (!hits(tileBox(x, y, z), d.cov)) return null;
            try { return await fetchImage(tileUrl(d, x, y, z, time), tile.signal); } catch (e) { return null; }
          },
          onTileLoad: () => self._kick(),
          updateTriggers: { getTileData: key },
          parameters: { depthWriteEnabled: false, depthCompare: "always" },
          ...mm,
          renderSubLayers: p => {
            if (!p.data) return null;
            const { west, south, east, north } = p.tile.bbox;
            return new deck.BitmapLayer(p, {
              data: null, image: p.data, bounds: [west, south, east, north],
              _imageCoordinateSystem: globe ? deck.COORDINATE_SYSTEM.CARTESIAN : deck.COORDINATE_SYSTEM.DEFAULT,
              parameters: d.blend
                ? { depthWriteEnabled: false, depthCompare: "always", blendColorSrcFactor: "src-alpha", blendColorDstFactor: "one", blendAlphaSrcFactor: "one", blendAlphaDstFactor: "one-minus-src-alpha" }
                : { depthWriteEnabled: false, depthCompare: "always" },
              ...mm
            });
          }
        }));
      }
      return out;
    }

    getState() {
      const s = {}; if (this.on.size) s.on = DEFS.filter(d => this.on.has(d.id)).map(d => d.id);
      const op = {}; for (const id of this.on) if (this.op[id] != null && Math.abs(this.op[id] - BY.get(id).op) > 0.004) op[id] = Math.round(this.op[id] * 100) / 100;
      if (Object.keys(op).length) s.op = op;
      if (this.t) s.t = this.t; if (this.h !== "") s.h = +this.h;
      return s;
    }
    setState(s = {}) {
      this.on = new Set((s.on || []).filter(id => BY.has(id)));
      this.op = {}; for (const [k, v] of Object.entries(s.op || {})) if (BY.has(k) && isFinite(v)) this.op[k] = Math.max(0, Math.min(1, +v));
      this.t = /^\d{4}-\d{2}-\d{2}$/.test(s.t || "") ? s.t : "";
      this.h = s.h != null && s.h !== "" && +s.h >= 0 && +s.h <= 23 ? String(+s.h) : "";
      this._render(); this.onUpdate();
    }
    credits() {
      const seen = new Set(), out = [];
      for (const d of DEFS) if (this.on.has(d.id)) { const c = LIC[d.lic].c; if (!seen.has(c)) { seen.add(c); out.push(c); } }
      return out.join(" · ");
    }
    tooltip() { return null; }

    // ---------- UI
    bindPanel(el) { injectCss(); this.el = el; this._render(); }
    _render() {
      const el = this.el; if (!el) return;
      const anyDay = DEFS.some(d => this.on.has(d.id) && d.time && (d.time === "day" || d.time === "hour"));
      const anyHour = this.on.has("no2");
      let h = `<div class="note">Live satellite and federal map overlays, streamed from the source. Off by default.</div>`;
      if (anyDay) {
        h += `<div class="ctl"><span class="lbl">Date (UTC)${anyHour ? " / hour" : ""}</span><div class="eo-time">
          <button class="chip" data-eo-step="-1" title="previous day">&lsaquo;</button>
          <input type="date" data-eo-t value="${esc(this.t)}" max="${ymd(new Date())}">
          <button class="chip" data-eo-step="1" title="next day">&rsaquo;</button>`;
        if (anyHour) { h += `<select data-eo-h><option value="">latest</option>`; for (let i = 0; i < 24; i++) h += `<option value="${i}"${this.h === String(i) ? " selected" : ""}>${String(i).padStart(2, "0")}Z</option>`; h += `</select>`; }
        h += `<button class="chip" data-eo-latest title="each layer's most recent complete day">latest</button></div>`;
        h += `<div class="note">${this.t ? "Showing " + esc(this.t) : "Latest available day per layer"}.</div></div>`;
      }
      for (const g of GROUPS) {
        h += `<div class="eo-g"><span class="lbl">${g}</span>`;
        for (const d of DEFS.filter(x => x.g === g)) {
          const on = this.on.has(d.id);
          h += `<label class="eo-row" title="${esc(d.d)}"><input type="checkbox" data-eo="${d.id}"${on ? " checked" : ""}><span>${esc(d.name)}</span><span class="eo-z">${d.cov ? "US " : ""}z${d.z[0]}+</span></label>`;
          if (on) {
            const op = this.op[d.id] ?? d.op;
            h += `<div class="eo-x"><input type="range" min="0" max="1" step="0.05" value="${op}" data-eo-op="${d.id}" title="opacity">`;
            h += this._legendHtml(d);
            h += `<div class="note">${esc(d.d)} <a href="${LIC[d.lic].u}" target="_blank" rel="noopener">${esc(LIC[d.lic].c)}</a></div></div>`;
          }
        }
        h += `</div>`;
      }
      el.innerHTML = h;
      el.querySelectorAll("input[data-eo]").forEach(i => i.onchange = () => { const id = i.dataset.eo; i.checked ? this.on.add(id) : this.on.delete(id); this._render(); this.onUpdate(); });
      el.querySelectorAll("input[data-eo-op]").forEach(i => i.oninput = () => { this.op[i.dataset.eoOp] = +i.value; this.onUpdate(); });
      const ti = el.querySelector("input[data-eo-t]"); if (ti) ti.onchange = () => { this.t = ti.value || ""; this._render(); this.onUpdate(); };
      const hs = el.querySelector("select[data-eo-h]"); if (hs) hs.onchange = () => { this.h = hs.value; this._render(); this.onUpdate(); };
      el.querySelectorAll("[data-eo-step]").forEach(b => b.onclick = () => {
        const base = this.t ? new Date(this.t + "T12:00:00Z") : new Date(Date.now() - 864e5);
        const n = new Date(base.getTime() + (+b.dataset.eoStep) * 864e5); if (n.getTime() > Date.now()) return;
        this.t = ymd(n); this._render(); this.onUpdate();
      });
      const lb = el.querySelector("[data-eo-latest]"); if (lb) lb.onclick = () => { this.t = ""; this.h = ""; this._render(); this.onUpdate(); };
      el.querySelectorAll("[data-eo-agsleg]").forEach(n => this._fillAgsLegend(BY.get(n.dataset.eoAgsleg), n));
    }
    _legendHtml(d) {
      const L = d.legend;
      if (L && L.img) return `<img class="eo-leg" alt="legend" src="${L.img}" loading="lazy" onerror="this.remove()">`;
      if (L && L.wmsLegend) return `<img class="eo-leg" alt="legend" style="max-height:120px" src="${d.url}?SERVICE=WMS&REQUEST=GetLegendGraphic&VERSION=1.3.0&FORMAT=image/png&LAYER=${encodeURIComponent(d.layer)}" loading="lazy" onerror="this.remove()">`;
      if (L && L.ramp) return `<div class="eo-ramp" style="background:linear-gradient(90deg,${L.ramp.join(",")})"></div><div class="eo-rl"><span>${esc(L.lo)}</span><span>${esc(L.hi)}</span></div>`;
      if (d.agsLegend != null) return `<div class="eo-al" data-eo-agsleg="${d.id}"></div>`;
      return "";
    }
    async _fillAgsLegend(d, node) {
      try {
        let items = this._agsLeg.get(d.id);
        if (!items) {
          const j = await (await fetch(d.url + "/legend?f=json", { credentials: "omit" })).json();
          const lay = (j.layers || []).find(l => l.layerId === d.agsLegend) || (j.layers || [])[0];
          items = ((lay && lay.legend) || []).filter(x => x.imageData && !/nodata|fill-no/i.test(x.label || "")).slice(0, 40);
          this._agsLeg.set(d.id, items);
        }
        node.innerHTML = items.map(x => `<div><img src="data:${x.contentType || "image/png"};base64,${x.imageData}" alt=""><span>${esc(x.label || "")}</span></div>`).join("");
      } catch (e) { node.remove(); }
    }
  }
  EarthLayers.DEFS = DEFS;
  window.EarthLayers = EarthLayers;
})();

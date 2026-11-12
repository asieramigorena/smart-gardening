'use strict';
/* Shared state, data loading, variable catalogue, colour maps, small numeric helpers. */
const App = { state: { range: null }, sites: {}, hooks: {} };
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const ok = Number.isFinite;
const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const el = (tag, props = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'style') node.style.cssText = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined) node.append(kid);
  return node;
};
const fmt = (v, d = 2) => (ok(v) ? v.toFixed(d) : '–');
const PALETTE = ['#4fd1c5', '#f6ad55', '#63b3ed', '#fc8181', '#b794f4', '#68d391', '#f687b3', '#ecc94b', '#a0aec0'];

/* ---------------- variable catalogue ---------------- */
const CATALOG = [
  [/need_72h/, { label: 'Irrigation need, next 72 h (ET₀ − rain)', unit: 'mm', cmap: 'BrBG', d: 1, diverging: true, rev: true }],
  [/rain_next_24h/, { label: 'Rain, next 24 h', unit: 'mm', cmap: 'YlGnBu', d: 1, zero: true }],
  [/et0_next_24h/, { label: 'ET₀, next 24 h', unit: 'mm', cmap: 'YlOrBr', d: 2, zero: true }],
  [/precipitation_probability|precip_prob/, { label: 'Rain probability', unit: '%', cmap: 'YlGnBu', d: 0, lo: 0, hi: 100 }],
  [/precip_cum/, { label: 'Rain accumulated', unit: 'mm', cmap: 'YlGnBu', d: 1, zero: true }],
  [/water_balance_cum/, { label: 'Rain − ET₀ accumulated', unit: 'mm', cmap: 'BrBG', d: 1, diverging: true }],
  [/precipitation|precip_mm/, { label: 'Precipitation', unit: 'mm', cmap: 'YlGnBu', d: 2, agg: 'sum', zero: true }],
  [/et0/, { label: 'ET₀', unit: 'mm', cmap: 'YlOrBr', d: 3, agg: 'sum', zero: true }],
  [/soil_moisture/, { label: 'Soil moisture', unit: 'm³/m³', cmap: 'viridis', d: 3, soil: true, rev: true }],
  [/soil_temp/, { label: 'Soil temperature', unit: '°C', cmap: 'inferno', d: 1, soil: true }],
  [/surface_temp/, { label: 'Ground surface temperature', unit: '°C', cmap: 'inferno', d: 1 }],
  [/air_temp_max/, { label: 'Air temperature, hourly max', unit: '°C', cmap: 'turbo', d: 1 }],
  [/air_temp_min/, { label: 'Air temperature, hourly min', unit: '°C', cmap: 'turbo', d: 1 }],
  [/temperature_2m|air_temp/, { label: 'Air temperature', unit: '°C', cmap: 'turbo', d: 1 }],
  [/relative_humidity|rh_pct/, { label: 'Relative humidity', unit: '%', cmap: 'GnBu', d: 0, hi: 100 }],
  [/vapour_pressure_deficit/, { label: 'Vapour pressure deficit', unit: 'kPa', cmap: 'magma', d: 2, zero: true }],
  [/shortwave|solar_rad/, { label: 'Solar radiation', unit: 'W/m²', cmap: 'inferno', d: 0, zero: true }],
  [/cloud_cover/, { label: 'Cloud cover', unit: '%', cmap: 'Greys', d: 0, lo: 0, hi: 100, rev: true }],
  [/wind_direction/, { label: 'Wind direction (from)', unit: '°', cmap: 'sinebow', d: 0, lo: 0, hi: 360 }],
  [/wind_speed/, { label: 'Wind speed', unit: 'm/s', cmap: 'viridis', d: 1, zero: true }],
  [/surface_pressure/, { label: 'Surface pressure', unit: 'hPa', cmap: 'cividis', d: 1 }],
];
const describeCache = new Map();
function describe(key) {
  if (describeCache.has(key)) return describeCache.get(key);
  let base = key, suffix = '', group = 'Observed';
  const lead = /_lead(\d)d$/.exec(base);
  if (lead) { base = base.replace(/_lead\dd$/, ''); suffix = ` · forecast +${lead[1]} d`; }
  if (base.startsWith('fc_')) { base = base.slice(3); group = 'Forecast as issued'; }
  if (base.startsWith('om_')) { base = base.slice(3); suffix = ' · model' + suffix; group = 'Model (reanalysis)'; }
  let info = { label: key, unit: '', cmap: 'viridis', d: 2 };
  for (const [re, meta] of CATALOG) if (re.test(base)) { info = { ...meta }; break; }
  const depth = /_(\d+)_to_(\d+)cm|_(\d+)cm/.exec(base);
  if (depth) info.label += depth[1] ? ` ${depth[1]}–${depth[2]} cm` : ` ${depth[3]} cm`;
  info.label += suffix;
  info.key = key; info.group = group; info.lead = lead ? +lead[1] : 0;
  describeCache.set(key, info);
  return info;
}

/* ---------------- colour maps ---------------- */
const CMAPS = {
  viridis: d3.interpolateViridis, magma: d3.interpolateMagma, inferno: d3.interpolateInferno, plasma: d3.interpolatePlasma,
  cividis: d3.interpolateCividis, turbo: d3.interpolateTurbo, YlGnBu: d3.interpolateYlGnBu, GnBu: d3.interpolateGnBu,
  YlOrBr: d3.interpolateYlOrBr, Blues: d3.interpolateBlues, Greys: d3.interpolateGreys, RdBu: d3.interpolateRdBu,
  BrBG: d3.interpolateBrBG, RdYlBu: d3.interpolateRdYlBu, PuOr: d3.interpolatePuOr, sinebow: d3.interpolateSinebow,
};
const lutCache = new Map();
function lut(name, reverse) {
  const id = name + (reverse ? '_r' : '');
  if (lutCache.has(id)) return lutCache.get(id);
  const f = CMAPS[name] || CMAPS.viridis, out = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const c = d3.rgb(f(reverse ? 1 - i / 255 : i / 255));
    out[i * 3] = c.r; out[i * 3 + 1] = c.g; out[i * 3 + 2] = c.b;
  }
  lutCache.set(id, out);
  return out;
}
function paintBar(canvas, table) {
  canvas.width = 256; canvas.height = 1;
  const ctx = canvas.getContext('2d'), img = ctx.createImageData(256, 1);
  for (let i = 0; i < 256; i++) { img.data[i * 4] = table[i * 3]; img.data[i * 4 + 1] = table[i * 3 + 1]; img.data[i * 4 + 2] = table[i * 3 + 2]; img.data[i * 4 + 3] = 255; }
  ctx.putImageData(img, 0, 0);
}
function legendBar(title, table, lo, hi, digits) {
  const canvas = el('canvas', { class: 'legend-bar' });
  paintBar(canvas, table);
  const ticks = el('div', { class: 'legend-ticks' });
  for (let i = 0; i <= 4; i++) ticks.append(el('span', { text: fmt(lo + (hi - lo) * i / 4, digits) }));
  return el('div', {}, el('div', { class: 'legend-title', text: title }), canvas, ticks);
}
function fillSelect(sel, options, value) {
  sel.replaceChildren();
  let group = null, groupName = null;
  for (const o of options) {
    const opt = el('option', { value: o.value, text: o.label });
    if (o.group) {
      if (o.group !== groupName) { groupName = o.group; group = el('optgroup', { label: o.group }); sel.append(group); }
      group.append(opt);
    } else sel.append(opt);
  }
  if (value !== undefined && options.some((o) => o.value === value)) sel.value = value;
}
const varOptions = (keys) => keys.map((k) => { const d = describe(k); return { value: k, label: `${d.label} (${d.unit})`, group: d.group }; })
  .sort((a, b) => (a.group === b.group ? 0 : a.group < b.group ? 1 : -1));

/* ---------------- statistics ---------------- */
function quantiles(arr, qs) {
  const clean = Float64Array.from(arr).filter(ok).sort();
  if (!clean.length) return qs.map(() => NaN);
  return qs.map((q) => clean[Math.min(clean.length - 1, Math.floor(q * (clean.length - 1)))]);
}
function pearson(x, y, lag = 0, i0 = 0, i1 = x.length) {
  let n = 0, sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
  for (let i = i0; i < i1; i++) {
    const j = i + lag;
    if (j < 0 || j >= y.length) continue;
    const a = x[i], b = y[j];
    if (!ok(a) || !ok(b)) continue;
    n++; sx += a; sy += b; sxx += a * a; syy += b * b; sxy += a * b;
  }
  if (n < 3) return { r: NaN, n, slope: NaN, intercept: NaN };
  const cov = sxy - sx * sy / n, vx = sxx - sx * sx / n, vy = syy - sy * sy / n;
  const slope = cov / vx;
  return { r: cov / Math.sqrt(vx * vy), n, slope, intercept: (sy - slope * sx) / n };
}
/* Aggregate an hourly column into blocks of `size` hours (sum or mean); needs 75 % coverage. */
function aggregate(col, size, how) {
  const n = Math.floor(col.length / size), out = new Float64Array(n);
  for (let b = 0; b < n; b++) {
    let s = 0, c = 0;
    for (let i = b * size; i < (b + 1) * size; i++) if (ok(col[i])) { s += col[i]; c++; }
    out[b] = c >= size * 0.75 ? (how === 'sum' ? s * size / c : s / c) : NaN;
  }
  return out;
}
function rolling(col, win) {
  const out = new Float64Array(col.length).fill(NaN);
  let s = 0, c = 0;
  for (let i = 0; i < col.length; i++) {
    if (ok(col[i])) { s += col[i]; c++; }
    if (i >= win && ok(col[i - win])) { s -= col[i - win]; c--; }
    if (i >= win - 1 && c >= win * 0.6) out[i] = s / c;
  }
  return out;
}

/* ---------------- sun position (NOAA low-precision formulas) ---------------- */
function sunPosition(epochSec, latDeg, lonDeg) {
  const rad = Math.PI / 180, d = (epochSec - 946728000) / 86400;
  const g = (357.529 + 0.98560028 * d) * rad, q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * rad, e = (23.439 - 0.00000036 * d) * rad;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) % 24;
  const ha = (gmst * 15 + lonDeg) * rad - ra, lat = latDeg * rad;
  const alt = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(ha));
  const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(lat) - Math.sin(lat) * Math.cos(ha));
  return { altitude: alt, azimuth: (az + 2 * Math.PI) % (2 * Math.PI) };
}

/* ---------------- loading ---------------- */
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function getBuffer(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.arrayBuffer();
}
const SITES = {
  bilbao: { label: 'Bilbao (weather model, no probes yet)', file: 'series_bilbao.json', utcOffset: 1, lat: 43.263, lon: -2.935,
    defaults: ['soil_moisture_0_to_7cm', 'soil_moisture_7_to_28cm', 'soil_moisture_28_to_100cm', 'precipitation', 'et0_fao_evapotranspiration', 'temperature_2m', 'shortwave_radiation'] },
  darrington: { label: 'Darrington WA (USCRN station, real probes)', file: 'series_darrington.json', utcOffset: -8, lat: 48.54, lon: -121.45,
    defaults: ['soil_moisture_5cm', 'soil_moisture_10cm', 'soil_moisture_20cm', 'soil_moisture_50cm', 'soil_moisture_100cm', 'precip_mm', 'air_temp_c', 'solar_rad_wm2'] },
};
async function loadSite(id) {
  if (App.sites[id]) return App.sites[id];
  const raw = await getJSON('data/' + SITES[id].file);
  const cols = {};
  for (const [name, values] of Object.entries(raw.cols)) {
    const a = new Float64Array(raw.n);
    for (let i = 0; i < raw.n; i++) a[i] = values[i] === null ? NaN : values[i];
    cols[name] = a;
  }
  const time = new Float64Array(raw.n);
  for (let i = 0; i < raw.n; i++) time[i] = raw.t0 + i * raw.dt;
  const site = { id, ...SITES[id], t0: raw.t0, n: raw.n, time, cols, names: Object.keys(cols) };
  App.sites[id] = site;
  return site;
}
/* Index range [i0, i1) of a site that matches the Time series zoom, or everything. */
function zoomRange(site, useZoom) {
  const r = App.state.range;
  if (!useZoom || !r || r.site !== site.id) return [0, site.n];
  return [clamp(Math.floor((r.min - site.t0) / 3600), 0, site.n), clamp(Math.ceil((r.max - site.t0) / 3600) + 1, 0, site.n)];
}

async function loadGrid() {
  const meta = App.meta = await getJSON('data/meta.json');
  const [gridBuf, demBuf] = await Promise.all([getBuffer('data/grid.bin'), getBuffer('data/dem.bin')]);
  const g = meta.grid, nx = g.lons.length, ny = g.lats.length, nt = g.times.length, size = nt * ny * nx;
  const cube = new Float32Array(gridBuf);
  const fields = {};
  g.variables.forEach((name, v) => { fields[name] = cube.subarray(v * size, (v + 1) * size); });
  // derived: accumulations from the first hour of the window
  const rain = fields.precipitation, et0 = fields.et0_fao_evapotranspiration, cell = ny * nx;
  if (rain && et0) {
    const cum = new Float32Array(size), bal = new Float32Array(size);
    for (let t = 0; t < nt; t++) for (let c = 0; c < cell; c++) {
      const prev = t ? (t - 1) * cell + c : -1, k = t * cell + c;
      cum[k] = (t ? cum[prev] : 0) + (rain[k] || 0);
      bal[k] = (t ? bal[prev] : 0) + (rain[k] || 0) - (et0[k] || 0);
    }
    fields.precip_cum = cum; fields.water_balance_cum = bal;
    // forward-looking windows from each hour: what the next day / three days hold
    const ahead = (src, hours) => {
      const out = new Float32Array(size).fill(NaN);
      for (let t = 0; t + hours < nt; t++) for (let c = 0; c < cell; c++) {
        let sum = 0;
        for (let h = 1; h <= hours; h++) sum += src[(t + h) * cell + c] || 0;
        out[t * cell + c] = sum;
      }
      return out;
    };
    fields.rain_next_24h = ahead(rain, 24);
    fields.et0_next_24h = ahead(et0, 24);
    const r72 = ahead(rain, 72), e72 = ahead(et0, 72), need = new Float32Array(size);
    for (let k = 0; k < size; k++) need[k] = e72[k] - r72[k];
    fields.need_72h = need;
  }
  const stats = {};
  for (const [name, f] of Object.entries(fields)) {
    const info = describe(name);
    let [p2, p98, mn, mx] = quantiles(f, [0.02, 0.98, 0, 1]);
    if (info.diverging) { const m = Math.max(Math.abs(p2), Math.abs(p98)), M = Math.max(Math.abs(mn), Math.abs(mx)); p2 = -m; p98 = m; mn = -M; mx = M; }
    if (info.zero) { p2 = 0; mn = 0; }
    if (info.lo !== undefined) { p2 = mn = info.lo; }
    if (info.hi !== undefined && info.lo !== undefined) { p98 = mx = info.hi; }
    if (!(p98 > p2)) p98 = p2 + 1;
    if (!(mx > mn)) mx = mn + 1;
    stats[name] = { p2, p98, min: mn, max: mx };
  }
  App.grid = { nx, ny, nt, cell, lats: g.lats, lons: g.lons, times: g.times, fields, stats, names: Object.keys(fields) };
  App.dem = { ...meta.dem, z: new Int16Array(demBuf) };
  try { App.parks = await getJSON('data/parks.json'); } catch (err) { App.parks = null; console.warn('No parks layer:', err.message); }
}

/* ---------------- icons (our own static SVG, safe to inject) ---------------- */
const ICONS = {
  tree: '<path d="M12 2.5a5.5 5.5 0 0 0-5.3 7A4.5 4.5 0 0 0 8 18h3v3.5h2V18h3a4.5 4.5 0 0 0 1.3-8.5A5.5 5.5 0 0 0 12 2.5z" fill="currentColor"/>',
  flower: '<g fill="currentColor"><circle cx="12" cy="6.5" r="3.2"/><circle cx="6.8" cy="10.3" r="3.2"/><circle cx="17.2" cy="10.3" r="3.2"/><circle cx="8.8" cy="16.3" r="3.2"/><circle cx="15.2" cy="16.3" r="3.2"/></g><circle cx="12" cy="11.8" r="2.3" fill="#10161d"/>',
  sprout: '<path d="M12 21v-9M6 21h12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M12 13C12 8.5 8.8 6 4 6c0 4.5 3.2 7 8 7zM12 11c0-4.6 3.2-7.5 8-7.5 0 4.6-3.2 7.5-8 7.5z" fill="currentColor"/>',
  flag: '<path d="M6 21.5V2.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M7 3h11l-3 4 3 4H7z" fill="currentColor"/>',
  shield: '<path d="M12 2l8 3v6.5c0 5-3.4 8.7-8 10.5-4.6-1.8-8-5.5-8-10.5V5z" fill="currentColor"/><path d="M12 7.5c-2.6 1.2-3.8 3.4-3 6.5 3-.4 4.6-2.6 3-6.5z" fill="#10161d"/>',
  drop: '<path d="M12 2.5C8.2 7.6 6 11 6 14.3a6 6 0 0 0 12 0C18 11 15.8 7.6 12 2.5z" fill="currentColor"/>',
  station: '<path d="M10 3.5a2 2 0 0 1 4 0v9.6a4.2 4.2 0 1 1-4 0z" fill="currentColor"/><circle cx="12" cy="16.8" r="1.8" fill="#10161d"/>',
  pin: '<circle cx="12" cy="12" r="7.5" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="3 2.4"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/>',
  isoline: '<path d="M2 14c4-6 7 2 10-3s6-4 10-1" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2 20c4-5 8 1 11-2.5S19 15 22 16" fill="none" stroke="currentColor" stroke-width="1.4" opacity=".6"/>',
  wind: '<path d="M4 19L18 5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M10.5 5H18v7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
  grid: '<g fill="currentColor"><circle cx="5" cy="5" r="1.8"/><circle cx="12" cy="5" r="1.8"/><circle cx="19" cy="5" r="1.8"/><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/><circle cx="5" cy="19" r="1.8"/><circle cx="12" cy="19" r="1.8"/><circle cx="19" cy="19" r="1.8"/></g>',
  values: '<text x="12" y="16.5" text-anchor="middle" font-size="11.5" font-family="ui-monospace,Menlo,monospace" font-weight="700" fill="currentColor">1.2</text>',
  streets: '<path d="M4 21L9.5 3M20 21L14.5 3M12 5v3M12 11v3M12 17v3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
  relief: '<path d="M2 20l6.5-10 4 5 3-4L22 20z" fill="currentColor"/>',
  sun: '<circle cx="12" cy="12" r="4.2" fill="currentColor"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  shadow: '<path d="M2 20l6.5-10 4 5 3-4L22 20z" fill="currentColor" opacity=".4"/><path d="M8.5 10l4 5 3-4 2.5 3.5L8 20z" fill="currentColor"/>',
  sea: '<path d="M2 9c3-2.4 5.5 2.4 8.5 0s5.5-2.4 8.5 0c1.5 1.2 3 .6 3 .6M2 15c3-2.4 5.5 2.4 8.5 0s5.5-2.4 8.5 0c1.5 1.2 3 .6 3 .6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z" fill="currentColor"/><path d="M3 12.5l9 5 9-5M3 16.5l9 5 9-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
};
const iconSVG = (name, size = 16) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" focusable="false">${ICONS[name] || ''}</svg>`;
const iconEl = (name, size = 16, colour) => {
  const span = document.createElement('span');
  span.className = 'ico';
  if (colour) span.style.color = colour;
  span.innerHTML = iconSVG(name, size);
  return span;
};
/* Green-space categories: colour, icon, outline dash, drawing order (bottom first). */
const PARK_STYLE = {
  protected: { colour: '#b794f4', icon: 'shield', dash: '7 5', order: 0, fill: 0.05 },
  park: { colour: '#68d391', icon: 'tree', order: 1, fill: 0.16 },
  golf: { colour: '#63b3ed', icon: 'flag', order: 2, fill: 0.14 },
  garden: { colour: '#f687b3', icon: 'flower', order: 3, fill: 0.16 },
  allotments: { colour: '#f6ad55', icon: 'sprout', order: 4, fill: 0.22 },
};
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

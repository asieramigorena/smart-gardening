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
}

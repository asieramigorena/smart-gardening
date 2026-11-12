'use strict';
/* Map tab: terrain-shaded composite of forecast fields with single-variable or RGB channel mixing. */
App.initMap = function initMap() {
  const G = App.grid, D = App.dem, site = App.meta.site;
  const W = D.width, H = D.height, N = W * H;
  const st = App.state.map = {
    mode: 'single', v: 'precipitation', cmap: 'YlGnBu', rev: false,
    r: 'vapour_pressure_deficit', g: 'soil_moisture_9_to_27cm', b: 'precipitation', gamma: 0.8,
    range: 'robust', interp: 'bilinear', opacity: 0.85,
    contours: true, wind: false, points: false, osm: 0.45,
    relief: true, light: 'sun', shadows: true,
    t: 0, playing: false, timer: null, pin: null,
  };
  const now = Date.now() / 1000;
  st.t = clamp(G.times.findIndex((t) => t >= now - 1800), 0, G.nt - 1);
  if (st.t < 0) st.t = 0;

  /* ---------- geometry: DEM pixel <-> lon/lat <-> grid index ---------- */
  const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const mN = mercY(D.north), mS = mercY(D.south);
  const latOfRow = (y) => (Math.atan(Math.sinh(mN - (y / H) * (mN - mS))) * 180) / Math.PI;
  const lonOfCol = (x) => D.west + (x / W) * (D.east - D.west);
  const gridCol = (lon) => ((lon - G.lons[0]) / (G.lons[G.nx - 1] - G.lons[0])) * (G.nx - 1);
  const gridRow = (lat) => ((G.lats[0] - lat) / (G.lats[0] - G.lats[G.ny - 1])) * (G.ny - 1);
  const colF = new Float32Array(W), rowF = new Float32Array(H);
  for (let x = 0; x < W; x++) colF[x] = clamp(gridCol(lonOfCol(x + 0.5)), 0, G.nx - 1);
  for (let y = 0; y < H; y++) rowF[y] = clamp(gridRow(latOfRow(y + 0.5)), 0, G.ny - 1);

  /* ---------- terrain normals (computed once) ---------- */
  const exaggeration = 2.2, px = D.metres_per_pixel;
  const nX = new Float32Array(N), nY = new Float32Array(N), nZ = new Float32Array(N);
  const slopeDeg = new Float32Array(N), aspectDeg = new Float32Array(N);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const k = y * W + x;
    const xl = x > 0 ? k - 1 : k, xr = x < W - 1 ? k + 1 : k, yu = y > 0 ? k - W : k, yd = y < H - 1 ? k + W : k;
    const dzdx = (D.z[xr] - D.z[xl]) / ((xr - xl) * px || px);       // towards east
    const dzdy = (D.z[yu] - D.z[yd]) / (((yd - yu) / W) * px || px); // towards north
    const len = Math.hypot(dzdx * exaggeration, dzdy * exaggeration, 1);
    nX[k] = -dzdx * exaggeration / len; nY[k] = -dzdy * exaggeration / len; nZ[k] = 1 / len;
    slopeDeg[k] = (Math.atan(Math.hypot(dzdx, dzdy)) * 180) / Math.PI;
    aspectDeg[k] = ((Math.atan2(-dzdx, -dzdy) * 180) / Math.PI + 360) % 360; // downslope direction, from north
  }
  const shade = new Float32Array(N).fill(0.7);
  let shadeKey = '';
  function updateShade() {
    const sun = sunPosition(G.times[st.t], site.lat, site.lon);
    const useSun = st.light === 'sun';
    const key = [useSun ? st.t : 'nw', st.shadows, st.relief].join('|');
    $('#sun-readout').textContent = `sun azimuth  ${fmt((sun.azimuth * 180) / Math.PI, 0).padStart(4)}°\nsun altitude ${fmt((sun.altitude * 180) / Math.PI, 1).padStart(5)}°` + (sun.altitude <= 0 ? '  (night)' : '');
    if (key === shadeKey) return;
    shadeKey = key;
    if (!st.relief) { shade.fill(0.72); return; }
    const night = useSun && sun.altitude <= 0.01;   // after dark: keep the relief readable with a dimmed fixed light
    const az = useSun && !night ? sun.azimuth : (315 * Math.PI) / 180, alt = useSun && !night ? sun.altitude : Math.PI / 4;
    const lx = Math.sin(az) * Math.cos(alt), ly = Math.cos(az) * Math.cos(alt), lz = Math.sin(alt), dim = night ? 0.78 : 1;
    for (let k = 0; k < N; k++) { const s = nX[k] * lx + nY[k] * ly + nZ[k] * lz; shade[k] = (s > 0 ? 0.3 + 0.75 * s : 0.3) * dim; }
    if (st.shadows && useSun && !night) {
      const dx = Math.sin(az), dy = -Math.cos(az), rise = Math.tan(alt) * px;
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const k = y * W + x, z0 = D.z[k];
        for (let s = 2; s < 220; s *= 1.3) {
          const sx = Math.round(x + dx * s), sy = Math.round(y + dy * s);
          if (sx < 0 || sy < 0 || sx >= W || sy >= H) break;
          if (D.z[sy * W + sx] > z0 + rise * s + 4) { shade[k] *= 0.55; break; }
        }
      }
    }
  }

  /* ---------- Leaflet ---------- */
  const bounds = L.latLngBounds([D.south, D.west], [D.north, D.east]);
  const map = L.map('map', { zoomSnap: 0.25, zoomDelta: 0.5, minZoom: 9, maxZoom: 15, maxBounds: bounds.pad(0.4), attributionControl: true });
  map.fitBounds(bounds);
  map.attributionControl.setPrefix('').addAttribution('Weather: Open-Meteo · Terrain: Mapzen/AWS Terrarium · Streets: © OpenStreetMap');
  const CanvasOverlay = L.ImageOverlay.extend({
    _initImage() {
      const node = (this._image = this._url);
      L.DomUtil.addClass(node, 'leaflet-image-layer');
      if (this._zoomAnimated) L.DomUtil.addClass(node, 'leaflet-zoom-animated');
      if (this.options.className) L.DomUtil.addClass(node, this.options.className);
      node.onselectstart = L.Util.falseFn;
    },
  });
  map.createPane('field').style.zIndex = 300;
  map.createPane('labels').style.zIndex = 350;
  map.createPane('vector').style.zIndex = 380;
  map.getPane('labels').style.pointerEvents = 'none';
  map.getPane('vector').style.pointerEvents = 'none';
  const fieldCanvas = el('canvas', { width: W, height: H });
  const fctx = fieldCanvas.getContext('2d');
  const image = fctx.createImageData(W, H);
  new CanvasOverlay(fieldCanvas, bounds, { pane: 'field', className: 'field-canvas', interactive: false }).addTo(map);
  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { pane: 'labels', className: 'osm-labels', opacity: st.osm, maxZoom: 19 }).addTo(map);
  const VS = 2, VW = W * VS, VH = H * VS;
  const vecCanvas = el('canvas', { width: VW, height: VH });
  const vctx = vecCanvas.getContext('2d');
  new CanvasOverlay(vecCanvas, bounds, { pane: 'vector', interactive: false }).addTo(map);
  const vx = (lon) => ((lon - D.west) / (D.east - D.west)) * VW;
  const vy = (lat) => ((mN - mercY(lat)) / (mN - mS)) * VH;
  L.circleMarker([site.lat, site.lon], { radius: 5, color: '#fff', weight: 1.5, fillColor: '#4fd1c5', fillOpacity: 1 }).addTo(map).bindTooltip(site.name + ' (garden site)');
  L.circleMarker([43.302, -2.911], { radius: 4, color: '#fff', weight: 1.2, fillColor: '#f6ad55', fillOpacity: 1 }).addTo(map).bindTooltip('Bilbao airport · AEMET station');
  const legendControl = L.control({ position: 'bottomleft' });
  legendControl.onAdd = () => { const box = el('div', { class: 'map-legend', id: 'map-legend' }); L.DomEvent.disableClickPropagation(box); return box; };
  legendControl.addTo(map);
  const pinMarker = L.circleMarker([site.lat, site.lon], { radius: 8, color: '#fff', weight: 2, fill: false, dashArray: '3 3' });

  /* ---------- ranges ---------- */
  function rangeOf(name) {
    const s = G.stats[name];
    if (st.range === 'global') return [s.min, s.max];
    if (st.range === 'frame') {
      const f = G.fields[name], base = st.t * G.cell;
      let lo = Infinity, hi = -Infinity;
      for (let c = 0; c < G.cell; c++) { const v = f[base + c]; if (ok(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
      if (describe(name).diverging) { const m = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m; hi = m; }
      return hi > lo ? [lo, hi] : [lo, lo + 1];
    }
    return [s.p2, s.p98];
  }
  /* bilinear sample of a field at fractional grid position */
  function sample(f, base, fx, fy) {
    const i0 = fx | 0, j0 = fy | 0, i1 = i0 < G.nx - 1 ? i0 + 1 : i0, j1 = j0 < G.ny - 1 ? j0 + 1 : j0, wx = fx - i0, wy = fy - j0;
    const a = f[base + j0 * G.nx + i0], b = f[base + j0 * G.nx + i1], c = f[base + j1 * G.nx + i0], d = f[base + j1 * G.nx + i1];
    return (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy;
  }

  /* ---------- raster composite ---------- */
  function channels() {
    return st.mode === 'single' ? [st.v] : [st.r, st.g, st.b];
  }
  function renderField() {
    updateShade();
    const names = channels(), base = st.t * G.cell, nearest = st.interp === 'nearest';
    const fs = names.map((n) => (n === 'none' ? null : G.fields[n]));
    const rg = names.map((n) => (n === 'none' ? [0, 1] : rangeOf(n)));
    const soil = names.map((n) => n !== 'none' && !!describe(n).soil);
    const table = st.mode === 'single' ? lut(st.cmap, st.rev) : null;
    const fade = st.mode === 'single' && !!describe(st.v).zero && describe(st.v).agg === 'sum';
    const gam = new Uint8Array(256);
    for (let i = 0; i < 256; i++) gam[i] = Math.round(255 * Math.pow(i / 255, st.gamma));
    const data = image.data, op = st.opacity, nx = G.nx;
    const vals = [0, 0, 0];
    for (let y = 0; y < H; y++) {
      const fy = nearest ? Math.round(rowF[y]) : rowF[y];
      const j0 = fy | 0, j1 = j0 < G.ny - 1 ? j0 + 1 : j0, wy = fy - j0;
      for (let x = 0; x < W; x++) {
        const k = y * W + x, sea = D.z[k] <= 0;
        const fx = nearest ? Math.round(colF[x]) : colF[x];
        const i0 = fx | 0, i1 = i0 < nx - 1 ? i0 + 1 : i0, wx = fx - i0;
        let r, g, b, has = true;
        for (let c = 0; c < fs.length; c++) {
          const f = fs[c];
          if (!f || (sea && soil[c])) { vals[c] = NaN; continue; }
          const p = base + j0 * nx, q = base + j1 * nx;
          const v = (f[p + i0] * (1 - wx) + f[p + i1] * wx) * (1 - wy) + (f[q + i0] * (1 - wx) + f[q + i1] * wx) * wy;
          vals[c] = (v - rg[c][0]) / (rg[c][1] - rg[c][0]);
        }
        if (table) {
          if (ok(vals[0])) { const i = (clamp(vals[0], 0, 1) * 255) | 0; r = table[i * 3]; g = table[i * 3 + 1]; b = table[i * 3 + 2]; } else has = false;
        } else {
          r = ok(vals[0]) ? gam[(clamp(vals[0], 0, 1) * 255) | 0] : 0;
          g = ok(vals[1]) ? gam[(clamp(vals[1], 0, 1) * 255) | 0] : 0;
          b = ok(vals[2]) ? gam[(clamp(vals[2], 0, 1) * 255) | 0] : 0;
          if (sea && soil.every((s, c) => s || !fs[c])) has = false;
        }
        const br = sea ? 34 : 150, bg = sea ? 48 : 150, bb = sea ? 66 : 146;
        const a = has ? (fade ? op * clamp(vals[0] / 0.06, 0, 1) : op) : 0, s = sea ? (st.relief ? 0.9 : 1) : shade[k] * (st.relief ? 1.25 : 1.35);
        const o = k * 4;
        data[o] = (br + (r - br) * a) * s; data[o + 1] = (bg + (g - bg) * a) * s; data[o + 2] = (bb + (b - bb) * a) * s; data[o + 3] = 255;
      }
    }
    fctx.putImageData(image, 0, 0);
  }

  /* ---------- vector overlays ---------- */
  function halo(text, x, y) {
    vctx.lineWidth = 4; vctx.strokeStyle = 'rgba(0,0,0,.75)'; vctx.strokeText(text, x, y);
    vctx.fillStyle = '#fff'; vctx.fillText(text, x, y);
  }
  function renderVectors() {
    vctx.clearRect(0, 0, VW, VH);
    const base = st.t * G.cell;
    const lead = st.mode === 'single' ? st.v : st.r;
    if (st.contours && lead !== 'none') {
      const f = G.fields[lead], UP = 6, cw = (G.nx - 1) * UP + 1, chh = (G.ny - 1) * UP + 1;
      const up = new Float64Array(cw * chh);
      for (let j = 0; j < chh; j++) for (let i = 0; i < cw; i++) up[j * cw + i] = sample(f, base, i / UP, j / UP);
      const [lo, hi] = rangeOf(lead);
      const levels = d3.ticks(lo, hi, 6).filter((v) => v > lo && v < hi);
      const placed = [];
      const info = describe(lead);
      const lonAt = (cx) => G.lons[0] + ((cx - 0.5) / (cw - 1)) * (G.lons[G.nx - 1] - G.lons[0]);
      const latAt = (cy) => G.lats[0] - ((cy - 0.5) / (chh - 1)) * (G.lats[0] - G.lats[G.ny - 1]);
      vctx.font = '600 20px ui-monospace, Menlo, monospace'; vctx.textAlign = 'center'; vctx.textBaseline = 'middle';
      for (const c of d3.contours().size([cw, chh]).thresholds(levels)(up)) {
        vctx.beginPath();
        const labels = [];
        for (const poly of c.coordinates) for (const ring of poly) {
          if (ring.length < 8) continue;
          ring.forEach(([cx, cy], i) => { const X = vx(lonAt(cx)), Y = vy(latAt(cy)); if (i) vctx.lineTo(X, Y); else vctx.moveTo(X, Y); });
          if (ring.length > 80) { const [cx, cy] = ring[Math.floor(ring.length / 3)]; labels.push([vx(lonAt(cx)), vy(latAt(cy)), ring.length]); }
        }
        vctx.lineWidth = 3.4; vctx.strokeStyle = 'rgba(0,0,0,.45)'; vctx.stroke();
        vctx.lineWidth = 1.5; vctx.strokeStyle = 'rgba(255,255,255,.85)'; vctx.stroke();
        labels.sort((a, b) => b[2] - a[2]);
        for (const [X, Y] of labels.slice(0, 2)) {
          if (X < 60 || X > VW - 60 || Y < 30 || Y > VH - 30 || placed.some(([px2, py2]) => Math.hypot(px2 - X, py2 - Y) < 150)) continue;
          placed.push([X, Y]);
          halo(fmt(c.value, Math.min(info.d, 2)), X, Y);
        }
      }
    }
    if (st.wind && G.fields.wind_speed_10m && G.fields.wind_direction_10m) {
      const sp = G.fields.wind_speed_10m, di = G.fields.wind_direction_10m, top = G.stats.wind_speed_10m.p98 || 10;
      for (let j = 0; j < G.ny; j += 1) for (let i = (j % 2); i < G.nx; i += 2) {
        const v = sp[base + j * G.nx + i], d = di[base + j * G.nx + i];
        if (!ok(v) || !ok(d)) continue;
        const X = vx(G.lons[i]), Y = vy(G.lats[j]), len = 14 + 46 * clamp(v / top, 0, 1), a = ((d + 180) * Math.PI) / 180; // blowing towards
        const ux = Math.sin(a), uy = -Math.cos(a), x2 = X + ux * len, y2 = Y + uy * len;
        for (const [w, col] of [[5, 'rgba(0,0,0,.6)'], [2.2, '#fff']]) {
          vctx.lineWidth = w; vctx.strokeStyle = col; vctx.beginPath();
          vctx.moveTo(X - ux * len * 0.3, Y - uy * len * 0.3); vctx.lineTo(x2, y2);
          vctx.lineTo(x2 - ux * 10 - uy * 6, y2 - uy * 10 + ux * 6); vctx.moveTo(x2, y2); vctx.lineTo(x2 - ux * 10 + uy * 6, y2 - uy * 10 - ux * 6);
          vctx.stroke();
        }
      }
    }
    if (st.points) {
      vctx.fillStyle = 'rgba(255,255,255,.8)';
      for (let j = 0; j < G.ny; j++) for (let i = 0; i < G.nx; i++) { vctx.beginPath(); vctx.arc(vx(G.lons[i]), vy(G.lats[j]), 2.5, 0, 6.3); vctx.fill(); }
    }
  }

  /* ---------- legend ---------- */
  function renderLegend() {
    const box = $('#map-legend');
    box.replaceChildren();
    if (st.mode === 'single') {
      const info = describe(st.v), [lo, hi] = rangeOf(st.v);
      box.append(legendBar(`${info.label} (${info.unit})`, lut(st.cmap, st.rev), lo, hi, info.d));
      return;
    }
    [['r', 0], ['g', 1], ['b', 2]].forEach(([ch, idx]) => {
      const name = st[ch];
      if (name === 'none') return;
      const info = describe(name), [lo, hi] = rangeOf(name), table = new Uint8Array(256 * 3);
      for (let i = 0; i < 256; i++) table[i * 3 + idx] = Math.round(255 * Math.pow(i / 255, st.gamma));
      box.append(legendBar(`${ch.toUpperCase()} · ${info.label} (${info.unit})`, table, lo, hi, info.d));
    });
    // additive mixing key
    const size = 118, tri = el('canvas', { width: size, height: 104, style: 'margin-top:8px' });
    const ctx = tri.getContext('2d'), img = ctx.createImageData(size, 104);
    const A = [size / 2, 4], B = [4, 100], C = [size - 4, 100];
    const area = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1]);
    const total = area(A, B, C);
    for (let y = 0; y < 104; y++) for (let x = 0; x < size; x++) {
      const P = [x, y], wa = area(P, B, C) / total, wb = area(A, P, C) / total, wc = area(A, B, P) / total;
      if (wa < 0 || wb < 0 || wc < 0) continue;
      const m = Math.max(wa, wb, wc), o = (y * size + x) * 4;
      img.data[o] = 255 * wa / m; img.data[o + 1] = 255 * wb / m; img.data[o + 2] = 255 * wc / m; img.data[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    box.append(el('div', { class: 'row', style: 'align-items:flex-end' }, tri,
      el('div', { class: 'hint', text: 'Mixing key: top = R only, left = G only, right = B only; the centre (white) means all three are high together.' })));
  }

  /* ---------- time ---------- */
  const localFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Madrid', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  const utcFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  function renderTime() {
    const t = G.times[st.t], rel = Math.round((t - App.meta.built_at) / 3600);
    $('#t-slider').value = st.t;
    $('#t-local').textContent = localFmt.format(t * 1000) + ' local';
    $('#t-utc').textContent = utcFmt.format(t * 1000) + ' UTC';
    const tag = $('#t-rel');
    tag.textContent = rel <= 0 ? `analysis ${rel} h` : `forecast +${rel} h`;
    tag.classList.toggle('past', rel <= 0);
    $('#t-play').textContent = st.playing ? '❚❚' : '▶';
  }

  /* ---------- probe ---------- */
  function valuesAt(lat, lon, t) {
    const fx = clamp(gridCol(lon), 0, G.nx - 1), fy = clamp(gridRow(lat), 0, G.ny - 1), base = t * G.cell, out = {};
    for (const name of G.names) out[name] = sample(G.fields[name], base, fx, fy);
    return out;
  }
  let probeLL = null;
  function renderProbe() {
    const table = $('#probe-table');
    if (!probeLL) return;
    const { lat, lng } = probeLL;
    if (!bounds.contains(probeLL)) { $('#probe-pos').textContent = 'outside the data area'; table.replaceChildren(); return; }
    const x = clamp(Math.floor(((lng - D.west) / (D.east - D.west)) * W), 0, W - 1);
    const y = clamp(Math.floor(((mN - mercY(lat)) / (mN - mS)) * H), 0, H - 1), k = y * W + x, z = D.z[k];
    $('#probe-pos').textContent = `${fmt(lat, 4)}° N  ${fmt(Math.abs(lng), 4)}° W\nelevation ${z > 0 ? z + ' m' : 'sea'}   slope ${fmt(slopeDeg[k], 1)}°\naspect ${z > 0 ? fmt(aspectDeg[k], 0) + '°' : '–'}   lit ${fmt(clamp((shade[k] - 0.3) / 0.75, 0, 1) * 100, 0)} %`;
    const vals = valuesAt(lat, lng, st.t), active = new Set(channels());
    table.replaceChildren();
    for (const name of G.names) {
      const info = describe(name), s = G.stats[name], v = z <= 0 && info.soil ? NaN : vals[name];
      const frac = clamp((v - s.min) / (s.max - s.min), 0, 1);
      const bar = el('span', { class: 'bar' }, el('i', { style: `width:${ok(frac) ? frac * 100 : 0}%` }));
      table.append(el('tr', { class: active.has(name) ? 'active' : '' },
        el('td', {}, info.label, bar), el('td', { class: 'val', text: `${fmt(v, info.d)} ${info.unit}` })));
    }
  }
  map.on('mousemove', (e) => { probeLL = e.latlng; requestAnimationFrame(renderProbe); });

  /* ---------- pinned point series ---------- */
  let pointPlots = [];
  function renderPoint() {
    const host = $('#point-charts');
    pointPlots.forEach((p) => p.destroy());
    pointPlots = [];
    host.replaceChildren();
    if (!st.pin) return;
    const { lat, lng } = st.pin;
    $('#point-title').textContent = `${fmt(lat, 4)}° N, ${fmt(Math.abs(lng), 4)}° W · forecast and recent analysis, hourly (UTC axis)`;
    const fx = clamp(gridCol(lng), 0, G.nx - 1), fy = clamp(gridRow(lat), 0, G.ny - 1);
    const names = [...new Set([...channels().filter((n) => n !== 'none'), 'precipitation', 'temperature_2m', 'et0_fao_evapotranspiration'])].filter((n) => G.fields[n]);
    const width = Math.max(320, host.clientWidth || 800);
    names.forEach((name, idx) => {
      const info = describe(name), ys = new Array(G.nt);
      for (let t = 0; t < G.nt; t++) { const v = sample(G.fields[name], t * G.cell, fx, fy); ys[t] = ok(v) ? v : null; }
      const card = el('div', { class: 'chart-card' }, el('h3', { text: `${info.label} (${info.unit})` }));
      host.append(card);
      const plot = new uPlot({
        width, height: 110, legend: { show: false },
        cursor: { sync: { key: 'point' }, drag: { x: false, y: false } },
        scales: { x: { time: true } }, tzDate: App.utcDate,
        axes: [App.timeAxis(), App.axis({ size: 50 })],
        series: [{}, { stroke: PALETTE[idx % PALETTE.length], width: 1.5, fill: info.agg === 'sum' ? PALETTE[idx % PALETTE.length] + '33' : undefined, paths: info.agg === 'sum' ? uPlot.paths.stepped({ align: -1 }) : undefined }],
        hooks: { draw: [(u) => {
          const draw = (ts, colour, dash) => {
            const X = u.valToPos(ts, 'x', true);
            if (X < u.bbox.left || X > u.bbox.left + u.bbox.width) return;
            u.ctx.save(); u.ctx.strokeStyle = colour; u.ctx.lineWidth = devicePixelRatio; u.ctx.setLineDash(dash);
            u.ctx.beginPath(); u.ctx.moveTo(X, u.bbox.top); u.ctx.lineTo(X, u.bbox.top + u.bbox.height); u.ctx.stroke(); u.ctx.restore();
          };
          draw(App.meta.built_at, 'rgba(246,173,85,.7)', [4, 4]);
          draw(G.times[st.t], '#fff', []);
        }] },
      }, [G.times, ys], card);
      plot.over.addEventListener('click', () => {
        const ts = plot.posToVal(plot.cursor.left, 'x');
        setTime(clamp(Math.round((ts - G.times[0]) / 3600), 0, G.nt - 1));
      });
      pointPlots.push(plot);
    });
  }
  map.on('click', (e) => {
    if (!bounds.contains(e.latlng)) return;
    st.pin = e.latlng;
    pinMarker.setLatLng(e.latlng).addTo(map);
    renderPoint();
  });

  /* ---------- orchestration ---------- */
  function renderAll() { renderField(); renderVectors(); renderLegend(); renderTime(); renderProbe(); pointPlots.forEach((p) => p.redraw(false)); }
  function setTime(t) { st.t = clamp(t, 0, G.nt - 1); renderAll(); }
  function setPlaying(on) {
    st.playing = on;
    clearInterval(st.timer);
    if (on) st.timer = setInterval(() => setTime((st.t + 1) % G.nt), +$('#t-speed').value);
    renderTime();
  }

  /* ---------- controls ---------- */
  const gridOpts = varOptions(G.names).map((o) => ({ value: o.value, label: o.label }));
  fillSelect($('#map-var'), gridOpts, st.v);
  for (const ch of ['r', 'g', 'b']) fillSelect($('#map-' + ch), [{ value: 'none', label: '— none —' }, ...gridOpts], st[ch]);
  fillSelect($('#map-cmap'), Object.keys(CMAPS).map((k) => ({ value: k, label: k })), st.cmap);
  const bind = (id, key, parse = (v) => v, after) => {
    const node = $(id), out = $(id + '-out');
    const read = () => (node.type === 'checkbox' ? node.checked : parse(node.value));
    node.addEventListener(node.type === 'range' ? 'input' : 'change', () => {
      st[key] = read();
      if (out) out.textContent = typeof st[key] === 'number' ? st[key].toFixed(2) : st[key];
      if (after) after();
      renderAll();
    });
  };
  bind('#map-var', 'v', String, () => {
    const info = describe(st.v);
    st.cmap = info.cmap; st.rev = !!info.rev;
    $('#map-cmap').value = st.cmap; $('#map-cmap-rev').checked = st.rev;
    if (st.pin) renderPoint();
  });
  bind('#map-cmap', 'cmap'); bind('#map-cmap-rev', 'rev');
  for (const ch of ['r', 'g', 'b']) bind('#map-' + ch, ch, String, () => st.pin && renderPoint());
  bind('#map-gamma', 'gamma', Number); bind('#map-range', 'range'); bind('#map-interp', 'interp');
  bind('#map-opacity', 'opacity', Number);
  bind('#ov-contours', 'contours'); bind('#ov-wind', 'wind'); bind('#ov-points', 'points');
  bind('#ov-osm', 'osm', Number, () => osm.setOpacity(st.osm));
  bind('#tr-relief', 'relief'); bind('#tr-light', 'light'); bind('#tr-shadows', 'shadows');
  $$('#map-mode button').forEach((btn) => btn.addEventListener('click', () => {
    st.mode = btn.dataset.mode;
    $$('#map-mode button').forEach((b) => b.classList.toggle('on', b === btn));
    $('#single-controls').hidden = st.mode !== 'single';
    $('#rgb-controls').hidden = st.mode !== 'rgb';
    if (st.pin) renderPoint();
    renderAll();
  }));
  const slider = $('#t-slider');
  slider.max = G.nt - 1;
  slider.addEventListener('input', () => setTime(+slider.value));
  $('#t-prev').addEventListener('click', () => setTime(st.t - 1));
  $('#t-next').addEventListener('click', () => setTime(st.t + 1));
  $('#t-play').addEventListener('click', () => setPlaying(!st.playing));
  $('#t-speed').addEventListener('change', () => setPlaying(st.playing));
  $('#t-now').addEventListener('click', () => setTime(G.times.findIndex((t) => t >= Date.now() / 1000 - 1800)));
  $('#map-cmap-rev').checked = st.rev;

  App.hooks.mapShown = () => { map.invalidateSize(); if (st.pin) renderPoint(); };
  st.pin = L.latLng(site.lat, site.lon);
  pinMarker.addTo(map);
  renderAll();
  renderPoint();
};

/* Shared dark axis style for uPlot. */
App.axis = (extra = {}) => ({
  stroke: '#7d8da0', font: '10px ui-monospace, Menlo, monospace',
  grid: { stroke: 'rgba(255,255,255,0.06)', width: 1 }, ticks: { stroke: 'rgba(255,255,255,0.12)', width: 1, size: 4 },
  ...extra,
});

/* Time axis in UTC with 24-hour labels that adapt to the visible span. */
App.utcDate = (ts) => uPlot.tzDate(new Date(ts * 1000), 'Etc/UTC');
App.timeAxis = () => App.axis({
  values: (u, splits) => {
    const span = u.scales.x.max - u.scales.x.min;
    const f = d3.utcFormat(span > 500 * 86400 ? '%b %Y' : span > 45 * 86400 ? '%d %b %y' : span > 3 * 86400 ? '%a %d %b' : '%d %b %H:%M');
    return splits.map((t) => f(new Date(t * 1000)));
  },
  space: 90,
});

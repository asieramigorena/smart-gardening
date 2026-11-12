'use strict';
/* Map tab: terrain-shaded composite of forecast fields (single variable or RGB channel mixing),
   OpenStreetMap green spaces with per-area statistics, a map key and a value histogram. */
App.initMap = function initMap() {
  const G = App.grid, D = App.dem, site = App.meta.site;
  const W = D.width, H = D.height, N = W * H;
  const st = App.state.map = {
    mode: 'single', v: 'precipitation', cmap: 'YlGnBu', rev: false,
    r: 'vapour_pressure_deficit', g: 'soil_moisture_9_to_27cm', b: 'precipitation', gamma: 0.8,
    range: 'robust', interp: 'bilinear', opacity: 0.85,
    contours: true, wind: false, points: false, values: false, osm: 0.45,
    relief: true, light: 'sun', shadows: true,
    parks: { protected: true, park: true, golf: true, garden: true, allotments: true }, parkNames: true, parkFill: true,
    t: 0, playing: false, timer: null, pin: null,
  };
  const now = Date.now() / 1000;
  st.t = Math.max(0, G.times.findIndex((t) => t >= now - 1800));

  /* ---------- geometry: DEM pixel <-> lon/lat <-> grid index ---------- */
  const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const mN = mercY(D.north), mS = mercY(D.south);
  const latOfRow = (y) => (Math.atan(Math.sinh(mN - (y / H) * (mN - mS))) * 180) / Math.PI;
  const lonOfCol = (x) => D.west + (x / W) * (D.east - D.west);
  const gridCol = (lon) => ((lon - G.lons[0]) / (G.lons[G.nx - 1] - G.lons[0])) * (G.nx - 1);
  const gridRow = (lat) => ((G.lats[0] - lat) / (G.lats[0] - G.lats[G.ny - 1])) * (G.ny - 1);
  const demIndex = (lat, lon) => clamp(Math.floor(((mN - mercY(lat)) / (mN - mS)) * H), 0, H - 1) * W + clamp(Math.floor(((lon - D.west) / (D.east - D.west)) * W), 0, W - 1);
  const colF = new Float32Array(W), rowF = new Float32Array(H);
  for (let x = 0; x < W; x++) colF[x] = clamp(gridCol(lonOfCol(x + 0.5)), 0, G.nx - 1);
  for (let y = 0; y < H; y++) rowF[y] = clamp(gridRow(latOfRow(y + 0.5)), 0, G.ny - 1);
  const landCell = new Uint8Array(G.cell);   // grid points that sit on land
  for (let j = 0; j < G.ny; j++) for (let i = 0; i < G.nx; i++) landCell[j * G.nx + i] = D.z[demIndex(G.lats[j], G.lons[i])] > 0 ? 1 : 0;

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
    aspectDeg[k] = ((Math.atan2(-dzdx, -dzdy) * 180) / Math.PI + 360) % 360; // direction the slope faces, from north
  }
  const shade = new Float32Array(N).fill(0.7);
  let shadeKey = '';
  function updateShade() {
    const sun = sunPosition(G.times[st.t], site.lat, site.lon);
    const useSun = st.light === 'sun';
    const key = [useSun ? st.t : 'nw', st.shadows, st.relief].join('|');
    $('#sun-readout').textContent = `sun azimuth  ${fmt((sun.azimuth * 180) / Math.PI, 0).padStart(4)}° (${compass((sun.azimuth * 180) / Math.PI)})\nsun altitude ${fmt((sun.altitude * 180) / Math.PI, 1).padStart(5)}°` + (sun.altitude <= 0 ? '  (night)' : '');
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
  const map = L.map('map', { zoomSnap: 0.25, zoomDelta: 0.5, minZoom: 9, maxZoom: 16, maxBounds: bounds.pad(0.4), attributionControl: true });
  map.fitBounds(bounds);
  map.attributionControl.setPrefix('').addAttribution('Weather: Open-Meteo · Terrain: Mapzen/AWS Terrarium · Streets and green spaces: © OpenStreetMap contributors');
  L.control.scale({ metric: true, imperial: false, position: 'bottomright' }).addTo(map);
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
  map.createPane('parks').style.zIndex = 360;
  map.createPane('vector').style.zIndex = 380;
  map.createPane('poi').style.zIndex = 620;
  map.getPane('labels').style.pointerEvents = 'none';
  map.getPane('vector').style.pointerEvents = 'none';
  const fieldCanvas = el('canvas', { width: W, height: H });
  const fctx = fieldCanvas.getContext('2d');
  const image = fctx.createImageData(W, H);
  new CanvasOverlay(fieldCanvas, bounds, { pane: 'field', className: 'field-canvas', interactive: false }).addTo(map);
  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { pane: 'labels', className: 'osm-labels', opacity: st.osm, maxZoom: 19 }).addTo(map);
  // isolines, arrows and numbers are drawn at screen resolution and redrawn after each pan or zoom,
  // so text stays crisp and the same size at every zoom level
  const vecCanvas = el('canvas', { class: 'vec-canvas' });
  map.getPane('vector').appendChild(vecCanvas);
  const vctx = vecCanvas.getContext('2d');
  let VW = 0, VH = 0;
  function placeVectorCanvas() {
    const size = map.getSize(), dpr = window.devicePixelRatio || 1;
    VW = size.x; VH = size.y;
    vecCanvas.width = Math.round(VW * dpr); vecCanvas.height = Math.round(VH * dpr);
    vecCanvas.style.width = VW + 'px'; vecCanvas.style.height = VH + 'px';
    L.DomUtil.setPosition(vecCanvas, map.containerPointToLayerPoint([0, 0]));
    vctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  const P = (lat, lon) => map.latLngToContainerPoint([lat, lon]);

  const poiIcon = (icon, colour, size = 22) => L.divIcon({
    className: 'poi-wrap', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    html: `<div class="poi" style="--c:${colour};width:${size}px;height:${size}px">${iconSVG(icon, Math.round(size * 0.62))}</div>`,
  });
  const labelled = (marker, text, cls = 'poi-label') => marker.bindTooltip(el('span', { text }), { permanent: true, direction: 'right', offset: [12, 0], className: cls });
  labelled(L.marker([site.lat, site.lon], { icon: poiIcon('drop', '#4fd1c5', 26), pane: 'poi', keyboard: false }).addTo(map), `${site.name} · garden site`);
  labelled(L.marker([43.302, -2.911], { icon: poiIcon('station', '#f6ad55', 22), pane: 'poi', keyboard: false }).addTo(map), 'AEMET 1082 · Bilbao airport');
  const pinMarker = L.marker([site.lat, site.lon], { icon: poiIcon('pin', '#ffffff', 26), pane: 'poi', interactive: false, keyboard: false });
  const legendControl = L.control({ position: 'bottomleft' });
  legendControl.onAdd = () => {
    const box = el('div', { class: 'map-legend', id: 'map-legend' });
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    return box;
  };
  legendControl.addTo(map);

  /* ---------- ranges and sampling ---------- */
  function rangeOf(name) {
    const s = G.stats[name];
    if (st.range === 'global') return [s.min, s.max];
    if (st.range === 'frame') {
      const f = G.fields[name], base = st.t * G.cell;
      let lo = Infinity, hi = -Infinity;
      for (let c = 0; c < G.cell; c++) { const v = f[base + c]; if (ok(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
      if (describe(name).diverging) { const m = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m; hi = m; }
      return hi > lo ? [lo, hi] : [ok(lo) ? lo : 0, (ok(lo) ? lo : 0) + 1];
    }
    return [s.p2, s.p98];
  }
  function sample(f, base, fx, fy) {
    const i0 = fx | 0, j0 = fy | 0, i1 = i0 < G.nx - 1 ? i0 + 1 : i0, j1 = j0 < G.ny - 1 ? j0 + 1 : j0, wx = fx - i0, wy = fy - j0;
    const a = f[base + j0 * G.nx + i0], b = f[base + j0 * G.nx + i1], c = f[base + j1 * G.nx + i0], d = f[base + j1 * G.nx + i1];
    return (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy;
  }
  /* A sampler returns a field's value at hour t for a point or for the area mean of a green space. */
  const pointSampler = (ll) => {
    const fx = clamp(gridCol(ll.lng), 0, G.nx - 1), fy = clamp(gridRow(ll.lat), 0, G.ny - 1), sea = D.z[demIndex(ll.lat, ll.lng)] <= 0;
    return (name, t) => (sea && describe(name).soil ? NaN : sample(G.fields[name], t * G.cell, fx, fy));
  };
  const areaSampler = (props) => (name, t) => {
    const f = G.fields[name], base = t * G.cell;
    let s = 0, w = 0;
    for (const [c, wt] of props.w) { const v = f[base + c]; if (ok(v)) { s += v * wt; w += wt; } }
    return w ? s / w : NaN;
  };
  const sumAhead = (sampler, name, t, hours) => {
    let s = 0, n = 0;
    for (let h = 1; h <= hours && t + h < G.nt; h++) { const v = sampler(name, t + h); if (ok(v)) { s += v; n++; } }
    return { s, n };
  };

  /* ---------- raster composite ---------- */
  const channels = () => (st.mode === 'single' ? [st.v] : [st.r, st.g, st.b]);
  function renderField() {
    updateShade();
    const names = channels(), base = st.t * G.cell, nearest = st.interp === 'nearest';
    const fs = names.map((n) => (n === 'none' ? null : G.fields[n]));
    const rg = names.map((n) => (n === 'none' ? [0, 1] : rangeOf(n)));
    const soil = names.map((n) => n !== 'none' && !!describe(n).soil);
    const table = st.mode === 'single' ? lut(st.cmap, st.rev) : null;
    const fade = st.mode === 'single' && !!describe(st.v).zero && /precipitation|rain_next/.test(st.v);
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
        let r = 0, g = 0, b = 0, has = true;
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
          if (!fs.some((f, c) => f && ok(vals[c]))) has = false;
        }
        const br = sea ? 34 : 150, bg = sea ? 48 : 150, bb = sea ? 66 : 146;
        const a = has ? (fade ? op * clamp(vals[0] / 0.06, 0, 1) : op) : 0, s = sea ? (st.relief ? 0.9 : 1) : shade[k] * (st.relief ? 1.25 : 1.35);
        const o = k * 4;
        data[o] = (br + (r - br) * a) * s; data[o + 1] = (bg + (g - bg) * a) * s; data[o + 2] = (bb + (b - bb) * a) * s; data[o + 3] = 255;
      }
    }
    fctx.putImageData(image, 0, 0);
  }

  /* ---------- vector overlays (screen space) ---------- */
  function halo(text, x, y, colour = '#fff') {
    vctx.lineWidth = 3; vctx.strokeStyle = 'rgba(0,0,0,.8)'; vctx.strokeText(text, x, y);
    vctx.fillStyle = colour; vctx.fillText(text, x, y);
  }
  function renderVectors() {
    placeVectorCanvas();
    vctx.clearRect(0, 0, VW, VH);
    const base = st.t * G.cell;
    const lead = st.mode === 'single' ? st.v : st.r;
    const info = lead !== 'none' ? describe(lead) : null;
    const inView = (pt, pad = 0) => pt.x > -pad && pt.y > -pad && pt.x < VW + pad && pt.y < VH + pad;
    if (st.contours && info) {
      const f = G.fields[lead], UP = 6, cw = (G.nx - 1) * UP + 1, chh = (G.ny - 1) * UP + 1;
      const up = new Float64Array(cw * chh);
      for (let j = 0; j < chh; j++) for (let i = 0; i < cw; i++) up[j * cw + i] = sample(f, base, i / UP, j / UP);
      if (up.some(ok)) {
        const [lo, hi] = rangeOf(lead);
        const levels = d3.ticks(lo, hi, 6).filter((v) => v > lo && v < hi);
        const placed = [];
        const lonAt = (cx) => G.lons[0] + ((cx - 0.5) / (cw - 1)) * (G.lons[G.nx - 1] - G.lons[0]);
        const latAt = (cy) => G.lats[0] - ((cy - 0.5) / (chh - 1)) * (G.lats[0] - G.lats[G.ny - 1]);
        vctx.font = '600 11px ui-monospace, Menlo, monospace'; vctx.textAlign = 'center'; vctx.textBaseline = 'middle';
        for (const c of d3.contours().size([cw, chh]).thresholds(levels)(Array.from(up, (v) => (ok(v) ? v : -1e9)))) {
          vctx.beginPath();
          const labels = [];
          for (const poly of c.coordinates) for (const ring of poly) {
            if (ring.length < 8) continue;
            const pts = ring.map(([cx, cy]) => P(latAt(cy), lonAt(cx)));
            pts.forEach((pt, i) => (i ? vctx.lineTo(pt.x, pt.y) : vctx.moveTo(pt.x, pt.y)));
            for (let k = 0; k < pts.length; k += 45) if (inView(pts[k], -30)) labels.push(pts[k]);
          }
          vctx.lineWidth = 3; vctx.strokeStyle = 'rgba(0,0,0,.45)'; vctx.stroke();
          vctx.lineWidth = 1.2; vctx.strokeStyle = 'rgba(255,255,255,.85)'; vctx.stroke();
          let n = 0;
          for (const pt of labels) {
            if (n >= 3 || placed.some((q) => Math.hypot(q.x - pt.x, q.y - pt.y) < 130)) continue;
            placed.push(pt); n++;
            halo(fmt(c.value, Math.min(info.d, 2)), pt.x, pt.y);
          }
        }
      }
    }
    const gp = [];   // screen position of every grid point
    for (let j = 0; j < G.ny; j++) for (let i = 0; i < G.nx; i++) gp.push(P(G.lats[j], G.lons[i]));
    const spacing = Math.hypot(gp[1].x - gp[0].x, gp[1].y - gp[0].y);
    if (st.wind && G.fields.wind_speed_10m && G.fields.wind_direction_10m) {
      const sp = G.fields.wind_speed_10m, di = G.fields.wind_direction_10m, top = G.stats.wind_speed_10m.p98 || 10;
      const step = spacing < 34 ? 2 : 1, maxLen = Math.min(34, spacing * step * 0.9);
      for (let j = 0; j < G.ny; j += step) for (let i = step > 1 ? j % 2 : 0; i < G.nx; i += step) {
        const c = j * G.nx + i, v = sp[base + c], d = di[base + c], pt = gp[c];
        if (!ok(v) || !ok(d) || !inView(pt, 40)) continue;
        const len = 8 + (maxLen - 8) * clamp(v / top, 0, 1), a = ((d + 180) * Math.PI) / 180; // blowing towards
        const ux = Math.sin(a), uy = -Math.cos(a), x2 = pt.x + ux * len * 0.6, y2 = pt.y + uy * len * 0.6, x1 = pt.x - ux * len * 0.4, y1 = pt.y - uy * len * 0.4;
        for (const [w, col] of [[3.6, 'rgba(0,0,0,.6)'], [1.6, '#fff']]) {
          vctx.lineWidth = w; vctx.strokeStyle = col; vctx.beginPath();
          vctx.moveTo(x1, y1); vctx.lineTo(x2, y2);
          vctx.moveTo(x2 - ux * 6 - uy * 4, y2 - uy * 6 + ux * 4); vctx.lineTo(x2, y2); vctx.lineTo(x2 - ux * 6 + uy * 4, y2 - uy * 6 - ux * 4);
          vctx.stroke();
        }
      }
    }
    if (st.points) {
      vctx.fillStyle = 'rgba(255,255,255,.9)'; vctx.strokeStyle = 'rgba(0,0,0,.6)'; vctx.lineWidth = 1;
      for (const pt of gp) if (inView(pt, 4)) { vctx.beginPath(); vctx.arc(pt.x, pt.y, 2.2, 0, 6.3); vctx.fill(); vctx.stroke(); }
    }
    if (st.values && info) {
      const f = G.fields[lead], step = spacing < 30 ? Math.ceil(30 / spacing) : 1;
      vctx.font = '600 10.5px ui-monospace, Menlo, monospace'; vctx.textAlign = 'center'; vctx.textBaseline = st.points ? 'bottom' : 'middle';
      for (let j = 0; j < G.ny; j += step) for (let i = 0; i < G.nx; i += step) {
        const c = j * G.nx + i, v = f[base + c], pt = gp[c];
        if (!ok(v) || (info.soil && !landCell[c]) || !inView(pt, 20)) continue;
        halo(fmt(v, Math.min(info.d, 2)), pt.x, pt.y - (st.points ? 4 : 0));
      }
    }
  }
  map.on('zoomstart', () => { vecCanvas.style.visibility = 'hidden'; });
  map.on('moveend zoomend resize', () => { vecCanvas.style.visibility = ''; renderVectors(); });

  /* ---------- green spaces ---------- */
  const parks = App.parks ? App.parks.features : [];
  const parkLabels = App.parks ? App.parks.categories : {};
  const parkCounts = d3.rollup(parks, (v) => v.length, (f) => f.properties.cat);
  const parkRenderer = L.canvas({ pane: 'parks', padding: 0.3, tolerance: 3 });
  const parkLayers = {};
  const markerLayer = L.layerGroup().addTo(map);
  let openPark = null;
  const parkTitle = (p) => p.name || `Unnamed ${String(parkLabels[p.cat] || p.cat).toLowerCase()}`;
  const fmtArea = (ha) => (ha >= 100 ? `${f0(ha)} ha` : ha >= 1 ? `${fmt(ha, 1)} ha` : `${f0(ha * 1e4)} m²`);
  const f0 = (v) => (ok(v) ? Math.round(v).toLocaleString('en-GB') : '–');

  function parkTooltip(layer) {
    const p = layer.feature.properties, sampler = areaSampler(p), lead = channels().find((n) => n !== 'none');
    const box = el('div', { class: 'park-tip' },
      el('div', { class: 'park-tip-head' }, iconEl(PARK_STYLE[p.cat].icon, 14, PARK_STYLE[p.cat].colour), el('b', { text: parkTitle(p) })),
      el('div', { class: 'muted', text: `${parkLabels[p.cat]} · ${fmtArea(p.area_ha)} · ${p.elev[1].toFixed(0)} m` }));
    if (lead) { const info = describe(lead); box.append(el('div', { text: `${info.label}: ${fmt(sampler(lead, st.t), info.d)} ${info.unit} (area mean)` })); }
    box.append(el('div', { class: 'muted', text: 'click for details' }));
    return box;
  }

  function parkPopup(feature) {
    const p = feature.properties, sampler = areaSampler(p), t = st.t, style = PARK_STYLE[p.cat];
    const row = (label, value, cls = '') => el('tr', { class: cls }, el('td', { text: label }), el('td', { class: 'val', text: value }));
    const val = (name) => { const info = describe(name); return `${fmt(sampler(name, t), info.d)} ${info.unit}`; };
    const box = el('div', { class: 'park-pop' });
    box.append(el('div', { class: 'park-pop-head' }, iconEl(style.icon, 20, style.colour),
      el('div', {}, el('div', { class: 'park-pop-title', text: parkTitle(p) }),
        el('div', { class: 'muted' }, `${parkLabels[p.cat]} · `, el('a', { href: `https://www.openstreetmap.org/${p.osm}`, target: '_blank', rel: 'noopener', text: `OSM ${p.osm}` })))));

    const terrain = el('table', { class: 'pop-table' });
    terrain.append(row('Area', fmtArea(p.area_ha)), row('Elevation min / mean / max', `${p.elev[0]} / ${p.elev[1].toFixed(0)} / ${p.elev[2]} m`),
      row('Mean slope', `${fmt(p.slope, 1)}°`), row('Faces', p.slope >= 2 ? `${compass(p.aspect)} (${p.aspect}°)` : 'flat'),
      row('Forecast grid cells used', String(p.w.length)));
    box.append(el('h4', { text: 'Terrain' }), terrain);

    const when = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Madrid', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(G.times[t] * 1000);
    const nowT = el('table', { class: 'pop-table' });
    for (const name of ['temperature_2m', 'relative_humidity_2m', 'vapour_pressure_deficit', 'cloud_cover', 'wind_speed_10m', 'soil_moisture_3_to_9cm', 'soil_moisture_9_to_27cm', 'soil_moisture_27_to_81cm'])
      if (G.fields[name]) nowT.append(row(describe(name).label, val(name)));
    box.append(el('h4', { text: `At ${when} (local), area mean` }), nowT);

    const wb = el('table', { class: 'pop-table wb' });
    wb.append(el('tr', {}, el('th', { text: '' }), el('th', { text: 'next 24 h' }), el('th', { text: 'next 72 h' })));
    const r24 = sumAhead(sampler, 'precipitation', t, 24), r72 = sumAhead(sampler, 'precipitation', t, 72);
    const e24 = sumAhead(sampler, 'et0_fao_evapotranspiration', t, 24), e72 = sumAhead(sampler, 'et0_fao_evapotranspiration', t, 72);
    wb.append(el('tr', {}, el('td', { text: 'Rain' }), el('td', { class: 'val', text: `${fmt(r24.s, 1)} mm` }), el('td', { class: 'val', text: `${fmt(r72.s, 1)} mm` })));
    wb.append(el('tr', {}, el('td', { text: 'ET₀ (demand)' }), el('td', { class: 'val', text: `${fmt(e24.s, 1)} mm` }), el('td', { class: 'val', text: `${fmt(e72.s, 1)} mm` })));
    wb.append(el('tr', {}, el('td', { text: 'Rain − ET₀' }), el('td', { class: 'val', text: `${fmt(r24.s - e24.s, 1)} mm` }), el('td', { class: 'val', text: `${fmt(r72.s - e72.s, 1)} mm` })));
    box.append(el('h4', { text: 'Water balance from this hour' }), wb);
    if (r72.n < 72) box.append(el('div', { class: 'hint', text: `Only ${r72.n} forecast hours remain after this time.` }));
    const need = e72.s - r72.s, litres = need * p.area_ha * 1e4;
    box.append(el('div', { class: 'verdict ' + (need > 0 ? 'need' : 'ok') },
      iconEl(need > 0 ? 'drop' : 'sea', 16),
      el('span', { text: need > 0
        ? `Demand exceeds rain by ${fmt(need, 1)} mm over 3 days, about ${f0(litres)} L over the whole area (${fmt(need, 1)} L per m²).`
        : `Forecast rain covers the demand over the next 3 days (surplus ${fmt(-need, 1)} mm).` })));
    box.append(el('div', { class: 'hint', text: 'ET₀ is the demand of a well-watered short lawn; multiply by your crop coefficient (handbook §3) for a specific plant.' }));
    const btn = el('button', { class: 'pop-btn', type: 'button' }, iconEl('pin', 14), ' Plot area-mean series below');
    btn.addEventListener('click', () => { pinPark(feature); });
    box.append(btn);
    return box;
  }
  const popup = L.popup({ maxWidth: 360, minWidth: 300, className: 'park-popup', autoPanPaddingTopLeft: [10, 70] });
  map.on('popupclose', () => { openPark = null; });
  function openParkPopup(feature) {
    openPark = feature;
    const [lon, lat] = feature.properties.label;
    popup.setLatLng([lat, lon]).setContent(parkPopup(feature)).openOn(map);
  }

  if (parks.length) {
    const byCat = d3.group(parks, (f) => f.properties.cat);
    for (const cat of Object.keys(PARK_STYLE).sort((a, b) => PARK_STYLE[a].order - PARK_STYLE[b].order)) {
      const s = PARK_STYLE[cat], features = byCat.get(cat) || [];
      const interactive = cat !== 'protected';   // large reserves would swallow every click; they open from their icon
      parkLayers[cat] = L.geoJSON({ type: 'FeatureCollection', features }, {
        pane: 'parks', renderer: parkRenderer, interactive,
        style: () => ({ color: s.colour, weight: cat === 'protected' ? 1.6 : 1.3, opacity: 0.95, dashArray: s.dash || null, fill: true, fillColor: s.colour, fillOpacity: st.parkFill ? s.fill : 0 }),
        onEachFeature: (feature, layer) => {
          if (!interactive) return;
          layer.bindTooltip(parkTooltip, { sticky: true, direction: 'top', offset: [0, -8], className: 'park-tooltip' });
          layer.on('mouseover', () => layer.setStyle({ weight: 3, fillOpacity: Math.max(0.3, s.fill) }));
          layer.on('mouseout', () => parkLayers[cat].resetStyle(layer));
          layer.on('click', (e) => { e.originalEvent._parkClick = true; openParkPopup(feature); });
        },
      });
      if (st.parks[cat]) parkLayers[cat].addTo(map);
    }
  }

  /* icons and names, decluttered by zoom and size */
  function refreshParkMarkers() {
    markerLayer.clearLayers();
    if (!parks.length) return;
    const z = map.getZoom(), view = map.getBounds().pad(0.1);
    const minHa = z < 10.5 ? 200 : z < 11.5 ? 15 : z < 12.5 ? 3 : z < 13.5 ? 0.8 : z < 14.5 ? 0.25 : 0.05;
    const candidates = parks.filter((f) => {
      const p = f.properties;
      if (!st.parks[p.cat]) return false;
      if (!view.contains([p.label[1], p.label[0]])) return false;
      if (p.cat === 'protected') return p.area_ha >= 20 || z >= 12;
      if (!p.name && p.cat === 'garden') return z >= 15 && p.area_ha >= 0.1;   // mostly private gardens
      return p.area_ha >= minHa;
    }).sort((a, b) => b.properties.area_ha - a.properties.area_ha).slice(0, 260);
    const icons = [], labels = [];
    for (const [lat, lon, chars] of [[site.lat, site.lon, 26], [43.302, -2.911, 27]]) {
      const pt = map.latLngToContainerPoint([lat, lon]);
      icons.push({ x0: pt.x - 14, x1: pt.x + 14, y0: pt.y - 14, y1: pt.y + 14 });
      labels.push({ x0: pt.x + 12, x1: pt.x + 20 + chars * 6.6, y0: pt.y - 10, y1: pt.y + 10 });
    }
    const hit = (r, list) => list.some((q) => r.x0 < q.x1 && r.x1 > q.x0 && r.y0 < q.y1 && r.y1 > q.y0);
    for (const f of candidates) {
      const p = f.properties, s = PARK_STYLE[p.cat], pt = map.latLngToContainerPoint([p.label[1], p.label[0]]);
      const size = p.cat === 'protected' ? 24 : 20, iconBox = { x0: pt.x - 12, x1: pt.x + 12, y0: pt.y - 12, y1: pt.y + 12 };
      if (hit(iconBox, icons)) continue;
      icons.push(iconBox);
      const marker = L.marker([p.label[1], p.label[0]], { icon: poiIcon(s.icon, s.colour, size), pane: 'poi', keyboard: false, riseOnHover: true });
      marker.on('click', (e) => { e.originalEvent._parkClick = true; openParkPopup(f); });
      let named = false;
      if (st.parkNames && p.name && (z >= 13.25 || p.area_ha >= (z >= 12 ? 8 : 60) || p.cat === 'protected')) {
        const text = p.name.length > 30 ? p.name.slice(0, 29).trimEnd() + '…' : p.name;
        const w = text.length * 6.3 + 14, labelBox = { x0: pt.x + 10, x1: pt.x + 10 + w, y0: pt.y - 9, y1: pt.y + 9 };
        if (!hit(labelBox, labels) && !hit(labelBox, icons.slice(0, -1))) {
          labels.push(labelBox);
          marker.bindTooltip(el('span', { text }), { permanent: true, direction: 'right', offset: [10, 0], className: `park-label park-label-${p.cat}` });
          named = true;
        }
      }
      if (!named) marker.bindTooltip(() => parkTooltip({ feature: f }), { direction: 'top', offset: [0, -10], className: 'park-tooltip' });
      markerLayer.addLayer(marker);
    }
  }
  map.on('zoomend moveend', refreshParkMarkers);

  /* ---------- legend: colour scale, histogram over land, pointer / pin markers ---------- */
  let legendScales = [];
  function scaleBlock(title, table, lo, hi, info, name, channelColour) {
    const wrap = el('div', { class: 'legend-scale' });
    const head = el('div', { class: 'legend-title' }, channelColour ? el('span', { class: 'chip', style: `background:${channelColour}` }) : null, title);
    const barBox = el('div', { class: 'legend-barbox' });
    const canvas = el('canvas', { class: 'legend-bar' });
    paintBar(canvas, table);
    const hover = el('span', { class: 'legend-mark hover', hidden: '' }), pin = el('span', { class: 'legend-mark pin', hidden: '' });
    barBox.append(canvas, hover, pin);
    const ticks = el('div', { class: 'legend-ticks' });
    for (let i = 0; i <= 4; i++) ticks.append(el('span', { text: fmt(lo + (hi - lo) * i / 4, info.d) }));
    wrap.append(head, barBox, ticks);
    const place = (mark, v) => { if (!ok(v)) { mark.hidden = true; return; } mark.hidden = false; mark.style.left = `${clamp((v - lo) / (hi - lo), 0, 1) * 100}%`; mark.title = fmt(v, info.d); };
    legendScales.push({ name, hover: (v) => place(hover, v), pin: (v) => place(pin, v) });
    return wrap;
  }
  function landValues(name) {
    const f = G.fields[name], base = st.t * G.cell, out = [];
    for (let c = 0; c < G.cell; c++) if (landCell[c] && ok(f[base + c])) out.push(f[base + c]);
    return out;
  }
  function histogram(values, lo, hi, table) {
    const bins = 36, counts = new Array(bins).fill(0);
    for (const v of values) counts[clamp(Math.floor(((v - lo) / (hi - lo)) * bins), 0, bins - 1)]++;
    const max = Math.max(1, ...counts), Wc = 250, Hc = 38;
    const canvas = el('canvas', { class: 'legend-hist', width: Wc * 2, height: Hc * 2 });
    const ctx = canvas.getContext('2d');
    ctx.scale(2, 2);
    const bw = Wc / bins;
    counts.forEach((c, i) => {
      if (!c) return;
      const k = Math.round(((i + 0.5) / bins) * 255), h = Math.max(1.5, (c / max) * (Hc - 2));
      ctx.fillStyle = table ? `rgb(${table[k * 3]},${table[k * 3 + 1]},${table[k * 3 + 2]})` : '#7d8da0';
      ctx.fillRect(i * bw + 0.5, Hc - h, bw - 1, h);
    });
    return canvas;
  }
  function statsLine(name, values) {
    const info = describe(name);
    if (!values.length) return el('div', { class: 'legend-stats', text: 'no values at this hour' });
    const mean = d3.mean(values), sd = d3.deviation(values) || 0;
    let extra = '';
    if (/precipitation$|rain_next/.test(name)) extra = ` · wet area ${fmt((100 * values.filter((v) => v >= 0.1).length) / values.length, 0)} %`;
    if (name === 'need_72h') extra = ` · needs water ${fmt((100 * values.filter((v) => v > 0).length) / values.length, 0)} %`;
    return el('div', { class: 'legend-stats', text: `land, this hour: min ${fmt(d3.min(values), info.d)} · mean ${fmt(mean, info.d)} · max ${fmt(d3.max(values), info.d)} · σ ${fmt(sd, info.d)} ${info.unit}${extra}` });
  }
  const RANGE_TEXT = { robust: 'colours span the 2–98 % range of the whole 9-day window', global: 'colours span the full min–max of the 9-day window', frame: 'colours span this hour only' };
  function renderLegend() {
    const box = $('#map-legend');
    box.replaceChildren();
    legendScales = [];
    if (st.mode === 'single') {
      const info = describe(st.v), [lo, hi] = rangeOf(st.v), table = lut(st.cmap, st.rev), vals = landValues(st.v);
      box.append(scaleBlock(`${info.label} (${info.unit})`, table, lo, hi, info, st.v));
      box.append(histogram(vals, lo, hi, table), statsLine(st.v, vals));
    } else {
      const colours = ['#ff5a5a', '#4ade80', '#60a5fa'];
      [['r', 0], ['g', 1], ['b', 2]].forEach(([ch, idx]) => {
        const name = st[ch];
        if (name === 'none') return;
        const info = describe(name), [lo, hi] = rangeOf(name), table = new Uint8Array(256 * 3);
        for (let i = 0; i < 256; i++) table[i * 3 + idx] = Math.round(255 * Math.pow(i / 255, st.gamma));
        box.append(scaleBlock(`${info.label} (${info.unit})`, table, lo, hi, info, name, colours[idx]), statsLine(name, landValues(name)));
      });
      const size = 92, tri = el('canvas', { width: size, height: 82 });
      const ctx = tri.getContext('2d'), img = ctx.createImageData(size, 82);
      const A = [size / 2, 3], B = [3, 79], C = [size - 3, 79];
      const area = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1]);
      const total = area(A, B, C);
      for (let y = 0; y < 82; y++) for (let x = 0; x < size; x++) {
        const P = [x, y], wa = area(P, B, C) / total, wb = area(A, P, C) / total, wc = area(A, B, P) / total;
        if (wa < 0 || wb < 0 || wc < 0) continue;
        const m = Math.max(wa, wb, wc), o = (y * size + x) * 4;
        img.data[o] = 255 * wa / m; img.data[o + 1] = 255 * wb / m; img.data[o + 2] = 255 * wc / m; img.data[o + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      box.append(el('div', { class: 'row', style: 'align-items:center;margin-top:6px' }, tri,
        el('div', { class: 'hint', text: 'Top = only R high, left = only G, right = only B. White = all three high; dark = all low.' })));
    }
    box.append(el('div', { class: 'legend-foot' },
      el('span', { class: 'legend-mark-key hover' }), 'pointer ',
      el('span', { class: 'legend-mark-key pin' }), 'pinned · ', RANGE_TEXT[st.range]));
    markPin();
  }
  function markPin() {
    if (!st.pin) return;
    for (const s of legendScales) s.pin(st.pin.sampler(s.name, st.t));
  }

  /* ---------- map key ---------- */
  const keyControl = L.control({ position: 'topright' });
  keyControl.onAdd = () => {
    const box = el('details', { class: 'map-key', open: '' });
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    return box;
  };
  keyControl.addTo(map);
  function keyRow(icon, colour, label, note, active = true) {
    return el('div', { class: 'key-row' + (active ? '' : ' off') }, iconEl(icon, 16, colour), el('div', {}, el('div', { text: label }), note ? el('div', { class: 'muted', text: note }) : null));
  }
  function keyArea(cat) {
    const s = PARK_STYLE[cat];
    const swatch = el('span', { class: 'key-swatch', style: `border-color:${s.colour};background:${s.colour}33;border-style:${s.dash ? 'dashed' : 'solid'}` });
    return el('div', { class: 'key-row' + (st.parks[cat] ? '' : ' off') }, swatch, iconEl(s.icon, 15, s.colour),
      el('div', {}, el('div', { text: parkLabels[cat] || cat }), el('div', { class: 'muted', text: `${(parkCounts.get(cat) || 0).toLocaleString('en-GB')} areas` })));
  }
  function renderKey() {
    const box = $('.map-key');
    if (!box) return;
    const open = box.open;
    box.replaceChildren(el('summary', {}, iconEl('layers', 14), ' Map key'));
    box.open = open;
    const lead = channels().find((n) => n !== 'none');
    box.append(el('div', { class: 'key-h', text: 'Places' }),
      keyRow('drop', '#4fd1c5', `${site.name} · garden site`, 'where the irrigation system will run'),
      keyRow('station', '#f6ad55', 'AEMET weather station', 'official observations, Bilbao airport'),
      keyRow('pin', '#ffffff', st.pin && st.pin.kind === 'park' ? 'Pinned area' : 'Pinned point', 'click the map or a green space to move it'));
    if (parks.length) {
      box.append(el('div', { class: 'key-h', text: 'Green spaces (OpenStreetMap)' }));
      for (const cat of ['allotments', 'garden', 'park', 'golf', 'protected']) box.append(keyArea(cat));
    }
    box.append(el('div', { class: 'key-h', text: 'Overlays' }),
      keyRow('isoline', '#ffffff', 'Isolines', lead ? `equal ${describe(lead).label.toLowerCase()}, labelled in ${describe(lead).unit}` : '', st.contours),
      keyRow('wind', '#ffffff', 'Wind arrows', 'point downwind; longer = stronger', st.wind),
      keyRow('grid', '#ffffff', 'Forecast grid points', 'model values every ~2 km', st.points),
      keyRow('values', '#ffffff', 'Grid values', 'colour-layer value at each grid point', st.values),
      el('div', { class: 'key-h', text: 'Ground' }),
      keyRow('relief', '#c9c9c0', 'Shaded relief', st.light === 'sun' ? 'lit by the sun at the selected hour' : 'lit from the north-west', st.relief),
      keyRow('shadow', '#8a8a82', 'Cast shadows', 'terrain blocking the sun', st.relief && st.shadows && st.light === 'sun'),
      keyRow('sea', '#3a5677', 'Sea', 'soil variables are not shown here'));
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
  let probeLL = null;
  function renderProbe() {
    const table = $('#probe-table');
    if (!probeLL) return;
    if (!bounds.contains(probeLL)) { $('#probe-pos').textContent = 'outside the data area'; table.replaceChildren(); for (const s of legendScales) s.hover(NaN); return; }
    const { lat, lng } = probeLL, k = demIndex(lat, lng), z = D.z[k], sampler = pointSampler(probeLL);
    $('#probe-pos').textContent = `${fmt(lat, 4)}° N  ${fmt(Math.abs(lng), 4)}° W\nelevation ${z > 0 ? z + ' m' : 'sea'}   slope ${fmt(slopeDeg[k], 1)}°\nfaces ${z > 0 && slopeDeg[k] >= 2 ? `${compass(aspectDeg[k])} (${fmt(aspectDeg[k], 0)}°)` : '–'}   lit ${fmt(clamp((shade[k] - 0.3) / 0.75, 0, 1) * 100, 0)} %`;
    const active = new Set(channels());
    table.replaceChildren();
    for (const name of G.names) {
      const info = describe(name), s = G.stats[name], v = sampler(name, st.t);
      const frac = clamp((v - s.min) / (s.max - s.min), 0, 1);
      const bar = el('span', { class: 'bar' }, el('i', { style: `width:${ok(frac) ? frac * 100 : 0}%` }));
      table.append(el('tr', { class: active.has(name) ? 'active' : '' },
        el('td', {}, info.label, bar), el('td', { class: 'val', text: `${fmt(v, info.d)} ${info.unit}` })));
    }
    for (const s of legendScales) s.hover(sampler(s.name, st.t));
  }
  map.on('mousemove', (e) => { probeLL = e.latlng; requestAnimationFrame(renderProbe); });
  map.on('mouseout', () => { for (const s of legendScales) s.hover(NaN); });

  /* ---------- pinned point / area series ---------- */
  let pointPlots = [];
  function renderPoint() {
    const host = $('#point-charts');
    pointPlots.forEach((p) => p.destroy());
    pointPlots = [];
    host.replaceChildren();
    if (!st.pin) return;
    $('#point-title').textContent = st.pin.title;
    const names = [...new Set([...channels().filter((n) => n !== 'none'), 'precipitation', 'et0_fao_evapotranspiration', 'need_72h', 'soil_moisture_9_to_27cm', 'temperature_2m'])].filter((n) => G.fields[n]);
    const width = Math.max(320, host.clientWidth || 800);
    names.forEach((name, idx) => {
      const info = describe(name), ys = new Array(G.nt);
      for (let t = 0; t < G.nt; t++) { const v = st.pin.sampler(name, t); ys[t] = ok(v) ? v : null; }
      const card = el('div', { class: 'chart-card' }, el('h3', { text: `${info.label} (${info.unit})` }));
      host.append(card);
      const colour = PALETTE[idx % PALETTE.length], bars = /precipitation$|et0_fao/.test(name);
      const plot = new uPlot({
        width, height: 104, legend: { show: false },
        cursor: { sync: { key: 'point' }, drag: { x: false, y: false } },
        scales: { x: { time: true } }, tzDate: App.utcDate,
        axes: [App.timeAxis(), App.axis({ size: 50 })],
        series: [{}, { stroke: colour, width: 1.5, fill: bars ? colour + '33' : undefined, paths: bars ? uPlot.paths.stepped({ align: -1 }) : undefined }],
        hooks: { draw: [(u) => {
          const line = (ts, c, dash) => {
            const X = u.valToPos(ts, 'x', true);
            if (X < u.bbox.left || X > u.bbox.left + u.bbox.width) return;
            u.ctx.save(); u.ctx.strokeStyle = c; u.ctx.lineWidth = devicePixelRatio; u.ctx.setLineDash(dash);
            u.ctx.beginPath(); u.ctx.moveTo(X, u.bbox.top); u.ctx.lineTo(X, u.bbox.top + u.bbox.height); u.ctx.stroke(); u.ctx.restore();
          };
          if (name === 'need_72h') {   // zero line: above = irrigation needed
            const Y = u.valToPos(0, 'y', true);
            if (Y > u.bbox.top && Y < u.bbox.top + u.bbox.height) { u.ctx.save(); u.ctx.strokeStyle = 'rgba(255,255,255,.35)'; u.ctx.lineWidth = devicePixelRatio; u.ctx.beginPath(); u.ctx.moveTo(u.bbox.left, Y); u.ctx.lineTo(u.bbox.left + u.bbox.width, Y); u.ctx.stroke(); u.ctx.restore(); }
          }
          line(App.meta.built_at, 'rgba(246,173,85,.7)', [4, 4]);
          line(G.times[st.t], '#fff', []);
        }] },
      }, [G.times, ys], card);
      plot.over.addEventListener('click', () => {
        const ts = plot.posToVal(plot.cursor.left, 'x');
        setTime(clamp(Math.round((ts - G.times[0]) / 3600), 0, G.nt - 1));
      });
      pointPlots.push(plot);
    });
    host.append(el('div', { class: 'hint', text: 'Dashed orange line: when the data was built (left = analysis, right = forecast). White line: the hour shown on the map; click a chart to move it.' }));
  }
  function pinPoint(ll) {
    st.pin = { kind: 'point', sampler: pointSampler(ll), title: `${fmt(ll.lat, 4)}° N, ${fmt(Math.abs(ll.lng), 4)}° W · hourly, 2 days of analysis then 7 days of forecast (UTC axis)` };
    pinMarker.setLatLng(ll).addTo(map);
    renderPoint(); renderLegend(); renderKey();
  }
  function pinPark(feature) {
    const p = feature.properties;
    st.pin = { kind: 'park', sampler: areaSampler(p), title: `${parkTitle(p)} · ${parkLabels[p.cat]}, ${fmtArea(p.area_ha)} · area mean over ${p.w.length} grid cells, hourly (UTC axis)` };
    pinMarker.setLatLng([p.label[1], p.label[0]]).addTo(map);
    renderPoint(); renderLegend(); renderKey();
    $('#point-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  map.on('click', (e) => {
    if (e.originalEvent && e.originalEvent._parkClick) return;
    if (bounds.contains(e.latlng)) pinPoint(e.latlng);
  });

  /* ---------- orchestration ---------- */
  function renderAll() {
    renderField(); renderVectors(); renderLegend(); renderKey(); renderTime(); renderProbe();
    pointPlots.forEach((p) => p.redraw(false));
    if (openPark) popup.setContent(parkPopup(openPark));
  }
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
    renderPoint();
  });
  bind('#map-cmap', 'cmap'); bind('#map-cmap-rev', 'rev');
  for (const ch of ['r', 'g', 'b']) bind('#map-' + ch, ch, String, () => renderPoint());
  bind('#map-gamma', 'gamma', Number); bind('#map-range', 'range'); bind('#map-interp', 'interp');
  bind('#map-opacity', 'opacity', Number);
  bind('#ov-contours', 'contours'); bind('#ov-wind', 'wind'); bind('#ov-points', 'points'); bind('#ov-values', 'values');
  bind('#ov-osm', 'osm', Number, () => osm.setOpacity(st.osm));
  bind('#tr-relief', 'relief'); bind('#tr-light', 'light'); bind('#tr-shadows', 'shadows');

  // green-space toggles, generated with icons and counts
  const toggles = $('#park-toggles');
  if (!parks.length) toggles.append(el('p', { class: 'hint', text: 'No green-space data. Run: python ML/src/build_dashboard_data.py --parks' }));
  for (const cat of ['allotments', 'garden', 'park', 'golf', 'protected']) {
    if (!parkLayers[cat]) continue;
    const s = PARK_STYLE[cat], input = el('input', { type: 'checkbox', id: `park-${cat}` });
    input.checked = st.parks[cat];
    input.addEventListener('change', () => {
      st.parks[cat] = input.checked;
      if (input.checked) parkLayers[cat].addTo(map); else map.removeLayer(parkLayers[cat]);
      refreshParkMarkers(); renderKey();
    });
    toggles.append(el('label', { class: 'chk', title: parkLabels[cat] }, input, iconEl(s.icon, 15, s.colour),
      el('span', { text: parkLabels[cat] }), el('span', { class: 'count', text: (parkCounts.get(cat) || 0).toLocaleString('en-GB') })));
  }
  $('#park-names').addEventListener('change', (e) => { st.parkNames = e.target.checked; refreshParkMarkers(); });
  $('#park-fill').addEventListener('change', (e) => {
    st.parkFill = e.target.checked;
    for (const layer of Object.values(parkLayers)) layer.eachLayer((l) => layer.resetStyle(l));
  });
  // icons beside the static overlay and terrain controls
  for (const node of $$('#map-controls [data-icon]')) node.prepend(iconEl(node.dataset.icon, 15));

  $$('#map-mode button').forEach((btn) => btn.addEventListener('click', () => {
    st.mode = btn.dataset.mode;
    $$('#map-mode button').forEach((b) => b.classList.toggle('on', b === btn));
    $('#single-controls').hidden = st.mode !== 'single';
    $('#rgb-controls').hidden = st.mode !== 'rgb';
    renderPoint();
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

  App.hooks.mapShown = () => { map.invalidateSize(); renderPoint(); };
  st.pin = { kind: 'point', sampler: pointSampler(L.latLng(site.lat, site.lon)), title: `${site.name} garden site · hourly, 2 days of analysis then 7 days of forecast (UTC axis)` };
  pinMarker.addTo(map);
  renderAll();
  renderPoint();
  refreshParkMarkers();
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

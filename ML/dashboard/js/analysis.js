'use strict';
/* Cross-analysis, carpet plot and forecast verification tabs. */

/* ---------- small chart helpers (d3 + canvas) ---------- */
function chartFrame(host, height, m = { l: 52, r: 14, t: 10, b: 34 }) {
  host.replaceChildren();
  const W = Math.max(300, host.clientWidth || 520), H = height;
  host.style.minHeight = H + 'px';
  const canvas = el('canvas', { class: 'abs', width: W * devicePixelRatio, height: H * devicePixelRatio, style: `width:${W}px;height:${H}px` });
  const ctx = canvas.getContext('2d');
  ctx.scale(devicePixelRatio, devicePixelRatio);
  host.append(canvas);
  const svg = d3.select(host).append('svg').attr('class', 'abs').attr('width', W).attr('height', H);
  const tip = el('div', { class: 'tip', hidden: '' });
  host.append(tip);
  return { W, H, m, ctx, svg, tip, host };
}
function drawAxes(f, x, y, xLabel, yLabel, xFormat, yFormat) {
  const { svg, W, H, m } = f;
  svg.append('g').attr('class', 'axis gridline').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6).tickSize(-(W - m.l - m.r)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6).tickFormat(yFormat || null));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(W / 90))).tickFormat(xFormat || null));
  if (xLabel) svg.append('text').attr('class', 'lbl').attr('x', (m.l + W - m.r) / 2).attr('y', H - 4).attr('text-anchor', 'middle').text(xLabel);
  if (yLabel) svg.append('text').attr('class', 'lbl').attr('x', m.l + 4).attr('y', m.t + 8).text(yLabel);
}
function showTip(f, ev, text) {
  const rect = f.host.getBoundingClientRect();
  f.tip.hidden = false;
  f.tip.textContent = text;
  const x = ev.clientX - rect.left + 12, y = ev.clientY - rect.top + 12;
  f.tip.style.left = Math.min(x, f.W - f.tip.offsetWidth - 4) + 'px';
  f.tip.style.top = Math.min(y, f.H - f.tip.offsetHeight - 4) + 'px';
}
/* Scatter of two aligned arrays on canvas; optional colour array and reference line. */
function scatter(host, xs, ys, o) {
  const f = chartFrame(host, o.height || 440);
  const pts = [];
  for (let i = 0; i < xs.length; i++) if (ok(xs[i]) && ok(ys[i])) pts.push(i);
  if (!pts.length) { f.svg.append('text').attr('class', 'lbl').attr('x', 60).attr('y', 40).text('No overlapping data for this selection'); return f; }
  let xd = d3.extent(pts, (i) => xs[i]), yd = d3.extent(pts, (i) => ys[i]);
  if (o.square) { xd = yd = [Math.min(xd[0], yd[0]), Math.max(xd[1], yd[1])]; }
  const x = d3.scaleLinear().domain(xd).nice().range([f.m.l, f.W - f.m.r]);
  const y = d3.scaleLinear().domain(yd).nice().range([f.H - f.m.b, f.m.t]);
  drawAxes(f, x, y, o.xLabel, o.yLabel);
  const clipId = 'clip' + Math.random().toString(36).slice(2);
  f.svg.append('clipPath').attr('id', clipId).append('rect').attr('x', f.m.l).attr('y', f.m.t).attr('width', f.W - f.m.l - f.m.r).attr('height', f.H - f.m.t - f.m.b);
  let colour = null;
  if (o.c) {
    const [lo, hi] = quantiles(pts.map((i) => o.c[i]), [0.02, 0.98]);
    const table = lut(o.cmap || 'viridis');
    colour = (i) => { const v = o.c[i]; if (!ok(v)) return 'rgba(125,141,160,.25)'; const k = (clamp((v - lo) / (hi - lo || 1), 0, 1) * 255) | 0; return `rgba(${table[k * 3]},${table[k * 3 + 1]},${table[k * 3 + 2]},.55)`; };
    f.colourRange = [lo, hi];
  }
  const size = pts.length > 8000 ? 1.6 : pts.length > 1500 ? 2.2 : 3.2, ctx = f.ctx;
  ctx.fillStyle = `rgba(79,209,197,${pts.length > 8000 ? 0.18 : 0.45})`;
  for (const i of pts) { if (colour) ctx.fillStyle = colour(i); ctx.fillRect(x(xs[i]) - size / 2, y(ys[i]) - size / 2, size, size); }
  const line = (a, b, cls) => { const [x0, x1] = x.domain(); f.svg.append('line').attr('x1', x(x0)).attr('x2', x(x1)).attr('y1', y(a + b * x0)).attr('y2', y(a + b * x1)).attr('stroke', cls).attr('stroke-width', 1.2).attr('stroke-dasharray', '5 4').attr('clip-path', `url(#${clipId})`); };
  if (o.identity) line(0, 1, '#7d8da0');
  if (o.fit && ok(o.fit.slope)) line(o.fit.intercept, o.fit.slope, '#f6ad55');
  return f;
}

/* ============================ CROSS-ANALYSIS ============================ */
App.initCross = async function initCross() {
  const st = App.state.cross = { site: 'bilbao', x: null, y: null, c: 'doy', lag: 0, period: 'all', res: 'd' };
  $('#cx-res').value = st.res;
  fillSelect($('#cx-site'), Object.entries(SITES).map(([value, s]) => ({ value, label: s.label })), st.site);
  const baseVars = (site) => site.names.filter((k) => !k.startsWith('fc_'));

  async function setup() {
    const site = await loadSite(st.site), keys = baseVars(site);
    const soil = keys.find((k) => /soil_moisture_(7_to_28|20)cm/.test(k)) || keys[0];
    const et0 = keys.find((k) => /et0/.test(k)) || keys[1];
    st.x = et0; st.y = soil;
    fillSelect($('#cx-x'), varOptions(keys), st.x);
    fillSelect($('#cx-y'), varOptions(keys), st.y);
    fillSelect($('#cx-c'), [{ value: 'none', label: '— none —' }, { value: 'hour', label: 'Hour of day (local)' }, { value: 'doy', label: 'Day of year' }, { value: 'time', label: 'Date (early → late)' }, ...varOptions(keys)], st.c);
    draw();
  }
  function column(site, key, i0, i1) {
    if (key === 'hour') return Float64Array.from({ length: i1 - i0 }, (_, i) => (((site.t0 / 3600 + i0 + i + site.utcOffset) % 24) + 24) % 24);
    if (key === 'doy') return Float64Array.from({ length: i1 - i0 }, (_, i) => d3.utcDay.count(d3.utcYear(new Date(site.time[i0 + i] * 1000)), new Date(site.time[i0 + i] * 1000)));
    if (key === 'time') return Float64Array.from({ length: i1 - i0 }, (_, i) => i0 + i);
    return site.cols[key].subarray(i0, i1);
  }
  function prepared(site, key, i0, i1) {
    let col = column(site, key, i0, i1);
    if (st.res === 'd') col = aggregate(col, 24, describe(key).agg === 'sum' ? 'sum' : 'mean');
    return col;
  }

  async function draw() {
    const site = await loadSite(st.site);
    const [i0, i1] = zoomRange(site, st.period === 'zoom');
    const X = prepared(site, st.x, i0, i1), Y = prepared(site, st.y, i0, i1);
    const step = st.res === 'd' ? 24 : 1, lagSteps = Math.round(st.lag / step);
    const shifted = new Float64Array(X.length).fill(NaN);
    for (let i = 0; i < X.length; i++) { const j = i + lagSteps; if (j >= 0 && j < Y.length) shifted[i] = Y[j]; }
    const fit = pearson(X, shifted);
    const dx = describe(st.x), dy = describe(st.y);
    const cArr = st.c === 'none' ? null : prepared(site, st.c, i0, i1);
    const cyc = st.c === 'hour' || st.c === 'doy';
    scatter($('#cx-scatter'), X, shifted, {
      xLabel: `${dx.label} (${dx.unit})`, yLabel: `${dy.label} (${dy.unit})${st.lag ? `, ${st.lag > 0 ? '+' : ''}${st.lag} h` : ''}`,
      c: cArr, cmap: cyc ? 'sinebow' : 'viridis', fit,
    });
    $('#cx-title').textContent = `${fit.n.toLocaleString()} points · ${st.res === 'd' ? 'daily' : 'hourly'} · ${new Date(site.time[i0] * 1000).toISOString().slice(0, 10)} to ${new Date(site.time[i1 - 1] * 1000).toISOString().slice(0, 10)}`;
    $('#cx-stats').textContent = `n         ${fit.n.toLocaleString()}\nPearson r ${fmt(fit.r, 3)}\nR²        ${fmt(fit.r * fit.r, 3)}\nslope     ${ok(fit.slope) ? fit.slope.toPrecision(3) : '–'} ${dy.unit} per ${dx.unit}\nintercept ${ok(fit.intercept) ? fit.intercept.toPrecision(3) : '–'} ${dy.unit}` +
      (cArr ? `\ncolour    ${st.c === 'hour' ? 'hour of day' : st.c === 'doy' ? 'day of year' : st.c === 'time' ? 'date' : describe(st.c).label}${cyc ? ' (cyclic)' : ', dark = low, bright = high'}` : '');

    // lag curve
    const maxLag = st.res === 'd' ? 10 : 72, lags = d3.range(-maxLag, maxLag + 1);
    const rs = lags.map((l) => pearson(X, Y, l).r);
    const f = chartFrame($('#cx-lagplot'), 200);
    const x = d3.scaleLinear().domain([-maxLag, maxLag]).range([f.m.l, f.W - f.m.r]);
    const ext = d3.extent(rs.filter(ok)), pad = 0.05;
    const y = d3.scaleLinear().domain([Math.min(-0.1, (ext[0] ?? 0) - pad), Math.max(0.1, (ext[1] ?? 0) + pad)]).range([f.H - f.m.b, f.m.t]);
    drawAxes(f, x, y, `lag of Y, ${st.res === 'd' ? 'days' : 'hours'}`, 'r');
    f.svg.append('line').attr('x1', f.m.l).attr('x2', f.W - f.m.r).attr('y1', y(0)).attr('y2', y(0)).attr('stroke', '#7d8da0');
    f.svg.append('path').attr('d', d3.line().defined((d) => ok(d[1])).x((d) => x(d[0])).y((d) => y(d[1]))(lags.map((l, i) => [l, rs[i]])))
      .attr('fill', 'none').attr('stroke', '#4fd1c5').attr('stroke-width', 1.6);
    const best = d3.maxIndex(rs, (r) => (ok(r) ? Math.abs(r) : -1));
    if (best >= 0) {
      f.svg.append('circle').attr('cx', x(lags[best])).attr('cy', y(rs[best])).attr('r', 4).attr('fill', '#f6ad55');
      f.svg.append('text').attr('class', 'lbl').attr('x', clamp(x(lags[best]) + 8, f.m.l, f.W - 150)).attr('y', clamp(y(rs[best]) - 8, 14, f.H - 40))
        .text(`strongest: r = ${fmt(rs[best], 3)} at ${lags[best] > 0 ? '+' : ''}${lags[best]} ${st.res === 'd' ? 'd' : 'h'}`).attr('fill', '#f6ad55');
    }
    const cur = st.res === 'd' ? lagSteps : st.lag;
    f.svg.append('line').attr('x1', x(cur)).attr('x2', x(cur)).attr('y1', f.m.t).attr('y2', f.H - f.m.b).attr('stroke', '#fff').attr('stroke-dasharray', '3 3');

    // correlation matrix
    const keys = baseVars(site).filter((k) => !/air_temp_(max|min)|surface_pressure/.test(k)).slice(0, 18);
    const cols = keys.map((k) => prepared(site, k, i0, i1));
    const host = $('#cx-matrix'), W = Math.max(320, host.clientWidth || 520), label = 150, cell = Math.max(14, Math.floor((W - label - 70) / keys.length)), size = cell * keys.length;
    host.replaceChildren();
    host.style.minHeight = size + label + 'px';
    const svg = d3.select(host).append('svg').attr('width', W).attr('height', size + label);
    const short = (k) => { const s = describe(k).label; return s.length > 24 ? s.slice(0, 23) + '…' : s; };
    const g = svg.append('g').attr('transform', `translate(${label},${label})`);
    const tip = el('div', { class: 'tip', hidden: '' });
    host.append(tip);
    keys.forEach((a, i) => keys.forEach((b, j) => {
      const r = i === j ? 1 : pearson(cols[i], cols[j]).r;
      g.append('rect').attr('x', j * cell).attr('y', i * cell).attr('width', cell - 1).attr('height', cell - 1)
        .attr('fill', ok(r) ? d3.interpolateRdBu(0.5 - r / 2) : '#1b2530').style('cursor', 'pointer')
        .attr('stroke', (a === st.y && b === st.x) ? '#fff' : 'none')
        .on('mousemove', (ev) => { const rect = host.getBoundingClientRect(); tip.hidden = false; tip.textContent = `${describe(a).label}\n${describe(b).label}\nr = ${fmt(r, 3)}`; tip.style.left = Math.min(ev.clientX - rect.left + 12, W - 240) + 'px'; tip.style.top = ev.clientY - rect.top + 12 + 'px'; })
        .on('mouseleave', () => { tip.hidden = true; })
        .on('click', () => { st.x = b; st.y = a; $('#cx-x').value = b; $('#cx-y').value = a; draw(); });
      if (cell >= 30 && ok(r)) g.append('text').attr('x', j * cell + cell / 2).attr('y', i * cell + cell / 2 + 3).attr('text-anchor', 'middle')
        .attr('font', '9px monospace').style('font', '9px ui-monospace, monospace').attr('fill', Math.abs(r) > 0.6 ? '#fff' : '#111').style('pointer-events', 'none').text(r.toFixed(2));
    }));
    keys.forEach((k, i) => {
      svg.append('text').attr('class', 'lbl').attr('x', label - 6).attr('y', label + i * cell + cell / 2 + 3).attr('text-anchor', 'end').text(short(k));
      svg.append('text').attr('class', 'lbl').attr('transform', `translate(${label + i * cell + cell / 2 + 3},${label - 6}) rotate(-55)`).text(short(k));
    });
  }

  $('#cx-site').addEventListener('change', (e) => { st.site = e.target.value; setup(); });
  for (const [id, key] of [['#cx-x', 'x'], ['#cx-y', 'y'], ['#cx-c', 'c'], ['#cx-period', 'period'], ['#cx-res', 'res']]) $(id).addEventListener('change', (e) => { st[key] = e.target.value; draw(); });
  $('#cx-lag').addEventListener('input', (e) => { st.lag = +e.target.value; $('#cx-lag-out').textContent = `${st.lag > 0 ? '+' : ''}${st.lag} h`; draw(); });
  App.hooks.crossShown = () => draw();
  await setup();
};

/* ================================ CARPET ================================ */
App.initCarpet = async function initCarpet() {
  const st = App.state.carpet = { site: 'bilbao', v: null, cmap: null };
  fillSelect($('#cp-site'), Object.entries(SITES).map(([value, s]) => ({ value, label: s.label })), st.site);
  fillSelect($('#cp-cmap'), Object.keys(CMAPS).map((k) => ({ value: k, label: k })), 'inferno');
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  async function setup() {
    const site = await loadSite(st.site), keys = site.names.filter((k) => !k.startsWith('fc_'));
    st.v = keys.find((k) => /et0/.test(k)) || keys.find((k) => /solar|shortwave/.test(k)) || keys[0];
    fillSelect($('#cp-var'), varOptions(keys), st.v);
    st.cmap = describe(st.v).cmap; $('#cp-cmap').value = st.cmap;
    draw();
  }
  async function draw() {
    const site = await loadSite(st.site), col = site.cols[st.v], info = describe(st.v);
    const off = site.utcOffset * 3600, day0 = Math.floor((site.t0 + off) / 86400), days = Math.floor((site.time[site.n - 1] + off) / 86400) - day0 + 1;
    const [lo, hi] = quantiles(col, [0.01, 0.99]), table = lut(st.cmap, !!info.rev && st.cmap === info.cmap);
    $('#cp-title').textContent = `${info.label} (${info.unit}) · hour of day against date · ${site.label}`;
    const host = $('#cp-plot'), W = Math.max(320, host.clientWidth || 900), m = { l: 44, r: 10, t: 6, b: 26 }, rowH = 13, H = m.t + m.b + 24 * rowH;
    host.replaceChildren(); host.style.minHeight = H + 'px';
    const raster = el('canvas', { width: days, height: 24 });
    const rctx = raster.getContext('2d'), img = rctx.createImageData(days, 24);
    const grid = new Float32Array(days * 24).fill(NaN);
    for (let i = 0; i < site.n; i++) {
      const local = site.time[i] + off - 3600, d = Math.floor(local / 86400) - day0, h = Math.floor((((local % 86400) + 86400) % 86400) / 3600);
      if (d < 0 || d >= days) continue;
      grid[h * days + d] = col[i];
      const v = col[i], o = (h * days + d) * 4;
      if (!ok(v)) continue;
      const k = (clamp((v - lo) / (hi - lo || 1), 0, 1) * 255) | 0;
      img.data[o] = table[k * 3]; img.data[o + 1] = table[k * 3 + 1]; img.data[o + 2] = table[k * 3 + 2]; img.data[o + 3] = 255;
    }
    rctx.putImageData(img, 0, 0);
    const canvas = el('canvas', { class: 'abs', width: W, height: H });
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = '#05080b'; ctx.fillRect(m.l, m.t, W - m.l - m.r, 24 * rowH);
    ctx.drawImage(raster, m.l, m.t, W - m.l - m.r, 24 * rowH);
    host.append(canvas);
    const svg = d3.select(host).append('svg').attr('class', 'abs').attr('width', W).attr('height', H);
    const x = d3.scaleUtc().domain([new Date(day0 * 86400e3), new Date((day0 + days) * 86400e3)]).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([0, 24]).range([m.t, m.t + 24 * rowH]);
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${m.t + 24 * rowH})`).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(W / 100))));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).tickValues([0, 6, 12, 18, 24]).tickFormat((h) => String(h).padStart(2, '0') + 'h'));
    canvas.style.pointerEvents = 'none';
    svg.on('mousemove', (ev) => {
      const [px, py] = d3.pointer(ev);
      const d = Math.floor(((px - m.l) / (W - m.l - m.r)) * days), h = Math.floor((py - m.t) / rowH);
      if (d < 0 || d >= days || h < 0 || h > 23) return;
      const date = new Date((day0 + d) * 86400e3).toISOString().slice(0, 10);
      $('#cp-readout').textContent = `${date}  ${String(h).padStart(2, '0')}:00–${String(h + 1).padStart(2, '0')}:00\n${info.label}\n${fmt(grid[h * days + d], info.d)} ${info.unit}`;
    });
    $('#cp-legend').replaceChildren(legendBar(`${info.label} (${info.unit}), 1–99 % range`, table, lo, hi, info.d));

    // mean daily cycle per month
    const sum = new Float64Array(12 * 24), cnt = new Float64Array(12 * 24);
    for (let i = 0; i < site.n; i++) {
      if (!ok(col[i])) continue;
      const dt = new Date((site.time[i] + off - 3600) * 1000), k = dt.getUTCMonth() * 24 + dt.getUTCHours();
      sum[k] += col[i]; cnt[k]++;
    }
    const f = chartFrame($('#cp-cycle'), 230, { l: 52, r: 14, t: 24, b: 34 });
    const means = d3.range(12).map((mo) => d3.range(24).map((h) => (cnt[mo * 24 + h] ? sum[mo * 24 + h] / cnt[mo * 24 + h] : NaN)));
    const cx = d3.scaleLinear().domain([0, 23]).range([f.m.l, f.W - f.m.r]);
    const cy = d3.scaleLinear().domain(d3.extent(means.flat().filter(ok))).nice().range([f.H - f.m.b, f.m.t]);
    drawAxes(f, cx, cy, 'hour of day (local standard time)', info.unit, (h) => String(h).padStart(2, '0'));
    const monthColour = (mo) => d3.interpolateSinebow((mo + 0.5) / 12);
    means.forEach((row, mo) => {
      f.svg.append('path').attr('d', d3.line().defined(ok).x((_, h) => cx(h)).y((v) => cy(v))(row)).attr('fill', 'none').attr('stroke', monthColour(mo)).attr('stroke-width', 1.4);
    });
    const keys = el('div', { class: 'keys' });
    MONTHS.forEach((name, mo) => keys.append(el('span', {}, el('i', { style: `background:${monthColour(mo)}` }), name)));
    $('#cp-cycle').prepend(keys);
    keys.style.cssText = 'position:relative;z-index:2;padding-left:52px';
    f.host.querySelectorAll('.abs').forEach((n) => { n.style.top = '18px'; });
    f.host.style.minHeight = '252px';
  }
  $('#cp-site').addEventListener('change', (e) => { st.site = e.target.value; setup(); });
  $('#cp-var').addEventListener('change', (e) => { st.v = e.target.value; st.cmap = describe(st.v).cmap; $('#cp-cmap').value = st.cmap; draw(); });
  $('#cp-cmap').addEventListener('change', (e) => { st.cmap = e.target.value; draw(); });
  App.hooks.carpetShown = () => draw();
  await setup();
};

/* ============================ FORECAST SKILL ============================ */
App.initSkill = async function initSkill() {
  const st = App.state.skill = { site: 'bilbao', v: null, res: 'd', lead: 1, thr: 1 };
  fillSelect($('#sk-site'), Object.entries(SITES).map(([value, s]) => ({ value, label: s.label })), st.site);
  const STATION = { precipitation: 'precip_mm', temperature_2m: 'air_temp_c', shortwave_radiation: 'solar_rad_wm2', relative_humidity_2m: 'rh_pct' };
  const observedFor = (site, base) => [base, STATION[base], 'om_' + base].find((k) => k && site.cols[k]);
  const LEAD_COLOURS = ['#4fd1c5', '#f6ad55', '#b794f4'];

  function leadsOf(site, base) {
    return site.names.map((k) => new RegExp(`^fc_${base}_lead(\\d)d$`).exec(k)).filter(Boolean).map((m) => +m[1]).sort();
  }
  async function setup() {
    const site = await loadSite(st.site);
    const bases = [...new Set(site.names.map((k) => /^fc_(.+)_lead\dd$/.exec(k)).filter(Boolean).map((m) => m[1]))]
      .filter((b) => b !== 'precipitation_probability' && observedFor(site, b));
    st.v = bases.includes('precipitation') ? 'precipitation' : bases[0];
    fillSelect($('#sk-var'), bases.map((b) => ({ value: b, label: `${describe(b).label} (${describe(b).unit})` })), st.v);
    draw();
  }
  function pairs(site, base, lead) {
    const info = describe(base), how = info.agg === 'sum' ? 'sum' : 'mean';
    let obs = site.cols[observedFor(site, base)], fc = site.cols[`fc_${base}_lead${lead}d`], time = site.time;
    if (st.res === 'd') { obs = aggregate(obs, 24, how); fc = aggregate(fc, 24, how); time = Float64Array.from(obs, (_, i) => site.t0 + i * 86400); }
    return { obs, fc, time };
  }
  function scores(p) {
    let n = 0, sb = 0, sa = 0, sq = 0, so = 0, hit = 0, miss = 0, fa = 0;
    for (let i = 0; i < p.obs.length; i++) {
      const o = p.obs[i], f = p.fc[i];
      if (!ok(o) || !ok(f)) continue;
      n++; sb += f - o; sa += Math.abs(f - o); sq += (f - o) ** 2; so += o;
      const eo = o >= st.thr, ef = f >= st.thr;
      if (eo && ef) hit++; else if (eo) miss++; else if (ef) fa++;
    }
    return { n, mean: so / n, bias: sb / n, mae: sa / n, rmse: Math.sqrt(sq / n), r: pearson(p.fc, p.obs).r,
      pod: hit / (hit + miss), far: fa / (hit + fa), csi: hit / (hit + miss + fa) };
  }

  async function draw() {
    const site = await loadSite(st.site), base = st.v;
    if (!base) return;
    const info = describe(base), leads = leadsOf(site, base), obsKey = observedFor(site, base);
    fillSelect($('#sk-lead'), leads.map((l) => ({ value: String(l), label: `${l} day${l > 1 ? 's' : ''} ahead` })), String(st.lead));
    if (!leads.includes(st.lead)) st.lead = leads[0];
    $('#sk-thr-unit').textContent = `${info.unit}${info.agg === 'sum' ? (st.res === 'd' ? ' per day' : ' per hour') : ''}`;
    $('#sk-note').textContent = `truth: ${describe(obsKey).label}\n${obsKey.startsWith('om_') || st.site === 'bilbao' ? 'source: weather-model archive (not a gauge)' : 'source: station instrument'}`;
    const all = leads.map((l) => pairs(site, base, l)), sc = all.map(scores);

    // table
    const table = el('table', { class: 'scores' });
    const head = ['lead', 'n', 'obs mean', 'bias', 'MAE', 'RMSE', 'r', 'hit rate', 'false alarms', 'CSI'];
    table.append(el('tr', {}, ...head.map((h) => el('th', { text: h }))));
    sc.forEach((s, i) => table.append(el('tr', {},
      el('td', {}, el('span', { style: `color:${LEAD_COLOURS[i]}`, text: `+${leads[i]} d` })),
      ...[s.n.toLocaleString(), fmt(s.mean, info.d), (s.bias >= 0 ? '+' : '') + fmt(s.bias, info.d), fmt(s.mae, info.d), fmt(s.rmse, info.d), fmt(s.r, 3),
        fmt(s.pod * 100, 0) + ' %', fmt(s.far * 100, 0) + ' %', fmt(s.csi, 2)].map((t) => el('td', { text: t })))));
    $('#sk-table').replaceChildren(table, el('p', { class: 'hint', text: `Hit rate, false alarms and CSI count events of at least ${st.thr} ${info.unit}. Units: ${info.unit}.` }));

    // scatter for the chosen lead
    const p = all[leads.indexOf(st.lead)];
    scatter($('#sk-scatter'), p.obs, p.fc, { xLabel: `observed (${info.unit})`, yLabel: `forecast +${st.lead} d (${info.unit})`, identity: true, square: true, fit: pearson(p.obs, p.fc) });
    $('#sk-scatter-title').textContent = `${info.label}, ${st.res === 'd' ? 'daily' : 'hourly'} · grey = perfect forecast, orange = fitted`;

    // error histogram
    const errs = all.map((q) => Array.from(q.fc, (f, i) => f - q.obs[i]).filter(ok));
    const [e1, e99] = quantiles(errs.flat(), [0.01, 0.99]), lim = Math.max(Math.abs(e1), Math.abs(e99)) || 1;
    const fh = chartFrame($('#sk-hist'), 210);
    const hx = d3.scaleLinear().domain([-lim, lim]).range([fh.m.l, fh.W - fh.m.r]);
    const bins = errs.map((e) => d3.bin().domain(hx.domain()).thresholds(41)(e).map((b) => ({ x: (b.x0 + b.x1) / 2, y: b.length / e.length })));
    const hy = d3.scaleLinear().domain([0, d3.max(bins.flat(), (b) => b.y) || 1]).nice().range([fh.H - fh.m.b, fh.m.t]);
    drawAxes(fh, hx, hy, `forecast − observed (${info.unit})`, 'share', null, d3.format('.0%'));
    fh.svg.append('line').attr('x1', hx(0)).attr('x2', hx(0)).attr('y1', fh.m.t).attr('y2', fh.H - fh.m.b).attr('stroke', '#7d8da0').attr('stroke-dasharray', '3 3');
    bins.forEach((b, i) => fh.svg.append('path').attr('d', d3.line().x((d) => hx(d.x)).y((d) => hy(d.y)).curve(d3.curveStep)(b)).attr('fill', 'none').attr('stroke', LEAD_COLOURS[i]).attr('stroke-width', 1.5));

    // MAE by month
    const fm = chartFrame($('#sk-month'), 210);
    const monthly = all.map((q) => d3.rollups(d3.range(q.obs.length).filter((i) => ok(q.obs[i]) && ok(q.fc[i])),
      (idx) => d3.mean(idx, (i) => Math.abs(q.fc[i] - q.obs[i])), (i) => new Date(q.time[i] * 1000).toISOString().slice(0, 7))
      .map(([mo, v]) => ({ t: new Date(mo + '-15T00:00Z'), v })).sort((a, b) => a.t - b.t));
    const mx = d3.scaleUtc().domain(d3.extent(monthly.flat(), (d) => d.t)).range([fm.m.l, fm.W - fm.m.r]);
    const my = d3.scaleLinear().domain([0, d3.max(monthly.flat(), (d) => d.v) || 1]).nice().range([fm.H - fm.m.b, fm.m.t]);
    drawAxes(fm, mx, my, 'month', `MAE (${info.unit})`);
    monthly.forEach((row, i) => fm.svg.append('path').attr('d', d3.line().x((d) => mx(d.t)).y((d) => my(d.v))(row)).attr('fill', 'none').attr('stroke', LEAD_COLOURS[i]).attr('stroke-width', 1.5));
    for (const id of ['#sk-hist', '#sk-month']) {
      const keys = el('div', { class: 'keys', style: 'position:absolute;right:14px;top:4px;z-index:2' });
      leads.forEach((l, i) => keys.append(el('span', {}, el('i', { style: `background:${LEAD_COLOURS[i]}` }), `+${l} d`)));
      $(id).append(keys);
    }

    // reliability of rain probability
    const relHost = $('#sk-rel'), probLeads = leadsOf(site, 'precipitation_probability').filter((l) => site.cols[`fc_precipitation_probability_lead${l}d`].some(ok));
    const rainKey = observedFor(site, 'precipitation');
    if (!probLeads.length || !rainKey) {
      relHost.replaceChildren(el('p', { class: 'hint', text: 'Past rain probabilities are not archived for this site, so reliability cannot be checked here. Switch the site to Darrington to see it.' }));
      relHost.style.minHeight = '60px';
      $('#sk-rel-note').textContent = '';
    } else {
      $('#sk-rel-note').textContent = 'hourly; rain = 0.1 mm or more; diagonal = perfectly calibrated';
      const fr = chartFrame(relHost, 260);
      const rx = d3.scaleLinear().domain([0, 100]).range([fr.m.l, fr.W - fr.m.r]), ry = d3.scaleLinear().domain([0, 100]).range([fr.H - fr.m.b, fr.m.t]);
      drawAxes(fr, rx, ry, 'forecast probability (%)', 'observed frequency (%)');
      fr.svg.append('line').attr('x1', rx(0)).attr('y1', ry(0)).attr('x2', rx(100)).attr('y2', ry(100)).attr('stroke', '#7d8da0').attr('stroke-dasharray', '5 4');
      const rain = site.cols[rainKey];
      probLeads.forEach((l, i) => {
        const prob = site.cols[`fc_precipitation_probability_lead${l}d`], sumP = new Float64Array(10), wet = new Float64Array(10), cnt = new Float64Array(10);
        for (let k = 0; k < prob.length; k++) { if (!ok(prob[k]) || !ok(rain[k])) continue; const b = Math.min(9, Math.floor(prob[k] / 10)); sumP[b] += prob[k]; cnt[b]++; if (rain[k] >= 0.1) wet[b]++; }
        const pts = d3.range(10).filter((b) => cnt[b] >= 20).map((b) => [sumP[b] / cnt[b], (100 * wet[b]) / cnt[b], cnt[b]]);
        fr.svg.append('path').attr('d', d3.line().x((d) => rx(d[0])).y((d) => ry(d[1]))(pts)).attr('fill', 'none').attr('stroke', LEAD_COLOURS[i]).attr('stroke-width', 1.6);
        fr.svg.append('g').selectAll('circle').data(pts).join('circle').attr('cx', (d) => rx(d[0])).attr('cy', (d) => ry(d[1])).attr('r', 3.5).attr('fill', LEAD_COLOURS[i])
          .on('mousemove', (ev, d) => showTip(fr, ev, `+${l} d\nforecast ${fmt(d[0], 0)} %\nobserved ${fmt(d[1], 0)} %\n${d[2].toLocaleString()} hours`)).on('mouseleave', () => { fr.tip.hidden = true; });
      });
      const keys = el('div', { class: 'keys', style: 'position:absolute;right:14px;top:4px;z-index:2' });
      probLeads.forEach((l, i) => keys.append(el('span', {}, el('i', { style: `background:${LEAD_COLOURS[i]}` }), `+${l} d`)));
      relHost.append(keys);
    }
  }
  $('#sk-site').addEventListener('change', (e) => { st.site = e.target.value; setup(); });
  $('#sk-var').addEventListener('change', (e) => { st.v = e.target.value; draw(); });
  $('#sk-res').addEventListener('change', (e) => { st.res = e.target.value; draw(); });
  $('#sk-lead').addEventListener('change', (e) => { st.lead = +e.target.value; draw(); });
  $('#sk-thr').addEventListener('change', (e) => { st.thr = +e.target.value || 0; draw(); });
  App.hooks.skillShown = () => draw();
  await setup();
};

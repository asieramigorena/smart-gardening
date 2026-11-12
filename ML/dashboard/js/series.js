'use strict';
/* Time series tab: stacked, zoom-linked uPlot panels grouped by unit. */
App.initSeries = async function initSeries() {
  const st = App.state.series = { site: 'bilbao', res: 'h', selected: null, plots: [], syncing: false };
  fillSelect($('#ts-site'), Object.entries(SITES).map(([value, s]) => ({ value, label: s.label })), st.site);

  function buildVarList(site) {
    const host = $('#ts-vars');
    host.replaceChildren();
    const groups = d3.group(site.names.map(describe), (d) => d.group);
    for (const [name, items] of groups) {
      const box = el('details', name === 'Observed' ? { open: '' } : {}, el('summary', { text: `${name} (${items.length})` }));
      for (const info of items.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))) {
        const input = el('input', { type: 'checkbox', value: info.key });
        input.checked = st.selected.has(info.key);
        input.addEventListener('change', () => { if (input.checked) st.selected.add(info.key); else st.selected.delete(info.key); draw(); });
        box.append(el('label', { class: 'chk' }, input, `${info.label} (${info.unit})`));
      }
      host.append(box);
    }
  }

  function seriesData(site, keys) {
    // returns [x, ...ys] at the chosen resolution, gaps as null
    const size = st.res === 'h' ? 1 : 24, n = Math.floor(site.n / size);
    const x = new Array(n);
    for (let i = 0; i < n; i++) x[i] = site.t0 + (i * size + (size > 1 ? size / 2 : 0)) * 3600;
    const ys = keys.map((k) => {
      const info = describe(k);
      let col = site.cols[k];
      if (size > 1) col = aggregate(col, size, info.agg === 'sum' ? 'sum' : 'mean');
      if (st.res === 'w') col = rolling(col, 7);
      return Array.from(col, (v) => (ok(v) ? v : null));
    });
    return [x, ...ys];
  }

  function setRange(min, max, source) {
    if (st.syncing) return;
    st.syncing = true;
    for (const p of st.plots) if (p !== source) p.setScale('x', { min, max });
    st.syncing = false;
    App.state.range = { site: st.site, min, max };
  }

  async function draw() {
    const site = await loadSite(st.site);
    const host = $('#ts-charts');
    const keep = st.plots[0] ? { min: st.plots[0].scales.x.min, max: st.plots[0].scales.x.max } : null;
    st.plots.forEach((p) => p.destroy());
    st.plots = [];
    host.replaceChildren();
    const keys = [...st.selected].filter((k) => site.cols[k]);
    if (!keys.length) { host.append(el('div', { class: 'panel muted', text: 'Tick at least one variable on the left.' })); return; }
    // one panel per unit; soil moisture and other families stay together by unit
    const groups = d3.group(keys, (k) => { const d = describe(k); return d.unit + (d.agg === 'sum' ? '|' + d.label.replace(/ · .*/, '') : ''); });
    const width = Math.max(360, (host.clientWidth || 900) - 26);
    for (const [, members] of groups) {
      const infos = members.map(describe), unit = infos[0].unit;
      const data = seriesData(site, members);
      const bars = infos[0].agg === 'sum';
      const title = [...new Set(infos.map((d) => d.label.replace(/ \d.*$/, '').replace(/ · .*/, '')))].join(' / ') +
        ` (${unit}${bars && st.res !== 'h' ? ' per day' : bars ? ' per hour' : ''})`;
      const card = el('div', { class: 'panel chart-card' }, el('h3', { text: title }));
      host.append(card);
      const plot = new uPlot({
        width, height: 170,
        cursor: { sync: { key: 'ts', setSeries: false }, drag: { x: true, y: false } },
        scales: { x: { time: true } }, tzDate: App.utcDate,
        axes: [App.timeAxis(), App.axis({ size: 56 })],
        legend: { live: true },
        series: [{ value: (u, v) => (v == null ? '–' : new Date(v * 1000).toISOString().slice(0, 16).replace('T', ' ') + 'Z') },
          ...members.map((k, i) => ({
            label: describe(k).label, stroke: PALETTE[i % PALETTE.length], width: 1.2, spanGaps: false,
            fill: bars && members.length === 1 ? PALETTE[i] + '40' : undefined,
            paths: bars ? uPlot.paths.stepped({ align: 1 }) : undefined,
            value: (u, v) => (v == null ? '–' : v.toFixed(describe(k).d)),
            points: { show: false },
          }))],
        hooks: { setScale: [(u, key) => { if (key === 'x') setRange(u.scales.x.min, u.scales.x.max, u); }] },
      }, data, card);
      st.plots.push(plot);
    }
    if (keep && ok(keep.min)) st.plots.forEach((p) => p.setScale('x', keep));
    else App.state.range = { site: st.site, min: site.time[0], max: site.time[site.n - 1] };
  }

  async function switchSite() {
    const site = await loadSite(st.site);
    st.selected = new Set(site.defaults.filter((k) => site.cols[k]));
    st.plots.forEach((p) => p.destroy());
    st.plots = [];
    buildVarList(site);
    await draw();
  }

  $('#ts-site').addEventListener('change', (e) => { st.site = e.target.value; switchSite(); });
  $('#ts-res').addEventListener('change', (e) => { st.res = e.target.value; draw(); });
  $$('#ts-range button').forEach((btn) => btn.addEventListener('click', async () => {
    const site = await loadSite(st.site), days = +btn.dataset.days, max = site.time[site.n - 1];
    $$('#ts-range button').forEach((b) => b.classList.toggle('on', b === btn));
    const min = days ? max - days * 86400 : site.time[0];
    st.plots.forEach((p) => p.setScale('x', { min, max }));
  }));
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if ($('#tab-series').classList.contains('on')) draw(); }, 200); });
  App.hooks.seriesShown = () => draw();
  await switchSite();
};

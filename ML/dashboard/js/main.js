'use strict';
/* Start-up: load data, wire the tabs, initialise each view the first time it is shown. */
(async () => {
  const fatal = (message) => { const box = $('#fatal'); box.hidden = false; box.textContent = message; };
  const ready = {};
  const views = {
    map: { init: () => App.initMap(), shown: 'mapShown' },
    series: { init: () => App.initSeries(), shown: 'seriesShown' },
    cross: { init: () => App.initCross(), shown: 'crossShown' },
    carpet: { init: () => App.initCarpet(), shown: 'carpetShown' },
    skill: { init: () => App.initSkill(), shown: 'skillShown' },
  };
  async function show(tab) {
    $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
    $$('.tab').forEach((s) => s.classList.toggle('on', s.id === 'tab-' + tab));
    try {
      if (!ready[tab]) { ready[tab] = true; await views[tab].init(); }
      else if (App.hooks[views[tab].shown]) App.hooks[views[tab].shown]();
    } catch (err) { console.error(err); fatal(`The ${tab} view failed:\n\n${err.stack || err}`); }
    if (location.hash.slice(1) !== tab) history.replaceState(null, '', '#' + tab);
  }
  $$('#tabs button').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));

  try {
    await loadGrid();
  } catch (err) {
    console.error(err);
    const meta = App.meta || {};
    if (meta.dem && !meta.grid) {
      fatal('The forecast grid has not been downloaded yet (Open-Meteo hourly limit).\n\nRun:  python ML/src/build_dashboard_data.py --grid-only\n\nThe other tabs work: reload with #series in the address.');
      if (location.hash.length < 2 || location.hash === '#map') return;
      $('#fatal').hidden = true;
    } else { fatal(`Could not load the data files.\n\n${err.message}\n\nBuild them with:  python ML/src/build_dashboard_data.py`); return; }
  }
  const meta = App.meta;
  const built = new Date(meta.built_at * 1000).toISOString().slice(0, 16).replace('T', ' ');
  $('#build-info').textContent = `${meta.site.name} · ${meta.site.lat.toFixed(3)}° N ${Math.abs(meta.site.lon).toFixed(3)}° W · data built ${built} UTC`;
  const start = location.hash.slice(1);
  show(views[start] ? start : 'map');
})();

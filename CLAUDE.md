# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Smart Gardening: an IoT urban gardening system that decides on plant care (e.g. watering) from soil readings, temperature and light sensors, combined with rain forecasts. (The README's "floor lecture" is a literal translation of the Spanish *lectura del suelo*, meaning soil readings.)

## Commands

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python ML/src/build_dataset.py                 # build the hourly training table (2024 to now)
.venv/bin/python ML/src/build_dataset.py --years 2025 2026
.venv/bin/python ML/src/build_training_set.py            # forest training set -> data/training/<zone>/ (--zone, --lag, --list)
.venv/bin/python -m unittest discover -s ML/tests        # all tests; one: -k test_valve_time

# local dashboard: data files, then the app at http://localhost:8765
.venv/bin/python ML/src/fetch_site_weather.py bilbao 43.263 -2.935      # slow, cached; --refresh to redo
.venv/bin/python ML/src/fetch_site_weather.py darrington 48.54 -121.45
.venv/bin/python ML/src/build_dashboard_data.py                         # --grid-only refreshes just the forecast map
.venv/bin/python ML/dashboard/serve.py                                  # --no-open, --port N
```

Tests use stdlib `unittest` (no pytest); there is no linter. `AEMET_KEY` and the Open-Meteo endpoint URLs (`ARCHIVE_URL`, `PREVIOUS_RUNS_URL`, `FORECAST_URL`) live in `.env` (git-ignored). `data/` is git-ignored and fully regenerable.

## Architecture

The design is specified in `ML/docs/README.md` (success metric, model inputs and outputs) and the agronomy behind it in `ML/docs/Irrigation Simulator Handbook.pdf`. Read the README before changing features or targets.

Planned pipeline, hourly: a quantile regression forest predicts soil moisture at +6/24/48 h assuming no irrigation; a behavior tree turns that into a valve-open duration (water flow is constant) and acts as the failsafe. Only the data layer exists so far.

`ML/src/connectors/` holds one module per data source. All return hourly DataFrames indexed in UTC, where sums and means (rain, radiation, ET0) cover the hour ending at the timestamp:

- `uscrn.py`: NOAA station files with soil moisture by depth. This is the training target. The hourly product has no wind or pressure.
- `open_meteo.py`: `observed` (reanalysis, fills what the station lacks), `forecast_as_issued` (archived forecasts by lead time, from 2024), `forecast` (live).
- `aemet.py`: official Spanish forecast and station observations for the deployment site (Bilbao). No radiation or ET0, so it is a cross-check, not the primary forecast.

`ML/src/build_dataset.py` joins them into `data/processed/<station>_hourly.parquet`: station columns unprefixed, `om_` for Open-Meteo observed, `fc_<variable>_lead<N>d` for forecasts.

Leakage rule: forecast features must come from `fc_*_lead*` columns (what was known at decision time), never from observed values at future timestamps. Only `training/forecast.py` (`ForecastView`) turns lead columns into features: hour t+k is read from lead ⌈(k + lag)/24⌉ days, and horizons the stored leads cannot cover raise `LeakageError`.

Coding conventions: modules start with a docstring header (file name, purpose, "Written by Diego Revilla on DD-MM-YYYY", "Copyright University of Deusto"); domain objects are frozen, slotted, keyword-only dataclasses that validate in `__post_init__` and raise subclasses of `garden.errors.GardenError`; physics methods accept scalars or arrays. Connectors are classes with static methods (`connectors.open_meteo.OpenMeteoDataset`).

### Domain model (`ML/src/garden/`)

- `soil.py` `Soil` (θ_sat, θ_FC, θ_WP, Ksat, intake rate; TAW, RAW, θ_refill, relative depletion) and `SoilCalibration` (θ_FC as the post-drainage median after soaking rain, from the probe record).
- `plant.py` `Plant` (FAO-56 Kc curve, stage lengths, root growth, p adjusted for ETc) and `Planting` (annual from `sown_on`, perennial restarting each `season_start`).
- `zone.py` `Zone` (one valve-controlled bed: area, location, timezone, soil, planting, `ProbeArray`, `IrrigationSystem`; README valve-time formula in `valve_open_seconds`), `ProbeArray` (thickness-weighted root-zone θ, renormalised over missing probes), `IrrigationLog`.
- `catalogue.py` reference `SOILS` and `PLANTS` (FAO-56 tables, Rawls 1982); zones refer to them by key.
- `config.py` `Garden.load()` reads `ML/config/garden.toml` (zones plus optional `[zones.<key>.data]` binding to an hourly table and its weather column names).

### Training set (`ML/src/training/`)

`TrainingSetBuilder(zone, binding).build()` returns a `TrainingSet` (`X(split)`, `y(split, kind="theta"|"dtheta")`, `save`, `load`). Steps: `SensorQualityControl` bit-flags each probe reading (missing, out of range, stuck ≥ 72 h, spike, frozen soil) and removes flagged values; root-zone θ from the probes; feature blocks in `features.py` (subclasses of `FeatureBlock`, one per README input group plus `WaterBalanceFeatures`, a one-bucket FAO-56 projection with 48 h drainage); `TargetBuilder` (θ_rz at +6/24/48 h and its change, masked when the valve opens inside the horizon); chronological `TemporalSplit` (70/15/15) with an embargo of the longest horizon before each boundary. `manifest.json` records zone, calibration, settings, feature groups, coverage, QC counts and the git commit. Darrington is the only zone with data; it is unirrigated, so the irrigation features are constant there.

## Dashboard

`ML/dashboard/` is a local, dependency-free web app (plain scripts, no build step; d3, uPlot and Leaflet are vendored in `vendor/`). `serve.py` serves that folder and maps `/data/` to `data/dashboard/app/`, which `ML/src/build_dashboard_data.py` fills with `meta.json`, `grid.bin` (float32 forecast cube `[variable][hour][row north to south][col]`, about 2 km spacing, 2 days back and 7 ahead), `dem.bin` (int16 terrain in Web Mercator, from `connectors/terrain.py`), `parks.json` (OpenStreetMap parks, gardens, allotments, golf courses and protected areas from `connectors/osm.py`, each with terrain statistics and bilinear weights `w` onto the forecast grid so the app can average any field over an area; raw Overpass answers are cached in `data/raw/osm/`, rebuild with `--parks`) and one hourly `series_<site>.json` per site.

Scripts load in order and share the global `App`: `js/core.js` (loading, the variable catalogue `describe()` that maps column names to labels, units and colour maps, statistics, sun position), `js/map.js` (Leaflet map; one canvas composites terrain shading with either a single variable or three variables routed to R, G and B; a second canvas draws isolines and wind arrows), `js/series.js` (zoom-linked uPlot panels), `js/analysis.js` (cross-plot with lag, correlation matrix, carpet plot, forecast verification), `js/main.js` (tabs; each view initialises on first show). Derived forecast fields (`rain_next_24h`, `et0_next_24h`, `need_72h` = ET₀ − rain over the next 72 h) are computed in `loadGrid`. A new data column appears in the app automatically; add a `CATALOG` entry in `core.js` to give it a label and unit.

Open-Meteo's free tier has an hourly request limit; the forecast grid download is chunked and cached under `data/raw/open_meteo/grid_cache/`, so rerunning `build_dashboard_data.py --grid-only` resumes. Bilbao has no soil probes yet (its soil moisture is the ERA5-Land model value) and no archived rain probabilities; Darrington has both.

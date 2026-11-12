"""Prepare the data files for the local dashboard (ML/dashboard).

    python ML/src/fetch_site_weather.py bilbao 43.263 -2.935      # slow, cached
    python ML/src/fetch_site_weather.py darrington 48.54 -121.45
    python ML/src/build_dataset.py
    python ML/src/build_dashboard_data.py            # add --grid-only to refresh just the forecast map

Writes into data/dashboard/app/:
  meta.json              grid axes, variables, terrain bounds, build time
  grid.bin               float32 [variable][time][row north->south][col west->east]
  dem.bin                int16 terrain heights, Web Mercator raster
  series_bilbao.json     hourly record: observed (ERA5 model) + forecasts as issued
  series_darrington.json hourly record: USCRN station + model soil moisture + forecasts
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from connectors import open_meteo, terrain

DATA = Path(__file__).resolve().parents[2] / "data"
RAW = DATA / "raw" / "open_meteo"
OUT = DATA / "dashboard" / "app"
SITE = {"name": "Bilbao", "lat": 43.263, "lon": -2.935}
WEST, EAST, SOUTH, NORTH = -3.26, -2.62, 43.08, 43.44
GRID_NX, GRID_NY = 27, 19   # about 2 km spacing
GRID_VARIABLES = [
    "temperature_2m", "relative_humidity_2m", "vapour_pressure_deficit", "precipitation",
    "precipitation_probability", "et0_fao_evapotranspiration", "shortwave_radiation", "cloud_cover",
    "wind_speed_10m", "wind_direction_10m", "soil_moisture_3_to_9cm", "soil_moisture_9_to_27cm",
    "soil_moisture_27_to_81cm", "soil_temperature_6cm",
]


def series_json(frame: pd.DataFrame, path: Path, decimals: int = 3) -> None:
    frame = frame.asfreq("h")
    cols = {}
    for name in frame.columns:
        values = frame[name].astype(float).round(decimals)
        cols[name] = [None if np.isnan(v) else v for v in values.to_numpy()]
    payload = {"t0": int(frame.index[0].timestamp()), "dt": 3600, "n": len(frame), "cols": cols}
    path.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"{path.name}: {len(frame):,} hours x {len(cols)} columns, {path.stat().st_size / 1e6:.1f} MB")


def bilbao_series(now: pd.Timestamp) -> pd.DataFrame:
    obs = pd.read_parquet(RAW / "bilbao_observed.parquet").dropna(subset=["temperature_2m"]).loc[:now]
    issued = pd.read_parquet(RAW / "bilbao_issued.parquet").dropna(axis=1, how="all").add_prefix("fc_")
    return obs.join(issued, how="left")


def darrington_series() -> pd.DataFrame:
    station = pd.read_parquet(DATA / "processed" / "WA_Darrington_21_NNE_hourly.parquet")
    model = pd.read_parquet(RAW / "darrington_observed.parquet")
    model = model[[c for c in model.columns if c.startswith("soil_") or c == "vapour_pressure_deficit"]].add_prefix("om_")
    return station.join(model, how="left")


def forecast_grid(stamp: str) -> tuple[np.ndarray, dict]:
    """Fetch the forecast cube in chunks, caching each chunk on disk so a run
    stopped by Open-Meteo's hourly limit resumes where it left off."""
    lons = np.linspace(WEST, EAST, GRID_NX)
    lats = np.linspace(NORTH, SOUTH, GRID_NY)          # north first
    grid_lat, grid_lon = np.meshgrid(lats, lons, indexing="ij")
    flat_lat, flat_lon = grid_lat.ravel().round(4).tolist(), grid_lon.ravel().round(4).tolist()
    cache = RAW / "grid_cache" / stamp
    cache.mkdir(parents=True, exist_ok=True)
    chunk, parts = 40, []
    for i in range(0, len(flat_lat), chunk):
        part = cache / f"{i:04d}.parquet"
        if not part.exists():
            frame = open_meteo.forecast_grid(flat_lat[i:i + chunk], flat_lon[i:i + chunk], days=7, past_days=2,
                                             variables=GRID_VARIABLES, chunk=chunk)
            frame.to_parquet(part)
            print(f"  grid points {i}-{i + len(frame) // frame['time'].nunique() - 1} fetched", flush=True)
            time.sleep(4)
        parts.append(pd.read_parquet(part))
    long = pd.concat(parts, ignore_index=True)
    times = np.sort(long["time"].unique())
    cube = np.full((len(GRID_VARIABLES), len(times), GRID_NY, GRID_NX), np.nan, dtype="<f4")
    n_points = GRID_NY * GRID_NX
    for v, name in enumerate(GRID_VARIABLES):
        # rows arrive point by point in request order, each with the full time axis
        cube[v] = long[name].to_numpy(dtype="float32").reshape(n_points, len(times)).T.reshape(len(times), GRID_NY, GRID_NX)
    meta = {"lats": lats.round(4).tolist(), "lons": lons.round(4).tolist(),
            "times": [int(pd.Timestamp(t).timestamp()) for t in times], "variables": GRID_VARIABLES}
    return cube, meta


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").floor("h")
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update({"built_at": int(now.timestamp()), "site": SITE})
    if "--grid-only" not in sys.argv:
        if not (OUT / "dem.bin").exists() or "dem" not in meta:
            heights, meta["dem"] = terrain.elevation_raster(WEST, SOUTH, EAST, NORTH, zoom=11)
            (OUT / "dem.bin").write_bytes(heights.tobytes())
            print(f"dem.bin: {heights.shape} int16, max {heights.max()} m")
        series_json(bilbao_series(now), OUT / "series_bilbao.json")
        series_json(darrington_series(), OUT / "series_darrington.json")
        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    # the forecast map: cached per day so an interrupted run can resume
    try:
        cube, meta["grid"] = forecast_grid(now.strftime("%Y%m%d"))
        (OUT / "grid.bin").write_bytes(cube.tobytes())
        meta["grid_built_at"] = int(now.timestamp())
        print(f"grid.bin: {cube.shape} float32, {cube.nbytes / 1e6:.1f} MB, {np.isnan(cube).mean():.1%} missing")
    except RuntimeError as exc:
        print(f"Forecast grid incomplete ({exc}). Run again later; finished chunks are kept.")
    meta_path.write_text(json.dumps(meta, separators=(",", ":")))
    print("meta.json written")


if __name__ == "__main__":
    main()

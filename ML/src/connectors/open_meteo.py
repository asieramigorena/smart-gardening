"""
open_meteo.py
Connecting handlers for the Open-Meteo API, including observed weather, as-issued forecasts, and terrain elevation.

Written by Diego Revilla on 12-04-2026
Copyright University of Deusto
"""

from __future__ import annotations
from pathlib import Path

import time

import pandas as pd
from dotenv import dotenv_values

from . import utils

class OpenMeteoError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"Open-Meteo request failed (HTTP {self.status_code}): {self.args[0]}"
        return f"Open-Meteo request failed: {self.args[0]}"

class OpenMeteoDataset():
    """A dataset containing weather data from the Open-Meteo API."""

    OBSERVED_VARIABLES = [
        "temperature_2m", "relative_humidity_2m", "wind_speed_10m", "surface_pressure",
        "shortwave_radiation", "precipitation", "et0_fao_evapotranspiration",
    ]
    FORECAST_VARIABLES = [
        "precipitation", "precipitation_probability", "et0_fao_evapotranspiration",
        "shortwave_radiation", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
    ]

    @staticmethod
    def _fetch_hourly(url: str, params: dict) -> pd.DataFrame:
        params = {**params, "timezone": "GMT", "wind_speed_unit": "ms"}
        payload = None
        for attempt in range(4):  # gateways sometimes answer a long request with a non-JSON error page
            resp = utils.get(url, params=params, timeout=180)
            try:
                payload = resp.json()
                break
            except ValueError:
                time.sleep(5 * (attempt + 1))
        if payload is None:
            raise RuntimeError(f"Open-Meteo returned no JSON (HTTP {resp.status_code}) for {url}")
        if resp.status_code != 200 or payload.get("error"):
            raise RuntimeError(f"Open-Meteo request failed: {payload.get('reason', resp.status_code)}")
        hourly = payload["hourly"]
        index = pd.DatetimeIndex(pd.to_datetime(hourly.pop("time"), utc=True), name="time")
        df = pd.DataFrame(hourly, index=index).astype(float)
        df.attrs = {"latitude": payload["latitude"], "longitude": payload["longitude"],
                 "elevation": payload.get("elevation")}
        return df
    
    
    @staticmethod
    def _yearly_chunks(start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        for year in range(start.year, end.year + 1):
            yield (max(start, pd.Timestamp(year, 1, 1)).date().isoformat(),
                   min(end, pd.Timestamp(year, 12, 31)).date().isoformat())
    
    @staticmethod
    def observed(lat: float, lon: float, start, end, variables: list[str] | None = None) -> pd.DataFrame:
        """Observed past weather from the ERA5-based archive (about 5 days behind
        real time). This is a gridded model product, not a station measurement:
        use it to fill variables a station lacks (wind, pressure, ET0)."""
        variables = variables or OpenMeteoDataset.OBSERVED_VARIABLES
        frames = [
            OpenMeteoDataset._fetch_hourly(dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("ARCHIVE_URL"), {"latitude": lat, "longitude": lon, "start_date": s,
                                        "end_date": e, "hourly": ",".join(variables)})
            for s, e in OpenMeteoDataset._yearly_chunks(start, end)
        ]
        return pd.concat(frames).sort_index()
    
    @staticmethod
    def forecast_as_issued(lat: float, lon: float, start, end, variables: list[str] | None = None,
                           lead_days: tuple[int, ...] = (1, 2, 3)) -> pd.DataFrame:
        variables = variables or OpenMeteoDataset.FORECAST_VARIABLES
        requested = [f"{v}_previous_day{n}" for v in variables for n in lead_days]
        frames = [
            OpenMeteoDataset._fetch_hourly(dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("PREVIOUS_RUNS_URL"), {"latitude": lat, "longitude": lon, "start_date": s,
                                              "end_date": e, "hourly": ",".join(requested)})
            for s, e in OpenMeteoDataset._yearly_chunks(start, end)
        ]
        df = pd.concat(frames).sort_index()
        return df.rename(columns=lambda c: c.replace("_previous_day", "_lead") + "d")
    
    @staticmethod
    def forecast(lat: float, lon: float, days: int = 7, past_days: int = 0,
                 variables: list[str] | None = None) -> pd.DataFrame:
        """Live hourly forecast for the next ``days`` days (up to 16)."""
        variables = variables or OpenMeteoDataset.FORECAST_VARIABLES
        return OpenMeteoDataset._fetch_hourly(dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("FORECAST_URL"), {"latitude": lat, "longitude": lon, "forecast_days": days,
                                            "past_days": past_days, "hourly": ",".join(variables)})
    
    
    ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
    GRID_VARIABLES = [
        "precipitation", "temperature_2m", "et0_fao_evapotranspiration",
        "shortwave_radiation", "soil_moisture_9_to_27cm", "wind_speed_10m",
    ]
    
    
    @staticmethod
    def elevation(lats: list[float], lons: list[float]) -> list[float]:
        """Terrain height in metres (Copernicus 90 m DEM) for paired coordinates."""
        out: list[float] = []
        for i in range(0, len(lats), 100):
            resp = utils.get(OpenMeteoDataset.ELEVATION_URL, params={
                "latitude": ",".join(f"{v:.4f}" for v in lats[i:i + 100]),
                "longitude": ",".join(f"{v:.4f}" for v in lons[i:i + 100]),
            })
            payload = resp.json()
            if resp.status_code != 200 or payload.get("error"):
                raise RuntimeError(f"Open-Meteo elevation failed: {payload.get('reason', resp.status_code)}")
            out.extend(payload["elevation"])
        return out
    
    @staticmethod
    def forecast_grid(lats: list[float], lons: list[float], days: int = 7,
                      variables: list[str] | None = None, past_days: int = 0,
                      chunk: int = 40, pause: float = 6.0) -> pd.DataFrame:
        """Live hourly forecast for many points at once (paired coordinates).
        Returns a long frame with columns latitude, longitude, time and one
        column per variable; latitude and longitude are the requested values.
        Requests are paced to stay inside the free tier's per-minute limit."""
        variables = variables or OpenMeteoDataset.GRID_VARIABLES
        frames = []
        for i in range(0, len(lats), chunk):
            if i:
                time.sleep(pause)
            chunk_lats, chunk_lons = lats[i:i + chunk], lons[i:i + chunk]
            resp = utils.get(dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("FORECAST_URL"), params={
                "latitude": ",".join(f"{v:.4f}" for v in chunk_lats),
                "longitude": ",".join(f"{v:.4f}" for v in chunk_lons),
                "forecast_days": days, "past_days": past_days, "hourly": ",".join(variables),
                "timezone": "GMT", "wind_speed_unit": "ms",
            }, timeout=120)
            payload = resp.json()
            if resp.status_code != 200 or (isinstance(payload, dict) and payload.get("error")):
                raise RuntimeError(f"Open-Meteo grid forecast failed: {payload.get('reason', resp.status_code)}")
            points = payload if isinstance(payload, list) else [payload]
            for lat, lon, point in zip(chunk_lats, chunk_lons, points):
                frame = pd.DataFrame(point["hourly"])
                frame["time"] = pd.to_datetime(frame["time"], utc=True)
                frame.insert(0, "longitude", lon)
                frame.insert(0, "latitude", lat)
                frames.append(frame)
        return pd.concat(frames, ignore_index=True)
    
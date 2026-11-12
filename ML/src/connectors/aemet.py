"""
aemet.py
Connecting handlers for the AEMET API, including municipal forecasts and station observations.

Written by Diego Revilla on 12-04-2026
Copyright University of Deusto
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from . import utils

BASE_URL = "https://opendata.aemet.es/opendata/api"
LOCAL_TZ = "Europe/Madrid"
BILBAO_MUNICIPALITY = "48020"
BILBAO_AIRPORT_STATION = "1082"



def _api_key() -> str:
    key = os.environ.get("AEMET_KEY")
    if not key:
        try:
            env_file = Path(__file__).resolve().parents[3] / ".env"
            key = dotenv_values(env_file).get("AEMET_KEY")
        except ImportError:
            key = None
    if not key:
        raise RuntimeError("AEMET_KEY is not set (environment variable or .env file)")
    return key


def _get(path: str):
    resp = utils.get(f"{BASE_URL}/{path}", headers={"api_key": _api_key()})
    resp.raise_for_status()
    envelope = resp.json()
    if envelope.get("estado") != 200 or "datos" not in envelope:
        raise RuntimeError(f"AEMET request failed: {envelope.get('descripcion', envelope)}")
    data = utils.get(envelope["datos"])
    data.raise_for_status()
    return json.loads(data.content.decode("latin-1"))


def _number(value) -> float:
    """AEMET uses 'Ip' for trace precipitation and '' for no value."""
    if value in ("Ip", "ip"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def hourly_forecast(municipality: str = dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("MUNICIPALITY")) -> pd.DataFrame:
    """Official hourly forecast for a municipality (INE code), about 48-72 h.

    Precipitation is the sum over the hour ENDING at the timestamp, to match
    the other connectors. Probabilities come in 6-hour blocks and are repeated
    on every hour of their block.
    """
    days = _get(f"prediccion/especifica/municipio/horaria/{municipality}")[0]["prediccion"]["dia"]
    hourly: dict[str, dict[pd.Timestamp, float]] = {}
    blocks: dict[str, dict[pd.Timestamp, float]] = {}
    print("hourly_forecast: got", len(days), "days of forecast for municipality", municipality)

    def local(day: pd.Timestamp, hour: int) -> pd.Timestamp:
        return (day + pd.Timedelta(hours=hour)).tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT")

    for day in days:
        date = pd.Timestamp(day["fecha"])
        # values valid AT the hour
        for field, name in (("temperatura", "temperature_c"), ("humedadRelativa", "rh_pct")):
            for item in day.get(field, []):
                hourly.setdefault(name, {})[local(date, int(item["periodo"]))] = _number(item["value"])
        # sums over the hour that STARTS at 'periodo': shift to end of hour
        for field, name in (("precipitacion", "precip_mm"), ("nieve", "snow_mm")):
            for item in day.get(field, []):
                hourly.setdefault(name, {})[local(date, int(item["periodo"]) + 1)] = _number(item["value"])
        for item in day.get("vientoAndRachaMax", []):
            when = local(date, int(item["periodo"]))
            if "velocidad" in item:
                hourly.setdefault("wind_speed_ms", {})[when] = _number(item["velocidad"][0]) / 3.6
            elif "value" in item:
                hourly.setdefault("wind_gust_ms", {})[when] = _number(item["value"]) / 3.6
        for field, name in (("probPrecipitacion", "precip_prob_pct"), ("probTormenta", "storm_prob_pct")):
            for item in day.get(field, []):
                period = item["periodo"]
                first, last = int(period[:2]), int(period[2:])
                span = (last - first) % 24 or 24
                for offset in range(1, span + 1):  # hours ending inside the block
                    blocks.setdefault(name, {})[local(date, first + offset)] = _number(item["value"])

    df = pd.DataFrame({**hourly, **blocks})
    df = df[df.index.notna()]
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC").rename("time")
    return df.sort_index()


_OBSERVATION_FIELDS = {
    "ta": "temperature_c", "hr": "rh_pct", "prec": "precip_mm", "vv": "wind_speed_ms",
    "vmax": "wind_gust_ms", "pres": "pressure_hpa", "ts": "soil_surface_temp_c",
    "inso": "sunshine_min",
}


def station_observations(station: str = dotenv_values(Path(__file__).resolve().parents[3] / ".env").get("STATION")) -> pd.DataFrame:
    """Hourly observations from one AEMET station (IDEMA code) for roughly the
    last 12-24 hours. Call it on a schedule and append to build a history.
    Columns depend on what the station measures."""
    rows = _get(f"observacion/convencional/datos/estacion/{station}")
    df = pd.DataFrame(rows)
    index = pd.DatetimeIndex(pd.to_datetime(df["fint"], utc=True), name="time")
    present = {src: dst for src, dst in _OBSERVATION_FIELDS.items() if src in df.columns}
    out = df[list(present)].rename(columns=present).apply(pd.to_numeric, errors="coerce")
    out.index = index
    return out.sort_index()

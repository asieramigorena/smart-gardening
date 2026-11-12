"""USCRN hourly station data (NOAA): observed weather plus soil moisture and
soil temperature at 5, 10, 20, 50 and 100 cm.

Format reference: ML/docs/readme-hourly-uscrn-v2.1.man. The hourly product has
no wind or pressure, so ET0 inputs must be completed from another source
(see open_meteo.observed).
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from . import utils

BASE_URL = "https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02"

RAW_COLUMNS = [
    "WBANNO", "UTC_DATE", "UTC_TIME", "LST_DATE", "LST_TIME", "CRX_VN",
    "LONGITUDE", "LATITUDE", "T_CALC", "T_HR_AVG", "T_MAX", "T_MIN", "P_CALC",
    "SOLARAD", "SOLARAD_FLAG", "SOLARAD_MAX", "SOLARAD_MAX_FLAG",
    "SOLARAD_MIN", "SOLARAD_MIN_FLAG", "SUR_TEMP_TYPE", "SUR_TEMP",
    "SUR_TEMP_FLAG", "SUR_TEMP_MAX", "SUR_TEMP_MAX_FLAG", "SUR_TEMP_MIN",
    "SUR_TEMP_MIN_FLAG", "RH_HR_AVG", "RH_HR_AVG_FLAG",
    "SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "SOIL_MOISTURE_20",
    "SOIL_MOISTURE_50", "SOIL_MOISTURE_100",
    "SOIL_TEMP_5", "SOIL_TEMP_10", "SOIL_TEMP_20", "SOIL_TEMP_50", "SOIL_TEMP_100",
]
_TEXT_COLUMNS = ["WBANNO", "UTC_DATE", "UTC_TIME", "LST_DATE", "LST_TIME", "CRX_VN", "SUR_TEMP_TYPE"]

# raw name -> (tidy name, QC flag column or None)
_MEASUREMENTS = {
    "T_HR_AVG": ("air_temp_c", None),
    "T_MAX": ("air_temp_max_c", None),
    "T_MIN": ("air_temp_min_c", None),
    "P_CALC": ("precip_mm", None),
    "SOLARAD": ("solar_rad_wm2", "SOLARAD_FLAG"),
    "SUR_TEMP": ("surface_temp_c", "SUR_TEMP_FLAG"),
    "RH_HR_AVG": ("rh_pct", "RH_HR_AVG_FLAG"),
    **{f"SOIL_MOISTURE_{d}": (f"soil_moisture_{d}cm", None) for d in (5, 10, 20, 50, 100)},
    **{f"SOIL_TEMP_{d}": (f"soil_temp_{d}cm", None) for d in (5, 10, 20, 50, 100)},
}


def filename(station: str, year: int) -> str:
    return f"CRNH0203-{year}-{station}.txt"


def download(station: str, year: int, dest_dir: Path | str, *, refresh: bool | None = None) -> Path:
    """Download one station-year file. By default an existing file is reused,
    except for the current year, which is refreshed when older than 12 hours."""
    dest = Path(dest_dir) / filename(station, year)
    if dest.exists():
        if refresh is None:
            is_current_year = year == pd.Timestamp.now(tz="UTC").year
            refresh = is_current_year and (time.time() - dest.stat().st_mtime) > 12 * 3600
        if not refresh:
            return dest
    resp = utils.get(f"{BASE_URL}/{year}/{filename(station, year)}")
    if resp.status_code == 404:
        raise FileNotFoundError(f"No USCRN file for station {station!r} in {year}")
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def read_file(path: Path | str) -> pd.DataFrame:
    """Parse one station-year file into a tidy hourly frame (UTC, end of hour).

    Missing codes (-9999, -99) and values with a non-zero QC flag become NaN.
    """
    raw = pd.read_csv(path, sep=r"\s+", header=None, names=RAW_COLUMNS,
                      dtype={c: str for c in _TEXT_COLUMNS})
    index = pd.to_datetime(raw["UTC_DATE"] + raw["UTC_TIME"], format="%Y%m%d%H%M", utc=True)
    out = pd.DataFrame(index=pd.DatetimeIndex(index, name="time"))
    for raw_name, (tidy_name, flag) in _MEASUREMENTS.items():
        values = raw[raw_name].astype(float).where(raw[raw_name] > -99)
        if flag is not None:
            values = values.where(raw[flag] == 0)
        out[tidy_name] = values.to_numpy()
    out.attrs = {
        "latitude": float(raw["LATITUDE"].iloc[-1]),
        "longitude": float(raw["LONGITUDE"].iloc[-1]),
    }
    return out


def load(station: str, years: list[int], cache_dir: Path | str) -> pd.DataFrame:
    """Download (if needed) and concatenate several years for one station.

    The result has a gap-free hourly index, so missing hours show up as NaN
    rows. Latitude and longitude are in ``df.attrs``.

    Soil moisture probes read falsely low in frozen soil: mask rows where the
    matching soil temperature is at or below about 0.5 C before training.
    """
    frames = [read_file(download(station, y, cache_dir)) for y in sorted(years)]
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    last_observed = df.dropna(how="all").index.max()
    df = df.loc[:last_observed].asfreq("h")
    df.attrs = {**frames[-1].attrs, "station": station}
    return df

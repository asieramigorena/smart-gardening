"""Assemble the hourly training table for one USCRN station.

    python ML/src/build_dataset.py                       # Darrington, 2024 to now
    python ML/src/build_dataset.py --years 2025 2026

Joins, on the UTC hour:
  - station measurements (soil moisture and temperature by depth, rain,
    radiation, air temperature, humidity)               no prefix
  - Open-Meteo observed weather for what the station lacks (wind, pressure,
    ET0)                                                 prefix om_
  - Open-Meteo forecasts as issued 1, 2 and 3 days ahead  prefix fc_
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from connectors import open_meteo, uscrn

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRST_FORECAST_YEAR = 2026  # Open-Meteo's archive of past forecast runs starts here


def build(station: str, years: list[int], data_dir: Path) -> pd.DataFrame:
    station_df = uscrn.load(station, years, data_dir / "raw" / "uscrn")
    lat, lon = station_df.attrs["latitude"], station_df.attrs["longitude"]
    start, end = station_df.index.min().date(), station_df.index.max().date()

    observed = open_meteo.observed(lat, lon, start, end).add_prefix("om_")
    table = station_df.join(observed, how="left")

    forecast_start = max(pd.Timestamp(start), pd.Timestamp(FIRST_FORECAST_YEAR, 1, 1)).date()
    if forecast_start <= end:
        issued = open_meteo.forecast_as_issued(lat, lon, forecast_start, end).add_prefix("fc_")
        table = table.join(issued, how="left")

    table.attrs = station_df.attrs
    return table


def main() -> None:
    this_year = pd.Timestamp.now(tz="UTC").year
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--station", default="WA_Darrington_21_NNE")
    parser.add_argument("--years", type=int, nargs="+", default=list(range(FIRST_FORECAST_YEAR, this_year + 1)))
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    table = build(args.station, args.years, args.data_dir)
    out = args.data_dir / "processed" / f"{args.station}_hourly.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out)

    print(f"{out}: {len(table):,} hourly rows, {table.index.min()} to {table.index.max()}")
    coverage = (table.notna().mean() * 100).round(1)
    print("Coverage (% of hours with a value):")
    print(coverage.to_string())


if __name__ == "__main__":
    main()

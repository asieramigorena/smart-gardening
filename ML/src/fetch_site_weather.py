"""Cache Open-Meteo observed weather and as-issued forecasts for a point.

    python ML/src/fetch_site_weather.py bilbao 43.263 -2.935

Writes data/raw/open_meteo/<name>_observed.parquet and <name>_issued.parquet.
Observed includes ERA5-Land modelled soil moisture and soil temperature, the
only soil information available where there is no station.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from connectors.open_meteo import OpenMeteoDataset

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "open_meteo"
START = "2024-01-01"
OBSERVED = OpenMeteoDataset.OBSERVED_VARIABLES + [
    "vapour_pressure_deficit", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm",
    "soil_moisture_28_to_100cm", "soil_temperature_0_to_7cm", "soil_temperature_28_to_100cm",
]


def main() -> None:
    name, lat, lon = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    end = pd.Timestamp.now(tz="UTC").date().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = {
        "observed": lambda: OpenMeteoDataset.observed(lat, lon, START, end, OBSERVED),
        "issued": lambda: OpenMeteoDataset.forecast_as_issued(lat, lon, START, end),
    }
    for kind, fetch in jobs.items():
        path = OUT_DIR / f"{name}_{kind}.parquet"
        if path.exists() and "--refresh" not in sys.argv:
            print(name, kind, "cached (pass --refresh to download again)", flush=True)
            continue
        frame = fetch()
        frame.to_parquet(path)
        print(name, kind, frame.shape, flush=True)


if __name__ == "__main__":
    main()

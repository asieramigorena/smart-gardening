"""
config.py
Loads the garden description (ML/config/garden.toml) into Zone objects and
tells the training pipeline where each zone's hourly data lives.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .catalogue import PLANTS, SOILS
from .errors import ConfigError, GardenError
from .plant import Planting
from .zone import (GeoPoint, IrrigationMethod, IrrigationSystem, ProbeArray, SoilProbe, Zone,
                   ZoneRole)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "ML" / "config" / "garden.toml"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherChannels:
    """Column names of the weather inputs in a zone's hourly table.

    Observed values cover the hour ending at the timestamp. Forecasts follow
    the ``<prefix><variable>_lead<N>d`` convention of build_dataset.py."""

    rain_mm: str
    et0_mm: str
    radiation_w_m2: str
    forecast_prefix: str = "fc_"
    forecast_rain_mm: str = "precipitation"
    forecast_rain_probability_pct: str = "precipitation_probability"
    forecast_et0_mm: str = "et0_fao_evapotranspiration"
    forecast_radiation_w_m2: str = "shortwave_radiation"


@dataclass(frozen=True, slots=True, kw_only=True)
class DataBinding:
    """Where a zone's hourly table is and how to read it."""

    table: Path
    weather: WeatherChannels
    calibrate_soil: bool = False
    irrigation_log: Path | None = None


class Garden(Mapping[str, Zone]):
    """All configured zones, plus the data binding of those that have data."""

    def __init__(self, zones: dict[str, Zone], bindings: dict[str, DataBinding], source: Path):
        self._zones, self._bindings, self.source = zones, bindings, source

    def __getitem__(self, key: str) -> Zone:
        if key not in self._zones:
            raise ConfigError(str(self.source), f"no zone {key!r}; zones: {', '.join(self._zones)}")
        return self._zones[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._zones)

    def __len__(self) -> int:
        return len(self._zones)

    def binding(self, key: str) -> DataBinding:
        self[key]  # raises for unknown zones
        if key not in self._bindings:
            raise ConfigError(str(self.source), f"zone {key!r} has no [zones.{key}.data] table")
        return self._bindings[key]

    def with_data(self) -> list[str]:
        return list(self._bindings)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG) -> Garden:
        return _ConfigReader(Path(path)).read()


class _ConfigReader:
    """Turns the TOML document into validated domain objects. Every error
    names the zone and field so a typo is easy to find."""

    def __init__(self, path: Path):
        self.path = path

    def fail(self, where: str, message: str) -> ConfigError:
        return ConfigError(str(self.path), f"{where}: {message}")

    def read(self) -> Garden:
        try:
            doc = tomllib.loads(self.path.read_text())
        except FileNotFoundError:
            raise ConfigError(str(self.path), "file not found") from None
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(str(self.path), f"invalid TOML: {exc}") from None
        if doc.get("schema_version") != SCHEMA_VERSION:
            raise self.fail("schema_version", f"expected {SCHEMA_VERSION}, got {doc.get('schema_version')!r}")
        defaults = doc.get("defaults", {})
        zones, bindings = {}, {}
        for key, spec in doc.get("zones", {}).items():
            where = f"zones.{key}"
            try:
                zones[key] = self._zone(key, {**defaults, **spec})
            except ConfigError:
                raise
            except (GardenError, KeyError, TypeError, ValueError) as exc:
                raise self.fail(where, str(exc)) from exc
            if "data" in spec:
                bindings[key] = self._binding(where, spec["data"])
        if not zones:
            raise self.fail("zones", "no zones defined")
        return Garden(zones, bindings, self.path)

    @staticmethod
    def _require(spec: Mapping[str, Any], name: str, where: str):
        if name not in spec:
            raise KeyError(f"missing {where}.{name}")
        return spec[name]

    def _zone(self, key: str, spec: dict) -> Zone:
        req = self._require
        loc = req(spec, "location", "zone")
        soil_spec, plant_spec = req(spec, "soil", "zone"), req(spec, "planting", "zone")
        irrigation = None
        if spec.get("irrigation"):
            irr = spec["irrigation"]
            irrigation = IrrigationSystem(method=IrrigationMethod(req(irr, "method", "irrigation")),
                                          flow_l_h=float(req(irr, "flow_l_h", "irrigation")),
                                          application_efficiency=irr.get("application_efficiency"))
        season = plant_spec.get("season_start")
        planting = Planting(
            plant=PLANTS[req(plant_spec, "plant", "planting")],
            sown_on=date.fromisoformat(plant_spec["sown_on"]) if "sown_on" in plant_spec else None,
            season_start=tuple(int(x) for x in season.split("-")) if season else None,
        )
        probes = ProbeArray(SoilProbe(depth_cm=float(p["depth_cm"]), channel=p["channel"],
                                      temperature_channel=p.get("temperature_channel"))
                            for p in req(spec, "probes", "zone"))
        return Zone(key=key, name=spec.get("name", key), role=ZoneRole(spec.get("role", "deployment")),
                    area_m2=float(req(spec, "area_m2", "zone")),
                    location=GeoPoint(latitude=float(req(loc, "lat", "location")),
                                      longitude=float(req(loc, "lon", "location")),
                                      elevation_m=loc.get("elevation_m")),
                    timezone=req(spec, "timezone", "zone"), soil=SOILS[req(soil_spec, "texture", "soil")],
                    planting=planting, probes=probes, irrigation=irrigation,
                    soil_depth_m=soil_spec.get("depth_m"), notes=spec.get("notes", ""))

    def _binding(self, where: str, spec: dict) -> DataBinding:
        try:
            weather = WeatherChannels(**spec["weather"])
            table = REPO_ROOT / spec["table"]
            log = REPO_ROOT / spec["irrigation_log"] if spec.get("irrigation_log") else None
        except KeyError as exc:
            raise self.fail(f"{where}.data", f"missing {exc}") from None
        except TypeError as exc:
            raise self.fail(f"{where}.data.weather", str(exc)) from None
        return DataBinding(table=table, weather=weather, calibrate_soil=bool(spec.get("calibrate_soil")),
                           irrigation_log=log)

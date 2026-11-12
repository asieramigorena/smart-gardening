"""
garden
Domain model of the irrigated garden: soils, plants, plantings and zones.

    from garden import Garden
    garden = Garden.load()               # ML/config/garden.toml
    zone = garden["bilbao_tomatoes"]
    zone.valve_open_seconds(theta=0.18, root_depth_m=zone.root_depth_m(index))

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from .catalogue import PLANTS, SOILS, Catalogue
from .config import DEFAULT_CONFIG, DataBinding, Garden, WeatherChannels
from .errors import CatalogueError, ConfigError, DataError, GardenError, ValidationError
from .plant import CropCoefficients, GrowthStage, Plant, Planting, StageLengths
from .soil import Soil, SoilCalibration
from .zone import (GeoPoint, IrrigationEvent, IrrigationLog, IrrigationMethod, IrrigationSystem,
                   ProbeArray, SoilProbe, Zone, ZoneRole)

__all__ = [
    "PLANTS", "SOILS", "Catalogue", "DEFAULT_CONFIG", "DataBinding", "Garden", "WeatherChannels",
    "CatalogueError", "ConfigError", "DataError", "GardenError", "ValidationError",
    "CropCoefficients", "GrowthStage", "Plant", "Planting", "StageLengths", "Soil", "SoilCalibration",
    "GeoPoint", "IrrigationEvent", "IrrigationLog", "IrrigationMethod", "IrrigationSystem",
    "ProbeArray", "SoilProbe", "Zone", "ZoneRole",
]

"""
catalogue.py
Reference soils and plants. Zones refer to these by key, so every bed that
says "sandy_loam" or "tomato" gets exactly the same parameters.

Soil limits are the midpoints of the ranges in FAO-56 table 19; saturation
and saturated conductivity are from Rawls, Brakensiek & Saxton (1982); intake
rates are the basic infiltration rates of FAO Irrigation Water Management
manual 2. Crop values are from FAO-56 tables 11 (stage lengths, spring
planting in a temperate climate), 12 (Kc) and 22 (root depth, p).

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Generic, Protocol, TypeVar

from .errors import CatalogueError
from .plant import CropCoefficients, Plant, StageLengths
from .soil import Soil


class Keyed(Protocol):
    key: str


T = TypeVar("T", bound=Keyed)


class Catalogue(Mapping[str, T], Generic[T]):
    """Read-only registry of domain objects indexed by their ``key``."""

    def __init__(self, kind: str, items: list[T]):
        self.kind = kind
        self._items: dict[str, T] = {}
        for item in items:
            if item.key in self._items:
                raise ValueError(f"duplicate {kind} key {item.key!r}")
            self._items[item.key] = item

    def __getitem__(self, key: str) -> T:
        try:
            return self._items[key]
        except KeyError:
            raise CatalogueError(self.kind, key, list(self._items)) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"Catalogue({self.kind!r}, {list(self._items)})"


_SOIL_SOURCE = "FAO-56 table 19 midpoints; Rawls et al. (1982); FAO IWM manual 2"

SOILS: Catalogue[Soil] = Catalogue("soil", [
    Soil(key="sand", name="Sand", theta_sat=0.437, theta_fc=0.12, theta_wp=0.045,
         ksat_mm_h=210.0, intake_rate_mm_h=30.0, source=_SOIL_SOURCE),
    Soil(key="loamy_sand", name="Loamy sand", theta_sat=0.437, theta_fc=0.15, theta_wp=0.065,
         ksat_mm_h=61.1, intake_rate_mm_h=25.0, source=_SOIL_SOURCE),
    Soil(key="sandy_loam", name="Sandy loam", theta_sat=0.453, theta_fc=0.23, theta_wp=0.11,
         ksat_mm_h=25.9, intake_rate_mm_h=20.0, source=_SOIL_SOURCE),
    Soil(key="loam", name="Loam", theta_sat=0.463, theta_fc=0.25, theta_wp=0.12,
         ksat_mm_h=13.2, intake_rate_mm_h=12.0, source=_SOIL_SOURCE),
    Soil(key="silt_loam", name="Silt loam", theta_sat=0.501, theta_fc=0.29, theta_wp=0.15,
         ksat_mm_h=6.8, intake_rate_mm_h=10.0, source=_SOIL_SOURCE),
    Soil(key="silty_clay_loam", name="Silty clay loam", theta_sat=0.471, theta_fc=0.335, theta_wp=0.205,
         ksat_mm_h=2.3, intake_rate_mm_h=6.0, source=_SOIL_SOURCE),
    Soil(key="silty_clay", name="Silty clay", theta_sat=0.479, theta_fc=0.36, theta_wp=0.23,
         ksat_mm_h=1.2, intake_rate_mm_h=4.0, source=_SOIL_SOURCE),
    Soil(key="clay", name="Clay", theta_sat=0.475, theta_fc=0.36, theta_wp=0.22,
         ksat_mm_h=0.6, intake_rate_mm_h=3.0, source=_SOIL_SOURCE),
    # Raised beds are usually filled with a peat or compost blend: high
    # porosity and holding capacity. Typical values for a 1:1 loam-compost mix.
    Soil(key="raised_bed_mix", name="Raised-bed mix (loam + compost)", theta_sat=0.60, theta_fc=0.35,
         theta_wp=0.12, ksat_mm_h=40.0, intake_rate_mm_h=25.0,
         source="typical values for a loam-compost blend; measure if possible"),
])

PLANTS: Catalogue[Plant] = Catalogue("plant", [
    Plant(key="tomato", name="Tomato", kc=CropCoefficients(0.60, 1.15, 0.80),
          stages=StageLengths(30, 40, 40, 25), root_depth_min_m=0.15, root_depth_max_m=1.0,
          depletion_fraction=0.40, max_height_m=0.6),
    Plant(key="lettuce", name="Lettuce", kc=CropCoefficients(0.70, 1.00, 0.95),
          stages=StageLengths(20, 30, 15, 10), root_depth_min_m=0.10, root_depth_max_m=0.40,
          depletion_fraction=0.30, max_height_m=0.3),
    Plant(key="pepper", name="Sweet pepper", kc=CropCoefficients(0.60, 1.05, 0.90),
          stages=StageLengths(25, 35, 40, 20), root_depth_min_m=0.15, root_depth_max_m=0.80,
          depletion_fraction=0.30, max_height_m=0.7),
    Plant(key="green_bean", name="Green bean", kc=CropCoefficients(0.50, 1.05, 0.90),
          stages=StageLengths(20, 30, 30, 10), root_depth_min_m=0.15, root_depth_max_m=0.60,
          depletion_fraction=0.45, max_height_m=0.4),
    Plant(key="zucchini", name="Zucchini", kc=CropCoefficients(0.50, 0.95, 0.75),
          stages=StageLengths(25, 35, 25, 15), root_depth_min_m=0.15, root_depth_max_m=0.80,
          depletion_fraction=0.50, max_height_m=0.3),
    Plant(key="carrot", name="Carrot", kc=CropCoefficients(0.70, 1.05, 0.95),
          stages=StageLengths(20, 30, 50, 20), root_depth_min_m=0.10, root_depth_max_m=0.75,
          depletion_fraction=0.35, max_height_m=0.3),
    Plant(key="potato", name="Potato", kc=CropCoefficients(0.50, 1.15, 0.75),
          stages=StageLengths(25, 30, 45, 30), root_depth_min_m=0.15, root_depth_max_m=0.50,
          depletion_fraction=0.35, max_height_m=0.6),
    Plant(key="strawberry", name="Strawberry", kc=CropCoefficients(0.40, 0.85, 0.75),
          stages=StageLengths(60, 60, 90, 60), root_depth_min_m=0.20, root_depth_max_m=0.30,
          depletion_fraction=0.20, max_height_m=0.2, perennial=True),
    # Cool-season turf (rye, bluegrass, fescue). Stage lengths here describe
    # spring green-up, summer, and autumn decline over one year from March.
    Plant(key="turfgrass_cool", name="Cool-season grass", kc=CropCoefficients(0.90, 0.95, 0.95),
          stages=StageLengths(30, 30, 150, 60), root_depth_min_m=0.50, root_depth_max_m=0.50,
          depletion_fraction=0.40, max_height_m=0.1, perennial=True),
])

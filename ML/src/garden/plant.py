"""
plant.py
Crop water-use parameters (FAO-56 single crop coefficient method) and the
planting that places a crop in time.

    ETc = Kc · ET₀                          crop water use (mm)
    Kc  follows the four-stage curve: flat at Kc_ini, linear rise during
        development, flat at Kc_mid, linear fall to Kc_end (FAO-56 fig. 25)
    Z_r grows linearly from Z_r,min at sowing to Z_r,max at the start of the
        mid-season stage (FAO-56 eq. 8-1 simplified)
    p   = p_table + 0.04 (5 − ETc[mm/day]), clipped to [0.1, 0.8]  (FAO-56 §8)

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd

from .errors import ValidationError


class GrowthStage(StrEnum):
    INITIAL = "initial"
    DEVELOPMENT = "development"
    MID = "mid"
    LATE = "late"
    FINISHED = "finished"  # after harvest (annuals) or end of season (perennials)


@dataclass(frozen=True, slots=True)
class StageLengths:
    """Length of each FAO-56 growth stage, in days."""

    initial: int
    development: int
    mid: int
    late: int

    def __post_init__(self) -> None:
        if min(self.initial, self.development, self.mid, self.late) < 0 or self.total == 0:
            raise ValidationError("StageLengths", f"stage lengths must be non-negative and not all zero: {self}")

    @property
    def total(self) -> int:
        return self.initial + self.development + self.mid + self.late

    @property
    def boundaries(self) -> tuple[int, int, int, int]:
        """Day on which development, mid, late and the end begin."""
        a = self.initial
        b = a + self.development
        c = b + self.mid
        return a, b, c, c + self.late


@dataclass(frozen=True, slots=True)
class CropCoefficients:
    initial: float
    mid: float
    end: float

    def __post_init__(self) -> None:
        if not all(0.0 < k <= 1.5 for k in (self.initial, self.mid, self.end)):
            raise ValidationError("CropCoefficients", f"Kc values must be in (0, 1.5]: {self}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Plant:
    """One crop's water-use profile. Values are FAO-56 tables 11, 12 and 22
    unless ``source`` says otherwise; adjust Kc_mid and Kc_end for climate
    (FAO-56 eq. 62) if humidity or wind differ much from RH_min 45 %, u₂ 2 m/s."""

    key: str
    name: str
    kc: CropCoefficients
    stages: StageLengths
    root_depth_min_m: float
    root_depth_max_m: float
    depletion_fraction: float
    max_height_m: float
    perennial: bool = False
    source: str = "FAO-56 tables 11, 12, 22"

    def __post_init__(self) -> None:
        if not 0.0 < self.root_depth_min_m <= self.root_depth_max_m <= 3.0:
            raise ValidationError(f"Plant {self.key!r}", "need 0 < Z_r,min ≤ Z_r,max ≤ 3 m")
        if not 0.05 <= self.depletion_fraction <= 0.8:
            raise ValidationError(f"Plant {self.key!r}", "depletion fraction p must be in [0.05, 0.8]")

    # All methods take a day (or an array of days) since sowing / season start.
    def stage(self, day) -> np.ndarray:
        a, b, c, d = self.stages.boundaries
        day = np.asarray(day, dtype=float)
        labels = np.array([s.value for s in GrowthStage])
        return labels[np.searchsorted([a, b, c, d], day, side="right")]

    def crop_coefficient(self, day) -> np.ndarray:
        a, b, c, d = self.stages.boundaries
        return np.interp(day, [0, a, b, c, d], [self.kc.initial, self.kc.initial, self.kc.mid, self.kc.mid, self.kc.end])

    def root_depth_m(self, day) -> np.ndarray:
        if self.perennial:
            return np.full(np.shape(day), self.root_depth_max_m, dtype=float)
        _, b, _, _ = self.stages.boundaries
        return np.interp(day, [0, b], [self.root_depth_min_m, self.root_depth_max_m])

    def adjusted_depletion_fraction(self, etc_mm_day) -> np.ndarray:
        """p corrected for the evaporative demand: plants stress earlier on hot days."""
        return np.clip(self.depletion_fraction + 0.04 * (5.0 - np.asarray(etc_mm_day, dtype=float)), 0.1, 0.8)

    def season_progress(self, day) -> np.ndarray:
        """0 at sowing, 1 at the end of the late stage (can exceed 1)."""
        return np.asarray(day, dtype=float) / self.stages.total

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "kc": vars_of(self.kc), "stages_days": vars_of(self.stages),
                "root_depth_m": [self.root_depth_min_m, self.root_depth_max_m],
                "depletion_fraction": self.depletion_fraction, "max_height_m": self.max_height_m,
                "perennial": self.perennial, "source": self.source}


@dataclass(frozen=True, slots=True, kw_only=True)
class Planting:
    """A plant placed in time.

    Annual crops count days from ``sown_on``. Perennials (turf, strawberries)
    restart their stage curve every year on ``season_start`` (month, day), so
    "days since planting" becomes "days into the current season".
    """

    plant: Plant
    sown_on: date | None = None
    season_start: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.plant.perennial and self.season_start is None:
            raise ValidationError(f"Planting of {self.plant.key!r}", "perennials need season_start (month, day)")
        if not self.plant.perennial and self.sown_on is None:
            raise ValidationError(f"Planting of {self.plant.key!r}", "annual crops need sown_on")

    def days_since_planting(self, times: pd.DatetimeIndex) -> np.ndarray:
        """Fractional days at each timestamp (local calendar for perennials).
        Negative before sowing for annual crops."""
        if not self.plant.perennial:
            origin = pd.Timestamp(self.sown_on, tz=times.tz)
            return ((times - origin) / pd.Timedelta(days=1)).to_numpy(dtype=float)
        month, day = self.season_start
        this_year = pd.to_datetime({"year": times.year, "month": month, "day": day}).dt.tz_localize(times.tz)
        this_year = pd.DatetimeIndex(this_year)
        last_year = this_year - pd.DateOffset(years=1)
        origin = np.where(times >= this_year, this_year, last_year)
        return ((times - pd.DatetimeIndex(origin)) / pd.Timedelta(days=1)).to_numpy(dtype=float)

    def to_dict(self) -> dict:
        return {"plant": self.plant.key, "sown_on": self.sown_on.isoformat() if self.sown_on else None,
                "season_start": list(self.season_start) if self.season_start else None}


def vars_of(obj) -> dict:
    """Field dict of a slotted dataclass (``vars`` does not work with slots)."""
    return {name: getattr(obj, name) for name in obj.__dataclass_fields__}

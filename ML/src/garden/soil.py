"""
soil.py
Soil hydraulic properties: the water-holding limits that turn a volumetric
moisture reading into "how much the plant can still use" (FAO-56 ch. 8).

    θ_sat  saturation (total porosity)
    θ_FC   field capacity, what the soil holds after free drainage (~2 days)
    θ_WP   permanent wilting point, below which roots cannot extract water
    TAW    total available water = 1000 (θ_FC − θ_WP) Z_r          [mm]
    RAW    readily available water = p · TAW                        [mm]
    θ_refill = θ_FC − p (θ_FC − θ_WP), the moisture at which stress starts

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .errors import DataError, ValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class Soil:
    """Hydraulic description of one soil. Moisture contents are volumetric
    (m³ of water per m³ of soil); rates are in mm of water per hour."""

    key: str
    name: str
    theta_sat: float
    theta_fc: float
    theta_wp: float
    ksat_mm_h: float
    intake_rate_mm_h: float
    source: str = ""

    def __post_init__(self) -> None:
        if not 0.0 < self.theta_wp < self.theta_fc < self.theta_sat < 1.0:
            raise ValidationError(
                f"Soil {self.key!r}",
                f"need 0 < θ_WP < θ_FC < θ_sat < 1, got {self.theta_wp}, {self.theta_fc}, {self.theta_sat}")
        if self.ksat_mm_h <= 0 or self.intake_rate_mm_h <= 0:
            raise ValidationError(f"Soil {self.key!r}", "conductivity and intake rate must be positive")

    # ------------------------------------------------------------------ limits
    @property
    def available_water(self) -> float:
        """θ_FC − θ_WP (m³/m³); multiply by 1000 for mm of water per m of roots."""
        return self.theta_fc - self.theta_wp

    def taw_mm(self, root_depth_m):
        """Total available water in the root zone (mm). Accepts arrays."""
        return 1000.0 * self.available_water * np.asarray(root_depth_m, dtype=float)

    def raw_mm(self, root_depth_m, depletion_fraction):
        """Readily available water (mm): what can be used before stress."""
        return np.asarray(depletion_fraction, dtype=float) * self.taw_mm(root_depth_m)

    def theta_refill(self, depletion_fraction):
        """Moisture at which the plant starts to suffer (m³/m³)."""
        return self.theta_fc - np.asarray(depletion_fraction, dtype=float) * self.available_water

    def relative_depletion(self, theta):
        """D_r / TAW: 0 at field capacity, 1 at wilting point, negative when
        wetter than field capacity. Comparable across soils."""
        return (self.theta_fc - np.asarray(theta, dtype=float)) / self.available_water

    def clip(self, theta):
        """Clip moisture to the physical range [θ_WP, θ_sat]."""
        return np.clip(theta, self.theta_wp, self.theta_sat)

    # ------------------------------------------------------------ calibration
    def calibrated(self, calibration: SoilCalibration) -> Soil:
        """A copy whose limits come from the probe record instead of the
        textbook texture class (keeps conductivity and intake rate)."""
        return replace(self, key=f"{self.key}@calibrated", theta_fc=calibration.theta_fc,
                       theta_wp=calibration.theta_wp,
                       theta_sat=max(self.theta_sat, calibration.theta_max + 0.01),
                       source=f"{self.source}; θ_FC and θ_WP calibrated from probes ({calibration.method})")

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "theta_sat": self.theta_sat, "theta_fc": self.theta_fc,
                "theta_wp": self.theta_wp, "ksat_mm_h": self.ksat_mm_h,
                "intake_rate_mm_h": self.intake_rate_mm_h, "source": self.source}


@dataclass(frozen=True, slots=True, kw_only=True)
class SoilCalibration:
    """Field capacity and wilting point estimated from a moisture record.

    Probes are rarely calibrated to the soil they sit in, so textbook limits
    can disagree with readings by several percentage points. Calibrating keeps
    the success band (θ_refill to θ_FC) consistent with what the probe reports.

    - θ_FC: median moisture ``drainage_h`` hours after a soaking rain
      (≥ ``soak_mm`` in 24 h) with no further rain during the drainage period.
      That is the operational definition of field capacity.
    - θ_WP: the texture-class value, unless the probe goes lower; then the
      ``wp_quantile`` of the record (a probe offset, not drier soil).
    """

    theta_fc: float
    theta_wp: float
    theta_max: float
    events: int
    method: str = field(default="post-drainage median")

    @classmethod
    def from_record(cls, theta: pd.Series, rain_mm: pd.Series, texture: Soil, *, soak_mm: float = 10.0,
                    drainage_h: int = 48, dry_tolerance_mm: float = 0.5, wp_quantile: float = 0.005,
                    min_events: int = 3) -> SoilCalibration:
        theta = theta.astype(float)
        rain = rain_mm.reindex(theta.index).fillna(0.0)
        rain_24h = rain.rolling(24, min_periods=1).sum()
        rain_after = rain[::-1].rolling(drainage_h, min_periods=1).sum()[::-1].shift(-1)  # (t, t+drainage]
        soaked = (rain_24h >= soak_mm) & (rain_after <= dry_tolerance_mm)
        starts = soaked & ~soaked.shift(1, fill_value=False)
        samples = theta.shift(-drainage_h)[starts].dropna()
        if len(samples) < min_events:
            raise DataError(f"only {len(samples)} drainage events found; need {min_events} to estimate θ_FC")
        theta_fc = float(samples.median())
        observed_floor = float(theta.quantile(wp_quantile))
        theta_wp = min(texture.theta_wp, observed_floor)
        if theta_wp >= theta_fc:
            raise DataError(f"calibrated θ_WP {theta_wp:.3f} is not below θ_FC {theta_fc:.3f}")
        return cls(theta_fc=round(theta_fc, 4), theta_wp=round(theta_wp, 4),
                   theta_max=float(theta.max()), events=len(samples))

    def to_dict(self) -> dict:
        return {"theta_fc": self.theta_fc, "theta_wp": self.theta_wp, "theta_max": self.theta_max,
                "events": self.events, "method": self.method}

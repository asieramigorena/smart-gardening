"""
zone.py
An irrigation zone: one bed behind one valve, with its soil, its planting,
its probes and its water supply. This is the unit the forest predicts for and
the behavior tree acts on.

Valve time for a refill (ML/docs/README.md):

    t = λ · 1000 · (θ_FC − θ) · Z_r · A / (E_a · Q)         [h]

1000 (θ_FC − θ) Z_r is the deficit in mm; mm × m² = litres; dividing by the
delivered flow E_a·Q (L/h) gives hours.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .errors import DataError, ValidationError
from .plant import Planting
from .soil import Soil


class ZoneRole(StrEnum):
    TRAINING = "training"      # a monitored, unirrigated reference site that supplies training data
    DEPLOYMENT = "deployment"  # a bed the controller actually waters


class IrrigationMethod(StrEnum):
    DRIP = "drip"
    MICRO_SPRINKLER = "micro_sprinkler"
    SPRINKLER = "sprinkler"
    SOAKER_HOSE = "soaker_hose"

    @property
    def typical_efficiency(self) -> float:
        """Application efficiency E_a (FAO Irrigation Water Management manual 4)."""
        return {"drip": 0.90, "micro_sprinkler": 0.85, "sprinkler": 0.75, "soaker_hose": 0.80}[self.value]


@dataclass(frozen=True, slots=True, kw_only=True)
class GeoPoint:
    latitude: float
    longitude: float
    elevation_m: float | None = None

    def __post_init__(self) -> None:
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValidationError("GeoPoint", f"invalid coordinates ({self.latitude}, {self.longitude})")


@dataclass(frozen=True, slots=True, kw_only=True)
class IrrigationSystem:
    """Valve plus emitters. Flow is constant, so water = open time × flow."""

    method: IrrigationMethod
    flow_l_h: float
    application_efficiency: float | None = None

    def __post_init__(self) -> None:
        if self.flow_l_h <= 0:
            raise ValidationError("IrrigationSystem", "flow must be positive")
        if self.application_efficiency is None:
            object.__setattr__(self, "application_efficiency", self.method.typical_efficiency)
        if not 0.3 <= self.application_efficiency <= 1.0:
            raise ValidationError("IrrigationSystem", "application efficiency must be in [0.3, 1]")

    def application_rate_mm_h(self, area_m2: float) -> float:
        """Water depth delivered per hour over the bed (L/h ÷ m² = mm/h)."""
        return self.flow_l_h / area_m2


@dataclass(frozen=True, slots=True, kw_only=True)
class SoilProbe:
    """One moisture sensor. ``channel`` names its reading in the hourly data;
    ``temperature_channel`` (optional) is the soil temperature at the same
    depth, used to mask frozen-soil readings."""

    depth_cm: float
    channel: str
    temperature_channel: str | None = None

    def __post_init__(self) -> None:
        if not 0 < self.depth_cm <= 300:
            raise ValidationError("SoilProbe", f"depth must be in (0, 300] cm, got {self.depth_cm}")

    @property
    def label(self) -> str:
        return f"{self.depth_cm:g}cm"


class ProbeArray:
    """The probes of one zone, ordered by depth.

    Each probe represents the soil layer from halfway to the probe above to
    halfway to the probe below (the shallowest from the surface, the deepest
    downwards without limit). Root-zone moisture is the thickness-weighted
    mean of the layers inside the root depth.
    """

    def __init__(self, probes: Iterable[SoilProbe]):
        self._probes = tuple(sorted(probes, key=lambda p: p.depth_cm))
        if not self._probes:
            raise ValidationError("ProbeArray", "a zone needs at least one moisture probe")
        depths = [p.depth_cm for p in self._probes]
        if len(set(depths)) != len(depths):
            raise ValidationError("ProbeArray", f"duplicate probe depths {depths}")
        d = np.array(depths, dtype=float) / 100.0
        mids = (d[1:] + d[:-1]) / 2
        self._tops = np.concatenate([[0.0], mids])
        self._bottoms = np.concatenate([mids, [np.inf]])

    def __iter__(self) -> Iterator[SoilProbe]:
        return iter(self._probes)

    def __len__(self) -> int:
        return len(self._probes)

    @property
    def channels(self) -> list[str]:
        return [p.channel for p in self._probes]

    def layer_weights(self, root_depth_m) -> np.ndarray:
        """Weights (n_times × n_probes) summing to 1 per row."""
        zr = np.atleast_1d(np.asarray(root_depth_m, dtype=float))[:, None]
        overlap = np.clip(np.minimum(self._bottoms, zr) - self._tops, 0.0, None)
        return overlap / zr

    def root_zone_theta(self, readings: pd.DataFrame, root_depth_m, *, min_weight: float = 0.6) -> pd.Series:
        """Root-zone moisture from per-probe readings (columns = channels).

        A missing probe's weight is shared among the others; if the probes
        still reporting cover less than ``min_weight`` of the root zone, the
        result is NaN rather than an extrapolation."""
        missing = [c for c in self.channels if c not in readings]
        if missing:
            raise DataError(f"probe channels not in data: {missing}")
        values = readings[self.channels].to_numpy(dtype=float)
        weights = np.broadcast_to(self.layer_weights(root_depth_m), values.shape).copy()
        weights[np.isnan(values)] = 0.0
        covered = weights.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            theta = np.nansum(values * weights, axis=1) / covered
        theta[covered < min_weight] = np.nan
        return pd.Series(theta, index=readings.index, name="theta_rz")

    def to_list(self) -> list[dict]:
        return [{"depth_cm": p.depth_cm, "channel": p.channel, "temperature_channel": p.temperature_channel}
                for p in self._probes]


@dataclass(frozen=True, slots=True)
class IrrigationEvent:
    start: pd.Timestamp
    duration_s: float

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.Timedelta(seconds=self.duration_s)


class IrrigationLog:
    """Valve openings of one zone (UTC). Empty for unirrigated training sites."""

    def __init__(self, events: Iterable[IrrigationEvent] = ()):
        self._events = sorted(events, key=lambda e: e.start)
        for e in self._events:
            if e.start.tzinfo is None:
                raise ValidationError("IrrigationLog", "event timestamps must be timezone-aware")

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[IrrigationEvent]:
        return iter(self._events)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> IrrigationLog:
        """From a table with columns ``start`` (UTC) and ``duration_s``."""
        return cls(IrrigationEvent(pd.Timestamp(r.start), float(r.duration_s)) for r in frame.itertuples())

    def seconds_per_hour(self, index: pd.DatetimeIndex) -> pd.Series:
        """Valve-open seconds within each hour ending at the index timestamp."""
        out = pd.Series(0.0, index=index)
        one_hour = pd.Timedelta(hours=1)
        for e in self._events:
            hour_end = e.start.floor("h") + one_hour
            while hour_end - one_hour < e.end:
                overlap = (min(e.end, hour_end) - max(e.start, hour_end - one_hour)).total_seconds()
                if hour_end in out.index:
                    out[hour_end] += overlap
                hour_end += one_hour
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class Zone:
    """One valve-controlled bed. Immutable: build a new zone to change it."""

    key: str
    name: str
    role: ZoneRole
    area_m2: float
    location: GeoPoint
    timezone: str
    soil: Soil
    planting: Planting
    probes: ProbeArray
    irrigation: IrrigationSystem | None = None
    soil_depth_m: float | None = None  # raised bed or hardpan: roots cannot go deeper
    notes: str = ""
    tz: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.area_m2 <= 0:
            raise ValidationError(f"Zone {self.key!r}", "area must be positive")
        if self.soil_depth_m is not None and not 0.05 <= self.soil_depth_m <= 3.0:
            raise ValidationError(f"Zone {self.key!r}", "soil depth must be in [0.05, 3] m")
        if self.role is ZoneRole.DEPLOYMENT and self.irrigation is None:
            raise ValidationError(f"Zone {self.key!r}", "a deployment zone needs an irrigation system")
        try:
            object.__setattr__(self, "tz", ZoneInfo(self.timezone))
        except Exception as exc:
            raise ValidationError(f"Zone {self.key!r}", f"unknown timezone {self.timezone!r}") from exc

    @property
    def plant(self):
        return self.planting.plant

    # -------------------------------------------------- state along a time axis
    def days_since_planting(self, times: pd.DatetimeIndex) -> np.ndarray:
        return self.planting.days_since_planting(times.tz_convert(self.tz))

    def root_depth_m(self, times: pd.DatetimeIndex) -> np.ndarray:
        depth = self.plant.root_depth_m(np.clip(self.days_since_planting(times), 0, None))
        return depth if self.soil_depth_m is None else np.minimum(depth, self.soil_depth_m)

    def crop_coefficient(self, times: pd.DatetimeIndex) -> np.ndarray:
        return self.plant.crop_coefficient(np.clip(self.days_since_planting(times), 0, None))

    def theta_refill(self, times: pd.DatetimeIndex, etc_mm_day=None) -> np.ndarray:
        """Lower edge of the success band. With ``etc_mm_day`` the depletion
        fraction is corrected for evaporative demand."""
        p = (self.plant.adjusted_depletion_fraction(etc_mm_day) if etc_mm_day is not None
             else np.full(len(times), self.plant.depletion_fraction))
        return self.soil.theta_refill(p)

    # ------------------------------------------------------------ actuation
    def deficit_mm(self, theta, root_depth_m) -> np.ndarray:
        """Water needed to bring the root zone back to field capacity (mm)."""
        return np.clip(1000.0 * (self.soil.theta_fc - np.asarray(theta, dtype=float)) * root_depth_m, 0, None)

    def valve_open_seconds(self, theta, root_depth_m, refill_fraction: float = 1.0) -> np.ndarray:
        """Valve time that refills ``refill_fraction`` (λ) of the deficit."""
        if self.irrigation is None:
            raise ValidationError(f"Zone {self.key!r}", "has no irrigation system")
        if not 0 < refill_fraction <= 1:
            raise ValidationError(f"Zone {self.key!r}", "refill fraction must be in (0, 1]")
        litres = refill_fraction * self.deficit_mm(theta, root_depth_m) * self.area_m2
        return 3600.0 * litres / (self.irrigation.application_efficiency * self.irrigation.flow_l_h)

    def needs_pulsing(self) -> bool:
        """True when the emitters deliver faster than the soil can absorb."""
        return (self.irrigation is not None
                and self.irrigation.application_rate_mm_h(self.area_m2) > self.soil.intake_rate_mm_h)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "role": self.role.value, "area_m2": self.area_m2,
            "location": {"latitude": self.location.latitude, "longitude": self.location.longitude,
                         "elevation_m": self.location.elevation_m},
            "timezone": self.timezone, "soil_depth_m": self.soil_depth_m, "soil": self.soil.to_dict(), "planting": self.planting.to_dict(),
            "plant": self.plant.to_dict(), "probes": self.probes.to_list(),
            "irrigation": None if self.irrigation is None else {
                "method": self.irrigation.method.value, "flow_l_h": self.irrigation.flow_l_h,
                "application_efficiency": self.irrigation.application_efficiency},
            "notes": self.notes,
        }

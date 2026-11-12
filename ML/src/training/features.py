"""
features.py
Feature blocks of the moisture forest (ML/docs/README.md, "Forest / Inputs").

Every row is a decision time t: the end of an hour whose observations have
just arrived. A block may use anything observed up to and including t, and
forecasts only through ForecastView (what was issued before t). Each block
declares its group name and returns a frame of uniquely named columns; the
builder checks names and alignment, so blocks can be added or removed
without touching the others.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from garden import IrrigationLog, WeatherChannels, Zone

from .forecast import ForecastView


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildContext:
    """Everything a block may read, computed once per zone."""

    zone: Zone
    weather: WeatherChannels
    frame: pd.DataFrame          # raw hourly table, regular UTC index
    theta: pd.DataFrame          # probe readings after quality control
    qc: pd.DataFrame             # quality flags per probe
    theta_rz: pd.Series          # root-zone moisture
    days: np.ndarray             # days since planting / season start
    root_depth_m: np.ndarray
    kc: np.ndarray
    irrigation_s: pd.Series      # valve-open seconds in each hour
    forecast: ForecastView

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.frame.index


class FeatureBlock(ABC):
    """One coherent group of inputs."""

    group: str = ""

    @abstractmethod
    def build(self, ctx: BuildContext) -> pd.DataFrame:
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def _rolling_sum(series: pd.Series, hours: int, min_coverage: float = 0.9) -> pd.Series:
    """Sum over the ``hours`` ending at t; scaled up over small gaps, NaN over large ones."""
    roll = series.rolling(hours, min_periods=max(1, int(np.ceil(min_coverage * hours))))
    return roll.mean() * hours


# ---------------------------------------------------------------------- blocks
class TimeFeatures(FeatureBlock):
    group = "time"

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        local = ctx.index.tz_convert(ctx.zone.tz)
        hour = local.hour + local.minute / 60
        doy = local.dayofyear - 1 + hour / 24
        year_days = np.where(local.is_leap_year, 366, 365)
        return pd.DataFrame({
            "hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24),
            "doy_sin": np.sin(2 * np.pi * doy / year_days), "doy_cos": np.cos(2 * np.pi * doy / year_days),
            "days_since_planting": ctx.days,
        }, index=ctx.index)


class MoistureFeatures(FeatureBlock):
    """θ at each depth, root-zone θ, its changes, and depletion relative to the soil's limits."""

    group = "moisture"

    def __init__(self, deltas_h: tuple[int, ...] = (1, 6, 24)):
        self.deltas_h = deltas_h

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        out = {}
        for probe in ctx.zone.probes:
            theta = ctx.theta[probe.channel]
            out[f"theta_{probe.label}"] = theta
            for h in self.deltas_h:
                out[f"dtheta_{probe.label}_{h}h"] = theta - theta.shift(h)
        out["theta_rz"] = ctx.theta_rz
        for h in self.deltas_h:
            out[f"dtheta_rz_{h}h"] = ctx.theta_rz - ctx.theta_rz.shift(h)
        out["depletion_rz"] = ctx.zone.soil.relative_depletion(ctx.theta_rz)
        return pd.DataFrame(out, index=ctx.index)


class IrrigationHistoryFeatures(FeatureBlock):
    group = "irrigation"

    def __init__(self, cap_h: float = 720.0):
        self.cap_h = cap_h  # "never irrigated" is encoded as this many hours

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        opened = ctx.irrigation_s > 0
        hour_number = pd.Series(np.arange(len(ctx.index), dtype=float), index=ctx.index)
        last_hour = hour_number.where(opened).ffill()
        since = (hour_number - last_hour).fillna(self.cap_h).clip(upper=self.cap_h)
        # Duration of the most recent opening: sum consecutive open hours.
        burst = (opened != opened.shift()).cumsum()
        burst_total = ctx.irrigation_s.groupby(burst).transform("sum").where(opened)
        last_duration = burst_total.ffill().fillna(0.0)
        return pd.DataFrame({"hours_since_irrigation": since, "last_irrigation_s": last_duration}, index=ctx.index)


class ObservedWeatherFeatures(FeatureBlock):
    group = "observed_weather"

    def __init__(self, rain_h: tuple[int, ...] = (24, 72, 168), et0_h: tuple[int, ...] = (24, 72)):
        self.rain_h, self.et0_h = rain_h, et0_h

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        w, f = ctx.weather, ctx.frame
        out = {f"rain_past_{h}h": _rolling_sum(f[w.rain_mm], h) for h in self.rain_h}
        out |= {f"et0_past_{h}h": _rolling_sum(f[w.et0_mm], h) for h in self.et0_h}
        out["radiation_now"] = f[w.radiation_w_m2]
        return pd.DataFrame(out, index=ctx.index)


class ForecastFeatures(FeatureBlock):
    group = "forecast"

    def __init__(self, rain_h: tuple[int, ...] = (6, 24, 48, 72), rain_prob_h: int = 24,
                 et0_h: tuple[int, ...] = (24, 72), radiation_h: int = 24):
        self.rain_h, self.rain_prob_h, self.et0_h, self.radiation_h = rain_h, rain_prob_h, et0_h, radiation_h

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        w, fc = ctx.weather, ctx.forecast
        out = {f"fc_rain_next_{h}h": fc.window(w.forecast_rain_mm, h) for h in self.rain_h}
        if w.forecast_rain_probability_pct in fc.leads:
            out[f"fc_rain_prob_max_{self.rain_prob_h}h"] = fc.window(
                w.forecast_rain_probability_pct, self.rain_prob_h, how="max")
        out |= {f"fc_et0_next_{h}h": fc.window(w.forecast_et0_mm, h) for h in self.et0_h}
        out[f"fc_radiation_next_{self.radiation_h}h"] = fc.window(w.forecast_radiation_w_m2, self.radiation_h,
                                                                  how="mean")
        return pd.DataFrame(out, index=ctx.index)


class ZoneFeatures(FeatureBlock):
    """Bed descriptors. Numeric soil and plant parameters let one forest
    serve several beds; the keys are kept as categories for inspection."""

    group = "zone"

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        soil, plant, n = ctx.zone.soil, ctx.zone.plant, len(ctx.index)
        return pd.DataFrame({
            "soil_theta_fc": np.full(n, soil.theta_fc), "soil_theta_wp": np.full(n, soil.theta_wp),
            "soil_taw_mm_per_m": np.full(n, 1000 * soil.available_water),
            "soil_ksat_mm_h": np.full(n, soil.ksat_mm_h),
            "root_depth_m": ctx.root_depth_m, "kc": ctx.kc,
            "growth_stage": pd.Categorical(plant.stage(np.clip(ctx.days, 0, None))),
            "soil_key": pd.Categorical([soil.key] * n), "plant_key": pd.Categorical([plant.key] * n),
        }, index=ctx.index)


class WaterBalanceFeatures(FeatureBlock):
    """Physics prior: a one-bucket FAO-56 projection of θ_rz with the
    forecast rain and crop water use. The forest then learns the correction
    (interception, runoff, probe bias, layering) instead of the whole curve.

        θ* = θ_rz + (P − Kc·ET₀) / (1000 Z_r)
        θ_bucket = θ_FC + (θ* − θ_FC) · exp(−h / τ)   when θ* > θ_FC (free drainage)
                 = max(θ*, θ_WP)                       otherwise

    τ = 48 h fits the Darrington training split best (MAE at +48 h 0.0104
    against 0.0160 for persistence; τ = 24 h gives 0.0133, 96 h 0.0125)."""

    group = "water_balance"

    def __init__(self, horizons_h: tuple[int, ...] = (6, 24, 48), drainage_tau_h: float = 48.0):
        if drainage_tau_h <= 0:
            raise ValueError("drainage time constant must be positive")
        self.horizons_h, self.drainage_tau_h = horizons_h, drainage_tau_h

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        w, fc, soil = ctx.weather, ctx.forecast, ctx.zone.soil
        out = {}
        for h in self.horizons_h:
            rain = fc.window(w.forecast_rain_mm, h)
            etc = ctx.kc * fc.window(w.forecast_et0_mm, h)
            out[f"etc_next_{h}h"] = etc
            projected = ctx.theta_rz + (rain - etc) / (1000.0 * ctx.root_depth_m)
            drained = soil.theta_fc + (projected - soil.theta_fc) * np.exp(-h / self.drainage_tau_h)
            bucket = projected.where(projected <= soil.theta_fc, drained).clip(lower=soil.theta_wp)
            out[f"theta_bucket_{h}h"] = bucket
        return pd.DataFrame(out, index=ctx.index)


DEFAULT_BLOCKS: tuple[FeatureBlock, ...] = (
    TimeFeatures(), MoistureFeatures(), IrrigationHistoryFeatures(), ObservedWeatherFeatures(),
    ForecastFeatures(), ZoneFeatures(), WaterBalanceFeatures(),
)


# ---------------------------------------------------------------------- targets
class TargetBuilder:
    """Root-zone moisture h hours ahead, assuming no irrigation.

    Rows whose horizon contains a valve opening are masked: that future is
    not the one the forest is asked about. Both the level θ(t+h) and the
    change θ(t+h) − θ(t) are written; the change is usually easier to learn
    and the level is recovered by adding θ_rz back."""

    def __init__(self, horizons_h: tuple[int, ...] = (6, 24, 48)):
        if not horizons_h or min(horizons_h) < 1:
            raise ValueError("horizons must be positive hours")
        self.horizons_h = tuple(sorted(horizons_h))

    @property
    def max_horizon(self) -> int:
        return self.horizons_h[-1]

    def columns(self) -> list[str]:
        return [f"y_{kind}_rz_{h}h" for h in self.horizons_h for kind in ("theta", "dtheta")]

    def build(self, ctx: BuildContext) -> pd.DataFrame:
        irrigated = (ctx.irrigation_s > 0).astype(float)
        out = {}
        for h in self.horizons_h:
            future = ctx.theta_rz.shift(-h)
            watered_ahead = irrigated[::-1].rolling(h, min_periods=1).max()[::-1].shift(-1).fillna(0) > 0
            future = future.mask(watered_ahead)
            out[f"y_theta_rz_{h}h"] = future
            out[f"y_dtheta_rz_{h}h"] = future - ctx.theta_rz
        return pd.DataFrame(out, index=ctx.index)


def irrigation_seconds(log: IrrigationLog, index: pd.DatetimeIndex) -> pd.Series:
    return log.seconds_per_hour(index) if len(log) else pd.Series(0.0, index=index)

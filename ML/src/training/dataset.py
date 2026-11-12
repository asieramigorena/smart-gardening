"""
dataset.py
Assembles the forest's training set for one zone and stores it with a
manifest that records every choice needed to rebuild or audit it.

    builder = TrainingSetBuilder(garden["darrington"], garden.binding("darrington"))
    training_set = builder.build()
    training_set.save(Path("data/training/darrington"))
    X, y = training_set.X("train"), training_set.y("train")

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from garden import DataBinding, DataError, IrrigationLog, SoilCalibration, Zone, ZoneRole
from garden.config import REPO_ROOT

from .features import DEFAULT_BLOCKS, BuildContext, FeatureBlock, TargetBuilder, irrigation_seconds
from .forecast import ForecastView
from .quality import QualitySettings, SensorQualityControl

MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class SplitSettings:
    """Chronological split. Rows whose target horizon crosses into the next
    period are dropped (the embargo), so no label of one split is a value
    observed in another."""

    train: float = 0.70
    validation: float = 0.15
    test: float = 0.15

    def __post_init__(self) -> None:
        if abs(self.train + self.validation + self.test - 1.0) > 1e-9 or min(asdict(self).values()) < 0:
            raise ValueError("split fractions must be non-negative and sum to 1")


class TemporalSplit:
    NAMES = ("train", "validation", "test")

    def __init__(self, settings: SplitSettings, embargo_h: int):
        self.settings, self.embargo = settings, pd.Timedelta(hours=embargo_h)

    def assign(self, index: pd.DatetimeIndex) -> tuple[pd.Series, dict]:
        start, end = index.min(), index.max()
        span = end - start
        cut1 = (start + span * self.settings.train).ceil("h")
        cut2 = (start + span * (self.settings.train + self.settings.validation)).ceil("h")
        labels = pd.Series(np.select([index < cut1, index < cut2], ["train", "validation"], "test"),
                           index=index, name="split")
        embargoed = ((index >= cut1 - self.embargo) & (index < cut1)) | ((index >= cut2 - self.embargo) & (index < cut2))
        labels[embargoed] = "embargo"
        bounds = {"train": [start, cut1], "validation": [cut1, cut2], "test": [cut2, end]}
        return labels, {k: [t.isoformat() for t in v] for k, v in bounds.items()}


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildSettings:
    horizons_h: tuple[int, ...] = (6, 24, 48)
    availability_lag_h: int = 0
    quality: QualitySettings = field(default_factory=QualitySettings)
    split: SplitSettings = field(default_factory=SplitSettings)
    min_root_zone_coverage: float = 0.6


class TrainingSet:
    """Features, targets, quality flags and split labels for one zone."""

    def __init__(self, frame: pd.DataFrame, manifest: dict):
        self.frame, self.manifest = frame, manifest

    @property
    def feature_columns(self) -> list[str]:
        return [c for cols in self.manifest["features"].values() for c in cols]

    @property
    def target_columns(self) -> list[str]:
        return self.manifest["targets"]

    def rows(self, split: str | None = None) -> pd.DataFrame:
        if split is None:
            return self.frame[self.frame["split"] != "embargo"]
        if split not in TemporalSplit.NAMES:
            raise ValueError(f"split must be one of {TemporalSplit.NAMES}")
        return self.frame[self.frame["split"] == split]

    def X(self, split: str | None = None) -> pd.DataFrame:
        return self.rows(split)[self.feature_columns]

    def y(self, split: str | None = None, kind: str = "theta") -> pd.DataFrame:
        if kind not in ("theta", "dtheta"):
            raise ValueError("kind must be 'theta' or 'dtheta'")
        return self.rows(split)[[c for c in self.target_columns if c.startswith(f"y_{kind}_")]]

    def save(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        data, meta = directory / "dataset.parquet", directory / "manifest.json"
        self.frame.to_parquet(data)
        meta.write_text(json.dumps(self.manifest, indent=2, default=str))
        return data, meta

    @classmethod
    def load(cls, directory: Path) -> TrainingSet:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            raise DataError(f"{directory}: manifest version {manifest.get('manifest_version')} not supported")
        return cls(pd.read_parquet(directory / "dataset.parquet"), manifest)

    def __repr__(self) -> str:
        counts = self.frame["split"].value_counts().to_dict()
        return f"TrainingSet({self.manifest['zone']['key']!r}, {len(self.feature_columns)} features, {counts})"


class TrainingSetBuilder:
    """Turns a zone's hourly table into a TrainingSet."""

    def __init__(self, zone: Zone, binding: DataBinding, settings: BuildSettings = BuildSettings(),
                 blocks: tuple[FeatureBlock, ...] = DEFAULT_BLOCKS):
        if zone.role is not ZoneRole.TRAINING and binding.irrigation_log is None:
            raise DataError(f"zone {zone.key!r} is irrigated: its data binding needs an irrigation_log")
        self.zone, self.binding, self.settings, self.blocks = zone, binding, settings, blocks
        self.targets = TargetBuilder(settings.horizons_h)

    # ---------------------------------------------------------------- loading
    def _load_table(self) -> pd.DataFrame:
        if not self.binding.table.exists():
            raise DataError(f"{self.binding.table} not found; run ML/src/build_dataset.py first")
        frame = pd.read_parquet(self.binding.table)
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise DataError("the hourly table needs a timezone-aware DatetimeIndex")
        w = self.binding.weather
        required = self.zone.probes.channels + [w.rain_mm, w.et0_mm, w.radiation_w_m2]
        missing = [c for c in required if c not in frame]
        if missing:
            raise DataError(f"{self.binding.table.name} lacks columns {missing}")
        frame = frame.tz_convert("UTC").sort_index()
        return frame[~frame.index.duplicated(keep="last")].asfreq("h")

    def _irrigation_log(self) -> IrrigationLog:
        if self.binding.irrigation_log is None:
            return IrrigationLog()
        log = pd.read_csv(self.binding.irrigation_log, parse_dates=["start"])
        log["start"] = pd.to_datetime(log["start"], utc=True)
        return IrrigationLog.from_frame(log)

    # ------------------------------------------------------------- assembling
    def context(self, frame: pd.DataFrame) -> tuple[BuildContext, SoilCalibration | None]:
        zone = self.zone
        theta, qc = SensorQualityControl(zone, self.settings.quality).apply(frame)
        index = frame.index
        root_depth = zone.root_depth_m(index)
        calibration = None
        if self.binding.calibrate_soil:
            provisional = zone.probes.root_zone_theta(theta, root_depth, min_weight=self.settings.min_root_zone_coverage)
            calibration = SoilCalibration.from_record(provisional, frame[self.binding.weather.rain_mm], zone.soil)
            # Zones are immutable: the calibrated soil makes a new zone object.
            zone = replace(zone, soil=zone.soil.calibrated(calibration))
        ctx = BuildContext(
            zone=zone, weather=self.binding.weather, frame=frame, theta=theta, qc=qc,
            theta_rz=zone.probes.root_zone_theta(theta, root_depth, min_weight=self.settings.min_root_zone_coverage),
            days=zone.days_since_planting(index), root_depth_m=root_depth, kc=zone.crop_coefficient(index),
            irrigation_s=irrigation_seconds(self._irrigation_log(), index),
            forecast=ForecastView(frame, self.binding.weather.forecast_prefix, self.settings.availability_lag_h),
        )
        return ctx, calibration

    def _features(self, ctx: BuildContext) -> tuple[pd.DataFrame, dict[str, list[str]]]:
        parts, groups = [], {}
        for block in self.blocks:
            part = block.build(ctx)
            if not part.index.equals(ctx.index):
                raise DataError(f"{block!r} returned a misaligned index")
            clash = set(part.columns) & {c for cols in groups.values() for c in cols}
            if clash:
                raise DataError(f"{block!r} repeats columns {sorted(clash)}")
            groups.setdefault(block.group, []).extend(part.columns)
            parts.append(part)
        return pd.concat(parts, axis=1), groups

    def build(self) -> TrainingSet:
        frame = self._load_table()
        ctx, calibration = self.context(frame)
        features, groups = self._features(ctx)
        targets = self.targets.build(ctx)

        table = pd.concat([features, targets, ctx.qc], axis=1)
        usable = table["theta_rz"].notna() & targets.notna().all(axis=1)
        table = table[usable]
        if table.empty:
            raise DataError("no hour has both a root-zone moisture and all targets")

        split, bounds = TemporalSplit(self.settings.split, self.targets.max_horizon).assign(table.index)
        table["split"] = pd.Categorical(split, categories=[*TemporalSplit.NAMES, "embargo"])

        manifest = self._manifest(ctx, calibration, groups, table, usable, bounds)
        return TrainingSet(table, manifest)

    def _manifest(self, ctx, calibration, groups, table, usable, bounds) -> dict:
        settings = self.settings
        return {
            "manifest_version": MANIFEST_VERSION,
            "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "git_commit": _git_commit(),
            "source_table": str(self.binding.table.relative_to(REPO_ROOT)),
            "zone": ctx.zone.to_dict(),
            "soil_calibration": calibration.to_dict() if calibration else None,
            "settings": {"horizons_h": list(settings.horizons_h), "availability_lag_h": settings.availability_lag_h,
                         "quality": asdict(settings.quality), "split": asdict(settings.split),
                         "min_root_zone_coverage": settings.min_root_zone_coverage},
            "rows": {"hours_in_table": int(len(ctx.index)), "usable": int(usable.sum()),
                     **{k: int(v) for k, v in table["split"].value_counts().items()}},
            "period": [table.index.min().isoformat(), table.index.max().isoformat()],
            "split_bounds": bounds,
            "features": groups,
            "targets": self.targets.columns(),
            "quality_flags": SensorQualityControl.summary(ctx.qc),
            "feature_coverage": {c: round(float(table[c].notna().mean()), 4) for cols in groups.values() for c in cols},
            "leakage_rule": "forecast features use only lead columns issued at least 24·N h before the hour "
                            "they describe (training/forecast.py); targets are masked when the valve opens "
                            "inside the horizon; split boundaries carry an embargo of the longest horizon.",
        }


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None

"""
training
Training-set construction for the soil-moisture forest: probe quality
control, leakage-safe forecast features, targets and chronological splits,
and the baselines the forest is compared against.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from .baselines import (Baseline, Bucket, FeaturePrep, LinearQuantile, Persistence, PointBaseline,
                        coverage, evaluate, pinball_loss, residual_offsets)
from .dataset import BuildSettings, SplitSettings, TemporalSplit, TrainingSet, TrainingSetBuilder
from .features import (DEFAULT_BLOCKS, BuildContext, FeatureBlock, ForecastFeatures, IrrigationHistoryFeatures,
                       MoistureFeatures, ObservedWeatherFeatures, TargetBuilder, TimeFeatures,
                       WaterBalanceFeatures, ZoneFeatures)
from .forecast import ForecastView, LeakageError
from .quality import ProbeFlag, QualitySettings, SensorQualityControl

__all__ = [
    "Baseline", "Bucket", "FeaturePrep", "LinearQuantile", "Persistence", "PointBaseline", "coverage",
    "evaluate", "pinball_loss", "residual_offsets",
    "BuildSettings", "SplitSettings", "TemporalSplit", "TrainingSet", "TrainingSetBuilder",
    "DEFAULT_BLOCKS", "BuildContext", "FeatureBlock", "ForecastFeatures", "IrrigationHistoryFeatures",
    "MoistureFeatures", "ObservedWeatherFeatures", "TargetBuilder", "TimeFeatures", "WaterBalanceFeatures",
    "ZoneFeatures", "ForecastView", "LeakageError", "ProbeFlag", "QualitySettings", "SensorQualityControl",
]

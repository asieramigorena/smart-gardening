"""
training
Training-set construction for the soil-moisture forest: probe quality
control, leakage-safe forecast features, targets and chronological splits.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from .dataset import BuildSettings, SplitSettings, TemporalSplit, TrainingSet, TrainingSetBuilder
from .features import (DEFAULT_BLOCKS, BuildContext, FeatureBlock, ForecastFeatures, IrrigationHistoryFeatures,
                       MoistureFeatures, ObservedWeatherFeatures, TargetBuilder, TimeFeatures,
                       WaterBalanceFeatures, ZoneFeatures)
from .forecast import ForecastView, LeakageError
from .quality import ProbeFlag, QualitySettings, SensorQualityControl

__all__ = [
    "BuildSettings", "SplitSettings", "TemporalSplit", "TrainingSet", "TrainingSetBuilder",
    "DEFAULT_BLOCKS", "BuildContext", "FeatureBlock", "ForecastFeatures", "IrrigationHistoryFeatures",
    "MoistureFeatures", "ObservedWeatherFeatures", "TargetBuilder", "TimeFeatures", "WaterBalanceFeatures",
    "ZoneFeatures", "ForecastView", "LeakageError", "ProbeFlag", "QualitySettings", "SensorQualityControl",
]

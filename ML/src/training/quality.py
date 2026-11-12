"""
quality.py
Moisture-probe quality control (ML/docs/README.md §9). Each reading gets a
bit mask of the problems found; flagged readings are removed before any
feature or target is computed, and the masks are kept in the dataset so the
behavior tree's health checks can be trained or tested on the same rules.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag

import numpy as np
import pandas as pd

from garden import SoilProbe, Zone


class ProbeFlag(IntFlag):
    OK = 0
    MISSING = 1        # no reading (dropout)
    OUT_OF_RANGE = 2   # outside the physically possible range
    STUCK = 4          # identical value for too long: frozen firmware or a dead sensor
    SPIKE = 8          # one-hour excursion that immediately reverses
    FROZEN_SOIL = 16   # soil temperature near 0 °C: dielectric probes read falsely dry


@dataclass(frozen=True, slots=True, kw_only=True)
class QualitySettings:
    theta_min: float = 0.0
    theta_max: float = 0.6
    stuck_hours: int = 72           # the Darrington probes never repeat a value longer than 46 h
    spike_threshold: float = 0.05   # m³/m³ jump out and back within one hour
    freezing_c: float = 0.5


class SensorQualityControl:
    """Applies the checks to every probe of a zone."""

    def __init__(self, zone: Zone, settings: QualitySettings = QualitySettings()):
        self.zone, self.settings = zone, settings

    def flags(self, frame: pd.DataFrame, probe: SoilProbe) -> pd.Series:
        s = self.settings
        theta = frame[probe.channel].astype(float)
        run_id = (theta.diff() != 0).cumsum()
        run_length = theta.groupby(run_id).transform("size")
        rise, fall = theta - theta.shift(1), theta - theta.shift(-1)
        checks = {
            ProbeFlag.MISSING: theta.isna(),
            ProbeFlag.OUT_OF_RANGE: (theta < s.theta_min) | (theta > s.theta_max),
            ProbeFlag.STUCK: (run_length >= s.stuck_hours) & theta.notna(),
            ProbeFlag.SPIKE: (np.sign(rise) == np.sign(fall))
                             & (np.minimum(rise.abs(), fall.abs()) > s.spike_threshold),
        }
        if probe.temperature_channel and probe.temperature_channel in frame:
            checks[ProbeFlag.FROZEN_SOIL] = frame[probe.temperature_channel] <= s.freezing_c
        flags = np.zeros(len(theta), dtype=np.int16)
        for flag, hit in checks.items():
            flags |= np.where(hit.to_numpy(dtype=bool), int(flag), 0).astype(np.int16)
        return pd.Series(flags, index=theta.index, name=f"qc_{probe.label}")

    def apply(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Returns (clean readings with flagged values as NaN, flag masks)."""
        clean, masks = {}, {}
        for probe in self.zone.probes:
            flags = self.flags(frame, probe)
            clean[probe.channel] = frame[probe.channel].where(flags.to_numpy() == int(ProbeFlag.OK))
            masks[flags.name] = flags
        return pd.DataFrame(clean, index=frame.index), pd.DataFrame(masks, index=frame.index)

    @staticmethod
    def summary(masks: pd.DataFrame) -> dict[str, dict[str, int]]:
        """Hours carrying each flag, per probe."""
        # int(flag): IntFlag members are iterable, which pandas would treat as a sequence
        return {col: {flag.name.lower(): int(((masks[col].to_numpy() & int(flag)) > 0).sum())
                      for flag in ProbeFlag if flag}
                for col in masks}

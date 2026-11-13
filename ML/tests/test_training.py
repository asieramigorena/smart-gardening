"""
test_training.py
Training pipeline: forecast leakage, targets, quality control, split.

    .venv/bin/python -m unittest discover -s ML/tests

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garden import (PLANTS, SOILS, GeoPoint, IrrigationEvent, IrrigationLog, Planting, ProbeArray,  # noqa: E402
                    SoilProbe, WeatherChannels, Zone, ZoneRole)
from training import (BuildContext, ForecastView, LeakageError, ProbeFlag, SensorQualityControl,  # noqa: E402
                      SplitSettings, TargetBuilder, TemporalSplit)

INDEX = pd.date_range("2026-01-01", periods=200, freq="h", tz="UTC")


def lead_frame() -> pd.DataFrame:
    """Each lead column encodes its own lead in the value: lead N → N × 100 + hour number."""
    hours = np.arange(len(INDEX))
    return pd.DataFrame({f"fc_rain_lead{n}d": n * 100 + hours for n in (1, 2, 3)}, index=INDEX)


def make_zone() -> Zone:
    return Zone(key="z", name="z", role=ZoneRole.TRAINING, area_m2=1.0,
                location=GeoPoint(latitude=0, longitude=0), timezone="UTC", soil=SOILS["loam"],
                planting=Planting(plant=PLANTS["turfgrass_cool"], season_start=(3, 1)),
                probes=ProbeArray([SoilProbe(depth_cm=10, channel="p10", temperature_channel="t10")]))


class ForecastViewTest(unittest.TestCase):
    def test_lead_needed_for_each_horizon(self):
        view = ForecastView(lead_frame())
        self.assertEqual([view.lead_for("rain", k) for k in (1, 24, 25, 48, 49, 72)], [1, 1, 2, 2, 3, 3])
        lagged = ForecastView(lead_frame(), availability_lag_h=6)
        self.assertEqual(lagged.lead_for("rain", 18), 1)
        self.assertEqual(lagged.lead_for("rain", 19), 2)

    def test_refuses_horizons_beyond_the_stored_leads(self):
        with self.assertRaises(LeakageError):
            ForecastView(lead_frame()).lead_for("rain", 73)
        with self.assertRaises(LeakageError):
            ForecastView(lead_frame(), availability_lag_h=6).window("rain", 72)

    def test_values_come_from_the_future_hour_and_the_right_lead(self):
        view = ForecastView(lead_frame())
        t = 10
        self.assertEqual(view.hour_ahead("rain", 5).iloc[t], 100 + t + 5)
        self.assertEqual(view.hour_ahead("rain", 30).iloc[t], 200 + t + 30)
        expected = sum(100 * ((k + 23) // 24) + t + k for k in range(1, 49))
        self.assertAlmostEqual(view.window("rain", 48).iloc[t], expected)

    def test_window_is_nan_where_the_record_ends(self):
        self.assertTrue(np.isnan(ForecastView(lead_frame()).window("rain", 24).iloc[-1]))


class TargetBuilderTest(unittest.TestCase):
    def context(self, theta: pd.Series, irrigation: pd.Series) -> BuildContext:
        zone = make_zone()
        frame = pd.DataFrame({"p10": theta}, index=INDEX)
        return BuildContext(zone=zone, weather=WeatherChannels(rain_mm="r", et0_mm="e", radiation_w_m2="s"),
                            frame=frame, theta=frame, qc=pd.DataFrame(index=INDEX), theta_rz=theta,
                            days=np.zeros(len(INDEX)), root_depth_m=np.full(len(INDEX), 0.5),
                            kc=np.ones(len(INDEX)), irrigation_s=irrigation, forecast=ForecastView(lead_frame()))

    def test_targets_are_future_values_and_masked_after_watering(self):
        theta = pd.Series(np.linspace(0.1, 0.3, len(INDEX)), index=INDEX)
        log = IrrigationLog([IrrigationEvent(INDEX[100] - pd.Timedelta(minutes=30), 600)])
        targets = TargetBuilder((6,)).build(self.context(theta, log.seconds_per_hour(INDEX)))
        y = targets["y_theta_rz_6h"]
        self.assertAlmostEqual(y.iloc[50], theta.iloc[56])
        self.assertAlmostEqual(targets["y_dtheta_rz_6h"].iloc[50], theta.iloc[56] - theta.iloc[50])
        self.assertTrue(y.iloc[94:100].isna().all())   # watering at hour 100 falls inside (t, t+6]
        self.assertFalse(np.isnan(y.iloc[93]) or np.isnan(y.iloc[100]))


class QualityControlTest(unittest.TestCase):
    def test_flags(self):
        theta = 0.2 + 0.001 * np.arange(len(INDEX))
        theta[20] += 0.1                      # spike
        theta[40] = np.nan                    # dropout
        theta[60] = 0.9                       # out of range (and a spike)
        theta[100:180] = 0.25                 # stuck for 80 h
        temp = np.full(len(INDEX), 8.0)
        temp[190] = 0.0                       # frozen
        frame = pd.DataFrame({"p10": theta, "t10": temp}, index=INDEX)
        clean, masks = SensorQualityControl(make_zone()).apply(frame)
        flags = masks["qc_10cm"]
        self.assertEqual(flags.iloc[20], ProbeFlag.SPIKE)
        self.assertEqual(flags.iloc[40], ProbeFlag.MISSING)
        self.assertTrue(flags.iloc[60] & ProbeFlag.OUT_OF_RANGE)
        self.assertTrue((flags.iloc[100:180].to_numpy() == int(ProbeFlag.STUCK)).all())
        self.assertEqual(flags.iloc[190], ProbeFlag.FROZEN_SOIL)
        self.assertEqual(flags.iloc[10], ProbeFlag.OK)
        self.assertTrue(clean["p10"][flags != 0].isna().all())


class TemporalSplitTest(unittest.TestCase):
    def test_chronological_with_embargo(self):
        labels, bounds = TemporalSplit(SplitSettings(train=0.5, validation=0.25, test=0.25), 6).assign(INDEX)
        order = labels[labels != "embargo"].tolist()
        self.assertEqual(order, sorted(order, key=["train", "validation", "test"].index))
        cut = pd.Timestamp(bounds["validation"][0])
        before = labels[(INDEX >= cut - pd.Timedelta(hours=6)) & (INDEX < cut)]
        self.assertTrue((before == "embargo").all() and len(before) == 6)


if __name__ == "__main__":
    unittest.main()

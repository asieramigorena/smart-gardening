"""
test_baselines.py
Baselines: pinball loss, residual calibration, feature preparation and the
evaluation table, on small synthetic training sets.

    .venv/bin/python -m unittest discover -s ML/tests -k Baseline

Written by Diego Revilla on 27-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garden import DataError  # noqa: E402
from training import (Bucket, FeaturePrep, LinearQuantile, Persistence, TrainingSet, coverage,  # noqa: E402
                      evaluate, pinball_loss, residual_offsets)

HORIZONS = (6, 24)


def synthetic_set(n: int = 300, seed: int = 0) -> TrainingSet:
    """θ_rz drifts slowly; the target is θ_rz plus a known drying of 0.001 per
    hour plus noise, and the bucket feature knows the drying but not the noise."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    theta = 0.25 + 0.02 * np.sin(np.arange(n) / 30)
    frame = pd.DataFrame({"theta_rz": theta, "kind": pd.Categorical(["a"] * n), "constant": 1.0}, index=index)
    for h in HORIZONS:
        future = theta - 0.001 * h + rng.normal(0, 0.002, n)
        frame[f"theta_bucket_{h}h"] = theta - 0.001 * h
        frame[f"y_theta_rz_{h}h"] = future
        frame[f"y_dtheta_rz_{h}h"] = future - theta
    frame["split"] = np.where(np.arange(n) < 0.6 * n, "train", np.where(np.arange(n) < 0.8 * n, "validation", "test"))
    manifest = {"zone": {"key": "synthetic"},
                "features": {"moisture": ["theta_rz", "constant", "kind"],
                             "water_balance": [f"theta_bucket_{h}h" for h in HORIZONS]},
                "targets": [f"y_{k}_rz_{h}h" for h in HORIZONS for k in ("theta", "dtheta")]}
    return TrainingSet(frame, manifest)


class PinballLossTest(unittest.TestCase):                                   # part 1
    def test_known_values(self):
        y = np.array([1.0, 2.0, 3.0])
        self.assertAlmostEqual(pinball_loss(y, y, 0.1), 0.0)
        # under-prediction by 1 costs q, over-prediction by 1 costs 1 − q
        self.assertAlmostEqual(pinball_loss(np.array([2.0]), np.array([1.0]), 0.1), 0.1)
        self.assertAlmostEqual(pinball_loss(np.array([1.0]), np.array([2.0]), 0.1), 0.9)
        self.assertAlmostEqual(pinball_loss(np.array([2.0, 1.0]), np.array([1.0, 2.0]), 0.5), 0.5)

    def test_minimised_by_the_quantile(self):
        y = np.random.default_rng(1).normal(size=5000)
        candidates = np.linspace(-2, 2, 81)
        best = candidates[np.argmin([pinball_loss(y, np.full_like(y, c), 0.1) for c in candidates])]
        self.assertAlmostEqual(best, np.quantile(y, 0.1), delta=0.06)

    def test_coverage(self):
        self.assertAlmostEqual(coverage(np.arange(10), np.full(10, 3)), 0.3)


class ResidualOffsetsTest(unittest.TestCase):                              # part 2
    def test_quantiles_of_the_residuals_ignoring_nan(self):
        residuals = pd.Series(list(range(101)) + [np.nan], dtype=float)
        offsets = residual_offsets(residuals, (0.1, 0.5))
        self.assertEqual(set(offsets), {0.1, 0.5})
        self.assertAlmostEqual(offsets[0.1], 10.0)
        self.assertAlmostEqual(offsets[0.5], 50.0)

    def test_refuses_an_empty_series(self):
        with self.assertRaises(DataError):
            residual_offsets(pd.Series([np.nan, np.nan]), (0.5,))


class FeaturePrepTest(unittest.TestCase):                                  # part 3
    def setUp(self):
        self.train = pd.DataFrame({"a": [1.0, 2.0, 3.0, np.nan], "flat": 5.0,
                                   "kind": pd.Categorical(["x", "y", "x", "y"])})
        self.prep = FeaturePrep().fit(self.train)

    def test_keeps_only_numeric_varying_columns(self):
        self.assertEqual(self.prep.columns, ["a"])

    def test_output_is_finite_and_standardised_on_train(self):
        out = self.prep.transform(self.train)
        self.assertEqual(out.shape, (4, 1))
        self.assertTrue(np.isfinite(out).all())
        self.assertAlmostEqual(out[:, 0].mean(), 0.0)

    def test_reuses_train_statistics_on_other_data(self):
        later = pd.DataFrame({"a": [100.0, np.nan], "flat": 0.0, "kind": pd.Categorical(["x", "x"])})
        out = self.prep.transform(later)
        median_row = self.prep.transform(pd.DataFrame({"a": [2.0]}))
        self.assertAlmostEqual(out[1, 0], median_row[0, 0])   # NaN becomes the train median, not the new data's
        self.assertGreater(out[0, 0], 10)                     # scaled by the train spread, so far out

    def test_transform_before_fit(self):
        with self.assertRaises(ValueError):
            FeaturePrep().transform(self.train)


class BaselineEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.ts = synthetic_set()

    def test_persistence_offsets_learn_the_drying(self):
        model = Persistence().fit(self.ts)
        self.assertAlmostEqual(model.offsets[24][0.5], -0.024, delta=0.001)
        q = model.predict(self.ts.X("validation"), 24)
        self.assertTrue((q["q10"] < q["q50"]).all())

    def test_ranking_on_validation(self):
        models = [Persistence(), Bucket(), LinearQuantile(stride=1)]
        for m in models:
            m.fit(self.ts)
        scores = evaluate(models, self.ts, "validation")
        self.assertEqual(len(scores), len(HORIZONS) * len(models))
        for h in HORIZONS:
            self.assertLessEqual(scores.loc[(h, "bucket"), "mae_q50"], scores.loc[(h, "persistence"), "mae_q50"])
            self.assertAlmostEqual(scores.loc[(h, "bucket"), "coverage_q10"], 0.1, delta=0.1)

    def test_unfitted_and_missing_inputs(self):
        with self.assertRaises(ValueError):
            Persistence().predict(self.ts.X("validation"), 6)
        with self.assertRaises(DataError):
            Bucket().fit(self.ts).point(self.ts.X("train"), 48)


if __name__ == "__main__":
    unittest.main()

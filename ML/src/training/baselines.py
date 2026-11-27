"""
baselines.py
Reference forecasters the soil-moisture forest has to beat, and the scores
they are compared on. All of them output the same thing as the forest: θ_rz
at each horizon as a 10th percentile and a median.

    models = [Persistence(), Bucket(), LinearQuantile()]
    for m in models:
        m.fit(training_set)
    scores = evaluate(models, training_set, "validation")

Persistence (θ stays where it is) is the floor; the FAO-56 bucket
(`theta_bucket_<h>h`) is the bar that says whether the ML adds anything over
physics; the linear quantile regression on the full feature matrix says
whether the forest's nonlinearity earns its cost.

Written by Diego Revilla on 27-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from garden import DataError

from .dataset import TrainingSet

QUANTILES: tuple[float, ...] = (0.1, 0.5)


def horizons_of(training_set: TrainingSet) -> tuple[int, ...]:
    """Horizons in hours, from the target columns (`y_theta_rz_<h>h`)."""
    cols = training_set.y(kind="theta").columns
    return tuple(sorted(int(c.removeprefix("y_theta_rz_").removesuffix("h")) for c in cols))


def quantile_column(q: float) -> str:
    return f"q{round(q * 100):02d}"


# ---------------------------------------------------------------------- scores
def pinball_loss(y: np.ndarray, pred: np.ndarray, q: float) -> float:
    r = y - pred
    return float(np.mean(np.where(r > 0, q * r, (q - 1) * r)))


def coverage(y: np.ndarray, pred: np.ndarray) -> float:
    """Share of observations below the predicted quantile; ≈ q when calibrated."""
    return float(np.mean(np.asarray(y) < np.asarray(pred)))


# ---------------------------------------------------------------------- models
class Baseline(ABC):
    """Fits on the train split and predicts θ_rz quantiles for one horizon."""

    name: str = "baseline"

    def __init__(self, quantiles: tuple[float, ...] = QUANTILES):
        if not quantiles or not all(0.0 < q < 1.0 for q in quantiles):
            raise ValueError("quantiles must lie strictly between 0 and 1")
        self.quantiles = tuple(sorted(quantiles))

    @abstractmethod
    def fit(self, training_set: TrainingSet) -> Baseline: ...

    @abstractmethod
    def predict(self, X: pd.DataFrame, horizon_h: int) -> pd.DataFrame:
        """One column per quantile (`q10`, `q50`), indexed like X; NaN where
        the model has no input."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def residual_offsets(residuals: pd.Series, quantiles: tuple[float, ...]) -> dict[float, float]:
    r = residuals.dropna()
    if r.empty:
        raise DataError("no residuals left after dropping NaN")
    return {q: float(r.quantile(q)) for q in quantiles}


class PointBaseline(Baseline):
    """A deterministic forecast made probabilistic by shifting it by the
    quantiles of its own train-split errors."""

    def __init__(self, quantiles: tuple[float, ...] = QUANTILES):
        super().__init__(quantiles)
        self.offsets: dict[int, dict[float, float]] = {}

    @abstractmethod
    def point(self, X: pd.DataFrame, horizon_h: int) -> pd.Series: ...

    def fit(self, training_set: TrainingSet) -> PointBaseline:
        X, y = training_set.X("train"), training_set.y("train", kind="theta")
        self.offsets = {h: residual_offsets(y[f"y_theta_rz_{h}h"] - self.point(X, h), self.quantiles)
                        for h in horizons_of(training_set)}
        return self

    def predict(self, X: pd.DataFrame, horizon_h: int) -> pd.DataFrame:
        if horizon_h not in self.offsets:
            raise ValueError(f"{self.name} was not fitted for {horizon_h} h")
        base = self.point(X, horizon_h)
        return pd.DataFrame({quantile_column(q): base + off for q, off in self.offsets[horizon_h].items()},
                            index=X.index)


class Persistence(PointBaseline):
    """θ_rz(t + h) = θ_rz(t)."""

    name = "persistence"

    def point(self, X: pd.DataFrame, horizon_h: int) -> pd.Series:
        return _column(X, "theta_rz")


class Bucket(PointBaseline):
    """The one-bucket FAO-56 projection from `WaterBalanceFeatures`."""

    name = "bucket"

    def point(self, X: pd.DataFrame, horizon_h: int) -> pd.Series:
        return _column(X, f"theta_bucket_{horizon_h}h")


def _column(X: pd.DataFrame, name: str) -> pd.Series:
    if name not in X.columns:
        raise DataError(f"feature {name!r} is not in the training set")
    return X[name]


class FeaturePrep:
    """Turns the feature matrix into what a linear model can use. Every
    statistic is learnt on the train split only and reused unchanged on
    validation and test, so nothing from those periods leaks into the fit."""

    def __init__(self):
        self.columns: list[str] = []
        self.medians = self.means = self.scales = None

    def fit(self, X: pd.DataFrame) -> FeaturePrep:
        numeric = X.select_dtypes(include=np.number)
        # keep columns with gaps (they are filled below); drop only the constant or all-NaN ones
        self.columns = [c for c in numeric.columns if numeric[c].nunique() > 1]
        self.medians = numeric[self.columns].median()
        filled = numeric[self.columns].fillna(self.medians)
        self.means = filled.mean()
        self.scales = filled.std(ddof=0).replace(0, 1)  # a column can still be flat once NaN are filled
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        if self.medians is None:
            raise ValueError("FeaturePrep.transform called before fit")
        missing = [c for c in self.columns if c not in X.columns]
        if missing:
            raise DataError(f"features missing at transform time: {', '.join(missing)}")
        filled = X[self.columns].astype(float).fillna(self.medians)   # train medians, never the new data's
        return ((filled - self.means) / self.scales).to_numpy()


class LinearQuantile(Baseline):
    """Linear quantile regression of the change Δθ_rz on all numeric features,
    one model per horizon and quantile; θ_rz is added back to give the level.

    `stride` fits on every stride-th train row: consecutive hours are nearly
    duplicates, and the exact solver grows faster than linearly with rows
    (17 k rows take about 18 s per model, every 4th row about 1 s)."""

    name = "linear_quantile"

    def __init__(self, quantiles: tuple[float, ...] = QUANTILES, *, alpha: float = 0.0, stride: int = 3):
        super().__init__(quantiles)
        if alpha < 0 or stride < 1:
            raise ValueError("alpha must be non-negative and stride at least 1")
        self.alpha, self.stride = alpha, stride
        self.prep = FeaturePrep()
        self.models: dict[tuple[int, float], object] = {}

    def fit(self, training_set: TrainingSet) -> LinearQuantile:
        from sklearn.linear_model import QuantileRegressor   # only this baseline needs scikit-learn

        X, dy = training_set.X("train"), training_set.y("train", kind="dtheta")
        self.prep.fit(X)
        features = self.prep.transform(X)
        for h in horizons_of(training_set):
            target = dy[f"y_dtheta_rz_{h}h"].to_numpy()
            rows = np.flatnonzero(np.isfinite(target))[::self.stride]
            if rows.size == 0:
                raise DataError(f"no train targets at {h} h")
            for q in self.quantiles:
                model = QuantileRegressor(quantile=q, alpha=self.alpha, solver="highs")
                self.models[(h, q)] = model.fit(features[rows], target[rows])
        return self

    def predict(self, X: pd.DataFrame, horizon_h: int) -> pd.DataFrame:
        if (horizon_h, self.quantiles[0]) not in self.models:
            raise ValueError(f"{self.name} was not fitted for {horizon_h} h")
        features, level = self.prep.transform(X), _column(X, "theta_rz")
        return pd.DataFrame({quantile_column(q): level + self.models[(horizon_h, q)].predict(features)
                             for q in self.quantiles}, index=X.index)

    def __repr__(self) -> str:
        return f"LinearQuantile(alpha={self.alpha}, stride={self.stride})"


# ---------------------------------------------------------------------- evaluation
def evaluate(models: list[Baseline], training_set: TrainingSet, split: str = "validation") -> pd.DataFrame:
    """Scores per model and horizon on one split. Every model is scored on the
    same rows (those where all of them and the target are defined), so the
    numbers are directly comparable. Pinball and MAE are in m³/m³."""
    X, y = training_set.X(split), training_set.y(split, kind="theta")
    rows = []
    for h in horizons_of(training_set):
        target = y[f"y_theta_rz_{h}h"]
        predictions = {m.name: m.predict(X, h) for m in models}
        valid = target.notna()
        for p in predictions.values():
            valid &= p.notna().all(axis=1)
        if not valid.any():
            raise DataError(f"no {split} rows where every model and the {h} h target are defined")
        observed = target[valid].to_numpy()
        for name, p in predictions.items():
            row = {"model": name, "horizon_h": h, "rows": int(valid.sum())}
            for q in models[0].quantiles:
                pred = p.loc[valid, quantile_column(q)].to_numpy()
                row[f"pinball_{quantile_column(q)}"] = pinball_loss(observed, pred, q)
                row[f"coverage_{quantile_column(q)}"] = coverage(observed, pred)
            if 0.5 in models[0].quantiles:
                row["mae_q50"] = float(np.mean(np.abs(observed - p.loc[valid, "q50"].to_numpy())))
            rows.append(row)
    return pd.DataFrame(rows).set_index(["horizon_h", "model"]).sort_index()

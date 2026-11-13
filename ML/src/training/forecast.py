"""
forecast.py
What the forecast said, as it was known at decision time.

The hourly table stores archived forecasts as ``<prefix><var>_lead<N>d``: the
value for hour T from a model run with a lead time of at least 24·N hours,
so that run started no later than T − 24·N h. At decision time t the
forecast for hour t + k is therefore only usable from a lead

    N(k) = ceil((k + lag) / 24)

where ``lag`` is how long after its start a run becomes available. Reading
any shorter lead would use a forecast that did not exist yet (leakage). This
class is the only place that turns lead columns into features, and it
refuses horizons the stored leads cannot cover.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import math
import re
import warnings

import numpy as np
import pandas as pd

from garden import DataError


class LeakageError(DataError):
    """A feature would need a forecast issued after the decision time."""


class ForecastView:

    def __init__(self, frame: pd.DataFrame, prefix: str = "fc_", availability_lag_h: int = 0):
        if frame.index.freq is None or frame.index.freq != pd.tseries.frequencies.to_offset("h"):
            raise DataError("ForecastView needs a regular hourly index (use asfreq('h'))")
        if availability_lag_h < 0:
            raise ValueError("availability lag must be non-negative")
        self.frame, self.prefix, self.lag = frame, prefix, availability_lag_h
        pattern = re.compile(rf"^{re.escape(prefix)}(.+)_lead(\d+)d$")
        self.leads: dict[str, set[int]] = {}
        for col in frame.columns:
            if m := pattern.match(col):
                self.leads.setdefault(m.group(1), set()).add(int(m.group(2)))

    def lead_for(self, variable: str, hours_ahead: int) -> int:
        """Smallest stored lead (days) that was available ``hours_ahead`` early."""
        if hours_ahead < 1:
            raise ValueError("hours_ahead starts at 1 (the hour after the decision)")
        if variable not in self.leads:
            raise DataError(f"no forecast columns for {variable!r}; have {sorted(self.leads)}")
        needed = math.ceil((hours_ahead + self.lag) / 24)
        usable = [n for n in sorted(self.leads[variable]) if n >= needed]
        if not usable:
            raise LeakageError(f"{variable} {hours_ahead} h ahead needs lead ≥ {needed} d; "
                               f"stored leads are {sorted(self.leads[variable])}")
        return usable[0]

    def max_horizon(self, variable: str) -> int:
        """Longest window (hours) this variable can cover without leakage."""
        return 24 * max(self.leads.get(variable, {0})) - self.lag

    def hour_ahead(self, variable: str, hours_ahead: int) -> pd.Series:
        """At each t: the forecast for hour t + k, as known at t."""
        column = f"{self.prefix}{variable}_lead{self.lead_for(variable, hours_ahead)}d"
        return self.frame[column].shift(-hours_ahead)

    def window(self, variable: str, hours: int, how: str = "sum", *, start: int = 1,
               min_coverage: float = 0.9) -> pd.Series:
        """Aggregate of hours t+start … t+hours. A sum over a partly missing
        window is scaled up to the full window when at least ``min_coverage``
        of its hours exist, and NaN otherwise."""
        block = np.column_stack([self.hour_ahead(variable, k).to_numpy() for k in range(start, hours + 1)])
        present = np.isfinite(block)
        coverage = present.mean(axis=1)
        with warnings.catch_warnings():  # all-NaN rows are expected at the edges of the record
            warnings.simplefilter("ignore", RuntimeWarning)
            if how == "sum":
                values = np.nanmean(block, axis=1) * block.shape[1]
            elif how == "max":
                values = np.nanmax(block, axis=1)
            elif how == "mean":
                values = np.nanmean(block, axis=1)
            else:
                raise ValueError(f"unknown aggregation {how!r}")
        values[coverage < min_coverage] = np.nan
        return pd.Series(values, index=self.frame.index)

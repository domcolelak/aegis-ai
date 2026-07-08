"""Incremental EWMA mean/variance used as detector baselines."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class EwmaStats:
    """Exponentially weighted mean and variance (West's update)."""

    alpha: float = 0.3
    mean: float = 0.0
    variance: float = 0.0
    samples: int = 0

    def z_score(self, value: float) -> float:
        """How many baseline deviations ``value`` sits above the mean.

        On a perfectly constant history the variance collapses to zero, which
        would make any change infinitely anomalous; a Poisson-style floor
        (std >= sqrt(mean)) keeps count data sanely scaled instead.
        """
        if self.samples == 0:
            return 0.0
        std = math.sqrt(self.variance)
        floor = math.sqrt(max(self.mean, 1e-9))
        return (value - self.mean) / max(std, floor, 1e-9)

    def update(self, value: float, weight: float = 1.0) -> None:
        """Fold ``value`` into the baseline.

        ``weight`` in (0, 1] scales the learning rate: observations backed by
        little data (a ratio computed from a handful of events) should nudge
        the baseline, not own it. A full-weight first sample initializes the
        mean directly; partial-weight updates blend from the zero prior.
        """
        if weight <= 0:
            return
        effective_alpha = self.alpha * min(weight, 1.0)
        if self.samples == 0 and weight >= 1.0:
            self.mean = value
            self.samples = 1
            return
        diff = value - self.mean
        incr = effective_alpha * diff
        self.mean += incr
        self.variance = (1.0 - effective_alpha) * (self.variance + diff * incr)
        self.samples += 1

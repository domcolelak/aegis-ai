"""Detects services whose error share of traffic leaves its baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from aegis.detection._stats import EwmaStats
from aegis.detection._windows import window_bounds, window_end, window_index
from aegis.detection.models import AnomalyCluster, AnomalyKind
from aegis.events import Severity

if TYPE_CHECKING:
    from datetime import datetime

    from aegis.events import LogEvent


@dataclass(slots=True, frozen=True)
class ErrorRatioConfig:
    window: timedelta = timedelta(seconds=10)
    alpha: float = 0.3
    min_events: int = 20
    min_ratio: float = 0.10
    # Anomalous when the ratio exceeds max(min_ratio, factor * baseline).
    factor: float = 3.0
    warmup_windows: int = 3
    max_representatives: int = 10


@dataclass(slots=True)
class _Bucket:
    total: int = 0
    errors: int = 0
    representatives: list[UUID] = field(default_factory=list)


class ErrorRatioDetector:
    """Ratio, not count: 40 errors in 100 events is an incident signal, the
    same 40 errors in 40 000 events is background noise. Complements the
    frequency detector, which sees absolute rates only."""

    def __init__(self, config: ErrorRatioConfig | None = None) -> None:
        self._config = config or ErrorRatioConfig()
        self._buckets: dict[tuple[str, int], _Bucket] = {}
        self._baselines: dict[str, EwmaStats] = {}

    def observe(self, event: LogEvent) -> None:
        config = self._config
        index = window_index(event.timestamp, config.window)
        bucket = self._buckets.setdefault((event.service, index), _Bucket())
        bucket.total += 1
        if event.severity >= Severity.ERROR:
            bucket.errors += 1
            if len(bucket.representatives) < config.max_representatives:
                bucket.representatives.append(event.event_id)

    def flush(self, now: datetime) -> list[AnomalyCluster]:
        config = self._config
        clusters: list[AnomalyCluster] = []
        due = sorted(
            (
                item
                for item in self._buckets.items()
                if window_end(item[0][1], config.window) <= now
            ),
            key=lambda item: item[0][1],
        )
        for (service, index), bucket in due:
            del self._buckets[(service, index)]
            baseline = self._baselines.setdefault(service, EwmaStats(alpha=config.alpha))
            ratio = bucket.errors / bucket.total if bucket.total else 0.0

            threshold = max(config.min_ratio, config.factor * baseline.mean)
            if (
                baseline.samples >= config.warmup_windows
                and bucket.total >= config.min_events
                and ratio >= threshold
            ):
                clusters.append(
                    AnomalyCluster(
                        cluster_id=uuid4(),
                        kind=AnomalyKind.ERROR_RATIO_DEVIATION,
                        service=service,
                        window=window_bounds(index, config.window),
                        event_count=bucket.errors,
                        confidence=min(0.99, (ratio - baseline.mean) / max(ratio, 1e-9)),
                        representative_events=tuple(bucket.representatives),
                        attributes={
                            "error_ratio": round(ratio, 4),
                            "baseline_ratio": round(baseline.mean, 4),
                            "errors": bucket.errors,
                            "total": bucket.total,
                        },
                    )
                )
            # Volume-weighted learning: a ratio computed from a handful of
            # events must not poison the baseline (9 errors out of 9 events
            # is not evidence that "100% errors is normal"), while steady
            # low-volume traffic should still teach it.
            baseline.update(ratio, weight=bucket.total / config.min_events)
        return clusters

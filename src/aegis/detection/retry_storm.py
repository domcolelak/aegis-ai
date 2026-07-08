"""Detects retry storms: bursts of TASK_RETRY events far above baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from aegis.detection._stats import EwmaStats
from aegis.detection._windows import window_bounds, window_end, window_index
from aegis.detection.models import AnomalyCluster, AnomalyKind
from aegis.events import EventKind

if TYPE_CHECKING:
    from datetime import datetime

    from aegis.events import LogEvent


@dataclass(slots=True, frozen=True)
class RetryStormConfig:
    window: timedelta = timedelta(seconds=10)
    alpha: float = 0.3
    min_retries: int = 15
    factor: float = 3.0
    # A count this high is a storm regardless of any baseline; retries are a
    # amplification mechanism, so absolute volume alone is meaningful.
    absolute_threshold: int = 50
    max_representatives: int = 10


@dataclass(slots=True)
class _Bucket:
    count: int = 0
    representatives: list[UUID] = field(default_factory=list)


class RetryStormDetector:
    def __init__(self, config: RetryStormConfig | None = None) -> None:
        self._config = config or RetryStormConfig()
        self._buckets: dict[tuple[str, int], _Bucket] = {}
        self._baselines: dict[str, EwmaStats] = {}

    def observe(self, event: LogEvent) -> None:
        if event.kind is not EventKind.TASK_RETRY:
            return
        index = window_index(event.timestamp, self._config.window)
        bucket = self._buckets.setdefault((event.service, index), _Bucket())
        bucket.count += 1
        if len(bucket.representatives) < self._config.max_representatives:
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
            over_baseline = bucket.count >= config.factor * max(baseline.mean, 1.0)
            is_storm = bucket.count >= config.min_retries and (
                over_baseline or bucket.count >= config.absolute_threshold
            )
            if is_storm:
                surge = bucket.count / max(baseline.mean, 1.0)
                clusters.append(
                    AnomalyCluster(
                        cluster_id=uuid4(),
                        kind=AnomalyKind.RETRY_STORM,
                        service=service,
                        window=window_bounds(index, config.window),
                        event_count=bucket.count,
                        confidence=min(0.99, surge / (surge + config.factor)),
                        representative_events=tuple(bucket.representatives),
                        attributes={
                            "retries": bucket.count,
                            "baseline_mean": round(baseline.mean, 3),
                        },
                    )
                )
            baseline.update(float(bucket.count))
        return clusters

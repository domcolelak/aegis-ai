"""Frequency spike detection per (service, signature) with an EWMA baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from aegis.detection._stats import EwmaStats
from aegis.detection._windows import window_bounds, window_end, window_index
from aegis.detection.models import AnomalyCluster, AnomalyKind

if TYPE_CHECKING:
    from datetime import datetime

    from aegis.events import LogEvent

type _Key = tuple[str, str]  # (service, signature fingerprint)


@dataclass(slots=True, frozen=True)
class FrequencyConfig:
    window: timedelta = timedelta(seconds=10)
    alpha: float = 0.3
    z_threshold: float = 3.0
    min_count: int = 10
    warmup_windows: int = 3
    max_representatives: int = 10
    # Cap on zero-filled gap windows so an idle year cannot stall a flush.
    max_gap_fill: int = 50


@dataclass(slots=True)
class _Bucket:
    count: int = 0
    representatives: list[UUID] = field(default_factory=list)


class FrequencySpikeDetector:
    """Flags windows where one signature's rate leaves its own baseline.

    Baselines are per (service, signature): a template that normally appears
    five times per window and suddenly appears five hundred times is anomalous
    even if the service's total volume barely moves. Quiet windows between
    activity are zero-filled into the baseline so sensitivity recovers after
    idle periods.
    """

    def __init__(self, config: FrequencyConfig | None = None) -> None:
        self._config = config or FrequencyConfig()
        self._buckets: dict[tuple[_Key, int], _Bucket] = {}
        self._baselines: dict[_Key, EwmaStats] = {}
        self._last_window: dict[_Key, int] = {}
        self._templates: dict[str, str] = {}

    def observe(self, event: LogEvent) -> None:
        key = (event.service, event.signature.fingerprint)
        index = window_index(event.timestamp, self._config.window)
        bucket = self._buckets.setdefault((key, index), _Bucket())
        bucket.count += 1
        if len(bucket.representatives) < self._config.max_representatives:
            bucket.representatives.append(event.event_id)
        self._templates.setdefault(event.signature.fingerprint, event.signature.template)

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
        for (key, index), bucket in due:
            del self._buckets[(key, index)]
            baseline = self._baselines.setdefault(key, EwmaStats(alpha=config.alpha))

            if (last := self._last_window.get(key)) is not None:
                for _ in range(min(index - last - 1, config.max_gap_fill)):
                    baseline.update(0.0)
            self._last_window[key] = index

            z = baseline.z_score(float(bucket.count))
            warmed_up = baseline.samples >= config.warmup_windows
            if warmed_up and bucket.count >= config.min_count and z >= config.z_threshold:
                service, fingerprint = key
                clusters.append(
                    AnomalyCluster(
                        cluster_id=uuid4(),
                        kind=AnomalyKind.FREQUENCY_SPIKE,
                        service=service,
                        window=window_bounds(index, config.window),
                        event_count=bucket.count,
                        confidence=min(0.99, z / (z + config.z_threshold)),
                        representative_events=tuple(bucket.representatives),
                        attributes={
                            "observed": bucket.count,
                            "baseline_mean": round(baseline.mean, 3),
                            "z_score": round(z, 2),
                            "template": self._templates.get(fingerprint, ""),
                        },
                    )
                )
            baseline.update(float(bucket.count))
        return clusters

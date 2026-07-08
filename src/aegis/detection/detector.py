"""Detector seam and the engine that fans events out to all detectors.

Detectors are incremental: ``observe`` must be cheap (it runs per event on
the hot path) and may buffer; ``flush`` finalizes every window that ended at
or before ``now`` and emits clusters. Event time drives the windows, not
arrival time, so replayed history detects identically to live streams.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from aegis.detection.models import AnomalyCluster
    from aegis.events import LogEvent


class AnomalyDetector(Protocol):
    def observe(self, event: LogEvent) -> None: ...

    def flush(self, now: datetime) -> Sequence[AnomalyCluster]: ...


class DetectionEngine:
    def __init__(self, detectors: Sequence[AnomalyDetector]) -> None:
        self._detectors = list(detectors)

    def observe(self, event: LogEvent) -> None:
        for detector in self._detectors:
            detector.observe(event)

    def flush(self, now: datetime) -> list[AnomalyCluster]:
        clusters = [cluster for detector in self._detectors for cluster in detector.flush(now)]
        clusters.sort(key=lambda cluster: cluster.window.start)
        return clusters

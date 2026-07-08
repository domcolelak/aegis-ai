"""Anomaly domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from aegis.events import AttributeValue, TimeWindow


class AnomalyKind(StrEnum):
    FREQUENCY_SPIKE = auto()
    NEW_SIGNATURE = auto()
    ERROR_RATIO_DEVIATION = auto()
    RETRY_STORM = auto()


@dataclass(slots=True, frozen=True)
class AnomalyCluster:
    """A group of events a detector judged anomalous, with its evidence.

    ``attributes`` carries the numbers that triggered the detection (observed
    rate, baseline, z-score, ...): a cluster must be auditable without
    re-running the detector.
    """

    cluster_id: UUID
    kind: AnomalyKind
    service: str
    window: TimeWindow
    event_count: int
    confidence: float
    representative_events: tuple[UUID, ...] = ()
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)

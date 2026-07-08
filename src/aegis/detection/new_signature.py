"""Detects log templates never seen before (new error signatures)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from aegis.detection.models import AnomalyCluster, AnomalyKind
from aegis.events import Severity, TimeWindow

if TYPE_CHECKING:
    from datetime import datetime

    from aegis.events import LogEvent

type _Key = tuple[str, str]  # (service, signature fingerprint)


@dataclass(slots=True, frozen=True)
class NewSignatureConfig:
    # Signatures seen among the first N events form the vocabulary baseline;
    # without a learning phase, startup would flag everything as new.
    learning_events: int = 500
    min_severity: Severity = Severity.WARNING
    max_representatives: int = 10


@dataclass(slots=True)
class _Pending:
    template: str
    first_seen: datetime
    last_seen: datetime
    count: int = 0
    representatives: list[UUID] = field(default_factory=list)


class NewSignatureDetector:
    """A WARNING-or-worse template appearing for the first time is evidence.

    "First connection timeout ever logged by booking-api" is exactly the kind
    of event that starts an incident timeline. Low-severity new templates are
    absorbed into the vocabulary silently -- new INFO messages usually just
    mean a deploy.
    """

    def __init__(self, config: NewSignatureConfig | None = None) -> None:
        self._config = config or NewSignatureConfig()
        self._known: set[_Key] = set()
        self._pending: dict[_Key, _Pending] = {}
        self._events_seen = 0

    def observe(self, event: LogEvent) -> None:
        self._events_seen += 1
        key = (event.service, event.signature.fingerprint)
        if key in self._known:
            return
        learning = self._events_seen <= self._config.learning_events
        if learning or event.severity < self._config.min_severity:
            self._known.add(key)
            return

        pending = self._pending.get(key)
        if pending is None:
            pending = _Pending(
                template=event.signature.template,
                first_seen=event.timestamp,
                last_seen=event.timestamp,
            )
            self._pending[key] = pending
        pending.count += 1
        pending.first_seen = min(pending.first_seen, event.timestamp)
        pending.last_seen = max(pending.last_seen, event.timestamp)
        if len(pending.representatives) < self._config.max_representatives:
            pending.representatives.append(event.event_id)

    def flush(self, now: datetime) -> list[AnomalyCluster]:
        clusters: list[AnomalyCluster] = []
        for (service, _fingerprint), pending in sorted(
            self._pending.items(), key=lambda item: item[1].first_seen
        ):
            clusters.append(
                AnomalyCluster(
                    cluster_id=uuid4(),
                    kind=AnomalyKind.NEW_SIGNATURE,
                    service=service,
                    window=TimeWindow(start=pending.first_seen, end=pending.last_seen),
                    event_count=pending.count,
                    # Novelty is binary; recurrence raises confidence a little.
                    confidence=min(0.9, 0.7 + 0.02 * pending.count),
                    representative_events=tuple(pending.representatives),
                    attributes={"template": pending.template},
                )
            )
        self._known.update(self._pending)
        self._pending.clear()
        return clusters

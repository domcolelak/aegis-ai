"""In-memory incident dataset the tools query.

This is the tool-facing read model: events, clusters, and the causal graph
for one incident, with the small set of queries the investigation actually
needs. When the persistence milestone lands, a repository-backed twin can
replace it behind the same method surface.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aegis.events import EventKind, Severity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from aegis.detection import AnomalyCluster
    from aegis.events import LogEvent
    from aegis.graph import IncidentGraph


def event_digest(event: LogEvent) -> dict[str, object]:
    """The compact JSON shape events take inside tool results and prompts."""
    digest: dict[str, object] = {
        "event_id": str(event.event_id),
        "ts": event.timestamp.isoformat(),
        "service": event.service,
        "severity": event.severity.name,
        "kind": event.kind.value,
        "message": event.message[:200],
    }
    if event.trace_id is not None:
        digest["trace_id"] = event.trace_id
    return digest


class InvestigationDataStore:
    def __init__(
        self,
        events: Sequence[LogEvent],
        clusters: Sequence[AnomalyCluster],
        graph: IncidentGraph,
        dependency_map: Mapping[str, frozenset[str]],
    ) -> None:
        self._events = sorted(events, key=lambda event: event.timestamp)
        self._by_id: dict[UUID, LogEvent] = {event.event_id: event for event in self._events}
        self._by_fingerprint: dict[str, list[LogEvent]] = defaultdict(list)
        for event in self._events:
            self._by_fingerprint[event.signature.fingerprint].append(event)
        self.clusters: tuple[AnomalyCluster, ...] = tuple(clusters)
        self.graph = graph
        self.dependency_map = dependency_map

    @property
    def events(self) -> Sequence[LogEvent]:
        return self._events

    def event(self, event_id: UUID) -> LogEvent | None:
        return self._by_id.get(event_id)

    def events_in_window(
        self,
        start: datetime,
        end: datetime,
        *,
        service: str | None = None,
        min_severity: Severity = Severity.DEBUG,
        limit: int = 30,
    ) -> list[LogEvent]:
        matches = [
            event
            for event in self._events
            if start <= event.timestamp <= end
            and event.severity >= min_severity
            and (service is None or event.service == service)
        ]
        return matches[:limit]

    def search(self, query: str, *, only_errors: bool = False, limit: int = 30) -> list[LogEvent]:
        needle = query.lower()
        matches = [
            event
            for event in self._events
            if needle in event.message.lower()
            and (not only_errors or event.severity >= Severity.ERROR)
        ]
        return matches[:limit]

    def similar_to(self, event_id: UUID, *, limit: int = 20) -> tuple[list[LogEvent], int]:
        """Events sharing the reference event's template; (samples, total)."""
        reference = self._by_id.get(event_id)
        if reference is None:
            return [], 0
        family = self._by_fingerprint[reference.signature.fingerprint]
        return family[:limit], len(family)

    def error_rate(
        self, service: str, start: datetime, end: datetime, *, bucket_s: int = 10
    ) -> list[dict[str, object]]:
        buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # [total, errors]
        for event in self._events:
            if event.service != service or not start <= event.timestamp <= end:
                continue
            index = int(event.timestamp.timestamp() // bucket_s)
            buckets[index][0] += 1
            if event.severity >= Severity.ERROR:
                buckets[index][1] += 1
        return [
            {
                "bucket_start": _bucket_iso(index, bucket_s),
                "total": total,
                "errors": errors,
                "ratio": round(errors / total, 4) if total else 0.0,
            }
            for index, (total, errors) in sorted(buckets.items())
        ]

    def pool_pressure(self, start: datetime, end: datetime) -> dict[str, dict[str, object]]:
        """DB_POOL evidence per service inside the window."""
        hits: dict[str, list[LogEvent]] = defaultdict(list)
        for event in self._events:
            if event.kind is EventKind.DB_POOL and start <= event.timestamp <= end:
                hits[event.service].append(event)
        return {
            service: {
                "count": len(events),
                "first": events[0].timestamp.isoformat(),
                "last": events[-1].timestamp.isoformat(),
                "sample": events[0].message[:200],
            }
            for service, events in hits.items()
        }


def _bucket_iso(index: int, bucket_s: int) -> str:
    return datetime.fromtimestamp(index * bucket_s, tz=UTC).isoformat()

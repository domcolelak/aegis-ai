"""Shared fixtures: a hand-built miniature incident for investigation tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegis.correlation import CausalEdge
from aegis.detection import AnomalyCluster, AnomalyKind
from aegis.events import EventKind, LogEvent, Severity, TimeWindow
from aegis.graph import IncidentGraph
from aegis.investigation.data import InvestigationDataStore
from aegis.investigation.evidence import EvidenceBundle, build_evidence
from aegis.parsing import signature_of

BASE = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)

DEPENDENCIES: dict[str, frozenset[str]] = {
    "booking-api": frozenset({"postgres", "payments"}),
    "worker": frozenset({"postgres"}),
}


def _event(
    message: str,
    *,
    service: str,
    offset_s: float,
    severity: Severity = Severity.ERROR,
    kind: EventKind = EventKind.GENERIC,
    trace_id: str | None = None,
) -> LogEvent:
    return LogEvent(
        event_id=uuid4(),
        timestamp=BASE + timedelta(seconds=offset_s),
        service=service,
        source_id=f"{service}.log",
        severity=severity,
        kind=kind,
        message=message,
        signature=signature_of(message),
        trace_id=trace_id,
    )


def _edge(source: LogEvent, target: LogEvent, score: float) -> CausalEdge:
    return CausalEdge(
        source_event=source.event_id,
        target_event=target.event_id,
        composite_score=score,
        strategy_scores={"stub": score},
    )


@dataclass(slots=True, frozen=True)
class IncidentFixture:
    dataset: InvestigationDataStore
    evidence: EvidenceBundle
    events: dict[str, LogEvent]


@pytest.fixture
def small_incident() -> IncidentFixture:
    stripe = _event(
        "stripe payment request timed out after 30000ms",
        service="payments",
        offset_s=0,
        kind=EventKind.EXTERNAL_CALL,
        trace_id="t-1",
    )
    leak = _event(
        "Unhandled TimeoutError in create_booking: database session left open",
        service="booking-api",
        offset_s=3,
        kind=EventKind.EXCEPTION,
        trace_id="t-1",
    )
    pool = _event(
        "QueuePool limit of size 100 overflow 10 reached",
        service="booking-api",
        offset_s=10,
        kind=EventKind.DB_POOL,
    )
    pg = _event(
        "remaining connection slots are reserved for superuser connections",
        service="postgres",
        offset_s=15,
        severity=Severity.CRITICAL,
        kind=EventKind.DB_POOL,
    )
    retries = [
        _event(
            f"Retrying create_booking (attempt {n})",
            service="worker",
            offset_s=20 + n,
            severity=Severity.WARNING,
            kind=EventKind.TASK_RETRY,
        )
        for n in range(1, 4)
    ]
    outage = _event(
        "POST /api/bookings HTTP/1.1 500 (12034ms)",
        service="booking-api",
        offset_s=30,
        kind=EventKind.HTTP_REQUEST,
    )
    noise = _event(
        "cache warmed for tenant acme",
        service="booking-api",
        offset_s=1,
        severity=Severity.INFO,
    )

    events = [stripe, leak, pool, pg, *retries, outage, noise]
    edges = [
        _edge(stripe, leak, 0.85),
        _edge(leak, pool, 0.7),
        _edge(pool, pg, 0.7),
        _edge(pg, retries[0], 0.6),
        _edge(pool, outage, 0.6),
    ]
    cluster = AnomalyCluster(
        cluster_id=uuid4(),
        kind=AnomalyKind.RETRY_STORM,
        service="worker",
        window=TimeWindow(start=retries[0].timestamp, end=retries[-1].timestamp),
        event_count=len(retries),
        confidence=0.9,
        representative_events=tuple(event.event_id for event in retries),
        attributes={"retries": len(retries)},
    )
    dataset = InvestigationDataStore(events, [cluster], IncidentGraph(events, edges), DEPENDENCIES)
    return IncidentFixture(
        dataset=dataset,
        evidence=build_evidence(dataset),
        events={
            "stripe": stripe,
            "leak": leak,
            "pool": pool,
            "pg": pg,
            "retry": retries[0],
            "outage": outage,
            "noise": noise,
        },
    )

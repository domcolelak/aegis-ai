"""Repositories: the only place SQL lives.

EventRepository takes the engine directly -- its hot path is a COPY through
the raw asyncpg connection (an order of magnitude faster than executemany at
log volumes), which is a Core/driver-level concern. The other repositories
work in ORM units of work through the session factory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aegis.db.models import (
    AgentFindingRecord,
    AnomalyClusterRecord,
    CausalEdgeRecord,
    EventSignatureRecord,
    IncidentMemoryRecord,
    IncidentRecord,
    InvestigationRecord,
    LogEventRecord,
    ToolExecutionRecord,
)
from aegis.events import EventKind, EventSignature, LogEvent, Severity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from aegis.correlation import CausalEdge
    from aegis.detection import AnomalyCluster
    from aegis.events import TimeWindow
    from aegis.investigation.orchestrator import InvestigationResult

_COPY_COLUMNS = (
    "event_id",
    "timestamp",
    "service",
    "source_id",
    "severity",
    "kind",
    "message",
    "fingerprint",
    "trace_id",
    "request_id",
    "host",
    "attributes",
)


class EventRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def bulk_insert(self, events: Sequence[LogEvent]) -> int:
        """Signature upsert + COPY of the events, in one transaction."""
        if not events:
            return 0
        signatures = {event.signature.fingerprint: event.signature.template for event in events}
        async with self._engine.begin() as conn:
            await conn.execute(
                pg_insert(EventSignatureRecord)
                .values(
                    [
                        {"fingerprint": fingerprint, "template": template}
                        for fingerprint, template in signatures.items()
                    ]
                )
                .on_conflict_do_nothing(index_elements=["fingerprint"])
            )
            raw = await conn.get_raw_connection()
            driver = cast("asyncpg.Connection[asyncpg.Record]", raw.driver_connection)
            await driver.copy_records_to_table(
                "log_events",
                records=[
                    (
                        event.event_id,
                        event.timestamp,
                        event.service,
                        event.source_id,
                        int(event.severity),
                        event.kind.value,
                        event.message,
                        event.signature.fingerprint,
                        event.trace_id,
                        event.request_id,
                        event.host,
                        json.dumps(dict(event.attributes)),
                    )
                    for event in events
                ],
                columns=_COPY_COLUMNS,
            )
        return len(events)

    async def count(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(select(func.count()).select_from(LogEventRecord))
            return int(result.scalar_one())

    async def fetch_window(
        self,
        start: datetime,
        end: datetime,
        *,
        service: str | None = None,
        min_severity: Severity = Severity.DEBUG,
        limit: int = 10_000,
    ) -> list[LogEvent]:
        stmt = (
            select(LogEventRecord, EventSignatureRecord.template)
            .join(
                EventSignatureRecord,
                LogEventRecord.fingerprint == EventSignatureRecord.fingerprint,
            )
            .where(
                LogEventRecord.timestamp >= start,
                LogEventRecord.timestamp <= end,
                LogEventRecord.severity >= int(min_severity),
            )
            .order_by(LogEventRecord.timestamp)
            .limit(limit)
        )
        if service is not None:
            stmt = stmt.where(LogEventRecord.service == service)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_to_domain(row.LogEventRecord, row.template) for row in rows]


def _to_domain(record: LogEventRecord, template: str) -> LogEvent:
    # JSONB comes back as dict[str, object]; the values were scalar
    # AttributeValues when we wrote them (see bulk_insert), so narrow back.
    attributes = cast("Mapping[str, str | int | float | bool]", record.attributes)
    return LogEvent(
        event_id=record.event_id,
        timestamp=record.timestamp,
        service=record.service,
        source_id=record.source_id,
        severity=Severity(record.severity),
        kind=EventKind(record.kind),
        message=record.message,
        signature=EventSignature(template=template, fingerprint=record.fingerprint),
        trace_id=record.trace_id,
        request_id=record.request_id,
        host=record.host,
        attributes=attributes,
    )


class IncidentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        window: TimeWindow,
        *,
        clusters: Sequence[AnomalyCluster] = (),
        edges: Sequence[CausalEdge] = (),
        summary: str | None = None,
    ) -> UUID:
        incident = IncidentRecord(window_start=window.start, window_end=window.end, summary=summary)
        async with self._sessions.begin() as session:
            session.add(incident)
            session.add_all(
                AnomalyClusterRecord(
                    cluster_id=cluster.cluster_id,
                    incident_id=incident.incident_id,
                    kind=cluster.kind.value,
                    service=cluster.service,
                    window_start=cluster.window.start,
                    window_end=cluster.window.end,
                    event_count=cluster.event_count,
                    confidence=cluster.confidence,
                    attributes=dict(cluster.attributes),
                    representative_events=[str(rid) for rid in cluster.representative_events],
                )
                for cluster in clusters
            )
            session.add_all(
                CausalEdgeRecord(
                    incident_id=incident.incident_id,
                    source_event=edge.source_event,
                    target_event=edge.target_event,
                    composite_score=edge.composite_score,
                    strategy_scores=dict(edge.strategy_scores),
                )
                for edge in edges
            )
        return incident.incident_id

    async def get(self, incident_id: UUID) -> IncidentRecord | None:
        async with self._sessions() as session:
            return await session.get(IncidentRecord, incident_id)

    async def list_recent(self, *, limit: int = 50) -> list[IncidentRecord]:
        async with self._sessions() as session:
            stmt = select(IncidentRecord).order_by(IncidentRecord.created_at.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars())

    async def set_status(self, incident_id: UUID, status: str) -> None:
        async with self._sessions.begin() as session:
            incident = await session.get(IncidentRecord, incident_id)
            if incident is None:
                raise KeyError(f"incident {incident_id} not found")
            incident.status = status

    async def edges_for(self, incident_id: UUID) -> list[CausalEdgeRecord]:
        async with self._sessions() as session:
            stmt = select(CausalEdgeRecord).where(CausalEdgeRecord.incident_id == incident_id)
            return list((await session.execute(stmt)).scalars())


class InvestigationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def persist(self, incident_id: UUID, result: InvestigationResult) -> None:
        async with self._sessions.begin() as session:
            session.add(
                InvestigationRecord(
                    investigation_id=result.investigation_id,
                    incident_id=incident_id,
                    status="completed",
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    assessment=result.assessment.model_dump(mode="json"),
                    challenge=result.challenge.model_dump(mode="json"),
                )
            )
            session.add_all(
                AgentFindingRecord(
                    investigation_id=result.investigation_id,
                    agent=agent,
                    finding=finding.model_dump(mode="json"),
                )
                for agent, finding in result.findings.items()
            )
            session.add_all(
                ToolExecutionRecord(
                    investigation_id=result.investigation_id,
                    agent=execution.agent,
                    tool=execution.tool,
                    arguments=dict(execution.arguments),
                    outcome=execution.outcome,
                    detail=execution.detail,
                    duration_ms=execution.duration_ms,
                    at=execution.at,
                )
                for execution in result.tool_executions
            )

    async def get(self, investigation_id: UUID) -> InvestigationRecord | None:
        async with self._sessions() as session:
            return await session.get(InvestigationRecord, investigation_id)

    async def latest_for_incident(self, incident_id: UUID) -> InvestigationRecord | None:
        async with self._sessions() as session:
            stmt = (
                select(InvestigationRecord)
                .where(InvestigationRecord.incident_id == incident_id)
                .order_by(InvestigationRecord.started_at.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalars().first()


class MemoryRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, record: IncidentMemoryRecord) -> UUID:
        async with self._sessions.begin() as session:
            session.add(record)
        return record.id

    async def similar(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 3,
        exclude_incident: UUID | None = None,
    ) -> list[tuple[IncidentMemoryRecord, float]]:
        """Top-k nearest by cosine distance; returns (record, distance)."""
        distance = IncidentMemoryRecord.embedding.cosine_distance(list(embedding))
        stmt = (
            select(IncidentMemoryRecord, distance.label("distance")).order_by(distance).limit(limit)
        )
        if exclude_incident is not None:
            stmt = stmt.where(IncidentMemoryRecord.incident_id.is_distinct_from(exclude_incident))
        async with self._sessions() as session:
            rows = (await session.execute(stmt)).all()
        return [(row.IncidentMemoryRecord, float(row.distance)) for row in rows]

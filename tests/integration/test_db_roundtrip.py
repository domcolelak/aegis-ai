"""Integration tests against real PostgreSQL + pgvector.

Gated by AEGIS_TEST_DATABASE_URL (set by the CI service container); skipped
locally when no database is available. The session fixture runs the real
Alembic migration, so these tests also verify the hand-written migration
matches the ORM models.
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aegis.db import (
    EventRepository,
    IncidentRepository,
    InvestigationRepository,
    MemoryRepository,
    create_db_engine,
    create_session_factory,
)
from aegis.events import EventKind, LogEvent, Severity, TimeWindow
from aegis.investigation.assessment import (
    AdvocateChallenge,
    RootCauseAssessment,
    SpecialistFinding,
)
from aegis.investigation.orchestrator import InvestigationResult
from aegis.investigation.tools.base import ToolExecution
from aegis.memory import HashingEmbedder, IncidentMemory
from aegis.parsing import signature_of

DATABASE_URL = os.environ.get("AEGIS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="AEGIS_TEST_DATABASE_URL not set"),
]

BASE = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)

type Db = tuple[AsyncEngine, async_sessionmaker[AsyncSession]]


@pytest.fixture(scope="session")
def migrated_database() -> str:
    assert DATABASE_URL is not None
    os.environ["AEGIS_DATABASE_URL"] = DATABASE_URL
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    return DATABASE_URL


@pytest.fixture
async def db(migrated_database: str) -> AsyncGenerator[Db]:
    engine = create_db_engine(migrated_database)
    try:
        yield engine, create_session_factory(engine)
    finally:
        await engine.dispose()


def make_event(index: int, *, service: str = "booking-api") -> LogEvent:
    message = f"connection timeout after {index}ms"
    return LogEvent(
        event_id=uuid4(),
        timestamp=BASE + timedelta(seconds=index),
        service=service,
        source_id=f"{service}.log",
        severity=Severity.ERROR if index % 3 == 0 else Severity.INFO,
        kind=EventKind.EXTERNAL_CALL,
        message=message,
        signature=signature_of(message),
        trace_id=f"t-{index}",
        attributes={"attempt": index},
    )


async def test_copy_bulk_insert_and_window_fetch(db: Db) -> None:
    engine, _sessions = db
    repo = EventRepository(engine)
    events = [make_event(i) for i in range(500)]
    events += [make_event(i, service="worker") for i in range(50)]

    inserted = await repo.bulk_insert(events)

    assert inserted == 550
    fetched = await repo.fetch_window(
        BASE,
        BASE + timedelta(seconds=100),
        service="booking-api",
        min_severity=Severity.ERROR,
    )
    assert fetched, "window fetch must return the inserted error events"
    assert all(event.service == "booking-api" for event in fetched)
    assert all(event.severity >= Severity.ERROR for event in fetched)
    # Domain roundtrip: enums, signature template, and JSONB attributes survive.
    sample = fetched[0]
    assert sample.kind is EventKind.EXTERNAL_CALL
    assert sample.signature.template == "connection timeout after <NUM>ms"
    assert "attempt" in sample.attributes


async def test_incident_and_investigation_roundtrip(db: Db) -> None:
    _engine, sessions = db
    incidents = IncidentRepository(sessions)
    investigations = InvestigationRepository(sessions)
    window = TimeWindow(start=BASE, end=BASE + timedelta(minutes=2))

    incident_id = await incidents.create(window, summary="pool exhaustion cascade")
    await incidents.set_status(incident_id, "investigating")

    result = InvestigationResult(
        investigation_id=uuid4(),
        assessment=RootCauseAssessment.model_validate(
            {
                "root_cause": "session leak",
                "confidence": 0.8,
                "probable_trigger": "stripe timeout",
                "failure_chain": [{"service": "booking-api", "description": "leak"}],
                "supporting_evidence": ["evidence"],
                "affected_services": ["booking-api"],
                "recommended_actions": ["close sessions"],
            }
        ),
        findings={
            "log_analyst": SpecialistFinding.model_validate(
                {
                    "summary": "timeouts first",
                    "hypotheses": [{"statement": "leak", "confidence": 0.8}],
                }
            )
        },
        challenge=AdvocateChallenge.model_validate(
            {
                "weaknesses": ["thin evidence"],
                "strongest_counterargument": "pool was undersized",
                "doubt": 0.3,
            }
        ),
        tool_executions=(
            ToolExecution(
                agent="log_analyst",
                tool="search_events",
                arguments={"query": "timeout"},
                outcome="ok",
                detail="[]",
                duration_ms=1.2,
            ),
        ),
        started_at=BASE,
        completed_at=BASE + timedelta(minutes=1),
    )
    await investigations.persist(incident_id, result)

    stored_incident = await incidents.get(incident_id)
    assert stored_incident is not None
    assert stored_incident.status == "investigating"
    stored = await investigations.latest_for_incident(incident_id)
    assert stored is not None
    assert stored.assessment is not None
    assert stored.assessment["root_cause"] == "session leak"


async def test_pgvector_similarity_orders_incidents(db: Db) -> None:
    _engine, sessions = db
    incidents = IncidentRepository(sessions)
    memory = IncidentMemory(MemoryRepository(sessions), HashingEmbedder())
    window = TimeWindow(start=BASE, end=BASE + timedelta(minutes=2))

    def assessment(root_cause: str, trigger: str, service: str) -> RootCauseAssessment:
        return RootCauseAssessment.model_validate(
            {
                "root_cause": root_cause,
                "confidence": 0.8,
                "probable_trigger": trigger,
                "failure_chain": [{"service": service, "description": trigger}],
                "supporting_evidence": ["..."],
                "affected_services": [service],
                "recommended_actions": ["fix"],
            }
        )

    pool_incident = await incidents.create(window)
    dns_incident = await incidents.create(window)
    await memory.remember(
        pool_incident,
        assessment(
            "database session leak exhausted the connection pool",
            "stripe timeout under traffic spike",
            "booking-api",
        ),
    )
    await memory.remember(
        dns_incident,
        assessment("DNS delegation expired", "expired DNS zone", "mesh-gateway"),
    )

    # The hashing embedder matches on shared vocabulary (no stemming), so the
    # query reuses the stored incident's wording the way a real new incident
    # summary would.
    matches = await memory.recall(
        "stripe timeout under traffic spike: database session leak exhausted "
        "the connection pool in booking-api",
        limit=2,
    )

    assert matches, "pgvector must return the vocabulary-overlapping incident"
    assert matches[0].incident_id == pool_incident

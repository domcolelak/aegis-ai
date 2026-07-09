"""Worker layer against real Redis (and PostgreSQL for the arq flow)."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from aegis.api.queues import ArqMemoryQueue
from aegis.api.redis_bus import RedisEventBus
from aegis.db import IncidentRepository, MemoryRepository, create_db_engine, create_session_factory
from aegis.events import TimeWindow
from aegis.investigation.assessment import RootCauseAssessment
from aegis.investigation.progress import ProgressEvent, ProgressKind
from aegis.workers.tasks import remember_incident, shutdown, startup

DATABASE_URL = os.environ.get("AEGIS_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("AEGIS_TEST_REDIS_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None or REDIS_URL is None,
        reason="AEGIS_TEST_DATABASE_URL and AEGIS_TEST_REDIS_URL not set",
    ),
]


async def test_redis_bus_round_trips_progress_events() -> None:
    assert REDIS_URL is not None
    bus = RedisEventBus.from_url(REDIS_URL)
    topic = uuid4()
    event = ProgressEvent(
        investigation_id=uuid4(),
        kind=ProgressKind.INVESTIGATION_COMPLETED,
        message="root cause: session leak",
        progress=1.0,
        at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )

    try:
        async with bus.subscribe(topic) as queue:
            await asyncio.sleep(0.1)  # let the pubsub subscription settle
            await bus.publish(topic, event)
            received = await asyncio.wait_for(queue.get(), timeout=5.0)
    finally:
        await bus.aclose()

    assert received == event


async def test_arq_worker_remembers_incident_exactly_once() -> None:
    assert DATABASE_URL is not None
    assert REDIS_URL is not None
    os.environ["AEGIS_DATABASE_URL"] = DATABASE_URL
    os.environ["AEGIS_REDIS_URL"] = REDIS_URL
    command.upgrade(AlembicConfig("alembic.ini"), "head")

    engine = create_db_engine(DATABASE_URL)
    sessions = create_session_factory(engine)
    try:
        window_start = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
        incident_id = await IncidentRepository(sessions).create(
            TimeWindow(start=window_start, end=window_start + timedelta(minutes=1))
        )
        assessment = RootCauseAssessment.model_validate(
            {
                "root_cause": "worker-indexed session leak",
                "confidence": 0.8,
                "probable_trigger": "stripe timeout",
                "failure_chain": [{"service": "booking-api", "description": "leak"}],
                "supporting_evidence": ["..."],
                "affected_services": ["booking-api"],
                "recommended_actions": ["close sessions"],
            }
        )

        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        queue = ArqMemoryQueue(pool)
        # Enqueued twice, deduplicated by the deterministic job id.
        await queue.enqueue_remember(incident_id, assessment)
        await queue.enqueue_remember(incident_id, assessment)
        await pool.aclose()

        worker = Worker(
            functions=[remember_incident],
            redis_settings=RedisSettings.from_dsn(REDIS_URL),
            on_startup=startup,
            on_shutdown=shutdown,
            burst=True,
            poll_delay=0.1,
        )
        await worker.main()
        await worker.close()

        assert await MemoryRepository(sessions).exists_for(incident_id)
    finally:
        await engine.dispose()

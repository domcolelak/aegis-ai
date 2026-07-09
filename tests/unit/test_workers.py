"""Worker layer without Redis: seams, idempotency, and event serialization."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from aegis.api.queues import ArqMemoryQueue
from aegis.api.redis_bus import decode_event, encode_event
from aegis.investigation.assessment import RootCauseAssessment
from aegis.investigation.progress import ProgressEvent, ProgressKind
from aegis.workers.tasks import remember_incident


def make_assessment() -> RootCauseAssessment:
    return RootCauseAssessment.model_validate(
        {
            "root_cause": "session leak",
            "confidence": 0.8,
            "probable_trigger": "stripe timeout",
            "failure_chain": [{"service": "booking-api", "description": "leak"}],
            "supporting_evidence": ["..."],
            "affected_services": ["booking-api"],
            "recommended_actions": ["close sessions"],
        }
    )


class FakePool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], str | None]] = []

    async def enqueue_job(self, function: str, *args: object, _job_id: str | None = None) -> None:
        self.jobs.append((function, args, _job_id))


async def test_queue_enqueues_with_deterministic_job_id() -> None:
    pool = FakePool()
    queue = ArqMemoryQueue(pool)  # type: ignore[arg-type]  # structural fake
    incident_id = uuid4()

    await queue.enqueue_remember(incident_id, make_assessment())

    (function, args, job_id) = pool.jobs[0]
    assert function == "remember_incident"
    assert args[0] == str(incident_id)
    assert isinstance(args[1], dict)
    assert job_id == f"remember-{incident_id}"


class FakeRepository:
    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    async def exists_for(self, incident_id: UUID) -> bool:
        return self._exists


class FakeMemory:
    def __init__(self) -> None:
        self.remembered: list[UUID] = []

    async def remember(self, incident_id: UUID, assessment: RootCauseAssessment) -> UUID:
        self.remembered.append(incident_id)
        return incident_id


async def test_remember_task_stores_new_incidents() -> None:
    memory = FakeMemory()
    ctx = {"memory_repository": FakeRepository(exists=False), "memory": memory}
    incident_id = uuid4()

    outcome = await remember_incident(
        ctx, str(incident_id), make_assessment().model_dump(mode="json")
    )

    assert outcome == "stored"
    assert memory.remembered == [incident_id]


async def test_remember_task_is_idempotent_on_retry() -> None:
    memory = FakeMemory()
    ctx = {"memory_repository": FakeRepository(exists=True), "memory": memory}

    outcome = await remember_incident(ctx, str(uuid4()), make_assessment().model_dump(mode="json"))

    assert outcome == "duplicate"
    assert memory.remembered == []


def test_progress_event_survives_the_wire() -> None:
    original = ProgressEvent(
        investigation_id=uuid4(),
        kind=ProgressKind.AGENT_COMPLETED,
        message="pool exhaustion confirmed",
        progress=0.61,
        agent="database_investigator",
        at=datetime(2026, 7, 9, 14, 31, 5, tzinfo=UTC),
    )

    assert decode_event(encode_event(original)) == original

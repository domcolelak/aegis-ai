"""arq task definitions and worker configuration.

Idempotency has two layers: the producer enqueues with a deterministic
``_job_id`` (arq refuses duplicates while the job is pending), and the task
itself checks whether the incident is already remembered before writing --
so a retry after a mid-task crash cannot double-insert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from arq.connections import RedisSettings

from aegis.core.config import Settings
from aegis.db import MemoryRepository, create_db_engine, create_session_factory
from aegis.investigation.assessment import RootCauseAssessment
from aegis.memory import IncidentMemory
from aegis.memory.embeddings import build_embedder

if TYPE_CHECKING:
    from collections.abc import Mapping

type WorkerContext = dict[Any, Any]  # arq's context type; keys are ours below


async def startup(ctx: WorkerContext) -> None:
    settings = Settings()
    engine = create_db_engine(settings.database_url)
    sessions = create_session_factory(engine)
    repository = MemoryRepository(sessions)
    ctx["engine"] = engine
    ctx["memory_repository"] = repository
    ctx["memory"] = IncidentMemory(repository, build_embedder(settings))


async def shutdown(ctx: WorkerContext) -> None:
    await ctx["engine"].dispose()


async def remember_incident(
    ctx: WorkerContext, incident_id: str, assessment: Mapping[str, object]
) -> str:
    """Embed and store one solved incident into pgvector memory."""
    repository: MemoryRepository = ctx["memory_repository"]
    memory: IncidentMemory = ctx["memory"]
    identifier = UUID(incident_id)
    if await repository.exists_for(identifier):
        return "duplicate"
    validated = RootCauseAssessment.model_validate(dict(assessment))
    await memory.remember(identifier, validated)
    return "stored"


class WorkerSettings:
    """Entrypoint: ``arq aegis.workers.tasks.WorkerSettings``."""

    functions: ClassVar = [remember_incident]
    on_startup = startup
    on_shutdown = shutdown
    # Bounded retries with arq's built-in backoff; a failing embedding
    # provider must not be hammered, and the task's own existence check makes
    # every retry safe.
    max_tries = 3
    job_timeout = 60
    redis_settings = RedisSettings.from_dsn(Settings().redis_url)

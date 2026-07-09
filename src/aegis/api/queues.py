"""Task-queue seam between the analysis service and the arq worker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from arq.connections import ArqRedis

    from aegis.investigation.assessment import RootCauseAssessment


class MemoryQueue(Protocol):
    async def enqueue_remember(
        self, incident_id: UUID, assessment: RootCauseAssessment
    ) -> None: ...


class ArqMemoryQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue_remember(self, incident_id: UUID, assessment: RootCauseAssessment) -> None:
        await self._pool.enqueue_job(
            "remember_incident",
            str(incident_id),
            assessment.model_dump(mode="json"),
            # Deterministic job id: re-enqueueing the same incident while a
            # job is pending is a no-op (deduplication), and the task's own
            # existence check covers the already-completed case.
            _job_id=f"remember-{incident_id}",
        )

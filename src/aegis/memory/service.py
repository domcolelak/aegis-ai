"""IncidentMemory: store solved incidents, recall the relevant ones."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aegis.db.models import IncidentMemoryRecord

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from aegis.investigation.assessment import RootCauseAssessment
    from aegis.memory.embeddings import EmbeddingProvider


class MemoryStore(Protocol):
    """The slice of MemoryRepository this service needs (test seam)."""

    async def add(self, record: IncidentMemoryRecord) -> UUID: ...

    async def similar(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 3,
        exclude_incident: UUID | None = None,
    ) -> list[tuple[IncidentMemoryRecord, float]]: ...


@dataclass(slots=True, frozen=True)
class SimilarIncident:
    incident_id: UUID | None
    similarity: float
    summary: str
    root_cause: str
    remediation: str | None

    def as_evidence(self) -> str:
        remediation = f" Remediation: {self.remediation}" if self.remediation else ""
        return (
            f"[similarity {self.similarity:.2f}] {self.summary} "
            f"Root cause: {self.root_cause}.{remediation}"
        )


class IncidentMemory:
    def __init__(self, store: MemoryStore, embedder: EmbeddingProvider) -> None:
        self._store = store
        self._embedder = embedder

    async def remember(self, incident_id: UUID, assessment: RootCauseAssessment) -> UUID:
        text = _memory_text(assessment)
        (embedding,) = await self._embedder.embed([text])
        return await self._store.add(
            IncidentMemoryRecord(
                incident_id=incident_id,
                summary=text,
                root_cause=assessment.root_cause,
                failure_chain=[step.model_dump(mode="json") for step in assessment.failure_chain],
                affected_services=list(assessment.affected_services),
                remediation="; ".join(assessment.recommended_actions) or None,
                embedding=embedding,
            )
        )

    async def recall(
        self,
        description: str,
        *,
        limit: int = 3,
        exclude_incident: UUID | None = None,
        min_similarity: float = 0.3,
    ) -> list[SimilarIncident]:
        (embedding,) = await self._embedder.embed([description])
        matches = await self._store.similar(
            embedding, limit=limit, exclude_incident=exclude_incident
        )
        results = []
        for record, distance in matches:
            similarity = 1.0 - distance
            if similarity < min_similarity:
                continue
            results.append(
                SimilarIncident(
                    incident_id=record.incident_id,
                    similarity=round(similarity, 4),
                    summary=record.summary,
                    root_cause=record.root_cause,
                    remediation=record.remediation,
                )
            )
        return results


def _memory_text(assessment: RootCauseAssessment) -> str:
    services = ", ".join(assessment.affected_services)
    return (
        f"{assessment.probable_trigger} -> {assessment.root_cause}. Affected services: {services}."
    )

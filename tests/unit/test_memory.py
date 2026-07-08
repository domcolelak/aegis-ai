"""Incident memory: hashing embedder properties and the service logic,
against an in-memory store implementing the MemoryStore protocol."""

import math
from collections.abc import Sequence
from uuid import UUID, uuid4

from aegis.db.models import EMBEDDING_DIM, IncidentMemoryRecord
from aegis.investigation.assessment import RootCauseAssessment
from aegis.memory import HashingEmbedder, IncidentMemory


def assessment(root_cause: str, trigger: str, services: list[str]) -> RootCauseAssessment:
    return RootCauseAssessment.model_validate(
        {
            "root_cause": root_cause,
            "confidence": 0.8,
            "probable_trigger": trigger,
            "failure_chain": [{"service": services[0], "description": trigger}],
            "supporting_evidence": ["..."],
            "affected_services": services,
            "recommended_actions": ["fix it"],
        }
    )


class InMemoryStore:
    def __init__(self) -> None:
        self.records: list[IncidentMemoryRecord] = []

    async def add(self, record: IncidentMemoryRecord) -> UUID:
        record.id = record.id or uuid4()
        self.records.append(record)
        return record.id

    async def similar(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 3,
        exclude_incident: UUID | None = None,
    ) -> list[tuple[IncidentMemoryRecord, float]]:
        scored = [
            (record, 1.0 - _dot(record.embedding, embedding))
            for record in self.records
            if exclude_incident is None or record.incident_id != exclude_incident
        ]
        scored.sort(key=lambda pair: pair[1])
        return scored[:limit]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class TestHashingEmbedder:
    async def test_deterministic_normalized_fixed_dim(self) -> None:
        embedder = HashingEmbedder()

        first, second = await embedder.embed(
            ["connection pool exhausted", "connection pool exhausted"]
        )

        assert first == second
        assert len(first) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(_dot(first, first)), 1.0, rel_tol=1e-9)

    async def test_shared_vocabulary_means_nearby_vectors(self) -> None:
        embedder = HashingEmbedder()
        base, related, unrelated = await embedder.embed(
            [
                "database connection pool exhausted after timeout",
                "connection pool exhaustion in the database after timeouts",
                "user avatar upload completed successfully",
            ]
        )

        assert _dot(base, related) > _dot(base, unrelated)


class TestIncidentMemory:
    async def test_remember_then_recall_orders_by_similarity(self) -> None:
        store = InMemoryStore()
        memory = IncidentMemory(store, HashingEmbedder())
        pool_incident = uuid4()
        await memory.remember(
            pool_incident,
            assessment(
                "database session leak exhausted the connection pool",
                "stripe timeout during traffic spike",
                ["booking-api", "postgres"],
            ),
        )
        await memory.remember(
            uuid4(),
            assessment(
                "DNS resolution failure in the service mesh",
                "expired DNS zone delegation",
                ["mesh-gateway"],
            ),
        )

        matches = await memory.recall(
            "connection pool exhaustion after payment timeouts in booking-api"
        )

        assert matches, "the pool incident shares enough vocabulary to be recalled"
        assert "session leak" in matches[0].root_cause
        assert matches[0].incident_id == pool_incident
        assert "similarity" in matches[0].as_evidence()

    async def test_recall_filters_low_similarity_and_excluded_incident(self) -> None:
        store = InMemoryStore()
        memory = IncidentMemory(store, HashingEmbedder())
        incident_id = uuid4()
        await memory.remember(
            incident_id,
            assessment("pool exhaustion", "timeout", ["booking-api"]),
        )

        gibberish = await memory.recall("zzz qqq xyzzy plugh entirely unrelated words")
        excluded = await memory.recall(
            "pool exhaustion timeout booking-api", exclude_incident=incident_id
        )

        assert gibberish == []
        assert excluded == []

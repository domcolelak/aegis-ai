"""SQLAlchemy 2.0 typed ORM models.

Naming convention on the metadata gives every constraint a deterministic
name, which is what makes Alembic migrations reviewable. JSONB is used where
the shape is genuinely dynamic (attributes, LLM findings); everything the
system queries by gets a real column and an index.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    MetaData,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# voyage-3.5 family and the hashing fallback both emit 1024 dimensions.
EMBEDDING_DIM = 1024

type JsonDict = dict[str, object]


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {  # noqa: RUF012 -- SQLAlchemy declarative config attribute
        datetime: TIMESTAMP(timezone=True),
        JsonDict: JSONB,
    }


class LogSourceRecord(Base):
    __tablename__ = "log_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[str] = mapped_column(unique=True)
    log_format: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class EventSignatureRecord(Base):
    __tablename__ = "event_signatures"

    fingerprint: Mapped[str] = mapped_column(primary_key=True)
    template: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(server_default=text("now()"))


class LogEventRecord(Base):
    """Append-only event store.

    Designed to adopt ``PARTITION BY RANGE (timestamp)`` without model
    changes once volume demands it; see package docstring.
    """

    __tablename__ = "log_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime]
    service: Mapped[str]
    source_id: Mapped[str]
    severity: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str]
    message: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(ForeignKey("event_signatures.fingerprint"))
    trace_id: Mapped[str | None]
    request_id: Mapped[str | None]
    host: Mapped[str | None]
    attributes: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (
        # BRIN: timestamps arrive nearly ordered, so block ranges are tight.
        Index("ix_log_events_timestamp_brin", "timestamp", postgresql_using="brin"),
        Index("ix_log_events_service_timestamp", "service", "timestamp"),
        Index(
            "ix_log_events_trace_id",
            "trace_id",
            postgresql_where=text("trace_id IS NOT NULL"),
        ),
        Index("ix_log_events_fingerprint", "fingerprint"),
    )


class IncidentRecord(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(default="detected")
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class AnomalyClusterRecord(Base):
    __tablename__ = "anomaly_clusters"

    cluster_id: Mapped[UUID] = mapped_column(primary_key=True)
    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.incident_id"), index=True
    )
    kind: Mapped[str]
    service: Mapped[str]
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    event_count: Mapped[int]
    confidence: Mapped[float]
    attributes: Mapped[JsonDict] = mapped_column(default=dict)
    representative_events: Mapped[list[str]] = mapped_column(JSONB, default=list)


class CausalEdgeRecord(Base):
    __tablename__ = "causal_edges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.incident_id"), index=True)
    source_event: Mapped[UUID]
    target_event: Mapped[UUID]
    composite_score: Mapped[float] = mapped_column(Float)
    strategy_scores: Mapped[JsonDict]


class InvestigationRecord(Base):
    __tablename__ = "investigations"

    investigation_id: Mapped[UUID] = mapped_column(primary_key=True)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.incident_id"), index=True)
    status: Mapped[str] = mapped_column(default="completed")
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    assessment: Mapped[JsonDict | None]
    challenge: Mapped[JsonDict | None]


class AgentFindingRecord(Base):
    __tablename__ = "agent_findings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.investigation_id"), index=True
    )
    agent: Mapped[str]
    finding: Mapped[JsonDict]


class ToolExecutionRecord(Base):
    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.investigation_id"), index=True
    )
    agent: Mapped[str]
    tool: Mapped[str]
    arguments: Mapped[JsonDict]
    outcome: Mapped[str]
    detail: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[float]
    at: Mapped[datetime]


class GeneratedPatchRecord(Base):
    __tablename__ = "generated_patches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.investigation_id"), index=True
    )
    reasoning: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)
    affected_files: Mapped[list[str]] = mapped_column(JSONB)
    confidence: Mapped[float]
    risks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class IncidentMemoryRecord(Base):
    __tablename__ = "incident_memory"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID | None] = mapped_column(ForeignKey("incidents.incident_id"))
    summary: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    failure_chain: Mapped[list[JsonDict]] = mapped_column(JSONB)
    affected_services: Mapped[list[str]] = mapped_column(JSONB)
    remediation: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        # HNSW over cosine distance: recall/latency sweet spot for the
        # "top-k similar incidents" query; exact search is pointless here.
        Index(
            "ix_incident_memory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

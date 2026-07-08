"""Initial schema: events, incidents, investigations, incident memory.

Hand-written (no live database at development time); the CI integration job
runs ``alembic upgrade head`` against real PostgreSQL+pgvector and then
exercises the ORM against the migrated schema, which is what keeps this file
honest.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "log_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("log_format", sa.String(), nullable=False),
        sa.Column("created_at", _TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_log_sources_source_id"),
    )

    op.create_table(
        "event_signatures",
        sa.Column("fingerprint", sa.String(), primary_key=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("first_seen", _TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "log_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("timestamp", _TIMESTAMPTZ, nullable=False),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "fingerprint",
            sa.String(),
            sa.ForeignKey(
                "event_signatures.fingerprint",
                name="fk_log_events_fingerprint_event_signatures",
            ),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("host", sa.String(), nullable=True),
        sa.Column("attributes", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_log_events_timestamp_brin",
        "log_events",
        ["timestamp"],
        postgresql_using="brin",
    )
    op.create_index("ix_log_events_service_timestamp", "log_events", ["service", "timestamp"])
    op.create_index(
        "ix_log_events_trace_id",
        "log_events",
        ["trace_id"],
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )
    op.create_index("ix_log_events_fingerprint", "log_events", ["fingerprint"])

    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("window_start", _TIMESTAMPTZ, nullable=False),
        sa.Column("window_end", _TIMESTAMPTZ, nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "anomaly_clusters",
        sa.Column("cluster_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey(
                "incidents.incident_id", name="fk_anomaly_clusters_incident_id_incidents"
            ),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("window_start", _TIMESTAMPTZ, nullable=False),
        sa.Column("window_end", _TIMESTAMPTZ, nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("attributes", JSONB(), nullable=False),
        sa.Column("representative_events", JSONB(), nullable=False),
    )
    op.create_index("ix_anomaly_clusters_incident_id", "anomaly_clusters", ["incident_id"])

    op.create_table(
        "causal_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.incident_id", name="fk_causal_edges_incident_id_incidents"),
            nullable=False,
        ),
        sa.Column("source_event", sa.Uuid(), nullable=False),
        sa.Column("target_event", sa.Uuid(), nullable=False),
        sa.Column("composite_score", sa.Float(), nullable=False),
        sa.Column("strategy_scores", JSONB(), nullable=False),
    )
    op.create_index("ix_causal_edges_incident_id", "causal_edges", ["incident_id"])

    op.create_table(
        "investigations",
        sa.Column("investigation_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.incident_id", name="fk_investigations_incident_id_incidents"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", _TIMESTAMPTZ, nullable=False),
        sa.Column("completed_at", _TIMESTAMPTZ, nullable=True),
        sa.Column("assessment", JSONB(), nullable=True),
        sa.Column("challenge", JSONB(), nullable=True),
    )
    op.create_index("ix_investigations_incident_id", "investigations", ["incident_id"])

    op.create_table(
        "agent_findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "investigation_id",
            sa.Uuid(),
            sa.ForeignKey(
                "investigations.investigation_id",
                name="fk_agent_findings_investigation_id_investigations",
            ),
            nullable=False,
        ),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("finding", JSONB(), nullable=False),
    )
    op.create_index("ix_agent_findings_investigation_id", "agent_findings", ["investigation_id"])

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "investigation_id",
            sa.Uuid(),
            sa.ForeignKey(
                "investigations.investigation_id",
                name="fk_tool_executions_investigation_id_investigations",
            ),
            nullable=False,
        ),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("at", _TIMESTAMPTZ, nullable=False),
    )
    op.create_index("ix_tool_executions_investigation_id", "tool_executions", ["investigation_id"])

    op.create_table(
        "incident_memory",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.incident_id", name="fk_incident_memory_incident_id_incidents"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("failure_chain", JSONB(), nullable=False),
        sa.Column("affected_services", JSONB(), nullable=False),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", _TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_incident_memory_embedding_hnsw",
        "incident_memory",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    for table in (
        "incident_memory",
        "tool_executions",
        "agent_findings",
        "investigations",
        "causal_edges",
        "anomaly_clusters",
        "incidents",
        "log_events",
        "event_signatures",
        "log_sources",
    ):
        op.drop_table(table)

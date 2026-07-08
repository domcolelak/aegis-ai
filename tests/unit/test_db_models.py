"""Schema sanity without a database: DDL must compile for PostgreSQL and the
scale-critical index choices must actually render into it."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.schema import CreateIndex, CreateTable

from aegis.db.models import Base, LogEventRecord
from aegis.db.repositories import _to_domain
from aegis.events import EventKind, Severity

DIALECT = PGDialect()  # type: ignore[no-untyped-call]  # dialect ctor lacks annotations


def test_every_table_and_index_compiles_for_postgres() -> None:
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=DIALECT))
        assert table.name in ddl
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=DIALECT))


def test_log_events_scale_indexes_render() -> None:
    table = Base.metadata.tables["log_events"]
    indexes = {
        str(index.name): str(CreateIndex(index).compile(dialect=DIALECT)) for index in table.indexes
    }

    assert "USING brin" in indexes["ix_log_events_timestamp_brin"]
    assert "WHERE trace_id IS NOT NULL" in indexes["ix_log_events_trace_id"]


def test_incident_memory_uses_hnsw_over_cosine_and_fixed_dim_vector() -> None:
    table = Base.metadata.tables["incident_memory"]
    table_ddl = str(CreateTable(table).compile(dialect=DIALECT))
    index_ddl = {
        str(index.name): str(CreateIndex(index).compile(dialect=DIALECT)) for index in table.indexes
    }["ix_incident_memory_embedding_hnsw"]

    assert "VECTOR(1024)" in table_ddl
    assert "USING hnsw" in index_ddl
    assert "vector_cosine_ops" in index_ddl


def test_record_to_domain_mapping_roundtrips_enums() -> None:
    record = LogEventRecord(
        event_id=uuid4(),
        timestamp=datetime(2026, 7, 6, 14, 31, tzinfo=UTC),
        service="booking-api",
        source_id="booking-api.log",
        severity=int(Severity.ERROR),
        kind=EventKind.DB_POOL.value,
        message="QueuePool limit reached",
        fingerprint="abc123",
        trace_id="t-1",
        request_id=None,
        host=None,
        attributes={"pool_used": 97},
    )

    event = _to_domain(record, template="QueuePool limit reached")

    assert event.severity is Severity.ERROR
    assert event.kind is EventKind.DB_POOL
    assert event.signature.fingerprint == "abc123"
    assert event.attributes == {"pool_used": 97}

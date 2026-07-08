"""Persistence: async SQLAlchemy 2.x over PostgreSQL + pgvector.

Scale posture for log_events (documented, deliberately not over-built):
BRIN on timestamp (append-only data; a B-tree would be ~100x larger for no
benefit), composite (service, timestamp) B-tree for the hot query shape,
template text normalized into event_signatures, COPY-based bulk insert.
The schema is written so ``PARTITION BY RANGE (timestamp)`` plus retention
jobs can be adopted without model changes -- worth doing around the
50-100M row mark, not speculatively.
"""

from aegis.db.engine import create_db_engine, create_session_factory
from aegis.db.models import Base
from aegis.db.repositories import (
    EventRepository,
    IncidentRepository,
    InvestigationRepository,
    MemoryRepository,
)

__all__ = [
    "Base",
    "EventRepository",
    "IncidentRepository",
    "InvestigationRepository",
    "MemoryRepository",
    "create_db_engine",
    "create_session_factory",
]

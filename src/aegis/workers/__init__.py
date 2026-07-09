"""Distributed background tasks (arq over Redis).

Why arq and not Celery: the whole codebase is asyncio-native; Celery would
force either an event loop per task invocation or a second synchronous
database stack. arq tasks are plain coroutines sharing the exact same async
SQLAlchemy layer the API uses.

What is distributed and what is not: only coarse, latency-insensitive work
leaves the process (incident-memory indexing -- embedding + insert).
Detection and correlation stay in-process: they operate on data already in
memory, and shipping it over Redis would cost more than the computation.
"""

from aegis.workers.tasks import WorkerSettings, remember_incident

__all__ = ["WorkerSettings", "remember_incident"]

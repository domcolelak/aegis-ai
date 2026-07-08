"""In-process topic bus bridging the orchestrator to WebSocket subscribers.

Deliberately in-process: with one API process it is the whole solution, and
it hides behind small seams (ProgressPublisher on the publish side, subscribe
on the consume side) so a Redis pub/sub implementation can replace it when
workers move out of process. Slow consumers lose the *oldest* events, never
block the pipeline: progress streams are telemetry, not a ledger.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from aegis.investigation.progress import ProgressEvent


class InProcessEventBus:
    def __init__(self, *, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._topics: defaultdict[UUID, set[asyncio.Queue[ProgressEvent]]] = defaultdict(set)

    async def publish(self, topic: UUID, event: ProgressEvent) -> None:
        for queue in tuple(self._topics.get(topic, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.get_nowait()  # drop the oldest, keep the freshest
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, topic: UUID) -> AsyncIterator[asyncio.Queue[ProgressEvent]]:
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue(self._queue_size)
        self._topics[topic].add(queue)
        try:
            yield queue
        finally:
            subscribers = self._topics[topic]
            subscribers.discard(queue)
            if not subscribers:
                del self._topics[topic]


class TopicPublisher:
    """Adapts the bus to the ProgressPublisher protocol for one topic."""

    def __init__(self, bus: InProcessEventBus, topic: UUID) -> None:
        self._bus = bus
        self._topic = topic

    async def publish(self, event: ProgressEvent) -> None:
        await self._bus.publish(self._topic, event)

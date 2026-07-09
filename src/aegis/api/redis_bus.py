"""Redis pub/sub implementation of the EventBus seam.

Same contract as InProcessEventBus, but frames cross process boundaries: a
WebSocket client can attach to any API instance while the analysis runs in
another. Slow consumers still lose the oldest events rather than blocking --
progress streams are telemetry, not a ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import redis.asyncio as aioredis

from aegis.investigation.progress import ProgressEvent, ProgressKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CHANNEL_PREFIX = "aegis:progress:"


def encode_event(event: ProgressEvent) -> str:
    return json.dumps(
        {
            "investigation_id": str(event.investigation_id),
            "kind": event.kind.value,
            "message": event.message,
            "progress": event.progress,
            "agent": event.agent,
            "at": event.at.isoformat(),
        }
    )


def decode_event(payload: str | bytes) -> ProgressEvent:
    data = json.loads(payload)
    return ProgressEvent(
        investigation_id=UUID(data["investigation_id"]),
        kind=ProgressKind(data["kind"]),
        message=data["message"],
        progress=data["progress"],
        agent=data["agent"],
        at=datetime.fromisoformat(data["at"]),
    )


class RedisEventBus:
    def __init__(self, client: aioredis.Redis, *, queue_size: int = 256) -> None:
        self._redis = client
        self._queue_size = queue_size

    @classmethod
    def from_url(cls, url: str) -> RedisEventBus:
        client = aioredis.from_url(url)  # type: ignore[no-untyped-call]  # redis-py stub gap
        return cls(client)

    async def publish(self, topic: UUID, event: ProgressEvent) -> None:
        await self._redis.publish(f"{_CHANNEL_PREFIX}{topic}", encode_event(event))

    @asynccontextmanager
    async def subscribe(self, topic: UUID) -> AsyncIterator[asyncio.Queue[ProgressEvent]]:
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue(self._queue_size)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"{_CHANNEL_PREFIX}{topic}")

        async def pump() -> None:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                event = decode_event(message["data"])
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    queue.get_nowait()
                    queue.put_nowait(event)

        reader = asyncio.create_task(pump())
        try:
            yield queue
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py stub gap

    async def aclose(self) -> None:
        await self._redis.aclose()

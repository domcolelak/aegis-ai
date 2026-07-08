"""Bounded, closable channel connecting pipeline stages.

A Channel is an asyncio queue with explicit end-of-stream semantics: producers
``send`` and finally ``close``; consumers iterate with ``async for``, which
ends once the channel is closed and drained. The bound is what gives the
pipeline backpressure: a producer awaiting ``send`` on a full channel is
suspended until a consumer catches up, so no stage can buffer unboundedly.

Ownership rule: ``close`` belongs to the producing side and must be called
after all ``send`` calls have completed (``async with`` does this). Senders
still suspended when the channel closes fail with ChannelClosedError as soon
as a slot frees up; on shutdown they are expected to be cancelled by their
TaskGroup instead.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

from aegis.core.errors import ChannelClosedError


class _ClosedMarker:
    __slots__ = ()


_CLOSED = _ClosedMarker()


class Channel[T]:
    """Multi-producer, multi-consumer bounded channel."""

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        # One extra queue slot is reserved for the close marker so that
        # close() never blocks; the semaphore keeps real items at maxsize.
        self._queue: asyncio.Queue[T | _ClosedMarker] = asyncio.Queue(maxsize + 1)
        self._send_slots = asyncio.Semaphore(maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def send(self, item: T) -> None:
        if self._closed:
            raise ChannelClosedError("send on closed channel")
        await self._send_slots.acquire()
        if self._closed:
            # Closed while we were waiting for a slot.
            self._send_slots.release()
            raise ChannelClosedError("channel closed while sending")
        self._queue.put_nowait(item)

    async def close(self) -> None:
        """Close the channel; already-queued items remain readable."""
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(_CLOSED)

    async def receive(self) -> T:
        item = await self._queue.get()
        if isinstance(item, _ClosedMarker):
            # Re-enqueue so every other consumer also observes the close.
            self._queue.put_nowait(_CLOSED)
            raise ChannelClosedError("channel drained")
        self._send_slots.release()
        return item

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> T:
        try:
            return await self.receive()
        except ChannelClosedError:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

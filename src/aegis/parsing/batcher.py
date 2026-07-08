"""Groups channel items into batches bounded by size and latency.

A batch is emitted when it reaches ``max_size`` (throughput: amortize the
executor's pickle round-trip) or when ``max_wait`` elapses since its first
item (latency: a trickle of events must not sit in the batcher while an
incident is unfolding). Backpressure is preserved end to end: this reads at
most one item ahead.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aegis.core.errors import ChannelClosedError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from aegis.core.channel import Channel


async def batched[T](
    channel: Channel[T], *, max_size: int, max_wait: float
) -> AsyncGenerator[list[T]]:
    if max_size < 1:
        raise ValueError("max_size must be >= 1")
    if max_wait <= 0:
        raise ValueError("max_wait must be > 0")

    loop = asyncio.get_running_loop()
    batch: list[T] = []
    deadline = 0.0

    while True:
        try:
            if batch:
                timeout = max(0.0, deadline - loop.time())
                item = await asyncio.wait_for(channel.receive(), timeout)
            else:
                item = await channel.receive()
        except TimeoutError:
            yield batch
            batch = []
            continue
        except ChannelClosedError:
            if batch:
                yield batch
            return

        if not batch:
            deadline = loop.time() + max_wait
        batch.append(item)
        if len(batch) >= max_size:
            yield batch
            batch = []

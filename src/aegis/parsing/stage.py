"""Async side of the parsing stage: channel in, executor, channel out.

The executor is injected, not created here: the composition root owns worker
lifecycle (one ProcessPoolExecutor shared by the app), and tests can pass a
ThreadPoolExecutor to stay fast while exercising the identical code path --
``parse_batch`` is pure, so thread versus process changes nothing but cost.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aegis.parsing.batcher import batched
from aegis.parsing.cpu import parse_batch

if TYPE_CHECKING:
    from concurrent.futures import Executor

    from aegis.core.channel import Channel
    from aegis.events import LogEvent, RawLogEvent


class ParsingStage:
    def __init__(
        self,
        executor: Executor,
        *,
        batch_size: int = 2000,
        max_wait: float = 0.25,
    ) -> None:
        self._executor = executor
        self._batch_size = batch_size
        self._max_wait = max_wait

    async def run(self, raw: Channel[RawLogEvent], parsed: Channel[LogEvent]) -> int:
        """Parse until the raw channel is drained; returns events parsed."""
        loop = asyncio.get_running_loop()
        total = 0
        try:
            async for batch in batched(raw, max_size=self._batch_size, max_wait=self._max_wait):
                events = await loop.run_in_executor(self._executor, parse_batch, batch)
                for event in events:
                    await parsed.send(event)
                total += len(events)
        finally:
            await parsed.close()
        return total

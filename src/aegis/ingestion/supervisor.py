"""Runs all configured sources concurrently into one bounded channel.

Structured concurrency contract: sources run as sibling tasks in a TaskGroup,
so one failing source cancels the rest and surfaces as an ExceptionGroup
carrying IngestionError (fail-fast; per-source quarantine is a deliberate
non-goal until there is an operator UI to report quarantined sources to).
The channel is closed on every exit path -- success, failure, cancellation --
so downstream stages always terminate.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING

from aegis.core.errors import IngestionError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aegis.core.channel import Channel
    from aegis.events import RawLogEvent
    from aegis.ingestion.source import LogSource


class IngestionSupervisor:
    def __init__(self, sources: Sequence[LogSource], channel: Channel[RawLogEvent]) -> None:
        ids = [source.source_id for source in sources]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate source_id among sources: {ids}")
        self._sources = sources
        self._channel = channel

    async def run(self) -> Mapping[str, int]:
        """Stream every source to completion; returns events counted per source."""
        counts: dict[str, int] = {source.source_id: 0 for source in self._sources}
        try:
            async with asyncio.TaskGroup() as tg:
                for source in self._sources:
                    tg.create_task(self._pump(source, counts))
        finally:
            await self._channel.close()
        return counts

    async def _pump(self, source: LogSource, counts: dict[str, int]) -> None:
        try:
            # aclosing guarantees the generator's finally runs here and now on
            # any exit (error or cancellation), not whenever the GC finalizer
            # gets around to it -- file handles are released deterministically.
            async with aclosing(source.stream()) as stream:
                async for event in stream:
                    await self._channel.send(event)
                    counts[source.source_id] += 1
        except Exception as exc:
            raise IngestionError(source.source_id) from exc

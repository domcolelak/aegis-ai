"""Ingestion + parsing throughput benchmark.

    uv run python scripts/benchmark_ingest.py --multiplier 256

Multiplies the synthetic incident's files N times on disk, then streams them
through the real pipeline (chunked file ingestion with backpressure, batched
parsing in a ProcessPoolExecutor) counting events without retaining them.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from aegis.core.channel import Channel
from aegis.events import LogEvent, RawLogEvent
from aegis.ingestion import IngestionSupervisor
from aegis.parsing import ParsingStage
from aegis.synthetic import generate, materialize


async def run(multiplier: int, workers: int) -> None:
    incident = generate(seed=7)
    with tempfile.TemporaryDirectory(prefix="aegis-bench-") as tmp:
        directory = Path(tmp)
        sources = materialize(incident, directory)
        total_lines = 0
        for name in incident.files:
            path = directory / name
            content = path.read_bytes()
            path.write_bytes(content * multiplier)
            total_lines += content.count(b"\n") * multiplier
        print(f"prepared ~{total_lines:,} log lines across {len(incident.files)} files")

        raw: Channel[RawLogEvent] = Channel(maxsize=8192)
        parsed: Channel[LogEvent] = Channel(maxsize=8192)
        counted = 0

        async def count() -> None:
            nonlocal counted
            async for _ in parsed:
                counted += 1

        started = time.perf_counter()
        with ProcessPoolExecutor(max_workers=workers) as executor:
            stage = ParsingStage(executor, batch_size=2000, max_wait=0.25)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(IngestionSupervisor(sources, raw).run())
                tg.create_task(stage.run(raw, parsed))
                tg.create_task(count())
        elapsed = time.perf_counter() - started

    print(
        f"parsed {counted:,} events in {elapsed:.2f}s "
        f"({counted / elapsed:,.0f} events/s, {workers} parser workers)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--multiplier", type=int, default=256)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run(args.multiplier, args.workers))

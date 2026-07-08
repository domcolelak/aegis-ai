import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from aegis.core.channel import Channel
from aegis.events import LogEvent, LogFormat, RawLogEvent, Severity
from aegis.parsing import ParsingStage

RECEIVED_AT = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)


def _raw_lines(count: int) -> list[RawLogEvent]:
    return [
        RawLogEvent(
            source_id="app.log",
            payload=f"2026-07-06 14:31:{i % 60:02d},000 ERROR app.svc failure {i}".encode(),
            received_at=RECEIVED_AT,
            log_format=LogFormat.PLAIN,
        )
        for i in range(count)
    ]


async def _run_stage(stage: ParsingStage, raw_events: list[RawLogEvent]) -> list[LogEvent]:
    raw: Channel[RawLogEvent] = Channel(maxsize=64)
    parsed: Channel[LogEvent] = Channel(maxsize=64)

    async def produce() -> None:
        async with raw:
            for event in raw_events:
                await raw.send(event)

    async def consume() -> list[LogEvent]:
        return [event async for event in parsed]

    async with asyncio.TaskGroup() as tg:
        tg.create_task(produce())
        runner = tg.create_task(stage.run(raw, parsed))
        consumer = tg.create_task(consume())

    assert runner.result() == len(raw_events)
    return consumer.result()


async def test_stage_parses_all_events_and_closes_output() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        stage = ParsingStage(executor, batch_size=10, max_wait=0.05)
        events = await _run_stage(stage, _raw_lines(35))

    assert len(events) == 35
    assert all(event.severity is Severity.ERROR for event in events)
    # One template despite 35 distinct messages -- masking collapsed them.
    assert len({event.signature.fingerprint for event in events}) == 1


async def test_stage_closes_output_channel_when_input_fails() -> None:
    raw: Channel[RawLogEvent] = Channel(maxsize=4)
    parsed: Channel[LogEvent] = Channel(maxsize=4)
    with ThreadPoolExecutor(max_workers=1) as executor:
        stage = ParsingStage(executor, batch_size=2, max_wait=0.05)
        run_task = asyncio.create_task(stage.run(raw, parsed))
        await raw.send(_raw_lines(1)[0])
        # Let the stage actually start (send need not yield to the loop);
        # cancelling a never-started task would skip its finally block.
        await asyncio.sleep(0.01)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    assert parsed.closed


@pytest.mark.slow
async def test_stage_round_trips_through_a_real_process_pool() -> None:
    """Proves RawLogEvent/LogEvent (slotted frozen dataclasses) pickle across
    the process boundary -- the one thing a ThreadPoolExecutor cannot verify."""
    with ProcessPoolExecutor(max_workers=1) as executor:
        stage = ParsingStage(executor, batch_size=50, max_wait=0.1)
        events = await _run_stage(stage, _raw_lines(120))

    assert len(events) == 120
    assert events[0].message == "failure 0"

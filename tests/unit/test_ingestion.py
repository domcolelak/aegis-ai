import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aegis.core.channel import Channel
from aegis.core.errors import IngestionError
from aegis.events import LogFormat, RawLogEvent
from aegis.ingestion import (
    DockerReplaySource,
    FileLogSource,
    IngestionSupervisor,
    LogSource,
    StructuredJsonLogSource,
)


async def _collect(source: LogSource) -> list[RawLogEvent]:
    return [event async for event in source.stream()]


async def test_file_source_streams_lines_in_order(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"first\nsecond\nthird\n")

    events = await _collect(FileLogSource(path))

    assert [event.payload for event in events] == [b"first", b"second", b"third"]
    assert all(event.source_id == "app.log" for event in events)
    assert all(event.log_format is LogFormat.PLAIN for event in events)


async def test_file_source_handles_crlf_missing_final_newline_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"first\r\n\r\n\nsecond\r\nlast without newline")

    events = await _collect(FileLogSource(path))

    assert [event.payload for event in events] == [b"first", b"second", b"last without newline"]


async def test_file_source_reassembles_lines_across_chunk_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    lines = [f"line-{i:04d}-{'x' * (i % 23)}".encode() for i in range(200)]
    path.write_bytes(b"\n".join(lines) + b"\n")

    # A 7-byte chunk guarantees every line straddles chunk boundaries.
    events = await _collect(FileLogSource(path, chunk_size=7))

    assert [event.payload for event in events] == lines


async def test_specialized_sources_declare_their_format(tmp_path: Path) -> None:
    json_path = tmp_path / "events.jsonl"
    json_path.write_bytes(b'{"msg": "a"}\n')
    docker_path = tmp_path / "container-json.log"
    docker_path.write_bytes(b'{"log": "a\\n", "stream": "stdout", "time": "T"}\n')

    json_events = await _collect(StructuredJsonLogSource(json_path))
    docker_events = await _collect(DockerReplaySource(docker_path, container="api-1"))

    assert json_events[0].log_format is LogFormat.JSON
    assert docker_events[0].log_format is LogFormat.DOCKER_JSON
    assert docker_events[0].source_id == "docker:api-1"


async def test_supervisor_merges_sources_counts_and_closes_channel(tmp_path: Path) -> None:
    a = tmp_path / "a.log"
    a.write_bytes(b"a1\na2\n")
    b = tmp_path / "b.log"
    b.write_bytes(b"b1\n")
    channel: Channel[RawLogEvent] = Channel(maxsize=16)
    supervisor = IngestionSupervisor([FileLogSource(a), FileLogSource(b)], channel)

    counts = await supervisor.run()

    assert channel.closed
    assert counts == {"a.log": 2, "b.log": 1}
    received = [event.payload async for event in channel]
    assert sorted(received) == [b"a1", b"a2", b"b1"]


async def test_supervisor_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"x\n")
    channel: Channel[RawLogEvent] = Channel(maxsize=4)

    with pytest.raises(ValueError, match="duplicate source_id"):
        IngestionSupervisor([FileLogSource(path), FileLogSource(path)], channel)


async def test_failing_source_surfaces_ingestion_error_and_closes_channel(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ok.log"
    path.write_bytes(b"fine\n")

    class BrokenSource:
        source_id = "broken"

        async def stream(self) -> AsyncGenerator[RawLogEvent]:
            raise OSError("disk on fire")
            yield  # pragma: no cover  # makes this an async generator

    channel: Channel[RawLogEvent] = Channel(maxsize=16)
    supervisor = IngestionSupervisor([FileLogSource(path), BrokenSource()], channel)

    with pytest.raises(ExceptionGroup) as excinfo:
        await supervisor.run()

    ingestion_errors = excinfo.value.exceptions
    assert any(
        isinstance(exc, IngestionError) and exc.source_id == "broken" for exc in ingestion_errors
    )
    assert channel.closed


async def test_cancellation_closes_channel_and_releases_file_handle(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_bytes(b"line\n" * 1000)
    # maxsize=1 forces the supervisor to suspend mid-file on a full channel.
    channel: Channel[RawLogEvent] = Channel(maxsize=1)
    supervisor = IngestionSupervisor([FileLogSource(path)], channel)

    task = asyncio.create_task(supervisor.run())
    assert (await channel.receive()).payload == b"line"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert channel.closed
    # On Windows an open handle would make this raise PermissionError, so a
    # successful unlink proves the source's finally block ran.
    path.unlink()


async def test_custom_source_satisfies_protocol() -> None:
    class InMemorySource:
        source_id = "memory"

        async def stream(self) -> AsyncGenerator[RawLogEvent]:
            yield RawLogEvent(
                source_id=self.source_id,
                payload=b"hello",
                received_at=datetime.now(tz=UTC),
            )

    source: LogSource = InMemorySource()  # structural typing check
    events = [event async for event in source.stream()]

    assert events[0].payload == b"hello"

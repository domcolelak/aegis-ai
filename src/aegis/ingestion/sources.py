"""File-backed log sources.

FileLogSource reads fixed-size chunks in a worker thread so the event loop is
never blocked on disk I/O, and holds at most one chunk (plus a partial line)
in memory regardless of file size. The other sources reuse that framing and
differ only in the declared payload format, which the parsing stage
dispatches on.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aegis.events import LogFormat, RawLogEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class FileLogSource:
    """Streams a log file line by line as RawLogEvents."""

    def __init__(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
        log_format: LogFormat = LogFormat.PLAIN,
        chunk_size: int = 64 * 1024,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        self._path = Path(path)
        self.source_id = source_id or self._path.name
        self.log_format = log_format
        self._chunk_size = chunk_size

    async def stream(self) -> AsyncGenerator[RawLogEvent]:
        handle = await asyncio.to_thread(self._path.open, "rb")
        try:
            remainder = b""
            while chunk := await asyncio.to_thread(handle.read, self._chunk_size):
                *lines, remainder = (remainder + chunk).split(b"\n")
                for line in lines:
                    if payload := line.rstrip(b"\r"):
                        yield self._event(payload)
            # A last line without a trailing newline is still a record.
            if payload := remainder.rstrip(b"\r"):
                yield self._event(payload)
        finally:
            handle.close()

    def _event(self, payload: bytes) -> RawLogEvent:
        return RawLogEvent(
            source_id=self.source_id,
            payload=payload,
            received_at=datetime.now(tz=UTC),
            log_format=self.log_format,
        )


class StructuredJsonLogSource(FileLogSource):
    """Newline-delimited JSON (one object per line)."""

    def __init__(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None:
        super().__init__(
            path, source_id=source_id, log_format=LogFormat.JSON, chunk_size=chunk_size
        )


class DockerReplaySource(FileLogSource):
    """Replays a captured Docker ``json-file`` driver log for a container.

    This is the daemon-free adapter: it consumes the exact on-disk format the
    Docker json-file logging driver writes, so demos and tests need no Docker
    daemon. A live daemon-API source later implements the same LogSource
    protocol and reuses the DOCKER_JSON parser unchanged.
    """

    def __init__(self, path: str | Path, *, container: str, chunk_size: int = 64 * 1024) -> None:
        super().__init__(
            path,
            source_id=f"docker:{container}",
            log_format=LogFormat.DOCKER_JSON,
            chunk_size=chunk_size,
        )
        self.container = container

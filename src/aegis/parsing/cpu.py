"""Worker-process entry points. Pure, picklable, asyncio-free.

``parse_batch`` is the only function the executor is handed: batching
amortizes the pickle round-trip so the process pool pays off at real log
volumes (regex parsing plus template extraction over tens of thousands of
lines is GIL-bound CPU work).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.events import LogFormat
from aegis.parsing.formats import parse_docker, parse_json, parse_plain

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aegis.events import LogEvent, RawLogEvent


def parse_one(raw: RawLogEvent) -> LogEvent:
    """Interpret one raw record; never raises on malformed input."""
    match raw.log_format:
        case LogFormat.JSON:
            return parse_json(raw)
        case LogFormat.DOCKER_JSON:
            return parse_docker(raw)
        case LogFormat.PLAIN:
            return parse_plain(raw)


def parse_batch(raw_events: Sequence[RawLogEvent]) -> list[LogEvent]:
    return [parse_one(raw) for raw in raw_events]

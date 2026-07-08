"""Normalized event domain model.

Hot-path types are frozen, slotted dataclasses: events flow through the
pipeline by the hundred thousand and need no re-validation after the parsing
stage produced them. Pydantic is reserved for trust boundaries (API schemas,
LLM output, tool arguments, configuration).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum, auto
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Mapping

type AttributeValue = str | int | float | bool


class Severity(IntEnum):
    """Syslog-inspired levels; IntEnum so "WARNING and above" is a comparison."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def from_text(cls, text: str, default: Severity | None = None) -> Severity:
        normalized = text.strip().upper()
        try:
            return _SEVERITY_ALIASES[normalized]
        except KeyError:
            if default is not None:
                return default
            raise ValueError(f"unknown severity: {text!r}") from None


_SEVERITY_ALIASES: dict[str, Severity] = {
    "TRACE": Severity.DEBUG,
    "DEBUG": Severity.DEBUG,
    "INFO": Severity.INFO,
    "NOTICE": Severity.INFO,
    "WARN": Severity.WARNING,
    "WARNING": Severity.WARNING,
    "ERR": Severity.ERROR,
    "ERROR": Severity.ERROR,
    "EXCEPTION": Severity.ERROR,
    "CRIT": Severity.CRITICAL,
    "CRITICAL": Severity.CRITICAL,
    "FATAL": Severity.CRITICAL,
    "ALERT": Severity.CRITICAL,
    "EMERG": Severity.CRITICAL,
    "PANIC": Severity.CRITICAL,
}


class LogFormat(StrEnum):
    """Record format a source produces; the parsing stage dispatches on it."""

    PLAIN = auto()
    JSON = auto()
    DOCKER_JSON = auto()


class EventKind(StrEnum):
    HTTP_REQUEST = auto()
    DB_QUERY = auto()
    DB_POOL = auto()
    EXTERNAL_CALL = auto()
    TASK_RETRY = auto()
    EXCEPTION = auto()
    LIFECYCLE = auto()
    GENERIC = auto()


@dataclass(slots=True, frozen=True)
class TimeWindow:
    """Closed interval [start, end]; timezone-aware by construction."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("TimeWindow requires timezone-aware datetimes")
        if self.end < self.start:
            raise ValueError("TimeWindow end precedes start")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts <= self.end

    def overlaps(self, other: TimeWindow) -> bool:
        return self.start <= other.end and other.start <= self.end


@dataclass(slots=True, frozen=True)
class EventSignature:
    """A log template with the variable parts masked out.

    Signatures are the deduplication unit for embeddings and anomaly
    bookkeeping: millions of events collapse into a few hundred templates.
    """

    template: str
    fingerprint: str

    @classmethod
    def from_template(cls, template: str) -> EventSignature:
        digest = hashlib.sha256(template.encode()).hexdigest()[:16]
        return cls(template=template, fingerprint=digest)


@dataclass(slots=True, frozen=True)
class RawLogEvent:
    """One unparsed record as read from a source.

    The payload stays ``bytes`` so the single decode happens in the CPU-pool
    parsing stage, not once per pipeline hop.
    """

    source_id: str
    payload: bytes
    received_at: datetime
    log_format: LogFormat = LogFormat.PLAIN


@dataclass(slots=True, frozen=True)
class LogEvent:
    event_id: UUID
    timestamp: datetime
    service: str
    source_id: str
    severity: Severity
    kind: EventKind
    message: str
    signature: EventSignature
    trace_id: str | None = None
    request_id: str | None = None
    host: str | None = None
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)

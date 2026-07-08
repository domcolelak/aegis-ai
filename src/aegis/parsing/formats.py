"""Per-format payload interpretation. Pure and picklable; runs in workers.

Parsing never raises on malformed input: format-specific failures degrade to
plain-text handling, and a line that matches nothing still becomes a valid
LogEvent carrying the raw text. Losing a malformed line during an incident
would mean losing exactly the evidence that matters most.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from uuid import uuid4

from aegis.events import EventKind, LogEvent, RawLogEvent, Severity
from aegis.parsing.classify import classify_kind
from aegis.parsing.masking import signature_of

if TYPE_CHECKING:
    from aegis.events import AttributeValue

# "2026-07-06 14:31:02,123 ERROR app.services.booking Message text"
_LEVEL_FIRST = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"\[?(?P<level>[A-Za-z]+)\]?:?\s+"
    r"(?:(?P<logger>[\w.\-]+(?:\.[\w\-]+)+)\s+)?"
    r"(?P<msg>.*)$"
)
# Nginx error log: "2026/07/06 14:31:22 [error] 71#71: message"
_NGINX_ERROR = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\] (?P<msg>.*)$"
)
_PLAIN_PATTERNS = (_LEVEL_FIRST, _NGINX_ERROR)

_FALLBACK_LEVEL = re.compile(
    r"\b(DEBUG|TRACE|INFO|NOTICE|WARNING|WARN|ERROR|ERR|CRITICAL|CRIT|FATAL|ALERT)\b"
)
_TRACE_IN_TEXT = re.compile(r"\btrace[_-]?id[=:]\s*\"?([\w-]+)", re.IGNORECASE)
_REQUEST_IN_TEXT = re.compile(r"\b(?:request[_-]?id|req[_-]?id)[=:]\s*\"?([\w-]+)", re.IGNORECASE)
# datetime.fromisoformat accepts at most 6 fractional digits; Docker writes 9.
_EXCESS_FRACTION = re.compile(r"(\.\d{6})\d+")

_TS_KEYS = ("timestamp", "time", "ts", "@timestamp", "asctime")
_LEVEL_KEYS = ("level", "severity", "levelname", "lvl")
_MSG_KEYS = ("message", "msg", "event")
_SERVICE_KEYS = ("service", "app", "application", "service_name")
_TRACE_KEYS = ("trace_id", "traceId", "trace", "otelTraceID")
_REQUEST_KEYS = ("request_id", "requestId", "req_id")
_HOST_KEYS = ("host", "hostname", "instance")
_CONSUMED_KEYS = frozenset(
    _TS_KEYS + _LEVEL_KEYS + _MSG_KEYS + _SERVICE_KEYS + _TRACE_KEYS + _REQUEST_KEYS + _HOST_KEYS
)


def parse_plain(
    raw: RawLogEvent, *, service: str | None = None, text: str | None = None
) -> LogEvent:
    line = text if text is not None else raw.payload.decode("utf-8", errors="replace")
    line = line.strip()

    timestamp = raw.received_at
    severity = Severity.INFO
    message = line
    attributes: dict[str, AttributeValue] = {}

    for pattern in _PLAIN_PATTERNS:
        if match := pattern.match(line):
            groups = match.groupdict()
            timestamp = _parse_timestamp_text(groups["ts"]) or raw.received_at
            severity = Severity.from_text(groups["level"], default=Severity.INFO)
            message = groups["msg"].strip()
            if logger := groups.get("logger"):
                attributes["logger"] = logger
            break
    else:
        if level_match := _FALLBACK_LEVEL.search(line):
            severity = Severity.from_text(level_match.group(1), default=Severity.INFO)

    trace_match = _TRACE_IN_TEXT.search(line)
    request_match = _REQUEST_IN_TEXT.search(line)

    return _build_event(
        raw,
        timestamp=timestamp,
        service=service or _default_service(raw.source_id),
        severity=severity,
        message=message,
        trace_id=trace_match.group(1) if trace_match else None,
        request_id=request_match.group(1) if request_match else None,
        host=None,
        attributes=attributes,
    )


def parse_json(
    raw: RawLogEvent, *, service: str | None = None, text: str | None = None
) -> LogEvent:
    line = text if text is not None else raw.payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return parse_plain(raw, service=service, text=line)
    if not isinstance(data, dict):
        return parse_plain(raw, service=service, text=line)

    message = str(_first(data, _MSG_KEYS) or line).strip()
    severity_raw = _first(data, _LEVEL_KEYS)
    attributes: dict[str, AttributeValue] = {
        key: value
        for key, value in data.items()
        if key not in _CONSUMED_KEYS and isinstance(value, str | int | float | bool)
    }

    return _build_event(
        raw,
        timestamp=_parse_timestamp_value(_first(data, _TS_KEYS)) or raw.received_at,
        service=str(_first(data, _SERVICE_KEYS) or service or _default_service(raw.source_id)),
        severity=(
            Severity.from_text(str(severity_raw), default=Severity.INFO)
            if severity_raw is not None
            else Severity.INFO
        ),
        message=message,
        trace_id=_optional_str(_first(data, _TRACE_KEYS)),
        request_id=_optional_str(_first(data, _REQUEST_KEYS)),
        host=_optional_str(_first(data, _HOST_KEYS)),
        attributes=attributes,
    )


def parse_docker(raw: RawLogEvent) -> LogEvent:
    """Docker ``json-file`` driver: {"log": ..., "stream": ..., "time": ...}."""
    container = raw.source_id.removeprefix("docker:")
    try:
        envelope = json.loads(raw.payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return parse_plain(raw, service=container)
    if not isinstance(envelope, dict) or "log" not in envelope:
        return parse_plain(raw, service=container)

    inner = str(envelope["log"]).rstrip("\n")
    # Containers often emit structured JSON themselves; unwrap one level.
    if inner.lstrip().startswith("{"):
        event = parse_json(raw, service=container, text=inner)
    else:
        event = parse_plain(raw, service=container, text=inner)

    attributes = dict(event.attributes)
    if stream := envelope.get("stream"):
        attributes["stream"] = str(stream)
    envelope_time = _parse_timestamp_value(envelope.get("time"))

    return LogEvent(
        event_id=event.event_id,
        # The container's own timestamp (parsed from the inner line) wins;
        # the daemon's envelope time is the fallback.
        timestamp=event.timestamp
        if event.timestamp != raw.received_at
        else (envelope_time or raw.received_at),
        service=event.service,
        source_id=event.source_id,
        severity=event.severity,
        kind=event.kind,
        message=event.message,
        signature=event.signature,
        trace_id=event.trace_id,
        request_id=event.request_id,
        host=event.host,
        attributes=attributes,
    )


def _build_event(
    raw: RawLogEvent,
    *,
    timestamp: datetime,
    service: str,
    severity: Severity,
    message: str,
    trace_id: str | None,
    request_id: str | None,
    host: str | None,
    attributes: dict[str, AttributeValue],
) -> LogEvent:
    kind = classify_kind(message)
    if kind is EventKind.EXCEPTION and severity < Severity.ERROR:
        severity = Severity.ERROR
    return LogEvent(
        event_id=uuid4(),
        timestamp=timestamp,
        service=service,
        source_id=raw.source_id,
        severity=severity,
        kind=kind,
        message=message,
        signature=signature_of(message),
        trace_id=trace_id,
        request_id=request_id,
        host=host,
        attributes=attributes,
    )


def _default_service(source_id: str) -> str:
    return PurePosixPath(source_id.replace("\\", "/")).stem or source_id


def _first(data: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if (value := data.get(key)) is not None:
            return value
    return None


def _optional_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _parse_timestamp_text(text: str) -> datetime | None:
    normalized = text.strip().replace(",", ".")
    if normalized[:10].count("/") == 2:
        normalized = normalized[:10].replace("/", "-") + normalized[10:]
    normalized = _EXCESS_FRACTION.sub(r"\1", normalized)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_timestamp_value(value: object | None) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        return _parse_timestamp_text(value)
    return None

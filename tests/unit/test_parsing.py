import json
from datetime import UTC, datetime

import pytest

from aegis.events import EventKind, LogFormat, RawLogEvent, Severity
from aegis.parsing import mask_message, parse_one
from aegis.parsing.classify import classify_kind

RECEIVED_AT = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)


def raw(
    payload: bytes,
    *,
    log_format: LogFormat = LogFormat.PLAIN,
    source_id: str = "app.log",
) -> RawLogEvent:
    return RawLogEvent(
        source_id=source_id, payload=payload, received_at=RECEIVED_AT, log_format=log_format
    )


class TestMasking:
    def test_masks_variable_parts(self) -> None:
        message = (
            "2026-07-06T14:31:02.123Z request 550e8400-e29b-41d4-a716-446655440000 "
            "from 10.0.3.7:8080 took 3000.5ms buffer 0xdeadbeef"
        )

        assert mask_message(message) == ("<TS> request <UUID> from <IP> took <NUM>ms buffer <HEX>")

    def test_value_differences_collapse_to_same_template(self) -> None:
        a = mask_message("connection timeout after 3000ms on 10.0.0.1")
        b = mask_message("connection timeout after 12ms on 192.168.4.20")

        assert a == b


class TestClassify:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Retrying task create_booking in 2s", EventKind.TASK_RETRY),
            ("QueuePool limit of size 100 overflow reached", EventKind.DB_POOL),
            ("remaining connection slots are reserved", EventKind.DB_POOL),
            ("SELECT * FROM bookings WHERE id = 1", EventKind.DB_QUERY),
            ("sqlalchemy session was not closed", EventKind.DB_QUERY),
            ("Stripe API request failed", EventKind.EXTERNAL_CALL),
            ('POST /api/bookings HTTP/1.1" 500', EventKind.HTTP_REQUEST),
            ("Unhandled TimeoutError in handler", EventKind.EXCEPTION),
            ("Traceback (most recent call last):", EventKind.EXCEPTION),
            ("Application started, listening on 0.0.0.0:8000", EventKind.LIFECYCLE),
            ("cache warmed for tenant acme", EventKind.GENERIC),
        ],
    )
    def test_rules(self, message: str, expected: EventKind) -> None:
        assert classify_kind(message) is expected

    def test_retry_wins_over_the_exception_it_mentions(self) -> None:
        assert classify_kind("Retrying after TimeoutError (attempt 3)") is EventKind.TASK_RETRY


class TestPlain:
    def test_level_first_line(self) -> None:
        line = b"2026-07-06 14:31:22,504 ERROR app.services.booking Connection timeout after 3000ms"
        event = parse_one(raw(line))

        assert event.timestamp == datetime(2026, 7, 6, 14, 31, 22, 504000, tzinfo=UTC)
        assert event.severity is Severity.ERROR
        assert event.message == "Connection timeout after 3000ms"
        assert event.service == "app"  # from source stem; registration maps real names later
        assert event.attributes["logger"] == "app.services.booking"

    def test_nginx_error_line(self) -> None:
        event = parse_one(
            raw(b"2026/07/06 14:31:31 [error] 71#71: upstream timed out while reading response")
        )

        assert event.severity is Severity.ERROR
        assert event.timestamp == datetime(2026, 7, 6, 14, 31, 31, tzinfo=UTC)
        assert "upstream timed out" in event.message

    def test_unmatched_line_falls_back_but_keeps_level_hint(self) -> None:
        event = parse_one(raw(b"something odd ERROR happened somewhere"))

        assert event.severity is Severity.ERROR
        assert event.timestamp == RECEIVED_AT
        assert event.message == "something odd ERROR happened somewhere"

    def test_trace_and_request_ids_extracted_from_text(self) -> None:
        event = parse_one(
            raw(b"2026-07-06 14:31:22,000 INFO api.http handled trace_id=abc-123 request_id=r-9")
        )

        assert event.trace_id == "abc-123"
        assert event.request_id == "r-9"

    def test_exception_kind_promotes_severity_to_error(self) -> None:
        event = parse_one(raw(b"unhandled TimeoutError in request handler, traceback follows"))

        assert event.kind is EventKind.EXCEPTION
        assert event.severity is Severity.ERROR


class TestJson:
    def test_extracts_aliased_fields_and_attributes(self) -> None:
        payload = (
            b'{"ts": "2026-07-06T14:31:19Z", "lvl": "warning", "msg": "pool usage high",'
            b' "service": "booking-api", "traceId": "t-1", "request_id": "r-1",'
            b' "hostname": "web-2", "pool_used": 97, "pool_size": 100, "nested": {"x": 1}}'
        )
        event = parse_one(raw(payload, log_format=LogFormat.JSON))

        assert event.timestamp == datetime(2026, 7, 6, 14, 31, 19, tzinfo=UTC)
        assert event.severity is Severity.WARNING
        assert event.service == "booking-api"
        assert event.trace_id == "t-1"
        assert event.request_id == "r-1"
        assert event.host == "web-2"
        assert event.attributes == {"pool_used": 97, "pool_size": 100}  # scalars only

    def test_epoch_timestamp(self) -> None:
        event = parse_one(raw(b'{"time": 1782822679, "msg": "hello"}', log_format=LogFormat.JSON))

        assert event.timestamp == datetime.fromtimestamp(1782822679, tz=UTC)

    def test_invalid_json_degrades_to_plain(self) -> None:
        event = parse_one(raw(b"{not json at all", log_format=LogFormat.JSON))

        assert event.message == "{not json at all"
        assert event.timestamp == RECEIVED_AT


class TestDocker:
    def test_plain_inner_line_uses_envelope_time_and_container_service(self) -> None:
        payload = (
            b'{"log": "pool exhausted, waiting for connection\\n",'
            b' "stream": "stderr", "time": "2026-07-06T14:31:19.123456789Z"}'
        )
        event = parse_one(
            raw(payload, log_format=LogFormat.DOCKER_JSON, source_id="docker:booking-api-1")
        )

        assert event.service == "booking-api-1"
        # Nanosecond fraction is trimmed to microseconds, not rejected.
        assert event.timestamp == datetime(2026, 7, 6, 14, 31, 19, 123456, tzinfo=UTC)
        assert event.attributes["stream"] == "stderr"
        assert event.kind is EventKind.DB_POOL

    def test_structured_inner_line_is_unwrapped(self) -> None:
        inner = json.dumps(
            {"msg": "payment failed", "level": "error", "ts": "2026-07-06T14:31:22Z"}
        )
        payload = json.dumps(
            {"log": inner + "\n", "stream": "stdout", "time": "2026-07-06T14:31:23Z"}
        ).encode()
        event = parse_one(
            raw(payload, log_format=LogFormat.DOCKER_JSON, source_id="docker:payments-1")
        )

        assert event.message == "payment failed"
        assert event.severity is Severity.ERROR
        # The container's own timestamp wins over the daemon envelope time.
        assert event.timestamp == datetime(2026, 7, 6, 14, 31, 22, tzinfo=UTC)
        assert event.service == "payments-1"

    def test_non_envelope_payload_degrades_to_plain(self) -> None:
        event = parse_one(
            raw(b"garbage line", log_format=LogFormat.DOCKER_JSON, source_id="docker:api-1")
        )

        assert event.message == "garbage line"
        assert event.service == "api-1"

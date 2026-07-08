"""The canonical synthetic incident.

Storyline (all timestamps relative to ``base``, incident at +6 minutes):

1. ~6 minutes of steady baseline traffic across five services.
2. 14:31:00  traffic to POST /api/bookings spikes to ~11x baseline.
3. 14:31:02  Stripe latency rises (payments WARN), then times out (ERROR).
4. 14:31:05  booking-api leaks database sessions on the timeout path
             (unhandled TimeoutError, session never closed).
5. 14:31:15  SQLAlchemy pool exhaustion in booking-api (QueuePool errors).
6. 14:31:19  PostgreSQL runs out of connection slots (FATAL).
7. 14:31:22  worker retry storm against the exhausted database.
8. 14:31:31  nginx upstream timeouts + booking-api 500s: user-visible outage.

The deterministic pipeline is expected to rank the incident trigger window
(traffic spike / stripe latency on the booking path) as the root region and
route the strongest causal chain through the pool-exhaustion events.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aegis.events import TimeWindow
from aegis.ingestion import DockerReplaySource, FileLogSource, StructuredJsonLogSource

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from aegis.ingestion import LogSource

DEFAULT_BASE = datetime(2026, 7, 6, 14, 25, 0, tzinfo=UTC)

DEPENDENCY_MAP: Mapping[str, frozenset[str]] = {
    "booking-api": frozenset({"postgres", "payments"}),
    "payments": frozenset(),
    "worker-1": frozenset({"postgres", "booking-api"}),
    "nginx": frozenset({"booking-api"}),
    "postgres": frozenset(),
}


@dataclass(slots=True, frozen=True)
class SyntheticIncident:
    files: Mapping[str, bytes]
    dependency_map: Mapping[str, frozenset[str]]
    incident_window: TimeWindow
    base: datetime


def materialize(incident: SyntheticIncident, directory: Path) -> list[LogSource]:
    """Write the incident's files and build correctly-typed sources for them."""
    for name, content in incident.files.items():
        (directory / name).write_bytes(content)
    return [
        FileLogSource(directory / "booking-api.log"),
        StructuredJsonLogSource(directory / "payments.jsonl"),
        FileLogSource(directory / "postgres.log"),
        FileLogSource(directory / "nginx.log"),
        DockerReplaySource(directory / "worker-json.log", container="worker-1"),
    ]


def generate(seed: int = 7, base: datetime = DEFAULT_BASE) -> SyntheticIncident:
    rng = random.Random(seed)
    lines: dict[str, list[tuple[datetime, bytes]]] = {
        "booking-api.log": [],
        "payments.jsonl": [],
        "postgres.log": [],
        "nginx.log": [],
        "worker-json.log": [],
    }
    trace_counter = 0

    def next_trace() -> str:
        nonlocal trace_counter
        trace_counter += 1
        return f"t-{trace_counter:06d}"

    def booking(ts: datetime, level: str, message: str) -> None:
        line = f"{_plain_ts(ts)} {level} app.api.bookings {message}"
        lines["booking-api.log"].append((ts, line.encode()))

    def payments(ts: datetime, level: str, message: str, trace: str | None = None) -> None:
        record: dict[str, object] = {
            "ts": _iso(ts),
            "level": level,
            "msg": message,
            "service": "payments",
        }
        if trace is not None:
            record["trace_id"] = trace
        lines["payments.jsonl"].append((ts, json.dumps(record).encode()))

    def postgres(ts: datetime, level: str, message: str) -> None:
        lines["postgres.log"].append((ts, f"{_plain_ts(ts)} {level}: {message}".encode()))

    def nginx(ts: datetime, message: str) -> None:
        lines["nginx.log"].append((ts, f"{_nginx_ts(ts)} [error] 71#71: {message}".encode()))

    def worker(ts: datetime, level: str, message: str) -> None:
        inner = f"{_plain_ts(ts)} {level} celery.worker.strategy {message}\n"
        envelope = json.dumps({"log": inner, "stream": "stderr", "time": _iso(ts)})
        lines["worker-json.log"].append((ts, envelope.encode()))

    def jitter(start: datetime, spread_s: float) -> datetime:
        return start + timedelta(seconds=rng.uniform(0.0, spread_s))

    # ------------------------------------------------------------- baseline
    incident_start = base + timedelta(minutes=6)
    window = base
    while window < incident_start:
        for _ in range(8):
            trace = next_trace()
            duration = rng.uniform(18, 60)
            booking(
                jitter(window, 10),
                "INFO",
                f"POST /api/bookings HTTP/1.1 201 ({duration:.0f}ms) trace_id={trace}",
            )
        for _ in range(3):
            payments(
                jitter(window, 10),
                "info",
                f"stripe charge succeeded in {rng.uniform(180, 420):.0f}ms",
                trace=next_trace(),
            )
        for _ in range(2):
            worker(
                jitter(window, 10),
                "INFO",
                f"task create_booking succeeded in {rng.uniform(0.1, 0.4):.2f}s",
            )
        if int((window - base).total_seconds()) % 60 == 0:
            postgres(jitter(window, 5), "LOG", "checkpoint complete: wrote 132 buffers")
        window += timedelta(seconds=10)

    # ------------------------------------------------- 14:31:00 traffic spike
    spike_traces: list[str] = []
    for _ in range(90):
        trace = next_trace()
        spike_traces.append(trace)
        booking(
            jitter(incident_start, 10),
            "INFO",
            f"POST /api/bookings HTTP/1.1 201 ({rng.uniform(30, 220):.0f}ms) trace_id={trace}",
        )

    # -------------------------------------------- 14:31:02 stripe slows down
    for i in range(8):
        payments(
            jitter(incident_start + timedelta(seconds=2), 4),
            "warning",
            f"stripe request slow: {rng.uniform(3800, 7200):.0f}ms",
            trace=spike_traces[i],
        )

    # ------------------------- 14:31:05 stripe timeouts + the session leak
    for i in range(12):
        trace = spike_traces[8 + i]
        ts = jitter(incident_start + timedelta(seconds=5), 20)
        payments(ts, "error", "stripe payment request timed out after 30000ms", trace=trace)
        booking(
            ts + timedelta(milliseconds=rng.uniform(40, 220)),
            "ERROR",
            "Unhandled TimeoutError in create_booking: stripe charge aborted, "
            f"database session left open trace_id={trace}",
        )

    # ------------------------------- 14:31:15 SQLAlchemy pool exhaustion
    for _ in range(20):
        booking(
            jitter(incident_start + timedelta(seconds=15), 30),
            "ERROR",
            "sqlalchemy.exc.TimeoutError: QueuePool limit of size 100 overflow 10 "
            "reached, connection timed out after 30.00s",
        )

    # ------------------------------ 14:31:19 postgres out of connection slots
    for _ in range(10):
        postgres(
            jitter(incident_start + timedelta(seconds=19), 21),
            "FATAL",
            "remaining connection slots are reserved for non-replication superuser connections",
        )

    # ----------------------------------------- 14:31:22 worker retry storm
    for _ in range(70):
        worker(
            jitter(incident_start + timedelta(seconds=22), 30),
            "WARNING",
            f"Retrying create_booking (attempt {rng.randint(2, 6)}): "
            "TimeoutError connecting to postgres",
        )

    # --------------------------- 14:31:31 user-visible outage (nginx + 500s)
    for _ in range(60):
        booking(
            jitter(incident_start + timedelta(seconds=31), 29),
            "ERROR",
            f"POST /api/bookings HTTP/1.1 500 ({rng.uniform(9000, 14000):.0f}ms) "
            f"trace_id={next_trace()}",
        )
    for _ in range(25):
        nginx(
            jitter(incident_start + timedelta(seconds=31), 50),
            "upstream timed out (110: Connection timed out) while reading "
            "response header from upstream",
        )

    files = {
        name: b"\n".join(line for _, line in sorted(entries, key=lambda item: item[0])) + b"\n"
        for name, entries in lines.items()
    }
    return SyntheticIncident(
        files=files,
        dependency_map=DEPENDENCY_MAP,
        incident_window=TimeWindow(
            start=incident_start, end=incident_start + timedelta(seconds=120)
        ),
        base=base,
    )


def _plain_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S,") + f"{ts.microsecond // 1000:03d}"


def _nginx_ts(ts: datetime) -> str:
    return ts.strftime("%Y/%m/%d %H:%M:%S")


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")

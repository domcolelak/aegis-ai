"""Fixed-window bucketing helpers shared by the detectors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.events import TimeWindow


def window_index(ts: datetime, window: timedelta) -> int:
    return int(ts.timestamp() // window.total_seconds())


def window_bounds(index: int, window: timedelta) -> TimeWindow:
    start = datetime.fromtimestamp(index * window.total_seconds(), tz=UTC)
    return TimeWindow(start=start, end=start + window)


def window_end(index: int, window: timedelta) -> datetime:
    return datetime.fromtimestamp((index + 1) * window.total_seconds(), tz=UTC)

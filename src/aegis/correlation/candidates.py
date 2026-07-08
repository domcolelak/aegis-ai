"""Candidate pair generation: the O(n^2) killer.

Scoring every pair of events in an incident window is quadratic and almost
entirely wasted work. Pairs are only worth scoring when the events are close
in time AND share a blocking key: a trace/request id, a service relationship,
or the same template. The two-pointer sweep over time-ordered events plus a
per-event fan-out cap bounds the work regardless of window size.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from aegis.correlation.models import CorrelationContext
    from aegis.events import LogEvent

DEFAULT_HORIZON = timedelta(seconds=60)


def generate_candidates(
    events: Sequence[LogEvent],
    ctx: CorrelationContext,
    *,
    horizon: timedelta = DEFAULT_HORIZON,
    max_pairs_per_event: int = 50,
) -> Iterator[tuple[LogEvent, LogEvent]]:
    """Yields (source, target) with source.timestamp <= target.timestamp."""
    ordered = sorted(events, key=lambda event: event.timestamp)
    for i, source in enumerate(ordered):
        emitted = 0
        for target in ordered[i + 1 :]:
            if target.timestamp - source.timestamp > horizon:
                break
            if emitted >= max_pairs_per_event:
                break
            if _blocked_together(source, target, ctx):
                yield source, target
                emitted += 1


def _blocked_together(source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> bool:
    if source.trace_id is not None and source.trace_id == target.trace_id:
        return True
    if source.request_id is not None and source.request_id == target.request_id:
        return True
    if source.signature.fingerprint == target.signature.fingerprint:
        return True
    return ctx.related(source.service, target.service)

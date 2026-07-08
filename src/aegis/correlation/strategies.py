"""Correlation strategies: each scores one independent causal signal.

All strategies score the directed hypothesis "source contributed to target"
in [0, 1] and must stay pure -- anything they need beyond the two events
lives in CorrelationContext, precomputed once per incident. The weights are
relative evidence strengths, normalized by the scorer, so adding a strategy
does not silently dilute the others.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from aegis.events import EventKind, Severity

if TYPE_CHECKING:
    from aegis.correlation.models import CorrelationContext
    from aegis.events import LogEvent


class CorrelationStrategy(Protocol):
    # Read-only properties so frozen dataclass implementations conform.
    @property
    def name(self) -> str: ...

    @property
    def weight(self) -> float: ...

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float: ...


@dataclass(slots=True, frozen=True)
class TemporalProximityStrategy:
    """Exponential decay over the source->target gap; zero against time's arrow.

    Exponential rather than linear so that "one second apart" and "five
    seconds apart" differ meaningfully while everything near the horizon
    fades smoothly instead of hitting a cliff.
    """

    name: str = "temporal_proximity"
    weight: float = 0.30
    horizon: timedelta = timedelta(seconds=60)

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float:
        gap = (target.timestamp - source.timestamp).total_seconds()
        horizon = self.horizon.total_seconds()
        if gap < 0 or gap > horizon:
            return 0.0
        # tau = horizon / 3: at the horizon the score has decayed to ~5%.
        return math.exp(-3.0 * gap / horizon)


@dataclass(slots=True, frozen=True)
class TraceLinkageStrategy:
    """Shared trace id is the strongest structural link two events can have."""

    name: str = "trace_linkage"
    weight: float = 0.25

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float:
        if source.trace_id is not None and source.trace_id == target.trace_id:
            return 1.0
        if source.request_id is not None and source.request_id == target.request_id:
            return 0.6
        return 0.0


@dataclass(slots=True, frozen=True)
class ServiceDependencyStrategy:
    """Scores the service topology's support for the causal direction.

    Failure propagates *up* the dependency edge (a database failing makes its
    callers fail: 1.0). Load and resource leaks propagate *down* it (a leaking
    caller exhausts the database's pool: 0.6). Same service: 0.8. Unrelated
    services get zero -- the other strategies must argue on their own.
    """

    name: str = "service_dependency"
    weight: float = 0.25

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float:
        if source.service == target.service:
            return 0.8
        if ctx.depends_on(target.service, source.service):
            return 1.0  # target calls source; source's failure propagates up
        if ctx.depends_on(source.service, target.service):
            return 0.6  # source calls target; source's load/leak propagates down
        return 0.0


@dataclass(slots=True, frozen=True)
class SemanticSimilarityStrategy:
    """Template similarity: retries of the same timeout share their wording."""

    name: str = "semantic_similarity"
    weight: float = 0.20

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float:
        return ctx.similarity.score(source.signature, target.signature)


@dataclass(slots=True, frozen=True)
class ErrorPropagationStrategy:
    """Known failure-cascade patterns between event kinds.

    An exception followed by pool exhaustion, pool exhaustion followed by a
    retry burst -- these orderings recur across incidents regardless of
    service names or wording, which is exactly the evidence the structural
    and lexical strategies cannot see. Both events must be WARNING or worse;
    healthy events do not propagate errors.
    """

    name: str = "error_propagation"
    weight: float = 0.15

    def score(self, source: LogEvent, target: LogEvent, ctx: CorrelationContext) -> float:
        if source.severity < Severity.WARNING or target.severity < Severity.WARNING:
            return 0.0
        return _CASCADES.get((source.kind, target.kind), 0.0)


_CASCADES: dict[tuple[EventKind, EventKind], float] = {
    (EventKind.EXTERNAL_CALL, EventKind.EXCEPTION): 0.8,
    (EventKind.EXTERNAL_CALL, EventKind.TASK_RETRY): 0.7,
    (EventKind.EXCEPTION, EventKind.DB_POOL): 0.9,
    (EventKind.EXCEPTION, EventKind.TASK_RETRY): 0.8,
    (EventKind.EXCEPTION, EventKind.EXCEPTION): 0.5,
    (EventKind.DB_QUERY, EventKind.DB_POOL): 0.9,
    (EventKind.DB_POOL, EventKind.DB_QUERY): 0.8,
    (EventKind.DB_POOL, EventKind.DB_POOL): 0.7,
    (EventKind.DB_POOL, EventKind.TASK_RETRY): 0.9,
    (EventKind.DB_POOL, EventKind.HTTP_REQUEST): 0.7,
    (EventKind.TASK_RETRY, EventKind.DB_POOL): 0.7,
    (EventKind.TASK_RETRY, EventKind.TASK_RETRY): 0.6,
    (EventKind.HTTP_REQUEST, EventKind.EXCEPTION): 0.5,
}


def default_strategies() -> tuple[CorrelationStrategy, ...]:
    return (
        TemporalProximityStrategy(),
        TraceLinkageStrategy(),
        ServiceDependencyStrategy(),
        SemanticSimilarityStrategy(),
        ErrorPropagationStrategy(),
    )

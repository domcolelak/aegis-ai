"""Composite scoring over candidate pairs -> CausalEdges."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from aegis.correlation.candidates import DEFAULT_HORIZON, generate_candidates
from aegis.correlation.models import CausalEdge
from aegis.correlation.strategies import default_strategies

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aegis.correlation.models import CorrelationContext
    from aegis.correlation.strategies import CorrelationStrategy
    from aegis.events import LogEvent


class CorrelationEngine:
    """Weighted-average composite of all strategies, thresholded into edges.

    The composite is a weighted mean rather than a sum so scores stay in
    [0, 1] and the threshold keeps its meaning when strategies are added or
    removed. Edges keep the raw per-strategy breakdown for auditability.
    """

    def __init__(
        self,
        strategies: Sequence[CorrelationStrategy] | None = None,
        *,
        edge_threshold: float = 0.35,
        horizon: timedelta = DEFAULT_HORIZON,
        max_pairs_per_event: int = 50,
    ) -> None:
        self._strategies = tuple(strategies) if strategies is not None else default_strategies()
        if not self._strategies:
            raise ValueError("at least one correlation strategy is required")
        total_weight = sum(strategy.weight for strategy in self._strategies)
        if total_weight <= 0:
            raise ValueError("strategy weights must sum to a positive value")
        self._total_weight = total_weight
        self._edge_threshold = edge_threshold
        self._horizon = horizon
        self._max_pairs_per_event = max_pairs_per_event

    def score_pair(
        self, source: LogEvent, target: LogEvent, ctx: CorrelationContext
    ) -> CausalEdge | None:
        breakdown = {
            strategy.name: strategy.score(source, target, ctx) for strategy in self._strategies
        }
        composite = (
            sum(strategy.weight * breakdown[strategy.name] for strategy in self._strategies)
            / self._total_weight
        )
        if composite < self._edge_threshold:
            return None
        return CausalEdge(
            source_event=source.event_id,
            target_event=target.event_id,
            composite_score=round(composite, 4),
            strategy_scores={name: round(score, 4) for name, score in breakdown.items()},
        )

    def correlate(self, events: Sequence[LogEvent], ctx: CorrelationContext) -> list[CausalEdge]:
        edges = []
        for source, target in generate_candidates(
            events, ctx, horizon=self._horizon, max_pairs_per_event=self._max_pairs_per_event
        ):
            if (edge := self.score_pair(source, target, ctx)) is not None:
                edges.append(edge)
        return edges

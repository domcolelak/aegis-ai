"""Correlation domain model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from aegis.correlation.similarity import SignatureSimilarity


@dataclass(slots=True, frozen=True)
class CausalEdge:
    """Directed 'source probably contributed to target' with its evidence."""

    source_event: UUID
    target_event: UUID
    composite_score: float
    strategy_scores: Mapping[str, float]


@dataclass(slots=True, frozen=True)
class CorrelationContext:
    """Per-incident facts precomputed once so strategies stay pure.

    ``dependency_map`` maps a service to the services it calls (its
    dependencies). It comes from configuration for now; deriving it from
    observed traces is roadmap work.
    """

    dependency_map: Mapping[str, frozenset[str]]
    similarity: SignatureSimilarity

    def depends_on(self, service: str, dependency: str) -> bool:
        return dependency in self.dependency_map.get(service, frozenset())

    def related(self, a: str, b: str) -> bool:
        return a == b or self.depends_on(a, b) or self.depends_on(b, a)

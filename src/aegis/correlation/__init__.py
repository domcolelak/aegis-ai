"""Correlation: scores probable causal relationships between events.

Honesty note (also in the README): this is evidence-weighted plausibility, not
causal inference. Every edge records the per-strategy score breakdown that
produced it, so a claimed relationship is always auditable.

The engine never scores all pairs: candidate generation blocks on trace ids,
service relationships, and shared templates within a time horizon, turning an
O(n^2) problem into "hundreds of plausible pairs".
"""

from aegis.correlation.candidates import generate_candidates
from aegis.correlation.engine import CorrelationEngine
from aegis.correlation.models import CausalEdge, CorrelationContext
from aegis.correlation.similarity import SignatureSimilarity, TokenJaccardSimilarity
from aegis.correlation.strategies import (
    CorrelationStrategy,
    ErrorPropagationStrategy,
    SemanticSimilarityStrategy,
    ServiceDependencyStrategy,
    TemporalProximityStrategy,
    TraceLinkageStrategy,
    default_strategies,
)

__all__ = [
    "CausalEdge",
    "CorrelationContext",
    "CorrelationEngine",
    "CorrelationStrategy",
    "ErrorPropagationStrategy",
    "SemanticSimilarityStrategy",
    "ServiceDependencyStrategy",
    "SignatureSimilarity",
    "TemporalProximityStrategy",
    "TokenJaccardSimilarity",
    "TraceLinkageStrategy",
    "default_strategies",
    "generate_candidates",
]

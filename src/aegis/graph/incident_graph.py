"""The incident's causal evidence graph."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

import networkx as nx

from aegis.events import Severity
from aegis.graph.models import RootCandidate

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from uuid import UUID

    from aegis.correlation import CausalEdge
    from aegis.events import LogEvent

_SEVERITY_WEIGHTS: dict[Severity, float] = {
    Severity.CRITICAL: 1.0,
    Severity.ERROR: 0.9,
    Severity.WARNING: 0.6,
}
_DEFAULT_SEVERITY_WEIGHT = 0.3

# Relative importance of blast radius, time position, and severity when
# ranking root candidates. Reach dominates: an early harmless event that
# caused nothing is noise; an early event whose descendants cover the graph
# is the story.
_REACH_W, _EARLINESS_W, _IMPACT_W = 0.45, 0.30, 0.25


class IncidentGraph:
    """Directed graph: events as nodes, scored causal hypotheses as edges."""

    def __init__(self, events: Iterable[LogEvent], edges: Iterable[CausalEdge]) -> None:
        self._events: dict[UUID, LogEvent] = {event.event_id: event for event in events}
        self._edges: list[CausalEdge] = list(edges)
        self._graph: nx.DiGraph[UUID] = nx.DiGraph()
        for event_id in self._events:
            self._graph.add_node(event_id)
        for edge in self._edges:
            if edge.source_event not in self._events or edge.target_event not in self._events:
                raise ValueError(f"edge references unknown event: {edge}")
            self._graph.add_edge(
                edge.source_event,
                edge.target_event,
                score=edge.composite_score,
                breakdown=edge.strategy_scores,
            )

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def event(self, event_id: UUID) -> LogEvent:
        return self._events[event_id]

    def edges(self) -> Sequence[CausalEdge]:
        return tuple(self._edges)

    def prune(self, *, min_score: float) -> IncidentGraph:
        """Drop edges under ``min_score``, then events left fully isolated."""
        kept_edges = [edge for edge in self._edges if edge.composite_score >= min_score]
        connected = {edge.source_event for edge in kept_edges} | {
            edge.target_event for edge in kept_edges
        }
        kept_events = [event for event in self._events.values() if event.event_id in connected]
        return IncidentGraph(kept_events, kept_edges)

    def root_candidates(
        self,
        *,
        top_k: int = 5,
        anomaly_boost: Mapping[UUID, float] | None = None,
    ) -> list[RootCandidate]:
        """Rank probable origins: sources of the condensation, scored by
        blast radius x earliness x impact.

        ``anomaly_boost`` (event id -> detector confidence) lets anomalous
        events outrank merely-severe ones.
        """
        if not self._events:
            return []
        boost = anomaly_boost or {}
        condensation = nx.condensation(self._graph)
        total_nodes = self._graph.number_of_nodes()
        timestamps = [event.timestamp for event in self._events.values()]
        t_min, t_max = min(timestamps), max(timestamps)
        span_s = max((t_max - t_min).total_seconds(), 1e-9)

        candidates: list[RootCandidate] = []
        for scc_id in condensation.nodes:
            if condensation.in_degree(scc_id) != 0:
                continue
            members: set[UUID] = condensation.nodes[scc_id]["members"]
            member_events = [self._events[event_id] for event_id in members]

            downstream = sum(
                len(condensation.nodes[desc]["members"])
                for desc in nx.descendants(condensation, scc_id)
            )
            reach = (downstream + len(members)) / total_nodes
            earliest = min(member_events, key=lambda event: event.timestamp)
            earliness = 1.0 - (earliest.timestamp - t_min).total_seconds() / span_s
            impact = max(
                max(
                    _SEVERITY_WEIGHTS.get(event.severity, _DEFAULT_SEVERITY_WEIGHT),
                    boost.get(event.event_id, 0.0),
                )
                for event in member_events
            )
            candidates.append(
                RootCandidate(
                    event=earliest,
                    score=round(
                        _REACH_W * reach + _EARLINESS_W * earliness + _IMPACT_W * impact, 4
                    ),
                    reach=round(reach, 4),
                    earliness=round(earliness, 4),
                    impact_weight=impact,
                    scc_size=len(members),
                )
            )
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates[:top_k]

    def strongest_chain(self, source: UUID, sink: UUID) -> list[LogEvent]:
        """The most plausible causal path: maximizes the product of edge
        scores, computed as Dijkstra over -log(score). Empty if unreachable."""

        def cost(_u: UUID, _v: UUID, data: Mapping[str, object]) -> float:
            score = cast("float", data["score"])  # scores are in (0, 1] by construction
            return -math.log(max(score, 1e-6))

        try:
            path = nx.dijkstra_path(self._graph, source, sink, weight=cost)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
        return [self._events[event_id] for event_id in path]

    def timeline(self) -> list[LogEvent]:
        """Causal order (topological over the condensation), ties broken by
        timestamp inside each strongly connected component."""
        ordered: list[LogEvent] = []
        condensation = nx.condensation(self._graph)
        for scc_id in nx.topological_sort(condensation):
            members: set[UUID] = condensation.nodes[scc_id]["members"]
            ordered.extend(
                sorted(
                    (self._events[event_id] for event_id in members),
                    key=lambda event: event.timestamp,
                )
            )
        return ordered

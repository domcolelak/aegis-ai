from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegis.correlation import CausalEdge
from aegis.events import EventKind, LogEvent, Severity
from aegis.graph import IncidentGraph
from aegis.parsing import signature_of

BASE = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)


def make_event(
    message: str, *, offset_s: float = 0.0, severity: Severity = Severity.ERROR
) -> LogEvent:
    return LogEvent(
        event_id=uuid4(),
        timestamp=BASE + timedelta(seconds=offset_s),
        service="svc",
        source_id="svc.log",
        severity=severity,
        kind=EventKind.GENERIC,
        message=message,
        signature=signature_of(message),
    )


def edge(source: LogEvent, target: LogEvent, score: float = 0.8) -> CausalEdge:
    return CausalEdge(
        source_event=source.event_id,
        target_event=target.event_id,
        composite_score=score,
        strategy_scores={"stub": score},
    )


def test_build_exposes_nodes_edges_and_lookup() -> None:
    a, b = make_event("a"), make_event("b", offset_s=1)
    graph = IncidentGraph([a, b], [edge(a, b)])

    assert graph.node_count == 2
    assert graph.edge_count == 1
    assert graph.event(a.event_id) is a


def test_edge_to_unknown_event_is_a_bug_not_a_shrug() -> None:
    a, b = make_event("a"), make_event("b")

    with pytest.raises(ValueError, match="unknown event"):
        IncidentGraph([a], [edge(a, b)])


def test_prune_drops_weak_edges_and_isolated_nodes() -> None:
    a, b, c = make_event("a"), make_event("b", offset_s=1), make_event("c", offset_s=2)
    graph = IncidentGraph([a, b, c], [edge(a, b, score=0.9), edge(b, c, score=0.2)])

    pruned = graph.prune(min_score=0.5)

    assert pruned.node_count == 2  # c became isolated and was dropped
    assert pruned.edge_count == 1


class TestRootCandidates:
    def test_chain_root_outranks_disconnected_bystander(self) -> None:
        root = make_event("first failure", offset_s=0)
        mid = make_event("cascade", offset_s=5)
        leaf = make_event("outage", offset_s=10)
        bystander = make_event("unrelated error", offset_s=1)
        graph = IncidentGraph([root, mid, leaf, bystander], [edge(root, mid), edge(mid, leaf)])

        candidates = graph.root_candidates(top_k=10)

        assert candidates[0].event is root
        assert candidates[0].reach == 0.75  # root + 2 descendants of 4 nodes
        assert candidates[0].earliness == 1.0
        # The bystander is technically a source too, but reaches nothing.
        bystander_candidate = next(c for c in candidates if c.event is bystander)
        assert bystander_candidate.score < candidates[0].score

    def test_retry_cycle_condenses_into_one_candidate(self) -> None:
        trigger = make_event("timeout", offset_s=0)
        retry_a = make_event("retrying (attempt 1)", offset_s=2)
        retry_b = make_event("timeout again", offset_s=3)
        graph = IncidentGraph(
            [trigger, retry_a, retry_b],
            [
                edge(trigger, retry_a),
                edge(retry_a, retry_b),
                edge(retry_b, retry_a),  # the storm cycle
            ],
        )

        candidates = graph.root_candidates()

        # The cycle is condensed: only the trigger is a source.
        assert len(candidates) == 1
        assert candidates[0].event is trigger
        assert candidates[0].scc_size == 1

    def test_anomaly_boost_outranks_mere_severity(self) -> None:
        boring_root = make_event("warning w", severity=Severity.WARNING, offset_s=0)
        boring_leaf = make_event("warning x", severity=Severity.WARNING, offset_s=5)
        anomalous_root = make_event("warning y", severity=Severity.WARNING, offset_s=0)
        anomalous_leaf = make_event("warning z", severity=Severity.WARNING, offset_s=5)
        graph = IncidentGraph(
            [boring_root, boring_leaf, anomalous_root, anomalous_leaf],
            [edge(boring_root, boring_leaf), edge(anomalous_root, anomalous_leaf)],
        )

        candidates = graph.root_candidates(anomaly_boost={anomalous_root.event_id: 0.95})

        assert candidates[0].event is anomalous_root

    def test_empty_graph_has_no_candidates(self) -> None:
        assert IncidentGraph([], []).root_candidates() == []


class TestStrongestChain:
    def test_prefers_high_product_of_scores(self) -> None:
        a = make_event("a", offset_s=0)
        strong_mid = make_event("strong", offset_s=1)
        weak_mid = make_event("weak", offset_s=1)
        d = make_event("d", offset_s=2)
        graph = IncidentGraph(
            [a, strong_mid, weak_mid, d],
            [
                edge(a, strong_mid, 0.9),
                edge(strong_mid, d, 0.9),
                edge(a, weak_mid, 0.4),
                edge(weak_mid, d, 0.4),
            ],
        )

        chain = graph.strongest_chain(a.event_id, d.event_id)

        assert [event.message for event in chain] == ["a", "strong", "d"]

    def test_unreachable_or_unknown_returns_empty(self) -> None:
        a, b = make_event("a"), make_event("b", offset_s=1)
        graph = IncidentGraph([a, b], [])

        assert graph.strongest_chain(a.event_id, b.event_id) == []
        assert graph.strongest_chain(a.event_id, uuid4()) == []


def test_timeline_is_causal_despite_clock_skew() -> None:
    # The cause's timestamp is *later* than its effect (clock skew between
    # hosts) -- topological order must still put the cause first.
    cause = make_event("cause with skewed clock", offset_s=2.0)
    effect = make_event("effect", offset_s=1.5)
    graph = IncidentGraph([cause, effect], [edge(cause, effect)])

    timeline = graph.timeline()

    assert [event.message for event in timeline] == ["cause with skewed clock", "effect"]

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aegis.correlation import (
    CorrelationContext,
    CorrelationEngine,
    ErrorPropagationStrategy,
    SemanticSimilarityStrategy,
    ServiceDependencyStrategy,
    TemporalProximityStrategy,
    TokenJaccardSimilarity,
    TraceLinkageStrategy,
    generate_candidates,
)
from aegis.events import EventKind, LogEvent, Severity
from aegis.parsing import signature_of

BASE = datetime(2026, 7, 6, 14, 31, 0, tzinfo=UTC)


def make_event(
    message: str,
    *,
    service: str,
    offset_s: float = 0.0,
    trace_id: str | None = None,
    request_id: str | None = None,
    severity: Severity = Severity.ERROR,
    kind: EventKind = EventKind.GENERIC,
) -> LogEvent:
    return LogEvent(
        event_id=uuid4(),
        timestamp=BASE + timedelta(seconds=offset_s),
        service=service,
        source_id=f"{service}.log",
        severity=severity,
        kind=kind,
        message=message,
        signature=signature_of(message),
        trace_id=trace_id,
        request_id=request_id,
    )


def make_context(**dependencies: frozenset[str]) -> CorrelationContext:
    return CorrelationContext(
        dependency_map={k.replace("_", "-"): v for k, v in dependencies.items()},
        similarity=TokenJaccardSimilarity(),
    )


EMPTY_CTX = make_context()


class TestTemporalProximity:
    strategy = TemporalProximityStrategy(horizon=timedelta(seconds=60))

    def test_decays_with_gap_and_respects_time_arrow(self) -> None:
        source = make_event("a failed", service="s")
        near = make_event("b failed", service="s", offset_s=1.0)
        far = make_event("b failed", service="s", offset_s=55.0)
        before = make_event("b failed", service="s", offset_s=-5.0)

        near_score = self.strategy.score(source, near, EMPTY_CTX)
        far_score = self.strategy.score(source, far, EMPTY_CTX)

        assert 0.9 < near_score <= 1.0
        assert 0.0 < far_score < 0.1
        assert self.strategy.score(source, before, EMPTY_CTX) == 0.0

    def test_beyond_horizon_is_zero(self) -> None:
        source = make_event("a failed", service="s")
        target = make_event("b failed", service="s", offset_s=61.0)

        assert self.strategy.score(source, target, EMPTY_CTX) == 0.0


class TestTraceLinkage:
    strategy = TraceLinkageStrategy()

    def test_trace_beats_request_beats_nothing(self) -> None:
        a = make_event("x", service="s", trace_id="t-1", request_id="r-1")
        same_trace = make_event("y", service="s", trace_id="t-1")
        same_request = make_event("y", service="s", request_id="r-1")
        unrelated = make_event("y", service="s", trace_id="t-2")

        assert self.strategy.score(a, same_trace, EMPTY_CTX) == 1.0
        assert self.strategy.score(a, same_request, EMPTY_CTX) == 0.6
        assert self.strategy.score(a, unrelated, EMPTY_CTX) == 0.0

    def test_two_missing_trace_ids_do_not_match(self) -> None:
        a = make_event("x", service="s")
        b = make_event("y", service="s")

        assert self.strategy.score(a, b, EMPTY_CTX) == 0.0


class TestServiceDependency:
    strategy = ServiceDependencyStrategy()
    # booking-api calls postgres.
    ctx = make_context(booking_api=frozenset({"postgres"}))

    def test_failure_propagates_up_the_dependency(self) -> None:
        db_failure = make_event("pool exhausted", service="postgres")
        api_symptom = make_event("timeout", service="booking-api")

        assert self.strategy.score(db_failure, api_symptom, self.ctx) == 1.0

    def test_leak_propagates_down_the_dependency(self) -> None:
        api_leak = make_event("session not closed", service="booking-api")
        db_symptom = make_event("too many connections", service="postgres")

        assert self.strategy.score(api_leak, db_symptom, self.ctx) == 0.6

    def test_same_service_and_unrelated(self) -> None:
        a = make_event("x", service="booking-api")
        b = make_event("y", service="booking-api")
        stranger = make_event("z", service="unrelated-svc")

        assert self.strategy.score(a, b, self.ctx) == 0.8
        assert self.strategy.score(a, stranger, self.ctx) == 0.0


class TestSemanticSimilarity:
    strategy = SemanticSimilarityStrategy()

    def test_same_template_is_identical(self) -> None:
        a = make_event("connection timeout after 3000ms", service="s")
        b = make_event("connection timeout after 12ms", service="s")

        assert self.strategy.score(a, b, EMPTY_CTX) == 1.0

    def test_related_wording_scores_between_zero_and_one(self) -> None:
        a = make_event("database connection timeout", service="s")
        b = make_event("connection refused by database", service="s")
        unrelated = make_event("user avatar uploaded successfully", service="s")

        related_score = self.strategy.score(a, b, EMPTY_CTX)

        assert 0.0 < related_score < 1.0
        assert self.strategy.score(a, unrelated, EMPTY_CTX) == 0.0


class TestErrorPropagation:
    strategy = ErrorPropagationStrategy()

    def test_known_cascades_score_and_unknown_do_not(self) -> None:
        exception = make_event("TimeoutError raised", service="s", kind=EventKind.EXCEPTION)
        pool = make_event("pool exhausted", service="s", kind=EventKind.DB_POOL)
        generic = make_event("something", service="s", kind=EventKind.GENERIC)

        assert self.strategy.score(exception, pool, EMPTY_CTX) == 0.9
        assert self.strategy.score(pool, exception, EMPTY_CTX) == 0.0
        assert self.strategy.score(generic, pool, EMPTY_CTX) == 0.0

    def test_healthy_events_do_not_propagate(self) -> None:
        info_exception = make_event(
            "caught and handled", service="s", kind=EventKind.EXCEPTION, severity=Severity.INFO
        )
        pool = make_event("pool exhausted", service="s", kind=EventKind.DB_POOL)

        assert self.strategy.score(info_exception, pool, EMPTY_CTX) == 0.0


class TestCandidateGeneration:
    def test_blocks_on_relationship_and_horizon(self) -> None:
        ctx = make_context(api=frozenset({"db"}))
        a = make_event("a", service="api", offset_s=0)
        b = make_event("b", service="db", offset_s=5)  # related service
        c = make_event("c", service="mailer", offset_s=6)  # unrelated
        d = make_event("d", service="db", offset_s=500)  # related but too late

        pairs = {
            (source.message, target.message)
            for source, target in generate_candidates([c, d, b, a], ctx)
        }

        assert ("a", "b") in pairs
        assert all("c" not in pair for pair in pairs)
        assert ("a", "d") not in pairs

    def test_trace_links_otherwise_unrelated_services(self) -> None:
        a = make_event("a", service="api", trace_id="t-1")
        b = make_event("b", service="mailer", offset_s=2, trace_id="t-1")

        pairs = list(generate_candidates([a, b], EMPTY_CTX))

        assert [(pair[0].message, pair[1].message) for pair in pairs] == [("a", "b")]

    def test_fan_out_cap_limits_pairs_per_source(self) -> None:
        events = [make_event("boom", service="s", offset_s=i * 0.01) for i in range(30)]

        pairs = list(generate_candidates(events, EMPTY_CTX, max_pairs_per_event=5))

        first = events[0]
        from_first = [pair for pair in pairs if pair[0] is first]
        assert len(from_first) == 5


class TestCorrelationEngine:
    def test_incident_chain_produces_expected_edges(self) -> None:
        """Miniature of the target incident: stripe timeout -> session leak
        symptom -> pool exhaustion -> retry storm."""
        ctx = make_context(
            booking_api=frozenset({"postgres", "stripe-gateway"}),
            worker=frozenset({"postgres"}),
        )
        stripe = make_event(
            "stripe payment request timed out after 30s",
            service="stripe-gateway",
            offset_s=0,
            trace_id="t-42",
            kind=EventKind.EXTERNAL_CALL,
        )
        leak = make_event(
            "TimeoutError: database session was not closed",
            service="booking-api",
            offset_s=3,
            trace_id="t-42",
            kind=EventKind.EXCEPTION,
        )
        pool = make_event(
            "connection pool exhausted: 100/100 in use",
            service="postgres",
            offset_s=17,
            kind=EventKind.DB_POOL,
        )
        retries = make_event(
            "Retrying create_booking (attempt 4)",
            service="worker",
            offset_s=25,
            kind=EventKind.TASK_RETRY,
        )
        engine = CorrelationEngine()

        edges = engine.correlate([retries, pool, leak, stripe], ctx)
        by_pair = {(edge.source_event, edge.target_event): edge for edge in edges}

        def edge_between(source: LogEvent, target: LogEvent) -> object:
            return by_pair.get((source.event_id, target.event_id))

        stripe_to_leak = edge_between(stripe, leak)
        assert stripe_to_leak is not None, "trace-linked upstream timeout must correlate"
        leak_to_pool = edge_between(leak, pool)
        assert leak_to_pool is not None, "leak must correlate to downstream pool exhaustion"
        pool_to_retries = edge_between(pool, retries)
        assert pool_to_retries is not None

        assert edge_between(retries, stripe) is None, "no edges against time's arrow"

    def test_edges_carry_full_strategy_breakdown(self) -> None:
        ctx = make_context()
        a = make_event("timeout calling upstream", service="s", trace_id="t")
        b = make_event("timeout calling upstream", service="s", offset_s=1, trace_id="t")
        engine = CorrelationEngine()

        edges = engine.correlate([a, b], ctx)

        assert len(edges) == 1
        breakdown = edges[0].strategy_scores
        assert set(breakdown) == {
            "temporal_proximity",
            "trace_linkage",
            "service_dependency",
            "semantic_similarity",
            "error_propagation",
        }
        assert 0.0 < edges[0].composite_score <= 1.0

    def test_unrelated_pair_is_below_threshold(self) -> None:
        ctx = make_context()
        a = make_event("cache warmed", service="s")
        b = make_event("user logged in", service="s", offset_s=50)
        engine = CorrelationEngine()

        # Same service blocks them into candidacy, but weak temporal + zero
        # trace + zero semantic must stay under the edge threshold.
        assert engine.correlate([a, b], ctx) == []

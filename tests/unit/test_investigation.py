"""Investigation layer tests -- everything runs against scripted providers."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from aegis.core.errors import InvestigationError, ProviderRateLimitedError
from aegis.core.resilience import RetryPolicy
from aegis.investigation import InvestigationOrchestrator
from aegis.investigation.agents import (
    DatabaseInvestigator,
    DevilsAdvocate,
    IncidentCommander,
    LogAnalyst,
)
from aegis.investigation.progress import CollectingPublisher, ProgressKind
from aegis.investigation.providers.base import Completion, CompletionRequest, TextBlock
from aegis.investigation.providers.resilient import RateLimitedProvider, RetryingProvider
from aegis.investigation.providers.scripted import (
    ScriptedProvider,
    json_completion,
    text_completion,
    tool_call_completion,
)
from aegis.investigation.tools import InvestigationAudit, InvestigationContext, ToolRegistry
from aegis.investigation.tools.builtin import default_tools
from conftest import BASE, IncidentFixture


def finding_payload(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "hypotheses": [
            {
                "statement": "database sessions leak when stripe times out",
                "confidence": 0.85,
                "supporting_evidence": ["QueuePool exhaustion follows the timeouts"],
            }
        ],
    }


CHALLENGE_PAYLOAD = {
    "weaknesses": ["traffic spike alone could explain pool pressure"],
    "alternative_hypotheses": [
        {"statement": "undersized pool for peak traffic", "confidence": 0.3}
    ],
    "strongest_counterargument": "no direct evidence of unclosed sessions in the logs",
    "doubt": 0.35,
}

ASSESSMENT_PAYLOAD = {
    "root_cause": "database session leak in create_booking after Stripe timeouts",
    "confidence": 0.82,
    "probable_trigger": "Stripe latency during a traffic spike on POST /api/bookings",
    "failure_chain": [
        {"service": "payments", "description": "stripe requests time out"},
        {"service": "booking-api", "description": "sessions leak, pool exhausts"},
        {"service": "postgres", "description": "connection slots run out"},
        {"service": "worker", "description": "retry storm amplifies the outage"},
    ],
    "supporting_evidence": ["QueuePool errors follow each timeout"],
    "contradicting_evidence": ["pool sizing was marginal for the spike"],
    "affected_services": ["booking-api", "postgres", "worker"],
    "recommended_actions": ["close sessions with an async context manager on error paths"],
}


def make_ctx(fixture: IncidentFixture) -> InvestigationContext:
    return InvestigationContext(
        investigation_id=uuid4(),
        data=fixture.dataset,
        audit=InvestigationAudit(),
        tool_timeout_s=5.0,
    )


class TestAgentLoop:
    async def test_token_usage_is_metered_across_turns(
        self, small_incident: IncidentFixture
    ) -> None:
        import dataclasses

        from aegis.investigation.providers.base import TokenUsage

        with_usage = dataclasses.replace(
            json_completion(finding_payload("metered")),
            usage=TokenUsage(input_tokens=120, output_tokens=45),
        )
        provider = ScriptedProvider({"log_analyst": [with_usage]})
        agent = LogAnalyst(provider, ToolRegistry(default_tools()))
        ctx = make_ctx(small_incident)

        await agent.investigate(ctx, small_incident.evidence)

        assert ctx.usage.total() == TokenUsage(input_tokens=120, output_tokens=45)

    async def test_agent_calls_tools_then_returns_validated_finding(
        self, small_incident: IncidentFixture
    ) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    tool_call_completion("search_events", {"query": "timeout"}),
                    json_completion(finding_payload("timeouts precede pool exhaustion")),
                ]
            }
        )
        agent = LogAnalyst(provider, ToolRegistry(default_tools()))
        ctx = make_ctx(small_incident)

        finding = await agent.investigate(ctx, small_incident.evidence)

        assert finding.summary == "timeouts precede pool exhaustion"
        (entry,) = ctx.audit.entries
        assert entry.tool == "search_events"
        assert entry.outcome == "ok"
        assert entry.agent == "log_analyst"

    async def test_tool_budget_is_enforced(self, small_incident: IncidentFixture) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    tool_call_completion("search_events", {"query": "timeout"}),
                    tool_call_completion("search_events", {"query": "pool"}),
                    json_completion(finding_payload("done under protest")),
                ]
            }
        )
        agent = LogAnalyst(provider, ToolRegistry(default_tools()), max_tool_calls=1)
        ctx = make_ctx(small_incident)

        finding = await agent.investigate(ctx, small_incident.evidence)

        assert finding.summary == "done under protest"
        # Only the first call executed; the second got a budget refusal.
        assert len(ctx.audit.entries) == 1

    async def test_invalid_json_gets_one_correction_attempt(
        self, small_incident: IncidentFixture
    ) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    text_completion("I think it is the database, probably."),
                    json_completion(finding_payload("corrected")),
                ]
            }
        )
        agent = LogAnalyst(provider, ToolRegistry(default_tools()))

        finding = await agent.investigate(make_ctx(small_incident), small_incident.evidence)

        assert finding.summary == "corrected"

    async def test_persistently_invalid_output_fails_loudly(
        self, small_incident: IncidentFixture
    ) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    text_completion("not json"),
                    text_completion('{"summary": "missing hypotheses"}'),
                ]
            }
        )
        agent = LogAnalyst(provider, ToolRegistry(default_tools()))

        with pytest.raises(InvestigationError, match="failed validation twice"):
            await agent.investigate(make_ctx(small_incident), small_incident.evidence)

    async def test_runaway_tool_looping_hits_max_turns(
        self, small_incident: IncidentFixture
    ) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    tool_call_completion("search_events", {"query": "timeout"}) for _ in range(10)
                ]
            }
        )
        agent = LogAnalyst(provider, ToolRegistry(default_tools()), max_tool_calls=2, max_turns=3)

        with pytest.raises(InvestigationError, match="exceeded 3 turns"):
            await agent.investigate(make_ctx(small_incident), small_incident.evidence)


class TestOrchestrator:
    async def test_full_scripted_investigation(self, small_incident: IncidentFixture) -> None:
        window_end = (BASE + timedelta(seconds=60)).isoformat()
        provider = ScriptedProvider(
            {
                "log_analyst": [
                    tool_call_completion("search_events", {"query": "timeout"}),
                    json_completion(finding_payload("stripe timeouts start the cascade")),
                ],
                "database_investigator": [
                    tool_call_completion(
                        "analyze_db_connections",
                        {"start": BASE.isoformat(), "end": window_end},
                    ),
                    json_completion(finding_payload("pool pressure matches a session leak")),
                ],
                "devils_advocate": [json_completion(CHALLENGE_PAYLOAD)],
                "incident_commander": [json_completion(ASSESSMENT_PAYLOAD)],
            }
        )
        registry = ToolRegistry(default_tools())
        publisher = CollectingPublisher()
        orchestrator = InvestigationOrchestrator(
            specialists=[LogAnalyst(provider, registry), DatabaseInvestigator(provider, registry)],
            advocate=DevilsAdvocate(provider, registry),
            commander=IncidentCommander(provider, registry),
            publisher=publisher,
        )

        result = await orchestrator.investigate(small_incident.dataset, small_incident.evidence)

        assert "session leak" in result.assessment.root_cause
        assert result.assessment.confidence == 0.82
        assert set(result.findings) == {"log_analyst", "database_investigator"}
        assert result.challenge.doubt == 0.35
        assert [entry.outcome for entry in result.tool_executions] == ["ok", "ok"]
        assert result.completed_at >= result.started_at

        kinds = [event.kind for event in publisher.events]
        assert kinds[0] is ProgressKind.INVESTIGATION_STARTED
        assert kinds[-1] is ProgressKind.INVESTIGATION_COMPLETED
        assert kinds.count(ProgressKind.AGENT_COMPLETED) == 4
        progresses = [event.progress for event in publisher.events]
        assert progresses == sorted(progresses), "progress must be monotonic"
        assert progresses[-1] == 1.0

    async def test_failure_publishes_failed_event_and_raises(
        self, small_incident: IncidentFixture
    ) -> None:
        provider = ScriptedProvider(
            {
                "log_analyst": [text_completion("junk"), text_completion("junk")],
                "database_investigator": [
                    json_completion(finding_payload("fine")),
                ],
                "devils_advocate": [],
                "incident_commander": [],
            }
        )
        registry = ToolRegistry(default_tools())
        publisher = CollectingPublisher()
        orchestrator = InvestigationOrchestrator(
            specialists=[LogAnalyst(provider, registry), DatabaseInvestigator(provider, registry)],
            advocate=DevilsAdvocate(provider, registry),
            commander=IncidentCommander(provider, registry),
            publisher=publisher,
        )

        with pytest.raises(ExceptionGroup):
            await orchestrator.investigate(small_incident.dataset, small_incident.evidence)

        assert publisher.events[-1].kind is ProgressKind.INVESTIGATION_FAILED


class TestProviderDecorators:
    async def test_rate_limited_provider_caps_concurrency(self) -> None:
        in_flight = 0
        peak = 0

        class SlowProvider:
            async def complete(self, request: CompletionRequest) -> Completion:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.01)
                in_flight -= 1
                return Completion(content=(TextBlock("ok"),), stop_reason="end_turn")

        provider = RateLimitedProvider(SlowProvider(), max_concurrent=3)
        request = CompletionRequest(system="s", messages=())

        async with asyncio.TaskGroup() as tg:
            for _ in range(10):
                tg.create_task(provider.complete(request))

        assert peak <= 3

    async def test_retrying_provider_retries_transient_failures(self) -> None:
        calls = 0

        class FlakyProvider:
            async def complete(self, request: CompletionRequest) -> Completion:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ProviderRateLimitedError("slow down")
                return Completion(content=(TextBlock("ok"),), stop_reason="end_turn")

        provider = RetryingProvider(
            FlakyProvider(),
            policy=RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.01, jitter=False),
        )

        completion = await provider.complete(CompletionRequest(system="s", messages=()))

        assert completion.stop_reason == "end_turn"
        assert calls == 2

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from aegis.investigation.providers.base import ToolUseBlock
from aegis.investigation.tools import (
    InvestigationAudit,
    InvestigationContext,
    Tool,
    ToolRegistry,
    ToolResult,
    default_tools,
)
from conftest import BASE, IncidentFixture


def make_ctx(fixture: IncidentFixture, *, tool_timeout_s: float = 5.0) -> InvestigationContext:
    return InvestigationContext(
        investigation_id=uuid4(),
        data=fixture.dataset,
        audit=InvestigationAudit(),
        tool_timeout_s=tool_timeout_s,
    )


def call(name: str, **arguments: object) -> ToolUseBlock:
    return ToolUseBlock(tool_use_id="c-1", name=name, arguments=arguments)


async def run(ctx: InvestigationContext, name: str, **arguments: object) -> tuple[bool, object]:
    registry = ToolRegistry(default_tools())
    block = await registry.execute("test_agent", call(name, **arguments), ctx)
    payload = json.loads(block.content) if not block.is_error else block.content
    return block.is_error, payload


class TestRegistryGuards:
    def test_duplicate_tool_names_rejected(self) -> None:
        tools = default_tools()
        with pytest.raises(ValueError, match="duplicate tool name"):
            ToolRegistry([*tools, tools[0]])

    def test_unknown_allowlist_entry_is_a_config_bug(self) -> None:
        registry = ToolRegistry(default_tools())
        with pytest.raises(KeyError, match="does_not_exist"):
            registry.specs(["search_events", "does_not_exist"])

    async def test_unknown_tool_returns_error_result_and_audits(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)
        registry = ToolRegistry(default_tools())

        block = await registry.execute("test_agent", call("no_such_tool"), ctx)

        assert block.is_error
        assert "unknown tool" in block.content
        (entry,) = ctx.audit.entries
        assert entry.outcome == "error"
        assert entry.agent == "test_agent"

    async def test_invalid_arguments_are_reported_not_raised(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)

        is_error, payload = await run(ctx, "search_events", query="x")  # min_length=2

        assert is_error
        assert "invalid arguments" in str(payload)
        assert ctx.audit.entries[0].outcome == "error"

    async def test_slow_tool_times_out_and_audits_timeout(
        self, small_incident: IncidentFixture
    ) -> None:
        class NoArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class SlowTool(Tool[NoArgs]):
            name = "slow_tool"
            description = "sleeps"
            args_model = NoArgs

            async def execute(self, args: NoArgs, ctx: InvestigationContext) -> ToolResult:
                await asyncio.sleep(0.5)
                return ToolResult({"done": True})

        ctx = make_ctx(small_incident, tool_timeout_s=0.05)
        registry = ToolRegistry([SlowTool()])

        block = await registry.execute("test_agent", call("slow_tool"), ctx)

        assert block.is_error
        assert "timed out" in block.content
        assert ctx.audit.entries[0].outcome == "timeout"


class TestBuiltinTools:
    async def test_inspect_event_window_filters(self, small_incident: IncidentFixture) -> None:
        ctx = make_ctx(small_incident)

        is_error, payload = await run(
            ctx,
            "inspect_event_window",
            start=BASE.isoformat(),
            end=(BASE + timedelta(seconds=60)).isoformat(),
            service="booking-api",
            min_severity="ERROR",
        )

        assert not is_error
        assert isinstance(payload, list)
        services = {item["service"] for item in payload}
        assert services == {"booking-api"}
        # The INFO noise event is filtered by min_severity.
        assert all("cache warmed" not in item["message"] for item in payload)

    async def test_search_events_only_errors(self, small_incident: IncidentFixture) -> None:
        ctx = make_ctx(small_incident)

        _, everything = await run(ctx, "search_events", query="retrying")
        _, errors_only = await run(ctx, "search_events", query="retrying", only_errors=True)

        assert isinstance(everything, list)
        assert len(everything) == 3  # WARNING retries
        assert errors_only == []

    async def test_find_similar_events_counts_the_template_family(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)
        retry = small_incident.events["retry"]

        is_error, payload = await run(ctx, "find_similar_events", event_id=str(retry.event_id))

        assert not is_error
        assert isinstance(payload, dict)
        assert payload["total"] == 3  # "attempt <NUM>" masks into one template

    async def test_analyze_db_connections_summarizes_pool_pressure(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)

        is_error, payload = await run(
            ctx,
            "analyze_db_connections",
            start=BASE.isoformat(),
            end=(BASE + timedelta(seconds=60)).isoformat(),
        )

        assert not is_error
        assert isinstance(payload, dict)
        assert set(payload) == {"booking-api", "postgres"}
        assert payload["booking-api"]["count"] == 1

    async def test_calculate_error_rate_buckets(self, small_incident: IncidentFixture) -> None:
        ctx = make_ctx(small_incident)

        is_error, payload = await run(
            ctx,
            "calculate_error_rate",
            service="booking-api",
            start=BASE.isoformat(),
            end=(BASE + timedelta(seconds=60)).isoformat(),
            bucket_seconds=60,
        )

        assert not is_error
        assert isinstance(payload, list)
        (bucket,) = payload
        assert bucket["total"] == 4  # leak, pool, outage, noise
        assert bucket["errors"] == 3
        assert bucket["ratio"] == 0.75

    async def test_inspect_dependency_graph_both_directions(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)

        is_error, payload = await run(ctx, "inspect_dependency_graph", service="postgres")

        assert not is_error
        assert isinstance(payload, dict)
        assert payload["depends_on"] == []
        assert payload["depended_on_by"] == ["booking-api", "worker"]

    async def test_get_anomaly_details_with_kind_filter(
        self, small_incident: IncidentFixture
    ) -> None:
        ctx = make_ctx(small_incident)

        _, storms = await run(ctx, "get_anomaly_details", kind="retry_storm")
        _, none = await run(ctx, "get_anomaly_details", kind="frequency_spike")

        assert isinstance(storms, list)
        assert len(storms) == 1
        assert storms[0]["service"] == "worker"
        assert storms[0]["attributes"] == {"retries": 3}
        assert none == []

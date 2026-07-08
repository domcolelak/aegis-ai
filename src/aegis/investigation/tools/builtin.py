"""The built-in investigation tools.

Each argument model doubles as the schema shown to the model; naive
datetimes are coerced to UTC because language models are careless with
timezone suffixes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from aegis.events import Severity
from aegis.investigation.data import event_digest
from aegis.investigation.tools.base import InvestigationContext, Tool, ToolResult


def _ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_ensure_utc)]

type SeverityName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowArgs(_Args):
    start: UtcDateTime
    end: UtcDateTime
    service: str | None = None
    min_severity: SeverityName = "DEBUG"
    limit: int = Field(default=30, ge=1, le=100)


class InspectEventWindow(Tool[WindowArgs]):
    name = "inspect_event_window"
    description = (
        "List events in a time window, optionally filtered by service and minimum severity."
    )
    args_model = WindowArgs

    async def execute(self, args: WindowArgs, ctx: InvestigationContext) -> ToolResult:
        events = ctx.data.events_in_window(
            args.start,
            args.end,
            service=args.service,
            min_severity=Severity[args.min_severity],
            limit=args.limit,
        )
        return ToolResult([event_digest(event) for event in events])


class SearchArgs(_Args):
    query: str = Field(min_length=2)
    only_errors: bool = False
    limit: int = Field(default=30, ge=1, le=100)


class SearchEvents(Tool[SearchArgs]):
    name = "search_events"
    description = "Case-insensitive substring search over event messages."
    args_model = SearchArgs

    async def execute(self, args: SearchArgs, ctx: InvestigationContext) -> ToolResult:
        events = ctx.data.search(args.query, only_errors=args.only_errors, limit=args.limit)
        return ToolResult([event_digest(event) for event in events])


class SimilarArgs(_Args):
    event_id: UUID
    limit: int = Field(default=20, ge=1, le=100)


class FindSimilarEvents(Tool[SimilarArgs]):
    name = "find_similar_events"
    description = "Find events sharing the reference event's log template (masked signature)."
    args_model = SimilarArgs

    async def execute(self, args: SimilarArgs, ctx: InvestigationContext) -> ToolResult:
        samples, total = ctx.data.similar_to(args.event_id, limit=args.limit)
        return ToolResult({"total": total, "samples": [event_digest(event) for event in samples]})


class RangeArgs(_Args):
    start: UtcDateTime
    end: UtcDateTime


class AnalyzeDbConnections(Tool[RangeArgs]):
    name = "analyze_db_connections"
    description = (
        "Summarize database connection-pool pressure per service in a window "
        "(counts, first/last occurrence, sample message)."
    )
    args_model = RangeArgs

    async def execute(self, args: RangeArgs, ctx: InvestigationContext) -> ToolResult:
        return ToolResult(ctx.data.pool_pressure(args.start, args.end))


class ErrorRateArgs(_Args):
    service: str
    start: UtcDateTime
    end: UtcDateTime
    bucket_seconds: int = Field(default=10, ge=1, le=300)


class CalculateErrorRate(Tool[ErrorRateArgs]):
    name = "calculate_error_rate"
    description = "Per-bucket totals, error counts, and error ratio for one service."
    args_model = ErrorRateArgs

    async def execute(self, args: ErrorRateArgs, ctx: InvestigationContext) -> ToolResult:
        return ToolResult(
            ctx.data.error_rate(args.service, args.start, args.end, bucket_s=args.bucket_seconds)
        )


class DependencyArgs(_Args):
    service: str | None = None


class InspectDependencyGraph(Tool[DependencyArgs]):
    name = "inspect_dependency_graph"
    description = "Show service dependencies (what a service calls) and dependents (who calls it)."
    args_model = DependencyArgs

    async def execute(self, args: DependencyArgs, ctx: InvestigationContext) -> ToolResult:
        dependency_map = ctx.data.dependency_map
        if args.service is None:
            return ToolResult({name: sorted(deps) for name, deps in dependency_map.items()})
        dependents = sorted(name for name, deps in dependency_map.items() if args.service in deps)
        return ToolResult(
            {
                "service": args.service,
                "depends_on": sorted(dependency_map.get(args.service, frozenset())),
                "depended_on_by": dependents,
            }
        )


class AnomalyArgs(_Args):
    kind: str | None = None


class GetAnomalyDetails(Tool[AnomalyArgs]):
    name = "get_anomaly_details"
    description = "Anomaly clusters with their triggering numbers, optionally filtered by kind."
    args_model = AnomalyArgs

    async def execute(self, args: AnomalyArgs, ctx: InvestigationContext) -> ToolResult:
        clusters = [
            {
                "cluster_id": str(cluster.cluster_id),
                "kind": cluster.kind.value,
                "service": cluster.service,
                "window_start": cluster.window.start.isoformat(),
                "window_end": cluster.window.end.isoformat(),
                "event_count": cluster.event_count,
                "confidence": cluster.confidence,
                "attributes": dict(cluster.attributes),
                "representative_events": [str(rid) for rid in cluster.representative_events[:5]],
            }
            for cluster in ctx.data.clusters
            if args.kind is None or cluster.kind.value == args.kind
        ]
        return ToolResult(clusters)


def default_tools() -> tuple[Tool[Any], ...]:
    return (
        InspectEventWindow(),
        SearchEvents(),
        FindSimilarEvents(),
        AnalyzeDbConnections(),
        CalculateErrorRate(),
        InspectDependencyGraph(),
        GetAnomalyDetails(),
    )

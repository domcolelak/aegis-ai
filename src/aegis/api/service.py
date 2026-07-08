"""IncidentAnalysisService: the vertical slice behind POST /incidents/analyze.

Pre-creates the incident row and returns immediately (202) so clients can
attach to the WebSocket stream, then runs the full pipeline as a tracked
background task: ingest -> parse (process pool) -> persist -> detect ->
correlate -> graph -> memory recall -> multi-agent investigation -> persist
result -> remember. Failures mark the incident and publish a FAILED event;
nothing disappears silently.
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

import structlog

from aegis.api.bus import TopicPublisher
from aegis.core.channel import Channel
from aegis.correlation import CorrelationContext, CorrelationEngine, TokenJaccardSimilarity
from aegis.detection import default_engine
from aegis.events import Severity, TimeWindow
from aegis.graph import IncidentGraph
from aegis.ingestion import IngestionSupervisor
from aegis.investigation import build_evidence
from aegis.investigation.data import InvestigationDataStore
from aegis.investigation.progress import ProgressEvent, ProgressKind
from aegis.observability import Metrics, NullMetrics
from aegis.parsing import ParsingStage
from aegis.synthetic import generate, materialize

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from concurrent.futures import Executor

    from aegis.api.bus import InProcessEventBus
    from aegis.api.schemas import AnalyzeRequest
    from aegis.db import (
        EventRepository,
        IncidentRepository,
        InvestigationRepository,
    )
    from aegis.events import LogEvent, RawLogEvent
    from aegis.investigation.orchestrator import InvestigationOrchestrator
    from aegis.memory import IncidentMemory


class AnalysisService(Protocol):
    """Route-facing seam; the API tests stub this."""

    async def start_analysis(self, request: AnalyzeRequest) -> tuple[UUID, UUID]: ...


class IncidentAnalysisService:
    def __init__(
        self,
        *,
        events: EventRepository,
        incidents: IncidentRepository,
        investigations: InvestigationRepository,
        memory: IncidentMemory,
        orchestrator_factory: OrchestratorFactory,
        bus: InProcessEventBus,
        executor: Executor,
        metrics: Metrics | None = None,
    ) -> None:
        self._events = events
        self._incidents = incidents
        self._investigations = investigations
        self._memory = memory
        self._orchestrator_factory = orchestrator_factory
        self._bus = bus
        self._executor = executor
        self._metrics = metrics or NullMetrics()
        self._running: set[asyncio.Task[None]] = set()

    async def start_analysis(self, request: AnalyzeRequest) -> tuple[UUID, UUID]:
        incident_id = uuid4()
        investigation_id = uuid4()
        now = datetime.now(tz=UTC)
        await self._incidents.create(
            TimeWindow(start=now, end=now), incident_id=incident_id, status="analyzing"
        )
        task = asyncio.create_task(
            self._run(incident_id, investigation_id, request),
            name=f"analysis-{incident_id}",
        )
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return incident_id, investigation_id

    async def shutdown(self) -> None:
        for task in tuple(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)

    # ------------------------------------------------------------- pipeline
    async def _run(
        self, incident_id: UUID, investigation_id: UUID, request: AnalyzeRequest
    ) -> None:
        publisher = TopicPublisher(self._bus, incident_id)
        structlog.contextvars.bind_contextvars(
            incident_id=str(incident_id), investigation_id=str(investigation_id)
        )
        started = time.perf_counter()
        self._metrics.set_gauge("active_investigations", float(len(self._running)))
        try:
            events = await self._ingest_and_parse(request)
            await self._events.bulk_insert(events)
            self._metrics.inc("logs_parsed_total", len(events))
            logger.info("events_persisted", count=len(events))

            detector = default_engine()
            for event in events:
                detector.observe(event)
            clusters = detector.flush(now=events[-1].timestamp + timedelta(minutes=1))
            for cluster in clusters:
                self._metrics.inc("anomaly_clusters_total", kind=cluster.kind.value)
            logger.info("anomalies_detected", clusters=len(clusters))

            boost = {
                event_id: cluster.confidence
                for cluster in clusters
                for event_id in cluster.representative_events
            }
            interesting = [
                event
                for event in events
                if event.severity >= Severity.WARNING or event.event_id in boost
            ]
            dependency_map = generate(seed=request.seed).dependency_map
            ctx = CorrelationContext(
                dependency_map=dependency_map, similarity=TokenJaccardSimilarity()
            )
            edges = CorrelationEngine().correlate(interesting, ctx)
            self._metrics.inc("correlation_edges_created_total", len(edges))
            graph = IncidentGraph(interesting, edges).prune(min_score=0.35)
            logger.info("graph_built", candidates=len(interesting), edges=graph.edge_count)

            window = TimeWindow(start=events[0].timestamp, end=events[-1].timestamp)
            await self._incidents.attach_analysis(
                incident_id, window, clusters=clusters, edges=graph.edges()
            )

            dataset = InvestigationDataStore(events, clusters, graph, dependency_map)
            evidence = build_evidence(dataset)
            similar = await self._memory.recall(
                f"{evidence.root_candidates[0].event.message}"
                if evidence.root_candidates
                else "incident",
                exclude_incident=incident_id,
            )
            if similar:
                evidence = dataclasses.replace(
                    evidence,
                    similar_incidents=tuple(match.as_evidence() for match in similar),
                )

            orchestrator = self._orchestrator_factory(publisher)
            result = await orchestrator.investigate(
                dataset, evidence, investigation_id=investigation_id
            )

            await self._investigations.persist(incident_id, result)
            await self._memory.remember(incident_id, result.assessment)
            await self._incidents.set_status(incident_id, "completed")

            for execution in result.tool_executions:
                self._metrics.inc(
                    "agent_tool_calls_total",
                    agent=execution.agent,
                    tool=execution.tool,
                    outcome=execution.outcome,
                )
            self._metrics.inc("ai_tokens_used_total", result.usage.input_tokens, direction="input")
            self._metrics.inc(
                "ai_tokens_used_total", result.usage.output_tokens, direction="output"
            )
            self._metrics.inc("investigations_total", status="completed")
            logger.info(
                "investigation_completed",
                root_cause=result.assessment.root_cause,
                confidence=result.assessment.confidence,
            )
        except Exception:
            self._metrics.inc("investigations_total", status="failed")
            logger.exception("analysis_failed")
            await self._incidents.set_status(incident_id, "failed")
            await publisher.publish(
                ProgressEvent(
                    investigation_id=investigation_id,
                    kind=ProgressKind.INVESTIGATION_FAILED,
                    message="analysis pipeline failed",
                    progress=1.0,
                )
            )
            raise
        finally:
            self._metrics.observe("investigation_duration_seconds", time.perf_counter() - started)
            self._metrics.set_gauge("active_investigations", float(max(len(self._running) - 1, 0)))
            structlog.contextvars.unbind_contextvars("incident_id", "investigation_id")

    async def _ingest_and_parse(self, request: AnalyzeRequest) -> list[LogEvent]:
        incident = generate(seed=request.seed)
        with tempfile.TemporaryDirectory(prefix="aegis-incident-") as tmp:
            sources = materialize(incident, Path(tmp))
            raw: Channel[RawLogEvent] = Channel(maxsize=2048)
            parsed: Channel[LogEvent] = Channel(maxsize=2048)
            stage = ParsingStage(self._executor, batch_size=500, max_wait=0.05)
            supervisor = IngestionSupervisor(sources, raw)

            async def collect() -> list[LogEvent]:
                return [event async for event in parsed]

            async with asyncio.TaskGroup() as tg:
                ingest = tg.create_task(supervisor.run())
                tg.create_task(stage.run(raw, parsed))
                collector = tg.create_task(collect())
        for source_id, count in ingest.result().items():
            self._metrics.inc("logs_ingested_total", count, source=source_id)
        return sorted(collector.result(), key=lambda event: event.timestamp)


class OrchestratorFactory(Protocol):
    def __call__(self, publisher: TopicPublisher) -> InvestigationOrchestrator: ...

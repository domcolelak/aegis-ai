"""End-to-end: synthetic incident files -> root cause candidates.

Exercises the real deterministic pipeline with no mocks: file ingestion with
backpressure, batched parsing, anomaly detection, correlation, and graph
analysis. The acceptance criterion from the project brief: the incident
trigger region (booking-path traffic spike / stripe latency) must rank as the
top root candidate, and the strongest causal chain to the user-visible outage
must pass through the database pool exhaustion.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from aegis.core.channel import Channel
from aegis.correlation import CorrelationContext, CorrelationEngine, TokenJaccardSimilarity
from aegis.detection import (
    AnomalyCluster,
    AnomalyKind,
    DetectionEngine,
    ErrorRatioDetector,
    FrequencySpikeDetector,
    NewSignatureConfig,
    NewSignatureDetector,
    RetryStormDetector,
)
from aegis.events import EventKind, LogEvent, RawLogEvent, Severity
from aegis.graph import IncidentGraph
from aegis.ingestion import IngestionSupervisor
from aegis.parsing import ParsingStage
from aegis.synthetic import SyntheticIncident, generate, materialize

type PipelineResult = tuple[list[LogEvent], SyntheticIncident]


async def _run_pipeline(tmp_path: Path) -> PipelineResult:
    incident = generate(seed=7)
    sources = materialize(incident, tmp_path)
    raw: Channel[RawLogEvent] = Channel(maxsize=1024)
    parsed: Channel[LogEvent] = Channel(maxsize=1024)

    with ThreadPoolExecutor(max_workers=2) as executor:
        stage = ParsingStage(executor, batch_size=500, max_wait=0.05)
        supervisor = IngestionSupervisor(sources, raw)

        async def collect() -> list[LogEvent]:
            return [event async for event in parsed]

        async with asyncio.TaskGroup() as tg:
            tg.create_task(supervisor.run())
            tg.create_task(stage.run(raw, parsed))
            collector = tg.create_task(collect())

    events = sorted(collector.result(), key=lambda event: event.timestamp)
    return events, incident


@pytest.fixture(scope="module")
def pipeline_result(tmp_path_factory: pytest.TempPathFactory) -> PipelineResult:
    tmp_path = tmp_path_factory.mktemp("incident")
    return asyncio.run(_run_pipeline(tmp_path))


def _detect(events: list[LogEvent]) -> list[AnomalyCluster]:
    engine = DetectionEngine(
        [
            FrequencySpikeDetector(),
            NewSignatureDetector(NewSignatureConfig(learning_events=400)),
            ErrorRatioDetector(),
            RetryStormDetector(),
        ]
    )
    for event in events:
        engine.observe(event)
    return engine.flush(now=events[-1].timestamp + timedelta(seconds=30))


class TestFullPipeline:
    def test_ingestion_and_parsing_preserve_the_stream(
        self, pipeline_result: PipelineResult
    ) -> None:
        events, _ = pipeline_result

        assert len(events) > 700
        assert {event.service for event in events} == {
            "booking-api",
            "payments",
            "postgres",
            "nginx",
            "worker-1",
        }
        # Trace propagation across differently-formatted sources survived.
        traced = [event for event in events if event.trace_id is not None]
        assert len(traced) > 500

    def test_detectors_find_all_four_anomaly_kinds(self, pipeline_result: PipelineResult) -> None:
        events, _ = pipeline_result

        clusters = _detect(events)
        kinds = {cluster.kind for cluster in clusters}

        assert AnomalyKind.FREQUENCY_SPIKE in kinds
        assert AnomalyKind.NEW_SIGNATURE in kinds
        assert AnomalyKind.ERROR_RATIO_DEVIATION in kinds
        assert AnomalyKind.RETRY_STORM in kinds
        storm = next(c for c in clusters if c.kind is AnomalyKind.RETRY_STORM)
        assert storm.service == "worker-1"

    def test_root_candidate_is_the_incident_trigger_region(
        self, pipeline_result: PipelineResult
    ) -> None:
        events, incident = pipeline_result
        clusters = _detect(events)

        boost: dict[UUID, float] = {}
        for cluster in clusters:
            for event_id in cluster.representative_events:
                boost[event_id] = max(boost.get(event_id, 0.0), cluster.confidence)

        interesting = [
            event
            for event in events
            if event.severity >= Severity.WARNING or event.event_id in boost
        ]
        assert 100 < len(interesting) < 600, "pruning must reduce the problem space"

        ctx = CorrelationContext(
            dependency_map=incident.dependency_map, similarity=TokenJaccardSimilarity()
        )
        edges = CorrelationEngine().correlate(interesting, ctx)
        assert len(edges) > 50

        graph = IncidentGraph(interesting, edges).prune(min_score=0.35)
        candidates = graph.root_candidates(top_k=5, anomaly_boost=boost)
        assert candidates

        top = candidates[0]
        trigger_window_end = incident.incident_window.start + timedelta(seconds=15)
        assert top.event.timestamp <= trigger_window_end, (
            f"top candidate should be in the trigger region, got "
            f"{top.event.service}@{top.event.timestamp}: {top.event.message}"
        )
        assert top.event.service in {"booking-api", "payments"}

        # The strongest chain from the trigger to the user-visible outage
        # must pass through the database pool exhaustion.
        nginx_failures = [event for event in interesting if event.service == "nginx"]
        sink = max(nginx_failures, key=lambda event: event.timestamp)
        chain = graph.strongest_chain(top.event.event_id, sink.event_id)
        assert chain, "trigger must be causally connected to the outage"
        assert any(event.kind is EventKind.DB_POOL for event in chain), (
            "the causal chain must include pool exhaustion: "
            + " -> ".join(f"{e.service}:{e.message[:40]}" for e in chain)
        )

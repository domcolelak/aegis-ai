"""Offline end-to-end demo: no database, no API key, no network.

    uv run python scripts/demo.py [--pace SECONDS]

Generates the synthetic incident, runs the full deterministic pipeline
(ingestion -> process-pool parsing -> detection -> correlation -> causal
graph), then the multi-agent investigation with the scripted provider, and
prints the validated RootCauseAssessment.

``--pace`` inserts presentation pauses between stages (the pipeline itself
finishes in about a second, which is great for users and terrible for
screen recordings).
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path

from aegis.core.channel import Channel
from aegis.correlation import CorrelationContext, CorrelationEngine, TokenJaccardSimilarity
from aegis.detection import default_engine
from aegis.events import LogEvent, RawLogEvent, Severity
from aegis.graph import IncidentGraph
from aegis.ingestion import IngestionSupervisor
from aegis.investigation import InvestigationOrchestrator, build_evidence
from aegis.investigation.agents import (
    DatabaseInvestigator,
    DevilsAdvocate,
    IncidentCommander,
    LogAnalyst,
)
from aegis.investigation.data import InvestigationDataStore
from aegis.investigation.progress import ProgressEvent
from aegis.investigation.providers import ScriptedProvider
from aegis.investigation.providers.demo import demo_scripts
from aegis.investigation.tools import ToolRegistry, default_tools
from aegis.parsing import ParsingStage
from aegis.synthetic import generate, materialize


class PrintingPublisher:
    def __init__(self, pace: float = 0.0) -> None:
        self._pace = pace

    async def publish(self, event: ProgressEvent) -> None:
        agent = f" [{event.agent}]" if event.agent else ""
        print(f"  {event.progress:5.0%}{agent} {event.message}")
        await asyncio.sleep(self._pace / 2)


async def run_demo(pace: float = 0.0) -> None:
    started = time.perf_counter()
    incident = generate(seed=7)

    print("== Aegis AI offline demo ==")
    print("1. Ingesting and parsing the synthetic incident (5 services, 4 log formats)")
    with tempfile.TemporaryDirectory(prefix="aegis-demo-") as tmp:
        sources = materialize(incident, Path(tmp))
        raw: Channel[RawLogEvent] = Channel(maxsize=2048)
        parsed: Channel[LogEvent] = Channel(maxsize=2048)
        with ProcessPoolExecutor(max_workers=2) as executor:
            stage = ParsingStage(executor, batch_size=500, max_wait=0.05)

            async def collect() -> list[LogEvent]:
                return [event async for event in parsed]

            async with asyncio.TaskGroup() as tg:
                tg.create_task(IngestionSupervisor(sources, raw).run())
                tg.create_task(stage.run(raw, parsed))
                collector = tg.create_task(collect())
    events = sorted(collector.result(), key=lambda event: event.timestamp)
    print(f"   {len(events)} events parsed")
    await asyncio.sleep(pace)

    print("2. Detecting anomalies")
    detector = default_engine()
    for event in events:
        detector.observe(event)
    clusters = detector.flush(now=events[-1].timestamp + timedelta(minutes=1))
    for cluster in clusters:
        print(
            f"   [{cluster.kind.value}] {cluster.service}: {cluster.event_count} events "
            f"(confidence {cluster.confidence:.2f})"
        )

    await asyncio.sleep(pace)
    print("3. Correlating and building the causal graph")
    boost = {
        event_id: cluster.confidence
        for cluster in clusters
        for event_id in cluster.representative_events
    }
    interesting = [
        event for event in events if event.severity >= Severity.WARNING or event.event_id in boost
    ]
    ctx = CorrelationContext(
        dependency_map=incident.dependency_map, similarity=TokenJaccardSimilarity()
    )
    edges = CorrelationEngine().correlate(interesting, ctx)
    graph = IncidentGraph(interesting, edges).prune(min_score=0.35)
    print(f"   {graph.node_count} nodes, {graph.edge_count} edges after pruning")

    dataset = InvestigationDataStore(events, clusters, graph, incident.dependency_map)
    evidence = build_evidence(dataset)
    top = evidence.root_candidates[0]
    print(f"   top root candidate: {top.event.service}: {top.event.message[:70]}")

    await asyncio.sleep(pace)
    print("4. Running the multi-agent investigation (scripted provider, offline)")
    provider = ScriptedProvider(demo_scripts())
    registry = ToolRegistry(default_tools())
    orchestrator = InvestigationOrchestrator(
        specialists=[LogAnalyst(provider, registry), DatabaseInvestigator(provider, registry)],
        advocate=DevilsAdvocate(provider, registry),
        commander=IncidentCommander(provider, registry),
        publisher=PrintingPublisher(pace),
    )
    result = await orchestrator.investigate(dataset, evidence)
    assessment = result.assessment
    await asyncio.sleep(pace)

    elapsed = time.perf_counter() - started
    print()
    print("=" * 72)
    print(f"ROOT CAUSE PROBABILITY: {assessment.confidence:.1%}")
    print(assessment.root_cause)
    print()
    print(f"PROBABLE TRIGGER: {assessment.probable_trigger}")
    print()
    print("FAILURE CHAIN:")
    for step in assessment.failure_chain:
        print(f"  {step.service}: {step.description}")
    print()
    print("CONTRADICTING EVIDENCE (Devil's Advocate survived review):")
    for item in assessment.contradicting_evidence:
        print(f"  - {item}")
    print()
    print("RECOMMENDED ACTIONS:")
    for action in assessment.recommended_actions:
        print(f"  - {action}")
    print("=" * 72)
    print(f"done in {elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pace", type=float, default=0.0, help="pause between stages (seconds)")
    args = parser.parse_args()
    asyncio.run(run_demo(pace=args.pace))

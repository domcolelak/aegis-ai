"""EvidenceBundle: the deterministic engine's structured handoff to the AI.

Agents never receive raw logs. They receive this bundle -- anomaly clusters,
ranked root candidates, and the strongest causal chains -- rendered into a
compact prompt, and may drill deeper only through the audited tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aegis.events import Severity, TimeWindow
from aegis.investigation.data import event_digest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aegis.detection import AnomalyCluster
    from aegis.events import LogEvent
    from aegis.graph import RootCandidate
    from aegis.investigation.data import InvestigationDataStore


@dataclass(slots=True, frozen=True)
class EvidenceBundle:
    incident_window: TimeWindow
    services: tuple[str, ...]
    dependency_map: Mapping[str, frozenset[str]]
    clusters: tuple[AnomalyCluster, ...]
    root_candidates: tuple[RootCandidate, ...]
    causal_chains: tuple[tuple[LogEvent, ...], ...]
    # Populated by the incident-memory milestone (pgvector retrieval).
    similar_incidents: tuple[str, ...] = ()


def build_evidence(data: InvestigationDataStore, *, top_candidates: int = 3) -> EvidenceBundle:
    events = data.events
    if not events:
        raise ValueError("cannot build evidence from an empty dataset")
    window = TimeWindow(start=events[0].timestamp, end=events[-1].timestamp)

    boost = {
        event_id: cluster.confidence
        for cluster in data.clusters
        for event_id in cluster.representative_events
    }
    candidates = data.graph.root_candidates(top_k=top_candidates, anomaly_boost=boost)

    # The user-visible failure: the latest ERROR-or-worse event in the graph.
    sink = max(
        (event for event in events if event.severity >= Severity.ERROR and _in_graph(data, event)),
        key=lambda event: event.timestamp,
        default=None,
    )
    chains: list[tuple[LogEvent, ...]] = []
    if sink is not None:
        for candidate in candidates:
            chain = data.graph.strongest_chain(candidate.event.event_id, sink.event_id)
            if chain:
                chains.append(tuple(chain))

    return EvidenceBundle(
        incident_window=window,
        services=tuple(sorted({event.service for event in events})),
        dependency_map=data.dependency_map,
        clusters=data.clusters,
        root_candidates=tuple(candidates),
        causal_chains=tuple(chains),
    )


def render_evidence(bundle: EvidenceBundle) -> str:
    """Token-conscious plain-text rendering shared by all agent prompts."""
    lines: list[str] = [
        "## Incident window",
        f"{bundle.incident_window.start.isoformat()} .. {bundle.incident_window.end.isoformat()}",
        "",
        "## Services and dependencies (service -> calls)",
    ]
    for service in bundle.services:
        deps = ", ".join(sorted(bundle.dependency_map.get(service, frozenset()))) or "-"
        lines.append(f"- {service} -> {deps}")

    lines += ["", "## Anomaly clusters (detector output, deterministic)"]
    for cluster in bundle.clusters:
        attrs = ", ".join(f"{k}={v}" for k, v in cluster.attributes.items())
        lines.append(
            f"- [{cluster.kind.value}] service={cluster.service} "
            f"window={cluster.window.start.strftime('%H:%M:%S')}-"
            f"{cluster.window.end.strftime('%H:%M:%S')} events={cluster.event_count} "
            f"confidence={cluster.confidence:.2f} ({attrs})"
        )

    lines += ["", "## Root candidates (graph analysis, ranked)"]
    for rank, candidate in enumerate(bundle.root_candidates, start=1):
        digest = event_digest(candidate.event)
        lines.append(
            f"{rank}. score={candidate.score:.3f} reach={candidate.reach:.2f} "
            f"earliness={candidate.earliness:.2f} -> {digest['service']} "
            f"{digest['severity']} [{digest['event_id']}]: {digest['message']}"
        )

    lines += ["", "## Strongest causal chains (candidate -> visible failure)"]
    if not bundle.causal_chains:
        lines.append("(none found)")
    for chain in bundle.causal_chains:
        rendered = "\n   -> ".join(
            f"{event.service} [{event.kind.value}] {event.message[:90]}" for event in chain
        )
        lines.append(f"- {rendered}")

    if bundle.similar_incidents:
        lines += ["", "## Similar historical incidents"]
        lines.extend(f"- {summary}" for summary in bundle.similar_incidents)

    return "\n".join(lines)


def _in_graph(data: InvestigationDataStore, event: LogEvent) -> bool:
    try:
        data.graph.event(event.event_id)
    except KeyError:
        return False
    return True

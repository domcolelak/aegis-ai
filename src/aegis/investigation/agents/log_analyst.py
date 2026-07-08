"""Log Analyst: patterns, timelines, and signature families."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import SpecialistFinding
from aegis.investigation.evidence import EvidenceBundle, render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class LogAnalyst(Agent[EvidenceBundle, SpecialistFinding]):
    name = "log_analyst"
    output_model = SpecialistFinding

    def role_instructions(self) -> str:
        return (
            "a log analysis specialist. Reconstruct the incident timeline from "
            "log patterns: which error signatures appeared first, how they "
            "spread between services, and which anomaly clusters are symptoms "
            "versus causes."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return (
            "inspect_event_window",
            "search_events",
            "find_similar_events",
            "get_anomaly_details",
        )

    def render_input(self, data: EvidenceBundle) -> str:
        return "Investigate this incident from the log-pattern perspective.\n\n" + render_evidence(
            data
        )

"""Database Investigator: connections, pools, query failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import SpecialistFinding
from aegis.investigation.evidence import EvidenceBundle, render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class DatabaseInvestigator(Agent[EvidenceBundle, SpecialistFinding]):
    name = "database_investigator"
    output_model = SpecialistFinding

    def role_instructions(self) -> str:
        return (
            "a database reliability specialist. Determine whether database "
            "resources (connection pools, sessions, locks) caused or amplified "
            "the incident: who exhausted what, in which order, and whether the "
            "pressure pattern indicates a leak, a stampede, or organic load."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return (
            "analyze_db_connections",
            "calculate_error_rate",
            "inspect_event_window",
            "inspect_dependency_graph",
            "search_events",
        )

    def render_input(self, data: EvidenceBundle) -> str:
        return (
            "Investigate this incident from the database-resource perspective.\n\n"
            + render_evidence(data)
        )

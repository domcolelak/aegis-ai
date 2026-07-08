"""Devil's Advocate: attacks the leading hypothesis after the specialists."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import AdvocateChallenge
from aegis.investigation.briefs import AdvocateBrief
from aegis.investigation.evidence import render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class DevilsAdvocate(Agent[AdvocateBrief, AdvocateChallenge]):
    name = "devils_advocate"
    output_model = AdvocateChallenge

    def role_instructions(self) -> str:
        return (
            "an adversarial reviewer. Your only job is to attack the leading "
            "hypothesis: find evidence that contradicts it, alternative "
            "explanations that fit the same data, and gaps the specialists "
            "glossed over. Being convinced is a failure mode; verify with "
            "tools before conceding any point."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return (
            "inspect_event_window",
            "search_events",
            "find_similar_events",
            "calculate_error_rate",
            "get_anomaly_details",
        )

    def render_input(self, data: AdvocateBrief) -> str:
        findings = "\n\n".join(
            f"### Finding by {name}\n{finding.model_dump_json(indent=2)}"
            for name, finding in sorted(data.findings.items())
        )
        return (
            "Challenge the specialists' conclusions below.\n\n"
            f"{render_evidence(data.evidence)}\n\n## Specialist findings\n{findings}"
        )

"""Incident Commander: synthesizes everything into the final assessment.

Deliberately tool-less: the Commander weighs evidence already gathered by
the specialists and the Advocate; giving it tools would invite one more
uncontrolled investigation pass instead of a synthesis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import RootCauseAssessment
from aegis.investigation.briefs import CommanderBrief
from aegis.investigation.evidence import render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class IncidentCommander(Agent[CommanderBrief, RootCauseAssessment]):
    name = "incident_commander"
    output_model = RootCauseAssessment

    def role_instructions(self) -> str:
        return (
            "the incident commander. Weigh the specialists' findings against "
            "the Devil's Advocate's objections and the deterministic evidence, "
            "then commit to the most probable root cause. Confidence must "
            "reflect the strength of the objections: unresolved strong "
            "counterarguments mean lower confidence. List contradicting "
            "evidence honestly instead of omitting it."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return ()

    def render_input(self, data: CommanderBrief) -> str:
        findings = "\n\n".join(
            f"### Finding by {name}\n{finding.model_dump_json(indent=2)}"
            for name, finding in sorted(data.findings.items())
        )
        return (
            "Produce the final root-cause assessment.\n\n"
            f"{render_evidence(data.evidence)}\n\n"
            f"## Specialist findings\n{findings}\n\n"
            f"## Devil's Advocate challenge\n{data.challenge.model_dump_json(indent=2)}"
        )

"""Code Investigator: connects the failure evidence to actual source code."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import SpecialistFinding
from aegis.investigation.evidence import EvidenceBundle, render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class CodeInvestigator(Agent[EvidenceBundle, SpecialistFinding]):
    name = "code_investigator"
    output_model = SpecialistFinding

    def role_instructions(self) -> str:
        return (
            "a source-code specialist. Locate the code paths implicated by the "
            "evidence: search the repository for the failing operations, read "
            "the surrounding code, and identify concrete defects (unclosed "
            "resources, missing error handling, wrong ordering). Cite exact "
            "file paths and line numbers in your findings; a hypothesis "
            "without a location is not finished."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return (
            "list_repo_files",
            "search_source",
            "read_source",
            "find_symbol",
            "git_history",
            "search_events",
        )

    def render_input(self, data: EvidenceBundle) -> str:
        return "Find the code responsible for this incident.\n\n" + render_evidence(data)

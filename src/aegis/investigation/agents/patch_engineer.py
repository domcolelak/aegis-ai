"""Patch Engineer: turns the verdict into a reviewable unified diff.

The proposal is never applied -- it is validated (every path must stay inside
the configured repository) and stored for human review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.investigation.agents.base import Agent
from aegis.investigation.assessment import PatchProposal
from aegis.investigation.briefs import PatchBrief
from aegis.investigation.evidence import render_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence


class PatchEngineer(Agent[PatchBrief, PatchProposal]):
    name = "patch_engineer"
    output_model = PatchProposal

    def role_instructions(self) -> str:
        return (
            "a remediation engineer. Read the exact current source before "
            "writing anything; produce a minimal unified diff (--- a/path, "
            "+++ b/path, @@ hunks) that fixes the root cause and nothing "
            "else. Repository-relative paths only. State the risks of the "
            "change honestly; the diff will be reviewed by a human, never "
            "auto-applied."
        )

    def allowed_tools(self) -> Sequence[str] | None:
        return ("read_source", "search_source", "list_repo_files")

    def render_input(self, data: PatchBrief) -> str:
        return (
            "Propose a patch for the confirmed root cause.\n\n"
            f"## Root cause assessment\n{data.assessment.model_dump_json(indent=2)}\n\n"
            f"{render_evidence(data.evidence)}"
        )

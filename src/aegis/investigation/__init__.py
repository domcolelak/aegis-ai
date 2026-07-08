"""AI investigation layer.

AI enters only after the deterministic engine has reduced millions of events
to an EvidenceBundle (anomaly clusters, root candidates, causal chains).
Agents never see raw logs; they drill into the data through typed, audited,
budgeted tools, and every LLM output crossing back into the system is
validated with Pydantic before anything trusts it.
"""

from aegis.investigation.assessment import (
    AdvocateChallenge,
    Hypothesis,
    RootCauseAssessment,
    SpecialistFinding,
)
from aegis.investigation.evidence import EvidenceBundle, build_evidence, render_evidence
from aegis.investigation.orchestrator import InvestigationOrchestrator, InvestigationResult

__all__ = [
    "AdvocateChallenge",
    "EvidenceBundle",
    "Hypothesis",
    "InvestigationOrchestrator",
    "InvestigationResult",
    "RootCauseAssessment",
    "SpecialistFinding",
    "build_evidence",
    "render_evidence",
]

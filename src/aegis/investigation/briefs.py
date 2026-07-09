"""Composite inputs for the second-wave agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aegis.investigation.assessment import (
        AdvocateChallenge,
        RootCauseAssessment,
        SpecialistFinding,
    )
    from aegis.investigation.evidence import EvidenceBundle


@dataclass(slots=True, frozen=True)
class AdvocateBrief:
    evidence: EvidenceBundle
    findings: Mapping[str, SpecialistFinding]


@dataclass(slots=True, frozen=True)
class CommanderBrief:
    evidence: EvidenceBundle
    findings: Mapping[str, SpecialistFinding]
    challenge: AdvocateChallenge


@dataclass(slots=True, frozen=True)
class PatchBrief:
    evidence: EvidenceBundle
    assessment: RootCauseAssessment
    findings: Mapping[str, SpecialistFinding]

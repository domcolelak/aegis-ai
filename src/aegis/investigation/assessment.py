"""Validated shapes for everything an LLM hands back to the system.

``extra="forbid"`` everywhere: a model that invents fields fails loudly and
gets one correction attempt, instead of silently smuggling unvalidated data
into the incident record.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

type Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class _LlmOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Hypothesis(_LlmOutput):
    statement: str
    confidence: Confidence
    supporting_evidence: list[str] = []


class SpecialistFinding(_LlmOutput):
    """Output of one specialist investigator."""

    summary: str
    hypotheses: Annotated[list[Hypothesis], Field(min_length=1)]
    notable_event_ids: list[str] = []
    open_questions: list[str] = []


class AdvocateChallenge(_LlmOutput):
    """The Devil's Advocate's attack on the leading hypothesis."""

    weaknesses: list[str]
    alternative_hypotheses: list[Hypothesis] = []
    strongest_counterargument: str
    doubt: Confidence


class FailureChainStep(_LlmOutput):
    service: str
    description: str
    event_ref: str | None = None


class CodeLocation(_LlmOutput):
    path: str
    reason: str
    line_start: int | None = None
    line_end: int | None = None


class RootCauseAssessment(_LlmOutput):
    """The Incident Commander's final, validated verdict."""

    root_cause: str
    confidence: Confidence
    probable_trigger: str
    failure_chain: Annotated[list[FailureChainStep], Field(min_length=1)]
    supporting_evidence: list[str]
    contradicting_evidence: list[str] = []
    affected_services: list[str]
    suspected_code_locations: list[CodeLocation] = []
    recommended_actions: Annotated[list[str], Field(min_length=1)]

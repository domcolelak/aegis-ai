"""API request/response models -- the HTTP trust boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aegis.investigation.progress import ProgressEvent


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # MVP: the seeded synthetic incident is the only ingest trigger the API
    # exposes; registering live sources per request is roadmap work.
    synthetic: bool = True
    seed: int = Field(default=7, ge=0)


class AnalyzeAccepted(BaseModel):
    incident_id: UUID
    investigation_id: UUID
    websocket: str


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    log_format: str


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    log_format: str
    created_at: datetime


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    status: str
    window_start: datetime
    window_end: datetime
    summary: str | None
    created_at: datetime


class EdgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_event: UUID
    target_event: UUID
    composite_score: float
    strategy_scores: dict[str, object]


class GraphOut(BaseModel):
    incident_id: UUID
    edges: list[EdgeOut]


class FindingOut(BaseModel):
    agent: str
    finding: dict[str, object]


class InvestigationOut(BaseModel):
    investigation_id: UUID
    incident_id: UUID
    status: str
    started_at: datetime
    completed_at: datetime | None
    assessment: dict[str, object] | None
    challenge: dict[str, object] | None
    findings: list[FindingOut]


class PatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    investigation_id: UUID
    reasoning: str
    diff: str
    affected_files: list[str]
    confidence: float
    risks: list[str]
    created_at: datetime


class ProgressEventOut(BaseModel):
    """The typed WebSocket wire format."""

    type: str
    investigation_id: UUID
    message: str
    progress: float
    agent: str | None
    at: datetime

    @classmethod
    def from_event(cls, event: ProgressEvent) -> ProgressEventOut:
        return cls(
            type=event.kind.value,
            investigation_id=event.investigation_id,
            message=event.message,
            progress=event.progress,
            agent=event.agent,
            at=event.at,
        )

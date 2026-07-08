"""Typed investigation progress events and the publisher seam.

The API milestone streams these over WebSocket; tests collect them in
memory. The orchestrator publishes, never caring who listens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class ProgressKind(StrEnum):
    INVESTIGATION_STARTED = "investigation.started"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    INVESTIGATION_COMPLETED = "investigation.completed"
    INVESTIGATION_FAILED = "investigation.failed"


@dataclass(slots=True, frozen=True)
class ProgressEvent:
    investigation_id: UUID
    kind: ProgressKind
    message: str
    progress: float
    agent: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class ProgressPublisher(Protocol):
    async def publish(self, event: ProgressEvent) -> None: ...


class NullPublisher:
    async def publish(self, event: ProgressEvent) -> None:
        return None


class CollectingPublisher:
    """Test/demo publisher that keeps everything in memory."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    async def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)

"""Tool seam, execution context, and the audit trail."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from aegis.investigation.providers.base import TokenUsage, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from aegis.inspection import RepositoryInspector
    from aegis.investigation.data import InvestigationDataStore


@dataclass(slots=True, frozen=True)
class ToolResult:
    """JSON-serializable payload returned to the model."""

    data: object


@dataclass(slots=True, frozen=True)
class ToolExecution:
    """One audited tool call; the investigation's paper trail."""

    agent: str
    tool: str
    arguments: Mapping[str, object]
    outcome: Literal["ok", "error", "timeout"]
    detail: str
    duration_ms: float
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class InvestigationAudit:
    def __init__(self) -> None:
        self._entries: list[ToolExecution] = []

    def record(self, entry: ToolExecution) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> tuple[ToolExecution, ...]:
        return tuple(self._entries)


class UsageMeter:
    """Accumulates LLM token usage across every agent of one investigation."""

    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: TokenUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def total(self) -> TokenUsage:
        return TokenUsage(input_tokens=self.input_tokens, output_tokens=self.output_tokens)


@dataclass(slots=True, frozen=True)
class InvestigationContext:
    investigation_id: UUID
    data: InvestigationDataStore
    audit: InvestigationAudit
    tool_timeout_s: float = 10.0
    usage: UsageMeter = field(default_factory=UsageMeter)
    # Optional: source-code inspection jail; code tools fail cleanly when absent.
    repository: RepositoryInspector | None = None


class Tool[TArgs: BaseModel](ABC):
    """A capability exposed to agents: typed arguments in, JSON out.

    The Pydantic ``args_model`` is the single source of truth: it validates
    incoming arguments *and* generates the JSON schema the provider shows the
    model. There is no second, hand-written schema to drift.
    """

    name: str
    description: str
    args_model: type[TArgs]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
        )

    @abstractmethod
    async def execute(self, args: TArgs, ctx: InvestigationContext) -> ToolResult: ...

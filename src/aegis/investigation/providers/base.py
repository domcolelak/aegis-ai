"""Provider-neutral completion types.

Shaped close to the Anthropic Messages API (the reference implementation)
but owned by Aegis: agents and tests build these types, and a provider
adapter translates them. Swapping vendors touches one module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(slots=True, frozen=True)
class TextBlock:
    text: str


@dataclass(slots=True, frozen=True)
class ToolUseBlock:
    tool_use_id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(slots=True, frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


type ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(slots=True, frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: tuple[ContentBlock, ...]


@dataclass(slots=True, frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(slots=True, frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True, frozen=True)
class CompletionRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = 2048
    temperature: float = 0.0


@dataclass(slots=True, frozen=True)
class Completion:
    content: tuple[TextBlock | ToolUseBlock, ...]
    stop_reason: Literal["end_turn", "tool_use", "max_tokens"]
    usage: TokenUsage = TokenUsage()


class LLMProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> Completion: ...

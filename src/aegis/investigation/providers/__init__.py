"""LLM provider seam: agents depend on the protocol, never on an SDK."""

from aegis.investigation.providers.base import (
    Completion,
    CompletionRequest,
    ContentBlock,
    LLMProvider,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)
from aegis.investigation.providers.resilient import RateLimitedProvider, RetryingProvider
from aegis.investigation.providers.scripted import ScriptedProvider

__all__ = [
    "Completion",
    "CompletionRequest",
    "ContentBlock",
    "LLMProvider",
    "Message",
    "RateLimitedProvider",
    "RetryingProvider",
    "ScriptedProvider",
    "TextBlock",
    "TokenUsage",
    "ToolResultBlock",
    "ToolSpec",
    "ToolUseBlock",
]

"""Anthropic Messages API adapter.

Deliberately thin: translation between Aegis' neutral completion types and
the SDK, plus error mapping into the Aegis exception hierarchy so resilience
decorators can tell transient failures from permanent ones. Retries and
concurrency limits live in the decorators, not here (the SDK's own retries
are disabled to keep one retry policy in the system).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import anthropic

from aegis.core.errors import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from aegis.investigation.providers.base import (
    Completion,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolParam

    from aegis.investigation.providers.base import CompletionRequest, Message

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicProvider:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)
        self._model = model

    async def complete(self, request: CompletionRequest) -> Completion:
        tools: list[ToolParam] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.input_schema),
            }
            for tool in request.tools
        ]
        try:
            response = await self._client.messages.create(
                model=self._model,
                system=request.system,
                messages=[_to_message_param(message) for message in request.messages],
                tools=tools if tools else anthropic.omit,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitedError(str(exc)) from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailableError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc

        content: list[TextBlock | ToolUseBlock] = []
        for block in response.content:
            if block.type == "text":
                content.append(TextBlock(block.text))
            elif block.type == "tool_use":
                content.append(
                    ToolUseBlock(tool_use_id=block.id, name=block.name, arguments=block.input)
                )
        stop_reason: Literal["end_turn", "tool_use", "max_tokens"]
        match response.stop_reason:
            case "tool_use":
                stop_reason = "tool_use"
            case "max_tokens":
                stop_reason = "max_tokens"
            case _:
                stop_reason = "end_turn"
        return Completion(
            content=tuple(content),
            stop_reason=stop_reason,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )


def _to_message_param(message: Message) -> MessageParam:
    blocks: list[object] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.tool_use_id,
                    "name": block.name,
                    "input": dict(block.arguments),
                }
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return cast("MessageParam", {"role": message.role, "content": blocks})

"""Generic agent with the guarded tool-calling loop.

The loop is concrete, shared machinery -- budget enforcement, timeouts via
the registry, JSON validation with one correction attempt -- which is why
Agent is an ABC and not a Protocol: subclasses supply prompts and types,
never their own loop. Type parameters: TInput is what the orchestrator
hands the agent, TFinding is the validated Pydantic model it must return.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from aegis.core.errors import InvestigationError
from aegis.investigation.providers.base import (
    CompletionRequest,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aegis.investigation.providers.base import ContentBlock, LLMProvider
    from aegis.investigation.tools.base import InvestigationContext
    from aegis.investigation.tools.registry import ToolRegistry

_BUDGET_MESSAGE = (
    "Tool budget exhausted. Do not request more tools; provide your final answer as JSON now."
)


class Agent[TInput, TFinding: BaseModel](ABC):
    name: str
    output_model: type[TFinding]

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        *,
        max_tool_calls: int = 8,
        max_turns: int = 12,
        max_tokens: int = 2048,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._max_tool_calls = max_tool_calls
        self._max_turns = max_turns
        self._max_tokens = max_tokens

    # ----------------------------------------------------------- subclass API
    @abstractmethod
    def role_instructions(self) -> str:
        """What this agent is and how it should investigate."""

    @abstractmethod
    def render_input(self, data: TInput) -> str:
        """Turn the typed input into the opening user prompt."""

    def allowed_tools(self) -> Sequence[str] | None:
        """Tool allowlist; None means every registered tool."""
        return None

    # -------------------------------------------------------------- the loop
    def system_prompt(self) -> str:
        schema = json.dumps(self.output_model.model_json_schema())
        return (
            f"You are {self.name}. {self.role_instructions()}\n"
            "Verify hypotheses with the available tools before concluding; "
            "cite event ids from tool results as evidence.\n"
            "When finished, respond with ONLY a JSON object matching this schema:\n"
            f"{schema}"
        )

    async def investigate(self, ctx: InvestigationContext, data: TInput) -> TFinding:
        allowed = self.allowed_tools()
        tools = self._registry.specs(allowed) if allowed is None or allowed else ()
        messages: list[Message] = [
            Message(role="user", content=(TextBlock(self.render_input(data)),))
        ]
        tool_calls_used = 0
        validation_retries = 1

        for _ in range(self._max_turns):
            completion = await self._provider.complete(
                CompletionRequest(
                    system=self.system_prompt(),
                    messages=tuple(messages),
                    tools=tools,
                    max_tokens=self._max_tokens,
                )
            )
            tool_uses = [block for block in completion.content if isinstance(block, ToolUseBlock)]
            if tool_uses:
                messages.append(Message(role="assistant", content=completion.content))
                results: list[ContentBlock] = []
                for call in tool_uses:
                    if tool_calls_used >= self._max_tool_calls:
                        results.append(
                            ToolResultBlock(call.tool_use_id, _BUDGET_MESSAGE, is_error=True)
                        )
                        continue
                    tool_calls_used += 1
                    results.append(await self._registry.execute(self.name, call, ctx))
                messages.append(Message(role="user", content=tuple(results)))
                continue

            text = "".join(
                block.text for block in completion.content if isinstance(block, TextBlock)
            )
            try:
                return self._parse_finding(text)
            except (ValidationError, InvestigationError) as exc:
                if validation_retries == 0:
                    raise InvestigationError(f"{self.name}: reply failed validation twice") from exc
                validation_retries -= 1
                messages.append(Message(role="assistant", content=(TextBlock(text),)))
                messages.append(
                    Message(
                        role="user",
                        content=(
                            TextBlock(
                                f"Your reply was not valid: {exc}. "
                                "Respond with ONLY the corrected JSON object."
                            ),
                        ),
                    )
                )

        raise InvestigationError(f"{self.name}: exceeded {self._max_turns} turns")

    def _parse_finding(self, text: str) -> TFinding:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise InvestigationError("no JSON object found in the reply")
        return self.output_model.model_validate_json(text[start : end + 1])

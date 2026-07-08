"""Deterministic provider: replays scripted completions per agent.

Used by the test suite (no paid API calls, ever) and by the offline demo.
Scripts are keyed by a substring of the system prompt -- each agent's system
prompt starts with its own name, so concurrent agents sharing one provider
instance still get their own script.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

from aegis.investigation.providers.base import Completion, TextBlock, ToolUseBlock

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aegis.investigation.providers.base import CompletionRequest


class ScriptExhaustedError(AssertionError):
    """The agent asked for more completions than the script provides."""


class ScriptedProvider:
    def __init__(self, scripts: Mapping[str, Sequence[Completion]]) -> None:
        self._queues: dict[str, deque[Completion]] = {
            key: deque(completions) for key, completions in scripts.items()
        }

    async def complete(self, request: CompletionRequest) -> Completion:
        for key, queue in self._queues.items():
            if key in request.system:
                if not queue:
                    raise ScriptExhaustedError(f"script for {key!r} is exhausted")
                return queue.popleft()
        raise ScriptExhaustedError(
            f"no script matches system prompt starting {request.system[:60]!r}"
        )


def text_completion(text: str) -> Completion:
    return Completion(content=(TextBlock(text),), stop_reason="end_turn")


def json_completion(payload: object) -> Completion:
    return text_completion(json.dumps(payload))


def tool_call_completion(
    name: str, arguments: Mapping[str, object], *, call_id: str = "call-1"
) -> Completion:
    return Completion(
        content=(ToolUseBlock(tool_use_id=call_id, name=name, arguments=arguments),),
        stop_reason="tool_use",
    )

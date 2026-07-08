"""Tool dispatch: validation, timeout, audit -- never an if/elif chain.

Failures are returned to the model as error tool-results (so it can adapt)
rather than raised, but nothing is silent: every execution lands in the
audit trail with its outcome and timing.
"""

from __future__ import annotations

import asyncio
import json
import time

# Tool[Any]: a registry is a heterogeneous collection; each tool's TArgs is
# recovered internally via its own args_model, so Any never leaks outward.
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from aegis.investigation.providers.base import ToolResultBlock, ToolSpec
from aegis.investigation.tools.base import ToolExecution

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aegis.investigation.providers.base import ToolUseBlock
    from aegis.investigation.tools.base import InvestigationContext, Tool

_DETAIL_LIMIT = 2000


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool[Any]]) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name!r}")
            self._tools[tool.name] = tool

    def specs(self, allowed: Sequence[str] | None = None) -> tuple[ToolSpec, ...]:
        if allowed is None:
            return tuple(tool.spec() for tool in self._tools.values())
        missing = [name for name in allowed if name not in self._tools]
        if missing:
            raise KeyError(f"unknown tools in allowlist: {missing}")
        return tuple(self._tools[name].spec() for name in allowed)

    async def execute(
        self, agent: str, call: ToolUseBlock, ctx: InvestigationContext
    ) -> ToolResultBlock:
        started = time.perf_counter()
        outcome: Literal["ok", "error", "timeout"]

        tool = self._tools.get(call.name)
        if tool is None:
            outcome, detail = "error", f"unknown tool: {call.name!r}"
        else:
            try:
                async with asyncio.timeout(ctx.tool_timeout_s):
                    args = tool.args_model.model_validate(dict(call.arguments))
                    result = await tool.execute(args, ctx)
                outcome = "ok"
                detail = json.dumps(result.data, default=str)
            except ValidationError as exc:
                outcome, detail = "error", f"invalid arguments: {exc}"
            except TimeoutError:
                outcome, detail = "timeout", f"tool timed out after {ctx.tool_timeout_s}s"
            except Exception as exc:
                outcome, detail = "error", f"tool failed: {exc!r}"

        ctx.audit.record(
            ToolExecution(
                agent=agent,
                tool=call.name,
                arguments=dict(call.arguments),
                outcome=outcome,
                detail=detail[:_DETAIL_LIMIT],
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        )
        return ToolResultBlock(
            tool_use_id=call.tool_use_id,
            content=detail[:_DETAIL_LIMIT],
            is_error=outcome != "ok",
        )

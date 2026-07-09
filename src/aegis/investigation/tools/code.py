"""Source-inspection tools: the Code Investigator's and Patch Engineer's eyes.

All of them delegate to the root-jailed RepositoryInspector; when no
repository is configured for the investigation they fail with a clear error
result instead of pretending. File reads run in a worker thread so disk I/O
never blocks the loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.core.errors import RepositoryAccessError
from aegis.inspection import RepositoryInspector
from aegis.investigation.tools.base import InvestigationContext, Tool, ToolResult


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _repository(ctx: InvestigationContext) -> RepositoryInspector:
    if ctx.repository is None:
        raise RepositoryAccessError("no source repository is configured for this investigation")
    return ctx.repository


class ListFilesArgs(_Args):
    pattern: str = "**/*.py"
    limit: int = Field(default=100, ge=1, le=500)


class ListRepoFiles(Tool[ListFilesArgs]):
    name = "list_repo_files"
    description = "List repository files matching a glob pattern."
    args_model = ListFilesArgs

    async def execute(self, args: ListFilesArgs, ctx: InvestigationContext) -> ToolResult:
        repo = _repository(ctx)
        files = await asyncio.to_thread(repo.list_files, args.pattern, limit=args.limit)
        return ToolResult(files)


class ReadSourceArgs(_Args):
    path: str
    start: int = Field(default=1, ge=1)
    end: int | None = Field(default=None, ge=1)


class ReadSource(Tool[ReadSourceArgs]):
    name = "read_source"
    description = "Read a numbered line range of one repository file (window capped)."
    args_model = ReadSourceArgs

    async def execute(self, args: ReadSourceArgs, ctx: InvestigationContext) -> ToolResult:
        repo = _repository(ctx)
        text = await asyncio.to_thread(
            lambda: repo.read_lines(args.path, start=args.start, end=args.end)
        )
        return ToolResult({"path": args.path, "content": text})


class SearchSourceArgs(_Args):
    query: str = Field(min_length=2)
    glob: str = "**/*.py"
    regex: bool = False
    limit: int = Field(default=30, ge=1, le=100)


class SearchSource(Tool[SearchSourceArgs]):
    name = "search_source"
    description = "Search repository files for text (or a regex); returns path/line/text hits."
    args_model = SearchSourceArgs

    async def execute(self, args: SearchSourceArgs, ctx: InvestigationContext) -> ToolResult:
        repo = _repository(ctx)
        hits = await asyncio.to_thread(
            lambda: repo.search(args.query, glob=args.glob, regex=args.regex, limit=args.limit)
        )
        return ToolResult(hits)


class FindSymbolArgs(_Args):
    name: str = Field(min_length=1, max_length=100)


class FindSymbol(Tool[FindSymbolArgs]):
    name = "find_symbol"
    description = "Locate a Python function or class definition by name."
    args_model = FindSymbolArgs

    async def execute(self, args: FindSymbolArgs, ctx: InvestigationContext) -> ToolResult:
        repo = _repository(ctx)
        hits = await asyncio.to_thread(lambda: repo.find_symbol(args.name))
        return ToolResult(hits)


class GitHistoryArgs(_Args):
    path: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class GitHistory(Tool[GitHistoryArgs]):
    name = "git_history"
    description = "Recent commits (git log --oneline), optionally for one file."
    args_model = GitHistoryArgs

    async def execute(self, args: GitHistoryArgs, ctx: InvestigationContext) -> ToolResult:
        repo = _repository(ctx)
        log = await asyncio.to_thread(lambda: repo.git_history(args.path, limit=args.limit))
        return ToolResult({"log": log})


def code_tools() -> tuple[Tool[Any], ...]:
    return (ListRepoFiles(), ReadSource(), SearchSource(), FindSymbol(), GitHistory())

"""Deterministic validation of LLM-proposed patches.

The model writes the diff; Python decides whether it is acceptable. Every
path mentioned by the diff must resolve inside the configured repository
root, and the declared affected_files must cover the diff exactly -- a diff
that touches an undeclared file is rejected, not trimmed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from aegis.core.errors import InvestigationError

if TYPE_CHECKING:
    from aegis.inspection import RepositoryInspector
    from aegis.investigation.assessment import PatchProposal

_DIFF_PATH = re.compile(r"^(?:---|\+\+\+)\s+(?:[ab]/)?(?P<path>\S+)", re.MULTILINE)
_GIT_HEADER = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)", re.MULTILINE)


def extract_diff_paths(diff: str) -> set[str]:
    paths: set[str] = set()
    for match in _GIT_HEADER.finditer(diff):
        paths.add(match.group("a"))
        paths.add(match.group("b"))
    for match in _DIFF_PATH.finditer(diff):
        paths.add(match.group("path"))
    paths.discard("/dev/null")
    return paths


def validate_patch(proposal: PatchProposal, repository: RepositoryInspector) -> None:
    """Raises InvestigationError (or RepositoryAccessError) when the proposal
    is not acceptable; returns None when it is."""
    paths = extract_diff_paths(proposal.diff)
    if not paths:
        raise InvestigationError("patch diff contains no recognizable file paths")
    declared = set(proposal.affected_files)
    undeclared = paths - declared
    if undeclared:
        raise InvestigationError(
            f"patch touches files not declared in affected_files: {sorted(undeclared)}"
        )
    for path in paths | declared:
        # Raises RepositoryAccessError on absolute paths or traversal.
        repository.ensure_within_root(path)

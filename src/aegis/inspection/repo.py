"""Root-jailed, read-only repository access."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from aegis.core.errors import RepositoryAccessError

if TYPE_CHECKING:
    from collections.abc import Iterator

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
_MAX_FILE_BYTES = 1_000_000
_MAX_WINDOW = 400


class RepositoryInspector:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise RepositoryAccessError(f"repository root is not a directory: {root}")

    @property
    def root(self) -> Path:
        return self._root

    def ensure_within_root(self, relative: str) -> Path:
        """The jail: resolve and verify, or raise. Used by every accessor and
        by patch validation."""
        if not relative or relative.strip() == "":
            raise RepositoryAccessError("empty path")
        # Path.is_absolute() alone is platform-dependent: on Windows a
        # POSIX-absolute "/etc/passwd" reports False. Reject both notions.
        if relative.startswith(("/", "\\")) or Path(relative).is_absolute():
            raise RepositoryAccessError(f"absolute paths are not allowed: {relative!r}")
        resolved = (self._root / relative).resolve()
        if not resolved.is_relative_to(self._root):
            raise RepositoryAccessError(f"path escapes the repository root: {relative!r}")
        return resolved

    def list_files(self, pattern: str = "**/*.py", *, limit: int = 200) -> list[str]:
        matches: list[str] = []
        for path in sorted(self._root.glob(pattern)):
            if not path.is_file() or _skipped(path, self._root):
                continue
            matches.append(path.relative_to(self._root).as_posix())
            if len(matches) >= limit:
                break
        return matches

    def read_lines(self, relative: str, *, start: int = 1, end: int | None = None) -> str:
        """Numbered source lines; the window is capped so an agent cannot pull
        an entire large file into its context."""
        path = self.ensure_within_root(relative)
        if not path.is_file():
            raise RepositoryAccessError(f"not a file: {relative!r}")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise RepositoryAccessError(f"file too large to inspect: {relative!r}")
        if start < 1:
            raise RepositoryAccessError("start must be >= 1")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        stop = min(end if end is not None else start + _MAX_WINDOW - 1, start + _MAX_WINDOW - 1)
        window = lines[start - 1 : stop]
        return "\n".join(f"{start + i:5d} | {line}" for i, line in enumerate(window))

    def search(
        self, query: str, *, glob: str = "**/*.py", regex: bool = False, limit: int = 50
    ) -> list[dict[str, object]]:
        try:
            pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as exc:
            raise RepositoryAccessError(f"invalid search pattern: {exc}") from exc
        hits: list[dict[str, object]] = []
        for path in self._iter_files(glob):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    hits.append(
                        {
                            "path": path.relative_to(self._root).as_posix(),
                            "line": number,
                            "text": line.strip()[:200],
                        }
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    def find_symbol(self, name: str, *, limit: int = 20) -> list[dict[str, object]]:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise RepositoryAccessError(f"not a valid Python identifier: {name!r}")
        return self.search(rf"^\s*(?:async\s+def|def|class)\s+{name}\b", regex=True, limit=limit)

    def git_history(self, relative: str | None = None, *, limit: int = 10) -> str:
        """Recent commits (read-only ``git log``); errors are messages, never
        execution of repository content."""
        if not (self._root / ".git").exists():
            raise RepositoryAccessError("not a git repository")
        command = ["git", "-C", str(self._root), "log", "--oneline", f"-{limit}", "--no-color"]
        if relative is not None:
            command += ["--", self.ensure_within_root(relative).relative_to(self._root).as_posix()]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryAccessError(f"git unavailable: {exc}") from exc
        if completed.returncode != 0:
            raise RepositoryAccessError(f"git log failed: {completed.stderr.strip()[:200]}")
        return completed.stdout.strip()

    def _iter_files(self, glob: str) -> Iterator[Path]:
        for path in sorted(self._root.glob(glob)):
            if path.is_file() and not _skipped(path, self._root):
                yield path


def _skipped(path: Path, root: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.relative_to(root).parts)

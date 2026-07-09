"""Read-only source-repository inspection.

Security model: every path is resolved against an explicitly configured root
and must stay inside it after symlink resolution; absolute paths and traversal
are rejected; file size and line-window caps bound what an agent can pull into
context. Nothing here can execute repository code or write to it.
"""

from aegis.inspection.repo import RepositoryInspector

__all__ = ["RepositoryInspector"]

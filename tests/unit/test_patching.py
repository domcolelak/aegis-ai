from pathlib import Path

import pytest

from aegis.core.errors import InvestigationError, RepositoryAccessError
from aegis.inspection import RepositoryInspector
from aegis.investigation.assessment import PatchProposal
from aegis.investigation.patching import extract_diff_paths, validate_patch
from aegis.investigation.providers.demo import _DEMO_DIFF
from aegis.synthetic import materialize_repo


def proposal(diff: str, affected: list[str]) -> PatchProposal:
    return PatchProposal(
        reasoning="fix the leak",
        affected_files=affected,
        diff=diff,
        confidence=0.8,
    )


@pytest.fixture
def repo(tmp_path: Path) -> RepositoryInspector:
    return RepositoryInspector(materialize_repo(tmp_path))


def test_extracts_paths_from_unified_and_git_headers() -> None:
    diff = (
        "diff --git a/app/db.py b/app/db.py\n"
        "--- a/app/db.py\n"
        "+++ b/app/db.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "--- /dev/null\n"
        "+++ b/app/new_file.py\n"
        "@@ -0,0 +1 @@\n+created\n"
    )

    assert extract_diff_paths(diff) == {"app/db.py", "app/new_file.py"}


def test_demo_patch_is_valid_against_the_synthetic_repo(repo: RepositoryInspector) -> None:
    validate_patch(proposal(_DEMO_DIFF, ["app/services/booking_service.py"]), repo)


def test_rejects_diff_without_paths(repo: RepositoryInspector) -> None:
    with pytest.raises(InvestigationError, match="no recognizable file paths"):
        validate_patch(proposal("this is not a diff", ["app/db.py"]), repo)


def test_rejects_undeclared_files(repo: RepositoryInspector) -> None:
    with pytest.raises(InvestigationError, match="not declared"):
        validate_patch(proposal(_DEMO_DIFF, ["app/db.py"]), repo)


def test_rejects_paths_escaping_the_repository(repo: RepositoryInspector) -> None:
    escaping = "--- a/../outside.py\n+++ b/../outside.py\n@@ -1 +1 @@\n-x\n+y\n"

    with pytest.raises(RepositoryAccessError, match="escapes"):
        validate_patch(proposal(escaping, ["../outside.py"]), repo)


def test_rejects_absolute_paths(repo: RepositoryInspector) -> None:
    absolute = "--- a//etc/passwd\n+++ b//etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"

    with pytest.raises(RepositoryAccessError, match="absolute"):
        validate_patch(proposal(absolute, ["/etc/passwd"]), repo)

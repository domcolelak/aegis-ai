from pathlib import Path

import pytest

from aegis.core.errors import RepositoryAccessError
from aegis.inspection import RepositoryInspector
from aegis.synthetic import materialize_repo


@pytest.fixture
def repo(tmp_path: Path) -> RepositoryInspector:
    return RepositoryInspector(materialize_repo(tmp_path))


class TestJail:
    def test_traversal_is_rejected(self, repo: RepositoryInspector) -> None:
        for attempt in ("../secrets.txt", "app/../../etc/passwd", "..", ""):
            with pytest.raises(RepositoryAccessError):
                repo.ensure_within_root(attempt)

    def test_absolute_paths_are_rejected(self, repo: RepositoryInspector) -> None:
        with pytest.raises(RepositoryAccessError, match="absolute"):
            repo.ensure_within_root(str(Path.home()))

    def test_missing_root_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(RepositoryAccessError, match="not a directory"):
            RepositoryInspector(tmp_path / "does-not-exist")

    def test_oversized_files_are_refused(self, tmp_path: Path) -> None:
        root = materialize_repo(tmp_path)
        (root / "huge.py").write_bytes(b"x = 1\n" * 400_000)

        with pytest.raises(RepositoryAccessError, match="too large"):
            RepositoryInspector(root).read_lines("huge.py")


class TestReads:
    def test_list_files_skips_noise_dirs(self, tmp_path: Path) -> None:
        root = materialize_repo(tmp_path)
        (root / ".git").mkdir()
        (root / ".git" / "config.py").write_text("secret = 1", encoding="utf-8")

        files = RepositoryInspector(root).list_files()

        assert "app/services/booking_service.py" in files
        assert not any(".git" in path for path in files)

    def test_read_lines_windows_and_numbers(self, repo: RepositoryInspector) -> None:
        window = repo.read_lines("app/services/booking_service.py", start=8, end=9)

        assert window.splitlines()[0].startswith("    8 | ")
        assert "SessionLocal()" in window
        assert len(window.splitlines()) == 2

    def test_search_finds_the_leak_site(self, repo: RepositoryInspector) -> None:
        hits = repo.search("SessionLocal()")

        assert any(
            hit["path"] == "app/services/booking_service.py" and hit["line"] == 8 for hit in hits
        )

    def test_find_symbol_locates_definitions(self, repo: RepositoryInspector) -> None:
        hits = repo.find_symbol("create_booking")

        assert hits
        assert hits[0]["path"] == "app/services/booking_service.py"

    def test_find_symbol_rejects_non_identifiers(self, repo: RepositoryInspector) -> None:
        with pytest.raises(RepositoryAccessError, match="identifier"):
            repo.find_symbol("x; rm -rf /")

    def test_git_history_requires_a_git_repo(self, repo: RepositoryInspector) -> None:
        with pytest.raises(RepositoryAccessError, match="not a git repository"):
            repo.git_history()

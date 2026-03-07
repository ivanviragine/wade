"""Deterministic E2E contracts for `wade check` and `wade check-config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import _git, _run

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestCheckCommand:
    """Test `wade check` via CLI subprocess."""

    def test_check_in_main_checkout(self, e2e_repo: Path) -> None:
        """wade check on main -> exit 2, stdout contains IN_MAIN_CHECKOUT."""
        result = _run(["check"], cwd=e2e_repo)
        assert result.returncode == 2
        assert "IN_MAIN_CHECKOUT" in result.stdout

    def test_check_in_worktree(self, e2e_repo: Path) -> None:
        """wade check in a worktree -> exit 0, stdout contains IN_WORKTREE."""
        wt_dir = e2e_repo.parent / ".worktrees" / "42-test"
        _git(
            ["worktree", "add", "-b", "feat/42-test", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["check"], cwd=wt_dir)
        assert result.returncode == 0
        assert "IN_WORKTREE" in result.stdout

    def test_check_not_in_git(self, tmp_path: Path) -> None:
        """wade check outside git -> exit 1, stdout contains NOT_IN_GIT_REPO."""
        bare_dir = tmp_path / "not-a-repo"
        bare_dir.mkdir()

        result = _run(["check"], cwd=bare_dir)
        assert result.returncode == 1
        assert "NOT_IN_GIT_REPO" in result.stdout


class TestCheckConfigCommand:
    """Test `wade check-config` via CLI subprocess."""

    def test_valid_config(self, e2e_repo: Path) -> None:
        """wade check-config with valid .wade.yml -> exit 0."""
        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "VALID_CONFIG" in result.stdout

    def test_no_config(self, tmp_path: Path) -> None:
        """wade check-config without .wade.yml -> exit 1."""
        bare = tmp_path / "empty"
        bare.mkdir()
        _git(["init"], cwd=bare)
        _git(["config", "user.email", "test@test.com"], cwd=bare)
        _git(["config", "user.name", "Test"], cwd=bare)
        (bare / "x.txt").write_text("x\n", encoding="utf-8")
        _git(["add", "."], cwd=bare)
        _git(["commit", "-m", "init"], cwd=bare)

        result = _run(["check-config"], cwd=bare)
        assert result.returncode == 1
        assert "CONFIG_NOT_FOUND" in result.stdout

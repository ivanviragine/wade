"""Deterministic E2E contracts for session check and `wade check-config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import _git, _run

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestCheckCommand:
    """Test `wade implementation-session check` via CLI subprocess."""

    def test_check_in_main_checkout(self, e2e_repo: Path) -> None:
        """wade implementation-session check on main -> exit 2."""
        result = _run(["implementation-session", "check"], cwd=e2e_repo)
        assert result.returncode == 2
        assert "IN_MAIN_CHECKOUT" in result.stdout

    def test_check_in_worktree(self, e2e_repo: Path) -> None:
        """wade implementation-session check in worktree -> exit 0."""
        wt_dir = e2e_repo.parent / ".worktrees" / "42-test"
        _git(
            ["worktree", "add", "-b", "feat/42-test", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["implementation-session", "check"], cwd=wt_dir)
        assert result.returncode == 0
        assert "IN_WORKTREE" in result.stdout

    def test_check_not_in_git(self, tmp_path: Path) -> None:
        """wade implementation-session check outside git -> exit 1."""
        bare_dir = tmp_path / "not-a-repo"
        bare_dir.mkdir()

        result = _run(["implementation-session", "check"], cwd=bare_dir)
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

    def test_valid_knowledge_config(self, e2e_repo: Path) -> None:
        """wade check-config accepts a valid knowledge section."""
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8")
            + "\nknowledge:\n  enabled: true\n  path: docs/KNOWLEDGE.md\n",
            encoding="utf-8",
        )

        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "VALID_CONFIG" in result.stdout

    def test_invalid_knowledge_path_escape(self, e2e_repo: Path) -> None:
        """wade check-config rejects knowledge paths that escape the repo root."""
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8")
            + "\nknowledge:\n  enabled: true\n  path: ../KNOWLEDGE.md\n",
            encoding="utf-8",
        )

        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 3
        assert "INVALID_CONFIG" in result.stdout
        assert "knowledge.path: must be inside the project root" in result.stdout

    def test_valid_review_timeout_config(self, e2e_repo: Path) -> None:
        """wade check-config accepts the new per-command timeout field."""
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\nai:\n  review_plan:\n    timeout: 300\n",
            encoding="utf-8",
        )

        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "VALID_CONFIG" in result.stdout

    def test_invalid_review_timeout_rejected(self, e2e_repo: Path) -> None:
        """wade check-config rejects non-positive per-command timeout values."""
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\nai:\n  review_plan:\n    timeout: 0\n",
            encoding="utf-8",
        )

        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 3
        assert "INVALID_CONFIG" in result.stdout
        assert "ai.review_plan.timeout: must be a positive integer" in result.stdout

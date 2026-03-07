"""Deterministic E2E contracts for work sync and work list flows."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.e2e._support import (
    MAIN_BRANCH,
    MockGhCli,
    _assert_gh_called_with,
    _git,
    _parse_json_output,
    _run,
    _seed_mock_issue,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestWorkSyncCommand:
    """Test `wade work sync` via CLI subprocess."""

    def test_sync_clean_merge(self, e2e_repo: Path) -> None:
        """wade work sync when main has diverged - clean merge."""
        wt_dir = e2e_repo.parent / ".worktrees" / "50-feature"
        _git(
            ["worktree", "add", "-b", "feat/50-feature", str(wt_dir)],
            cwd=e2e_repo,
        )

        (wt_dir / "feature.py").write_text("# new feature\n", encoding="utf-8")
        _git(["add", "."], cwd=wt_dir)
        _git(["commit", "-m", "feat: new feature"], cwd=wt_dir)

        _git(["checkout", MAIN_BRANCH], cwd=e2e_repo)
        (e2e_repo / "docs.md").write_text("# Docs\n", encoding="utf-8")
        _git(["add", "."], cwd=e2e_repo)
        _git(["commit", "-m", "docs: add docs"], cwd=e2e_repo)

        result = _run(["work", "sync"], cwd=wt_dir)

        assert result.returncode == 0
        assert (wt_dir / "docs.md").exists()
        assert (wt_dir / "feature.py").exists()

    def test_sync_already_up_to_date(self, e2e_repo: Path) -> None:
        """wade work sync when already up to date - no-op."""
        wt_dir = e2e_repo.parent / ".worktrees" / "51-uptodate"
        _git(
            ["worktree", "add", "-b", "feat/51-uptodate", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "sync"], cwd=wt_dir)
        assert result.returncode == 0
        assert "already up to date" in result.stdout.lower()

    def test_sync_conflict_exit_code(self, e2e_repo: Path) -> None:
        """wade work sync with merge conflict -> exit 2."""
        wt_dir = e2e_repo.parent / ".worktrees" / "60-conflict"
        _git(
            ["worktree", "add", "-b", "feat/60-conflict", str(wt_dir)],
            cwd=e2e_repo,
        )

        (wt_dir / "README.md").write_text("Feature version\n", encoding="utf-8")
        _git(["add", "."], cwd=wt_dir)
        _git(["commit", "-m", "feat: update readme"], cwd=wt_dir)

        _git(["checkout", MAIN_BRANCH], cwd=e2e_repo)
        (e2e_repo / "README.md").write_text("Main version\n", encoding="utf-8")
        _git(["add", "."], cwd=e2e_repo)
        _git(["commit", "-m", "docs: update readme"], cwd=e2e_repo)

        result = _run(["work", "sync"], cwd=wt_dir)
        assert result.returncode == 2

        subprocess.run(["git", "merge", "--abort"], cwd=wt_dir, capture_output=True)

    def test_sync_json_output(self, e2e_repo: Path) -> None:
        """wade work sync --json emits structured events."""
        wt_dir = e2e_repo.parent / ".worktrees" / "52-json"
        _git(
            ["worktree", "add", "-b", "feat/52-json", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "sync", "--json"], cwd=wt_dir)
        assert result.returncode == 0

        non_empty_lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert non_empty_lines, "Expected JSON events, got empty stdout"
        for line in non_empty_lines:
            assert line.lstrip().startswith("{"), (
                f"Non-JSON line leaked into --json output: {line!r}\nFull stdout: {result.stdout!r}"
            )

        for line in non_empty_lines:
            parsed = json.loads(line)
            assert "event" in parsed

    def test_sync_from_main_rejected(self, e2e_repo: Path) -> None:
        """wade work sync from main branch -> exit 4 (preflight failure)."""
        result = _run(["work", "sync"], cwd=e2e_repo)
        assert result.returncode == 4


class TestWorkListCommand:
    """Test `wade work list` via CLI subprocess."""

    def test_list_empty(self, e2e_repo: Path) -> None:
        """wade work list with no worktrees should print explicit empty state."""
        result = _run(["work", "list"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "No active wade worktrees found." in result.stdout

    def test_list_with_worktrees(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        """wade work list with worktrees should query issue state via gh."""
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=10,
            title="Auth task",
            issue_state="OPEN",
        )
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=11,
            title="DB task",
            issue_state="OPEN",
        )
        for num, slug in [("10", "auth"), ("11", "db")]:
            wt_dir = e2e_repo.parent / ".worktrees" / f"{num}-{slug}"
            _git(
                ["worktree", "add", "-b", f"feat/{num}-{slug}", str(wt_dir)],
                cwd=e2e_repo,
            )

        result = _run(["work", "list"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "10" in result.stdout or "auth" in result.stdout, (
            f"Expected worktree '10-auth' in output, got: {result.stdout!r}"
        )
        assert "11" in result.stdout or "db" in result.stdout, (
            f"Expected worktree '11-db' in output, got: {result.stdout!r}"
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "10"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "11"],
        )

    def test_list_json(self, e2e_repo: Path) -> None:
        """wade work list --json outputs strict JSON with required keys."""
        wt_dir = e2e_repo.parent / ".worktrees" / "20-test"
        _git(
            ["worktree", "add", "-b", "feat/20-test", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "list", "--json"], cwd=e2e_repo)
        assert result.returncode == 0
        parsed = _parse_json_output(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1
        first = parsed[0]
        assert isinstance(first, dict)
        assert {"path", "branch", "issue", "staleness", "commits_ahead"}.issubset(first)

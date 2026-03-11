"""Deterministic E2E contracts for smart-start routing workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _count_gh_calls,
    _git,
    _init_origin_remote,
    _run,
    _seed_mock_issue,
    _seed_mock_pr,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestSmartStartCommand:
    """Test `wade smart-start` and numeric-entry routing contracts."""

    def test_smart_start_cd_bootstraps_when_no_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """smart-start --cd should route to implement flow when no PR exists."""
        from wade.git.branch import make_branch_name

        issue_number = 61
        issue_title = "Smart start should route to implement"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Route to implementation flow\n",
        )
        _init_origin_remote(e2e_repo)

        branch_name = make_branch_name("feat", issue_number, issue_title)
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["smart-start", str(issue_number), "--cd"], cwd=e2e_repo)

        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        assert expected_worktree.is_dir()
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--head", branch_name, "--draft"],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 1

    def test_numeric_entrypoint_routes_to_smart_start(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """`wade <issue> --cd` should rewrite through smart-start and create worktree."""
        from wade.git.branch import make_branch_name

        issue_number = 62
        issue_title = "Numeric entrypoint should route to smart start"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Verify numeric routing\n",
        )
        _init_origin_remote(e2e_repo)

        branch_name = make_branch_name("feat", issue_number, issue_title)
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run([str(issue_number), "--cd"], cwd=e2e_repo)

        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        assert expected_worktree.is_dir()
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--head", branch_name, "--draft"],
        )

    def test_smart_start_fails_for_unknown_issue_without_pr_side_effects(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """smart-start should fail for unknown issue numbers without creating PRs."""
        result = _run(["smart-start", "999"], cwd=e2e_repo)

        assert result.returncode != 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "999"],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 0

    def test_smart_start_merged_pr_short_circuits(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """smart-start should not run implement flow when the issue PR is already merged."""
        from wade.git.branch import make_branch_name

        issue_number = 63
        issue_title = "Merged PR should short-circuit smart start"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Verify merged PR path\n",
        )

        branch_name = make_branch_name("feat", issue_number, issue_title)
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )
        _seed_mock_pr(
            mock_gh_cli["state_file"],
            pr_number=7,
            head_branch=branch_name,
            title=issue_title,
            pr_state="MERGED",
            is_draft=False,
        )

        result = _run(["smart-start", str(issue_number)], cwd=e2e_repo)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "already merged" in output
        assert not expected_worktree.exists()
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "view", branch_name],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 0

    def test_smart_start_cd_reuses_existing_draft_pr_without_duplicate_create(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """smart-start --cd with an existing draft PR should not create another PR."""
        import json

        from wade.git.branch import make_branch_name

        issue_number = 64
        issue_title = "Existing draft PR should be reused"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Reuse existing PR path\n",
        )
        _init_origin_remote(e2e_repo)

        branch_name = make_branch_name("feat", issue_number, issue_title)
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )
        # Existing PR flows assume the remote head branch already exists.
        _git(["checkout", "-b", branch_name], cwd=e2e_repo)
        _git(["push", "-u", "origin", branch_name], cwd=e2e_repo)
        _git(["checkout", "main"], cwd=e2e_repo)
        _seed_mock_pr(
            mock_gh_cli["state_file"],
            pr_number=8,
            head_branch=branch_name,
            title=issue_title,
            pr_state="OPEN",
            is_draft=True,
        )

        result = _run(["smart-start", str(issue_number), "--cd"], cwd=e2e_repo)

        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        assert expected_worktree.is_dir()
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "view", branch_name],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 0

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        prs = state_data.get("prs", {})
        assert isinstance(prs, dict)
        assert len(prs) == 1
        existing = prs.get("8")
        assert isinstance(existing, dict)
        assert existing.get("head") == branch_name

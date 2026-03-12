"""Deterministic E2E contracts for implement and done flows."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _count_gh_calls,
    _find_mock_pr_number_by_head,
    _git,
    _init_origin_remote,
    _remote_has_branch,
    _run,
    _seed_mock_issue,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestImplementTaskCommand:
    """Test `wade implement` via CLI subprocess."""

    def test_implement_task_cd_bootstraps_worktree_and_draft_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --cd should create worktree, PLAN.md, and bootstrap draft PR."""
        issue_number = 42
        issue_title = "Add deterministic contract coverage"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\\n- Add E2E contract tests\\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)

        branch_name = "feat/42-add-deterministic-contract-coverage"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        assert expected_worktree.is_dir()
        assert _remote_has_branch(origin_repo, branch_name)

        plan_file = expected_worktree / "PLAN.md"
        assert plan_file.is_file()
        plan_text = plan_file.read_text(encoding="utf-8")
        assert f"Issue #{issue_number}" in plan_text
        assert issue_title in plan_text

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--head", branch_name, "--draft"],
        )
        assert (
            _count_gh_calls(
                mock_gh_cli["log_file"],
                ["pr", "create", "--head", branch_name],
            )
            == 1
        )

    def test_implement_task_fails_for_unknown_issue_without_pr_side_effects(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement should fail fast for unknown issues and avoid PR creation."""
        result = _run(["implement", "999", "--cd"], cwd=e2e_repo)

        assert result.returncode != 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "999"],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 0

    def test_implement_task_cd_runs_setup_worktree_hook(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --cd should execute configured post-worktree hook."""
        issue_number = 44
        issue_title = "Run setup hook from implement"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\\n- Validate setup-worktree hook\\n",
        )

        config_path = e2e_repo / ".wade.yml"
        original_config = config_path.read_text(encoding="utf-8")
        config_path.write_text(
            original_config
            + "\n"
            + "hooks:\n"
            + "  post_worktree_create: scripts/setup-worktree.sh\n",
            encoding="utf-8",
        )

        scripts_dir = e2e_repo / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        hook_script = scripts_dir / "setup-worktree.sh"
        hook_script.write_text(
            "#!/usr/bin/env sh\nset -eu\necho hook-ran > .hook-ran\n",
            encoding="utf-8",
        )
        hook_script.chmod(0o755)

        _git(["add", ".wade.yml", "scripts/setup-worktree.sh"], cwd=e2e_repo)
        _git(["commit", "-m", "test: add setup-worktree hook"], cwd=e2e_repo)

        _init_origin_remote(e2e_repo)
        branch_name = "feat/44-run-setup-hook-from-implement"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        hook_marker = expected_worktree / ".hook-ran"
        assert hook_marker.is_file()
        assert hook_marker.read_text(encoding="utf-8").strip() == "hook-ran"


class TestWorkDoneCommand:
    """Test `wade implementation-session done` via CLI subprocess."""

    def test_work_done_updates_existing_draft_pr_and_pushes_branch(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implementation-session done should push branch and update draft PR path."""
        issue_number = 43
        issue_title = "Finalize work done command contract"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\\n- Finish implementation\\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)

        branch_name = "feat/43-finalize-work-done-command-contract"

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Implemented feature and validated behavior.\\n", encoding="utf-8"
        )
        (worktree_path / "implementation.txt").write_text("work done contract\\n", encoding="utf-8")
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", f"feat: complete #{issue_number}"], cwd=worktree_path)
        assert _git(["status", "--porcelain"], cwd=worktree_path).stdout.strip() == ""

        result = _run(["implementation-session", "done"], cwd=worktree_path)
        assert result.returncode == 0
        assert _remote_has_branch(origin_repo, branch_name)
        pr_number = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "edit", pr_number, "--body"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "ready", pr_number],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", str(issue_number), "--remove-label", "in-progress"],
        )
        assert (
            _count_gh_calls(
                mock_gh_cli["log_file"],
                ["pr", "create", "--head", branch_name],
            )
            == 1
        )

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghaiw.models.config import AIConfig, ProjectConfig, ProjectSettings
from ghaiw.models.task import Task
from ghaiw.models.work import MergeStrategy
from ghaiw.services.work_service import _post_work_lifecycle, start


def _config(strategy: MergeStrategy) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(main_branch="main", merge_strategy=strategy),
        ai=AIConfig(default_tool="claude"),
    )


@patch("ghaiw.services.work_service.subprocess.run")
@patch("ghaiw.services.work_service.git_worktree.prune_worktrees")
@patch("ghaiw.services.work_service.git_worktree.remove_worktree")
@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
def test_pr_strategy_prompts_merge_on_existing_pr(
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_pr_for_branch.return_value = {"number": 99, "url": "https://example/pr/99"}
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    _post_work_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    assert mock_confirm.called
    confirm_msg = mock_confirm.call_args[0][0]
    assert "merge" in confirm_msg.lower()
    assert "99" in confirm_msg
    # Worktree is removed BEFORE merge so --delete-branch can succeed
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    mock_run.assert_any_call(["git", "pull", "--quiet"], cwd=repo_root)


@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm")
def test_pr_strategy_no_pr_warns_and_returns(
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_pr_for_branch.return_value = None
    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    mock_confirm.assert_not_called()
    mock_merge_pr.assert_not_called()


@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm", return_value=False)
def test_pr_strategy_user_declines_merge(
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_pr_for_branch.return_value = {"number": 99, "url": "https://example/pr/99"}
    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    mock_confirm.assert_called_once()
    mock_merge_pr.assert_not_called()


@patch("ghaiw.services.work_service.subprocess.run")
@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
def test_pr_strategy_merge_failure_handles_gracefully(
    _mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_pr_for_branch.return_value = {"number": 99, "url": "https://example/pr/99"}
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    mock_run.assert_any_call(
        ["git", "push", "origin", "--delete", "feat/42-test"],
        check=True,
        capture_output=True,
        cwd=tmp_path / "repo",
    )


@patch("ghaiw.services.work_service.subprocess.run")
@patch("ghaiw.services.work_service.git_worktree.prune_worktrees")
@patch("ghaiw.services.work_service.git_worktree.remove_worktree")
@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
def test_pr_strategy_cleanup_and_pull_after_merge(
    _mock_confirm: MagicMock,
    _mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    provider.get_pr_for_branch.return_value = {"number": 99, "url": "https://example/pr/99"}
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"

    _post_work_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    # Worktree is removed before merge; no separate cleanup call needed after
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_run.assert_any_call(["git", "pull", "--quiet"], cwd=repo_root)


@patch("ghaiw.services.work_service.prompts.confirm", return_value=False)
@patch("ghaiw.services.work_service.subprocess.run")
def test_direct_strategy_zero_ahead_offers_delete(
    mock_run: MagicMock,
    mock_confirm: MagicMock,
    tmp_path: Path,
) -> None:
    mock_run.return_value = MagicMock(stdout="0", returncode=0)
    provider = MagicMock()

    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.DIRECT),
        provider,
    )

    assert mock_confirm.called
    assert "delete" in mock_confirm.call_args[0][0].lower()


@patch("ghaiw.services.work_service.prompts.select", return_value=2)
@patch("ghaiw.services.work_service.subprocess.run")
def test_direct_strategy_commits_ahead_shows_menu(
    mock_run: MagicMock,
    mock_select: MagicMock,
    tmp_path: Path,
) -> None:
    mock_run.return_value = MagicMock(stdout="3", returncode=0)
    provider = MagicMock()

    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.DIRECT),
        provider,
    )

    choices = mock_select.call_args[0][1]
    joined = " ".join(choices)
    assert "Merge" in joined
    assert "close" in joined
    assert "Skip" in joined


@patch("ghaiw.services.work_service._cleanup_worktree")
@patch("ghaiw.services.work_service.prompts.select", return_value=1)
@patch("ghaiw.services.work_service.subprocess.run")
def test_direct_strategy_merge_and_close(
    mock_run: MagicMock,
    _mock_select: MagicMock,
    mock_cleanup: MagicMock,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    mock_run.side_effect = [
        MagicMock(stdout="3", returncode=0),
        MagicMock(returncode=0),
        MagicMock(returncode=0),
        MagicMock(returncode=0),
    ]
    provider = MagicMock()

    _post_work_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.DIRECT), provider
    )

    mock_run.assert_any_call(
        ["git", "merge", "--squash", "feat/42-test"],
        check=True,
        capture_output=True,
        cwd=repo_root,
    )
    mock_cleanup.assert_called_once_with(repo_root, wt_path, "main")
    provider.close_task.assert_called_once_with("42")


@patch("ghaiw.services.work_service.write_plan_md")
@patch("ghaiw.services.work_service._post_work_lifecycle")
@patch("ghaiw.services.work_service.launch_in_new_terminal", return_value=True)
@patch("ghaiw.services.work_service.AbstractAITool.get")
@patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=False)
@patch("ghaiw.services.work_service.copy_to_clipboard")
@patch("ghaiw.services.work_service.add_in_progress_label")
@patch("ghaiw.services.work_service.bootstrap_worktree")
@patch("ghaiw.services.work_service.git_worktree.list_worktrees", return_value=[])
@patch("ghaiw.services.work_service.git_worktree.create_worktree")
@patch("ghaiw.services.work_service.git_repo.get_repo_root")
@patch("ghaiw.services.work_service._resolve_target")
@patch("ghaiw.services.work_service.get_provider")
@patch("ghaiw.services.work_service.load_config")
def test_lifecycle_skipped_in_detach_mode(
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_resolve_target: MagicMock,
    mock_get_repo_root: MagicMock,
    _mock_create_worktree: MagicMock,
    _mock_list_worktrees: MagicMock,
    _mock_bootstrap_worktree: MagicMock,
    _mock_add_in_progress: MagicMock,
    _mock_clipboard: MagicMock,
    _mock_inside_ai: MagicMock,
    mock_get_adapter: MagicMock,
    _mock_launch_terminal: MagicMock,
    mock_lifecycle: MagicMock,
    _mock_write_plan_md: MagicMock,
    tmp_path: Path,
) -> None:
    mock_load_config.return_value = _config(MergeStrategy.PR)
    mock_get_repo_root.return_value = tmp_path
    mock_resolve_target.return_value = Task(id="42", title="Test")
    adapter = MagicMock()
    adapter.build_launch_command.return_value = ["claude"]
    mock_get_adapter.return_value = adapter

    result = start("42", ai_tool="claude", project_root=tmp_path, detach=True)

    assert result is True
    mock_lifecycle.assert_not_called()


@patch("ghaiw.services.work_service.write_plan_md")
@patch("ghaiw.services.work_service._post_work_lifecycle")
@patch("ghaiw.services.work_service.add_worked_by_labels")
@patch("ghaiw.services.work_service._post_exit_capture")
@patch("ghaiw.services.work_service.stop_title_keeper")
@patch("ghaiw.services.work_service.start_title_keeper")
@patch("ghaiw.services.work_service.set_terminal_title")
@patch("ghaiw.services.work_service.compose_work_title", return_value="title")
@patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=False)
@patch("ghaiw.services.work_service.copy_to_clipboard")
@patch("ghaiw.services.work_service.add_in_progress_label")
@patch("ghaiw.services.work_service.bootstrap_worktree")
@patch("ghaiw.services.work_service.git_worktree.list_worktrees", return_value=[])
@patch("ghaiw.services.work_service.git_worktree.create_worktree")
@patch("ghaiw.services.work_service.git_repo.get_repo_root")
@patch("ghaiw.services.work_service._resolve_target")
@patch("ghaiw.services.work_service.AbstractAITool.get")
@patch("ghaiw.services.work_service.get_provider")
@patch("ghaiw.services.work_service.load_config")
def test_lifecycle_runs_after_ai_crash(
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_get_adapter: MagicMock,
    mock_resolve_target: MagicMock,
    mock_get_repo_root: MagicMock,
    _mock_create_worktree: MagicMock,
    _mock_list_worktrees: MagicMock,
    _mock_bootstrap_worktree: MagicMock,
    _mock_add_in_progress: MagicMock,
    _mock_clipboard: MagicMock,
    _mock_inside_ai: MagicMock,
    _mock_compose_title: MagicMock,
    _mock_set_title: MagicMock,
    _mock_start_keeper: MagicMock,
    _mock_stop_keeper: MagicMock,
    _mock_post_exit_capture: MagicMock,
    _mock_add_worked_by: MagicMock,
    mock_lifecycle: MagicMock,
    _mock_write_plan_md: MagicMock,
    tmp_path: Path,
) -> None:
    mock_load_config.return_value = _config(MergeStrategy.PR)
    mock_get_repo_root.return_value = tmp_path
    mock_resolve_target.return_value = Task(id="42", title="Test")
    adapter = MagicMock()
    adapter.is_model_compatible.return_value = True
    adapter.launch.side_effect = RuntimeError("ai crashed")
    mock_get_adapter.return_value = adapter

    result = start("42", ai_tool="claude", project_root=tmp_path, detach=False)

    assert result is True
    mock_lifecycle.assert_called_once()

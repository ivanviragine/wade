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
@patch("ghaiw.services.work_service.git_repo.is_clean", return_value=True)
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
@patch(
    "ghaiw.services.work_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_prompts_merge_on_existing_pr(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check
    _post_work_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    assert mock_confirm.called
    confirm_msg = mock_confirm.call_args[0][0]
    assert "merge" in confirm_msg.lower()
    assert "99" in confirm_msg
    # Worktree is removed AFTER successful merge
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_run.assert_any_call(["git", "pull", "--quiet"], cwd=repo_root)


@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.prompts.confirm")
@patch("ghaiw.services.work_service.git_pr.get_pr_for_branch", return_value=None)
def test_pr_strategy_no_pr_warns_and_returns(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
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
@patch(
    "ghaiw.services.work_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_user_declines_merge(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
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
@patch(
    "ghaiw.services.work_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_merge_failure_preserves_branch(
    _mock_get_pr: MagicMock,
    _mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    # Branch should NOT be deleted on merge failure
    for call in mock_run.call_args_list:
        args = call[0][0] if call[0] else call[1].get("args", [])
        assert not (isinstance(args, list) and "push" in args and "--delete" in args), (
            "Branch should be preserved on merge failure"
        )


@patch("ghaiw.services.work_service.subprocess.run")
@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.git_repo.is_clean", return_value=True)
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
@patch(
    "ghaiw.services.work_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_merge_failure_restores_branch(
    _mock_get_pr: MagicMock,
    _mock_confirm: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    """On merge failure, HEAD should be restored from detached state to the branch."""
    provider = MagicMock()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    _post_work_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        wt_path,
        _config(MergeStrategy.PR),
        provider,
    )

    # Should have called git checkout feat/42-test to restore branch
    checkout_calls = [
        c for c in mock_run.call_args_list if c[0][0] == ["git", "checkout", "feat/42-test"]
    ]
    assert len(checkout_calls) == 1, (
        f"Expected branch restore checkout, got calls: {mock_run.call_args_list}"
    )


@patch("ghaiw.services.work_service.subprocess.run")
@patch("ghaiw.services.work_service.git_worktree.prune_worktrees")
@patch("ghaiw.services.work_service.git_worktree.remove_worktree")
@patch("ghaiw.services.work_service.git_pr.merge_pr")
@patch("ghaiw.services.work_service.git_repo.is_clean", return_value=True)
@patch("ghaiw.services.work_service.prompts.confirm", return_value=True)
@patch(
    "ghaiw.services.work_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_cleanup_and_pull_after_merge(
    _mock_get_pr: MagicMock,
    _mock_confirm: MagicMock,
    _mock_is_clean: MagicMock,
    _mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check

    _post_work_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    # Worktree is removed AFTER successful merge
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_run.assert_any_call(["git", "pull", "--quiet"], cwd=repo_root)


@patch("ghaiw.services.work_service.prompts.confirm", return_value=False)
@patch("ghaiw.services.work_service.git_branch.commits_ahead", return_value=0)
def test_direct_strategy_zero_ahead_offers_delete(
    _mock_ahead: MagicMock,
    mock_confirm: MagicMock,
    tmp_path: Path,
) -> None:
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
@patch("ghaiw.services.work_service.git_branch.commits_ahead", return_value=3)
def test_direct_strategy_commits_ahead_shows_menu(
    _mock_ahead: MagicMock,
    mock_select: MagicMock,
    tmp_path: Path,
) -> None:
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
@patch("ghaiw.services.work_service.git_branch.commits_ahead", return_value=3)
def test_direct_strategy_merge_and_close(
    _mock_ahead: MagicMock,
    mock_run: MagicMock,
    _mock_select: MagicMock,
    mock_cleanup: MagicMock,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
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
@patch("ghaiw.services.work_service._detect_ai_cli_env", return_value=None)
@patch("ghaiw.services.work_service.add_in_progress_label")
@patch("ghaiw.services.work_service.bootstrap_worktree")
@patch("ghaiw.services.work_service.git_worktree.list_worktrees", return_value=[])
@patch("ghaiw.services.work_service.git_worktree.create_worktree")
@patch("ghaiw.services.work_service.git_repo.get_repo_root")
@patch("ghaiw.services.work_service._resolve_target")
@patch("ghaiw.services.work_service.get_provider")
@patch("ghaiw.services.work_service.load_config")
@patch("ghaiw.services.work_service.git_pr.get_pr_for_branch", return_value=None)
@patch(
    "ghaiw.services.work_service.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("ghaiw.services.work_service.prompts")
def test_lifecycle_skipped_in_detach_mode(
    mock_prompts: MagicMock,
    _mock_bootstrap_pr: MagicMock,
    _mock_get_pr: MagicMock,
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_resolve_target: MagicMock,
    mock_get_repo_root: MagicMock,
    _mock_create_worktree: MagicMock,
    _mock_list_worktrees: MagicMock,
    _mock_bootstrap_worktree: MagicMock,
    _mock_add_in_progress: MagicMock,
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
    mock_prompts.is_tty.return_value = False
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
@patch("ghaiw.services.work_service._detect_ai_cli_env", return_value=None)
@patch("ghaiw.services.work_service.add_in_progress_label")
@patch("ghaiw.services.work_service.bootstrap_worktree")
@patch("ghaiw.services.work_service.git_worktree.list_worktrees", return_value=[])
@patch("ghaiw.services.work_service.git_worktree.create_worktree")
@patch("ghaiw.services.work_service.git_repo.get_repo_root")
@patch("ghaiw.services.work_service._resolve_target")
@patch("ghaiw.services.work_service.AbstractAITool.get")
@patch("ghaiw.services.work_service.get_provider")
@patch("ghaiw.services.work_service.load_config")
@patch("ghaiw.services.work_service.git_pr.get_pr_for_branch", return_value=None)
@patch(
    "ghaiw.services.work_service.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("ghaiw.services.work_service.prompts")
def test_lifecycle_skipped_after_ai_crash(
    mock_prompts: MagicMock,
    _mock_bootstrap_pr: MagicMock,
    _mock_get_pr: MagicMock,
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_get_adapter: MagicMock,
    mock_resolve_target: MagicMock,
    mock_get_repo_root: MagicMock,
    _mock_create_worktree: MagicMock,
    _mock_list_worktrees: MagicMock,
    _mock_bootstrap_worktree: MagicMock,
    _mock_add_in_progress: MagicMock,
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
    mock_prompts.is_tty.return_value = False
    adapter = MagicMock()
    adapter.is_model_compatible.return_value = True
    adapter.launch.side_effect = RuntimeError("ai crashed")
    mock_get_adapter.return_value = adapter

    result = start("42", ai_tool="claude", project_root=tmp_path, detach=False)

    assert result is True
    mock_lifecycle.assert_not_called()

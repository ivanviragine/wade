from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.config import AIConfig, ProjectConfig, ProjectSettings
from wade.models.session import MergeStrategy
from wade.models.task import Task
from wade.services.implementation_service import _post_implementation_lifecycle, start

_PULL_FF = "wade.services.implementation_service.git_repo.pull_ff_only"
_CHECKOUT = "wade.services.implementation_service.git_repo.checkout"
_CHECKOUT_DETACH = "wade.services.implementation_service.git_repo.checkout_detach"
_MERGE_SQUASH = "wade.services.implementation_service.git_repo.merge_squash"
_COMMIT_NO_EDIT = "wade.services.implementation_service.git_repo.commit_no_edit"
_PUSH = "wade.services.implementation_service.git_repo.push"


def _config(strategy: MergeStrategy) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(main_branch="main", merge_strategy=strategy),
        ai=AIConfig(default_tool="claude"),
    )


@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.prompts.select", return_value=0)
@patch("wade.services.implementation_service.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.webbrowser.open")
@patch(
    "wade.services.implementation_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_prompts_merge_on_existing_pr(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    mock_confirm: MagicMock,
    mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_pull_ff: MagicMock,
    tmp_path: Path,
) -> None:
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check
    _post_implementation_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    # select is called with "Merge PR" / "Wait for reviews" — user picks 0 (Merge PR)
    mock_select.assert_called_once()
    select_items = mock_select.call_args[0][1]
    assert "Merge PR" in select_items
    assert "Wait for reviews" in select_items
    # Worktree is removed AFTER successful merge
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_pull_ff.assert_called_once_with(repo_root)


@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.prompts.confirm")
@patch("wade.services.implementation_service.git_pr.get_pr_for_branch", return_value=None)
def test_pr_strategy_no_pr_warns_and_returns(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    _post_implementation_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    mock_confirm.assert_not_called()
    mock_merge_pr.assert_not_called()


@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.prompts.select", return_value=1)
@patch("wade.services.implementation_service.prompts.confirm", return_value=False)
@patch(
    "wade.services.implementation_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_user_declines_merge(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    mock_select: MagicMock,
    mock_merge_pr: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    _post_implementation_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    # select returns 1 (Wait for reviews) — merge should NOT be called
    mock_select.assert_called_once()
    mock_merge_pr.assert_not_called()


@patch(_CHECKOUT)
@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.prompts.select", return_value=0)
@patch("wade.services.implementation_service.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.webbrowser.open")
@patch(
    "wade.services.implementation_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_merge_failure_preserves_branch(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_checkout: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    _post_implementation_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.PR),
        provider,
    )

    # merge_pr raised, so no push or delete should have happened
    mock_merge_pr.assert_called_once()


@patch(_CHECKOUT)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.prompts.select", return_value=0)
@patch("wade.services.implementation_service.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.webbrowser.open")
@patch(
    "wade.services.implementation_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_merge_failure_restores_branch(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_checkout: MagicMock,
    tmp_path: Path,
) -> None:
    """On merge failure, HEAD should be restored from detached state to the branch."""
    provider = MagicMock()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    _post_implementation_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        wt_path,
        _config(MergeStrategy.PR),
        provider,
    )

    # Should have called checkout to restore branch after merge failure
    mock_checkout.assert_called_once_with(wt_path, "feat/42-test")


@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.git_pr.merge_pr")
@patch("wade.services.implementation_service.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.webbrowser.open")
@patch(
    "wade.services.implementation_service.git_pr.get_pr_for_branch",
    return_value={"number": 99, "url": "https://example/pr/99"},
)
def test_pr_strategy_cleanup_and_pull_after_merge(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_is_clean: MagicMock,
    _mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_pull_ff: MagicMock,
    tmp_path: Path,
) -> None:
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check

    _post_implementation_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.PR), provider
    )

    # Worktree is removed AFTER successful merge
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_pull_ff.assert_called_once_with(repo_root)


@patch("wade.services.implementation_service.prompts.confirm", return_value=False)
@patch("wade.services.implementation_service.git_branch.commits_ahead", return_value=0)
def test_direct_strategy_zero_ahead_offers_delete(
    _mock_ahead: MagicMock,
    mock_confirm: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()

    _post_implementation_lifecycle(
        tmp_path / "repo",
        "feat/42-test",
        42,
        tmp_path / "wt",
        _config(MergeStrategy.DIRECT),
        provider,
    )

    assert mock_confirm.called
    assert "delete" in mock_confirm.call_args[0][0].lower()


@patch("wade.services.implementation_service.prompts.select", return_value=2)
@patch("wade.services.implementation_service.git_branch.commits_ahead", return_value=3)
def test_direct_strategy_commits_ahead_shows_menu(
    _mock_ahead: MagicMock,
    mock_select: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()

    _post_implementation_lifecycle(
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


@patch("wade.services.implementation_service._cleanup_worktree")
@patch("wade.services.implementation_service.prompts.select", return_value=1)
@patch(_PUSH)
@patch(_COMMIT_NO_EDIT)
@patch(_MERGE_SQUASH)
@patch("wade.services.implementation_service.git_branch.commits_ahead", return_value=3)
def test_direct_strategy_merge_and_close(
    _mock_ahead: MagicMock,
    mock_merge_squash: MagicMock,
    _mock_commit: MagicMock,
    _mock_push: MagicMock,
    _mock_select: MagicMock,
    mock_cleanup: MagicMock,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    provider = MagicMock()

    _post_implementation_lifecycle(
        repo_root, "feat/42-test", 42, wt_path, _config(MergeStrategy.DIRECT), provider
    )

    mock_merge_squash.assert_called_once_with(repo_root, "feat/42-test")
    mock_cleanup.assert_called_once_with(repo_root, wt_path, "main")
    provider.close_task.assert_called_once_with("42")


@patch("wade.services.implementation_service.write_plan_md")
@patch("wade.services.implementation_service._post_implementation_lifecycle")
@patch("wade.services.implementation_service.launch_in_new_terminal", return_value=True)
@patch("wade.services.implementation_service.AbstractAITool.get")
@patch("wade.services.implementation_service._detect_ai_cli_env", return_value=None)
@patch("wade.services.implementation_service.add_in_progress_label")
@patch("wade.services.implementation_service.bootstrap_worktree")
@patch("wade.services.implementation_service.git_worktree.list_worktrees", return_value=[])
@patch("wade.services.implementation_service.git_worktree.create_worktree")
@patch("wade.services.implementation_service.git_repo.get_repo_root")
@patch("wade.services.implementation_service._resolve_task_target")
@patch("wade.services.implementation_service.get_provider")
@patch("wade.services.implementation_service.load_config")
@patch("wade.services.implementation_service.git_pr.get_pr_for_branch", return_value=None)
@patch(
    "wade.services.implementation_service.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("wade.services.implementation_service.prompts")
def test_lifecycle_skipped_in_detach_mode(
    mock_prompts: MagicMock,
    _mock_bootstrap_pr: MagicMock,
    _mock_get_pr: MagicMock,
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_resolve_task_target: MagicMock,
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
    mock_resolve_task_target.return_value = Task(id="42", title="Test")
    mock_prompts.is_tty.return_value = False
    adapter = MagicMock()
    adapter.build_launch_command.return_value = ["claude"]
    mock_get_adapter.return_value = adapter

    result = start("42", ai_tool="claude", project_root=tmp_path, detach=True)

    assert result.success is True
    mock_lifecycle.assert_not_called()


@patch("wade.services.implementation_service.write_plan_md")
@patch("wade.services.implementation_service._post_implementation_lifecycle")
@patch("wade.services.implementation_service.add_implemented_by_labels")
@patch("wade.services.implementation_service._capture_post_session_usage")
@patch("wade.services.implementation_service.stop_title_keeper")
@patch("wade.services.implementation_service.start_title_keeper")
@patch("wade.services.implementation_service.set_terminal_title")
@patch("wade.services.implementation_service.compose_implement_title", return_value="title")
@patch("wade.services.implementation_service._detect_ai_cli_env", return_value=None)
@patch("wade.services.implementation_service.add_in_progress_label")
@patch("wade.services.implementation_service.bootstrap_worktree")
@patch("wade.services.implementation_service.git_worktree.list_worktrees", return_value=[])
@patch("wade.services.implementation_service.git_worktree.create_worktree")
@patch("wade.services.implementation_service.git_repo.get_repo_root")
@patch("wade.services.implementation_service._resolve_task_target")
@patch("wade.services.implementation_service.AbstractAITool.get")
@patch("wade.services.implementation_service.get_provider")
@patch("wade.services.implementation_service.load_config")
@patch("wade.services.implementation_service.git_pr.get_pr_for_branch", return_value=None)
@patch(
    "wade.services.implementation_service.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("wade.services.implementation_service.prompts")
def test_lifecycle_skipped_after_ai_crash(
    mock_prompts: MagicMock,
    _mock_bootstrap_pr: MagicMock,
    _mock_get_pr: MagicMock,
    mock_load_config: MagicMock,
    _mock_get_provider: MagicMock,
    mock_get_adapter: MagicMock,
    mock_resolve_task_target: MagicMock,
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
    _mock_capture_post_session_usage: MagicMock,
    _mock_add_implemented_by: MagicMock,
    mock_lifecycle: MagicMock,
    _mock_write_plan_md: MagicMock,
    tmp_path: Path,
) -> None:
    mock_load_config.return_value = _config(MergeStrategy.PR)
    mock_get_repo_root.return_value = tmp_path
    mock_resolve_task_target.return_value = Task(id="42", title="Test")
    mock_prompts.is_tty.return_value = False
    adapter = MagicMock()
    adapter.is_model_compatible.return_value = True
    adapter.launch.side_effect = RuntimeError("ai crashed")
    mock_get_adapter.return_value = adapter

    result = start("42", ai_tool="claude", project_root=tmp_path, detach=False)

    assert result.success is False  # AI crash → failure
    mock_lifecycle.assert_not_called()

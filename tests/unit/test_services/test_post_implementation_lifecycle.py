from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import PRLookup, PRRef
from wade.git.repo import GitError
from wade.models.config import AIConfig, ProjectConfig, ProjectSettings
from wade.models.session import MergeStatus, MergeStrategy
from wade.models.task import Task
from wade.services.implementation_service import _post_implementation_lifecycle, start

_PULL_FF = "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only"
_CHECKOUT = "wade.services.implementation_service.lifecycle.git_repo.checkout"
_CHECKOUT_DETACH = "wade.services.implementation_service.lifecycle.git_repo.checkout_detach"
_IS_HEAD_ATTACHED = "wade.services.implementation_service.lifecycle.git_repo.is_head_attached"
_DETECT_MAIN = "wade.services.implementation_service.lifecycle.git_repo.detect_main_branch"


def _config(strategy: MergeStrategy) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(main_branch="main", merge_strategy=strategy),
        ai=AIConfig(default_tool="claude"),
    )


@pytest.fixture(autouse=True)
def _default_no_open_pr_for_issue() -> object:
    """Skip the start() not-open-PR gate's ``gh pr list`` (which now raises on
    failure) so these lifecycle tests exercise the merge flow, not a resolver
    abort.
    """
    with patch(
        "wade.services.implementation_service.core.find_open_pr_branch_for_issue",
        return_value=None,
    ):
        yield


@patch(_IS_HEAD_ATTACHED, return_value=True)
@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.lifecycle.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.lifecycle.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
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
    _mock_is_head_attached: MagicMock,
    tmp_path: Path,
) -> None:
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check
    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # select is called with "Merge PR" / "Wait for reviews" — user picks 0 (Merge PR)
    mock_select.assert_called_once()
    select_items = mock_select.call_args[0][1]
    assert "Merge PR" in select_items
    assert "Wait for reviews" in select_items
    # Worktree is removed AFTER successful merge
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_pull_ff.assert_called_once_with(repo_root)


@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.prompts.confirm")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(found=False),
)
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
        provider,
    )

    mock_confirm.assert_not_called()
    mock_merge_pr.assert_not_called()


@patch("wade.services.review_service.poll_for_reviews")
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=1)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=False)
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_user_declines_merge(
    _mock_get_pr: MagicMock,
    mock_confirm: MagicMock,
    mock_select: MagicMock,
    mock_merge_pr: MagicMock,
    mock_poll: MagicMock,
    tmp_path: Path,
) -> None:
    from wade.models.review import PollOutcome

    mock_poll.return_value = PollOutcome.INTERRUPTED
    provider = MagicMock()
    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(
            tmp_path / "repo",
            "feat/42-test",
            42,
            tmp_path / "wt",
            provider,
        )

    # select returns 1 (Wait for reviews) — merge should NOT be called
    mock_select.assert_called_once()
    mock_merge_pr.assert_not_called()
    mock_poll.assert_called_once()


@patch(_IS_HEAD_ATTACHED, return_value=True)
@patch(_CHECKOUT)
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_merge_failure_preserves_branch(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_checkout: MagicMock,
    _mock_is_head_attached: MagicMock,
    tmp_path: Path,
) -> None:
    provider = MagicMock()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(
            tmp_path / "repo",
            "feat/42-test",
            42,
            tmp_path / "wt",
            provider,
        )

    # merge_pr raised, so no push or delete should have happened
    mock_merge_pr.assert_called_once()


@patch(_IS_HEAD_ATTACHED, return_value=True)
@patch(_CHECKOUT)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
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
    _mock_is_head_attached: MagicMock,
    tmp_path: Path,
) -> None:
    """On merge failure, HEAD should be restored from detached state to the branch."""
    provider = MagicMock()
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    mock_merge_pr.side_effect = subprocess.CalledProcessError(1, ["gh", "pr", "merge"])

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(
            tmp_path / "repo",
            "feat/42-test",
            42,
            wt_path,
            provider,
        )

    # Should have called checkout to restore branch after merge failure
    mock_checkout.assert_called_once_with(wt_path, "feat/42-test")


@patch(_IS_HEAD_ATTACHED, return_value=True)
@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch("wade.services.implementation_service.lifecycle.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.lifecycle.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_cleanup_and_pull_after_merge(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    _mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_pull_ff: MagicMock,
    _mock_is_head_attached: MagicMock,
    tmp_path: Path,
) -> None:
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()  # Needs to exist for is_dir() check

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # Worktree is removed AFTER successful merge
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_pull_ff.assert_called_once_with(repo_root)


@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch(_CHECKOUT)
@patch(_DETECT_MAIN, return_value="main")
@patch(_IS_HEAD_ATTACHED, return_value=False)
@patch("wade.services.implementation_service.lifecycle.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.lifecycle.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_detached_repo_root_reattaches_then_merges(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    _mock_is_head_attached: MagicMock,
    mock_detect_main: MagicMock,
    mock_checkout: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_pull_ff: MagicMock,
    tmp_path: Path,
) -> None:
    """Detached repo_root HEAD is re-attached before the merge, then merge proceeds."""
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        status = _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # Re-attached repo_root to the detected default branch BEFORE merging.
    mock_detect_main.assert_called_once_with(repo_root)
    mock_checkout.assert_called_once_with(repo_root, "main")
    # Merge and normal cleanup still ran.
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    mock_remove_worktree.assert_called_once_with(repo_root, wt_path)
    mock_pull_ff.assert_called_once_with(repo_root)
    assert status == MergeStatus.MERGED


@patch(_CHECKOUT_DETACH)
@patch(_CHECKOUT)
@patch(_DETECT_MAIN, side_effect=GitError("Neither 'main' nor 'master' branch found"))
@patch(_IS_HEAD_ATTACHED, return_value=False)
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_detached_repo_root_detect_fails_no_merge(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_is_head_attached: MagicMock,
    _mock_detect_main: MagicMock,
    mock_checkout: MagicMock,
    mock_checkout_detach: MagicMock,
    tmp_path: Path,
) -> None:
    """When re-attach detection fails (no main/master), fail fast without merging."""
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        status = _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # No GitHub-side merge and the worktree is left untouched (never detached).
    mock_merge_pr.assert_not_called()
    mock_checkout.assert_not_called()
    mock_checkout_detach.assert_not_called()
    provider.close_task.assert_not_called()
    assert status == MergeStatus.MERGE_FAILED


@patch(_CHECKOUT_DETACH)
@patch(_DETECT_MAIN, return_value="main")
@patch(_IS_HEAD_ATTACHED, return_value=False)
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_detached_repo_root_checkout_fails_no_merge(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_is_head_attached: MagicMock,
    _mock_detect_main: MagicMock,
    mock_checkout_detach: MagicMock,
    tmp_path: Path,
) -> None:
    """When re-attach checkout is refused (e.g. conflicting changes), fail fast."""
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()

    checkout_target = "wade.services.implementation_service.lifecycle.git_repo.checkout"
    with (
        patch(checkout_target, side_effect=GitError("checkout refused: local changes")),
        patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True),
    ):
        status = _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # Re-attach checkout raised → no merge, worktree never detached.
    mock_merge_pr.assert_not_called()
    mock_checkout_detach.assert_not_called()
    provider.close_task.assert_not_called()
    assert status == MergeStatus.MERGE_FAILED


@patch(_PULL_FF)
@patch(_CHECKOUT_DETACH)
@patch(_CHECKOUT)
@patch(_DETECT_MAIN)
@patch(_IS_HEAD_ATTACHED, return_value=True)
@patch("wade.services.implementation_service.lifecycle.git_worktree.prune_worktrees")
@patch("wade.services.implementation_service.lifecycle.git_worktree.remove_worktree")
@patch("wade.services.implementation_service.lifecycle.git_pr.merge_pr")
@patch("wade.services.implementation_service.lifecycle.git_repo.is_clean", return_value=True)
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=0)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=True)
@patch("wade.services.implementation_service.lifecycle.webbrowser.open")
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_pr_strategy_attached_repo_root_skips_reattach(
    _mock_get_pr: MagicMock,
    _mock_webbrowser_open: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    _mock_is_clean: MagicMock,
    mock_merge_pr: MagicMock,
    _mock_remove_worktree: MagicMock,
    _mock_prune: MagicMock,
    _mock_is_head_attached: MagicMock,
    mock_detect_main: MagicMock,
    mock_checkout: MagicMock,
    _mock_checkout_detach: MagicMock,
    mock_pull_ff: MagicMock,
    tmp_path: Path,
) -> None:
    """Attached repo_root HEAD: no detection, no re-attach checkout — happy path intact."""
    mock_pull_ff.return_value = MagicMock(returncode=0)
    provider = MagicMock()
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    wt_path.mkdir()

    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        status = _post_implementation_lifecycle(repo_root, "feat/42-test", 42, wt_path, provider)

    # Attached: never detected the default branch, never re-attached via checkout.
    mock_detect_main.assert_not_called()
    mock_checkout.assert_not_called()
    mock_merge_pr.assert_called_once_with(repo_root=repo_root, pr_number=99, strategy="squash")
    assert status == MergeStatus.MERGED


@pytest.mark.parametrize("network_access", [True, False, None])
@patch("wade.services.review_service.start")
@patch("wade.services.review_service.poll_for_reviews")
@patch("wade.services.implementation_service.lifecycle.prompts.select", return_value=1)
@patch("wade.services.implementation_service.lifecycle.prompts.confirm", return_value=False)
@patch(
    "wade.services.implementation_service.lifecycle.git_pr.get_pr_for_branch",
    return_value=PRLookup(
        found=True, pr=PRRef(number=99, url="https://example/pr/99", state="OPEN")
    ),
)
def test_wait_for_reviews_threads_network_access_into_review(
    _mock_get_pr: MagicMock,
    _mock_confirm: MagicMock,
    _mock_select: MagicMock,
    mock_poll: MagicMock,
    mock_review_start: MagicMock,
    network_access: bool | None,
    tmp_path: Path,
) -> None:
    """An explicit --network/--no-network pin survives the 'Wait for reviews'
    hand-off: the follow-on review session receives the same tri-state rather
    than silently re-resolving it from config."""
    from wade.models.review import PollOutcome

    mock_poll.return_value = PollOutcome.COMMENTS_FOUND
    provider = MagicMock()
    with patch("wade.services.implementation_service.lifecycle.prompts.is_tty", return_value=True):
        _post_implementation_lifecycle(
            tmp_path / "repo",
            "feat/42-test",
            42,
            tmp_path / "wt",
            provider,
            network_access=network_access,
        )

    mock_review_start.assert_called_once()
    assert mock_review_start.call_args.kwargs["network_access"] is network_access


@patch("wade.services.implementation_service.core.write_plan_md")
@patch("wade.services.implementation_service.core._post_implementation_lifecycle")
@patch("wade.services.implementation_service.core.launch_in_new_terminal", return_value=True)
@patch("wade.services.implementation_service.core.AbstractAITool.get")
@patch("wade.services.implementation_service.core._detect_ai_cli_env", return_value=None)
@patch("wade.services.implementation_service.core.add_in_progress_label")
@patch("wade.services.implementation_service.core.bootstrap_worktree")
@patch("wade.services.implementation_service.core.git_worktree.list_worktrees", return_value=[])
@patch("wade.services.implementation_service.core.git_worktree.create_worktree")
@patch("wade.services.implementation_service.core.git_repo.get_repo_root")
@patch("wade.services.implementation_service.core._resolve_task_target")
@patch("wade.services.implementation_service.core.get_provider")
@patch("wade.services.implementation_service.core.load_config")
@patch(
    "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
    return_value=PRLookup(found=False),
)
@patch(
    "wade.services.implementation_service.core.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("wade.services.implementation_service.core.prompts")
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


@patch("wade.services.implementation_service.core.write_plan_md")
@patch("wade.services.implementation_service.core._post_implementation_lifecycle")
@patch("wade.services.implementation_service.core.add_implemented_by_labels")
@patch("wade.services.implementation_service.core._capture_post_session_usage")
@patch("wade.services.implementation_service.core.stop_title_keeper")
@patch("wade.services.implementation_service.core.start_title_keeper")
@patch("wade.services.implementation_service.core.set_terminal_title")
@patch("wade.services.implementation_service.core.compose_implement_title", return_value="title")
@patch("wade.services.implementation_service.core._detect_ai_cli_env", return_value=None)
@patch("wade.services.implementation_service.core.add_in_progress_label")
@patch("wade.services.implementation_service.core.bootstrap_worktree")
@patch("wade.services.implementation_service.core.git_worktree.list_worktrees", return_value=[])
@patch("wade.services.implementation_service.core.git_worktree.create_worktree")
@patch("wade.services.implementation_service.core.git_repo.get_repo_root")
@patch("wade.services.implementation_service.core._resolve_task_target")
@patch("wade.services.implementation_service.core.AbstractAITool.get")
@patch("wade.services.implementation_service.core.get_provider")
@patch("wade.services.implementation_service.core.load_config")
@patch(
    "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
    return_value=PRLookup(found=False),
)
@patch(
    "wade.services.implementation_service.core.bootstrap_draft_pr",
    return_value={"number": 1, "url": "http://test"},
)
@patch("wade.services.implementation_service.core.prompts")
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


# ---------------------------------------------------------------------------
# _merge_pr uncommitted-changes display + adaptive confirm (issue #453)
# ---------------------------------------------------------------------------

_LC = "wade.services.implementation_service.lifecycle"


@patch(f"{_LC}.git_repo.is_clean", return_value=False)
@patch(f"{_LC}._format_uncommitted_summary", return_value="1 unstaged, 2 untracked")
@patch(f"{_LC}._get_dirty_file_paths")
@patch(f"{_LC}._identify_session_dirty_files")
@patch(f"{_LC}.prompts.confirm", return_value=False)
@patch(f"{_LC}.console")
def test_merge_pr_genuine_changes_lists_and_defaults_no(
    mock_console: MagicMock,
    mock_confirm: MagicMock,
    mock_identify: MagicMock,
    mock_dirty: MagicMock,
    _mock_summary: MagicMock,
    _mock_is_clean: MagicMock,
    tmp_path: Path,
) -> None:
    from wade.services.implementation_service.lifecycle import _merge_pr

    mock_dirty.return_value = ["src/app.py", ".wade/state.json"]
    mock_identify.return_value = [".wade/state.json"]

    result = _merge_pr(tmp_path, "feat/x", 7, "7", tmp_path, MagicMock())

    # Declined → never merges.
    assert result == MergeStatus.NOT_MERGED
    # Confirm defaults to No when genuine work is at risk.
    assert mock_confirm.call_args.kwargs["default"] is False
    # Genuine file is listed (markup disabled for arbitrary filenames).
    detail_msgs = [c.args[0] for c in mock_console.detail.call_args_list]
    assert any("src/app.py" in m for m in detail_msgs)
    assert any("Your uncommitted changes:" in m for m in detail_msgs)


@patch(f"{_LC}.git_repo.is_clean", return_value=False)
@patch(f"{_LC}._format_uncommitted_summary", return_value="2 untracked")
@patch(f"{_LC}._get_dirty_file_paths")
@patch(f"{_LC}._identify_session_dirty_files")
@patch(f"{_LC}.prompts.confirm", return_value=False)
@patch(f"{_LC}.console")
def test_merge_pr_scaffold_only_lists_and_defaults_yes(
    mock_console: MagicMock,
    mock_confirm: MagicMock,
    mock_identify: MagicMock,
    mock_dirty: MagicMock,
    _mock_summary: MagicMock,
    _mock_is_clean: MagicMock,
    tmp_path: Path,
) -> None:
    from wade.services.implementation_service.lifecycle import _merge_pr

    scaffold = ["PLAN.md", ".wade/state.json"]
    mock_dirty.return_value = list(scaffold)
    mock_identify.return_value = list(scaffold)

    # Decline so we can assert the default without mocking the merge machinery.
    _merge_pr(tmp_path, "feat/x", 7, "7", tmp_path, MagicMock())

    # Confirm defaults to Yes when only regenerable scaffold is dirty.
    assert mock_confirm.call_args.kwargs["default"] is True
    detail_msgs = [c.args[0] for c in mock_console.detail.call_args_list]
    assert any("wade session files (regenerable):" in m for m in detail_msgs)
    assert any("PLAN.md" in m for m in detail_msgs)
    # No "genuine" heading when there is no genuine work.
    assert not any("Your uncommitted changes:" in m for m in detail_msgs)


@patch(f"{_LC}.git_repo.is_clean", return_value=False)
@patch(f"{_LC}._format_uncommitted_summary", return_value="1 unstaged, 1 untracked")
@patch(f"{_LC}._get_dirty_file_paths")
@patch(f"{_LC}._identify_session_dirty_files")
@patch(f"{_LC}.prompts.confirm", return_value=False)
@patch(f"{_LC}.console")
def test_merge_pr_mixed_lists_both_groups_defaults_no(
    mock_console: MagicMock,
    mock_confirm: MagicMock,
    mock_identify: MagicMock,
    mock_dirty: MagicMock,
    _mock_summary: MagicMock,
    _mock_is_clean: MagicMock,
    tmp_path: Path,
) -> None:
    from wade.services.implementation_service.lifecycle import _merge_pr

    mock_dirty.return_value = ["src/app.py", "PLAN.md"]
    mock_identify.return_value = ["PLAN.md"]

    _merge_pr(tmp_path, "feat/x", 7, "7", tmp_path, MagicMock())

    # Any genuine work present → default No.
    assert mock_confirm.call_args.kwargs["default"] is False
    detail_msgs = [c.args[0] for c in mock_console.detail.call_args_list]
    assert any("Your uncommitted changes:" in m for m in detail_msgs)
    assert any("wade session files (regenerable):" in m for m in detail_msgs)
    assert any("src/app.py" in m for m in detail_msgs)
    assert any("PLAN.md" in m for m in detail_msgs)


@patch(f"{_LC}.git_repo.is_clean", return_value=False)
@patch(f"{_LC}._format_uncommitted_summary", return_value="dirty")
@patch(f"{_LC}._get_dirty_file_paths", return_value=[])
@patch(f"{_LC}._identify_session_dirty_files", return_value=[])
@patch(f"{_LC}.prompts.confirm", return_value=False)
@patch(f"{_LC}.console")
def test_merge_pr_dirty_but_unenumerable_fails_closed(
    _mock_console: MagicMock,
    mock_confirm: MagicMock,
    _mock_identify: MagicMock,
    _mock_dirty: MagicMock,
    _mock_summary: MagicMock,
    _mock_is_clean: MagicMock,
    tmp_path: Path,
) -> None:
    """is_clean=False but no paths enumerated (git failure) → conservative default=False."""
    from wade.services.implementation_service.lifecycle import _merge_pr

    result = _merge_pr(tmp_path, "feat/x", 7, "7", tmp_path, MagicMock())

    assert result == MergeStatus.NOT_MERGED
    # Empty enumeration must NOT read as "safe scaffold-only" → default stays No.
    assert mock_confirm.call_args.kwargs["default"] is False


@patch(f"{_LC}._pull_main_after_merge")
@patch(f"{_LC}.git_worktree.prune_worktrees")
@patch(f"{_LC}.git_worktree.remove_worktree")
@patch(f"{_LC}._preserve_session_data")
@patch(f"{_LC}.git_pr.merge_pr")
@patch(f"{_LC}.git_repo.checkout_detach")
@patch(f"{_LC}.git_repo.is_head_attached", return_value=True)
@patch(f"{_LC}.git_repo.main_checkout_root")
@patch(f"{_LC}.git_repo.is_clean", return_value=False)
@patch(f"{_LC}._format_uncommitted_summary", return_value="2 untracked")
@patch(f"{_LC}._get_dirty_file_paths")
@patch(f"{_LC}._identify_session_dirty_files")
@patch(f"{_LC}.prompts.confirm", return_value=True)
def test_merge_pr_scaffold_only_proceed_merges(
    mock_confirm: MagicMock,
    mock_identify: MagicMock,
    mock_dirty: MagicMock,
    _mock_summary: MagicMock,
    _mock_is_clean: MagicMock,
    mock_main_root: MagicMock,
    _mock_head_attached: MagicMock,
    _mock_detach: MagicMock,
    mock_merge: MagicMock,
    _mock_preserve: MagicMock,
    _mock_remove: MagicMock,
    _mock_prune: MagicMock,
    _mock_pull: MagicMock,
    tmp_path: Path,
) -> None:
    from wade.services.implementation_service.lifecycle import _merge_pr

    mock_main_root.return_value = tmp_path
    scaffold = ["PLAN.md", ".wade/state.json"]
    mock_dirty.return_value = list(scaffold)
    mock_identify.return_value = list(scaffold)

    result = _merge_pr(tmp_path, "feat/x", 7, "7", tmp_path, MagicMock())

    # Proceeding on scaffold-only reaches the actual merge and completes.
    assert result == MergeStatus.MERGED
    assert mock_confirm.call_args.kwargs["default"] is True
    mock_merge.assert_called_once()

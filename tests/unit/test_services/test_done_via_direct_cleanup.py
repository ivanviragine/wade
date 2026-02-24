"""Tests for _done_via_direct() worktree cleanup retry/skip prompt."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghaiw.models.config import ProjectConfig, ProjectSettings
from ghaiw.models.work import MergeStrategy, SyncResult
from ghaiw.services.work_service import _done_via_direct

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO = Path("/fake/repo")
_BRANCH = "feat/1-test"
_ISSUE = "1"
_MAIN = "main"
_SYNC_OK = SyncResult(success=True, current_branch=_BRANCH, main_branch=_MAIN)

# Targets for the common "happy path" dependencies that must be mocked so the
# function reaches the cleanup block without failing for unrelated reasons.
_BASE_PATCH_TARGETS: dict[str, dict] = {
    "ghaiw.services.work_service.get_provider": {},
    "ghaiw.services.work_service.git_sync.fetch_origin": {},
    "ghaiw.services.work_service.git_sync.merge_branch": {"return_value": _SYNC_OK},
    "ghaiw.services.work_service.git_repo._run_git": {},
    "ghaiw.services.work_service.remove_in_progress_label": {},
    "ghaiw.services.work_service.console": {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(
            main_branch=_MAIN,
            merge_strategy=MergeStrategy.DIRECT,
        )
    )


def _enter_base_patches(stack: ExitStack) -> dict[str, MagicMock]:
    """Enter all happy-path patches and return their mock objects."""
    return {
        target: stack.enter_context(patch(target, **kwargs))
        for target, kwargs in _BASE_PATCH_TARGETS.items()
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDoneViaDirectCleanup:
    """Worktree cleanup retry/skip prompt in _done_via_direct()."""

    def test_cleanup_success_no_prompt(self) -> None:
        """When cleanup succeeds on first try, prompts.select is NOT called."""
        with ExitStack() as stack:
            _enter_base_patches(stack)
            stack.enter_context(patch("ghaiw.services.work_service.git_branch.delete_branch"))
            stack.enter_context(patch("ghaiw.services.work_service.git_worktree.prune_worktrees"))
            mock_select = stack.enter_context(patch("ghaiw.services.work_service.prompts.select"))

            result = _done_via_direct(
                repo_root=_REPO,
                branch=_BRANCH,
                issue_number=_ISSUE,
                main_branch=_MAIN,
                close_issue=False,
                config=_make_config(),
            )

        assert result is True
        mock_select.assert_not_called()

    def test_cleanup_failure_prompts_user(self) -> None:
        """When cleanup fails, prompts.select is called with the expected text and choices."""
        error = RuntimeError("branch is locked")
        with ExitStack() as stack:
            _enter_base_patches(stack)
            stack.enter_context(
                patch(
                    "ghaiw.services.work_service.git_branch.delete_branch",
                    side_effect=error,
                )
            )
            stack.enter_context(patch("ghaiw.services.work_service.git_worktree.prune_worktrees"))
            # User picks "Skip" so function can continue
            mock_select = stack.enter_context(
                patch(
                    "ghaiw.services.work_service.prompts.select",
                    return_value=1,
                )
            )
            stack.enter_context(patch("ghaiw.services.work_service.logger"))

            result = _done_via_direct(
                repo_root=_REPO,
                branch=_BRANCH,
                issue_number=_ISSUE,
                main_branch=_MAIN,
                close_issue=False,
                config=_make_config(),
            )

        assert result is True
        mock_select.assert_called_once_with(
            f"Worktree cleanup failed: {error}. What would you like to do?",
            ["Retry", "Skip (leave worktree in place)"],
        )

    def test_cleanup_failure_retry_succeeds(self) -> None:
        """First call raises → user picks Retry → second call succeeds; no warning logged."""
        error = RuntimeError("locked")
        call_count = 0

        def delete_side_effect(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise error

        with ExitStack() as stack:
            _enter_base_patches(stack)
            mock_delete = stack.enter_context(
                patch(
                    "ghaiw.services.work_service.git_branch.delete_branch",
                    side_effect=delete_side_effect,
                )
            )
            stack.enter_context(patch("ghaiw.services.work_service.git_worktree.prune_worktrees"))
            # User picks "Retry" (index 0)
            stack.enter_context(
                patch(
                    "ghaiw.services.work_service.prompts.select",
                    return_value=0,
                )
            )
            mock_logger = stack.enter_context(patch("ghaiw.services.work_service.logger"))

            result = _done_via_direct(
                repo_root=_REPO,
                branch=_BRANCH,
                issue_number=_ISSUE,
                main_branch=_MAIN,
                close_issue=False,
                config=_make_config(),
            )

        assert result is True
        assert mock_delete.call_count == 2
        mock_logger.warning.assert_not_called()

    def test_cleanup_failure_skip_logs_warning(self) -> None:
        """When user picks Skip, warning is logged with reason=user_skipped."""
        with ExitStack() as stack:
            _enter_base_patches(stack)
            stack.enter_context(
                patch(
                    "ghaiw.services.work_service.git_branch.delete_branch",
                    side_effect=RuntimeError("cannot delete"),
                )
            )
            stack.enter_context(patch("ghaiw.services.work_service.git_worktree.prune_worktrees"))
            # User picks "Skip" (index 1)
            stack.enter_context(
                patch(
                    "ghaiw.services.work_service.prompts.select",
                    return_value=1,
                )
            )
            mock_logger = stack.enter_context(patch("ghaiw.services.work_service.logger"))

            result = _done_via_direct(
                repo_root=_REPO,
                branch=_BRANCH,
                issue_number=_ISSUE,
                main_branch=_MAIN,
                close_issue=False,
                config=_make_config(),
            )

        assert result is True
        mock_logger.warning.assert_called_once_with(
            "worktree.cleanup_skipped", reason="user_skipped"
        )

    def test_cleanup_failure_retry_also_fails(self) -> None:
        """First call raises, user picks Retry, second call also raises.

        Warning logged once, no exception.
        """
        with ExitStack() as stack:
            _enter_base_patches(stack)
            mock_delete = stack.enter_context(
                patch(
                    "ghaiw.services.work_service.git_branch.delete_branch",
                    side_effect=RuntimeError("always fails"),
                )
            )
            stack.enter_context(patch("ghaiw.services.work_service.git_worktree.prune_worktrees"))
            # User picks "Retry" (index 0)
            stack.enter_context(
                patch(
                    "ghaiw.services.work_service.prompts.select",
                    return_value=0,
                )
            )
            mock_logger = stack.enter_context(patch("ghaiw.services.work_service.logger"))

            result = _done_via_direct(
                repo_root=_REPO,
                branch=_BRANCH,
                issue_number=_ISSUE,
                main_branch=_MAIN,
                close_issue=False,
                config=_make_config(),
            )

        assert result is True
        assert mock_delete.call_count == 2
        mock_logger.warning.assert_called_once_with(
            "worktree.cleanup_skipped", reason="retry_failed", exc_info=True
        )

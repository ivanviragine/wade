"""Regression tests for done() idempotency (#357, A3).

done() must defer stripping the worktree gitignore block (which un-hides session
artifacts like PLAN.md) until the PR finalize actually succeeds. If it stripped
earlier and a later step failed, a retry would see the un-hidden artifacts as a
dirty tree and fail the clean gate — leaving done() un-retryable. Every failure
branch must therefore leave the strip un-done.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.config import ProjectConfig, ProjectSettings
from wade.services.implementation_service.done import done

_DONE = "wade.services.implementation_service.done"


def _run_done(tmp_path: Path, *, pr_result: bool) -> MagicMock:
    """Drive done("42") to the _done_via_pr call, returning the strip mock."""
    repo_root = tmp_path / "repo"
    wt_path = tmp_path / "wt"
    repo_root.mkdir()
    wt_path.mkdir()

    config = ProjectConfig(project=ProjectSettings(main_branch="main"))

    with ExitStack() as stack:
        stack.enter_context(patch(f"{_DONE}.load_config", return_value=config))
        stack.enter_context(patch(f"{_DONE}.get_provider"))
        stack.enter_context(patch(f"{_DONE}.git_repo.get_repo_root", return_value=repo_root))
        stack.enter_context(patch(f"{_DONE}.find_worktree_path", return_value=wt_path))
        stack.enter_context(patch(f"{_DONE}.git_repo.get_current_branch", return_value="feat/42-x"))
        stack.enter_context(patch(f"{_DONE}.git_repo.is_clean", return_value=True))
        stack.enter_context(patch(f"{_DONE}._check_tracked_managed_files", return_value=[]))
        stack.enter_context(patch(f"{_DONE}.git_repo.unskip_worktree_file"))
        strip = stack.enter_context(patch(f"{_DONE}.strip_worktree_gitignore"))
        stack.enter_context(patch(f"{_DONE}._done_via_pr", return_value=pr_result))
        stack.enter_context(patch(f"{_DONE}.console"))

        result = done("42", project_root=repo_root)

    assert result is pr_result
    return strip


class TestDoneDefersStrip:
    def test_failure_leaves_gitignore_unstripped(self, tmp_path: Path) -> None:
        # _done_via_pr fails (push / PR API error) → the strip must NOT run, so a
        # retry still passes the clean gate.
        strip = _run_done(tmp_path, pr_result=False)
        strip.assert_not_called()

    def test_success_strips_gitignore(self, tmp_path: Path) -> None:
        # Only on success is the worktree gitignore block stripped.
        strip = _run_done(tmp_path, pr_result=True)
        strip.assert_called_once()

    def test_success_survives_strip_oserror(self, tmp_path: Path) -> None:
        # The PR is already pushed + updated when the strip runs. A filesystem
        # error there (read-only file, removed worktree dir) must NOT turn an
        # already-finalized PR into a reported failure — it is warned, not raised.
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        repo_root.mkdir()
        wt_path.mkdir()
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))

        with ExitStack() as stack:
            stack.enter_context(patch(f"{_DONE}.load_config", return_value=config))
            stack.enter_context(patch(f"{_DONE}.get_provider"))
            stack.enter_context(patch(f"{_DONE}.git_repo.get_repo_root", return_value=repo_root))
            stack.enter_context(patch(f"{_DONE}.find_worktree_path", return_value=wt_path))
            stack.enter_context(
                patch(f"{_DONE}.git_repo.get_current_branch", return_value="feat/42-x")
            )
            stack.enter_context(patch(f"{_DONE}.git_repo.is_clean", return_value=True))
            stack.enter_context(patch(f"{_DONE}._check_tracked_managed_files", return_value=[]))
            stack.enter_context(patch(f"{_DONE}.git_repo.unskip_worktree_file"))
            stack.enter_context(
                patch(f"{_DONE}.strip_worktree_gitignore", side_effect=OSError("read-only fs"))
            )
            stack.enter_context(patch(f"{_DONE}._done_via_pr", return_value=True))
            stack.enter_context(patch(f"{_DONE}.console"))

            result = done("42", project_root=repo_root)

        assert result is True

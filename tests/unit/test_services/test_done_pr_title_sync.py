"""Tests for PR-title sync in _done_via_pr().

The done() PR-title *gate* (block-on-invalid) is covered in test_done_gates.py
(``TestPrTitleGate`` + the dispatch-order test that pins it first for both
session types). This file covers the complementary *sync*: an already-open PR
whose title differs from the (validated) issue title is edited to match, so a
corrected title reaches the PR and PR Title Lint passes. ``_done_via_pr`` is the
single shared finalize path for both session types, so one set of tests covers
both.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.models.config import DoneConfig, ProjectConfig, ProjectSettings
from wade.models.task import Task
from wade.services.implementation_service import _done_via_pr

_DONE = "wade.services.implementation_service.done"


def _run_done_via_pr(
    tmp_path: Path,
    *,
    issue_title: str,
    pr_title: str,
    require_conventional_title: bool = True,
    update_title_ok: bool = True,
    is_draft: bool = False,
) -> tuple[bool, MagicMock, MagicMock]:
    """Drive _done_via_pr against an OPEN existing PR.

    Returns ``(result, update_pr_title_mock, mark_pr_ready_mock)`` so callers can
    assert on both the sync outcome and whether the finalize continued to
    mark-ready (which must not happen when a load-bearing sync fails). Pass
    ``is_draft=True`` to make ``mark_pr_ready`` a meaningful downstream signal —
    it is only called for draft PRs.
    """
    worktree_path = tmp_path / "wt-42"
    worktree_path.mkdir()
    (worktree_path / "PR-SUMMARY.md").write_text("Real summary of the work.\n")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    task = Task(id="42", title=issue_title, body="## Tasks\n- x\n")
    config = ProjectConfig(
        project=ProjectSettings(main_branch="main"),
        done=DoneConfig(require_conventional_title=require_conventional_title),
    )
    lookup = PRLookup(
        found=True,
        pr=PRRef(
            number=7,
            url="https://github.com/test/pull/7",
            title=pr_title,
            state="OPEN",
            isDraft=is_draft,
        ),
    )

    with ExitStack() as stack:
        mock_get_provider = stack.enter_context(patch(f"{_DONE}.get_provider"))
        stack.enter_context(patch(f"{_DONE}.git_repo._run_git"))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_for_branch", return_value=lookup))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_body", return_value="Implements #42\n"))
        stack.enter_context(patch(f"{_DONE}.git_pr.update_pr_body", return_value=True))
        mock_update_title = stack.enter_context(
            patch(f"{_DONE}.git_pr.update_pr_title", return_value=update_title_ok)
        )
        mock_mark_ready = stack.enter_context(
            patch(f"{_DONE}.git_pr.mark_pr_ready", return_value=True)
        )
        stack.enter_context(patch(f"{_DONE}.remove_in_progress_label"))

        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task
        mock_provider.find_parent_issue.return_value = None
        mock_get_provider.return_value = mock_provider

        result = _done_via_pr(
            repo_root=repo_root,
            branch="feat/42-x",
            issue_number="42",
            main_branch="main",
            close_issue=True,
            draft=False,
            config=config,
            worktree_path=worktree_path,
        )
        return result, mock_update_title, mock_mark_ready


class TestPrTitleSync:
    def test_syncs_when_pr_title_differs(self, tmp_path: Path) -> None:
        result, mock_update_title, _ = _run_done_via_pr(
            tmp_path,
            issue_title="feat: proper conventional title",
            pr_title="E3: stale non-conventional title",
        )
        assert result is True
        mock_update_title.assert_called_once()
        args = mock_update_title.call_args[0]
        assert args[1] == 7  # pr_number
        assert args[2] == "feat: proper conventional title"

    def test_no_sync_when_titles_match(self, tmp_path: Path) -> None:
        result, mock_update_title, _ = _run_done_via_pr(
            tmp_path,
            issue_title="feat: same title",
            pr_title="feat: same title",
        )
        assert result is True
        mock_update_title.assert_not_called()

    def test_no_sync_when_hatch_disabled(self, tmp_path: Path) -> None:
        # With the toggle off, wade does not touch the PR title even if it differs.
        result, mock_update_title, _ = _run_done_via_pr(
            tmp_path,
            issue_title="feat: proper title",
            pr_title="E3: stale title",
            require_conventional_title=False,
        )
        assert result is True
        mock_update_title.assert_not_called()


class TestPrTitleSyncFailure:
    """A failed sync must fail done iff the stale PR title would fail lint."""

    def test_fails_done_when_sync_fails_and_stale_title_non_conventional(
        self, tmp_path: Path
    ) -> None:
        # Sync fails and the current PR title is NOT conventional → PR Title Lint
        # would fail, so done must fail (and never reach mark-ready) so the sync
        # can be retried.
        result, mock_update_title, mock_mark_ready = _run_done_via_pr(
            tmp_path,
            issue_title="feat: proper conventional title",
            pr_title="E3: stale non-conventional title",
            update_title_ok=False,
            is_draft=True,
        )
        assert result is False
        mock_update_title.assert_called_once()
        mock_mark_ready.assert_not_called()

    def test_continues_when_sync_fails_but_stale_title_already_conventional(
        self, tmp_path: Path
    ) -> None:
        # Sync fails but the current PR title is already conventional → PR Title
        # Lint still passes, so a transient failure is non-blocking and done
        # completes (mark-ready runs).
        result, mock_update_title, mock_mark_ready = _run_done_via_pr(
            tmp_path,
            issue_title="feat: corrected issue title",
            pr_title="fix: conventional but stale PR title",
            update_title_ok=False,
            is_draft=True,
        )
        assert result is True
        mock_update_title.assert_called_once()
        mock_mark_ready.assert_called_once()

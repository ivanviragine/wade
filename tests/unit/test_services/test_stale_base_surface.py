"""Tests for the Part A stale-base surfacing in core.py (#407)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.repo import GitError
from wade.models.session import SyncEvent, SyncEventType, SyncResult
from wade.models.task import Task
from wade.services.implementation_service.core import (
    _classify_catchup_failure,
    _commits_behind_base,
    _surface_stale_base_if_behind,
)
from wade.utils import stale_base

_CORE = "wade.services.implementation_service.core"


def _result(
    *,
    conflicts: list[str] | None = None,
    events: list[SyncEvent] | None = None,
) -> SyncResult:
    return SyncResult(
        success=False,
        current_branch="feat/1-x",
        main_branch="main",
        conflicts=conflicts or [],
        events=events or [],
    )


class TestClassifyCatchupFailure:
    def test_conflicts_map_to_merge_conflict(self) -> None:
        r = _result(conflicts=["src/a.py"])
        assert _classify_catchup_failure(r) == stale_base.REASON_MERGE_CONFLICT

    def test_untracked_conflict_event(self) -> None:
        r = _result(events=[SyncEvent(event=SyncEventType.UNTRACKED_CONFLICT, data={})])
        assert _classify_catchup_failure(r) == stale_base.REASON_UNTRACKED_CONFLICT

    def test_conflict_event_without_conflict_list(self) -> None:
        r = _result(events=[SyncEvent(event=SyncEventType.CONFLICT, data={})])
        assert _classify_catchup_failure(r) == stale_base.REASON_MERGE_CONFLICT

    def test_no_signal_maps_to_unknown(self) -> None:
        r = _result(events=[SyncEvent(event=SyncEventType.ERROR, data={"reason": "stash_failed"})])
        assert _classify_catchup_failure(r) == stale_base.REASON_UNKNOWN


class TestCommitsBehindBase:
    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_prefers_origin_ref(self, mock_repo: MagicMock, mock_branch: MagicMock) -> None:
        mock_repo.has_remote.return_value = True
        mock_branch.commits_ahead.return_value = 7
        assert _commits_behind_base(Path("/repo"), "main", "feat/1-x") == 7
        mock_branch.commits_ahead.assert_called_once_with(Path("/repo"), "origin/main", "feat/1-x")

    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_falls_back_to_local_when_origin_ref_missing(
        self, mock_repo: MagicMock, mock_branch: MagicMock
    ) -> None:
        mock_repo.has_remote.return_value = True
        mock_branch.commits_ahead.side_effect = [GitError("bad ref"), 3]
        assert _commits_behind_base(Path("/repo"), "main", "feat/1-x") == 3
        assert mock_branch.commits_ahead.call_count == 2

    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_no_remote_uses_local_base(self, mock_repo: MagicMock, mock_branch: MagicMock) -> None:
        mock_repo.has_remote.return_value = False
        mock_branch.commits_ahead.return_value = 2
        assert _commits_behind_base(Path("/repo"), "main", "feat/1-x") == 2
        mock_branch.commits_ahead.assert_called_once_with(Path("/repo"), "main", "feat/1-x")

    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_returns_zero_when_no_ref_resolves(
        self, mock_repo: MagicMock, mock_branch: MagicMock
    ) -> None:
        mock_repo.has_remote.return_value = False
        mock_branch.commits_ahead.side_effect = GitError("bad ref")
        assert _commits_behind_base(Path("/repo"), "main", "feat/1-x") == 0


class TestSurfaceStaleBase:
    @patch(f"{_CORE}.console")
    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_writes_marker_and_warning_when_behind(
        self, mock_repo: MagicMock, mock_branch: MagicMock, mock_console: MagicMock, tmp_path: Path
    ) -> None:
        mock_repo.has_remote.return_value = True
        mock_branch.commits_ahead.return_value = 24
        warning = _surface_stale_base_if_behind(
            repo_root=tmp_path,
            worktree_path=tmp_path,
            base="main",
            current="feat/1-x",
            reason=stale_base.REASON_UNTRACKED_CONFLICT,
        )
        assert warning is not None
        assert "24 COMMITS BEHIND main" in warning
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert marker.behind == 24
        assert marker.reason == stale_base.REASON_UNTRACKED_CONFLICT
        # Loud, boxed, error-level output.
        mock_console.panel.assert_called_once()
        assert mock_console.panel.call_args.kwargs.get("border_style") == "error"

    @patch(f"{_CORE}.console")
    @patch(f"{_CORE}.git_branch")
    @patch(f"{_CORE}.git_repo")
    def test_clears_marker_and_no_warning_when_caught_up(
        self, mock_repo: MagicMock, mock_branch: MagicMock, mock_console: MagicMock, tmp_path: Path
    ) -> None:
        # A stale marker left from a previous startup must be cleared once caught up.
        stale_base.write_stale_base(tmp_path, 5, stale_base.REASON_MERGE_CONFLICT)
        mock_repo.has_remote.return_value = True
        mock_branch.commits_ahead.return_value = 0
        warning = _surface_stale_base_if_behind(
            repo_root=tmp_path,
            worktree_path=tmp_path,
            base="main",
            current="feat/1-x",
            reason=stale_base.REASON_UNKNOWN,
        )
        assert warning is None
        assert stale_base.read_stale_base(tmp_path) is None
        mock_console.panel.assert_not_called()


class TestPromptInjection:
    def test_stale_warning_prepended_to_prompt(self) -> None:
        from wade.services.implementation_service.draft_pr import build_implementation_prompt

        task = Task(id="1", title="fix: something")
        warning = "⚠️ BRANCH IS 3 COMMITS BEHIND main — do not start."
        prompt = build_implementation_prompt(task, "claude", has_plan=True, stale_warning=warning)
        assert prompt.startswith(warning)

    def test_no_warning_leaves_prompt_unchanged(self) -> None:
        from wade.services.implementation_service.draft_pr import build_implementation_prompt

        task = Task(id="1", title="fix: something")
        with_none = build_implementation_prompt(task, "claude", has_plan=True, stale_warning=None)
        assert not with_none.startswith("⚠️")

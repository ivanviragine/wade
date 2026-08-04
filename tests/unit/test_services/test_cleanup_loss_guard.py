"""Regression tests for the cleanup loss guard (issue #357, defect A2).

``_cleanup_worktree`` must refuse to remove a worktree with uncommitted changes
or unmerged local commits unless ``--discard-dirty`` is given, and must name
exactly what would be lost. ``--force`` (skip confirmation) never implies
``--discard-dirty``.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.services.implementation_service.cleanup import (
    _cleanup_worktree,
    _worktree_loss_risk,
)

_CLEANUP = "wade.services.implementation_service.cleanup"


class TestWorktreeLossRisk:
    def test_clean_and_merged_is_safe(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.is_clean", return_value=True))
            stack.enter_context(patch(f"{_CLEANUP}.git_branch.commits_ahead", return_value=0))
            losses = _worktree_loss_risk(tmp_path, wt, "feat/42-x", "main")
        assert losses == []

    def test_dirty_worktree_is_a_loss(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.is_clean", return_value=False))
            stack.enter_context(
                patch(
                    f"{_CLEANUP}.git_repo.get_dirty_status",
                    return_value={"staged": 1, "unstaged": 2, "untracked": 0},
                )
            )
            stack.enter_context(patch(f"{_CLEANUP}.git_branch.commits_ahead", return_value=0))
            losses = _worktree_loss_risk(tmp_path, wt, "feat/42-x", "main")
        assert len(losses) == 1
        assert "uncommitted" in losses[0]

    def test_unmerged_commits_are_a_loss(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.is_clean", return_value=True))
            stack.enter_context(patch(f"{_CLEANUP}.git_branch.commits_ahead", return_value=3))
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.merge_base", return_value="aaa"))
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.rev_parse", return_value="bbb"))
            losses = _worktree_loss_risk(tmp_path, wt, "feat/42-x", "main")
        assert len(losses) == 1
        assert "3 local commit(s) not merged" in losses[0]

    def test_merged_commits_are_not_a_loss(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.is_clean", return_value=True))
            stack.enter_context(patch(f"{_CLEANUP}.git_branch.commits_ahead", return_value=3))
            # merge-base == tip → the branch is fully merged into main.
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.merge_base", return_value="same"))
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.rev_parse", return_value="same"))
            losses = _worktree_loss_risk(tmp_path, wt, "feat/42-x", "main")
        assert losses == []

    def test_unverifiable_commit_count_fails_closed(self, tmp_path: Path) -> None:
        # A git error measuring commits must read as UNSAFE (a loss), never as
        # "no work to lose" — same fail-safe direction as the is_clean check.
        from wade.git.repo import GitError

        wt = tmp_path / "wt"
        wt.mkdir()
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_CLEANUP}.git_repo.is_clean", return_value=True))
            stack.enter_context(
                patch(f"{_CLEANUP}.git_branch.commits_ahead", side_effect=GitError("bad ref"))
            )
            losses = _worktree_loss_risk(tmp_path, wt, "feat/42-x", "main")
        assert len(losses) == 1
        assert "could not verify" in losses[0]


class TestCleanupWorktreeGuard:
    def _common_patches(self, stack: ExitStack, *, losses: list[str]) -> dict[str, MagicMock]:
        stack.enter_context(
            patch(f"{_CLEANUP}.git_repo.main_checkout_root", side_effect=lambda p: p)
        )
        stack.enter_context(
            patch(
                f"{_CLEANUP}.git_worktree.list_worktrees",
                return_value=[{"path": "/repo/wt", "branch": "feat/42-x"}],
            )
        )
        stack.enter_context(patch(f"{_CLEANUP}._worktree_loss_risk", return_value=losses))
        stack.enter_context(patch(f"{_CLEANUP}._preserve_session_data"))
        stack.enter_context(patch(f"{_CLEANUP}.console"))
        mock_remove = stack.enter_context(patch(f"{_CLEANUP}.git_worktree.remove_worktree"))
        mock_delete = stack.enter_context(patch(f"{_CLEANUP}.git_branch.delete_branch"))
        stack.enter_context(patch(f"{_CLEANUP}.git_worktree.prune_worktrees"))
        return {"remove": mock_remove, "delete": mock_delete}

    def test_refuses_dirty_without_discard(self) -> None:
        with ExitStack() as stack:
            mocks = self._common_patches(stack, losses=["uncommitted changes (1 staged, 0, 0)"])
            result = _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main")
        assert result is False
        mocks["remove"].assert_not_called()
        mocks["delete"].assert_not_called()

    def test_force_does_not_imply_discard(self) -> None:
        # `force` (skip confirmation) is handled by callers; _cleanup_worktree
        # itself only honors discard_dirty. A loss still refuses here.
        with ExitStack() as stack:
            mocks = self._common_patches(stack, losses=["3 local commit(s) not merged into main"])
            result = _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main", discard_dirty=False)
        assert result is False
        mocks["remove"].assert_not_called()

    def test_proceeds_with_discard_dirty(self) -> None:
        with ExitStack() as stack:
            mocks = self._common_patches(stack, losses=["uncommitted changes (1 staged, 0, 0)"])
            result = _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main", discard_dirty=True)
        assert result is True
        mocks["remove"].assert_called_once()
        # discard_dirty escalates to a force (-D) branch delete.
        _, kwargs = mocks["delete"].call_args
        assert kwargs.get("force") is True

    def test_removes_when_no_loss(self) -> None:
        with ExitStack() as stack:
            mocks = self._common_patches(stack, losses=[])
            result = _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main")
        assert result is True
        mocks["remove"].assert_called_once()
        # No loss + no discard → prefer -d (force=False).
        _, kwargs = mocks["delete"].call_args
        assert kwargs.get("force") is False

    def test_branch_delete_refusal_is_not_force_escalated(self) -> None:
        # If `git branch -d` refuses (e.g. transient error / behind main), wade
        # must NOT auto-escalate to `-D` — that would bypass the discard_dirty
        # gate and could force-delete unmerged commits.
        from wade.git.repo import GitError

        with ExitStack() as stack:
            mocks = self._common_patches(stack, losses=[])
            mocks["delete"].side_effect = GitError("branch not fully merged")
            result = _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main", discard_dirty=False)
        # Worktree removal itself still succeeded.
        assert result is True
        # delete_branch was attempted exactly once (force=False) — never retried
        # with force=True.
        assert mocks["delete"].call_count == 1
        _, kwargs = mocks["delete"].call_args
        assert kwargs.get("force") is False

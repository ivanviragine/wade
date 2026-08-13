"""Regression tests: retargeting an existing draft PR re-roots a scaffold branch.

Editing a PR's base does not rewrite its head branch's ancestry. A scaffold branch
cut from the old base (e.g. ``develop``) keeps that base's commits, which would then
merge into the new base (``main``) once the PR is retargeted. ``bootstrap_draft_pr``
re-roots a scaffold-only branch on the new base first — loss-free — before retargeting
(#376 review). A branch with real work is left untouched (the caller confirms/aborts
that retarget); every other case that cannot be rebuilt loss-free — an unresolvable
old base, a scaffold branch checked out in a worktree, or an unresolvable new base —
aborts instead of retargeting onto a stale branch.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.git.repo import GitError
from wade.services.implementation_service.draft_pr import (
    _branch_has_real_work,
    _find_checked_out_worktree,
    _restore_scaffold_head,
    bootstrap_draft_pr,
    reroot_scaffold_branch_for_retarget,
)

_D = "wade.services.implementation_service.draft_pr"


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.project.main_branch = "main"
    cfg.project.branch_prefix = "feat"
    return cfg


def _open_pr(base: str) -> PRLookup:
    return PRLookup(
        found=True,
        pr=PRRef(number=5, url="http://x/5", state="OPEN", baseRefName=base),
    )


def _resolver(**mapping: str | None) -> Callable[[Path, str], str | None]:
    """Build a ``resolve_start_point`` stub.

    Returns ``mapping[ref]`` for a named ref (pass ``None`` to model "resolves
    nowhere"); any ref not in the mapping resolves to itself — i.e. an existing local
    branch. Robust to how many times ``resolve_start_point`` is called, unlike a
    positional ``side_effect`` list.
    """

    def _inner(_repo: Path, ref: str) -> str | None:
        return mapping.get(ref, ref)

    return _inner


class TestRerootScaffoldBranch:
    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.git_repo.push_branch")
    @patch(f"{_D}.git_branch.create_scaffold_commit")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_reroots_scaffold_only_branch(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        mock_reset: MagicMock,
        mock_scaffold: MagicMock,
        mock_push: MagicMock,
        _tip: MagicMock,
    ) -> None:
        # Scaffold-only (1 commit) and not checked out → rebuild on the new base.
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )

        assert ok is True
        mock_reset.assert_called_once_with(Path("/repo"), "feat/42-x", "main")
        mock_scaffold.assert_called_once()
        mock_push.assert_called_once_with(Path("/repo"), "feat/42-x", force=True)

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.git_repo.push_branch")
    @patch(f"{_D}.git_branch.create_scaffold_commit")
    @patch(f"{_D}.git_branch.reset_worktree_hard")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_repo.has_tracked_changes", return_value=False)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"path": "/wt", "branch": "feat/42-x"}],
    )
    def test_checked_out_scaffold_branch_rerooted_in_place(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        _dirty: MagicMock,
        mock_reset_branch: MagicMock,
        mock_reset_wt: MagicMock,
        mock_scaffold: MagicMock,
        mock_push: MagicMock,
        _tip: MagicMock,
    ) -> None:
        # Scaffold-only (1 commit) but checked out and clean — `git branch -f` can't move
        # it, so re-root it in place with a hard reset inside its worktree (never leave it
        # rooted on the old base, which would leak commits into the retargeted PR).
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is True
        mock_reset_wt.assert_called_once_with(Path("/wt"), "main")
        mock_reset_branch.assert_not_called()  # branch -f can't move a checked-out branch
        mock_scaffold.assert_called_once()
        mock_push.assert_called_once_with(Path("/repo"), "feat/42-x", force=True)

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.console")
    @patch(f"{_D}.git_branch.reset_worktree_hard")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_repo.has_tracked_changes", return_value=True)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"path": "/wt", "branch": "feat/42-x"}],
    )
    def test_checked_out_dirty_worktree_aborts(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        _dirty: MagicMock,
        mock_reset_branch: MagicMock,
        mock_reset_wt: MagicMock,
        _console: MagicMock,
        _tip: MagicMock,
    ) -> None:
        # Checked out with uncommitted tracked changes — a hard reset would discard them,
        # so abort and require the user to commit/stash or remove the worktree.
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is False
        mock_reset_wt.assert_not_called()
        mock_reset_branch.assert_not_called()

    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.commits_ahead", return_value=3)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_real_work_leaves_branch_untouched(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        mock_reset: MagicMock,
    ) -> None:
        # More than the scaffold commit beyond the old base → real work, leave as-is
        # (the caller guards/confirms this retarget separately).
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is True
        mock_reset.assert_not_called()

    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=False)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_single_non_empty_commit_left_untouched(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        _tip: MagicMock,
        mock_reset: MagicMock,
    ) -> None:
        # Exactly one commit ahead but the tip is NOT empty → real work, not WADE's
        # scaffold. Leave it untouched instead of hard-resetting the user's commit away
        # (the count alone would have wrongly treated it as scaffold-only) (#376 review).
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is True
        mock_reset.assert_not_called()

    @patch(f"{_D}.console")
    @patch(f"{_D}.git_branch.reset_branch")
    def test_unresolvable_old_base_aborts(
        self,
        mock_reset: MagicMock,
        _console: MagicMock,
    ) -> None:
        # An empty/unknown old base means we cannot prove the branch is scaffold-only.
        # Abort rather than reset (which could discard real commits) or retarget as-is
        # (which would leak the old base's commits).
        ok = reroot_scaffold_branch_for_retarget(Path("/repo"), "feat/42-x", "", "main", "42")
        assert ok is False
        mock_reset.assert_not_called()

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_repo.has_remote", return_value=False)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver(main=None))
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch(f"{_D}.console")
    def test_unresolvable_new_base_aborts(
        self,
        _console: MagicMock,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        _has_remote: MagicMock,
        mock_reset: MagicMock,
        _tip: MagicMock,
    ) -> None:
        # Old base resolves; the new base resolves nowhere and there is no remote to
        # fetch from → abort rather than retarget onto a branch we could not rebuild.
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )

        assert ok is False
        mock_reset.assert_not_called()


class TestBranchWorkSignals:
    """`_branch_has_real_work` is commits-only (the guard signal); a mere checked-out
    worktree is not divergent work — only `_find_checked_out_worktree` sees it (#376)."""

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_scaffold_only_is_not_real_work(
        self, _resolve: MagicMock, _ahead: MagicMock, _tip: MagicMock
    ) -> None:
        # One commit ahead AND that commit is empty (WADE's scaffold) → not real work.
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is False

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=False)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_single_non_empty_commit_is_real_work(
        self, _resolve: MagicMock, _ahead: MagicMock, mock_tip: MagicMock
    ) -> None:
        # One commit ahead but the tip touched the tree (amended scaffold / squash / a PR
        # opened outside WADE) → real work; a bare count would have wrongly reset it (#376).
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is True
        mock_tip.assert_called_once()

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=None)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_single_commit_emptiness_unknown_fails_closed(
        self, _resolve: MagicMock, _ahead: MagicMock, _tip: MagicMock
    ) -> None:
        # Cannot determine whether the single commit is empty → fail closed as real work
        # so an indeterminate branch is never silently hard-reset.
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is True

    @patch(f"{_D}.git_branch.tip_commit_is_empty")
    @patch(f"{_D}.git_branch.commits_ahead", return_value=0)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_zero_commits_ahead_is_not_real_work(
        self, _resolve: MagicMock, _ahead: MagicMock, mock_tip: MagicMock
    ) -> None:
        # No commits beyond base → nothing to discard; the emptiness probe is not needed.
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is False
        mock_tip.assert_not_called()

    @patch(f"{_D}.git_branch.commits_ahead", return_value=4)
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_commits_past_scaffold_is_real_work(
        self, _resolve: MagicMock, _ahead: MagicMock
    ) -> None:
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is True

    @patch(f"{_D}.git_branch.commits_ahead", side_effect=GitError("no base"))
    @patch(f"{_D}.git_branch.resolve_start_point", side_effect=_resolver())
    def test_uncomputable_count_fails_closed(self, _resolve: MagicMock, _ahead: MagicMock) -> None:
        # Indeterminate → treat as real work so the retarget is never applied silently.
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is True

    @patch(f"{_D}.git_branch.commits_ahead")
    @patch(
        f"{_D}.git_branch.resolve_start_point",
        side_effect=_resolver(**{"feat/42-x": None}),
    )
    def test_unresolvable_head_fails_closed(
        self, _resolve: MagicMock, mock_ahead: MagicMock
    ) -> None:
        # The head resolves neither locally nor on origin → can't measure → fail closed.
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is True
        mock_ahead.assert_not_called()

    @patch(f"{_D}.git_branch.tip_commit_is_empty", return_value=True)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(
        f"{_D}.git_branch.resolve_start_point",
        side_effect=_resolver(**{"feat/42-x": "origin/feat/42-x"}),
    )
    def test_remote_only_head_measured_via_origin(
        self, _resolve: MagicMock, mock_ahead: MagicMock, mock_tip: MagicMock
    ) -> None:
        # Fresh clone: the PR head lives only on origin. It is still measured (1 empty
        # commit → scaffold-only → not real work) instead of being misclassified by a
        # failed local rev-list (#376 review).
        assert _branch_has_real_work(Path("/repo"), "feat/42-x", "main") is False
        # measured against the resolved origin head, not the bare local branch name
        assert mock_ahead.call_args[0][1] == "origin/feat/42-x"
        # emptiness probed against the same resolved origin head
        assert mock_tip.call_args[0][1] == "origin/feat/42-x"

    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_not_checked_out(self, _wt: MagicMock) -> None:
        assert _find_checked_out_worktree(Path("/repo"), "feat/42-x") == (False, None)

    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"path": "/wt", "branch": "feat/42-x"}],
    )
    def test_checked_out(self, _wt: MagicMock) -> None:
        assert _find_checked_out_worktree(Path("/repo"), "feat/42-x") == (True, Path("/wt"))

    @patch("wade.git.worktree.list_worktrees", side_effect=OSError("git failed"))
    def test_worktree_read_failure_fails_closed(self, _wt: MagicMock) -> None:
        # Can't read the worktree list → fail closed as checked-out-with-unknown-path so
        # the reroot refuses rather than risking a `git branch -f` on a checked-out branch.
        assert _find_checked_out_worktree(Path("/repo"), "feat/42-x") == (True, None)


class TestBootstrapRetargetReroots:
    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=True)
    @patch(f"{_D}.git_pr.update_pr_base", return_value=True)
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_reroots_before_update_pr_base(
        self,
        _mk: MagicMock,
        mock_update: MagicMock,
        mock_reroot: MagicMock,
    ) -> None:
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result == {"number": 5, "url": "http://x/5"}
        mock_reroot.assert_called_once_with(Path("/repo"), "feat/42-x", "develop", "main", "42")
        mock_update.assert_called_once()

    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=False)
    @patch(f"{_D}.git_pr.update_pr_base")
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_aborts_when_reroot_fails(
        self,
        _mk: MagicMock,
        mock_update: MagicMock,
        _reroot: MagicMock,
    ) -> None:
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result is None  # a failed re-root aborts before the PR base is edited
        mock_update.assert_not_called()

    @patch(f"{_D}._restore_scaffold_head")
    @patch(f"{_D}._resolve_head_sha", side_effect=["oldsha", "newsha"])
    @patch(f"{_D}.git_branch.resolve_start_point", return_value="feat/42-x")
    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=True)
    @patch(f"{_D}.git_pr.update_pr_base", return_value=False)
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_restores_head_when_update_pr_base_fails(
        self,
        _mk: MagicMock,
        _update: MagicMock,
        _reroot: MagicMock,
        _resolve: MagicMock,
        _sha: MagicMock,
        mock_restore: MagicMock,
    ) -> None:
        # The reroot rewrote the head (SHA changed) but the PR-base edit failed — roll the
        # head back to its pre-reroot SHA so the remote branch and the still-old-base PR
        # don't diverge (#376 review).
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result is None
        mock_restore.assert_called_once_with(Path("/repo"), "feat/42-x", "oldsha", 5)

    @patch(f"{_D}._restore_scaffold_head")
    @patch(f"{_D}._resolve_head_sha", side_effect=["samesha", "samesha"])
    @patch(f"{_D}.git_branch.resolve_start_point", return_value="feat/42-x")
    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=True)
    @patch(f"{_D}.git_pr.update_pr_base", return_value=False)
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_no_restore_when_reroot_left_head_unchanged(
        self,
        _mk: MagicMock,
        _update: MagicMock,
        _reroot: MagicMock,
        _resolve: MagicMock,
        _sha: MagicMock,
        mock_restore: MagicMock,
    ) -> None:
        # A real-work branch is left untouched by the reroot (SHA unchanged), so a restore
        # would be a needless hard reset that could discard uncommitted work — skip it.
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result is None
        mock_restore.assert_not_called()


class TestRestoreScaffoldHead:
    @patch(f"{_D}.git_repo.push_branch")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.reset_worktree_hard")
    @patch(f"{_D}._find_checked_out_worktree", return_value=(False, None))
    def test_restores_not_checked_out(
        self,
        _find: MagicMock,
        mock_reset_wt: MagicMock,
        mock_reset_branch: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        _restore_scaffold_head(Path("/repo"), "feat/42-x", "oldsha", 5)
        mock_reset_branch.assert_called_once_with(Path("/repo"), "feat/42-x", "oldsha")
        mock_reset_wt.assert_not_called()
        mock_push.assert_called_once_with(Path("/repo"), "feat/42-x", force=True)

    @patch(f"{_D}.git_repo.push_branch")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.reset_worktree_hard")
    @patch(f"{_D}._find_checked_out_worktree", return_value=(True, Path("/wt")))
    def test_restores_checked_out_in_worktree(
        self,
        _find: MagicMock,
        mock_reset_wt: MagicMock,
        mock_reset_branch: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        # `git branch -f` can't move a checked-out branch → reset inside its worktree.
        _restore_scaffold_head(Path("/repo"), "feat/42-x", "oldsha", 5)
        mock_reset_wt.assert_called_once_with(Path("/wt"), "oldsha")
        mock_reset_branch.assert_not_called()
        mock_push.assert_called_once_with(Path("/repo"), "feat/42-x", force=True)

    @patch(f"{_D}.console")
    @patch(f"{_D}.git_repo.push_branch", side_effect=GitError("push denied"))
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}._find_checked_out_worktree", return_value=(False, None))
    def test_reports_when_restore_fails(
        self,
        _find: MagicMock,
        _reset: MagicMock,
        _push: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        # A failed restore leaves divergence needing manual cleanup — report it loudly.
        _restore_scaffold_head(Path("/repo"), "feat/42-x", "oldsha", 5)
        mock_console.error.assert_called_once()

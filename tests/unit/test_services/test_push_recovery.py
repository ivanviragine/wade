"""Regression tests for non-fast-forward push recovery (#357, C4)."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.repo import GitError
from wade.services.implementation_service.done import (
    _is_non_fast_forward,
    _push_branch_with_recovery,
)

_DONE = "wade.services.implementation_service.done"


class TestNonFastForwardDetection:
    def test_detects_rejection(self) -> None:
        assert _is_non_fast_forward("Updates were rejected because the tip is behind") is True
        assert _is_non_fast_forward("! [rejected] main -> main (non-fast-forward)") is True

    def test_non_ff_ignores_other_errors(self) -> None:
        assert _is_non_fast_forward("fatal: authentication failed") is False

    def test_hook_and_policy_rejections_are_not_non_ff(self) -> None:
        # A bare "rejected" must not classify a hook / branch-protection
        # rejection as non-fast-forward — a force-push cannot fix those, so the
        # force-with-lease recovery menu must not be offered.
        assert _is_non_fast_forward("remote: error: push rejected by branch protection") is False
        hook_rejection = "! [remote rejected] main -> main (pre-receive hook declined)"
        assert _is_non_fast_forward(hook_rejection) is False


class TestPushRecovery:
    def _patches(self, stack: ExitStack, *, is_tty: bool, select: int = 0) -> dict[str, MagicMock]:
        stack.enter_context(patch(f"{_DONE}.console"))
        stack.enter_context(patch(f"{_DONE}.git_sync"))
        stack.enter_context(patch(f"{_DONE}.git_branch.commits_ahead", return_value=1))
        mock_push = stack.enter_context(patch(f"{_DONE}.git_repo.push_branch"))
        stack.enter_context(patch(f"{_DONE}.prompts.is_tty", return_value=is_tty))
        stack.enter_context(patch(f"{_DONE}.prompts.select", return_value=select))
        return {"push": mock_push}

    def test_clean_push_succeeds(self) -> None:
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=True)
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/repo"))
        assert result is True
        mocks["push"].assert_called_once()

    def test_non_tty_never_force_pushes(self) -> None:
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=False)
            # First push raises non-FF; no further push attempts in non-TTY.
            mocks["push"].side_effect = GitError("Updates were rejected (non-fast-forward)")
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/repo"))
        assert result is False
        assert mocks["push"].call_count == 1  # never force-pushed

    def test_force_with_lease_behind_explicit_confirm(self) -> None:
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=True, select=1)  # choice 1 = force-with-lease
            # First push non-FF, second (force) succeeds.
            mocks["push"].side_effect = [
                GitError("! [rejected] (non-fast-forward)"),
                None,
            ]
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/repo"))
        assert result is True
        assert mocks["push"].call_count == 2
        _, kwargs = mocks["push"].call_args
        assert kwargs.get("force") is True

    def test_cancel_does_not_push(self) -> None:
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=True, select=2)  # choice 2 = cancel
            mocks["push"].side_effect = GitError("(non-fast-forward)")
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/repo"))
        assert result is False
        assert mocks["push"].call_count == 1  # only the initial attempt


class TestPushRecoveryMarkerLifecycle:
    """#349: the done-marker must track what actually reached the remote."""

    def _patches(self, stack: ExitStack, *, is_tty: bool, select: int = 0) -> dict[str, MagicMock]:
        stack.enter_context(patch(f"{_DONE}.console"))
        stack.enter_context(patch(f"{_DONE}.git_sync"))
        stack.enter_context(patch(f"{_DONE}.git_branch.commits_ahead", return_value=1))
        mock_push = stack.enter_context(patch(f"{_DONE}.git_repo.push_branch"))
        stack.enter_context(patch(f"{_DONE}.prompts.is_tty", return_value=is_tty))
        stack.enter_context(patch(f"{_DONE}.prompts.select", return_value=select))
        mock_write = stack.enter_context(patch(f"{_DONE}._write_done_marker"))
        mock_markers = stack.enter_context(patch(f"{_DONE}.markers"))
        return {"push": mock_push, "write": mock_write, "markers": mock_markers}

    def test_recovery_merge_rewrites_marker_for_new_tip(self) -> None:
        # A recovery merge advances the branch tip, so the marker must be
        # re-written before the retry push — else the backstop rejects it.
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=True, select=0)  # merge-and-retry
            mocks["push"].side_effect = [GitError("(non-fast-forward)"), None]
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/wt"))
        assert result is True
        # Written twice: before the initial push, and again after the merge.
        assert mocks["write"].call_count == 2
        mocks["markers"].clear_markers.assert_not_called()

    def test_push_failure_clears_marker(self) -> None:
        # If nothing reached the remote, no stale done@<sha> may linger.
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=False)  # non-FF then non-TTY bail-out
            mocks["push"].side_effect = GitError("(non-fast-forward)")
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/wt"))
        assert result is False
        mocks["markers"].clear_markers.assert_called_once_with(Path("/wt"), "done")

    def test_non_recoverable_push_failure_clears_marker(self) -> None:
        with ExitStack() as stack:
            mocks = self._patches(stack, is_tty=True)
            mocks["push"].side_effect = GitError("fatal: authentication failed")
            result = _push_branch_with_recovery(Path("/repo"), "feat/1-x", None, Path("/wt"))
        assert result is False
        mocks["markers"].clear_markers.assert_called_once_with(Path("/wt"), "done")

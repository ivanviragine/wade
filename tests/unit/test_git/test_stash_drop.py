"""Unit tests for drop_stash_by_sha's re-verify guard (#357, A1 TOCTOU).

``git`` has no drop-by-SHA primitive: drop_stash_by_sha resolves a positional
``stash@{N}`` and then drops it. Between those two commands a concurrent
``stash push`` from another worktree can shift the positions. The re-verify
step (``rev-parse --verify <ref>^{commit}``) must turn that reordering into a
no-op instead of dropping an unrelated entry.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.stash import drop_stash_by_sha

_SHA = "0123456789abcdef0123456789abcdef01234567"
_OTHER = "89abcdef0123456789abcdef0123456789abcdef"


def _proc(returncode: int, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


@patch("wade.git.stash._find_stash_ref_by_sha", return_value="stash@{0}")
@patch("wade.git.stash._run_git")
def test_drops_when_ref_still_resolves_to_sha(mock_run: MagicMock, _find: MagicMock) -> None:
    # rev-parse confirms the position still points at our stash commit → drop.
    mock_run.side_effect = [_proc(0, stdout=f"{_SHA}\n"), _proc(0)]
    assert drop_stash_by_sha(_SHA, Path("/repo")) is True
    assert mock_run.call_count == 2  # rev-parse + drop
    assert mock_run.call_args_list[1].args[:3] == ("stash", "drop", "stash@{0}")


@patch("wade.git.stash._find_stash_ref_by_sha", return_value="stash@{0}")
@patch("wade.git.stash._run_git")
def test_skips_drop_when_position_reordered(mock_run: MagicMock, _find: MagicMock) -> None:
    # A concurrent push shifted the stack: the resolved position now maps to a
    # DIFFERENT commit → no-op (never drop someone else's entry).
    mock_run.return_value = _proc(0, stdout=f"{_OTHER}\n")
    assert drop_stash_by_sha(_SHA, Path("/repo")) is False
    assert mock_run.call_count == 1  # rev-parse only; drop never issued


@patch("wade.git.stash._find_stash_ref_by_sha", return_value=None)
@patch("wade.git.stash._run_git")
def test_returns_false_when_no_matching_entry(mock_run: MagicMock, _find: MagicMock) -> None:
    # Nothing resolves to the SHA (already dropped) → False, no git calls.
    assert drop_stash_by_sha(_SHA, Path("/repo")) is False
    assert mock_run.call_count == 0

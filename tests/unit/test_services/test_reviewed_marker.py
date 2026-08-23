"""Tests that ``wade review implementation`` records the ``reviewed@<sha>`` marker."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from unittest.mock import patch

from wade.models.delegation import DelegationMode, DelegationResult
from wade.utils import markers

rds = importlib.import_module("wade.services.review_delegation_service")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@e.st")
    _git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "a")


def _head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _cap(capsys) -> str:
    """Combined stdout+stderr with whitespace collapsed (``info``→out, ``warn``→err)."""
    captured = capsys.readouterr()
    return " ".join((captured.out + "\n" + captured.err).split())


class TestMarkReviewed:
    def test_writes_marker_for_head(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo(repo)
        monkeypatch.chdir(repo)
        rds._mark_reviewed()
        assert markers.marker_present(repo, "reviewed", _head(repo))

    def test_invalidated_by_new_commit(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo(repo)
        monkeypatch.chdir(repo)
        rds._mark_reviewed()
        old = _head(repo)

        (repo / "b.txt").write_text("b\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "b")
        new = _head(repo)

        # A subsequent commit invalidates the marker: it is present for the sha
        # it was written against, but not for the new HEAD — forcing a re-review.
        assert markers.marker_present(repo, "reviewed", old) is True
        assert markers.marker_present(repo, "reviewed", new) is False


class TestReviewImplementationWritesMarker:
    def test_no_diff_path_marks_reviewed(self, tmp_path: Path) -> None:
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value=""),
            patch.object(rds, "_committed_diff_fallback", return_value=""),
            patch.object(rds, "_mark_reviewed") as mock_mark,
        ):
            result = rds.review_implementation()
        assert result.skipped is True
        mock_mark.assert_called_once()

    def test_success_path_marks_reviewed(self, tmp_path: Path) -> None:
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.PROMPT)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_mark_reviewed") as mock_mark,
        ):
            rds.review_implementation()
        mock_mark.assert_called_once()

    def test_failure_path_does_not_mark(self, tmp_path: Path) -> None:
        failure = DelegationResult(
            success=False, feedback="boom", mode=DelegationMode.HEADLESS, exit_code=1
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=failure),
            patch.object(rds, "_mark_reviewed") as mock_mark,
        ):
            rds.review_implementation()
        mock_mark.assert_not_called()


class TestRecordReviewPass:
    """The review-pass cap marker (#384): counts meaningful review attempts."""

    def test_writes_review_pass_for_head(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo(repo)
        monkeypatch.chdir(repo)
        rds._record_review_pass()
        assert markers.marker_present(repo, "review-pass", _head(repo))
        assert markers.count_review_passes(repo) == 1

    def test_returns_resulting_count_and_is_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo(repo)
        monkeypatch.chdir(repo)
        assert rds._record_review_pass() == 1
        # Same HEAD → idempotent, so the returned count does not inflate.
        assert rds._record_review_pass() == 1

    def test_returns_none_on_git_failure(self, tmp_path: Path) -> None:
        with patch.object(rds.git_repo, "get_repo_root", side_effect=rds.GitError("boom")):
            assert rds._record_review_pass() is None

    def test_success_path_records_pass(self, tmp_path: Path) -> None:
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.PROMPT)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_record_review_pass") as mock_pass,
        ):
            rds.review_implementation()
        mock_pass.assert_called_once()

    def test_headless_timeout_still_records_pass(self, tmp_path: Path) -> None:
        # A headless timeout exits non-zero (success=False, timed_out=True) and
        # writes NO `reviewed` marker — but it still consumed a review→fix cycle,
        # so the pass MUST be recorded (this is what breaks the infinite loop).
        # #366 keeps timed_out results at success=False so this accounting holds.
        failure = DelegationResult(
            success=False,
            feedback="partial review output",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=failure),
            patch.object(rds, "_record_review_pass") as mock_pass,
            patch.object(rds, "_mark_reviewed") as mock_mark,
        ):
            rds.review_implementation()
        mock_pass.assert_called_once()
        mock_mark.assert_not_called()

    def test_reviewer_launch_failure_does_not_record_pass(self, tmp_path: Path) -> None:
        """Missing credentials/sandbox launch failure must not bypass `done` later."""
        failure = DelegationResult(
            success=False,
            feedback="Not logged in · Please run /login",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=False,
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=failure),
            patch.object(rds, "_record_review_pass") as mock_pass,
            patch.object(rds, "_mark_reviewed") as mock_mark,
            patch.object(rds.console, "warn") as mock_warn,
        ):
            rds.review_implementation()
        mock_pass.assert_not_called()
        mock_mark.assert_not_called()
        warning = mock_warn.call_args.args[0]
        assert "no review-pass budget" in warning
        # This branch also covers a reviewer that launched fine and then exited
        # nonzero, so the remedy must not prescribe restoring the runtime (#462
        # review) — it names both causes and points at the reviewer output.
        assert "Restore the reviewer runtime" not in warning
        assert "nonzero exit" in warning

    def test_no_diff_path_does_not_record_pass(self, tmp_path: Path) -> None:
        # The no-diff early return writes the exact-sha `reviewed` marker (which
        # already satisfies the gate) but must NOT consume a cap slot.
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value=""),
            patch.object(rds, "_committed_diff_fallback", return_value=""),
            patch.object(rds, "_mark_reviewed"),
            patch.object(rds, "_record_review_pass") as mock_pass,
        ):
            result = rds.review_implementation()
        assert result.skipped is True
        mock_pass.assert_not_called()


class TestAnnounceReviewPassBudget:
    """`wade review implementation` surfaces the running review-pass budget (#384)."""

    def test_remaining_budget_message(self, capsys) -> None:
        rds._announce_review_pass_budget(1, 2)
        text = _cap(capsys).lower()
        assert "review pass 1 of 2" in text
        assert "1 pass left" in text
        assert "done.max_review_passes" in text

    def test_plural_passes_left(self, capsys) -> None:
        rds._announce_review_pass_budget(1, 3)
        assert "2 passes left" in _cap(capsys).lower()

    def test_cap_reached_message(self, capsys) -> None:
        rds._announce_review_pass_budget(2, 2)
        text = _cap(capsys).lower()
        assert "review pass 2 of 2" in text
        assert "reached" in text

    def test_review_implementation_forwards_the_count(self, tmp_path: Path) -> None:
        # The command wires the recorded count into the budget announcement.
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.PROMPT)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_record_review_pass", return_value=2),
            patch.object(rds, "_announce_review_pass_budget") as mock_announce,
        ):
            rds.review_implementation()
        mock_announce.assert_called_once()
        assert mock_announce.call_args.args[0] == 2  # the recorded pass count

    def test_no_announce_when_pass_not_recorded(self, tmp_path: Path) -> None:
        # A git failure yields no count → nothing to announce (no crash).
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.PROMPT)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_record_review_pass", return_value=None),
            patch.object(rds, "_announce_review_pass_budget") as mock_announce,
        ):
            rds.review_implementation()
        mock_announce.assert_not_called()

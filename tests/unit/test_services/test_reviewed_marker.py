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

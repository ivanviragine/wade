"""Shared PR finalization refuses unreadable bodies and permits a fresh retry."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import PRLookup, PRRef
from wade.models.config import ProjectConfig
from wade.models.task import Task
from wade.services.implementation_service.done import _done_via_pr
from wade.services.implementation_service.lifecycle import ReviewStatus, ReviewStatusKind
from wade.utils import markers

_DONE = "wade.services.implementation_service.done"
_SHA = "a" * 40
_TITLE = "fix: preserve the PR body"


@contextmanager
def _finalizer(
    tmp_path: Path, *, repo_fallback: bool = False
) -> Iterator[tuple[Callable[[], bool], MagicMock]]:
    """Keep real push orchestration, body transformation, and marker operations."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    (worktree / "PR-SUMMARY.md").write_text("New implementation summary.\n", encoding="utf-8")
    root = repo if repo_fallback else worktree
    calls = MagicMock()
    calls.read.return_value = None
    calls.write.return_value = True
    calls.ready.return_value = True
    provider = MagicMock()
    provider.read_task.return_value = Task(id="42", title=_TITLE)
    provider.find_parent_issue.return_value = None
    lookup = PRLookup(
        found=True,
        pr=PRRef(
            number=7, url="https://github.com/test/pull/7", title=_TITLE, state="OPEN", isDraft=True
        ),
    )

    def push(*_args: object, **_kwargs: object) -> None:
        # A successful push must observe its real pre-push completion marker.
        assert markers.marker_present(root, "done", _SHA)

    with ExitStack() as stack:
        stack.enter_context(patch(f"{_DONE}.get_provider", return_value=provider))
        # This finalizer unit fakes Git; real late-clean/knowledge gates are integration-tested.
        stack.enter_context(patch(f"{_DONE}.git_repo.is_clean", return_value=True))
        stack.enter_context(patch(f"{_DONE}.git_repo.rev_parse", return_value=_SHA))
        stack.enter_context(patch(f"{_DONE}.git_repo.push_branch", side_effect=push))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_for_branch", return_value=lookup))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_body", calls.read))
        stack.enter_context(patch(f"{_DONE}.git_pr.update_pr_body", calls.write))
        stack.enter_context(patch(f"{_DONE}.git_pr.mark_pr_ready", calls.ready))
        stack.enter_context(patch(f"{_DONE}.git_pr.create_pr", calls.create))
        stack.enter_context(patch(f"{_DONE}.remove_in_progress_label", calls.label))
        stack.enter_context(patch(f"{_DONE}._maybe_trigger_bot_reviews", calls.bots))
        stack.enter_context(patch(f"{_DONE}.console.panel", calls.panel))

        def run() -> bool:
            return _done_via_pr(
                repo_root=repo,
                branch="feat/42-body",
                issue_number="42",
                main_branch="main",
                close_issue=True,
                draft=False,
                config=ProjectConfig(),
                worktree_path=None if repo_fallback else worktree,
                review_status=ReviewStatus(
                    kind=ReviewStatusKind.REVIEWED,
                    reviewed_sha=_SHA,
                    passes=1,
                    session_type="implementation",
                ),
            )

        yield run, calls


def _assert_no_completion(calls: MagicMock) -> None:
    calls.ready.assert_not_called()
    calls.label.assert_not_called()
    calls.bots.assert_not_called()
    calls.create.assert_not_called()
    calls.panel.assert_not_called()


@pytest.mark.parametrize("repo_fallback", [False, True])
def test_unreadable_body_revokes_only_active_done_markers(
    tmp_path: Path, repo_fallback: bool
) -> None:
    with _finalizer(tmp_path, repo_fallback=repo_fallback) as (run, calls):
        root = tmp_path / ("repo" if repo_fallback else "worktree")
        other = tmp_path / "unrelated"
        other.mkdir()
        assert markers.write_marker(other, "done", _SHA)
        assert markers.write_marker(root, "reviewed", _SHA)
        receipt = root / ".wade" / "reviews" / "receipt.json"
        receipt.parent.mkdir()
        receipt.write_text("review evidence", encoding="utf-8")
        docs = root / ".wade" / "documentation.json"
        docs.write_text("documentation evidence", encoding="utf-8")
        cycle = root / ".wade" / "review-cycles" / "active.json"
        cycle.parent.mkdir()
        cycle.write_text("feedback baseline", encoding="utf-8")
        assert markers.write_marker(root, "bot-review", _SHA)

        assert run() is False

        calls.read.assert_called_once_with(tmp_path / "repo", 7)
        calls.write.assert_not_called()
        _assert_no_completion(calls)
        assert not markers.marker_present(root, "done", _SHA)
        assert markers.marker_present(other, "done", _SHA)
        assert markers.marker_present(root, "reviewed", _SHA)
        assert markers.marker_present(root, "bot-review", _SHA)
        assert receipt.read_text(encoding="utf-8") == "review evidence"
        assert docs.read_text(encoding="utf-8") == "documentation evidence"
        assert cycle.read_text(encoding="utf-8") == "feedback baseline"


def test_failed_read_then_fresh_retry_preserves_content_and_is_idempotent(tmp_path: Path) -> None:
    starting = (
        "Implements #42\n\n"
        "<!-- wade:plan:start -->\n## Plan\nKeep the planned acceptance cases.\n"
        "<!-- wade:plan:end -->\n\nUser-authored description.\n\n"
        "<!-- wade:summary:start -->\n## Summary\nOld summary.\n<!-- wade:summary:end -->\n\n"
        "<!-- wade:review-status:start -->\n## Review Status\nOld status.\n"
        "<!-- wade:review-status:end -->\n\n"
        "<!-- wade:impl-usage:start -->\n## Token Usage (Implementation)\nUsage history.\n"
        "<!-- wade:impl-usage:end -->\n"
    )
    with _finalizer(tmp_path) as (run, calls):
        root = tmp_path / "worktree"
        assert run() is False
        calls.write.assert_not_called()
        _assert_no_completion(calls)
        assert not markers.marker_present(root, "done", _SHA)

        fresh = starting.replace(
            "User-authored description.",
            "User-authored description.\nNew note added between attempts.",
        )
        calls.read.return_value = fresh
        assert run() is True
        body = calls.write.call_args.args[2]
        assert calls.read.call_count == 2
        assert body.startswith("Closes #42\n")
        for preserved in [
            "<!-- wade:plan:start -->\n## Plan\nKeep the planned acceptance cases.\n"
            "<!-- wade:plan:end -->",
            "User-authored description.\nNew note added between attempts.",
            "<!-- wade:impl-usage:start -->\n## Token Usage (Implementation)\nUsage history.\n"
            "<!-- wade:impl-usage:end -->",
        ]:
            assert preserved in body
        assert "Old summary." not in body
        assert "Old status." not in body
        assert "New implementation summary." in body
        assert "Reviewed at `aaaaaaa`" in body
        assert body.count("<!-- wade:summary:start -->") == 1
        assert body.count("<!-- wade:review-status:start -->") == 1
        calls.ready.assert_called_once_with(tmp_path / "repo", 7)
        calls.label.assert_called_once()
        calls.bots.assert_called_once()
        calls.panel.assert_called_once()
        assert markers.marker_present(root, "done", _SHA)

        calls.read.return_value = body
        assert run() is True
        assert calls.read.call_count == 3
        repeated = calls.write.call_args.args[2]
        # Existing transformation may append a trailing newline around usage.
        assert repeated.rstrip("\n") == body.rstrip("\n")
        assert repeated.count("<!-- wade:summary:start -->") == 1
        assert repeated.count("<!-- wade:review-status:start -->") == 1


def test_successfully_read_empty_body_can_finalize(tmp_path: Path) -> None:
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = ""
        assert run() is True
        body = calls.write.call_args.args[2]
        assert body.startswith("Closes #42\n")
        assert "## Summary\n\nNew implementation summary." in body
        assert "## Review Status" in body
        calls.write.assert_called_once()
        calls.ready.assert_called_once()
        calls.label.assert_called_once()
        calls.bots.assert_called_once()
        assert markers.marker_present(tmp_path / "worktree", "done", _SHA)


def test_rejected_body_write_revokes_done_marker_and_stops_completion(tmp_path: Path) -> None:
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = "User description.\n"
        calls.write.return_value = False
        assert run() is False
        calls.write.assert_called_once()
        _assert_no_completion(calls)
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)

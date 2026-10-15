"""Completion enforces actual PR readiness and retains retry evidence."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import GhCliError, PRLookup, PRRef
from wade.models.config import ProjectConfig
from wade.models.task import Task
from wade.services.implementation_service.done import _done_via_pr
from wade.services.implementation_service.lifecycle import ReviewStatus, ReviewStatusKind
from wade.utils import markers

_DONE = "wade.services.implementation_service.done"
_SHA = "a" * 40
_TITLE = "fix: preserve the PR body"


@contextmanager
def _state_finalizer(
    tmp_path: Path,
    *,
    repo_fallback: bool = False,
    initial_draft: bool = True,
    fallback: PRLookup | None = None,
) -> Iterator[tuple[Callable[[], bool], MagicMock]]:
    """Keep real push orchestration, body transformation, and marker operations."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    (worktree / "PR-SUMMARY.md").write_text("New implementation summary.\n", encoding="utf-8")
    root = repo if repo_fallback else worktree
    calls = MagicMock()
    calls.read.return_value = "User description.\n"
    calls.write.return_value = True
    calls.ready.return_value = True
    provider = MagicMock()
    provider.read_task.return_value = Task(id="42", title=_TITLE)
    provider.find_parent_issue.return_value = None
    lookup = PRLookup(
        found=True,
        pr=PRRef(
            number=7,
            url="https://github.com/test/pull/7",
            title=_TITLE,
            state="OPEN",
            isDraft=initial_draft,
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
        calls.lookup.side_effect = [PRLookup(), fallback] if fallback is not None else None
        calls.lookup.return_value = lookup
        calls.create.return_value = {"number": 7, "url": "https://github.com/test/pull/7"}
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_for_branch", calls.lookup))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_body", calls.read))
        stack.enter_context(patch(f"{_DONE}.git_pr.update_pr_body", calls.write))
        stack.enter_context(patch(f"{_DONE}.git_pr.mark_pr_ready", calls.ready))
        stack.enter_context(patch(f"{_DONE}.git_pr.create_pr", calls.create))
        stack.enter_context(patch(f"{_DONE}.remove_in_progress_label", calls.label))
        stack.enter_context(patch(f"{_DONE}._maybe_trigger_bot_reviews", calls.bots))
        stack.enter_context(patch(f"{_DONE}.console.panel", calls.panel))

        def run(*, draft: bool = False) -> bool:
            return _done_via_pr(
                repo_root=repo,
                branch="feat/42-body",
                issue_number="42",
                main_branch="main",
                close_issue=True,
                draft=draft,
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


@pytest.mark.parametrize(
    "initial_draft,opt_out", [(True, False), (True, True), (False, False), (False, True)]
)
def test_ready_state_controls_promotion_and_bots(
    tmp_path: Path, initial_draft: bool, opt_out: bool
) -> None:
    with _state_finalizer(tmp_path, initial_draft=initial_draft) as (run, calls):
        assert run(draft=opt_out)
        assert calls.ready.call_count == int(initial_draft and not opt_out)
        assert calls.bots.call_count == int(not (initial_draft and opt_out))
        calls.label.assert_called_once()
        calls.panel.assert_called_once()


@pytest.mark.parametrize("repo_fallback", [False, True])
def test_failed_promotion_revokes_only_active_done_markers(
    tmp_path: Path, repo_fallback: bool
) -> None:
    with _state_finalizer(tmp_path, repo_fallback=repo_fallback) as (run, calls):
        active = tmp_path / ("repo" if repo_fallback else "worktree")
        other = tmp_path / ("worktree" if repo_fallback else "repo")
        evidence = {
            "reviews/receipt.json": "review",
            "docs.json": "docs",
            "review-cycles/42.json": "baseline",
        }
        for path, body in evidence.items():
            target = active / ".wade" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        markers.write_marker(active, "bot-codex", _SHA)
        markers.write_marker(other, "done", _SHA)
        calls.ready.return_value = False
        assert run() is False
        assert not markers.marker_present(active, "done", _SHA)
        assert markers.marker_present(other, "done", _SHA)
        assert markers.marker_present(active, "bot-codex", _SHA)
        for path, body in evidence.items():
            assert (active / ".wade" / path).read_text() == body
        calls.label.assert_not_called()
        calls.bots.assert_not_called()
        calls.panel.assert_not_called()


def test_failed_promotion_can_retry_current_body_without_duplicate_sections(tmp_path: Path) -> None:
    with _state_finalizer(tmp_path) as (run, calls):
        calls.read.return_value = (
            "User content.\n<!-- wade:plan:start -->\n## Tasks\nKeep plan.\n"
            "<!-- wade:plan:end -->\n"
        )
        calls.ready.return_value = False
        assert not run()
        first_body = calls.write.call_args.args[2]
        calls.read.return_value = "Late user note.\n" + first_body
        calls.ready.return_value = True
        assert run()
        final = calls.write.call_args.args[2]
        assert "Late user note." in final and "Keep plan." in final
        assert final.count("<!-- wade:summary:start -->") == 1
        assert final.count("<!-- wade:review-status:start -->") == 1
        assert calls.read.call_count == 2
        calls.create.assert_not_called()
        calls.label.assert_called_once()
        calls.bots.assert_called_once()


@pytest.mark.parametrize(
    "actual_draft,opt_out,ready_ok",
    [(True, False, True), (True, False, False), (True, True, True), (False, True, True)],
)
def test_fallback_recovery_uses_actual_state(
    tmp_path: Path, actual_draft: bool, opt_out: bool, ready_ok: bool
) -> None:
    lookup = PRLookup(found=True, pr=PRRef(number=7, state="OPEN", isDraft=actual_draft))
    with _state_finalizer(tmp_path, fallback=lookup) as (run, calls):
        calls.ready.return_value = ready_ok
        expected = not (actual_draft and not opt_out and not ready_ok)
        assert run(draft=opt_out) is expected
        assert calls.ready.call_count == int(actual_draft and not opt_out)
        assert calls.bots.call_count == int(expected and not (actual_draft and opt_out))
        assert calls.label.call_count == int(expected)
        assert calls.panel.call_count == int(expected)
        calls.create.assert_called_once()
        assert calls.lookup.call_count == 2
        assert calls.lookup.call_args.kwargs["require_draft_state"] is True
        assert markers.marker_present(tmp_path / "worktree", "done", _SHA) is expected


@pytest.mark.parametrize(
    "fallback",
    [
        PRLookup(),
        PRLookup(lookup_failed=True),
        PRLookup(found=True, pr=PRRef(number=8, state="OPEN", isDraft=False)),
        PRLookup(found=True, pr=PRRef(number=7, state="CLOSED", isDraft=False)),
        PRLookup(found=True, pr=PRRef(number=7, state="MERGED", isDraft=False)),
    ],
)
@pytest.mark.parametrize("opt_out", [False, True])
def test_unknown_or_mismatched_fallback_never_completes(
    tmp_path: Path, fallback: PRLookup, opt_out: bool
) -> None:
    with _state_finalizer(tmp_path, fallback=fallback) as (run, calls):
        assert run(draft=opt_out) is False
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)
        calls.create.assert_called_once()
        calls.ready.assert_not_called()
        calls.label.assert_not_called()
        calls.bots.assert_not_called()
        calls.panel.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [GhCliError("gh unavailable"), OSError("permission denied"), ValueError("malformed response")],
)
def test_fallback_lookup_exception_revokes_done_and_retains_retry(
    tmp_path: Path, error: Exception
) -> None:
    with _state_finalizer(tmp_path) as (run, calls):
        calls.lookup.side_effect = [PRLookup(), error]
        assert not run()
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)
        calls.label.assert_not_called()
        calls.bots.assert_not_called()
        calls.panel.assert_not_called()


@pytest.mark.parametrize("error", [GhCliError("gh unavailable"), OSError("permission denied")])
def test_promotion_execution_error_is_retryable_failure(tmp_path: Path, error: Exception) -> None:
    with _state_finalizer(tmp_path) as (run, calls):
        calls.ready.side_effect = error
        assert not run()
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)
        calls.label.assert_not_called()
        calls.bots.assert_not_called()
        calls.panel.assert_not_called()

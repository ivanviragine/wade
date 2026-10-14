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
    tmp_path: Path, *, repo_fallback: bool = False, review_enabled: bool = True
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
                )
                if review_enabled
                else None,
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


@pytest.mark.parametrize(
    "usage", ["", "<!-- wade:impl-usage:start -->\nUsage.\n<!-- wade:impl-usage:end -->\n"]
)
def test_finalizer_preserves_summary_heading_inside_plan(tmp_path: Path, usage: str) -> None:
    plan = (
        "<!-- wade:plan:start -->\n"
        "## Summary\n"
        "Acceptance requirements.\n"
        "## Tasks\n"
        "Keep tasks.\n"
        "<!-- wade:plan:end -->\n"
    )
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = plan + usage
        assert run() is True
        assert plan in calls.write.call_args.args[2]


@pytest.mark.parametrize(
    "payload",
    [
        "Substantive summary.\n```python\nexample",
        "Substantive summary.\n~~~\nexample",
        "Substantive summary.\n<!-- wade:plan:start -->\nnested\n<!-- wade:plan:end -->",
    ],
)
def test_finalizer_refuses_new_malformed_summary_then_retries(tmp_path: Path, payload: str) -> None:
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = "User description.\n"
        summary = tmp_path / "worktree" / "PR-SUMMARY.md"
        summary.write_text(payload, encoding="utf-8")
        assert run() is False
        calls.write.assert_not_called()
        _assert_no_completion(calls)
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)
        summary.write_text("Repaired summary.\n", encoding="utf-8")
        assert run() is True
        body = calls.write.call_args.args[2]
        calls.read.return_value = body
        assert run() is True
        assert calls.write.call_args.args[2] == body


@pytest.mark.parametrize(
    "body",
    [
        "<!-- wade:plan:end -->",
        "<!-- wade:plan:start -->",
        "```\nUnclosed.",
        "## Summary\nOne.\n## Summary\nTwo.",
        "<!-- wade:summary:start -->\nOne.\n<!-- wade:summary:end -->\n"
        "<!-- wade:summary:start -->\nTwo.\n<!-- wade:summary:end -->",
    ],
)
def test_finalizer_refuses_malformed_current_body(tmp_path: Path, body: str) -> None:
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = body
        assert run() is False
        calls.write.assert_not_called()
        _assert_no_completion(calls)
        assert not markers.marker_present(tmp_path / "worktree", "done", _SHA)


def test_finalizer_preserves_existing_positions_and_fenced_new_summary(tmp_path: Path) -> None:
    prefix = "## Summary\nUser-authored section.\n"
    plan = "<!-- wade:plan:start -->\n## Summary\nRequirements.\n<!-- wade:plan:end -->"
    summary = "<!-- wade:summary:start -->\nOld summary.\n<!-- wade:summary:end -->"
    review = "<!-- wade:review-status:start -->\nOld review.\n<!-- wade:review-status:end -->"
    body = "Closes #42\n" + prefix + review + "\n" + plan + "\n" + summary
    payload = "New description.\n```markdown\n<!-- wade:plan:start -->\n## Summary\n```"
    with _finalizer(tmp_path) as (run, calls):
        (tmp_path / "worktree" / "PR-SUMMARY.md").write_text(payload, encoding="utf-8")
        calls.read.return_value = body
        assert run() is True
        result = calls.write.call_args.args[2]
        assert prefix in result
        assert plan in result
        assert payload in result
        assert result.index("<!-- wade:review-status:start -->") < result.index(plan)
        assert result.index(plan) < result.index("<!-- wade:summary:start -->")
        calls.read.return_value = result
        assert run() is True
        assert calls.write.call_args.args[2] == result


def test_finalizer_missing_blocks_use_first_real_usage_anchor(tmp_path: Path) -> None:
    example = (
        "```markdown\n<!-- wade:impl-usage:start -->\nExample.\n<!-- wade:impl-usage:end -->\n```\n"
    )
    usage = "<!-- wade:impl-usage:start -->\nUsage.\n<!-- wade:impl-usage:end -->"
    with _finalizer(tmp_path) as (run, calls):
        calls.read.return_value = "Closes #42\n" + example + usage + "\n" + usage
        assert run() is True
        result = calls.write.call_args.args[2]
        assert example in result
        assert result.count(usage) == 2
        assert result.index(example) < result.index("<!-- wade:summary:start -->")
        assert result.index("<!-- wade:summary:start -->") < result.index(
            "<!-- wade:review-status:start -->"
        )
        assert result.index("<!-- wade:review-status:start -->") < result.index(usage)


@pytest.mark.parametrize("marked", [False, True])
def test_empty_projection_removal_stays_within_owner(tmp_path: Path, marked: bool) -> None:
    plan = "<!-- wade:plan:start -->\n## Summary\nKeep.\n<!-- wade:plan:end -->"
    old = "## Summary\nRemove.\n"
    if marked:
        old = "<!-- wade:summary:start -->\n" + old + "<!-- wade:summary:end -->\n"
    review = "<!-- wade:review-status:start -->\nOld.\n<!-- wade:review-status:end -->"
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        (tmp_path / "worktree" / "PR-SUMMARY.md").unlink()
        calls.read.return_value = "Closes #42\n" + old + plan + "\n" + review + "\nTail."
        assert run() is True
        assert (
            calls.write.call_args.args[2]
            == "Closes #42\n" + ("\n" if marked else "") + plan + "\n\nTail."
        )


def test_finalizer_retries_repaired_remote_body_with_fresh_read(tmp_path: Path) -> None:
    with _finalizer(tmp_path) as (run, calls):
        calls.read.side_effect = ["<!-- wade:plan:start -->\nBroken.", "Fresh user note.\n"]
        assert run() is False
        assert run() is True
        assert calls.read.call_count == 2
        assert "Fresh user note." in calls.write.call_args.args[2]
        assert "Broken." not in calls.write.call_args.args[2]


def test_finalizer_bounds_legacy_summary_and_replaces_changed_projection(tmp_path: Path) -> None:
    usage = "<!-- wade:impl-usage:start -->\nUsage.\n<!-- wade:impl-usage:end -->"
    body = "Closes #42\n\n## Summary\nOld legacy text.\n\n## Requirements\nKeep.\n\n" + usage
    expected = (
        "Closes #42\n\n## Requirements\nKeep.\n\n"
        "<!-- wade:summary:start -->\n## Summary\n\nNew implementation summary.\n"
        "<!-- wade:summary:end -->\n\n" + usage
    )
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        calls.read.return_value = body
        assert run() is True
        assert calls.write.call_args.args[2] == expected
        (tmp_path / "worktree" / "PR-SUMMARY.md").write_text("Changed summary.\n", encoding="utf-8")
        calls.read.return_value = expected
        assert run() is True
        changed = expected.replace("New implementation summary.", "Changed summary.")
        assert calls.write.call_args.args[2] == changed
        calls.read.return_value = changed
        assert run() is True
        assert calls.write.call_args.args[2] == changed


@pytest.mark.parametrize("reference", ["Implements #42", "Closes #42"])
def test_new_summary_reference_examples_survive_first_done_and_repeat(
    tmp_path: Path, reference: str
) -> None:
    payload = "Description.\n```markdown\n" + reference + "\n```"
    expected = (
        "Closes #42\n\nUser description.\n\n"
        "<!-- wade:summary:start -->\n## Summary\n\n" + payload + "\n<!-- wade:summary:end -->"
    )
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        (tmp_path / "worktree" / "PR-SUMMARY.md").write_text(payload, encoding="utf-8")
        calls.read.return_value = "User description.\n"
        assert run() is True
        assert calls.write.call_args.args[2] == expected
        calls.read.return_value = expected
        assert run() is True
        assert calls.write.call_args.args[2] == expected


@pytest.mark.parametrize(
    "example",
    [
        "- Example:\n  <!-- wade:summary:start -->\n  Keep.\n  <!-- wade:summary:end -->\n",
        "- ```markdown\n  <!-- wade:summary:end -->\n  ```\n",
    ],
)
def test_finalizer_preserves_list_contained_marker_examples(tmp_path: Path, example: str) -> None:
    old = "<!-- wade:summary:start -->\nOld.\n<!-- wade:summary:end -->"
    body = "Closes #42\n" + example + "\n" + old
    expected = body.replace("\nOld.\n", "\n## Summary\n\nNew implementation summary.\n")
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        calls.read.return_value = body
        assert run() is True
        assert calls.write.call_args.args[2] == expected
        calls.read.return_value = expected
        assert run() is True
        assert calls.write.call_args.args[2] == expected


@pytest.mark.parametrize("owner", ["summary", "review-status", "legacy"])
def test_reference_in_outgoing_projection_does_not_suppress_real_closing_ref(
    tmp_path: Path, owner: str
) -> None:
    prefix = "User description.\n\n"
    tail = "## Requirements\nKeep."
    if owner == "legacy":
        old = "## Summary\nCloses #42\n\n"
    else:
        old = f"<!-- wade:{owner}:start -->\nCloses #42\n<!-- wade:{owner}:end -->\n\n"
    summary = "<!-- wade:summary:start -->\n## Summary\n\nNew implementation summary.\n<!-- wade:summary:end -->"
    body = prefix + old + tail
    if owner == "summary":
        expected = "Closes #42\n\n" + prefix + summary + "\n\n" + tail
    elif owner == "review-status":
        expected = "Closes #42\n\n" + prefix + summary + "\n\n\n\n" + tail
    else:
        expected = "Closes #42\n\n" + prefix + tail + "\n\n" + summary
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        calls.read.return_value = body
        assert run() is True
        assert calls.write.call_args.args[2] == expected
        calls.read.return_value = expected
        assert run() is True
        assert calls.write.call_args.args[2] == expected


def test_finalizer_preserves_owned_summary_after_list_setext_heading(tmp_path: Path) -> None:
    prefix = "Closes #42\n\n- Example\n  =======\nHuman text.\n"
    old = "  <!-- wade:summary:start -->\n  Old.\n  <!-- wade:summary:end -->"
    expected = (
        prefix
        + "<!-- wade:summary:start -->\n## Summary\n\nNew implementation summary.\n<!-- wade:summary:end -->"
    )
    with _finalizer(tmp_path, review_enabled=False) as (run, calls):
        calls.read.return_value = prefix + old
        assert run() is True
        assert calls.write.call_args.args[2] == expected

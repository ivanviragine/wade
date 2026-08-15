"""Unit tests for the PR-body review-status block (#367).

Covers the three pieces the feature adds:

- ``_classify_review`` — the single classifier shared by the ``done`` review-ran
  gate and the PR-body renderer (one source of truth, no branching drift).
- ``_render_review_status`` — the per-kind line, including the attempted-vs-never
  distinction and the honest pass count for both session types.
- The marker-scoped block written into both PR-body paths (existing-PR
  ``_transform`` and new-PR ``_build_pr_body``): idempotent on re-run and
  preserving content outside the markers.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.models.config import (
    AICommandConfig,
    AIConfig,
    ProjectConfig,
    ProjectSettings,
)
from wade.models.task import Task
from wade.services.implementation_service.done import _classify_review, _done_via_pr
from wade.services.implementation_service.lifecycle import (
    REVIEW_STATUS_MARKER_END,
    REVIEW_STATUS_MARKER_START,
    ReviewStatus,
    ReviewStatusKind,
    SessionType,
    _build_pr_body,
    _render_review_status,
)
from wade.services.implementation_service.usage_tracking import (
    IMPL_USAGE_MARKER_END,
    IMPL_USAGE_MARKER_START,
)
from wade.utils import markers

_DONE = "wade.services.implementation_service.done"


# ---------------------------------------------------------------------------
# _classify_review — one kind per branch, mirroring the gate's short-circuit order
# ---------------------------------------------------------------------------


class TestClassifyReview:
    def test_reviewed_when_exact_sha_marker_present(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "reviewed", "head")
        status = _classify_review(ProjectConfig(), tmp_path, "head", skip_review=False)
        assert status.kind is ReviewStatusKind.REVIEWED
        assert status.reviewed_sha == "head"

    def test_skip_flag(self, tmp_path: Path) -> None:
        status = _classify_review(ProjectConfig(), tmp_path, "head", skip_review=True)
        assert status.kind is ReviewStatusKind.SKIPPED_FLAG

    def test_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(enabled=False)))
        status = _classify_review(config, tmp_path, "head", skip_review=False)
        assert status.kind is ReviewStatusKind.DISABLED

    def test_cap_reached_impl_only(self, tmp_path: Path) -> None:
        markers.record_review_pass(tmp_path, "sha1")
        markers.record_review_pass(tmp_path, "sha2")  # cap (default 2) reached
        status = _classify_review(
            ProjectConfig(), tmp_path, "newhead", skip_review=False, session_type="implementation"
        )
        assert status.kind is ReviewStatusKind.CAP_REACHED
        assert status.passes == 2

    def test_not_reviewed_before_cap(self, tmp_path: Path) -> None:
        markers.record_review_pass(tmp_path, "sha1")  # 1 < cap 2
        status = _classify_review(
            ProjectConfig(), tmp_path, "newhead", skip_review=False, session_type="implementation"
        )
        assert status.kind is ReviewStatusKind.NOT_REVIEWED
        assert status.passes == 1

    def test_review_pr_comments_never_caps(self, tmp_path: Path) -> None:
        # The cap is impl-only: even past the limit, review-pr-comments classifies
        # as NOT_REVIEWED (the gate then plainly refuses), never CAP_REACHED.
        markers.record_review_pass(tmp_path, "sha1")
        markers.record_review_pass(tmp_path, "sha2")
        status = _classify_review(
            ProjectConfig(),
            tmp_path,
            "newhead",
            skip_review=False,
            session_type="review-pr-comments",
        )
        assert status.kind is ReviewStatusKind.NOT_REVIEWED
        assert status.passes == 2

    def test_disabled_precedes_skip_flag(self, tmp_path: Path) -> None:
        # Short-circuit order mirrors the gate: reviews-disabled wins over the
        # --skip-review hatch when both are true.
        config = ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(enabled=False)))
        status = _classify_review(config, tmp_path, "head", skip_review=True)
        assert status.kind is ReviewStatusKind.DISABLED

    def test_reviewed_precedes_skip_flag(self, tmp_path: Path) -> None:
        # The exact-sha marker is positive evidence and outranks the hatches: a
        # commit that was actually reviewed must report REVIEWED even when
        # --skip-review was also passed on this run (#367).
        markers.write_marker(tmp_path, "reviewed", "head")
        status = _classify_review(ProjectConfig(), tmp_path, "head", skip_review=True)
        assert status.kind is ReviewStatusKind.REVIEWED

    def test_reviewed_precedes_disabled(self, tmp_path: Path) -> None:
        # Even with reviews disabled in config, a genuinely-present marker for
        # this exact sha must still report REVIEWED, not DISABLED.
        markers.write_marker(tmp_path, "reviewed", "head")
        config = ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(enabled=False)))
        status = _classify_review(config, tmp_path, "head", skip_review=False)
        assert status.kind is ReviewStatusKind.REVIEWED

    def test_carries_session_type_and_passes(self, tmp_path: Path) -> None:
        markers.record_review_pass(tmp_path, "sha1")
        status = _classify_review(
            ProjectConfig(), tmp_path, "abc1234def", skip_review=True, session_type="implementation"
        )
        assert status.session_type == "implementation"
        assert status.passes == 1
        assert status.reviewed_sha == "abc1234def"

    def test_is_frozen(self, tmp_path: Path) -> None:
        status = _classify_review(ProjectConfig(), tmp_path, "head", skip_review=True)
        try:
            status.passes = 99  # type: ignore[misc]
        except (TypeError, ValueError, AttributeError):
            return
        raise AssertionError("ReviewStatus should be immutable (frozen)")


# ---------------------------------------------------------------------------
# _render_review_status — per-kind line
# ---------------------------------------------------------------------------


def _status(
    kind: ReviewStatusKind,
    *,
    passes: int = 0,
    session_type: SessionType | str = SessionType.IMPLEMENTATION,
    sha: str = "abc1234def567",
) -> ReviewStatus:
    return ReviewStatus(
        kind=kind, passes=passes, session_type=SessionType(session_type), reviewed_sha=sha
    )


class TestRenderReviewStatus:
    def test_reviewed_shows_short_sha(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.REVIEWED, sha="abc1234def567"))
        assert "## Review Status" in out
        assert "✅ Reviewed at `abc1234`" in out  # 7-char short sha
        assert "wade review implementation" in out

    def test_skipped_with_passes_is_attempted_not_never(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.SKIPPED_FLAG, passes=2))
        assert "--skip-review" in out
        assert "review attempted on 2 distinct commits" in out
        assert "not reviewed" in out
        assert "never ran" not in out

    def test_skipped_without_passes_is_never_tried(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.SKIPPED_FLAG, passes=0))
        assert "--skip-review" in out
        assert "never ran" in out

    def test_skipped_single_pass_is_singular(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.SKIPPED_FLAG, passes=1))
        assert "review attempted on 1 distinct commit" in out

    def test_cap_reached_mentions_cap_and_count(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.CAP_REACHED, passes=2))
        assert "done.max_review_passes" in out
        assert "review attempted on 2 distinct commits" in out

    def test_disabled_note(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.DISABLED))
        assert "Review gate disabled" in out
        assert "review_implementation.enabled: false" in out
        assert "final commit was not reviewed" in out

    def test_disabled_with_passes_shows_pass_history(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.DISABLED, passes=3))
        assert "Review gate disabled" in out
        assert "final commit was not reviewed" in out
        assert "Review attempted on 3 distinct commits" in out
        assert "before the gate was disabled" not in out

    def test_not_reviewed_with_passes(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.NOT_REVIEWED, passes=3))
        assert "review attempted on 3 distinct commits" in out
        assert "not reviewed" in out

    def test_not_reviewed_without_passes(self) -> None:
        out = _render_review_status(_status(ReviewStatusKind.NOT_REVIEWED, passes=0))
        assert "never ran" in out

    def test_pass_count_rendered_for_review_pr_comments_session(self) -> None:
        # The pass count is honest for BOTH session types (review-pr-comments can
        # accrue review passes too — the earlier "always 0 there" premise was
        # false). A skipped review-pr-comments session with passes still shows them.
        out = _render_review_status(
            _status(ReviewStatusKind.SKIPPED_FLAG, passes=2, session_type="review-pr-comments")
        )
        assert "review attempted on 2 distinct commits" in out


# ---------------------------------------------------------------------------
# New-PR fallback path — _build_pr_body
# ---------------------------------------------------------------------------


class TestBuildPrBodyReviewStatus:
    def test_no_block_when_review_status_absent(self) -> None:
        # Backward compatible: callers that omit review_status get no block.
        body = _build_pr_body(Task(id="42", title="feat: x"))
        assert REVIEW_STATUS_MARKER_START not in body

    def test_block_added_after_summary(self, tmp_path: Path) -> None:
        pr_summary = tmp_path / "PR-SUMMARY.md"
        pr_summary.write_text("Did the work.\n")
        body = _build_pr_body(
            Task(id="42", title="feat: x"),
            pr_summary_path=pr_summary,
            review_status=_status(ReviewStatusKind.REVIEWED, sha="deadbeefcafe"),
        )
        assert REVIEW_STATUS_MARKER_START in body
        assert REVIEW_STATUS_MARKER_END in body
        assert "✅ Reviewed at `deadbee`" in body
        # Order: Closes → Summary → Review Status
        assert body.find("Closes #42") < body.find("## Summary") < body.find("## Review Status")

    def test_block_present_even_without_summary(self, tmp_path: Path) -> None:
        # A skipped review with no PR-SUMMARY still records the skip in the body.
        body = _build_pr_body(
            Task(id="42", title="feat: x"),
            review_status=_status(ReviewStatusKind.SKIPPED_FLAG, passes=0),
        )
        assert REVIEW_STATUS_MARKER_START in body
        assert "Review skipped" in body


# ---------------------------------------------------------------------------
# Existing-PR path — _transform inside _done_via_pr
# ---------------------------------------------------------------------------


def _run_done_via_pr(
    tmp_path: Path,
    *,
    starting_body: str,
    review_status: ReviewStatus,
) -> str:
    """Drive _done_via_pr against an OPEN, non-draft PR; return the written body."""
    worktree_path = tmp_path / "wt-42"
    worktree_path.mkdir(exist_ok=True)
    (worktree_path / "PR-SUMMARY.md").write_text("Real summary of the work.\n")
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    task = Task(id="42", title="feat: proper title", body="## Tasks\n- x\n")
    config = ProjectConfig(project=ProjectSettings(main_branch="main"))
    lookup = PRLookup(
        found=True,
        pr=PRRef(
            number=7,
            url="https://github.com/test/pull/7",
            title="feat: proper title",  # matches issue → no title sync
            state="OPEN",
            isDraft=False,
        ),
    )
    captured: dict[str, str] = {}

    with ExitStack() as stack:
        mock_get_provider = stack.enter_context(patch(f"{_DONE}.get_provider"))
        stack.enter_context(patch(f"{_DONE}._push_branch_with_recovery", return_value=True))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_for_branch", return_value=lookup))
        stack.enter_context(patch(f"{_DONE}.git_pr.get_pr_body", return_value=starting_body))

        def _capture(_repo: Path, _num: int, body: str) -> bool:
            captured["body"] = body
            return True

        stack.enter_context(patch(f"{_DONE}.git_pr.update_pr_body", side_effect=_capture))
        stack.enter_context(patch(f"{_DONE}.remove_in_progress_label"))

        provider = MagicMock()
        provider.read_task.return_value = task
        provider.find_parent_issue.return_value = None
        mock_get_provider.return_value = provider

        result = _done_via_pr(
            repo_root=repo_root,
            branch="feat/42-x",
            issue_number="42",
            main_branch="main",
            close_issue=True,
            draft=False,
            config=config,
            worktree_path=worktree_path,
            review_status=review_status,
        )
    assert result is True
    return captured["body"]


class TestReviewStatusBlockExistingPr:
    def test_block_written_and_preserves_outside_content(self, tmp_path: Path) -> None:
        starting = "Implements #42\n\nA reviewer's concurrent note.\n"
        body = _run_done_via_pr(
            tmp_path,
            starting_body=starting,
            review_status=_status(ReviewStatusKind.SKIPPED_FLAG, passes=0),
        )
        assert REVIEW_STATUS_MARKER_START in body
        assert "Review skipped" in body
        # Content outside wade's markers survives.
        assert "A reviewer's concurrent note." in body

    def test_block_before_impl_usage(self, tmp_path: Path) -> None:
        starting = (
            "Implements #42\n\n"
            f"{IMPL_USAGE_MARKER_START}\n## Token Usage (Implementation)\n{IMPL_USAGE_MARKER_END}\n"
        )
        body = _run_done_via_pr(
            tmp_path,
            starting_body=starting,
            review_status=_status(ReviewStatusKind.REVIEWED, sha="abc1234def"),
        )
        # Ordering: summary → review-status → impl-usage.
        assert body.find("## Summary") < body.find("## Review Status")
        assert body.find("## Review Status") < body.find(IMPL_USAGE_MARKER_START)

    def test_idempotent_on_rerun(self, tmp_path: Path) -> None:
        # Re-running done replaces its own block in place — never duplicates it.
        first = _run_done_via_pr(
            tmp_path,
            starting_body="Implements #42\n\nkeep me\n",
            review_status=_status(ReviewStatusKind.REVIEWED, sha="abc1234def"),
        )
        second = _run_done_via_pr(
            tmp_path,
            starting_body=first,
            review_status=_status(ReviewStatusKind.REVIEWED, sha="abc1234def"),
        )
        assert second.count(REVIEW_STATUS_MARKER_START) == 1
        assert second.count(REVIEW_STATUS_MARKER_END) == 1
        assert "keep me" in second

    def test_rerun_updates_status_line(self, tmp_path: Path) -> None:
        # A second done with a different outcome replaces the line (not appends).
        first = _run_done_via_pr(
            tmp_path,
            starting_body="Implements #42\n\nkeep me\n",
            review_status=_status(ReviewStatusKind.REVIEWED, sha="abc1234def"),
        )
        second = _run_done_via_pr(
            tmp_path,
            starting_body=first,
            review_status=_status(ReviewStatusKind.SKIPPED_FLAG, passes=1),
        )
        assert second.count(REVIEW_STATUS_MARKER_START) == 1
        assert "Review skipped" in second
        assert "✅ Reviewed" not in second

"""Regression tests for marker-bounded body updates (issue #357, A4/A5)."""

from __future__ import annotations

from wade.services.implementation_service.usage_tracking import (
    IMPL_USAGE_MARKER_END,
    IMPL_USAGE_MARKER_START,
)
from wade.utils.body_markers import (
    GITHUB_BODY_MAX,
    build_marked_block,
    enforce_body_budget,
    update_body_preserving_markers,
    upsert_marked_block,
)

START = "<!-- wade:test:start -->"
END = "<!-- wade:test:end -->"


class TestUpsertMarkedBlock:
    def test_concurrent_edit_outside_markers_survives(self) -> None:
        body = (
            "A reviewer's note added concurrently.\n\n"
            f"{START}\nold wade content\n{END}\n\n"
            "Trailing user content."
        )
        new = upsert_marked_block(body, START, END, "fresh wade content")
        assert "A reviewer's note added concurrently." in new
        assert "Trailing user content." in new
        assert "fresh wade content" in new
        assert "old wade content" not in new

    def test_replaces_without_duplicating(self) -> None:
        body = f"{START}\nv1\n{END}\n"
        once = upsert_marked_block(body, START, END, "v2")
        twice = upsert_marked_block(once, START, END, "v3")
        assert twice.count(START) == 1
        assert twice.count(END) == 1
        assert "v3" in twice
        assert "v1" not in twice and "v2" not in twice

    def test_empty_inner_removes_block(self) -> None:
        body = f"keep me\n\n{START}\ncontent\n{END}\n"
        new = upsert_marked_block(body, START, END, "")
        assert "keep me" in new
        assert START not in new


def _impl_block(*sessions: str) -> str:
    inner = "## Token Usage (Implementation)\n\n" + "\n\n".join(sessions)
    return build_marked_block(IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END, inner)


class TestEnforceBodyBudget:
    def test_under_limit_is_unchanged(self) -> None:
        body = "short body"
        warnings: list[str] = []
        out = enforce_body_budget(body, warn=warnings.append, label="x")
        assert out == body
        assert warnings == []

    def test_over_limit_drops_oldest_session_and_warns(self) -> None:
        # Oldest session is huge; newest is small. Body exceeds the cap.
        old = "### Session 1\n\n" + ("x" * (GITHUB_BODY_MAX + 100))
        new = "### Session 2\n\nrecent"
        body = "keep\n\n" + _impl_block(old, new)
        assert len(body) > GITHUB_BODY_MAX

        warnings: list[str] = []
        out = enforce_body_budget(body, warn=warnings.append, label="PR body")

        assert len(out) <= GITHUB_BODY_MAX
        assert "### Session 2" in out  # newest kept
        assert "### Session 1" not in out  # oldest dropped
        assert "keep" in out  # non-usage content preserved
        assert warnings  # a user-visible warning was emitted
        assert "Dropped content" in warnings[0]

    def test_trim_preserves_block_position(self) -> None:
        # A budget trim must replace the block content IN PLACE, not relocate the
        # whole block to the end of the body (which reorders it past trailing
        # content and the other usage block).
        old = "### Session 1\n\n" + ("x" * (GITHUB_BODY_MAX + 100))
        new = "### Session 2\n\nrecent"
        body = _impl_block(old, new) + "\n\nTRAILING SENTINEL"
        assert len(body) > GITHUB_BODY_MAX

        out = enforce_body_budget(body, warn=lambda _m: None, label="x")

        assert len(out) <= GITHUB_BODY_MAX
        assert "### Session 2" in out  # newest kept
        assert "### Session 1" not in out  # oldest dropped
        # The block stays BEFORE the trailing content — not moved to the end.
        assert out.index(IMPL_USAGE_MARKER_END) < out.index("TRAILING SENTINEL")


class TestUpdateBodyPreservingMarkers:
    def test_reads_then_writes_transformed(self) -> None:
        written: dict[str, str] = {}

        ok = update_body_preserving_markers(
            read_body=lambda: f"outside\n\n{START}\nold\n{END}\n",
            write_body=lambda b: (written.__setitem__("body", b), True)[1],
            transform=lambda b: upsert_marked_block(b, START, END, "new"),
        )
        assert ok is True
        assert "outside" in written["body"]
        assert "new" in written["body"]
        assert "old" not in written["body"]

    def test_returns_false_when_body_unreadable(self) -> None:
        called = {"write": False}
        ok = update_body_preserving_markers(
            read_body=lambda: None,
            write_body=lambda b: called.__setitem__("write", True) or True,
            transform=lambda b: b,
        )
        assert ok is False
        assert called["write"] is False

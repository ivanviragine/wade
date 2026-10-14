"""Tests for markdown parsing utilities."""

from __future__ import annotations

from typing import ClassVar

import pytest

from wade.utils.markdown import (
    MarkdownStructureError,
    append_session_to_body,
    build_sessions_block,
    extract_all_sections,
    extract_marker_block,
    extract_section,
    extract_title,
    has_marker_block,
    parse_sessions_from_body,
    remove_marker_block,
    replace_owned_section,
    scan_markdown_structure,
    strip_legacy_section,
)


class TestExtractTitle:
    def test_basic(self) -> None:
        assert extract_title("# Hello World\n\nSome text") == "Hello World"

    def test_no_title(self) -> None:
        assert extract_title("Just text\nNo heading") is None

    def test_ignores_h2(self) -> None:
        assert extract_title("## Not a title\n# Real Title") == "Real Title"


class TestExtractSection:
    def test_basic(self) -> None:
        content = "# Title\n\n## Context\n\nHello world\n\n## Tasks\n\n- [ ] Do it\n"
        assert extract_section(content, "Context") == "Hello world"

    def test_case_insensitive(self) -> None:
        content = "## COMPLEXITY\n\neasy\n"
        assert extract_section(content, "complexity") == "easy"

    def test_not_found(self) -> None:
        assert extract_section("# Title\n", "Missing") is None

    def test_multiline(self) -> None:
        content = "## Tasks\n\n- [ ] First\n- [ ] Second\n\n## End\n"
        result = extract_section(content, "Tasks")
        assert result is not None
        assert "First" in result
        assert "Second" in result


class TestExtractAllSections:
    def test_multiple(self) -> None:
        content = "# Title\n\n## Context\n\nHello\n\n## Tasks\n\n- Do it\n"
        sections = extract_all_sections(content)
        assert "context" in sections
        assert "tasks" in sections
        assert sections["context"] == "Hello"


class TestMarkerBlock:
    START = "<!-- wade:start -->"
    END = "<!-- wade:end -->"

    def test_has_markers(self) -> None:
        content = f"before\n{self.START}\nmiddle\n{self.END}\nafter"
        assert has_marker_block(content, self.START, self.END)

    def test_no_markers(self) -> None:
        assert not has_marker_block("no markers here", self.START, self.END)

    def test_extract(self) -> None:
        content = f"before\n{self.START}\nmiddle\n{self.END}\nafter"
        result = extract_marker_block(content, self.START, self.END)
        assert result == "middle"

    def test_remove(self) -> None:
        content = f"before\n{self.START}\nmiddle\n{self.END}\nafter"
        result = remove_marker_block(content, self.START, self.END)
        assert "middle" not in result
        assert "before" in result
        assert "after" in result

    def test_remove_blank_line_normalization(self) -> None:
        """Before and after content should be separated by exactly two newlines."""
        content = f"before\n\n{self.START}\nmiddle\n{self.END}\n\nafter"
        result = remove_marker_block(content, self.START, self.END)
        assert result == "before\n\nafter"

    def test_remove_block_at_end(self) -> None:
        """Block at end of content: result ends with single newline."""
        content = f"before\n\n{self.START}\nmiddle\n{self.END}\n"
        result = remove_marker_block(content, self.START, self.END)
        assert result == "before\n"

    def test_remove_block_only_content(self) -> None:
        """Content is only the block: result is empty string."""
        content = f"{self.START}\nmiddle\n{self.END}\n"
        result = remove_marker_block(content, self.START, self.END)
        assert result == ""

    def test_remove_marker_not_found(self) -> None:
        """Missing markers: content returned unchanged."""
        content = "no markers here"
        result = remove_marker_block(content, self.START, self.END)
        assert result == content

    def test_remove_idempotent(self) -> None:
        """Removing twice produces same result as removing once."""
        content = f"before\n{self.START}\nmiddle\n{self.END}\nafter"
        once = remove_marker_block(content, self.START, self.END)
        twice = remove_marker_block(once, self.START, self.END)
        assert once == twice


# ---------------------------------------------------------------------------
# Sessions block helpers
# ---------------------------------------------------------------------------


class TestSessionsBlock:
    SESSION: ClassVar[dict[str, str]] = {
        "phase": "Plan",
        "ai_tool": "claude",
        "session_id": "claude --resume abc-123",
    }
    SESSION2: ClassVar[dict[str, str]] = {
        "phase": "Implement",
        "ai_tool": "claude",
        "session_id": "claude --resume def-456",
    }

    def test_build_sessions_block_renders_table(self) -> None:
        block = build_sessions_block([self.SESSION])
        assert "## AI Sessions" in block
        assert "| Phase | Tool | Session |" in block
        assert "| Plan |" in block
        assert "`claude`" in block
        assert "`claude --resume abc-123`" in block

    def test_build_sessions_block_multiple_rows(self) -> None:
        block = build_sessions_block([self.SESSION, self.SESSION2])
        assert "| Plan |" in block
        assert "| Implement |" in block

    def test_build_sessions_block_empty(self) -> None:
        block = build_sessions_block([])
        assert "## AI Sessions" in block
        assert "| --- | --- | --- |" in block
        # No data rows
        lines = [
            ln
            for ln in block.splitlines()
            if ln.startswith("| ") and "---" not in ln and "Phase" not in ln
        ]
        assert lines == []

    def test_parse_sessions_from_body_empty_body(self) -> None:
        assert parse_sessions_from_body("no block here") == []

    def test_parse_sessions_from_body_parses_rows(self) -> None:
        block = build_sessions_block([self.SESSION, self.SESSION2])
        body = f"Some content\n\n{block}\n"
        rows = parse_sessions_from_body(body)
        assert len(rows) == 2
        assert rows[0]["phase"] == "Plan"
        assert rows[0]["ai_tool"] == "claude"
        assert rows[0]["session_id"] == "claude --resume abc-123"
        assert rows[1]["phase"] == "Implement"

    def test_parse_sessions_skips_header(self) -> None:
        block = build_sessions_block([self.SESSION])
        rows = parse_sessions_from_body(block)
        # Only data rows, no header
        assert all(row["phase"] not in {"Phase", "---"} for row in rows)
        assert len(rows) == 1

    def test_append_session_to_empty_body(self) -> None:
        body = "Some issue description.\n"
        result = append_session_to_body(body, "Plan", "claude", "claude --resume abc-123")
        assert "## AI Sessions" in result
        assert "claude --resume abc-123" in result
        assert "Some issue description." in result

    def test_append_session_accumulates(self) -> None:
        body = "Description\n"
        body = append_session_to_body(body, "Plan", "claude", "claude --resume abc-123")
        body = append_session_to_body(body, "Implement", "claude", "claude --resume def-456")
        rows = parse_sessions_from_body(body)
        assert len(rows) == 2
        assert rows[0]["phase"] == "Plan"
        assert rows[1]["phase"] == "Implement"

    def test_append_session_idempotent(self) -> None:
        body = "Description\n"
        body = append_session_to_body(body, "Plan", "claude", "claude --resume abc-123")
        body_again = append_session_to_body(body, "Plan", "claude", "claude --resume abc-123")
        assert body == body_again
        rows = parse_sessions_from_body(body)
        assert len(rows) == 1

    def test_append_session_preserves_existing_content(self) -> None:
        body = "Original description.\n\nMore text.\n"
        result = append_session_to_body(body, "Plan", "antigravity-cli", "Session ID: 123")
        assert "Original description." in result
        assert "More text." in result
        assert "Session ID: 123" in result


@pytest.mark.parametrize(
    "name", ["plan", "sessions", "deps", "impl-usage", "review-usage", "future-block"]
)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_structural_migration_preserves_managed_slices(name: str, newline: str) -> None:
    body = newline.join(
        [
            f"<!-- wade:{name}:start -->",
            "## Summary",
            "Keep requirements.",
            f"<!-- wade:{name}:end -->",
            "Tail without final newline",
        ]
    )
    assert strip_legacy_section(body, "Summary") == body


@pytest.mark.parametrize(
    "barrier",
    [
        "# Tasks\nKeep.",
        "## Tasks\nKeep.",
        "<!-- wade:future:start -->\nKeep.\n<!-- wade:future:end -->",
        "```text\nKeep.\n```",
        "~~~text\nKeep.\n~~~~",
    ],
)
def test_legacy_migration_stops_at_first_barrier(barrier: str) -> None:
    body = "Intro.\n\n## Summary\nOld.\n### Old detail\nRemove.\n" + barrier + "\nTail."
    assert strip_legacy_section(body, "Summary") == "Intro.\n\n" + barrier + "\nTail."


@pytest.mark.parametrize(
    "example",
    [
        "> ## Summary\nQuoted.",
        "- ## Summary\nList.",
        "  ## Summary\nList continuation.",
        "    ## Summary\nIndented code.",
        "## Summary Notes\nKeep.",
        "```markdown\n## Summary\n<!-- wade:plan:end -->\n```",
        "   ~~~~markdown\n## Summary\n~~~\n   ~~~~~",
    ],
)
def test_lookalikes_do_not_migrate(example: str) -> None:
    assert strip_legacy_section(example, "Summary") == example


@pytest.mark.parametrize(
    "body",
    [
        "<!-- wade:plan:end -->",
        "<!-- wade:plan:start -->",
        "<!-- wade:a:start -->\n<!-- wade:b:end -->",
        "<!-- wade:a:start -->\n<!-- wade:b:start -->\n<!-- wade:b:end -->\n<!-- wade:a:end -->",
        "```\n~~~",
        "~~~~\n~~~",
        "## Summary\nA.\n## Summary\nB.",
    ],
)
def test_unsafe_structure_refuses_legacy_migration(body: str) -> None:
    with pytest.raises(MarkdownStructureError):
        strip_legacy_section(body, "Summary")


def test_owned_pair_is_authoritative_and_stays_in_place() -> None:
    body = (
        "## Summary\n"
        "User section.\n"
        "<!-- wade:summary:start -->\n"
        "Old.\n"
        "<!-- wade:summary:end -->\n"
        "## Tasks\n"
        "Keep."
    )
    expected = (
        "## Summary\n"
        "User section.\n"
        "<!-- wade:summary:start -->\n"
        "New.\n"
        "<!-- wade:summary:end -->\n"
        "## Tasks\n"
        "Keep."
    )
    assert (
        replace_owned_section(body, name="summary", heading="Summary", content="New.") == expected
    )
    assert (
        replace_owned_section(body, name="summary", heading="Summary", content="")
        == "## Summary\nUser section.\n\n## Tasks\nKeep."
    )


def test_duplicate_owned_pairs_refused_but_unrelated_repeats_opaque() -> None:
    block = "<!-- wade:future:start -->\n## Summary\nKeep.\n<!-- wade:future:end -->\n"
    assert strip_legacy_section(block + block, "Summary") == block + block
    owned = block.replace("future", "summary")
    with pytest.raises(MarkdownStructureError, match="duplicate"):
        replace_owned_section(owned + owned, name="summary", heading="Summary", content="New.")


def test_owned_insertion_ignores_fenced_anchor_and_preserves_crlf() -> None:
    prefix = (
        "```\r\n"
        "<!-- wade:impl-usage:start -->\r\n"
        "example\r\n"
        "<!-- wade:impl-usage:end -->\r\n"
        "```\r\n"
        "Tail.\r\n"
    )
    real = "<!-- wade:impl-usage:start -->\r\nReal usage.\r\n<!-- wade:impl-usage:end -->"
    result = replace_owned_section(
        prefix + real, name="summary", heading="Summary", content="New.", before=("impl-usage",)
    )
    assert (
        result
        == prefix
        + "\r\n<!-- wade:summary:start -->\nNew.\n<!-- wade:summary:end -->\r\n\r\n"
        + real
    )
    assert (
        replace_owned_section(
            result, name="summary", heading="Summary", content="New.", before=("impl-usage",)
        )
        == result
    )


def test_scanner_supports_matching_longer_fences_and_opaque_marker_examples() -> None:
    body = "```text\n<!-- wade:plan:start -->\n````\n  ~~~text\n<!-- wade:plan:end -->\n  ~~~~"
    structure = scan_markdown_structure(body)
    assert len(structure.fences) == 2
    assert structure.blocks == []


def test_legacy_crlf_removal_preserves_unrelated_tail_without_final_newline() -> None:
    body = "Prefix.\r\n\r\n## Summary\r\nOld.\r\n## Tasks\r\nKeep."
    assert strip_legacy_section(body, "Summary") == "Prefix.\r\n\r\n## Tasks\r\nKeep."

"""Tests for markdown parsing utilities."""

from __future__ import annotations

from typing import ClassVar

from wade.utils.markdown import (
    append_session_to_body,
    build_sessions_block,
    extract_all_sections,
    extract_marker_block,
    extract_section,
    extract_title,
    has_marker_block,
    parse_sessions_from_body,
    remove_marker_block,
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
        result = append_session_to_body(body, "Plan", "gemini", "Session ID: 123")
        assert "Original description." in result
        assert "More text." in result
        assert "Session ID: 123" in result

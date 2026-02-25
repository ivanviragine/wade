"""Tests for markdown parsing utilities."""

from __future__ import annotations

from ghaiw.utils.markdown import (
    extract_all_sections,
    extract_marker_block,
    extract_section,
    extract_title,
    has_marker_block,
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
    START = "<!-- ghaiw:start -->"
    END = "<!-- ghaiw:end -->"

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

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
        "#\nKeep.",
        "##\nKeep.",
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
        == "## Summary\nUser section.\n<!-- wade:summary:start -->\n\n"
        "<!-- wade:summary:end -->\n## Tasks\nKeep."
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


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_non_markdown_line_separators_do_not_create_legacy_headings(separator: str) -> None:
    body = "User text." + separator + "## Summary\nKeep this paragraph."
    assert strip_legacy_section(body, "Summary") == body


@pytest.mark.parametrize("name", ["summary", "deps"])
@pytest.mark.parametrize(
    "example",
    [
        "- Example:\n  <!-- wade:{name}:start -->\n  Keep.\n  <!-- wade:{name}:end -->\n",
        "-\n  <!-- wade:{name}:start -->\n  Keep.\n  <!-- wade:{name}:end -->\n",
        "-\t```markdown\n\t<!-- wade:{name}:end -->\n\t```\n",
        "1. Example:\n   <!-- wade:{name}:start -->\n   Keep.\n   <!-- wade:{name}:end -->\n",
        "- ```markdown\n  <!-- wade:{name}:end -->\n  ```\n",
        "1. ~~~markdown\n   <!-- wade:{name}:end -->\n   ~~~~\n",
        "- Outer\n  - ```markdown\n    <!-- wade:{name}:end -->\n    ```\n"
        "  <!-- wade:{name}:start -->\n  Keep.\n  <!-- wade:{name}:end -->\n",
        "- Paragraph\nlazy continuation\n  <!-- wade:{name}:start -->\n"
        "  Keep.\n  <!-- wade:{name}:end -->\n",
    ],
)
def test_list_examples_are_not_owned_blocks(name: str, example: str) -> None:
    prefix = example.format(name=name) + "\n"
    old = f"<!-- wade:{name}:start -->\nOld.\n<!-- wade:{name}:end -->"
    expected = prefix + f"<!-- wade:{name}:start -->\nNew.\n<!-- wade:{name}:end -->"
    assert replace_owned_section(prefix + old, name=name, heading=None, content="New.") == expected


def test_unclosed_list_fence_refuses_mutation() -> None:
    with pytest.raises(MarkdownStructureError, match="unclosed code fence"):
        replace_owned_section(
            "- ```markdown\n  Keep.", name="summary", heading=None, content="New."
        )


@pytest.mark.parametrize("separator", ["- - -", "* * *", "_ _ _"])
def test_thematic_break_is_not_a_list_container(separator: str) -> None:
    body = separator + "\n  <!-- wade:summary:start -->\nOld.\n  <!-- wade:summary:end -->"
    assert replace_owned_section(body, name="summary", heading=None, content="New.") == (
        separator + "\n<!-- wade:summary:start -->\nNew.\n<!-- wade:summary:end -->"
    )


def test_dedented_fence_cannot_close_a_list_fence() -> None:
    with pytest.raises(MarkdownStructureError, match="unclosed code fence"):
        replace_owned_section("- ```\n```", name="summary", heading=None, content="New.")


@pytest.mark.parametrize(
    "prefix", ["-\n", "- ```\n  Code.\n  ```\n", "- Paragraph.\n\n      Code.\n"]
)
def test_dedented_paragraph_after_nonparagraph_list_content_ends_container(prefix: str) -> None:
    prefix += "Human text.\n"
    old = "  <!-- wade:summary:start -->\nOld.\n  <!-- wade:summary:end -->"
    expected = prefix + "<!-- wade:summary:start -->\nNew.\n<!-- wade:summary:end -->"
    assert (
        replace_owned_section(prefix + old, name="summary", heading=None, content="New.")
        == expected
    )


@pytest.mark.parametrize("lookalike", ["2. Not a list", "-"])
def test_noninterrupting_list_lookalikes_do_not_hide_owned_blocks(lookalike: str) -> None:
    prefix = "Paragraph.\n" + lookalike + "\n"
    old = "   <!-- wade:summary:start -->\nOld.\n   <!-- wade:summary:end -->"
    expected = prefix + "<!-- wade:summary:start -->\nNew.\n<!-- wade:summary:end -->"
    assert (
        replace_owned_section(prefix + old, name="summary", heading=None, content="New.")
        == expected
    )


@pytest.mark.parametrize("name", ["summary", "deps"])
@pytest.mark.parametrize("underline", ["=======", "---", "-"])
@pytest.mark.parametrize("payload_indent", ["", "  "])
def test_setext_heading_ends_lazy_list_paragraph(
    name: str, underline: str, payload_indent: str
) -> None:
    prefix = "- Example\n  " + underline + "\nHuman text.\n"
    old = f"  <!-- wade:{name}:start -->\n{payload_indent}Old.\n  <!-- wade:{name}:end -->"
    expected = prefix + f"<!-- wade:{name}:start -->\nNew.\n<!-- wade:{name}:end -->"
    assert replace_owned_section(prefix + old, name=name, heading=None, content="New.") == expected


@pytest.mark.parametrize("name,heading", [("summary", "Summary"), ("deps", "Dependencies")])
@pytest.mark.parametrize("human_count", [1, 2])
def test_empty_removal_retains_ownership_across_retries(
    name: str, heading: str, human_count: int
) -> None:
    human = (f"## {heading}\nHuman requirement.\n") * human_count
    old = f"<!-- wade:{name}:start -->\nOld projection.\n<!-- wade:{name}:end -->"
    expected = human + f"<!-- wade:{name}:start -->\n\n<!-- wade:{name}:end -->"
    once = replace_owned_section(human + old, name=name, heading=heading, content="")
    assert once == expected
    assert replace_owned_section(once, name=name, heading=heading, content="") == expected
    assert replace_owned_section(once, name=name, heading=heading, content="New.") == (
        human + f"<!-- wade:{name}:start -->\nNew.\n<!-- wade:{name}:end -->"
    )


@pytest.mark.parametrize("name", ["summary", "deps"])
@pytest.mark.parametrize("quote", ["> Example", "> > Nested example", "> Example\n  > continued"])
def test_lazy_blockquote_list_continuations_preserve_marker_examples(name: str, quote: str) -> None:
    example = (
        f"- {quote}\nlazy continuation\n"
        f"  <!-- wade:{name}:start -->\n  Keep.\n  <!-- wade:{name}:end -->\n"
    )
    assert scan_markdown_structure(example).blocks == []
    result = replace_owned_section(example, name=name, heading=None, content="New.")
    assert result == example + f"\n<!-- wade:{name}:start -->\nNew.\n<!-- wade:{name}:end -->"
    assert replace_owned_section(result, name=name, heading=None, content="New.") == result


@pytest.mark.parametrize("quote", ["> # Heading", ">", ">     Code.", "> Text.\n  > ==="])
def test_nonparagraph_blockquotes_end_lazy_list_context(quote: str) -> None:
    prefix = f"- {quote}\nHuman text.\n"
    old = "  <!-- wade:summary:start -->\nOld.\n  <!-- wade:summary:end -->"
    assert replace_owned_section(prefix + old, name="summary", heading=None, content="New.") == (
        prefix + "<!-- wade:summary:start -->\nNew.\n<!-- wade:summary:end -->"
    )


@pytest.mark.parametrize("heading", ["Summary", "Dependencies"])
@pytest.mark.parametrize(
    "context",
    [
        "- Text.\n  >     Code.\nHuman text.\n",
        "- Text.\n  > >     Code.\nHuman text.\n",
        "- Outer.\n  - Text.\n    >     Code.\nHuman text.\n",
        "- Text.\n  > # Heading\nHuman text.\n",
        "- Text.\n  >\nHuman text.\n",
        "- Text.\n  > ```\n  > Code.\n  > ```\nHuman text.\n",
        "- Text.\n  > Quote.\n\nHuman text.\n",
    ],
)
def test_container_boundaries_cannot_hide_following_plan(heading: str, context: str) -> None:
    plan = "  <!-- wade:plan:start -->\n  Requirements.\n  <!-- wade:plan:end -->"
    body = f"## {heading}\nLegacy.\n" + context + plan
    expected = "  > ```\n  > Code.\n  > ```\nHuman text.\n" + plan if "```" in context else plan
    assert strip_legacy_section(body, heading) == expected
    assert scan_markdown_structure(body).owned("plan") is not None


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("container", ["", "- ", "> ", "- > "])
@pytest.mark.parametrize("fence", ["```", "~~~~"])
@pytest.mark.parametrize("content", ["", "Keep.\n", "<!-- wade:plan:end -->\n\n"])
def test_container_fence_closure_contract(
    newline: str, container: str, fence: str, content: str
) -> None:
    continuation = container.replace("- ", "  ")
    middle = "".join(continuation + line + "\n" for line in content.split("\n")[:-1])
    unclosed = (container + fence + "\n" + middle).replace("\n", newline)
    closed = unclosed + continuation + fence + fence[0]
    structure = scan_markdown_structure(closed)
    assert len(structure.fences) == 1
    assert structure.fences[0].start == 0
    assert structure.fences[0].end == len(closed)
    assert structure.blocks == []
    with pytest.raises(MarkdownStructureError, match="unclosed code fence"):
        scan_markdown_structure(unclosed)


def test_parser_nesting_limit_refuses_instead_of_hiding_owned_blocks() -> None:
    body = "> " * 110 + "Nested quote.\n\n<!-- wade:plan:start -->\nKeep.\n<!-- wade:plan:end -->"
    with pytest.raises(MarkdownStructureError, match="nesting exceeds"):
        scan_markdown_structure(body)


@pytest.mark.parametrize("name", ["summary", "deps"])
def test_quoted_paragraph_continuation_with_code_indentation_remains_literal(name: str) -> None:
    body = (
        "- > Quote paragraph.\n  >     Still paragraph.\nlazy continuation\n"
        f"  <!-- wade:{name}:start -->\n  Keep.\n  <!-- wade:{name}:end -->\n"
    )
    assert scan_markdown_structure(body).blocks == []
    assert replace_owned_section(body, name=name, heading=None, content="") == body


@pytest.mark.parametrize("name", ["summary", "deps", "plan", "future"])
@pytest.mark.parametrize("wrapper", ["details", "div"])
def test_standalone_markers_inside_raw_html_remain_owned(name: str, wrapper: str) -> None:
    before, after = f"<{wrapper}>\n", f"\n</{wrapper}>"
    block = f"<!-- wade:{name}:start -->\nOld.\n<!-- wade:{name}:end -->"
    body = before + block + after
    expected = before + block.replace("Old.", "New.") + after
    assert scan_markdown_structure(body).owned(name) is not None
    result = replace_owned_section(body, name=name, heading=None, content="New.")
    assert result == expected
    assert replace_owned_section(result, name=name, heading=None, content="New.") == expected


@pytest.mark.parametrize("heading", ["Summary", "Dependencies"])
def test_multiline_html_cannot_hide_protected_plan(heading: str) -> None:
    plan = (
        "<!-- wade:plan:start -->\n\n## " + heading + "\nKeep requirements.\n\n"
        "</details>\n<!-- wade:plan:end -->"
    )
    body = "## " + heading + "\nLegacy.\n<details>\n" + plan
    assert strip_legacy_section(body, heading) == plan


def test_nested_owned_marker_in_raw_html_refuses_replacement() -> None:
    body = (
        "<!-- wade:summary:start -->\n<details>\n<!-- wade:plan:start -->\n"
        "Keep.\n<!-- wade:plan:end -->\n</details>\n<!-- wade:summary:end -->"
    )
    with pytest.raises(MarkdownStructureError, match="nested"):
        replace_owned_section(body, name="summary", heading="Summary", content="New.")


@pytest.mark.parametrize("heading", ["Summary", "Dependencies"])
@pytest.mark.parametrize(
    "boundary",
    [
        "  ## Requirements\nKeep.",
        "   # Requirements\nKeep.",
        "Requirements\n============\nKeep.",
        "Requirements\n---\nKeep.",
    ],
)
def test_all_actual_top_level_headings_bound_legacy_migration(heading: str, boundary: str) -> None:
    body = "Intro.\n\n## " + heading + "\nOld.\n\n" + boundary
    assert strip_legacy_section(body, heading) == "Intro.\n\n" + boundary


@pytest.mark.parametrize("heading", ["Summary", "Dependencies"])
@pytest.mark.parametrize("form", ["  ## {heading}\nHuman.", "{heading}\n---\nHuman."])
def test_boundary_only_heading_does_not_become_legacy_candidate(heading: str, form: str) -> None:
    body = form.format(heading=heading)
    assert strip_legacy_section(body, heading) == body

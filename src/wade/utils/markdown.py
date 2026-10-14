"""Markdown parsing utilities — plan file parsing, section extraction."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


def extract_title(content: str) -> str | None:
    """Extract the first # heading from markdown content."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return None


def extract_section(content: str, heading: str) -> str | None:
    """Extract the content of a ## section by heading name.

    Case-insensitive heading match. Returns None if section not found.
    """
    lines = content.split("\n")
    heading_lower = heading.lower()
    capturing = False
    section_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if capturing:
                break  # End of our section
            if stripped[3:].strip().lower() == heading_lower:
                capturing = True
                continue
        elif capturing:
            section_lines.append(line)

    if not section_lines:
        return None

    return "\n".join(section_lines).strip()


def extract_all_sections(content: str) -> dict[str, str]:
    """Extract all ## sections as a dict of lowercase_heading → content."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = stripped[3:].strip().lower()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


def has_marker_block(content: str, start_marker: str, end_marker: str) -> bool:
    """Check if content contains a marker-delimited block."""
    return start_marker in content and end_marker in content


def extract_marker_block(content: str, start_marker: str, end_marker: str) -> str | None:
    """Extract text between the last marker pair (exclusive of markers).

    Uses rfind so that documentation examples of the markers (e.g. in code
    blocks) are skipped in favor of the real block appended at the end.
    """
    end_idx = content.rfind(end_marker)
    start_idx = content.rfind(start_marker, 0, end_idx) if end_idx != -1 else -1
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    inner_start = start_idx + len(start_marker)
    return content[inner_start:end_idx].strip()


def remove_marker_block(content: str, start_marker: str, end_marker: str) -> str:
    """Remove the last marker-delimited block (including markers).

    Uses rfind so that documentation examples of the markers are preserved.
    """
    end_idx = content.rfind(end_marker)
    start_idx = content.rfind(start_marker, 0, end_idx) if end_idx != -1 else -1
    if start_idx == -1 or end_idx == -1:
        return content

    before = content[:start_idx].rstrip("\n")
    after = content[end_idx + len(end_marker) :].lstrip("\n")

    if not after.strip():
        return before + "\n" if before.strip() else ""
    return before + "\n\n" + after


# ---------------------------------------------------------------------------
# AI sessions block helpers
# ---------------------------------------------------------------------------

SESSIONS_MARKER_START = "<!-- wade:sessions:start -->"
SESSIONS_MARKER_END = "<!-- wade:sessions:end -->"


def parse_sessions_from_body(body: str) -> list[dict[str, str]]:
    """Extract existing session rows from the sessions block in a body string.

    Returns a list of dicts with keys: ``phase``, ``ai_tool``, ``session_id``.
    """
    start_idx = body.find(SESSIONS_MARKER_START)
    end_idx = body.find(SESSIONS_MARKER_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return []
    block_content = body[start_idx + len(SESSIONS_MARKER_START) : end_idx]
    rows: list[dict[str, str]] = []
    for line in block_content.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.split("|")[1:-1]]
        if len(cells) != 3:
            continue
        phase, ai_tool, session_id = cells
        if "---" in phase or phase.lower() == "phase":
            continue
        if phase and ai_tool and session_id:
            rows.append({"phase": phase, "ai_tool": ai_tool, "session_id": session_id})
    return rows


def build_sessions_block(rows: list[dict[str, str]]) -> str:
    """Render the sessions block markdown from a list of session row dicts."""
    lines = [
        SESSIONS_MARKER_START,
        "",
        "## AI Sessions",
        "",
        "| Phase | Tool | Session |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| {row['phase']} | `{row['ai_tool']}` | `{row['session_id']}` |")
    lines.append("")
    lines.append(SESSIONS_MARKER_END)
    return "\n".join(lines)


def append_session_to_body(body: str, phase: str, ai_tool: str, session_id: str) -> str:
    """Add a new session row to the sessions block in a body string.

    Accumulates across calls — never replaces the full block. Idempotent:
    if a row with the same ``session_id`` already exists, the body is
    returned unchanged.
    """
    existing_rows = parse_sessions_from_body(body)
    if any(row["session_id"] == session_id for row in existing_rows):
        return body
    new_rows = [*existing_rows, {"phase": phase, "ai_tool": ai_tool, "session_id": session_id}]
    new_block = build_sessions_block(new_rows)
    cleaned = remove_marker_block(body, SESSIONS_MARKER_START, SESSIONS_MARKER_END)
    return cleaned.rstrip("\n") + "\n\n" + new_block + "\n"


class MarkdownStructureError(ValueError):
    """A body cannot be safely edited without repairing its structure."""


class MarkdownRange(BaseModel):
    """Offsets into the original string; never normalized Markdown."""

    start: int
    end: int


class ManagedBlock(MarkdownRange):
    name: str


class MarkdownHeading(MarkdownRange):
    level: int
    title: str


class MarkdownStructure(BaseModel):
    blocks: list[ManagedBlock] = Field(default_factory=list)
    headings: list[MarkdownHeading] = Field(default_factory=list)
    fences: list[MarkdownRange] = Field(default_factory=list)

    def owned(self, name: str) -> ManagedBlock | None:
        matches = [block for block in self.blocks if block.name == name]
        if len(matches) > 1:
            raise MarkdownStructureError(f"duplicate wade:{name} blocks")
        return matches[0] if matches else None


_PARAGRAPH_INTERRUPT_RE = re.compile(r" {0,3}(?:#{1,6}(?:[ \t]|$)|>|`{3,}|~{3,}|<!--)")
_THEMATIC_BREAK_RE = re.compile(r" {0,3}(?:(?:-[ \t]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,})")


def _list_paragraph_open(content: str, paragraph: bool = False) -> bool:
    """Recognize text paragraphs inside list blockquotes, including lazy lines."""
    while quote := re.match(r" {0,3}>[ \t]?", content):
        content = content[quote.end() :]
    if paragraph and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", content):
        return False
    return (
        bool(content.strip())
        and (paragraph or not content.startswith("    "))
        and not bool(_PARAGRAPH_INTERRUPT_RE.match(content))
        and not bool(_THEMATIC_BREAK_RE.fullmatch(content))
    )


def _list_container_line(text: str, indents: list[int], paragraph: bool) -> tuple[str, bool, bool]:
    """Track list content columns without treating contained examples as owners.

    Blank-separated dedents and interrupting blocks leave the list; plain lazy
    paragraph continuations retain it. Expand tabs only for detection, never for
    offsets or replacements. Nested item columns survive a return to the parent.
    """
    visual = text.expandtabs(4)
    if not visual.strip():
        return visual, bool(indents), False
    indent = len(visual) - len(visual.lstrip(" "))
    thematic = _THEMATIC_BREAK_RE.fullmatch(visual)
    if thematic and (not indents or indent < indents[-1]):
        indents.clear()
        return text, False, False
    if indents and paragraph and indent >= indents[-1]:
        content = visual[indents[-1] :]
        if re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", content):
            # A setext underline completes the list paragraph; a following
            # dedented paragraph cannot be its lazy continuation.
            return content, True, False
    item = re.fullmatch(r"( *)([-+*]|[0-9]{1,9}[.)])(?:( +)(.*)|$)", visual)
    # Only a nonempty bullet or an ordered item starting at 1 can interrupt
    # an existing paragraph. Otherwise its following HTML is still top-level.
    interrupts_paragraph = (
        item and bool(item[4] and item[4].strip()) and (item[2] in {"-", "+", "*", "1.", "1)"})
    )
    if (
        item
        and (indent <= 3 or (indents and indent >= indents[-1]))
        and (indents or not paragraph or interrupts_paragraph)
    ):
        while indents and indent < indents[-1]:
            indents.pop()
        padding = len(item[3]) if item[3] and len(item[3]) <= 4 else 1
        content_column = indent + len(item[2]) + padding
        indents.append(content_column)
        content = visual[content_column:]
        return content, True, _list_paragraph_open(content)
    while len(indents) > 1 and indents[-2] <= indent < indents[-1]:
        indents.pop()
    if indents:
        if indent >= indents[-1]:
            content = visual[indents[-1] :]
            return content, True, _list_paragraph_open(content, paragraph)
        interrupts = _PARAGRAPH_INTERRUPT_RE.match(visual)
        if not paragraph or interrupts or thematic:
            indents.clear()
        else:
            return visual, True, True
    starts_block = _PARAGRAPH_INTERRUPT_RE.match(visual)
    return (
        text,
        False,
        bool(text.strip())
        and (paragraph or not visual.startswith("    "))
        and not bool(starts_block),
    )


def scan_markdown_structure(body: str) -> MarkdownStructure:
    """Scan standalone markers, column-zero headings and Markdown code fences.

    Heading migration deliberately accepts only column-zero ATX headings.
    Quoted, list-prefixed and indented examples are not migration candidates.
    Fences may have Markdown's zero-to-three-space indentation. Marker-looking
    text inside a fence is opaque, including examples with unbalanced markers.
    """
    result = MarkdownStructure()
    active: tuple[str, int] | None = None
    fence: tuple[str, int, int, int] | None = None
    list_indents: list[int] = []
    paragraph_open = False
    offset = 0
    # Markdown lines end at CR/LF, not Python's broader Unicode separators.
    for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", body):
        line = match.group()
        text = line.rstrip("\r\n")
        if fence is not None:
            char, length, start, container_indent = fence
            visual = text.expandtabs(4)
            if visual.startswith(" " * container_indent) and re.fullmatch(
                r" {0,3}" + re.escape(char) + "{" + str(length) + r",}[ \t]*",
                visual[container_indent:],
            ):
                result.fences.append(MarkdownRange(start=start, end=offset + len(line)))
                fence = None
            offset += len(line)
            continue
        container_text, in_list, paragraph_open = _list_container_line(
            text, list_indents, paragraph_open
        )
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", container_text)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = (opening[1][0], len(opening[1]), offset, list_indents[-1] if in_list else 0)
            paragraph_open = False
            offset += len(line)
            continue
        if in_list:
            offset += len(line)
            continue
        marker = re.fullmatch(r" {0,3}<!-- wade:([a-zA-Z0-9_-]+):(start|end) -->[ \t]*", text)
        if marker:
            name, kind = marker.groups()
            if kind == "start":
                if active is not None:
                    raise MarkdownStructureError(f"nested wade:{name} inside wade:{active[0]}")
                active = (name, offset)
            else:
                if active is None or active[0] != name:
                    raise MarkdownStructureError(f"unmatched wade:{name}:end marker")
                result.blocks.append(
                    ManagedBlock(name=name, start=active[1], end=offset + len(text))
                )
                active = None
        elif active is None:
            heading = re.fullmatch(r"(#{1,6})(?:[ \t]+(.*?)(?:[ \t]+#+)?)?[ \t]*", text)
            if heading:
                result.headings.append(
                    MarkdownHeading(
                        start=offset,
                        end=offset + len(line),
                        level=len(heading[1]),
                        title=heading[2] or "",
                    )
                )
        offset += len(line)
    if fence is not None:
        raise MarkdownStructureError("unclosed code fence")
    if active is not None:
        raise MarkdownStructureError(f"unclosed wade:{active[0]}:start marker")
    return result


def legacy_section_range(
    body: str, heading: str, structure: MarkdownStructure
) -> MarkdownRange | None:
    """Identify at most one legacy H2 section without crossing protected ranges."""
    matches = [item for item in structure.headings if item.level == 2 and item.title == heading]
    if len(matches) > 1:
        raise MarkdownStructureError(f"multiple legacy {heading} sections")
    if not matches:
        return None
    selected = matches[0]
    boundaries = [len(body)]
    boundaries.extend(
        item.start for item in structure.headings if item.level <= 2 and item.start > selected.start
    )
    boundaries.extend(item.start for item in structure.blocks if item.start > selected.start)
    boundaries.extend(item.start for item in structure.fences if item.start > selected.start)
    return MarkdownRange(start=selected.start, end=min(boundaries))


def strip_legacy_section(body: str, heading: str) -> str:
    """Remove a uniquely verified unowned section, preserving all other slices."""
    structure = scan_markdown_structure(body)
    section = legacy_section_range(body, heading, structure)
    return body if section is None else body[: section.start] + body[section.end :]


def replace_owned_section(
    body: str,
    *,
    name: str,
    heading: str | None,
    content: str,
    before: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
) -> str:
    """Replace an authoritative pair, or migrate bounded legacy text and insert.

    Existing owned pairs keep their original position. Missing projections use
    real managed anchors from the scanner; fenced examples cannot be anchors.
    An empty owned pair remains when same-named human headings need protection
    from legacy migration on a later retry. Otherwise empty projections remove
    the owned pair. Both the input and complete output must be structurally valid.
    """
    structure = scan_markdown_structure(body)
    owned = structure.owned(name)
    projection = (
        f"<!-- wade:{name}:start -->\n{content}\n<!-- wade:{name}:end -->" if content else ""
    )
    if owned is not None:
        if (
            not content
            and heading is not None
            and any(item.level == 2 and item.title == heading for item in structure.headings)
        ):
            # Without this ownership evidence, an identical empty retry would
            # misclassify a preserved human section as legacy and delete it.
            projection = f"<!-- wade:{name}:start -->\n\n<!-- wade:{name}:end -->"
        candidate = body[: owned.start] + projection + body[owned.end :]
    else:
        section = legacy_section_range(body, heading, structure) if heading is not None else None
        candidate = body if section is None else body[: section.start] + body[section.end :]
        if projection:
            structure = scan_markdown_structure(candidate)
            position = len(candidate)
            for anchor in after:
                block = next((item for item in structure.blocks if item.name == anchor), None)
                if block is not None:
                    position = block.end
                    break
            else:
                for anchor in before:
                    block = next((item for item in structure.blocks if item.name == anchor), None)
                    if block is not None:
                        position = block.start
                        break
            left, right = candidate[:position], candidate[position:]
            newline = "\r\n" if "\r\n" in body else "\n"
            prefix = (
                ""
                if not left
                else newline * (2 - min(2, left[len(left.rstrip("\r\n")) :].count("\n")))
            )
            suffix = (
                ""
                if not right
                else newline
                * (2 - min(2, right[: len(right) - len(right.lstrip("\r\n"))].count("\n")))
            )
            candidate = left + prefix + projection + suffix + right
    validated = scan_markdown_structure(candidate)
    validated.owned(name)
    return candidate

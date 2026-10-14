"""Markdown parsing utilities — plan file parsing, section extraction."""

from __future__ import annotations

import re
from collections import Counter

from markdown_it import MarkdownIt
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
    legacy_candidate: bool = False


class MarkdownStructure(BaseModel):
    blocks: list[ManagedBlock] = Field(default_factory=list)
    headings: list[MarkdownHeading] = Field(default_factory=list)
    fences: list[MarkdownRange] = Field(default_factory=list)

    def owned(self, name: str) -> ManagedBlock | None:
        matches = [block for block in self.blocks if block.name == name]
        if len(matches) > 1:
            raise MarkdownStructureError(f"duplicate wade:{name} blocks")
        return matches[0] if matches else None


_MAX_STRUCTURE_NESTING = 100


def scan_markdown_structure(body: str) -> MarkdownStructure:
    """Map CommonMark blocks back to untouched source slices for safe edits.

    The parser owns list/quote/code container semantics. Ownership still requires
    a standalone top-level marker, and legacy headings remain column-zero ATX.
    Rendering is never used, so parser normalization cannot rewrite user bytes.
    """
    result = MarkdownStructure()
    active: tuple[str, int] | None = None
    # Parser maps use CR/LF line numbers; retain offsets in the original body.
    lines = [match.group() for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", body)]
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt("commonmark", {"maxNesting": _MAX_STRUCTURE_NESTING}).parse(body)
    for token_index, token in enumerate(tokens):
        # markdown-it stops parsing at its nesting limit. Refuse rather than
        # mistake omitted descendants or subsequent blocks for unowned text.
        if token.level >= _MAX_STRUCTURE_NESTING - 1:
            raise MarkdownStructureError("Markdown nesting exceeds the safe scan limit")
        if token.map is None:
            continue
        start_line, end_line = token.map
        if token.type == "fence":
            # The locked parser's fence rule includes the closing source line
            # in map, but excludes it from content. getLines preserves one line
            # per source content line, even after stripping container prefixes.
            # Auto-closed fences lack that extra line and must never be edited.
            content_lines = token.content.count("\n") + int(
                bool(token.content) and not token.content.endswith("\n")
            )
            if end_line - start_line != content_lines + 2:
                raise MarkdownStructureError("unclosed code fence")
            result.fences.append(MarkdownRange(start=offsets[start_line], end=offsets[end_line]))
            continue
        if token.level != 0 or token.type not in {"html_block", "heading_open"}:
            continue
        if token.type == "html_block":
            # A top-level raw HTML span can contain several standalone comments,
            # including managed markers before/after a <details> boundary. Its
            # token nesting still excludes literal list/quote/code examples.
            for line_number in range(start_line, end_line):
                text = lines[line_number].rstrip("\r\n")
                marker = re.fullmatch(
                    r" {0,3}<!-- wade:([a-zA-Z0-9_-]+):(start|end) -->[ \t]*", text
                )
                if marker is None:
                    continue
                name, kind = marker.groups()
                if kind == "start":
                    if active is not None:
                        raise MarkdownStructureError(f"nested wade:{name} inside wade:{active[0]}")
                    active = (name, offsets[line_number])
                else:
                    if active is None or active[0] != name:
                        raise MarkdownStructureError(f"unmatched wade:{name}:end marker")
                    result.blocks.append(
                        ManagedBlock(
                            name=name, start=active[1], end=offsets[line_number] + len(text)
                        )
                    )
                    active = None
        elif active is None:
            text = lines[start_line].rstrip("\r\n")
            legacy_heading = re.fullmatch(r"(#{1,6})(?:[ \t]+(.*?)(?:[ \t]+#+)?)?[ \t]*", text)
            # Every actual top-level H1/H2 is a deletion boundary, including
            # indented ATX and setext headings. Only column-zero ATX headings
            # are eligible to start legacy migration.
            result.headings.append(
                MarkdownHeading(
                    start=offsets[start_line],
                    end=offsets[end_line],
                    level=int(token.tag[1:]),
                    title=tokens[token_index + 1].content,
                    legacy_candidate=legacy_heading is not None,
                )
            )
    if active is not None:
        raise MarkdownStructureError(f"unclosed wade:{active[0]}:start marker")
    return result


def legacy_section_range(
    body: str, heading: str, structure: MarkdownStructure
) -> MarkdownRange | None:
    """Identify at most one legacy H2 section without crossing protected ranges."""
    matches = [
        item
        for item in structure.headings
        if item.level == 2 and item.title == heading and item.legacy_candidate
    ]
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
    migrate_legacy: bool = True,
) -> str:
    """Replace an authoritative pair, or migrate one original legacy range.

    Original ownership and legacy selection are fixed before rendering. Empty
    removals retain a minimal pair when the cleared body exposes a same-named
    human H2, so repeated updates cannot reinterpret it as legacy. Unrelated
    managed ownership must survive the edit, not merely its literal bytes.
    """
    structure = scan_markdown_structure(body)
    owned = structure.owned(name)
    section = (
        legacy_section_range(body, heading, structure)
        if owned is None and heading is not None and migrate_legacy
        else None
    )

    def _render(projection: str) -> str:
        if owned is not None:
            return body[: owned.start] + projection + body[owned.end :]
        cleaned = body if section is None else body[: section.start] + body[section.end :]
        if not projection:
            return cleaned
        anchors = scan_markdown_structure(cleaned)
        position = len(cleaned)
        for anchor in after:
            block = next((item for item in anchors.blocks if item.name == anchor), None)
            if block is not None:
                position = block.end
                break
        else:
            for anchor in before:
                block = next((item for item in anchors.blocks if item.name == anchor), None)
                if block is not None:
                    position = block.start
                    break
        left, right = cleaned[:position], cleaned[position:]
        newline = "\r\n" if "\r\n" in body else "\n"
        prefix = (
            "" if not left else newline * (2 - min(2, left[len(left.rstrip("\r\n")) :].count("\n")))
        )
        suffix = (
            ""
            if not right
            else newline * (2 - min(2, right[: len(right) - len(right.lstrip("\r\n"))].count("\n")))
        )
        return left + prefix + projection + suffix + right

    projection = (
        f"<!-- wade:{name}:start -->\n{content}\n<!-- wade:{name}:end -->" if content else ""
    )
    candidate = _render(projection)
    if not content and heading is not None and (owned is not None or section is not None):
        # Removing HTML payloads can expose human headings hidden by the original
        # parse. Analyze cleared ownership, for both marked and legacy removals.
        empty_pair = f"<!-- wade:{name}:start -->\n\n<!-- wade:{name}:end -->"
        provisional = _render(empty_pair)
        cleared = scan_markdown_structure(provisional)
        if any(item.level == 2 and item.title == heading for item in cleared.headings):
            projection, candidate = empty_pair, provisional
    validated = scan_markdown_structure(candidate)
    if (validated.owned(name) is not None) != bool(projection):
        raise MarkdownStructureError(f"managed ownership changed for wade:{name}")
    original_unrelated = Counter(
        (block.name, body[block.start : block.end])
        for block in structure.blocks
        if block.name != name
    )
    remaining_unrelated = Counter(
        (block.name, candidate[block.start : block.end])
        for block in validated.blocks
        if block.name != name
    )
    if original_unrelated != remaining_unrelated:
        raise MarkdownStructureError(f"managed ownership changed outside wade:{name}")
    return candidate

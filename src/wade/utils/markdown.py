"""Markdown parsing utilities — plan file parsing, section extraction."""

from __future__ import annotations


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

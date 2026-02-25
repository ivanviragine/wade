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
    """Extract text between markers (exclusive of markers themselves)."""
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    inner_start = start_idx + len(start_marker)
    return content[inner_start:end_idx].strip()


def remove_marker_block(content: str, start_marker: str, end_marker: str) -> str:
    """Remove a marker-delimited block (including markers), normalizing blank lines."""
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return content

    before = content[:start_idx].rstrip("\n")
    after = content[end_idx + len(end_marker) :].lstrip("\n")

    if not after.strip():
        return before + "\n" if before.strip() else ""
    return before + "\n\n" + after

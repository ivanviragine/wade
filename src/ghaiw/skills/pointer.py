"""AGENTS.md pointer management — detect, insert, refresh, remove.

The workflow pointer is a block of text injected into a project's AGENTS.md
(or CLAUDE.md) that directs AI agents to the ghaiw workflow skill.

The block is wrapped in HTML comment markers for robust detection:
    <!-- ghaiw:pointer:start -->
    ## Git Workflow
    ...
    <!-- ghaiw:pointer:end -->

Behavioral reference: lib/init.sh (_append_agents_pointer, _pointer_*)
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ghaiw.skills.installer import get_templates_dir

logger = structlog.get_logger()

MARKER_START = "<!-- ghaiw:pointer:start -->"
MARKER_END = "<!-- ghaiw:pointer:end -->"


def get_pointer_content() -> str:
    """Load the pointer template content from templates/agents-pointer.md."""
    template_path = get_templates_dir() / "agents-pointer.md"
    if template_path.is_file():
        return template_path.read_text(encoding="utf-8").strip()
    # Fallback inline content
    return (
        "## Git Workflow\n\n"
        "**First action every session** — read @.claude/skills/workflow/SKILL.md for\n"
        "full rules."
    )


def has_pointer(file_path: Path) -> bool:
    """Check if a file contains the ghaiw pointer (marker-based)."""
    if not file_path.is_file():
        return False
    content = file_path.read_text(encoding="utf-8")
    return MARKER_START in content and MARKER_END in content


def extract_pointer_content(file_path: Path) -> str | None:
    """Extract the text between markers (for staleness comparison).

    Returns None if markers are not found.
    """
    if not file_path.is_file():
        return None

    content = file_path.read_text(encoding="utf-8")
    start_idx = content.find(MARKER_START)
    end_idx = content.find(MARKER_END)

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    inner = content[start_idx + len(MARKER_START) : end_idx].strip()
    return inner


def remove_pointer(file_path: Path) -> bool:
    """Remove the pointer block from a file.

    Handles both marker-based and old-style (## Git Workflow) formats.
    Returns True if the pointer was found and removed.
    """
    if not file_path.is_file():
        return False

    content = file_path.read_text(encoding="utf-8")

    # Try marker-based removal first
    start_idx = content.find(MARKER_START)
    end_idx = content.find(MARKER_END)

    if start_idx != -1 and end_idx != -1:
        # Remove the marker block including surrounding blank lines
        before = content[:start_idx].rstrip("\n")
        after = content[end_idx + len(MARKER_END) :].lstrip("\n")

        new_content = before
        if after.strip():
            new_content += "\n\n" + after
        new_content = new_content.rstrip() + "\n" if new_content.strip() else ""

        if not new_content.strip():
            # File is empty after removal — remove the file
            file_path.unlink()
            logger.info("pointer.removed_empty_file", path=str(file_path))
            return True

        file_path.write_text(new_content, encoding="utf-8")
        logger.info("pointer.removed", path=str(file_path))
        return True

    # Fallback: old-style removal (## Git Workflow to next ## or EOF)
    lines = content.split("\n")
    in_pointer = False
    new_lines: list[str] = []

    for line in lines:
        if line.strip() == "## Git Workflow" and not in_pointer:
            in_pointer = True
            continue
        if in_pointer and line.strip().startswith("## "):
            in_pointer = False
        if not in_pointer:
            new_lines.append(line)

    if in_pointer or len(new_lines) != len(lines):
        # Pointer was found and removed
        new_content = "\n".join(new_lines).rstrip() + "\n"
        if not new_content.strip():
            file_path.unlink()
            logger.info("pointer.removed_empty_file", path=str(file_path))
        else:
            file_path.write_text(new_content, encoding="utf-8")
            logger.info("pointer.removed_legacy", path=str(file_path))
        return True

    return False


def write_pointer(file_path: Path) -> None:
    """Write (append) the pointer block with markers to a file.

    Creates the file if it doesn't exist.
    """
    content = get_pointer_content()
    block = f"\n{MARKER_START}\n{content}\n{MARKER_END}\n"

    if file_path.is_file():
        existing = file_path.read_text(encoding="utf-8").rstrip("\n")
        file_path.write_text(existing + "\n" + block, encoding="utf-8")
    else:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(block.lstrip("\n"), encoding="utf-8")

    logger.info("pointer.written", path=str(file_path))


def ensure_pointer(project_root: Path) -> str | None:
    """Ensure the AGENTS.md pointer is present and up-to-date.

    Finds AGENTS.md or CLAUDE.md, creates AGENTS.md if neither exists.
    Refreshes if content is stale.

    Returns the path to the file where the pointer was written, or None on error.
    """
    # Find the target file (prefer AGENTS.md over CLAUDE.md)
    agents_md = project_root / "AGENTS.md"
    claude_md = project_root / "CLAUDE.md"

    if agents_md.is_file():
        target = agents_md
    elif claude_md.is_file():
        target = claude_md
    else:
        target = agents_md  # Create AGENTS.md if neither exists

    current_content = get_pointer_content()

    if has_pointer(target):
        # Check if content is stale
        existing = extract_pointer_content(target)
        if existing == current_content:
            logger.debug("pointer.already_current", path=str(target))
            return str(target)
        # Refresh: remove old, write new
        remove_pointer(target)

    write_pointer(target)

    # Create CLAUDE.md as a symlink to AGENTS.md so Claude Code can discover the pointer.
    # Only create if: we wrote to AGENTS.md AND CLAUDE.md doesn't already exist.
    if target == agents_md and not claude_md.exists() and not claude_md.is_symlink():
        claude_md.symlink_to("AGENTS.md")
        logger.info("pointer.claude_symlink_created", path=str(claude_md))

    return str(target)

"""Init service migrations — off-main cleanup and .gitignore management.

Migrates skills and AI-tool artifacts off the main checkout, manages the
wade-owned ``.gitignore`` marker block, and cleans legacy entries. Owns the
``GITIGNORE_*`` constants. Leaf module — imports nothing from siblings.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

import structlog

from wade.models.config import WADE_BASE_ALLOWLIST_PATTERN
from wade.skills import installer, pointer
from wade.ui.console import console

logger = structlog.get_logger()

# Marker comments wrapping the managed block in .gitignore
GITIGNORE_MARKER_START: Final = (
    "# wade:start — managed by wade (do not edit — run `wade update` to refresh)"
)
GITIGNORE_MARKER_END: Final = "# wade:end"

# Actual patterns placed between the markers (static base set)
GITIGNORE_ENTRIES: Final = [
    ".wade/",
    ".wade-managed",
    ".wade.yml",
    "PLAN.md",
    "PR-SUMMARY.md",
    ".commit-msg",
]

# Entries added by older versions without markers — used for backward-compat cleanup
_GITIGNORE_LEGACY_ENTRIES: Final = [
    "# wade managed files",
    ".issue-context.md",
]

__all__ = [
    "GITIGNORE_ENTRIES",
    "GITIGNORE_MARKER_END",
    "GITIGNORE_MARKER_START",
    "_GITIGNORE_LEGACY_ENTRIES",
    "_clean_gitignore",
    "_ensure_wade_dir_self_ignoring",
    "_migrate_ai_artifacts_off_main",
    "_migrate_gitignore_block",
    "_migrate_skills_off_main",
]


def _migrate_skills_off_main(project_root: Path) -> list[str]:
    """Remove old skill files from the main checkout.

    Skills are now installed per-session in worktrees only.  This cleans up
    skill directories and cross-tool symlinks left by previous ``wade init``
    or ``wade update`` runs.

    Returns list of removed paths (relative to project root).
    """
    removed: list[str] = []
    primary_skills_dir = project_root / ".claude" / "skills"

    # Remove known skill directories
    for skill_name in installer.MANAGED_SKILL_NAMES:
        skill_dir = primary_skills_dir / skill_name
        if skill_dir.is_symlink():
            skill_dir.unlink()
            removed.append(f".claude/skills/{skill_name}")
        elif skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed.append(f".claude/skills/{skill_name}")

    # Remove cross-tool symlinks (only symlinks — real dirs may contain user data)
    for cross_dir in installer.CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.is_symlink():
            cross_path.unlink()
            removed.append(cross_dir)
        elif cross_path.is_dir():
            logger.warning(
                "init.skip_non_symlink_cross_tool_dir",
                path=str(cross_path),
            )

    # Clean up empty parent directories
    for parent in [
        primary_skills_dir,
        project_root / ".github",
        project_root / ".agents",
        project_root / ".gemini",
        project_root / ".cursor",
    ]:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    return removed


def _migrate_ai_artifacts_off_main(project_root: Path) -> list[str]:
    """Remove AI-tool artifacts written to main by older wade versions.

    These files are now written only to worktrees during bootstrap.  This
    cleans them up from the main checkout so they no longer appear as diffs.

    Removed (when only wade-managed content is present):
    - CLAUDE.md symlink (if wade-created, pointing to AGENTS.md)
    - AGENTS.md pointer block
    - .claude/settings.json (project-level allowlist only)
    - .cursor/cli.json (project-level allowlist only)

    Returns list of removed paths (relative to project root).
    """
    import json as _json

    from crossby.sync.permissions import canonical_to_claude, canonical_to_cursor

    claude_wade_pattern = canonical_to_claude(WADE_BASE_ALLOWLIST_PATTERN)
    cursor_wade_pattern = canonical_to_cursor(WADE_BASE_ALLOWLIST_PATTERN)

    removed: list[str] = []

    # Remove CLAUDE.md symlink only if wade created it (points to AGENTS.md)
    claude_md = project_root / "CLAUDE.md"
    if claude_md.is_symlink():
        link_target = claude_md.resolve()
        if link_target == (project_root / "AGENTS.md").resolve():
            claude_md.unlink()
            removed.append("CLAUDE.md")

    # Remove pointer block from AGENTS.md (or CLAUDE.md if it exists as a file)
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = project_root / name
        if target.is_file() and pointer.remove_pointer(target):
            removed.append(name)

    # Remove .claude/settings.json only when it contains exclusively wade-managed
    # content.  Require the exact top-level key set {"permissions"} (not a subset)
    # and verify the wade allow-pattern is present so user-only allowlists and
    # files extended with hook keys are never removed.
    claude_settings = project_root / ".claude" / "settings.json"
    if claude_settings.is_file():
        try:
            raw = _json.loads(claude_settings.read_text(encoding="utf-8"))
            allow = raw.get("permissions", {}).get("allow", []) if isinstance(raw, dict) else []
            if (
                isinstance(raw, dict)
                and set(raw.keys()) == {"permissions"}
                and isinstance(allow, list)
                and claude_wade_pattern in allow
            ):
                claude_settings.unlink()
                removed.append(".claude/settings.json")
                claude_dir = project_root / ".claude"
                if claude_dir.is_dir() and not any(claude_dir.iterdir()):
                    claude_dir.rmdir()
        except (_json.JSONDecodeError, OSError):
            pass

    # Remove .cursor/cli.json only when it contains exclusively wade-managed
    # content (same exact-match logic as for .claude/settings.json above).
    cursor_cli = project_root / ".cursor" / "cli.json"
    if cursor_cli.is_file():
        try:
            raw = _json.loads(cursor_cli.read_text(encoding="utf-8"))
            allow = raw.get("permissions", {}).get("allow", []) if isinstance(raw, dict) else []
            if (
                isinstance(raw, dict)
                and set(raw.keys()) == {"permissions"}
                and isinstance(allow, list)
                and cursor_wade_pattern in allow
            ):
                cursor_cli.unlink()
                removed.append(".cursor/cli.json")
                cursor_dir = project_root / ".cursor"
                if cursor_dir.is_dir() and not any(cursor_dir.iterdir()):
                    cursor_dir.rmdir()
        except (_json.JSONDecodeError, OSError):
            pass

    return removed


def _migrate_gitignore_block(project_root: Path) -> None:
    """Remove the stale committed ``# wade:start`` block from ``.gitignore``.

    Called during ``update()`` to migrate existing projects.  New projects
    created after this change will never have the committed block.
    """
    from wade.utils.markdown import has_marker_block

    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return

    existing = gitignore.read_text(encoding="utf-8")
    has_markers = has_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
    has_legacy = any(entry in existing for entry in _GITIGNORE_LEGACY_ENTRIES)
    if has_markers or has_legacy:
        _clean_gitignore(project_root)
        console.info("Removed stale wade gitignore block — commit the change:")
        console.detail(
            'git add .gitignore && git commit -m "chore: remove wade managed gitignore block"'
        )


def _ensure_wade_dir_self_ignoring(project_root: Path) -> None:
    """Create ``.wade/.gitignore`` with ``*`` so ``.wade/`` doesn't appear untracked.

    Idempotent — safe to call on every init/update.
    """
    wade_dir = project_root / ".wade"
    wade_dir.mkdir(exist_ok=True)
    gi = wade_dir / ".gitignore"
    if not gi.is_file() or gi.read_text(encoding="utf-8").strip() != "*":
        gi.write_text("*\n", encoding="utf-8")


def _clean_gitignore(project_root: Path) -> None:
    """Remove the wade-managed block from .gitignore.

    Primary: marker-based removal (reliable, preserves user content).
    Fallback: line-by-line removal of known entries (backward compat for
    projects initialized before markers were introduced).
    """
    from wade.utils.markdown import has_marker_block, remove_marker_block

    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return

    existing = gitignore.read_text(encoding="utf-8")

    if has_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END):
        new_content = remove_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
        gitignore.write_text(new_content, encoding="utf-8")
        return

    # Fallback: remove individual known entries line-by-line
    all_known = set(GITIGNORE_ENTRIES) | set(_GITIGNORE_LEGACY_ENTRIES)
    lines = existing.splitlines()
    new_lines = [line for line in lines if line not in all_known]

    # Collapse consecutive blank lines
    cleaned: list[str] = []
    for line in new_lines:
        if line.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(line)

    content = "\n".join(cleaned).rstrip() + "\n" if cleaned else ""
    gitignore.write_text(content, encoding="utf-8")

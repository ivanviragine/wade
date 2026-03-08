"""Skill file installer — copy/symlink skill templates to target projects."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()


def get_wade_repo_root() -> Path:
    """Get the wade package repository root (for self-init detection).

    Works in both editable installs (dev) and regular installs.
    """
    return Path(__file__).parent.parent.parent.parent


def get_templates_dir() -> Path:
    """Get the path to the templates directory.

    Looks in two places:
    1. Repo root (development / editable install) — templates/ next to src/
    2. Inside the installed package (pip install) — wade/templates/
    """
    # 1. Dev mode: walk up from src/wade/skills/installer.py → repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    dev_templates = repo_root / "templates"
    if dev_templates.is_dir() and (dev_templates / "skills").is_dir():
        return dev_templates

    # 2. Installed package: templates are force-included as wade/templates/
    import importlib.resources

    pkg_templates = importlib.resources.files("wade").joinpath("templates")
    pkg_path = Path(str(pkg_templates))
    if pkg_path.is_dir():
        return pkg_path

    # Last resort — return the dev path (will trigger "not found" warning)
    return dev_templates


def get_skills_templates_dir() -> Path:
    """Get the path to the skill templates directory."""
    return get_templates_dir() / "skills"


# --- Skill registry: name → list of files ---

SKILL_FILES: dict[str, list[str]] = {
    "task": ["SKILL.md", "plan-format.md", "examples.md"],
    "plan-session": ["SKILL.md"],
    "implementation-session": ["SKILL.md"],
    "address-reviews-session": ["SKILL.md"],
    "deps": ["SKILL.md"],
}

# Skills that should always be overwritten on update
ALWAYS_OVERWRITE = {"plan-session", "implementation-session", "address-reviews-session"}

# Old skill names removed in the phase-skill refactor — cleaned up during update
_LEGACY_SKILLS = {"workflow", "sync", "pr-summary", "work-session", "review-session"}

# Cross-tool directories that get symlinked to .claude/skills
CROSS_TOOL_DIRS = [".github/skills", ".agents/skills", ".gemini/skills", ".cursor/skills"]

# --- Command-to-skill mapping: which skills each session type needs ---

PLAN_SKILLS: list[str] = ["plan-session", "task", "deps"]
DEPS_SKILLS: list[str] = ["deps"]
IMPLEMENT_SKILLS: list[str] = ["implementation-session", "task"]
REVIEW_SKILLS: list[str] = ["address-reviews-session", "task"]


def install_skills(
    project_root: Path,
    is_self_init: bool = False,
    force: bool = False,
    templates_dir: Path | None = None,
    skills: list[str] | None = None,
) -> list[str]:
    """Install skill files to a project.

    Args:
        project_root: Root of the target project.
        is_self_init: If True, create symlinks instead of copies.
        force: If True, overwrite existing files.
        templates_dir: Override the skills templates directory.
            Useful for worktrees where templates live in the worktree itself.
        skills: If provided, install only the listed skills instead of all
            ``SKILL_FILES``.  When ``None`` (default), all skills are installed.

    Returns:
        List of installed file paths (relative to project root).
    """
    installed: list[str] = []
    if templates_dir is None:
        templates_dir = get_skills_templates_dir()

    if not templates_dir.is_dir():
        logger.warning("skills.templates_not_found", path=str(templates_dir))
        return installed

    primary_skills_dir = project_root / ".claude" / "skills"

    # Clean up legacy skill directories from previous versions
    for legacy_name in _LEGACY_SKILLS:
        legacy_dir = primary_skills_dir / legacy_name
        if legacy_dir.is_symlink():
            legacy_dir.unlink()
            logger.debug("skills.removed_legacy", name=legacy_name)
        elif legacy_dir.is_dir():
            shutil.rmtree(legacy_dir)
            logger.debug("skills.removed_legacy", name=legacy_name)

    # Determine which skills to install
    if skills is not None:
        invalid = set(skills) - set(SKILL_FILES.keys())
        if invalid:
            logger.warning("skills.unknown_skill_names", names=sorted(invalid))
        skill_items = {name: SKILL_FILES[name] for name in skills if name in SKILL_FILES}

        # Prune stale skills: remove previously installed skills not in the
        # requested set (ensures clean per-command isolation on worktree reuse).
        if primary_skills_dir.is_dir():
            keep = set(skill_items.keys()) | set(_LEGACY_SKILLS)
            for entry in primary_skills_dir.iterdir():
                if entry.name not in keep and (entry.is_symlink() or entry.is_dir()):
                    if entry.is_symlink():
                        entry.unlink()
                    else:
                        shutil.rmtree(entry)
                    logger.debug("skills.pruned_stale", name=entry.name)
    else:
        skill_items = SKILL_FILES

    for skill_name, files in skill_items.items():
        if is_self_init:
            # Symlink the whole directory
            _link_skill_dir(project_root, skill_name, templates_dir)
            installed.append(f".claude/skills/{skill_name}")
        else:
            # Copy individual files
            overwrite = force or skill_name in ALWAYS_OVERWRITE
            for filename in files:
                src = templates_dir / skill_name / filename
                if not src.is_file():
                    continue
                dest = primary_skills_dir / skill_name / filename
                if _copy_skill_file(src, dest, overwrite=overwrite):
                    installed.append(f".claude/skills/{skill_name}/{filename}")

    # Ensure primary skills dir exists before cross-tool symlinks
    primary_skills_dir.mkdir(parents=True, exist_ok=True)

    # Cross-tool symlinks
    for cross_dir in CROSS_TOOL_DIRS:
        _link_cross_tool(project_root, cross_dir, primary_skills_dir)
        installed.append(cross_dir)

    return installed


def remove_skills(project_root: Path) -> list[str]:
    """Remove all installed skill files and directories.

    Returns list of removed paths.
    """
    removed: list[str] = []

    # Remove cross-tool symlinks first
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.is_symlink():
            cross_path.unlink()
            removed.append(cross_dir)
        elif cross_path.is_dir():
            shutil.rmtree(cross_path)
            removed.append(cross_dir)

    # Remove skill directories (current + legacy)
    primary_skills_dir = project_root / ".claude" / "skills"
    for skill_name in {*SKILL_FILES, *_LEGACY_SKILLS}:
        skill_dir = primary_skills_dir / skill_name
        if skill_dir.is_symlink():
            skill_dir.unlink()
            removed.append(f".claude/skills/{skill_name}")
        elif skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed.append(f".claude/skills/{skill_name}")

    # Clean up empty parent directories
    for parent in [
        primary_skills_dir,
        project_root / ".claude",
        project_root / ".github",
        project_root / ".agents",
        project_root / ".gemini",
        project_root / ".cursor",
    ]:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    return removed


def _copy_skill_file(src: Path, dest: Path, overwrite: bool = False) -> bool:
    """Copy a single skill file, creating parent dirs as needed.

    Returns True if file was installed, False if skipped.
    """
    if dest.exists() and not overwrite:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.debug("skills.copied", src=str(src), dest=str(dest))
    return True


def _link_skill_dir(project_root: Path, skill_name: str, templates_dir: Path) -> None:
    """Create a symlink from .claude/skills/<name> → ../../templates/skills/<name>.

    For self-init mode: edits to templates are immediately reflected.
    """
    link = project_root / ".claude" / "skills" / skill_name
    target = templates_dir / skill_name

    if not target.is_dir():
        return

    # Calculate relative path from link location (.claude/skills/<name>/) to target
    # Link lives 2 levels deep from project root, so go up 2 levels then into rel_target.
    try:
        rel_target = target.resolve().relative_to(project_root.resolve())
        rel_path = Path("../..") / rel_target
    except ValueError:
        # Target is outside project root — use absolute
        rel_path = target.resolve()

    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)

    link.symlink_to(rel_path)
    logger.debug("skills.linked", link=str(link), target=str(rel_path))


def _link_cross_tool(
    project_root: Path,
    cross_dir: str,
    primary_skills_dir: Path,
) -> None:
    """Create cross-tool symlink: .github/skills → .claude/skills etc."""
    link = project_root / cross_dir

    if not primary_skills_dir.is_dir():
        return

    # Calculate relative path from link parent to primary_skills_dir.
    # e.g. .github/skills → ../.claude/skills (matches bash behavior)
    rel_target = Path(os.path.relpath(primary_skills_dir.resolve(), link.parent.resolve()))

    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)

    link.symlink_to(rel_target)
    logger.debug("skills.cross_linked", link=str(link), target=str(rel_target))

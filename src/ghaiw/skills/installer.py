"""Skill file installer — copy/symlink skill templates to target projects.

Behavioral reference: lib/init.sh (skill installation in ghaiw_init/ghaiw_update)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()


def get_templates_dir() -> Path:
    """Get the path to the templates directory (packaged with ghaiw)."""
    # Walk up from this file to find the templates/ directory
    # Structure: src/ghaiw/skills/installer.py → ../../../../templates/
    pkg_root = Path(__file__).parent.parent.parent.parent
    templates = pkg_root / "templates"
    if templates.is_dir():
        return templates
    # Fallback for installed package
    import importlib.resources

    ref = importlib.resources.files("ghaiw").joinpath("../../templates")
    return Path(str(ref))


def get_skills_templates_dir() -> Path:
    """Get the path to the skill templates directory."""
    return get_templates_dir() / "skills"


# --- Skill registry: name → list of files ---

SKILL_FILES: dict[str, list[str]] = {
    "task": ["SKILL.md", "plan-format.md", "examples.md"],
    "sync": ["SKILL.md", "examples.md", "reference.md"],
    "deps": ["SKILL.md"],
    "pr-summary": ["SKILL.md"],
    "workflow": ["SKILL.md"],
}

# Skills that should always be overwritten on update
ALWAYS_OVERWRITE = {"workflow"}

# Cross-tool directories that get symlinked to .claude/skills
CROSS_TOOL_DIRS = [".github/skills", ".agents/skills", ".gemini/skills"]


def install_skills(
    project_root: Path,
    is_self_init: bool = False,
    force: bool = False,
) -> list[str]:
    """Install all skill files to a project.

    Args:
        project_root: Root of the target project.
        is_self_init: If True, create symlinks instead of copies.
        force: If True, overwrite existing files.

    Returns:
        List of installed file paths (relative to project root).
    """
    installed: list[str] = []
    templates_dir = get_skills_templates_dir()

    if not templates_dir.is_dir():
        logger.warning("skills.templates_not_found", path=str(templates_dir))
        return installed

    primary_skills_dir = project_root / ".claude" / "skills"

    for skill_name, files in SKILL_FILES.items():
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

    # Remove skill directories
    primary_skills_dir = project_root / ".claude" / "skills"
    for skill_name in SKILL_FILES:
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

    # Calculate relative path from link location to target
    try:
        rel_target = target.resolve().relative_to(project_root.resolve())
        # Build relative path from .claude/skills/<name> to templates/skills/<name>
        rel_path = Path("../../..") / rel_target
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

    # Calculate relative path
    try:
        rel_target = primary_skills_dir.resolve().relative_to(
            link.parent.resolve()
        )
    except ValueError:
        rel_target = primary_skills_dir.resolve()

    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)

    link.symlink_to(rel_target)
    logger.debug("skills.cross_linked", link=str(link), target=str(rel_target))

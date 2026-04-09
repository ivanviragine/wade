"""Skill file installer — copy/symlink skill templates to target projects."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Placeholder substitutions applied when skill files are copied to a project.
# Maps placeholder string → relative path inside templates/skills/_partials/.
_SKILL_PARTIALS: dict[str, str] = {
    "{user_interaction_prompt}": "_partials/user-interaction.md",
    "{review_enforcement_rule}": "_partials/review-enforcement-rule.md",
}


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


def load_prompt_template(name: str) -> str:
    """Load a prompt template by name from templates/prompts/.

    Args:
        name: Template filename (e.g. "review-plan.md").

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    template = get_templates_dir() / "prompts" / name
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8").strip()


# --- Skill registry: name → list of files ---

SKILL_FILES: dict[str, list[str]] = {
    "task": ["SKILL.md", "plan-format.md", "examples.md"],
    "plan-session": ["SKILL.md"],
    "implementation-session": ["SKILL.md"],
    "review-pr-comments-session": ["SKILL.md"],
    "deps": ["SKILL.md"],
    "knowledge": ["SKILL.md"],
}

# Skills that should always be overwritten on update
ALWAYS_OVERWRITE = {"plan-session", "implementation-session", "review-pr-comments-session"}

# Skills whose SKILL.md files contain placeholder strings (see _SKILL_PARTIALS) that
# must be expanded at install time.  These cannot be installed as plain directory
# symlinks in self-init mode because the agent would see unexpanded placeholders.
# Currently the same set as ALWAYS_OVERWRITE — kept separate because the concerns
# are distinct and may diverge if new skills are added.
INJECT_SKILLS = {"plan-session", "implementation-session", "review-pr-comments-session"}

# Old skill names removed in the phase-skill refactor — cleaned up during update
_LEGACY_SKILLS = {
    "workflow",
    "sync",
    "pr-summary",
    "work-session",
    "review-session",
    "address-reviews-session",
}

# All skill names Wade manages (current + legacy) — used for safe pruning
MANAGED_SKILL_NAMES: set[str] = set(SKILL_FILES) | _LEGACY_SKILLS

# Cross-tool directories that get symlinked to .claude/skills
CROSS_TOOL_DIRS = [".github/skills", ".agents/skills", ".gemini/skills", ".cursor/skills"]

# Wade-managed plan guard hook files that should never be committed.
PLAN_GUARD_HOOK_FILES = [
    ".claude/hooks/plan_write_guard.py",
    ".cursor/hooks/plan_write_guard.py",
    ".copilot/hooks/plan_write_guard.py",
    ".gemini/hooks/plan_write_guard.py",
]

# Wade-managed worktree guard hook files that should never be committed.
WORKTREE_GUARD_HOOK_FILES = [
    ".claude/hooks/worktree_guard.py",
    ".cursor/hooks/worktree_guard.py",
    ".copilot/hooks/worktree_guard.py",
    ".gemini/hooks/worktree_guard.py",
]

# Wade-managed hook config files written alongside hook scripts — never committed.
HOOK_CONFIG_FILES = [
    ".cursor/hooks.json",
    ".gemini/settings.json",
    ".github/hooks/hooks.json",
]

# --- Command-to-skill mapping: which skills each session type needs ---

PLAN_SKILLS: list[str] = ["plan-session", "task", "deps"]
DEPS_SKILLS: list[str] = ["deps"]
IMPLEMENT_SKILLS: list[str] = ["implementation-session", "task"]
REVIEW_SKILLS: list[str] = ["review-pr-comments-session", "task"]


def get_worktree_gitignore_entries() -> list[str]:
    """Compute gitignore entries for wade artifacts in worktrees.

    Returns **specific file paths** (never directories, except ``.wade/``)
    to ensure user-owned files in the same parent directories are never hidden.

    Cross-tool symlinks and untracked pointer files are not included here —
    they are added conditionally by ``write_worktree_gitignore()``.
    """
    entries: list[str] = []

    # Skill files (specific files, not directories — user may have their own skills)
    for name, files in sorted(SKILL_FILES.items()):
        for filename in files:
            entries.append(f".claude/skills/{name}/{filename}")

    # Guard hook scripts
    entries.extend(PLAN_GUARD_HOOK_FILES)
    entries.extend(WORKTREE_GUARD_HOOK_FILES)

    # Hook config files
    entries.extend(HOOK_CONFIG_FILES)

    # AI tool settings (written per-session to worktrees only)
    entries.append(".claude/settings.json")
    entries.append(".cursor/cli.json")
    entries.append(".gemini/policies/wade.toml")

    # Session artifacts
    entries.extend(
        [
            "PLAN.md",
            "PR-SUMMARY.md",
            ".commit-msg",
            ".wade/",
            ".wade-managed",
        ]
    )

    return entries


def install_skills(
    project_root: Path,
    is_self_init: bool = False,
    force: bool = False,
    templates_dir: Path | None = None,
    skills: list[str] | None = None,
    extra_partials: dict[str, str] | None = None,
) -> list[str]:
    """Install skill files to a project.

    Args:
        project_root: Root of the target project.
        is_self_init: If True, symlink skill directories instead of copying files.
            Skills in ``INJECT_SKILLS`` are always processed copies even in this mode,
            because their templates contain placeholders that must be expanded.
        force: If True, overwrite existing files.
        templates_dir: Override the skills templates directory.
            Useful for worktrees where templates live in the worktree itself.
        skills: If provided, install only the listed skills instead of all
            ``SKILL_FILES``.  When ``None`` (default), all skills are installed.

    Returns:
        List of installed paths (relative to project root).  Symlinked skill
        directories are reported at directory level; copied skills at file level.
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

        # Prune stale skills: remove Wade-managed skills not in the requested
        # set (ensures clean per-command isolation on worktree reuse).
        # Only remove known Wade-managed names — leave user-owned dirs untouched.
        if primary_skills_dir.is_dir():
            stale_managed = MANAGED_SKILL_NAMES - set(skill_items)
            for entry in primary_skills_dir.iterdir():
                if entry.name in stale_managed and (entry.is_symlink() or entry.is_dir()):
                    if entry.is_symlink():
                        entry.unlink()
                    else:
                        shutil.rmtree(entry)
                    logger.debug("skills.pruned_stale", name=entry.name)
    else:
        skill_items = SKILL_FILES

    for skill_name, files in skill_items.items():
        if is_self_init and skill_name not in INJECT_SKILLS:
            # Symlink the whole directory (no partials expansion needed)
            _link_skill_dir(project_root, skill_name, templates_dir)
            installed.append(f".claude/skills/{skill_name}")
        else:
            # Copy individual files, expanding partials if present.
            # In self-init mode, INJECT_SKILLS must be processed copies so agents
            # see expanded content rather than raw placeholder strings.
            overwrite = is_self_init or force or skill_name in ALWAYS_OVERWRITE

            # Remove existing symlink for inject skills in self-init before creating dir
            if is_self_init and skill_name in INJECT_SKILLS:
                link = primary_skills_dir / skill_name
                if link.is_symlink():
                    link.unlink()

            for filename in files:
                src = templates_dir / skill_name / filename
                if not src.is_file():
                    continue
                dest = primary_skills_dir / skill_name / filename
                if _copy_skill_file(
                    src,
                    dest,
                    overwrite=overwrite,
                    skills_templates_dir=templates_dir,
                    extra_partials=extra_partials,
                ):
                    installed.append(f".claude/skills/{skill_name}/{filename}")

    # Ensure primary skills dir exists before cross-tool symlinks
    primary_skills_dir.mkdir(parents=True, exist_ok=True)

    # Cross-tool symlinks — skip real user-owned directories
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.exists() and not cross_path.is_symlink():
            logger.debug("skills.skip_cross_tool_user_dir", path=str(cross_path))
            continue
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
        # Real user-owned directories are not removed

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


def _expand_partials(
    content: str,
    skills_templates_dir: Path,
    extra_partials: dict[str, str] | None = None,
) -> str:
    """Expand placeholder strings in *content* using partial template files.

    ``extra_partials`` (placeholder → replacement string) are applied first, so
    callers can override or suppress any entry in ``_SKILL_PARTIALS`` by passing
    an empty string.  File-based partials in ``_SKILL_PARTIALS`` are applied
    afterwards for any placeholders still present.  Unknown partial paths are
    left unchanged with a warning.
    """
    if extra_partials:
        for placeholder, replacement in extra_partials.items():
            content = content.replace(placeholder, replacement)
    for placeholder, rel_path in _SKILL_PARTIALS.items():
        if placeholder not in content:
            continue
        partial = skills_templates_dir / rel_path
        if not partial.is_file():
            logger.warning("skills.partial_not_found", path=str(partial))
            continue
        content = content.replace(placeholder, partial.read_text(encoding="utf-8").rstrip())
    return content


def _copy_skill_file(
    src: Path,
    dest: Path,
    overwrite: bool = False,
    skills_templates_dir: Path | None = None,
    extra_partials: dict[str, str] | None = None,
) -> bool:
    """Copy a single skill file, creating parent dirs as needed.

    If ``skills_templates_dir`` is provided, placeholder strings in the file
    content (see ``_SKILL_PARTIALS``) are expanded before writing.  Any
    ``extra_partials`` overrides are applied first (see ``_expand_partials``).

    Returns True if file was installed, False if skipped.
    """
    if dest.exists() and not overwrite:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    content = src.read_text(encoding="utf-8")
    needs_expansion = skills_templates_dir is not None and any(
        p in content for p in _SKILL_PARTIALS
    )
    if needs_expansion or extra_partials:
        base_templates_dir = skills_templates_dir or src.parent.parent
        content = _expand_partials(
            content,
            base_templates_dir,
            extra_partials=extra_partials,
        )
    dest.write_text(content, encoding="utf-8")
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

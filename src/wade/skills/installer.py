"""Skill file installer — copy/symlink skill templates to target projects."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import structlog

from wade.models.config import ProjectConfig
from wade.skills.doc_targets import detect_doc_targets, format_doc_targets
from wade.utils.markdown import has_marker_block, remove_marker_block

# Back-compat re-exports — moved to wade.utils.templates (#382), kept here so the
# ~10 unrelated importers of these loaders (and their tests) keep resolving.
from wade.utils.templates import get_skills_templates_dir as get_skills_templates_dir
from wade.utils.templates import get_templates_dir as get_templates_dir
from wade.utils.templates import get_wade_repo_root as get_wade_repo_root
from wade.utils.templates import load_hook_template as load_hook_template
from wade.utils.templates import load_prompt_template as load_prompt_template

logger = structlog.get_logger()

# Placeholder substitutions applied when skill files are copied to a project.
# Maps placeholder string → relative path inside templates/skills/_partials/.
_SKILL_PARTIALS: dict[str, str] = {
    "{user_interaction_prompt}": "_partials/user-interaction.md",
    "{review_enforcement_rule}": "_partials/review-enforcement-rule.md",
    "{review_plan_step}": "_partials/review-plan-step.md",
    "{review_implementation_closing_step}": "_partials/review-implementation-closing-step.md",
    "{doc_update_step}": "_partials/doc-update-step.md",
    "{knowledge_step}": "_partials/knowledge-step.md",
}


# --- Skill registry: name → list of files ---

SKILL_FILES: dict[str, list[str]] = {
    "task": ["SKILL.md", "plan-format.md", "examples.md"],
    "plan-session": ["SKILL.md", "reference/plan-format.md"],
    "implementation-session": [
        "SKILL.md",
        "reference/recovery.md",
        "reference/pr-summary-format.md",
        "reference/doc-update.md",
        "reference/tracking-issues.md",
        "reference/new-plan.md",
    ],
    "review-pr-comments-session": [
        "SKILL.md",
        "reference/recovery.md",
        "reference/doc-update.md",
    ],
    "deps": ["SKILL.md"],
    "knowledge": ["SKILL.md"],
}

# Skills that should always be overwritten on update
ALWAYS_OVERWRITE = {
    "plan-session",
    "implementation-session",
    "review-pr-comments-session",
    "knowledge",
}

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
CROSS_TOOL_DIRS = [".github/skills", ".agents/skills", ".cursor/skills"]

# LEGACY: standalone guard scripts that older wade versions copied into each
# worktree's ``.{tool}/hooks/`` dir. Guards are now the versioned ``wade hook``
# entry point (no copied scripts), but these paths are still gitignored and
# flagged-if-tracked so worktrees created by an older wade get cleaned up.
PLAN_GUARD_HOOK_FILES = [
    ".claude/hooks/plan_write_guard.py",
    ".cursor/hooks/plan_write_guard.py",
    ".copilot/hooks/plan_write_guard.py",
]
WORKTREE_GUARD_HOOK_FILES = [
    ".claude/hooks/worktree_guard.py",
    ".cursor/hooks/worktree_guard.py",
    ".copilot/hooks/worktree_guard.py",
]

# Wade-managed hook config files written per-session by crossby's hook writers
# (one per supported tool) — never committed. ``.agents/hooks.json`` is
# Antigravity CLI's Stop-hook config (crossby's AntigravityCLIHooksWriter).
HOOK_CONFIG_FILES = [
    ".cursor/hooks.json",
    ".agents/hooks.json",
    ".github/hooks/hooks.json",
    ".codex/hooks.json",
]

# --- Command-to-skill mapping: which skills each session type needs ---

PLAN_SKILLS: list[str] = ["plan-session", "task", "deps", "knowledge"]
DEPS_SKILLS: list[str] = ["deps"]
IMPLEMENT_SKILLS: list[str] = ["implementation-session", "task", "knowledge"]
REVIEW_SKILLS: list[str] = ["review-pr-comments-session", "task", "knowledge"]


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
    # Codex config — crossby's hook writer sets [features].hooks = true here
    # per-session so Codex loads the installed hooks; not user content in a worktree.
    entries.append(".codex/config.toml")

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


# --- Knowledge .gitattributes union-merge block (#358) ---

KNOWLEDGE_ATTRIBUTES_MARKER_START = "# wade:knowledge:start"
KNOWLEDGE_ATTRIBUTES_MARKER_END = "# wade:knowledge:end"


def _gitattributes_pattern(rel_path: Path) -> str:
    """Render a repo-relative path as a *literal* ``.gitattributes`` pattern.

    Escapes gitattributes glob metacharacters (``\\ * ? [``) so the path matches only
    itself, then C-quotes the whole token when it contains whitespace, a double quote, a
    control character, or a leading ``#``/``!`` — any of which would otherwise let git's
    line parser mis-split the pattern from its attributes (a path with a space would read
    only its first whitespace-delimited token as the pattern, orphaning ``merge=union``).
    A plain path — the common case, e.g. ``KNOWLEDGE.md`` — is returned unchanged. git
    C-unquotes a double-quoted pattern *before* glob-matching, so the two escapes compose.
    """
    posix = rel_path.as_posix()
    escaped = re.sub(r"([\\*?\[])", r"\\\1", posix)
    needs_quote = (
        any(ch.isspace() or ord(ch) < 0x20 for ch in posix)
        or '"' in posix
        or posix.startswith(("#", "!"))
    )
    if not needs_quote:
        return escaped
    body = escaped.replace("\\", "\\\\").replace('"', '\\"')
    body = body.replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return f'"{body}"'


def ensure_knowledge_merge_attributes(root: Path, config: ProjectConfig) -> None:
    """Ensure a wade-managed ``merge=union`` block for the knowledge files in ``.gitattributes``.

    The knowledge file (append-only entries) and its JSONL vote log are append-only
    logs; two sessions appending is a textbook conflict, and ``merge=union`` keeps
    **both** sides — exactly right here. Union does the real work locally, where
    ``catchup``/``sync`` merge the base branch into the worktree and the worktree's
    ``.gitattributes`` applies.

    ``.gitattributes`` is a **normal tracked repo file** (not gitignored). Bootstrap
    ensures the block in the worktree so even the first session has it before its
    first catchup/sync; the session commits it alongside its knowledge edit, so it
    merges to main and becomes the server-side backstop. Once main has it, later
    worktrees inherit it and this call is a no-op.

    Idempotent via the shared marker-block helpers. The write is skipped entirely
    when the on-disk content already matches, so a project that already has the block
    is never marked dirty. Paths derive from ``config.knowledge.path`` and its
    ``.ratings.jsonl`` sibling, validated and escaped as literal gitattributes patterns.
    """
    from wade.utils.knowledge_file import resolve_knowledge_path, resolve_ratings_path

    # Resolve + validate the knowledge path the same way knowledge resolution does — an
    # absolute or root-escaping path is rejected, and we write no block rather than a
    # bogus attributes line. Both patterns are rendered relative to root and escaped so a
    # path with a space or a glob metacharacter can't alter what ``merge=union`` matches.
    try:
        knowledge_abs = resolve_knowledge_path(root, config.knowledge)
    except ValueError:
        logger.debug("skills.knowledge_merge_attributes_invalid_path", path=str(root))
        return
    root_abs = root.resolve()
    ratings_abs = resolve_ratings_path(knowledge_abs)
    knowledge_pattern = _gitattributes_pattern(knowledge_abs.relative_to(root_abs))
    ratings_pattern = _gitattributes_pattern(ratings_abs.relative_to(root_abs))
    block = (
        f"{KNOWLEDGE_ATTRIBUTES_MARKER_START}\n"
        f"{knowledge_pattern} merge=union\n"
        f"{ratings_pattern} merge=union\n"
        f"{KNOWLEDGE_ATTRIBUTES_MARKER_END}\n"
    )

    gitattributes = root / ".gitattributes"
    existing = ""
    if gitattributes.is_file():
        existing = gitattributes.read_text(encoding="utf-8")
        if has_marker_block(
            existing, KNOWLEDGE_ATTRIBUTES_MARKER_START, KNOWLEDGE_ATTRIBUTES_MARKER_END
        ):
            existing = remove_marker_block(
                existing, KNOWLEDGE_ATTRIBUTES_MARKER_START, KNOWLEDGE_ATTRIBUTES_MARKER_END
            )

    new_content = existing.rstrip("\n") + "\n\n" + block if existing.strip() else block

    current = gitattributes.read_text(encoding="utf-8") if gitattributes.is_file() else ""
    if new_content == current:
        return  # already correct — don't touch mtime / make the file dirty
    gitattributes.write_text(new_content, encoding="utf-8")
    logger.debug("skills.knowledge_merge_attributes_written", path=str(root))


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
        extra_partials: Placeholder overrides. Caller-supplied values win over
            the ``{doc_targets}`` value computed here from ``project_root``.

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

    computed_partials = {"{doc_targets}": format_doc_targets(detect_doc_targets(project_root))}
    extra_partials = {**computed_partials, **(extra_partials or {})}

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
    afterwards for any placeholders still present, then ``extra_partials`` is
    re-applied so placeholders nested inside a just-expanded file partial (e.g.
    ``{doc_targets}`` inside ``doc-update-step.md``) also resolve.  Unknown
    partial paths are left unchanged with a warning.
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
    if extra_partials:
        for placeholder, replacement in extra_partials.items():
            content = content.replace(placeholder, replacement)
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

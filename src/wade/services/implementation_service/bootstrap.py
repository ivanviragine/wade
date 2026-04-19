"""Worktree bootstrap — file copying, skill installation, hooks, gitignore management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import structlog

from wade.git import repo as git_repo
from wade.models.config import AI_COMMAND_NAMES, ProjectConfig
from wade.models.task import Task
from wade.utils.markdown import has_marker_block, remove_marker_block

logger = structlog.get_logger()

__all__ = [
    "WORKTREE_GITIGNORE_MARKER_END",
    "WORKTREE_GITIGNORE_MARKER_START",
    "_check_tracked_managed_files",
    "_do_suppress_pointer_artifacts",
    "_effective_copy_files",
    "_format_uncommitted_summary",
    "_get_dirty_file_paths",
    "_get_info_exclude_path",
    "_identify_session_dirty_files",
    "_install_guard_hooks",
    "_resolve_worktrees_dir",
    "_suppress_pointer_artifacts",
    "bootstrap_worktree",
    "strip_worktree_gitignore",
    "write_plan_md",
    "write_worktree_gitignore",
]

# --- Worktree gitignore block markers ---
WORKTREE_GITIGNORE_MARKER_START = "# wade:worktree:start"
WORKTREE_GITIGNORE_MARKER_END = "# wade:worktree:end"


def _resolve_worktrees_dir(config: ProjectConfig, repo_root: Path) -> Path:
    """Resolve the worktrees directory from config."""
    wt_dir = config.project.worktrees_dir
    if Path(wt_dir).is_absolute():
        return Path(wt_dir)
    return (repo_root / wt_dir).resolve()


def _format_uncommitted_summary(cwd: Path) -> str:
    """Build a human-readable summary of dirty working tree status."""
    dirty = git_repo.get_dirty_status(cwd)
    parts: list[str] = []
    if dirty["staged"]:
        parts.append(f"{dirty['staged']} staged")
    if dirty["unstaged"]:
        parts.append(f"{dirty['unstaged']} unstaged")
    if dirty["untracked"]:
        parts.append(f"{dirty['untracked']} untracked")
    return ", ".join(parts) if parts else "dirty"


def _get_dirty_file_paths(cwd: Path) -> list[str]:
    """Return file paths from ``git status --porcelain``."""
    return git_repo.get_dirty_file_paths(cwd)


def _identify_session_dirty_files(dirty_paths: list[str]) -> list[str]:
    """Return dirty file paths that are wade session artifacts.

    Matches against ``get_worktree_gitignore_entries()`` — the same set
    of paths the worktree gitignore block hides.
    """
    from wade.skills.installer import get_worktree_gitignore_entries

    entries = get_worktree_gitignore_entries()
    dir_prefixes = [e for e in entries if e.endswith("/")]
    exact_paths = set(e for e in entries if not e.endswith("/"))

    matched: list[str] = []
    for path in dirty_paths:
        if path in exact_paths or any(path.startswith(prefix) for prefix in dir_prefixes):
            matched.append(path)

    return sorted(matched)


def _check_tracked_managed_files(cwd: Path) -> list[str]:
    """Return tracked wade-managed files that should not be committed.

    Checks for:
    - Skill directories from ``MANAGED_SKILL_NAMES``
    - Cross-tool symlink directories
    - Plan guard hook files
    - Worktree guard hook files
    - Session artifact exact paths (``PLAN.md``, ``PR-SUMMARY.md``, etc.)
    """
    from wade.skills.installer import (
        CROSS_TOOL_DIRS,
        MANAGED_SKILL_NAMES,
        PLAN_GUARD_HOOK_FILES,
        WORKTREE_GUARD_HOOK_FILES,
    )

    # Build path roots to check against git index (bare, no trailing slash).
    # git ls-files --cached reports tracked symlinks without trailing slashes,
    # so trailing-slash prefixes would miss them.
    roots: list[str] = [f".claude/skills/{name}" for name in MANAGED_SKILL_NAMES]
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = cwd / cross_dir
        if cross_path.is_symlink() or not cross_path.exists():
            roots.append(cross_dir)
    roots.extend(PLAN_GUARD_HOOK_FILES)
    roots.extend(WORKTREE_GUARD_HOOK_FILES)
    # Session artifact exact paths (never user content)
    roots.extend(["PLAN.md", "PR-SUMMARY.md", ".commit-msg", ".wade", ".wade-managed"])

    all_tracked = git_repo.list_tracked_files(cwd)
    tracked = [
        path
        for path in all_tracked
        if any(path == root or path.startswith(f"{root}/") for root in roots)
    ]
    return sorted(tracked)


def write_plan_md(
    worktree_path: Path,
    task: Task,
    plan_content: str | None = None,
) -> Path:
    """Write PLAN.md to the worktree.

    Args:
        worktree_path: Worktree directory.
        task: Task with metadata (id, title, url).
        plan_content: Optional plan content to use instead of task.body.
            When provided (e.g. extracted from a draft PR), this takes priority.
    """
    plan_path = worktree_path / "PLAN.md"
    lines = [
        f"# Issue #{task.id}: {task.title}",
        "",
    ]
    body = plan_content if plan_content is not None else task.body
    if body:
        lines.append(body)
    if task.url:
        lines.append("")
        lines.append(f"URL: {task.url}")

    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("implementation.plan_md_written", path=str(plan_path))
    return plan_path


def _install_guard_hooks(
    worktree_path: Path,
    *,
    guard_type: str,
) -> None:
    """Copy a guard script and configure all AI tool hooks.

    Args:
        worktree_path: Worktree directory.
        guard_type: ``"worktree"`` or ``"plan"`` — selects the guard script
            and the matching configure function on each tool module.
    """
    from wade.hooks import get_guard_script_path, get_worktree_guard_script_path

    if guard_type == "worktree":
        guard_src = get_worktree_guard_script_path()
        script_name = "worktree_guard.py"
    else:
        guard_src = get_guard_script_path()
        script_name = "plan_write_guard.py"

    tool_dirs = [".claude/hooks", ".cursor/hooks", ".copilot/hooks", ".gemini/hooks"]
    for tool_dir in tool_dirs:
        dest_dir = worktree_path / tool_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(guard_src, dest_dir / script_name)

    from crossby.config import claude_allowlist, copilot_hooks, cursor_hooks, gemini_hooks

    configure_fns = [
        (claude_allowlist, ".claude"),
        (cursor_hooks, ".cursor"),
        (copilot_hooks, ".copilot"),
        (gemini_hooks, ".gemini"),
    ]
    hook_fn_name = f"configure_{guard_type}_hooks"
    for module, tool_dir_prefix in configure_fns:
        fn = getattr(module, hook_fn_name)
        fn(worktree_path, worktree_path / tool_dir_prefix / "hooks" / script_name)

    logger.info(f"implementation.{guard_type}_guard_hooks_installed", path=str(worktree_path))


def _effective_copy_files(config: ProjectConfig) -> list[str]:
    """Compute the full list of files to copy into a new worktree.

    Merges user-configured copy_to_worktree with internal wade files
    that must always be present (.wade.yml, knowledge path + ratings when enabled).
    """
    from wade.services.knowledge_service import resolve_ratings_path

    internal: list[str] = [".wade.yml"]
    if config.knowledge.enabled:
        kpath = config.knowledge.path
        if not kpath.startswith("/") and ".." not in kpath.split("/"):
            internal.append(kpath)
            internal.append(str(resolve_ratings_path(Path(kpath))))

    files: list[str] = list(config.hooks.copy_to_worktree)
    for f in internal:
        if f not in files:
            files.append(f)
    return files


def _get_info_exclude_path(worktree_path: Path) -> Path | None:
    """Return the ``info/exclude`` path for the given worktree.

    In a linked worktree this resolves to the worktree-specific git dir
    (e.g. ``<main>/.git/worktrees/<name>/info/exclude``).
    """
    try:
        raw = git_repo.get_git_dir(worktree_path)
    except OSError:
        return None
    if raw is None:
        return None
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = worktree_path / git_dir
    return git_dir / "info" / "exclude"


def write_worktree_gitignore(worktree_path: Path) -> None:
    """Append a ``# wade:worktree:start`` block to ``.gitignore`` in the worktree.

    Lists **specific files** (never directories, except ``.wade/``) so that
    user-owned files in the same parent directories are never hidden.

    Also adds conditional entries for cross-tool symlinks (only when wade
    created them) and untracked pointer files.
    """
    from wade.skills.installer import CROSS_TOOL_DIRS, get_worktree_gitignore_entries

    entries = list(get_worktree_gitignore_entries())

    # Conditional cross-tool symlinks (only if wade created them as symlinks)
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = worktree_path / cross_dir
        if cross_path.is_symlink():
            entries.append(cross_dir)

    # Untracked pointer files (replacing broken info/exclude approach)
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = worktree_path / name
        if not (target.exists() or target.is_symlink()):
            continue
        if not git_repo.is_file_tracked(worktree_path, name):
            entries.append(name)

    block = (
        f"\n{WORKTREE_GITIGNORE_MARKER_START}\n"
        + "\n".join(entries)
        + f"\n{WORKTREE_GITIGNORE_MARKER_END}\n"
    )

    gitignore = worktree_path / ".gitignore"
    if gitignore.is_file():
        existing = gitignore.read_text(encoding="utf-8")
        # Remove existing worktree block if present (idempotent)
        if has_marker_block(
            existing, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
        ):
            existing = remove_marker_block(
                existing, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
            )
        gitignore.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8")
    else:
        # No .gitignore exists — write entries to info/exclude instead of
        # creating an untracked file that would fail is_clean() checks.
        exclude = _get_info_exclude_path(worktree_path)
        if exclude is not None:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            existing_exc = ""
            if exclude.is_file():
                existing_exc = exclude.read_text(encoding="utf-8")
            if has_marker_block(
                existing_exc,
                WORKTREE_GITIGNORE_MARKER_START,
                WORKTREE_GITIGNORE_MARKER_END,
            ):
                existing_exc = remove_marker_block(
                    existing_exc,
                    WORKTREE_GITIGNORE_MARKER_START,
                    WORKTREE_GITIGNORE_MARKER_END,
                )
            new_content = (
                existing_exc.rstrip("\n") + "\n" + block
                if existing_exc.strip()
                else block.lstrip("\n")
            )
            exclude.write_text(new_content, encoding="utf-8")
        else:
            # Fallback: create .gitignore anyway (best-effort)
            gitignore.write_text(block.lstrip("\n"), encoding="utf-8")

    logger.debug("implementation.worktree_gitignore_written", path=str(worktree_path))


def strip_worktree_gitignore(worktree_path: Path) -> None:
    """Remove the ``# wade:worktree:start`` block from ``.gitignore`` and ``info/exclude``.

    Preserves any user content outside the block.  If ``.gitignore`` was
    created solely for the worktree block (empty after stripping), the file
    is deleted so no untracked residue remains.
    """
    # Clean .gitignore
    gitignore = worktree_path / ".gitignore"
    if gitignore.is_file():
        existing = gitignore.read_text(encoding="utf-8")
        if has_marker_block(
            existing, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
        ):
            cleaned = remove_marker_block(
                existing, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
            )
            if cleaned.strip():
                gitignore.write_text(cleaned, encoding="utf-8")
            else:
                gitignore.unlink(missing_ok=True)
            logger.debug("implementation.worktree_gitignore_stripped", path=str(worktree_path))

    # Clean info/exclude (used when .gitignore was not tracked)
    exclude = _get_info_exclude_path(worktree_path)
    if exclude is not None and exclude.is_file():
        exc_content = exclude.read_text(encoding="utf-8")
        if has_marker_block(
            exc_content, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
        ):
            cleaned_exc = remove_marker_block(
                exc_content, WORKTREE_GITIGNORE_MARKER_START, WORKTREE_GITIGNORE_MARKER_END
            )
            exclude.write_text(cleaned_exc, encoding="utf-8")
            logger.debug("implementation.info_exclude_stripped", path=str(worktree_path))


def _suppress_pointer_artifacts(worktree_path: Path) -> None:
    """Prevent pointer-injected files from appearing dirty in the worktree.

    Called after ensure_pointer() so git status checks (is_clean) remain clean.
    Tracked files (e.g. an existing AGENTS.md) are marked ``--skip-worktree``
    so local modifications are invisible to git status.  Untracked pointer
    files are handled by ``write_worktree_gitignore()`` instead.

    Failures are silently swallowed — git commands may not be available in all
    contexts (tests, unusual setups), and a failed suppression is not fatal.
    """
    try:
        _do_suppress_pointer_artifacts(worktree_path)
    except Exception:
        logger.debug("implementation.suppress_pointer_skipped", path=str(worktree_path))


def _do_suppress_pointer_artifacts(worktree_path: Path) -> None:
    """Internal implementation of _suppress_pointer_artifacts.

    Only handles **tracked** pointer files via ``--skip-worktree``.
    Untracked pointer files are handled by ``write_worktree_gitignore()``
    which includes them in the worktree gitignore block.
    """
    pointer_files = ("AGENTS.md", "CLAUDE.md")

    for name in pointer_files:
        target = worktree_path / name
        if not (target.exists() or target.is_symlink()):
            continue
        if git_repo.is_file_tracked(worktree_path, name):
            git_repo.skip_worktree_file(worktree_path, name)
            logger.debug("implementation.skip_worktree", file=name)


def bootstrap_worktree(
    worktree_path: Path,
    config: ProjectConfig,
    repo_root: Path,
    skills: list[str] | None = None,
    plan_mode: bool = False,
    selected_ai_tool: str | None = None,
) -> None:
    """Run post-creation bootstrap: copy files, install skills, run hooks.

    Args:
        worktree_path: Path to the worktree directory.
        config: Project configuration.
        repo_root: Root of the main repository checkout.
        skills: If provided, install only the listed skills instead of all.
        plan_mode: If True, install file-write guard hooks for plan sessions.
        selected_ai_tool: Effective AI tool for this session (e.g. ``"cursor"``).
            When provided, takes precedence over persisted config when deciding
            whether to configure tool-specific worktree settings.
    """
    # Copy configured files + internal wade files that must always be present
    copy_files = _effective_copy_files(config)
    for filename in copy_files:
        src = repo_root / filename
        dest = worktree_path / filename
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logger.debug("implementation.bootstrap_copy", file=filename)

    # Install skill files — not tracked by git so worktrees don't inherit them
    from wade.skills.installer import get_wade_repo_root, install_skills

    is_self = repo_root.resolve() == get_wade_repo_root().resolve()

    # Suppress review step placeholders when reviews are explicitly disabled.
    # An empty string (or disabled one-liner) overrides the default file-based partial.
    skill_extra_partials: dict[str, str] = {}
    if config.ai.review_plan.enabled is False:
        skill_extra_partials["{review_plan_step}"] = (
            "5. ~~**Review**~~ — skipped (`review_plan.enabled: false` in `.wade.yml`)."
        )
    if config.ai.review_implementation.enabled is False:
        skill_extra_partials["{review_enforcement_rule}"] = ""
        skill_extra_partials["{review_implementation_closing_step}"] = (
            "**Step 1 — ~~Review~~** — skipped"
            " (`review_implementation.enabled: false` in `.wade.yml`)."
        )
    if is_self:
        # Worktree has its own templates/ checkout — symlink to those
        wt_templates = worktree_path / "templates" / "skills"
        install_skills(
            worktree_path,
            is_self_init=True,
            force=True,
            templates_dir=wt_templates,
            skills=skills,
            extra_partials=skill_extra_partials or None,
        )
    else:
        install_skills(
            worktree_path,
            is_self_init=False,
            force=True,
            skills=skills,
            extra_partials=skill_extra_partials or None,
        )
    logger.debug("implementation.bootstrap_skills", path=str(worktree_path))

    # Inject AGENTS.md pointer into worktree (after skills, which may add AGENTS.md content)
    from wade.skills import pointer

    pointer.ensure_pointer(worktree_path)
    _suppress_pointer_artifacts(worktree_path)
    logger.debug("implementation.bootstrap_pointer", path=str(worktree_path))

    # Always propagate allowlist to worktree — configure_allowlist is idempotent
    from crossby.config.claude_allowlist import configure_allowlist

    configure_allowlist(worktree_path, config.permissions.allowed_commands)

    # Propagate Cursor allowlist to worktree's per-project .cursor/cli.json.
    # Check both global cursor config and whether cursor is the project's AI tool —
    # the project-level .cursor/cli.json is no longer written to main (gitignored).
    from crossby.config.cursor_allowlist import configure_allowlist as configure_cursor_allowlist
    from crossby.config.cursor_allowlist import is_allowlist_configured as is_cursor_configured

    cursor_in_config = any(config.get_ai_tool(cmd) == "cursor" for cmd in [None, *AI_COMMAND_NAMES])
    if (
        selected_ai_tool == "cursor"
        or cursor_in_config
        or is_cursor_configured()
        or is_cursor_configured(repo_root)
    ):
        configure_cursor_allowlist(worktree_path, config.permissions.allowed_commands)

    # Propagate Gemini policy to worktree's .gemini/policies/wade.toml.
    # Gemini CLI uses the Policy Engine (TOML files) instead of --allowed-tools.
    gemini_in_config = any(config.get_ai_tool(cmd) == "gemini" for cmd in [None, *AI_COMMAND_NAMES])
    if (selected_ai_tool == "gemini" or gemini_in_config) and config.permissions.allowed_commands:
        from crossby.sync.permissions import GeminiPermissionWriter

        GeminiPermissionWriter.write(worktree_path, config.permissions.allowed_commands)

    # Run post-create hook
    if config.hooks.post_worktree_create:
        hook_path = repo_root / config.hooks.post_worktree_create
        if hook_path.is_file():
            try:
                subprocess.run(
                    [str(hook_path)],
                    cwd=str(worktree_path),
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                logger.info("implementation.hook_ran", hook=config.hooks.post_worktree_create)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Bootstrap hook timed out after 60 seconds: {hook_path}") from e
            except subprocess.CalledProcessError as e:
                hook_path_str = str(hook_path)
                logger.warning(
                    "implementation.hook_failed",
                    hook=config.hooks.post_worktree_create,
                    hook_path=hook_path_str,
                    error=e.stderr.decode("utf-8", errors="replace") if e.stderr else "",
                    msg=f"Hook script failed: {hook_path_str}. Check logs for details.",
                )

    # Install file-write guard hooks last so post-create scripts cannot
    # overwrite the guarded config files.
    _install_guard_hooks(worktree_path, guard_type="plan" if plan_mode else "worktree")

    # Write worktree gitignore block AFTER all file generation so the entry
    # list is complete (skills, hooks, settings, pointer are all in place).
    write_worktree_gitignore(worktree_path)

    # Apply --skip-worktree on .gitignore if it is tracked so modifications
    # from the worktree block don't appear in git status.
    if git_repo.is_file_tracked(worktree_path, ".gitignore"):
        git_repo.skip_worktree_file(worktree_path, ".gitignore")
        logger.debug("implementation.skip_worktree", file=".gitignore")

"""Worktree bootstrap — file copying, skill installation, hooks, gitignore management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from crossby.models.ai import AIToolID
    from crossby.sync.base import AbstractSyncWriter

from wade.git import repo as git_repo
from wade.models.config import (
    AI_COMMAND_NAMES,
    WADE_BASE_ALLOWLIST_PATTERN,
    ProjectConfig,
    with_wade_base_pattern,
)
from wade.models.hooks import StopGuard
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
    "_install_managed_git_hooks",
    "_install_post_tool_use_lint_hook",
    "_install_stop_hook",
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


# Canonical write-family tool names the guard scopes to; crossby's hook writers
# translate these to each tool's native tool names + matcher (Cursor Edit->Write,
# Copilot Write->write, agy Write->write_to_file, …).
#
# ``Bash`` is the canonical *shell* token, and it is here because a shell call is
# a write channel: `printf ... > ../main-repo/src/app.py` bypasses every
# write-tool matcher. crossby maps it per tool (Cursor ``Shell``, agy
# ``run_command``, Claude/Codex ``Bash`` unchanged), and ``wade-hook`` routes the
# resulting payload to ``shell_containment`` because crossby deliberately reports
# ``is_write=False`` for shell tool names.
_GUARD_SHELL_TOOL = "Bash"
_GUARD_WRITE_TOOLS = ["Edit", "Write", "Delete", "NotebookEdit", _GUARD_SHELL_TOOL]

# Seconds before a tool abandons the PreToolUse guard. Deliberately short: this
# runs on every write, and each tool's own default is generous enough (Cursor 60,
# Codex 600, agy 30, Copilot 30) that a hung hook stalls the agent noticeably.
# crossby validates > 0 and emits it under each tool's native key.
_GUARD_HOOK_TIMEOUT_SECONDS = 10


def _hook_writers() -> list[tuple[AIToolID, AbstractSyncWriter]]:
    """Tools wade installs guard hooks into, paired with crossby's writer for each.

    Shared by the PreToolUse and Stop installers so the two lists cannot drift,
    and read by the dialect-map parity test — a tool added here without matching
    entries in ``wade.hooks.cli``'s ``_TOOL_DIALECTS`` / ``_TOOL_STOP_DIALECTS``
    would silently fall back to the default output shape, so the test fails first.

    Imported lazily: ``crossby.sync.hooks`` pulls in every adapter, which is a
    cost worth paying once per worktree bootstrap but not on every ``wade`` start.
    """
    from crossby.models.ai import AIToolID
    from crossby.sync.hooks import (
        AntigravityCLIHooksWriter,
        ClaudeHooksWriter,
        CodexHooksWriter,
        CopilotHooksWriter,
        CursorHooksWriter,
    )

    # Antigravity CLI joined this list in crossby 0.13, which added agy's native
    # tool names to ``_TOOL_NAME_MAP`` (``Write`` -> ``write_to_file`` etc.).
    # Before that the canonical matcher compiled to names agy never emits, so the
    # hook installed and then never fired — protection in appearance only.
    return [
        (AIToolID.CLAUDE, ClaudeHooksWriter()),
        (AIToolID.CURSOR, CursorHooksWriter()),
        (AIToolID.COPILOT, CopilotHooksWriter()),
        (AIToolID.CODEX, CodexHooksWriter()),
        (AIToolID.ANTIGRAVITY_CLI, AntigravityCLIHooksWriter()),
    ]


def _log_sync_result(result: object, tool_id: object) -> None:
    """Surface a crossby ``SyncResult`` — warn on writer errors, else stay quiet.

    Hook writers return a ``SyncResult`` describing what happened. A silently
    discarded ``action == "error"`` (e.g. a pre-existing malformed config file)
    would leave a session unprotected without any signal, so log those; the
    ``message`` also carries any per-tool manual-fix notes worth recording.
    """
    action = getattr(result, "action", None)
    message = getattr(result, "message", "") or ""
    if action == "error":
        logger.warning(
            "implementation.hook_sync_error",
            tool=getattr(tool_id, "value", str(tool_id)),
            detail=message,
        )
    elif message:
        logger.debug(
            "implementation.hook_sync_note",
            tool=getattr(tool_id, "value", str(tool_id)),
            detail=message,
        )


def _install_guard_hooks(
    worktree_path: Path,
    *,
    guard_type: str,
) -> None:
    """Install a wade write-guard hook for ``guard_type`` into each tool's config.

    Points every tool's ``pre_tool_use`` hook at the versioned ``wade hook``
    entry point instead of copying a standalone script into the worktree — so the
    guard logic can never drift from the installed wade version. Per-tool config
    format and dialect are handled by crossby's hook writers; the decision logic
    lives in ``wade hook`` / ``wade.hooks.policies``.

    For tools that hard-sandbox writes (e.g. Codex ``--sandbox workspace-write``)
    the worktree-containment guard is **narrowed to the shell token** rather than
    skipped: the sandbox already covers tool-call writes, but it also permits
    ``/tmp`` and ``$TMPDIR``, so a shell redirect can still land outside the
    worktree. The plan guard is **always** installed in full — it is finer-grained
    than any directory sandbox (it must block source writes *inside* the workspace).

    Args:
        worktree_path: Worktree directory.
        guard_type: ``"worktree"`` or ``"plan"``.
    """
    import shlex

    from crossby.ai_tools import AbstractAITool
    from crossby.models.config import HookEntry
    from crossby.sync.base import SyncData

    root = shlex.quote(str(worktree_path))
    for tool_id, writer in _hook_writers():
        caps = AbstractAITool.get(tool_id).capabilities()
        tools = _GUARD_WRITE_TOOLS
        if guard_type == "worktree" and caps.sandboxes_writes:
            # A native write sandbox (Codex `--sandbox workspace-write`) already
            # confines *tool-call* writes to the workspace, so the file-write half
            # of this guard is redundant. It is not redundant for the shell half:
            # workspace-write also permits /tmp and $TMPDIR, so
            # `printf x > /tmp/pwn` is sandbox-legal yet outside the worktree.
            # Narrow the matcher to the shell token rather than skipping the tool.
            tools = [_GUARD_SHELL_TOOL]
        command = (
            f"wade-hook pre_tool_use --guard {guard_type} --tool {tool_id.value} --root {root}"
        )
        # fail_closed: block the write if the hook itself crashes/times out. Only
        # Cursor honors this (it defaults to fail-open, which silently defeats a
        # security guard); other writers ignore the field because their hooks
        # already fail closed. Cursor's published docs name `failClosed` only for
        # beforeShellExecution/beforeMCPExecution/beforeReadFile, but crossby 0.13
        # confirmed against cursor-agent's bundled hook runtime that preToolUse is
        # in the fail-closed event set and a failed hook there becomes
        # {"permission": "deny"} — under-documented, not unsupported.
        #
        # Deliberately not set on the Stop hook, which must stay fail-open so a bug
        # never traps the agent in a session it cannot end.
        hook = HookEntry(
            event="pre_tool_use",
            tools=tools,
            command=command,
            fail_closed=True,
            timeout=_GUARD_HOOK_TIMEOUT_SECONDS,
        )
        _log_sync_result(writer.sync(SyncData(hooks=[hook]), worktree_path), tool_id)

    logger.info(f"implementation.{guard_type}_guard_hooks_installed", path=str(worktree_path))


def _install_stop_hook(
    worktree_path: Path, *, guard: StopGuard = StopGuard.SESSION_COMPLETE
) -> None:
    """Install a Stop-hook completion reminder into each capable tool.

    On session Stop, ``wade hook stop --guard {guard}`` nudges (once) when the
    session's closing artifact is missing — enforcing the closing step rather than
    relying on the skill checklist. Two guards share this installer:

    - ``session-complete`` (impl/review sessions) nudges when the branch has
      commits ahead of its base and no current ``.wade/done@<HEAD>`` marker.
    - ``plan-complete`` (plan sessions) nudges when the plan dir holds no valid
      ``PLAN*.md`` yet.

    Installed only for tools that fire a blocking Stop hook
    (``supports_stop_hook``), which as of crossby 0.13 is every tool wade drives:
    Copilot joined once its ``agentStop`` event and blocking
    ``{"decision": "block", "reason": …}`` contract were confirmed. Merged
    alongside the PreToolUse write guard by crossby's hook writers.

    The per-tool Stop *shape* differs (Claude/Codex/Copilot block, Cursor sends a
    ``followup_message``, agy inverts polarity with ``{"decision": "continue"}``);
    ``wade-hook`` resolves that from its own stop-dialect map, not from here.
    """
    import shlex

    from crossby.ai_tools import AbstractAITool
    from crossby.models.config import HookEntry
    from crossby.sync.base import SyncData

    root = shlex.quote(str(worktree_path))
    for tool_id, writer in _hook_writers():
        if not AbstractAITool.get(tool_id).capabilities().supports_stop_hook:
            continue
        command = f"wade-hook stop --guard {guard.value} --tool {tool_id.value} --root {root}"
        hook = HookEntry(event="stop", tools=[], command=command)
        _log_sync_result(writer.sync(SyncData(hooks=[hook]), worktree_path), tool_id)

    logger.info("implementation.stop_hook_installed", path=str(worktree_path), guard=guard.value)


# Canonical write-family tool names the PostToolUse lint feedback fires on. Scoped
# to file-write tools (not Bash: a shell call has no path to lint; not Delete: the
# file is gone, so linting it only injects "file not found" noise). crossby's hook
# writers translate these per tool.
_LINT_FEEDBACK_TOOLS = ["Edit", "Write", "NotebookEdit"]


def _install_managed_git_hooks(worktree_path: Path, config: ProjectConfig) -> None:
    """Install the pre-push backstop + opt-in pre-commit/commit-msg gates in one batch.

    Gathers the hooks whose config gates are on, then installs them via a single
    :func:`install_worktree_git_hooks` call — the batch guarantees every prior
    user hook is captured before wade sets ``core.hooksPath`` (a per-hook
    capture-after-set would miss a user's custom repo-level hooksPath). All hooks
    are optional and the installer graceful-degrades on old git, so an install
    failure is swallowed to a warning and never crashes bootstrap.
    """
    from wade.skills.installer import (
        build_commit_msg_hook_script,
        build_pre_commit_hook_script,
        install_worktree_git_hooks,
        load_hook_template,
    )

    hooks: dict[str, str] = {}

    if config.done.pre_push_backstop:
        try:
            hooks["pre-push"] = load_hook_template("pre-push")
        except FileNotFoundError:
            logger.warning("implementation.pre_push_template_missing")

    pre_commit = config.hooks.pre_commit
    if pre_commit.lint or pre_commit.test:
        hooks["pre-commit"] = build_pre_commit_hook_script(pre_commit.lint, pre_commit.test)

    if config.hooks.commit_msg.conventional:
        hooks["commit-msg"] = build_commit_msg_hook_script()

    if not hooks:
        return

    try:
        if not install_worktree_git_hooks(worktree_path, hooks):
            logger.info(
                "implementation.git_hooks_skipped",
                path=str(worktree_path),
                hooks=sorted(hooks),
            )
    except Exception:
        logger.warning(
            "implementation.git_hooks_error",
            path=str(worktree_path),
            hooks=sorted(hooks),
            exc_info=True,
        )


def _install_post_tool_use_lint_hook(worktree_path: Path, config: ProjectConfig) -> None:
    """Install PostToolUse in-turn lint feedback into each context-capable tool.

    Opt-in via ``hooks.post_tool_use.enabled``. The lint command is
    ``post_tool_use.lint_cmd`` (**file-scoped** — the edited path is appended) or,
    when unset, ``pre_commit.lint`` run **unscoped** (whole-repo, which fires on
    every edit — prefer configuring a file-scoped ``lint_cmd``). The resolved
    command + timeout are baked into the hook argv so the lean ``wade-hook`` entry
    point never loads config. Gated to tools whose output dialect supports context
    injection (dialect ≠ ``DECISION``), so Antigravity CLI is skipped rather than
    firing a no-op subprocess per edit. Never fail-closed: this path must not block.
    """
    import shlex

    ptu = config.hooks.post_tool_use
    if not ptu.enabled:
        return
    lint_cmd = ptu.lint_cmd or config.hooks.pre_commit.lint
    if not lint_cmd:
        return

    from crossby.ai_tools import AbstractAITool
    from crossby.models.ai import HookOutputDialect
    from crossby.models.config import HookEntry
    from crossby.sync.base import SyncData

    scoped = bool(ptu.lint_cmd)  # file-scoped only when an explicit lint_cmd is set
    root = shlex.quote(str(worktree_path))
    quoted_cmd = shlex.quote(lint_cmd)

    installed_any = False
    for tool_id, writer in _hook_writers():
        # Context-capable tools only. agy's DECISION dialect has no verified
        # context-injection channel, so a PostToolUse hook there would fire a
        # subprocess on every edit and discard the result — pure cost.
        if AbstractAITool.get(tool_id).capabilities().hook_output_dialect is (
            HookOutputDialect.DECISION
        ):
            continue
        command = (
            f"wade-hook post_tool_use --tool {tool_id.value} --root {root} "
            f"--lint-cmd {quoted_cmd} --timeout {ptu.timeout}"
        )
        if not scoped:
            command += " --unscoped"
        # No fail_closed: PostToolUse must never block. Its own timeout bounds the
        # per-edit cost at the tool's hook-runner level too.
        hook = HookEntry(
            event="post_tool_use",
            tools=_LINT_FEEDBACK_TOOLS,
            command=command,
            timeout=ptu.timeout,
        )
        _log_sync_result(writer.sync(SyncData(hooks=[hook]), worktree_path), tool_id)
        installed_any = True

    if installed_any:
        logger.info("implementation.post_tool_use_lint_installed", path=str(worktree_path))


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
            "7. ~~**Review**~~ — skipped (`review_plan.enabled: false` in `.wade.yml`)."
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

    # Always propagate allowlist to worktree — configure_allowlist is idempotent.
    # wade's base pattern is guaranteed so ``wade ...`` stays pre-authorized even
    # when a project narrows permissions.allowed_commands.
    from crossby.config.claude_allowlist import configure_allowlist

    wade_patterns = with_wade_base_pattern(config.permissions.allowed_commands)
    configure_allowlist(worktree_path, wade_patterns)

    # Propagate Cursor allowlist to worktree's per-project .cursor/cli.json.
    # Check both global cursor config and whether cursor is the project's AI tool —
    # the project-level .cursor/cli.json is no longer written to main (gitignored).
    from crossby.config.cursor_allowlist import configure_allowlist as configure_cursor_allowlist
    from crossby.config.cursor_allowlist import is_allowlist_configured as is_cursor_configured

    cursor_in_config = any(config.get_ai_tool(cmd) == "cursor" for cmd in [None, *AI_COMMAND_NAMES])
    cursor_marker = [WADE_BASE_ALLOWLIST_PATTERN]
    if (
        selected_ai_tool == "cursor"
        or cursor_in_config
        or is_cursor_configured(patterns=cursor_marker)
        or is_cursor_configured(repo_root, cursor_marker)
    ):
        configure_cursor_allowlist(worktree_path, wade_patterns)

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

    # Every session gets a Stop-hook completion reminder, but the guard differs:
    # plan sessions nudge to write a valid plan; impl/review sessions nudge to run
    # `done` (and also get the pre-push backstop that hard-enforces it).
    if plan_mode:
        _install_stop_hook(worktree_path, guard=StopGuard.PLAN_COMPLETE)
    else:
        _install_stop_hook(worktree_path)

        # Managed git hooks: the pre-push backstop (makes `done` hard to skip) plus
        # the opt-in pre-commit / commit-msg quality gates. Installed together in
        # one batch so every prior user hook is captured before wade sets
        # core.hooksPath. All optional + graceful-degrading — never crash bootstrap.
        _install_managed_git_hooks(worktree_path, config)

        # PostToolUse in-turn lint feedback (opt-in) for context-capable tools.
        _install_post_tool_use_lint_hook(worktree_path, config)

    # Write worktree gitignore block AFTER all file generation so the entry
    # list is complete (skills, hooks, settings, pointer are all in place).
    write_worktree_gitignore(worktree_path)

    # Apply --skip-worktree on .gitignore if it is tracked so modifications
    # from the worktree block don't appear in git status.
    if git_repo.is_file_tracked(worktree_path, ".gitignore"):
        git_repo.skip_worktree_file(worktree_path, ".gitignore")
        logger.debug("implementation.skip_worktree", file=".gitignore")

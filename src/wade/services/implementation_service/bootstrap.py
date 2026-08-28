"""Worktree bootstrap — file copying, skill installation, hooks, gitignore management."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from collections import Counter
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
from wade.models.hooks import SessionPhase, StopGuard
from wade.models.task import Task
from wade.models.workflow import SESSION_PHASE_TO_KIND, SessionKind
from wade.skills.pointer import is_pointer_only
from wade.utils.markdown import has_marker_block, remove_marker_block

logger = structlog.get_logger()

__all__ = [
    "WORKTREE_GITIGNORE_MARKER_END",
    "WORKTREE_GITIGNORE_MARKER_START",
    "_check_tracked_managed_files",
    "_conditional_worktree_gitignore_entries",
    "_do_suppress_pointer_artifacts",
    "_effective_copy_files",
    "_format_uncommitted_summary",
    "_get_dirty_file_paths",
    "_get_info_exclude_path",
    "_identify_session_dirty_files",
    "_install_guard_hooks",
    "_install_managed_git_hooks",
    "_install_post_tool_use_lint_hook",
    "_install_session_start_hook",
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
    """Return file paths from ``git status --porcelain --untracked-files=all``."""
    return git_repo.get_dirty_file_paths(cwd)


def _conditional_worktree_gitignore_entries(worktree_path: Path) -> list[str]:
    """Compute the entries ``write_worktree_gitignore()`` adds conditionally.

    Cross-tool symlinks (only when wade created them) and untracked pointer
    files depend on the target project's state, so they can't live in the
    static ``get_worktree_gitignore_entries()`` list. Shared by
    ``write_worktree_gitignore()`` and ``_identify_session_dirty_files()`` so
    the two never recognize a different set of artifacts.
    """
    from wade.skills.installer import CROSS_TOOL_DIRS

    entries: list[str] = []

    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = worktree_path / cross_dir
        if cross_path.is_symlink():
            entries.append(cross_dir)

    for name in ("AGENTS.md", "CLAUDE.md"):
        target = worktree_path / name
        if not (target.exists() or target.is_symlink()):
            continue
        if git_repo.is_file_tracked(worktree_path, name):
            continue
        if is_pointer_only(target):
            entries.append(name)

    return entries


def _identify_session_dirty_files(dirty_paths: list[str], worktree_path: Path) -> list[str]:
    """Return dirty file paths that are wade session artifacts.

    Matches against ``get_worktree_gitignore_entries()`` plus
    ``_conditional_worktree_gitignore_entries()`` — the same set of paths
    ``write_worktree_gitignore()`` hides, static and conditional alike.

    A name match alone isn't enough: session artifacts are never committed, so
    a matched path that is tracked in the git index is real content wearing an
    artifact's name — a staged ``git mv user.txt PLAN.md`` reports only the new
    path, and a tracked ``.claude/settings.json`` can be genuine repo content —
    not regenerable scaffold. Excluding tracked matches here sends them through
    ``genuine`` instead, so the caller falls back to the conservative prompt.
    """
    from wade.skills.installer import get_worktree_gitignore_entries

    entries = list(get_worktree_gitignore_entries())
    entries.extend(_conditional_worktree_gitignore_entries(worktree_path))
    dir_prefixes = [e for e in entries if e.endswith("/")]
    exact_paths = set(e for e in entries if not e.endswith("/"))

    tracked = set(git_repo.list_tracked_files(worktree_path))

    matched: list[str] = []
    for path in dirty_paths:
        if path in tracked:
            continue
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
#
# ``MultiEdit`` is the batched-edit counterpart of ``Edit`` and must be listed
# separately: every tool matcher these compile to is matched **whole**, not by
# substring, so agy's ``multi_replace_file_content`` is not covered by the
# ``replace_file_content`` alternative that ``Edit`` produces — omitting it let a
# batched agy edit through with no hook firing at all. crossby has mapped it
# since 0.13 (agy ``multi_replace_file_content``, Cursor ``Write`` — deduped
# against ``Edit``); on Claude/Codex it passes through unchanged and is simply
# inert if the tool has no such name, which is the cheap side of the trade.
_GUARD_SHELL_TOOL = "Bash"
_GUARD_WRITE_TOOLS = ["Edit", "MultiEdit", "Write", "Delete", "NotebookEdit", _GUARD_SHELL_TOOL]

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


def _session_start_command(tool_value: str, quoted_root: str, phase_value: str) -> str:
    """Build the installed ``wade-hook session_start`` command for one tool + phase.

    Single source of truth so the install path and the stale-phase revocation
    (``hooks_remove``) in :func:`_install_session_start_hook` construct
    byte-identical commands: crossby matches a removal by exact command string, so
    any drift between the two would silently fail to reconcile a prior phase's hook.

    ``--guard context`` is a descriptive label, not a dispatch key: the runtime
    routes session_start by the *event* positional (``_is_session_start``), never by
    ``--guard``. It documents intent and is asserted by the install tests.
    """
    return (
        f"wade-hook session_start --guard context --tool {tool_value} "
        f"--root {quoted_root} --phase {phase_value}"
    )


def _install_session_start_hook(worktree_path: Path, *, phase: SessionPhase) -> None:
    """Install a SessionStart context-injection hook into each capable tool.

    On every SessionStart source (startup / resume / compact / clear / fork),
    ``wade-hook session_start`` re-injects a compact, phase-gated task reminder as
    ``additionalContext`` — countering context decay over long sessions, the
    largest single loss being *compaction*. Non-blocking, like the Stop hook (no
    ``fail_closed``).

    Installed only for tools that fire a SessionStart hook
    (``supports_session_start_hook`` — Claude/Codex/Copilot/Cursor as of crossby
    0.17; **agy is skipped**, its DECISION dialect having no verified context
    channel, so it degrades to the launch-loaded workflow). This mirrors how
    :func:`_install_stop_hook` gates on ``supports_stop_hook``.

    ``tools=[]`` is **load-bearing**: ``_tools_to_matcher([])`` returns ``.*``, so
    the Claude/Codex SessionStart matcher matches every ``source`` (the matcher is
    tested against the source, not a tool name). Narrowing it would silently drop
    resume/compaction re-injection. The per-tool payload *shape* (nested vs flat
    ``additionalContext`` vs Cursor ``additional_context``) is resolved by
    ``wade-hook`` / crossby, not here.

    A worktree is only ever one session kind at a time, but an implementation
    worktree is later **reused** for its review session (``review_service.start``
    re-bootstraps with ``SessionPhase.REVIEW``). crossby's hook writers dedup by
    exact command, so a prior ``--phase implement`` entry would survive alongside
    the new ``--phase review`` one and **both** would fire, injecting contradictory
    phase reminders. Every other-phase variant is therefore revoked via
    ``hooks_remove`` so exactly one SessionStart hook remains after re-bootstrap.
    """
    import shlex

    from crossby.ai_tools import AbstractAITool
    from crossby.models.config import HookEntry
    from crossby.sync.base import SyncData

    root = shlex.quote(str(worktree_path))
    for tool_id, writer in _hook_writers():
        if not AbstractAITool.get(tool_id).capabilities().supports_session_start_hook:
            continue
        command = _session_start_command(tool_id.value, root, phase.value)
        hook = HookEntry(event="session_start", tools=[], command=command)
        # Revoke this tool's SessionStart command for every *other* phase, so a
        # reused worktree (impl → review) ends up with exactly one entry rather
        # than a stale phase firing alongside the current one. sync() adds before
        # it removes, and no removal ever equals `command` (distinct --phase), so
        # the entry just installed is never clobbered.
        stale_phase_values = {other.value for other in SessionPhase if other != phase}
        # One compatibility window: canonical SessionKind values may already be
        # persisted by development builds, while released worktrees contain the
        # legacy implement/review values. Revoke both spellings explicitly.
        stale_phase_values.update(
            value
            for value in (
                SessionKind.PLAN.value,
                SessionKind.IMPLEMENTATION.value,
                SessionKind.REVIEW_PR_COMMENTS.value,
            )
            if value != phase.value
        )
        stale_removals = [
            ("session_start", _session_start_command(tool_id.value, root, value))
            for value in sorted(stale_phase_values)
        ]
        _log_sync_result(
            writer.sync(SyncData(hooks=[hook], hooks_remove=stale_removals), worktree_path),
            tool_id,
        )

    logger.info(
        "implementation.session_start_hook_installed", path=str(worktree_path), phase=phase.value
    )


# Canonical write-family tool names the PostToolUse lint feedback fires on. Scoped
# to file-write tools (not Bash: a shell call has no path to lint; not Delete: the
# file is gone, so linting it only injects "file not found" noise). crossby's hook
# writers translate these per tool. ``MultiEdit`` qualifies under that same rule —
# it leaves a real path behind — and, like the write guard above, needs naming
# because matchers are whole-name.
_LINT_FEEDBACK_TOOLS = ["Edit", "MultiEdit", "Write", "NotebookEdit"]


def _install_managed_git_hooks(worktree_path: Path, config: ProjectConfig) -> None:
    """Reconcile the pre-push backstop + opt-in pre-commit/commit-msg gates.

    Gathers the hooks whose config gates are on and hands the full desired set to
    :func:`reconcile_worktree_git_hooks`, which installs them in one batch
    (guaranteeing every prior user hook is captured before wade sets
    ``core.hooksPath``) **and** neutralizes any gate turned off since a prior
    bootstrap — so re-running ``wade implement`` on a reused worktree honors a
    disabled gate instead of leaving a stale one firing. All hooks are optional
    and the installer graceful-degrades on old git, so a failure is swallowed to a
    warning and never crashes bootstrap.
    """
    from wade.git.hooks import (
        build_commit_msg_hook_script,
        build_pre_commit_hook_script,
        reconcile_worktree_git_hooks,
    )
    from wade.utils.templates import load_hook_template

    hooks: dict[str, str] = {}

    if config.done.pre_push_backstop:
        try:
            hooks["pre-push"] = load_hook_template("pre-push")
        except FileNotFoundError:
            logger.warning("implementation.pre_push_template_missing")

    # build_*_hook_script both call load_hook_template, which raises
    # FileNotFoundError on a missing template — guard each the same way as the
    # pre-push branch so a packaging gap degrades to a warning instead of
    # crashing bootstrap for a project that opted into either gate.
    pre_commit = config.hooks.pre_commit
    if pre_commit.lint or pre_commit.test:
        try:
            hooks["pre-commit"] = build_pre_commit_hook_script(pre_commit.lint, pre_commit.test)
        except FileNotFoundError:
            logger.warning("implementation.pre_commit_template_missing")

    if config.hooks.commit_msg.conventional:
        try:
            hooks["commit-msg"] = build_commit_msg_hook_script()
        except FileNotFoundError:
            logger.warning("implementation.commit_msg_template_missing")

    try:
        reconcile_worktree_git_hooks(worktree_path, hooks)
    except Exception:
        logger.warning(
            "implementation.git_hooks_error",
            path=str(worktree_path),
            hooks=sorted(hooks),
            exc_info=True,
        )


def _install_post_tool_use_lint_hook(worktree_path: Path, config: ProjectConfig) -> None:
    """Reconcile PostToolUse in-turn lint feedback across each tool.

    Opt-in via ``hooks.post_tool_use.enabled`` with a resolvable lint command
    (``post_tool_use.lint_cmd`` file-scoped, or ``pre_commit.lint`` whole-repo).
    The installed hook command is **stable** — ``wade-hook post_tool_use --tool
    <id> --root <root>``, with the lint command/timeout/scope resolved from
    ``.wade.yml`` at runtime — so re-bootstrapping a reused worktree is idempotent
    (identical command → crossby dedups; no duplicate entry on reconfigure) and a
    hook left over from a now-disabled gate self-noops.

    Only **context-capable** tools (output dialect ≠ ``DECISION``) get the hook —
    Antigravity CLI has no verified context channel, so it is skipped. When the
    gate is off (or the tool can't inject context) any prior wade entry is
    **removed**; the stable command makes that removal deterministic regardless of
    the previously-configured lint command. Never fail-closed: this must not block.
    """
    import shlex

    from crossby.ai_tools import AbstractAITool
    from crossby.models.ai import HookOutputDialect
    from crossby.models.config import HookEntry
    from crossby.sync.base import SyncData

    ptu = config.hooks.post_tool_use
    enabled = ptu.enabled and bool(ptu.lint_cmd or config.hooks.pre_commit.lint)
    root = shlex.quote(str(worktree_path))

    for tool_id, writer in _hook_writers():
        command = f"wade-hook post_tool_use --tool {tool_id.value} --root {root}"
        # Never fail-closed: a malformed tool settings file or an OSError on write
        # must not abort bootstrap for this optional, off-by-default gate. Guard
        # per tool so one tool's failure doesn't skip the rest, mirroring the
        # warn-and-continue treatment in _install_managed_git_hooks.
        try:
            context_capable = (
                AbstractAITool.get(tool_id).capabilities().hook_output_dialect
                is not HookOutputDialect.DECISION
            )
            if enabled and context_capable:
                # No fail_closed: PostToolUse must never block. The timeout bounds
                # the per-edit cost at the tool's hook-runner level too. Note this
                # OUTER (tool-runner) bound is baked at bootstrap, unlike wade-hook's
                # INNER subprocess timeout, which re-resolves from .wade.yml every
                # run — so raising post_tool_use.timeout without re-bootstrapping
                # leaves the old outer bound in place until the next bootstrap.
                # Fails open either way, so this is a latent staleness, not a bug.
                data = SyncData(
                    hooks=[
                        HookEntry(
                            event="post_tool_use",
                            tools=_LINT_FEEDBACK_TOOLS,
                            command=command,
                            timeout=ptu.timeout,
                        )
                    ]
                )
            else:
                # Gate off, or the tool can't inject context — retract any prior
                # wade entry. Removing a non-existent entry is a no-op (no config
                # file is created), so this is safe to run every bootstrap.
                data = SyncData(hooks_remove=[("post_tool_use", command)])
            _log_sync_result(writer.sync(data, worktree_path), tool_id)
        except Exception:
            logger.warning(
                "implementation.post_tool_use_hook_error",
                tool=tool_id.value,
                path=str(worktree_path),
                exc_info=True,
            )

    if enabled:
        logger.info("implementation.post_tool_use_lint_installed", path=str(worktree_path))


def _effective_copy_files(config: ProjectConfig) -> list[str]:
    """Compute the full list of files to copy into a new worktree.

    Merges user-configured ``copy_to_worktree`` with internal wade files that must
    always be present (``.wade.yml``).

    The knowledge file and its ratings sidecar are **never** copied (#358): they are
    tracked, so the worktree checkout already has the committed version — copying
    main's (possibly dirty) copy over it is exactly what manufactured the stale
    snapshot this issue removes. Any lingering knowledge/ratings entries in a
    project's ``copy_to_worktree`` (pre-#358 config, before the migration strips
    them) are filtered out here so the copy can never resurrect the bug.
    """
    from wade.utils.knowledge_file import knowledge_copy_exclusions
    from wade.utils.paths import collapse_relative_path

    # Canonicalized (``.``/``..``-folded) set of knowledge/ratings paths that must never
    # be copied — the same single derivation the ``strip_knowledge_from_copy_to_worktree``
    # migration uses, so a redundant-``..`` spelling can't bypass one site and re-copy
    # main's knowledge file.
    excluded: set[str] = set()
    if config.knowledge.enabled:
        excluded = knowledge_copy_exclusions(config.knowledge.path)

    internal: list[str] = [".wade.yml"]
    files: list[str] = [
        f for f in config.hooks.copy_to_worktree if collapse_relative_path(f) not in excluded
    ]
    for f in internal:
        if f not in files:
            files.append(f)
    return files


def _multiset_difference(lines: list[str], subtract: list[str]) -> list[str]:
    """Return ``lines`` minus ``subtract`` as MULTISETS — order- and count-preserving.

    Each element of ``subtract`` cancels at most one equal element of ``lines``: a line
    appearing twice in ``lines`` and once in ``subtract`` yields one occurrence, not
    zero. The append-only ratings log can legitimately repeat a serialized line, so a
    plain set difference would silently drop a genuinely-new duplicate vote whose twin
    is already committed.
    """
    remaining = Counter(subtract)
    result: list[str] = []
    for line in lines:
        if remaining.get(line, 0) > 0:
            remaining[line] -= 1
        else:
            result.append(line)
    return result


def _is_durable_event_spool(lines: list[str]) -> bool:
    """Whether untracked JSONL lines are safe #462 staged-vote transport data."""
    try:
        for line in lines:
            record = json.loads(line)
            if not (
                isinstance(record, dict)
                and isinstance(record.get("event_id"), str)
                and bool(record["event_id"])
            ):
                return False
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _carry_forward_pending_votes(
    worktree_path: Path, repo_root: Path, config: ProjectConfig
) -> None:
    """Flush main's uncommitted ratings votes into the new worktree, then clean main.

    A throwaway (plan / ``task deps``) ``wade knowledge rate`` appends its vote line
    to **main's** working-copy ratings log — that is where a detached session's
    knowledge writes are redirected. Those lines are uncommitted, so they never reach
    origin on their own. At the next attached worktree's bootstrap we move them into
    the new worktree's log (so they ride into that branch's PR to origin) and restore
    main's ratings file to its committed state, returning main to clean.

    Votes are additive, so this is "late but lossless" — the vote lands in origin one
    attached session later. Serialized by ``file_lock`` on main's ratings file so two
    concurrent bootstraps can't double-carry: the first carries + clears, the rest see
    a clean main.
    """
    from wade.utils.filelock import file_lock
    from wade.utils.knowledge_file import resolve_knowledge_path, resolve_ratings_path

    try:
        main_ratings = resolve_ratings_path(resolve_knowledge_path(repo_root, config.knowledge))
    except ValueError:
        return
    if not main_ratings.is_file():
        return
    try:
        relpath = main_ratings.relative_to(repo_root).as_posix()
    except ValueError:
        return
    tracked_in_main = git_repo.is_file_tracked(repo_root, relpath)
    main_legacy = main_ratings.with_suffix(".yml")
    try:
        legacy_relpath = main_legacy.relative_to(repo_root).as_posix()
    except ValueError:
        legacy_relpath = None
    # Tri-state on purpose. ``show_file_at_head`` returns None both for "absent
    # at HEAD" and "git call failed", and the untracked branch below deletes
    # main's spool based on this answer: a transient git failure read as "no
    # legacy file" would skip the HEAD restore and leave main's staged legacy
    # deletion behind. ``None`` therefore means "unresolvable" and aborts the
    # carry instead of guessing.
    legacy_at_head: bool | None = (
        git_repo.path_exists_at_head(repo_root, legacy_relpath)
        if legacy_relpath is not None
        else False
    )
    legacy_tracked_in_main = legacy_at_head is True

    with file_lock(main_ratings):
        if not main_ratings.is_file():
            return
        # Snapshot main's exact bytes so the whole carry is recoverable: if any step of
        # the worktree transfer fails AFTER we reset main below, we restore these bytes
        # so the pending votes survive for a later bootstrap to retry — never lost.
        original_main_bytes = main_ratings.read_bytes()
        working_lines = [
            ln for ln in main_ratings.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        if tracked_in_main:
            committed_text = git_repo.show_file_at_head(repo_root, relpath)
            committed = committed_text.splitlines() if committed_text is not None else []
        else:
            # #462's parent handoff can create the ratings sidecar before any
            # attached session has ever committed one.  Treat it as a WADE spool
            # only when every record has a durable event ID; never move an
            # arbitrary untracked user file merely because it has this name.
            if not _is_durable_event_spool(working_lines):
                logger.warning("implementation.ratings_untracked_spool_rejected", path=relpath)
                return
            committed = []
        # Multiset (not set) difference: the append-only log may legitimately repeat a
        # serialized line, so subtracting the committed lines as a set would drop a
        # genuinely-new duplicate vote whose identical twin already lives in HEAD.
        pending = _multiset_difference(working_lines, committed)
        if not pending:
            return

        # Restore main's tracked ratings (or remove the verified untracked
        # #462 spool) FIRST, and only carry into the worktree if that succeeds.
        # Otherwise the same events remain pending and could be delivered twice.
        if tracked_in_main:
            restored = git_repo.checkout_paths(repo_root, relpath)
        elif legacy_at_head is None:
            # Cannot tell whether main owes a legacy-file restore. Leave the
            # spool intact; the next bootstrap retries once git answers.
            logger.warning("implementation.ratings_carry_legacy_state_unresolved", path=relpath)
            return
        else:
            try:
                if legacy_tracked_in_main:
                    assert legacy_relpath is not None
                    restored = git_repo.restore_paths_to_head(repo_root, legacy_relpath)
                else:
                    restored = True
                # Keep the pending spool intact unless every prerequisite for
                # resetting main has succeeded.  ``restore_paths_to_head``
                # deliberately returns False rather than raising when git
                # cannot restore the staged legacy migration.
                if restored:
                    main_ratings.unlink()
            except OSError:
                restored = False
        if not restored:
            logger.warning("implementation.ratings_carry_restore_failed", path=relpath)
            return

        # main is now reset to HEAD (pending removed). Any failure persisting the
        # worktree copy from here must roll main back to its snapshot so the votes are
        # not lost from BOTH locations. The transfer is only "successful" once the
        # worktree write completes.
        worktree_ratings: Path | None = None
        original_worktree_bytes: bytes | None = None
        try:
            worktree_ratings = resolve_ratings_path(
                resolve_knowledge_path(worktree_path, config.knowledge)
            )
            worktree_ratings.parent.mkdir(parents=True, exist_ok=True)
            # Snapshot the worktree side too. The append below completes before
            # the legacy removal, so a failure there must undo BOTH halves —
            # otherwise the same votes sit in main and in the branch, and the
            # next bootstrap carries main's copy across a second time.
            original_worktree_bytes = (
                worktree_ratings.read_bytes() if worktree_ratings.is_file() else None
            )
            content = (
                original_worktree_bytes.decode("utf-8")
                if original_worktree_bytes is not None
                else ""
            )
            # Append EVERY pending vote — do NOT dedupe against the worktree's committed
            # records. ``pending`` is already exactly main's uncommitted votes (working
            # minus main's HEAD), and a rating event can serialize IDENTICALLY to a line
            # the worktree already committed while still being a genuinely-distinct event.
            # Subtracting the worktree's copy here would drop that vote, yet the main
            # restore above already removed it — losing it from both places. Transferring
            # the same physical line twice is impossible regardless: main is reset to HEAD
            # (pending removed) BEFORE this append, and any failure below rolls BOTH sides
            # back, so each pending line rides across at most once.
            prefix = "" if (content == "" or content.endswith("\n")) else "\n"
            with worktree_ratings.open("a", encoding="utf-8") as fd:
                fd.write(prefix + "".join(f"{ln}\n" for ln in pending))
            if legacy_tracked_in_main:
                worktree_legacy = worktree_ratings.with_suffix(".yml")
                worktree_legacy_relpath = worktree_legacy.relative_to(worktree_path).as_posix()
                if not git_repo.rm_file(worktree_path, worktree_legacy_relpath):
                    raise OSError(
                        f"Could not stage legacy ratings removal: {worktree_legacy_relpath}"
                    )
        except OSError:
            # Undo the worktree append first, then restore main, so the votes
            # end up in exactly one place — main, where they started.
            if worktree_ratings is not None:
                with contextlib.suppress(OSError):
                    if original_worktree_bytes is not None:
                        worktree_ratings.write_bytes(original_worktree_bytes)
                    elif worktree_ratings.exists():
                        worktree_ratings.unlink()
            with contextlib.suppress(OSError):
                main_ratings.write_bytes(original_main_bytes)
            if legacy_tracked_in_main and legacy_relpath is not None:
                try:
                    if not git_repo.rm_file(repo_root, legacy_relpath):
                        logger.warning(
                            "implementation.ratings_carry_legacy_restage_failed",
                            path=legacy_relpath,
                        )
                except OSError:
                    logger.warning(
                        "implementation.ratings_carry_legacy_restage_failed",
                        path=legacy_relpath,
                    )
            logger.warning("implementation.ratings_carry_transfer_failed", path=relpath)
            return
        logger.debug("implementation.ratings_votes_carried_forward", count=len(pending))


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
    from wade.skills.installer import get_worktree_gitignore_entries

    entries = list(get_worktree_gitignore_entries())
    entries.extend(_conditional_worktree_gitignore_entries(worktree_path))

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
    session_phase: SessionPhase | None = None,
    session_kind: SessionKind | None = None,
    task_id: str | None = None,
    work_skills: list[str] | None = None,
    review_skills: list[str] | None = None,
    refresh_skills: bool = False,
    compose_session_only: bool = False,
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
        session_phase: The wade session kind (implement / review / plan). When set,
            a SessionStart context-injection hook is installed for that phase; when
            ``None`` (e.g. ``task deps`` sessions) no such hook is installed. This
            is an **independent** signal from ``plan_mode`` (which selects the
            write/stop guard) — the two are correlated (``plan_mode is True`` iff
            ``session_phase is SessionPhase.PLAN``), an invariant pinned by a test
            rather than derived in code.
        session_kind: Canonical session identity. During the compatibility
            window this is derived from ``session_phase`` when omitted.
        task_id: Stable provider task identity persisted in the session manifest.
        work_skills: Ordered CLI override for the session WORK slot.
        review_skills: Ordered CLI override for the session REVIEW slot.
        refresh_skills: Explicitly replace an existing frozen session bundle.
        compose_session_only: Resolve and materialize only the immutable session
            bundle. This preflight mode deliberately skips hooks, copied files, and
            tool configuration so callers can reject invalid skill bindings before
            any provider-side mutation.
    """
    effective_session_kind = session_kind
    if effective_session_kind is None and session_phase is not None:
        effective_session_kind = SESSION_PHASE_TO_KIND[session_phase]

    if compose_session_only:
        if effective_session_kind is None or effective_session_kind is SessionKind.DEPS:
            raise ValueError("compose_session_only requires an interactive session kind")
        from wade.services.session_composition_service import compose_session

        compose_session(
            worktree_path,
            repo_root,
            config,
            kind=effective_session_kind,
            task_id=task_id,
            work_skills=work_skills,
            review_skills=review_skills,
            refresh=refresh_skills,
        )
        return

    # Copy configured files + internal wade files that must always be present
    copy_files = _effective_copy_files(config)
    for filename in copy_files:
        src = repo_root / filename
        dest = worktree_path / filename
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logger.debug("implementation.bootstrap_copy", file=filename)

    # Knowledge lifecycle (#358): worktree-local, merged through the PR. Only for
    # attached (branch-backed) worktrees — a throwaway detached-HEAD plan/deps
    # worktree is discarded at session end, so its wade-managed .gitattributes union
    # block and any carried-forward ratings votes would be lost with it. The
    # attached/detached split is the same deterministic signal the knowledge layer
    # uses to decide where reads/writes land (_resolve_knowledge_root).
    if config.knowledge.enabled and git_repo.is_head_attached(worktree_path):
        from wade.skills.installer import ensure_knowledge_merge_attributes

        ensure_knowledge_merge_attributes(worktree_path, config)
        _carry_forward_pending_votes(worktree_path, repo_root, config)

    # Install skill files — not tracked by git so worktrees don't inherit them
    from wade.skills.installer import SKILL_FILES, get_wade_repo_root, install_skills

    is_self = repo_root.resolve() == get_wade_repo_root().resolve()

    # Phase skills are compatibility pointers only. Dynamic methodology is
    # physically snapshotted under `.wade/session`, so no workflow partial is
    # ever injected into a replaceable skill. In WADE's own developer worktrees,
    # retain tool-native links to packaged defaults for authoring convenience;
    # project discovery excludes their resolved template roots.
    if is_self:
        from wade.skills.catalog import BUILTIN_METHODOLOGY_SKILLS

        # Worktree has its own templates/ checkout — symlink to those
        wt_templates = worktree_path / "templates" / "skills"
        selected = list(dict.fromkeys([*(skills or SKILL_FILES), *BUILTIN_METHODOLOGY_SKILLS]))
        install_skills(
            worktree_path,
            is_self_init=True,
            force=True,
            templates_dir=wt_templates,
            skills=selected,
        )
    else:
        install_skills(
            worktree_path,
            is_self_init=False,
            force=True,
            skills=skills,
        )
    logger.debug("implementation.bootstrap_skills", path=str(worktree_path))

    # Inject AGENTS.md pointer into worktree (after skills, which may add AGENTS.md content)
    from wade.skills import pointer

    pointer.ensure_pointer(worktree_path)
    _suppress_pointer_artifacts(worktree_path)
    logger.debug("implementation.bootstrap_pointer", path=str(worktree_path))

    # Compose the immutable workflow + active/inventory skill snapshots. Deps
    # passes no interactive kind and intentionally has no session bundle.
    if effective_session_kind is not None and effective_session_kind is not SessionKind.DEPS:
        from wade.services.session_composition_service import compose_session

        composition = compose_session(
            worktree_path,
            repo_root,
            config,
            kind=effective_session_kind,
            task_id=task_id,
            work_skills=work_skills,
            review_skills=review_skills,
            refresh=refresh_skills,
        )
        if composition.resolution is not None:
            for warning in composition.resolution.warnings:
                logger.warning("session.skill_binding_shadowed", warning=warning)
        logger.info(
            "implementation.session_composed",
            session=effective_session_kind.value,
            reused=composition.reused,
        )

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

    # SessionStart context injection: re-inject a compact, phase-gated task
    # reminder on startup/resume/compaction (all sessions with a known phase;
    # `task deps` passes None and opts out). Independent of plan_mode — a plan
    # worktree installs both the plan write/stop guards AND this hook.
    if session_phase is not None:
        _install_session_start_hook(worktree_path, phase=session_phase)

    # Write worktree gitignore block AFTER all file generation so the entry
    # list is complete (skills, hooks, settings, pointer are all in place).
    write_worktree_gitignore(worktree_path)

    # Apply --skip-worktree on .gitignore if it is tracked so modifications
    # from the worktree block don't appear in git status.
    if git_repo.is_file_tracked(worktree_path, ".gitignore"):
        git_repo.skip_worktree_file(worktree_path, ".gitignore")
        logger.debug("implementation.skip_worktree", file=".gitignore")

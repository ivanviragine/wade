"""``wade-hook`` — the lean write-guard entry point invoked on every tool edit.

This is a **dedicated console script**, separate from the main ``wade`` Typer
CLI, for one reason: latency. A PreToolUse hook fires on *every* file edit, so
its cold-start cost is paid constantly. Going through ``wade`` (``wade.cli.main``)
eagerly imports the entire command graph (~all subcommand modules) and, via the
old dialect lookup, every crossby adapter — ~450ms per edit. This module imports
only ``crossby.hooks.runtime`` + ``crossby.models.ai`` (the light models module,
no adapters) + ``wade.hooks.policies``, cutting cold start to ~150ms.

To stay lean it deliberately avoids two heavy imports:

- ``wade.cli.main`` — replaced by hand-rolled ``argparse`` parsing here.
- ``crossby.ai_tools`` — the per-tool output dialect is resolved from a static
  ``tool -> HookOutputDialect`` map (:data:`_TOOL_DIALECTS`) instead of loading
  every adapter to read one capability flag.

Contract: stdout carries only the decision JSON (nothing else), and the process
exit code is the universal block signal (2 = deny/block, 0 = allow). The main
``wade hook`` command (:mod:`wade.cli.hook`) is kept as a thin, discoverable
alias that delegates here.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

from crossby.hooks.runtime import (
    SHELL_TOOL_NAMES,
    HookDecision,
    HookEmission,
    emit_decision,
    emit_stop_decision,
    parse_event,
)
from crossby.models.ai import HookOutputDialect, HookStopDialect

from wade.hooks.policies import (
    GUARD_NAMES,
    plan_artifact_only,
    plan_complete,
    session_complete,
    session_start_context,
    shell_containment,
    worktree_containment,
)
from wade.models.hooks import SessionPhase, StopGuard
from wade.utils import markers

# Stop guards fail OPEN — an unknown one must never trap the agent. Derived from
# :class:`StopGuard` (the shared source of truth) so this set cannot drift from
# what bootstrap installs. ``session-complete`` (impl/review) nudges to run
# ``done``; ``plan-complete`` (plan sessions) nudges to write a valid plan file.
_STOP_GUARDS = frozenset(g.value for g in StopGuard)

# Static ``tool id -> output dialect`` map, mirroring each crossby adapter's
# ``capabilities().hook_output_dialect``. Inlined so the hot per-edit path never
# imports ``crossby.ai_tools`` (which eagerly loads all adapters). Kept in sync
# with crossby; an unknown id falls back to the universal hookSpecificOutput
# shape (+ exit 2), which every tool honors via the exit code.
#
# MUST be re-verified on every crossby version bump — these are copies, so a
# dialect change upstream silently gives a tool the wrong output shape here.
# Copilot moved HOOK_SPECIFIC_OUTPUT-adjacent EXIT_CODE -> PERMISSION_DECISION
# in crossby 0.13 once its documented stdout schema was confirmed.
_TOOL_DIALECTS: dict[str, HookOutputDialect] = {
    "claude": HookOutputDialect.HOOK_SPECIFIC_OUTPUT,
    "codex": HookOutputDialect.HOOK_SPECIFIC_OUTPUT,
    "cursor": HookOutputDialect.PERMISSION,
    "copilot": HookOutputDialect.PERMISSION_DECISION,
    "antigravity-cli": HookOutputDialect.DECISION,
}

# Static ``tool id -> stop dialect`` map, mirroring ``capabilities().hook_stop_dialect``.
# crossby 0.13 split the Stop channel out of ``HookOutputDialect`` because a tool's
# turn-complete contract does not follow from its tool-call contract (Copilot reads a
# flat ``permissionDecision`` for PreToolUse but ``{"decision": "block"}`` for
# ``agentStop``). ``emit_stop_decision`` still accepts a legacy ``HookOutputDialect``,
# but resolving the stop dialect explicitly keeps the two channels independent — which
# is the point of the split — instead of riding a deprecated compatibility path.
# An unknown id falls back to BLOCK_DECISION, the shape three of the five tools use.
_TOOL_STOP_DIALECTS: dict[str, HookStopDialect] = {
    "claude": HookStopDialect.BLOCK_DECISION,
    "codex": HookStopDialect.BLOCK_DECISION,
    "cursor": HookStopDialect.FOLLOWUP_MESSAGE,
    "copilot": HookStopDialect.BLOCK_DECISION,
    "antigravity-cli": HookStopDialect.CONTINUE_DECISION,
}

# Tools whose memory-bypass policy is defined in :func:`_memory_allow_paths` below
# — the same 5 tools ``bootstrap._hook_writers`` installs a guard for, not all 8
# ``AIToolID`` values. Copilot/Antigravity-CLI keep memory in-repo, so they resolve
# to the intentional empty tuple (no bypass) — present here so "handled, no bypass"
# stays distinguishable from "forgotten". Static (no ``crossby.ai_tools`` import on
# the hot per-edit path), exactly like :data:`_TOOL_DIALECTS`; a coverage test keeps
# this key set in sync with ``_hook_writers``.
_TOOL_MEMORY_DIRS: frozenset[str] = frozenset(
    {"claude", "codex", "cursor", "copilot", "antigravity-cli"}
)

# Env var each tool honors to relocate its whole data home (mirroring the tool's
# own CLI resolution — undocumented in crossby, whose adapters hardcode
# ``Path.home()`` and never needed this). A tool absent here (Cursor) has no known
# relocation var and always resolves under ``Path.home()``. MUST be re-verified if
# either tool changes its override var.
_TOOL_CONFIG_HOME_ENV: dict[str, str] = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}

# PreToolUse write guards fail CLOSED (deny) on any error or misconfiguration.
_WRITE_GUARDS = frozenset({"worktree", "plan"})

# Fallback timeout (seconds) for the PostToolUse linter when ``--timeout`` is
# omitted. Bootstrap always bakes an explicit value; this only guards a
# hand-invoked hook.
_POST_TOOL_USE_TIMEOUT = 10


def _is_post_tool_use(event: str) -> bool:
    """True when ``event`` names the PostToolUse hook, in any casing/spelling."""
    return event.strip().lower().replace("_", "") == "posttooluse"


def _is_session_start(event: str) -> bool:
    """True when ``event`` names the SessionStart hook, in any casing/spelling."""
    return event.strip().lower().replace("_", "") == "sessionstart"


def _dialect_for(tool: str) -> HookOutputDialect:
    return _TOOL_DIALECTS.get(tool.strip().lower(), HookOutputDialect.HOOK_SPECIFIC_OUTPUT)


def _stop_dialect_for(tool: str) -> HookStopDialect:
    return _TOOL_STOP_DIALECTS.get(tool.strip().lower(), HookStopDialect.BLOCK_DECISION)


def _tool_config_home(tool: str, home: Path) -> Path:
    """The directory ``tool`` actually persists data under, honoring its relocation env var.

    Falls back to ``home / ".<tool>"`` when ``tool`` has no entry in
    :data:`_TOOL_CONFIG_HOME_ENV`, or the var is unset/blank — e.g. the repo's own
    live-test harness sets ``CLAUDE_CONFIG_DIR`` to an isolated temp dir
    (``scripts/test-live-ai-taskr.sh``), so Claude's real memory location moves
    with it; without this, the allowlist would keep denying those writes.
    """
    env_var = _TOOL_CONFIG_HOME_ENV.get(tool)
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            return Path(override)
    return home / f".{tool}"


def _encode_claude_project_path(path: Path) -> str:
    """Mirror ``crossby.ai_tools.claude._encode_claude_path`` (``/`` and ``.`` -> ``-``).

    Duplicated, not imported: importing ``crossby.ai_tools.claude`` would import
    its parent package ``crossby.ai_tools`` first, which eagerly loads every
    adapter (see module docstring) — exactly what this hot per-edit path avoids.
    MUST be re-verified on every crossby version bump.
    """
    return str(path).replace("/", "-").replace(".", "-")


def _encode_cursor_project_path(path: Path) -> str:
    """Mirror crossby.ai_tools.cursor's working-dir encoding: strip a leading ``/``, ``/`` -> ``-``.

    Duplicated for the same lean-import reason as :func:`_encode_claude_project_path`.
    """
    return str(path).lstrip("/").replace("/", "-")


def _memory_allow_paths(tool: str, worktree_root: Path) -> tuple[Path, ...]:
    """Absolute, resolved memory root ``tool`` may write to despite containment.

    Scoped as tightly as each tool's own storage layout allows — narrower is not
    always possible, and the three tools differ in how narrow they get:

    - **Claude** nests memory one level below its per-project session dir —
      ``<config-home>/projects/<encoded-worktree>/memory/``. Sibling session
      transcripts live un-nested at ``<encoded-worktree>/``, so allowlisting that
      parent (as this used to) would over-grant writes to every transcript, not
      just memory. This is the only one of the three scoped to **both** this
      session **and** memory alone.
    - **Cursor**'s per-project dir *is* the memory location —
      ``<config-home>/projects/<encoded-worktree>/`` — but crossby's own reader
      (``crossby.handoff.readers.cursor.locate_sessions``) globs that *same*
      directory for session-transcript JSON. So this bypass is scoped to *this
      session's project* (an improvement over allowlisting every Cursor project
      on the machine, which the pre-narrowing code did) but not to memory
      alone: a guarded Cursor session can also rewrite or delete its own
      transcript files, not just append memory.
    - **Codex**'s rollouts are filed by date, not by project —
      ``<config-home>/sessions/YYYY/MM/DD/rollout-*.jsonl``, shared flat across
      *every* project on the machine (crossby filters by a ``cwd`` field inside
      each file, not by directory) — so unlike Claude/Cursor, this bypass is
      **not scoped to this session at all**: the whole ``sessions/`` tree, every
      other project's rollouts included, is writable. Accepted because Codex's
      storage offers no narrower boundary to key on; the alternative would be
      dropping Codex from :data:`_TOOL_MEMORY_DIRS` entirely (like Copilot /
      Antigravity-CLI) rather than approximating a per-session guarantee it
      cannot actually provide.

    ``<config-home>`` is :func:`_tool_config_home` (honors a relocation env var
    before falling back to ``Path.home() / ".<tool>"``). ``<encoded-worktree>`` is
    ``worktree_root`` — **canonicalized first** (``.resolve()``), so a
    ``worktrees_dir`` configured through a symlink still encodes the same
    physical path the launched tool observes as its CWD, not the symlink
    spelling — encoded the way the tool itself encodes its CWD into a
    project-dir name (:func:`_encode_claude_project_path` /
    :func:`_encode_cursor_project_path`) — for Claude and Cursor, scoping the
    allow-root to *this* session's project also means an ancestor-directory
    symlink (e.g. a hypothetical ``projects -> ..``) cannot widen the exception
    the way it could when the allow-root was the shared ``projects`` parent: the
    resolved root is still a session-specific leaf ending in ``/memory`` (Claude)
    or the encoded project name (Cursor), never an ancestor like the config home
    itself. Codex's allow-root is already tool-wide, so this property does not
    apply to it.

    The final path component (the ``allow_paths`` leaf itself — ``memory/`` for
    Claude, the encoded project dir for Cursor, ``sessions/`` for Codex) is
    **never** resolved through a symlink: only its parent is canonicalized, then
    the leaf name is reattached literally. Resolving the leaf too would let it
    silently redirect the exception to whatever it points at — e.g. a
    compromised session that replaces its own ``memory`` dir with a symlink to
    ``~/.claude`` would otherwise widen this allowlist to the tool's entire
    config home, including the very hook settings that enforce this guard. A
    write that lands on such a symlinked leaf still resolves (via
    ``_resolve_path`` in the write guards) to the real, symlinked-through target,
    which no longer falls under this literal, unresolved leaf path — so it is
    denied like any other out-of-bounds write.

    Threaded into the three write guards as ``allow_paths``.

    Degrades **safely, never raising** — this runs on the hot PreToolUse path:

    - an unrecognized tool, or one with an intentional empty policy (Copilot /
      Antigravity-CLI keep memory in-repo) → ``()`` (no bypass);
    - ``Path.home()`` unresolvable (e.g. HOME unset) → ``()`` (no bypass);
    - a path that will not resolve → ``()`` (no bypass).

    Kept lean like :func:`_dialect_for`: plain path joins, no ``crossby.ai_tools``
    import and no config load.
    """
    normalized = tool.strip().lower()
    if normalized not in _TOOL_MEMORY_DIRS:
        return ()
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return ()
    try:
        canonical_worktree = worktree_root.resolve(strict=False)
        if normalized == "claude":
            path = (
                _tool_config_home(normalized, home)
                / "projects"
                / _encode_claude_project_path(canonical_worktree)
                / "memory"
            )
        elif normalized == "cursor":
            path = (
                _tool_config_home(normalized, home)
                / "projects"
                / _encode_cursor_project_path(canonical_worktree)
            )
        elif normalized == "codex":
            path = _tool_config_home(normalized, home) / "sessions"
        else:
            return ()  # Copilot / Antigravity-CLI — intentional no bypass.
        # Resolve everything up to the leaf's parent (canonicalizing any
        # ancestor symlinks), then reattach the leaf name literally — never
        # follow a symlink *at* the leaf itself (see docstring).
        return (path.parent.resolve(strict=False) / path.name,)
    except (OSError, ValueError, RuntimeError):
        return ()


# Every value-taking flag must be listed so _event_from_argv skips the flag's
# VALUE when recovering the event positional from a rejected argv — otherwise a
# value like `--lint-cmd stop` is mistaken for a Stop event and flips a
# PreToolUse usage error from fail-closed to fail-open. `--unscoped` is a
# store_true (no value), so it is deliberately absent.
_VALUE_FLAGS = ("--guard", "--tool", "--root", "--lint-cmd", "--timeout", "--phase")


def _event_from_argv(argv: list[str]) -> str:
    """Recover the ``event`` positional from a raw argv that argparse rejected.

    Must key off the positional specifically, not scan the whole argv: a flag
    *value* of ``stop`` (e.g. ``--root stop``) would otherwise look like a Stop
    event and flip a PreToolUse usage error from fail-closed to fail-open.
    """
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            if arg in _VALUE_FLAGS:
                skip = True
            continue
        return arg.strip().lower()
    return ""


def _is_shell_call(ev: object) -> bool:
    """True when the payload is a shell invocation, not a path-addressed write.

    Requires a ``command`` *and* a shell-family (or absent) tool name. Cursor's
    ``beforeShellExecution`` sends a command with no ``tool_name`` at all, so an
    absent name counts; a *write* tool that happens to carry a command does not,
    and still gets the file-path guard.
    """
    if not getattr(ev, "command", None):
        return False
    tool_name = getattr(ev, "tool_name", None)
    return tool_name is None or tool_name in SHELL_TOOL_NAMES


def _is_stop_event(event: str, ev: object) -> bool:
    """True when this invocation is a session-Stop, by CLI arg or parsed payload.

    Checked independently of the guard name so an unrecognized ``--guard`` on a
    Stop event still takes the fail-*open* path instead of the fail-closed write
    path — a guard typo must never leave an agent unable to end its turn.
    """
    if event.strip().lower() == "stop":
        return True
    # Reads as if a hostile payload could claim `"event": "stop"` and talk a
    # PreToolUse write out of its fail-closed path. It cannot: `_run` calls
    # `parse_event(raw, event=event)`, and crossby's `_extract_event` gives that
    # override unconditional precedence — the payload's `hook_event_name` is
    # consulted only when the override is falsy. So `ev.event` is CLI-derived,
    # and this clause can differ from the one above only when the event arg is
    # empty, which no command wade installs ever is. Kept as the fail-open
    # safety net for exactly that malformed case.
    return getattr(ev, "event", None) == "stop"


def _mark_stop_nudged(worktree_root: Path) -> None:
    """Write the single-shot Stop marker; best-effort and race-safe against symlinks.

    Delegates to :func:`wade.utils.markers.write_flag_marker`, which creates the
    marker relative to an ``O_DIRECTORY | O_NOFOLLOW`` handle on ``.wade`` so a
    repo-controlled symlink swapped in for ``.wade`` can neither redirect the
    write outside the worktree nor slip through a TOCTOU window. A failed or
    unsupported write is harmless — we simply nudge again next time.
    """
    markers.write_flag_marker(worktree_root, "stop-nudged")


def _git_out(root: Path, *args: str) -> str | None:
    """Run a read-only ``git`` command in ``root``; return stripped stdout or None.

    Deliberately a raw ``subprocess`` call rather than the ``wade.git`` layer: it
    runs on the Stop path and must stay cheap and dependency-light. Any failure
    (non-zero exit, missing git, timeout) returns ``None`` so the caller can fail
    open.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _resolve_stop_base_ref(root: Path) -> str | None:
    """Resolve the base ref the session branch is measured against, or None.

    Honors a stacked ``.wade/base_branch`` when present; otherwise detects
    ``main``/``master`` (preferring the ``origin/`` remote-tracking ref). Returns
    ``None`` when nothing resolves so the caller fails open.
    """
    base_name: str | None = None
    base_file = root / ".wade" / "base_branch"
    try:
        if base_file.is_file():
            content = base_file.read_text(encoding="utf-8").strip()
            if content:
                base_name = content
    except OSError:
        base_name = None

    if base_name:
        candidates = [f"origin/{base_name}", base_name]
    else:
        candidates = ["origin/main", "origin/master", "main", "master"]

    for ref in candidates:
        if ref and _git_out(root, "rev-parse", "--verify", "--quiet", ref) is not None:
            return ref
    return None


def _stop_git_facts(root: Path) -> tuple[int, bool]:
    """Compute ``(commits_ahead_of_base, done_marker_present)`` for the Stop guard.

    All git calls go through :func:`_git_out` (raw ``subprocess``), NOT the
    ``wade.git`` layer: this runs in the lean entry point, which deliberately
    leaves ``structlog`` unconfigured, so a ``wade.git`` call's ``git.run`` debug
    line would print to stdout and corrupt the decision-JSON contract.

    ⚠ The ahead-count is ``git rev-list --count <base>..HEAD`` — commits on the
    session branch (HEAD) not on base — equivalent to
    ``commits_ahead(root, branch, base)`` with the **session branch in the branch
    position**. That is the *opposite* role assignment from the sync gate's
    behind-count (:func:`wade.services.implementation_service.done._behind_count`),
    which passes ``origin/<main>`` in the branch position. The ``done`` marker is
    checked against the current HEAD sha.

    Raises on any failure (detached HEAD, unresolvable base, git error) so the
    Stop branch can fail open — a Stop guard must never trap the agent.
    """
    head = _git_out(root, "rev-parse", "HEAD")
    if head is None:
        raise RuntimeError("cannot resolve HEAD")
    if _git_out(root, "symbolic-ref", "-q", "HEAD") is None:
        raise RuntimeError("detached HEAD")  # no session branch to finalize
    base_ref = _resolve_stop_base_ref(root)
    if base_ref is None:
        raise RuntimeError("cannot resolve base ref")
    count = _git_out(root, "rev-list", "--count", f"{base_ref}..HEAD")
    if count is None:
        raise RuntimeError("cannot count commits")
    done_present = markers.marker_present(root, "done", head)
    return int(count), done_present


def _run(event: str, guard: str, tool: str, root: str) -> HookEmission:
    """Apply a guard to the stdin hook payload and return what to emit.

    Two guard families with opposite failure semantics:

    - Write guards (``worktree`` / ``plan``, PreToolUse) fail **closed**: any
      exception, an unknown guard name, a missing ``--root``, or an *unparseable*
      payload denies the write. A guard that silently allows on error is worse
      than useless.
    - The Stop guards (``session-complete`` / ``plan-complete``) fail **open**: a
      bug, an internal error, a missing ``--root``, *or an unrecognized guard
      name* must never trap the agent in a session it cannot exit — they emit a
      non-blocking decision rather than inspecting the CWD (which could spuriously
      block completion).

    Payload handling is asymmetric on purpose. An *empty* payload describes no
    write target, so there is nothing that could escape the worktree — allowing
    it is safe. A *non-empty but malformed* payload may well contain a target we
    simply failed to parse, so a write guard must deny it rather than let an
    unverifiable write through.

    Two channels carry a write. A tool-call write names its target in
    ``file_path``; a *shell* write hides it inside ``command`` (crossby reports
    ``is_write=False`` for shell tool names by design, so the file-path policies
    would wave it through). A payload with a ``command`` is therefore routed to
    :func:`shell_containment`, and one carrying both channels is checked twice.
    """
    dialect = _dialect_for(tool)

    read_error: Exception | None = None
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError) as e:
        raw = ""
        read_error = e  # re-raised inside the write-guard block to fail closed

    ev = parse_event(raw, event=event)

    # The Stop channel is claimed by either a known Stop guard or the event, so an
    # unknown ``--guard`` on a Stop event cannot fall through to the write-guard
    # path below and block session completion (it fails open here instead).
    if guard in _STOP_GUARDS or _is_stop_event(event, ev):
        stop_dialect = _stop_dialect_for(tool)
        if guard not in _STOP_GUARDS:
            # Unrecognized guard on a Stop event — emit a non-blocking decision
            # without evaluating any policy. A Stop guard must never trap the agent.
            return emit_stop_decision(False, "", stop_dialect)
        if not root or read_error is not None:
            # No worktree root to inspect, or stdin was unreadable — fail open
            # rather than block completion. Falling back to the CWD (missing
            # root) or evaluating an empty event (read error) could spuriously
            # trap the agent, which a Stop guard must never do.
            return emit_stop_decision(False, "", stop_dialect)
        try:
            if guard == StopGuard.PLAN_COMPLETE:
                # Plan-session Stop: nudge unless the plan dir holds a valid plan.
                # ``has_valid_plan`` pulls in pydantic + models, so it is imported
                # lazily HERE — never on the hot PreToolUse write path, only on
                # this Stop branch. Any exception falls through to fail-open below.
                # The plan dir is ``<worktree>/.wade/plans`` because ``plan()`` sets
                # ``plan_output_dir = planning_worktree / ".wade" / "plans"`` and the
                # hook is installed with ``--root <planning_worktree>``.
                from wade.utils.plan_validation import has_valid_plan

                decision = plan_complete(
                    ev,
                    worktree_root=Path(root),
                    has_valid_plan=has_valid_plan(Path(root) / ".wade" / "plans"),
                )
            else:
                # Git facts feed the predicate: nudge only when the branch has
                # authored work (commits ahead of base) and no current done marker.
                # Computed here (lazy subprocess) to keep the predicate pure; any
                # failure raises and falls through to the fail-open handler below.
                commits_ahead, done_present = _stop_git_facts(Path(root))
                decision = session_complete(
                    ev,
                    worktree_root=Path(root),
                    commits_ahead=commits_ahead,
                    done_marker_present=done_present,
                )
            should_block = decision.action == "deny"
            reason = decision.reason
            if should_block:
                # Record that we nudged so the *next* Stop is allowed on any tool
                # — a tool-agnostic single-shot that doesn't rely on the
                # Claude-only stop_hook_active field. Best-effort: if the marker
                # can't be written we simply nudge again (still fail-open-safe).
                _mark_stop_nudged(Path(root))
        except Exception:
            should_block, reason = False, ""  # fail-open: never trap the agent
        return emit_stop_decision(should_block, reason, stop_dialect)

    # PreToolUse write guards from here down — fail closed.
    try:
        if guard not in _WRITE_GUARDS:
            # An unrecognized guard is a misconfiguration: the hook is installed but
            # enforces nothing. Decided *before* any payload or --root branching so
            # empty stdin cannot route past it into the allow branch below — which is
            # exactly how `--guard <typo>` used to exit 0 and silently protect nothing.
            # (A Stop event never reaches here; it fails open above.)
            known = ", ".join(GUARD_NAMES)
            decision = HookDecision.deny(
                f"wade hook: unknown guard '{guard}' (expected one of: {known}); "
                "denying to fail closed."
            )
        elif read_error is not None:
            # Couldn't read the payload — can't verify the write is contained.
            raise read_error
        elif not root:
            # Without a worktree root we cannot make a containment decision.
            decision = HookDecision.deny(
                f"wade hook: '{guard}' guard requires --root; denying to fail closed."
            )
        elif not raw.strip():
            # Empty payload describes no write target — nothing can escape the
            # worktree, so allow (denying would needlessly trap the agent).
            decision = HookDecision.allow()
        else:
            # Non-empty payload that won't parse may hide a write target; validate
            # up front so a write guard denies rather than defaulting to a no-op
            # event. The policy itself denies a parsed-but-pathless write.
            json.loads(raw)
            worktree_root = Path(root)
            plan_mode = guard == "plan"
            # The active tool's own memory subtree — writable despite containment
            # (see _memory_allow_paths). Computed once; plain path joins, no
            # crossby.ai_tools import and no config load, so the hot path stays lean.
            allow = _memory_allow_paths(tool, worktree_root)
            decision = HookDecision.allow()
            if ev.command:
                # Shell channel: crossby reports is_write=False for shell tool
                # names, so the file-path policies below would allow this outright.
                decision = shell_containment(
                    ev, worktree_root=worktree_root, plan_mode=plan_mode, allow_paths=allow
                )
            if decision.action != "deny" and not _is_shell_call(ev):
                # File-path channel — the only channel for a tool-call write, and a
                # second check when a payload happens to carry both. Skipped *only*
                # for a genuine shell call: gating this on "has a command" instead
                # would let a write tool that carries a command and no file_path
                # through, losing the "deny a write we cannot locate" invariant.
                decision = (
                    plan_artifact_only(ev, worktree_root=worktree_root, allow_paths=allow)
                    if plan_mode
                    else worktree_containment(ev, worktree_root=worktree_root, allow_paths=allow)
                )
    except Exception as e:
        decision = HookDecision.deny(f"wade hook guard error: {type(e).__name__}: {e}")

    return emit_decision(decision, dialect, event=ev.event or event)


def _resolve_edited_path(ev: object, root: str) -> str | None:
    """Resolve the just-edited file path, confined to the worktree, or None.

    Returns the absolute path only when the event names a path-addressed write
    (:attr:`HookEvent.is_write`) whose target resolves **inside** ``root``. A
    missing path, a non-write tool call (read/shell), a missing root, or a path
    that escapes the worktree all yield None — the linter must never run on a
    file it cannot attribute to this in-worktree edit.
    """
    if not root:
        return None
    if not getattr(ev, "is_write", False):
        return None
    file_path = getattr(ev, "file_path", None)
    if not file_path:
        return None
    root_path = Path(root).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved != root_path and root_path not in resolved.parents:
        return None
    return str(resolved)


def _resolve_post_tool_use_config(root: str) -> tuple[str, int, bool] | None:
    """Resolve ``(lint_cmd, timeout, scoped)`` from the worktree's ``.wade.yml``.

    Returns None when PostToolUse is disabled or no lint command resolves, so a
    stale hook installed by a prior session self-noops once the gate is turned
    off. ``lint_cmd`` is ``post_tool_use.lint_cmd`` (file-scoped) or, when unset,
    ``pre_commit.lint`` (whole-repo). Imported lazily so the config loader is only
    pulled in on this PostToolUse branch — never on the hot PreToolUse write path.
    """
    if not root:
        return None
    try:
        from wade.config.loader import load_config

        config = load_config(Path(root))
    except Exception:
        return None
    ptu = config.hooks.post_tool_use
    if not ptu.enabled:
        return None
    lint_cmd = ptu.lint_cmd or config.hooks.pre_commit.lint
    if not lint_cmd:
        return None
    return lint_cmd, ptu.timeout, bool(ptu.lint_cmd)


def _run_post_tool_use(
    tool: str, root: str, lint_cmd: str | None, timeout: int | None, *, unscoped: bool
) -> HookEmission:
    """Run the file-scoped PostToolUse linter — strictly fire-and-forget / fail-open.

    Always exits 0 and never denies. On a non-zero lint exit it returns the lint
    output as :meth:`HookDecision.context` so the agent can fix the file while the
    edit is still in working memory; every other outcome — a context-incapable
    tool, disabled/no lint command, unreadable stdin, a non-write / out-of-worktree
    edit, a timeout, empty output, or any exception — is a silent no-op.

    ``lint_cmd`` may be passed explicitly (an override, used by tests); when
    omitted it is resolved from the worktree's ``.wade.yml`` at ``root``, so the
    installed hook command is **stable** (``--tool``/``--root`` only). That keeps
    re-bootstrap idempotent (identical command → crossby dedups) and makes a hook
    left over from a now-disabled gate self-noop.

    The argv is built **safely**: ``shlex.split(lint_cmd)`` (config-authored,
    trusted) plus the resolved edited path (tool-emitted, untrusted) as a list,
    run with ``shell=False``. The path is never string-interpolated into a shell
    command, so a hostile path cannot inject.
    """
    import shlex
    import subprocess

    dialect = _dialect_for(tool)

    def _noop() -> HookEmission:
        return emit_decision(HookDecision.allow(), dialect, event="post_tool_use")

    # agy (DECISION dialect) has no context channel; bootstrap already skips it,
    # but double-guard so a stray install can't fire a per-edit subprocess for it.
    if dialect is HookOutputDialect.DECISION:
        return _noop()

    if lint_cmd is None:
        # No explicit override — resolve from config (also self-disables a stale
        # hook whose gate was turned off).
        resolved = _resolve_post_tool_use_config(root)
        if resolved is None:
            return _noop()
        lint_cmd, timeout, scoped = resolved
    else:
        scoped = not unscoped
        if timeout is None:
            timeout = _POST_TOOL_USE_TIMEOUT
    if not lint_cmd:
        return _noop()

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return _noop()
    ev = parse_event(raw, event="post_tool_use")

    try:
        # ValueError on an unbalanced-quote lint_cmd: config validation only
        # checks the command is a non-empty string, so a malformed value would
        # otherwise crash the hook with a non-zero exit — violating fail-open.
        argv = shlex.split(lint_cmd)
    except ValueError:
        return _noop()
    if not argv:
        return _noop()
    if scoped:
        edited_path = _resolve_edited_path(ev, root)
        if edited_path is None:
            return _noop()
        argv = [*argv, edited_path]

    try:
        proc = subprocess.run(
            argv,
            cwd=root or None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # TimeoutExpired is a SubprocessError — skip on overrun, never hang/block.
        return _noop()

    if proc.returncode == 0:
        return _noop()
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not output:
        return _noop()
    return emit_decision(HookDecision.context(output), dialect, event="post_tool_use")


def _run_session_start(tool: str, root: str, phase: str) -> HookEmission:
    """Emit the SessionStart context payload — strictly non-blocking / fail-open.

    Re-injects a compact, phase-gated task reminder on every SessionStart source
    (startup / resume / compact / clear / fork — the installed hook uses a ``.*``
    matcher). Always exit 0: ``context`` and ``allow`` are both non-blocking, so
    this channel can never trap a session from starting. Any problem — a
    context-incapable tool (agy), a missing ``--root`` / ``--phase``, an
    unrecognized phase, an unreadable ``PLAN.md``, or any exception — degrades to a
    no-op ``allow``.

    All dialect/shape logic is crossby's: ``emit_decision`` serializes
    :meth:`HookDecision.context` as nested ``hookSpecificOutput.additionalContext``
    (Claude/Codex), flat ``additionalContext`` (Copilot), or ``additional_context``
    (Cursor, gated to the events it reads it on). wade owns only the *policy* (the
    text, by phase) and the *install*.
    """
    dialect = _dialect_for(tool)

    def _noop() -> HookEmission:
        return emit_decision(HookDecision.allow(), dialect, event="session_start")

    # agy (DECISION dialect) has no verified context channel; bootstrap already
    # skips it via supports_session_start_hook, but double-guard so a stray install
    # can never emit anything.
    if dialect is HookOutputDialect.DECISION:
        return _noop()

    # Read + discard stdin best-effort (consistency with the other branches; the
    # payload is built from --root/--phase, not stdin).
    with contextlib.suppress(OSError, ValueError):
        sys.stdin.read()

    if not root or not phase:
        return _noop()  # nothing to inject / no phase — never block startup

    try:
        payload = session_start_context(Path(root), SessionPhase(phase))
    except Exception:
        # Fail open on ANY error (unknown phase, unreadable payload, …): a
        # SessionStart hook must never block a session from starting.
        return _noop()

    if not payload:
        return _noop()
    return emit_decision(HookDecision.context(payload), dialect, event="session_start")


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the guard, emit the decision, return the exit code."""
    parser = argparse.ArgumentParser(
        prog="wade-hook",
        description="Apply a wade write-guard to an AI tool's hook payload (stdin).",
        add_help=True,
    )
    parser.add_argument(
        "event",
        help="Canonical hook event: pre_tool_use, post_tool_use, stop, or session_start.",
    )
    parser.add_argument(
        "--guard",
        default="",
        help=(
            "Guard policy: worktree | plan | session-complete | plan-complete "
            "(| context for session_start context injection)."
        ),
    )
    parser.add_argument("--tool", required=True, help="AI tool id (selects the output dialect).")
    parser.add_argument("--root", default="", help="Worktree root — required by write guards.")
    parser.add_argument(
        "--phase",
        default="",
        help="Session phase for session_start context: plan | implement | review.",
    )
    # PostToolUse lint feedback (fail-open, never blocks). The installed hook
    # passes neither --lint-cmd nor --timeout (a stable, config-driven command);
    # these remain as explicit overrides (used by tests / manual invocation).
    parser.add_argument(
        "--lint-cmd",
        default=None,
        help="PostToolUse override: lint command; the edited path is appended unless --unscoped.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="PostToolUse override: seconds before the linter is abandoned (skip on overrun).",
    )
    parser.add_argument(
        "--unscoped",
        action="store_true",
        help="PostToolUse override: run the lint command whole-repo (do not append the path).",
    )

    raw_argv = sys.argv[1:] if argv is None else argv
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        # argparse exits 2 on a usage error. For a PreToolUse write guard that
        # would (via the non-zero code) block the edit, which is the safe
        # direction, so let it stand. On a *Stop*, *PostToolUse*, or *SessionStart*
        # event it is the opposite: those channels are fail-open (PostToolUse must
        # never block — the tool has already run; SessionStart must never block a
        # session from starting), so a malformed invocation there returns 0.
        event_arg = _event_from_argv(raw_argv)
        if event_arg == "stop" or _is_post_tool_use(event_arg) or _is_session_start(event_arg):
            return 0
        raise

    if _is_post_tool_use(ns.event):
        emission = _run_post_tool_use(
            ns.tool, ns.root, ns.lint_cmd, ns.timeout, unscoped=ns.unscoped
        )
    elif _is_session_start(ns.event):
        emission = _run_session_start(ns.tool, ns.root, ns.phase)
    else:
        emission = _run(ns.event, ns.guard, ns.tool, ns.root)
    if emission.stdout:
        sys.stdout.write(emission.stdout)
    if emission.stderr:
        sys.stderr.write(emission.stderr + "\n")
    return emission.exit_code


def cli_main() -> None:
    """Console-script entry point (``wade-hook``)."""
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    cli_main()

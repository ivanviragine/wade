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
    session_complete,
    shell_containment,
    stop_nudge_marker_path,
    worktree_containment,
)

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

# PreToolUse write guards fail CLOSED (deny) on any error or misconfiguration.
_WRITE_GUARDS = frozenset({"worktree", "plan"})


def _dialect_for(tool: str) -> HookOutputDialect:
    return _TOOL_DIALECTS.get(tool.strip().lower(), HookOutputDialect.HOOK_SPECIFIC_OUTPUT)


def _stop_dialect_for(tool: str) -> HookStopDialect:
    return _TOOL_STOP_DIALECTS.get(tool.strip().lower(), HookStopDialect.BLOCK_DECISION)


_VALUE_FLAGS = ("--guard", "--tool", "--root")


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
    return getattr(ev, "event", None) == "stop"


def _mark_stop_nudged(worktree_root: Path) -> None:
    """Write the single-shot Stop marker; best-effort and race-safe against symlinks.

    Opens ``.wade`` itself with ``O_DIRECTORY | O_NOFOLLOW`` and creates the
    marker *relative to that directory handle*, so a repo-controlled symlink
    swapped in for ``.wade`` can neither redirect the write outside the worktree
    nor slip through a TOCTOU window between checking and creating (the parent
    handle is the trusted anchor). A failed or unsupported write is harmless — we
    simply nudge again next time.
    """
    if not (hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd):
        return  # no atomic no-follow path available; skip rather than risk a follow
    marker = stop_nudge_marker_path(worktree_root)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    dir_fd = None
    try:
        dir_fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(
            marker.name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        os.close(fd)
    except OSError:
        pass  # a failed write just means we nudge again next time — still safe
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


def _run(event: str, guard: str, tool: str, root: str) -> HookEmission:
    """Apply a guard to the stdin hook payload and return what to emit.

    Two guard families with opposite failure semantics:

    - Write guards (``worktree`` / ``plan``, PreToolUse) fail **closed**: any
      exception, an unknown guard name, a missing ``--root``, or an *unparseable*
      payload denies the write. A guard that silently allows on error is worse
      than useless.
    - The Stop guard (``session-complete``) fails **open**: a bug, an internal
      error, a missing ``--root``, *or an unrecognized guard name* must never trap
      the agent in a session it cannot exit — it emits a non-blocking decision
      rather than inspecting the CWD (which could spuriously block completion).

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

    # The Stop channel is claimed by either the guard name or the event, so an
    # unknown ``--guard`` on a Stop event cannot fall through to the write-guard
    # path below and block session completion (it fails open here instead).
    if guard == "session-complete" or _is_stop_event(event, ev):
        stop_dialect = _stop_dialect_for(tool)
        if guard != "session-complete":
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
            decision = session_complete(ev, worktree_root=Path(root))
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
            known = ", ".join(g for g in GUARD_NAMES)
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
            decision = HookDecision.allow()
            if ev.command:
                # Shell channel: crossby reports is_write=False for shell tool
                # names, so the file-path policies below would allow this outright.
                decision = shell_containment(ev, worktree_root=worktree_root, plan_mode=plan_mode)
            if decision.action != "deny" and not _is_shell_call(ev):
                # File-path channel — the only channel for a tool-call write, and a
                # second check when a payload happens to carry both. Skipped *only*
                # for a genuine shell call: gating this on "has a command" instead
                # would let a write tool that carries a command and no file_path
                # through, losing the "deny a write we cannot locate" invariant.
                decision = (
                    plan_artifact_only(ev, worktree_root=worktree_root)
                    if plan_mode
                    else worktree_containment(ev, worktree_root=worktree_root)
                )
    except Exception as e:
        decision = HookDecision.deny(f"wade hook guard error: {type(e).__name__}: {e}")

    return emit_decision(decision, dialect, event=ev.event or event)


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the guard, emit the decision, return the exit code."""
    parser = argparse.ArgumentParser(
        prog="wade-hook",
        description="Apply a wade write-guard to an AI tool's hook payload (stdin).",
        add_help=True,
    )
    parser.add_argument(
        "event",
        help="Canonical hook event: pre_tool_use, stop, or session_start.",
    )
    parser.add_argument(
        "--guard", required=True, help="Guard policy: worktree | plan | session-complete."
    )
    parser.add_argument("--tool", required=True, help="AI tool id (selects the output dialect).")
    parser.add_argument("--root", default="", help="Worktree root — required by write guards.")

    raw_argv = sys.argv[1:] if argv is None else argv
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        # argparse exits 2 on a usage error. For a PreToolUse write guard that
        # would (via the non-zero code) block the edit, which is the safe
        # direction, so let it stand. On a *Stop* event it is the opposite: exit 2
        # means "block the stop", so a malformed invocation (e.g. a worktree path
        # with a space that the tool's runner word-split) would trap the agent with
        # an argparse usage message. The Stop channel fails open even here.
        if _event_from_argv(raw_argv) == "stop":
            return 0
        raise

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

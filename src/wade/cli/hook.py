"""``wade hook`` — discoverable alias for the lean ``wade-hook`` entry point.

The write-guard logic lives in :mod:`wade.hooks.cli` (the dedicated ``wade-hook``
console script), which is what tools' PreToolUse/Stop hooks actually invoke — it
imports only the runtime contract + policies so per-edit latency stays low. This
Typer command exists purely so ``wade hook …`` is discoverable/testable from the
main CLI; it delegates to the exact same code path.

Contract: stdout carries only the decision JSON, and the exit code is the
universal block signal (2 = deny/block, 0 = allow).
"""

from __future__ import annotations

import sys

import typer


def hook_command(
    event: str = typer.Argument(
        ...,
        help="Canonical hook event: pre_tool_use, stop, or session_start.",
    ),
    guard: str = typer.Option(
        ...,
        "--guard",
        help="Guard policy to apply: worktree | plan | session-complete.",
    ),
    tool: str = typer.Option(
        ...,
        "--tool",
        help="AI tool id (selects the output dialect for the decision).",
    ),
    root: str = typer.Option(
        "",
        "--root",
        help="Worktree root — required by the worktree/plan guards.",
    ),
) -> None:
    """Apply a wade write-guard to a tool's hook payload read from stdin."""
    from wade.hooks.cli import _run

    emission = _run(event, guard, tool, root)
    if emission.stdout:
        sys.stdout.write(emission.stdout)
    if emission.stderr:
        sys.stderr.write(emission.stderr + "\n")
    raise typer.Exit(emission.exit_code)

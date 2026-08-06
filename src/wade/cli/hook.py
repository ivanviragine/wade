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
        help="Canonical hook event: pre_tool_use, post_tool_use, stop, or session_start.",
    ),
    guard: str = typer.Option(
        "",
        "--guard",
        help="Guard policy to apply: worktree | plan | session-complete | plan-complete.",
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
    lint_cmd: str | None = typer.Option(
        None,
        "--lint-cmd",
        help="PostToolUse override: lint command (edited path appended unless --unscoped).",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="PostToolUse override: seconds before the linter is abandoned.",
    ),
    unscoped: bool = typer.Option(
        False,
        "--unscoped",
        help="PostToolUse override: run the lint command whole-repo (don't append the path).",
    ),
) -> None:
    """Apply a wade hook to a tool's payload read from stdin.

    Delegates to the same code paths as the lean ``wade-hook`` console script:
    the fail-open PostToolUse lint-feedback branch for ``post_tool_use``, and the
    write/stop guard dispatcher otherwise.
    """
    from wade.hooks.cli import _is_post_tool_use, _run, _run_post_tool_use

    if _is_post_tool_use(event):
        emission = _run_post_tool_use(tool, root, lint_cmd, timeout, unscoped=unscoped)
    else:
        emission = _run(event, guard, tool, root)
    if emission.stdout:
        sys.stdout.write(emission.stdout)
    if emission.stderr:
        sys.stderr.write(emission.stderr + "\n")
    raise typer.Exit(emission.exit_code)

"""``wade hook`` — discoverable alias for the lean ``wade-hook`` entry point.

The write-guard/context logic lives in :mod:`wade.hooks.cli` (the dedicated
``wade-hook`` console script), which is what tools' PreToolUse/PostToolUse/Stop/
SessionStart hooks actually invoke — it imports only the runtime contract +
policies so per-edit latency stays low. This Typer command exists purely so
``wade hook …`` is discoverable/testable from the main ``wade`` CLI.

It forwards its **raw, unparsed** arguments straight to that entry point's
argparse parser (:func:`wade.hooks.cli.main`) instead of re-declaring the flags
as typed Typer options. That is deliberate. A typed Typer option makes Click
reject a malformed invocation — ``--phase`` with no value, ``--timeout nope``, a
missing ``--tool`` — with a usage error (exit 2) *before* the event dispatcher
runs. For the fail-OPEN events (``session_start`` / ``post_tool_use`` / ``stop``)
that violates their contract: a usage error there must exit 0 and never block.
Re-declaring the flags also drifted from the lean parser on every new option
(each addition had to be mirrored in two places and kept semantically identical).
Forwarding argv removes that whole class of parity bug: the alias literally runs
the same parser, dispatch, and fail-open usage-error recovery as ``wade-hook``,
so ``stop``'s *legitimate* block (exit 2) is preserved while a ``stop`` *usage*
error still fails open — a distinction a command-boundary interceptor could not
make, because it cannot tell a parse error from a body exit.

Contract: stdout carries only the decision JSON, and the exit code is the
universal block signal (2 = deny/block, 0 = allow).
"""

from __future__ import annotations

import typer


def hook_command(ctx: typer.Context) -> None:
    """Apply a wade hook to a tool's stdin payload.

    Accepts the raw hook argv — EVENT [--guard G] [--tool T] [--root R] [--phase P]
    [--lint-cmd CMD] [--timeout N] [--unscoped] — and delegates to the exact code
    path the lean 'wade-hook' console script runs, including its fail-open
    usage-error recovery. The flags are parsed downstream, so this command lists no
    per-flag detail; run 'wade-hook --help' for the full parser. (See the module
    docstring for why the args are forwarded rather than typed here.)
    """
    from wade.hooks.cli import main

    raise typer.Exit(main(ctx.args))

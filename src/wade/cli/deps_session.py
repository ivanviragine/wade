"""Detached dependency-analysis session commands."""

from __future__ import annotations

import typer

deps_session_app = typer.Typer(
    help="Dependency-analysis session commands (check).",
)


@deps_session_app.command()
def check() -> None:
    """Verify detached dependency-session output and vote-staging access."""
    from wade.cli.session_shared import run_check

    run_check("deps")

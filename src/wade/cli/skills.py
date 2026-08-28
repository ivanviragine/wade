"""Dynamic skill discovery and resolution diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer

from wade.models.workflow import DelegationKind, SessionKind
from wade.services.skill_diagnostics_service import ResolutionReport

skills_app = typer.Typer(help="Inspect dynamic Agent Skill discovery and bindings.")
_T = TypeVar("_T")


def _run_diagnostic(operation: Callable[[], _T]) -> _T:
    """Run a read-only diagnostic and render expected domain failures tersely."""

    from wade.config.loader import ConfigError
    from wade.services.session_composition_service import SessionCompositionError
    from wade.services.skill_diagnostics_service import SkillDiagnosticsError
    from wade.skills.resolver import SkillResolutionError
    from wade.skills.validation import SkillValidationError

    try:
        return operation()
    except (
        ConfigError,
        SessionCompositionError,
        SkillDiagnosticsError,
        SkillResolutionError,
        SkillValidationError,
    ) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


@skills_app.command("list")
def list_skills() -> None:
    """List validated built-in and discovered project skills."""

    from wade.services.skill_diagnostics_service import project_context
    from wade.skills.catalog import builtin_skills

    _root, _main, _config, inventory = _run_diagnostic(lambda: project_context(Path.cwd()))
    typer.echo("BUILTIN_SKILLS")
    for skill in builtin_skills(inventory.builtin_templates).values():
        typer.echo(f"{skill.descriptor.canonical_ref}\t{skill.descriptor.description}")
    typer.echo("PROJECT_SKILLS")
    for skill in inventory.skills:
        typer.echo(
            f"{skill.descriptor.canonical_ref}\t{skill.origin}:"
            f"{skill.descriptor.source_path}\t{skill.descriptor.content_digest}"
        )


def _print_resolution(report: ResolutionReport) -> None:
    typer.echo(report.identity)
    for slot in report.slots:
        typer.echo(f"slot={slot.slot} digest={slot.digest or 'unresolved'}")
        for candidate in slot.candidates:
            marker = "WINNER" if candidate.selected else "candidate"
            refs = ", ".join(candidate.refs) if candidate.refs else "(none)"
            typer.echo(f"  rank={candidate.rank} {marker} source={candidate.source} refs={refs}")
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")


@skills_app.command("resolve")
def resolve_skills(
    session: SessionKind | None = typer.Option(None, "--session"),  # noqa: B008
    delegation: DelegationKind | None = typer.Option(None, "--delegation"),  # noqa: B008
) -> None:
    """Show effective ordered bindings and every applicable precedence candidate."""

    from wade.services.skill_diagnostics_service import (
        resolve_delegation_report,
        resolve_session_report,
    )

    if (session is None) == (delegation is None):
        typer.echo("error: supply exactly one of --session or --delegation", err=True)
        raise typer.Exit(2)
    if session is not None:
        report = _run_diagnostic(lambda: resolve_session_report(session, cwd=Path.cwd()))
    else:
        assert delegation is not None
        report = _run_diagnostic(lambda: resolve_delegation_report(delegation, cwd=Path.cwd()))
    _print_resolution(report)


@skills_app.command("check")
def check_skills() -> None:
    """Validate discovered trees and every explicitly configured skill reference."""

    from wade.services.skill_diagnostics_service import check_project_skills

    report = check_project_skills(Path.cwd())
    typer.echo("VALID_SKILLS" if report.valid else "INVALID_SKILLS")
    typer.echo(f"builtins={report.builtins}")
    typer.echo(f"project={report.project_skills}")
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")
    for error in report.errors:
        typer.echo(f"error: {error}")
    raise typer.Exit(0 if report.valid else 3)

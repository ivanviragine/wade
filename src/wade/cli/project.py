"""Thin Projects command dispatch and rendering of shared typed results."""

from __future__ import annotations

from typing import Literal

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from wade.models.project import ProjectError, ProjectRequest, ProjectSort, ProjectTarget
from wade.services.project_service import execute

project_app = typer.Typer(
    help="Read, rank and optionally sync one explicit GitHub Projects v2 board.",
    no_args_is_help=True,
)


def _run(
    operation: Literal["items", "rank", "pick", "sync"],
    *,
    owner: str | None,
    owner_kind: str | None,
    number: int | None,
    hostname: str | None,
    columns: str | None,
    sort: list[str] | None,
    filters: list[str] | None,
    repository: str | None,
    label: list[str] | None,
    assignee: str | None,
    status: str | None,
    kind: str | None,
    include_archived: bool,
    limit: int | None,
    json_output: bool,
    count: int = 1,
    apply: bool = False,
    dry_run: bool = False,
) -> None:
    try:
        target = None
        if any(value is not None for value in (owner, owner_kind, number, hostname)):
            if owner is None or owner_kind is None or number is None:
                raise ProjectError(
                    "Target overrides require --owner, --owner-kind and --number together."
                )
            target = ProjectTarget.model_validate(
                {"owner": owner, "kind": owner_kind, "number": number, "hostname": hostname}
            )
        sorts = None
        if sort is not None:
            sorts = []
            for expression in sort:
                field, separator, direction = expression.partition(":")
                if not separator:
                    raise ProjectError("Sort must be alias:asc or alias:desc.")
                sorts.append(ProjectSort.model_validate({"field": field, "direction": direction}))
        request = ProjectRequest.model_validate(
            {
                "operation": operation,
                "target": target,
                "columns": columns.split(",") if columns is not None else None,
                "sort": sorts,
                "filters": filters or [],
                "repository": repository,
                "label": label or [],
                "assignee": assignee,
                "status": status,
                "kind": kind,
                "include_archived": include_archived,
                "limit": limit,
                "count": count,
                "apply": apply,
                "dry_run": dry_run,
            }
        )
        result = execute(request)
    except (ProjectError, ValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        output = Console()
        output.print(
            f"{result.project.owner}/{result.project.number} "
            f"({result.project.hostname}): {result.project.title}",
            markup=False,
        )
        table = Table(*result.columns)
        for row in result.rows:
            table.add_row(
                *(
                    str(row.values.get(column)) if row.values.get(column) is not None else "—"
                    for column in result.columns
                )
            )
        output.print(table)
        if any(row.contributions for row in result.rows):
            factors = Table("Item", "Factor", "Raw", "Normalized", "Weight", "Weighted")
            for row in result.rows:
                for contribution in row.contributions:
                    factors.add_row(
                        row.item.identity,
                        contribution.field,
                        str(contribution.raw),
                        str(contribution.normalized),
                        str(contribution.weight),
                        str(contribution.weighted),
                    )
            output.print(factors)
        if result.operations:
            changes = Table("Item", "Field", "Old", "New", "Outcome")
            for change in result.operations:
                changes.add_row(
                    change.identity,
                    f"{change.field} ({change.field_id})",
                    str(change.old),
                    str(change.new),
                    change.status,
                )
            output.print(changes)
        if result.current_wip is not None:
            output.print(
                f"WIP {result.current_wip}/{result.wip_limit}; "
                f"available slots: {result.available_slots}. Advisory only."
            )
        for exclusion in result.excluded:
            output.print(f"Excluded {exclusion.identity}: {exclusion.reason}", markup=False)
        if result.reason:
            output.print(result.reason, markup=False)
        output.print(result.visibility, markup=False)
    raise typer.Exit(0 if result.success else 1)


@project_app.command("items")
def items(
    owner: str | None = typer.Option(
        None, "--owner", help="Exact project owner login; overrides require a complete target."
    ),
    owner_kind: str | None = typer.Option(None, "--owner-kind", help="user or organization."),
    number: int | None = typer.Option(
        None, "--number", min=1, help="Project number, not a node ID."
    ),
    hostname: str | None = typer.Option(None, "--hostname", help="gh authentication host."),
    columns: str | None = typer.Option(
        None, "--columns", help="Comma-separated ordered column aliases."
    ),
    sort: list[str] | None = typer.Option(  # noqa: B008
        None, "--sort", help="Repeat alias:asc|desc; listing sorts only."
    ),
    filters: list[str] | None = typer.Option(  # noqa: B008
        None, "--filter", help="Repeat exact alias=value (AND)."
    ),
    repository: str | None = typer.Option(None, "--repository", help="Qualified owner/repo."),
    label: list[str] | None = typer.Option(  # noqa: B008
        None, "--label", help="Exact label membership (repeatable)."
    ),
    assignee: str | None = typer.Option(None, "--assignee", help="Exact GitHub login membership."),
    status: str | None = typer.Option(
        None, "--status", help="Option ID/name/alias in the configured WIP Status field."
    ),
    kind: str | None = typer.Option(None, "--kind", help="Issue, PullRequest or DraftIssue."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Listing only; rank/pick/sync always exclude archived items.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Presentation limit, applied after complete evaluation."
    ),
    json_output: bool = typer.Option(False, "--json", help="One JSON document on stdout."),
) -> None:
    """List complete visible project items with typed filters and sorting."""
    _run(
        "items",
        owner=owner,
        owner_kind=owner_kind,
        number=number,
        hostname=hostname,
        columns=columns,
        sort=sort,
        filters=filters,
        repository=repository,
        label=label,
        assignee=assignee,
        status=status,
        kind=kind,
        include_archived=include_archived,
        limit=limit,
        json_output=json_output,
    )


@project_app.command("rank")
def rank(
    owner: str | None = typer.Option(
        None, "--owner", help="Exact project owner login; overrides require a complete target."
    ),
    owner_kind: str | None = typer.Option(None, "--owner-kind", help="user or organization."),
    number: int | None = typer.Option(
        None, "--number", min=1, help="Project number, not a node ID."
    ),
    hostname: str | None = typer.Option(None, "--hostname", help="gh authentication host."),
    columns: str | None = typer.Option(
        None, "--columns", help="Comma-separated ordered column aliases."
    ),
    sort: list[str] | None = typer.Option(  # noqa: B008
        None, "--sort", help="Repeat alias:asc|desc; listing sorts only."
    ),
    filters: list[str] | None = typer.Option(  # noqa: B008
        None, "--filter", help="Repeat exact alias=value (AND)."
    ),
    repository: str | None = typer.Option(None, "--repository", help="Qualified owner/repo."),
    label: list[str] | None = typer.Option(  # noqa: B008
        None, "--label", help="Exact label membership (repeatable)."
    ),
    assignee: str | None = typer.Option(None, "--assignee", help="Exact GitHub login membership."),
    status: str | None = typer.Option(
        None, "--status", help="Option ID/name/alias in the configured WIP Status field."
    ),
    kind: str | None = typer.Option(None, "--kind", help="Issue, PullRequest or DraftIssue."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Listing only; rank/pick/sync always exclude archived items.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Presentation limit, applied after complete evaluation."
    ),
    json_output: bool = typer.Option(False, "--json", help="One JSON document on stdout."),
) -> None:
    """Rank nonarchived items with explicit reproducible contributions."""
    _run(
        "rank",
        owner=owner,
        owner_kind=owner_kind,
        number=number,
        hostname=hostname,
        columns=columns,
        sort=sort,
        filters=filters,
        repository=repository,
        label=label,
        assignee=assignee,
        status=status,
        kind=kind,
        include_archived=include_archived,
        limit=limit,
        json_output=json_output,
    )


@project_app.command("pick")
def pick(
    owner: str | None = typer.Option(
        None, "--owner", help="Exact project owner login; overrides require a complete target."
    ),
    owner_kind: str | None = typer.Option(None, "--owner-kind", help="user or organization."),
    number: int | None = typer.Option(
        None, "--number", min=1, help="Project number, not a node ID."
    ),
    hostname: str | None = typer.Option(None, "--hostname", help="gh authentication host."),
    columns: str | None = typer.Option(
        None, "--columns", help="Comma-separated ordered column aliases."
    ),
    sort: list[str] | None = typer.Option(  # noqa: B008
        None, "--sort", help="Repeat alias:asc|desc; listing sorts only."
    ),
    filters: list[str] | None = typer.Option(  # noqa: B008
        None, "--filter", help="Repeat exact alias=value (AND)."
    ),
    repository: str | None = typer.Option(None, "--repository", help="Qualified owner/repo."),
    label: list[str] | None = typer.Option(  # noqa: B008
        None, "--label", help="Exact label membership (repeatable)."
    ),
    assignee: str | None = typer.Option(None, "--assignee", help="Exact GitHub login membership."),
    status: str | None = typer.Option(
        None, "--status", help="Option ID/name/alias in the configured WIP Status field."
    ),
    kind: str | None = typer.Option(None, "--kind", help="Issue, PullRequest or DraftIssue."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Listing only; rank/pick/sync always exclude archived items.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Presentation limit, applied after complete evaluation."
    ),
    json_output: bool = typer.Option(False, "--json", help="One JSON document on stdout."),
    count: int = typer.Option(1, "--count", min=1, help="Maximum advisory selections."),
) -> None:
    """Advise which ranked ready open issues fit the complete WIP pool."""
    _run(
        "pick",
        owner=owner,
        owner_kind=owner_kind,
        number=number,
        hostname=hostname,
        columns=columns,
        sort=sort,
        filters=filters,
        repository=repository,
        label=label,
        assignee=assignee,
        status=status,
        kind=kind,
        include_archived=include_archived,
        limit=limit,
        json_output=json_output,
        count=count,
    )


@project_app.command("sync")
def sync(
    owner: str | None = typer.Option(
        None, "--owner", help="Exact project owner login; overrides require a complete target."
    ),
    owner_kind: str | None = typer.Option(None, "--owner-kind", help="user or organization."),
    number: int | None = typer.Option(
        None, "--number", min=1, help="Project number, not a node ID."
    ),
    hostname: str | None = typer.Option(None, "--hostname", help="gh authentication host."),
    columns: str | None = typer.Option(
        None, "--columns", help="Comma-separated ordered column aliases."
    ),
    sort: list[str] | None = typer.Option(  # noqa: B008
        None, "--sort", help="Repeat alias:asc|desc; listing sorts only."
    ),
    filters: list[str] | None = typer.Option(  # noqa: B008
        None, "--filter", help="Repeat exact alias=value (AND)."
    ),
    repository: str | None = typer.Option(None, "--repository", help="Qualified owner/repo."),
    label: list[str] | None = typer.Option(  # noqa: B008
        None, "--label", help="Exact label membership (repeatable)."
    ),
    assignee: str | None = typer.Option(None, "--assignee", help="Exact GitHub login membership."),
    status: str | None = typer.Option(
        None, "--status", help="Option ID/name/alias in the configured WIP Status field."
    ),
    kind: str | None = typer.Option(None, "--kind", help="Issue, PullRequest or DraftIssue."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Listing only; rank/pick/sync always exclude archived items.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Presentation limit, applied after complete evaluation."
    ),
    json_output: bool = typer.Option(False, "--json", help="One JSON document on stdout."),
    apply: bool = typer.Option(
        False, "--apply", help="Explicitly enable sequential numeric writes."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Read-only preview (the default); conflicts with --apply."
    ),
) -> None:
    """Preview numeric score/rank write-back; only --apply sends mutations."""
    _run(
        "sync",
        owner=owner,
        owner_kind=owner_kind,
        number=number,
        hostname=hostname,
        columns=columns,
        sort=sort,
        filters=filters,
        repository=repository,
        label=label,
        assignee=assignee,
        status=status,
        kind=kind,
        include_archived=include_archived,
        limit=limit,
        json_output=json_output,
        apply=apply,
        dry_run=dry_run,
    )

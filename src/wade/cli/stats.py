"""``wade stats`` — local usage statistics (summary, sessions, migrate, ui, …)."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer

stats_app = typer.Typer(
    help="Local usage statistics: tokens, AI tools, models, PRs and insights.",
    invoke_without_command=True,
)

_SINCE_HELP = "Window start: 7d, 12h, 4w, 3m, 1y, YYYY-MM-DD or 'all'."


def _build_filter(
    since: str | None,
    until: str | None,
    project: list[str] | None,
    all_projects: bool,
    tool: list[str] | None,
    model: list[str] | None,
    kind: list[str] | None,
    task: str | None,
) -> tuple[object, str]:
    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.console import console

    try:
        flt = StatsFilter(
            since=stats_service.parse_time_bound(since),
            until=stats_service.parse_time_bound(until),
            tools=tool or [],
            models=model or [],
            kinds=kind or [],
            task_id=task,
        )
    except ValueError as exc:
        console.error(str(exc), markup=False)
        raise typer.Exit(2) from exc
    if project:
        flt.projects = list(project)
        scope = ", ".join(project)
    elif all_projects:
        scope = "all projects"
    else:
        ref = stats_service.resolve_project(Path.cwd())
        flt.projects = [ref.root]
        scope = ref.label
    window = f"since {since}" if since and since != "all" else "all time"
    return flt, f"{scope} · {window}"


# Shared option declarations (Typer needs them per command signature).
_since_opt = typer.Option("30d", "--since", "-s", help=_SINCE_HELP)
_until_opt = typer.Option(None, "--until", help="Window end (same formats as --since).")
_project_opt = typer.Option(
    None, "--project", "-p", help="Project slug (owner/repo) or root path; repeatable."
)
_all_opt = typer.Option(False, "--all-projects", "-A", help="Include every recorded project.")
_tool_opt = typer.Option(None, "--tool", "-t", help="AI tool (claude, codex, …); repeatable.")
_model_opt = typer.Option(None, "--model", "-m", help="Model name; repeatable.")
_kind_opt = typer.Option(
    None,
    "--kind",
    "-k",
    help="plan, implement, review_pr_comments or delegation; repeatable.",
)
_task_opt = typer.Option(None, "--task", help="Only this task ID.")
_json_opt = typer.Option(False, "--json", help="Output as JSON.")


@stats_app.callback()
def stats_callback(
    ctx: typer.Context,
    since: str = _since_opt,
    until: str | None = _until_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    tool: list[str] | None = _tool_opt,
    model: list[str] | None = _model_opt,
    kind: list[str] | None = _kind_opt,
    task: str | None = _task_opt,
    json_output: bool = _json_opt,
) -> None:
    """Local usage statistics. With no subcommand, prints the summary."""
    if ctx.invoked_subcommand is None:
        summary(
            since=since,
            until=until,
            project=project,
            all_projects=all_projects,
            tool=tool,
            model=model,
            kind=kind,
            task=task,
            json_output=json_output,
        )


@stats_app.command()
def summary(
    since: str = _since_opt,
    until: str | None = _until_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    tool: list[str] | None = _tool_opt,
    model: list[str] | None = _model_opt,
    kind: list[str] | None = _kind_opt,
    task: str | None = _task_opt,
    json_output: bool = _json_opt,
) -> None:
    """Totals, breakdowns by kind/tool/model, costliest tasks, PR flow and insights."""
    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.stats_view import render_report

    flt, scope = _build_filter(since, until, project, all_projects, tool, model, kind, task)
    assert isinstance(flt, StatsFilter)
    report = stats_service.build_stats_report(flt)
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        render_report(report, scope=scope)
    raise typer.Exit(0)


@stats_app.command()
def insights(
    since: str = _since_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    json_output: bool = _json_opt,
) -> None:
    """Only the data-backed observations (trends, rework, failures, efficiency)."""
    import json

    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.stats_view import render_insights

    flt, _ = _build_filter(since, None, project, all_projects, None, None, None, None)
    assert isinstance(flt, StatsFilter)
    report = stats_service.build_stats_report(flt)
    if json_output:
        typer.echo(json.dumps([i.model_dump() for i in report.insights], indent=2))
    else:
        render_insights(report)
    raise typer.Exit(0)


@stats_app.command()
def sessions(
    since: str = _since_opt,
    until: str | None = _until_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    tool: list[str] | None = _tool_opt,
    model: list[str] | None = _model_opt,
    kind: list[str] | None = _kind_opt,
    task: str | None = _task_opt,
    limit: int = typer.Option(50, "--limit", "-n", help="Most recent N sessions (0 = all)."),
    json_output: bool = _json_opt,
) -> None:
    """List recorded AI sessions, most recent first."""
    import json

    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.stats_view import render_sessions

    flt, _ = _build_filter(since, until, project, all_projects, tool, model, kind, task)
    assert isinstance(flt, StatsFilter)
    rows = list(reversed(stats_service.session_rows(stats_service.list_sessions(flt))))
    if limit > 0:
        rows = rows[:limit]
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        render_sessions(rows)
    raise typer.Exit(0)


@stats_app.command()
def prs(
    since: str = _since_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    task: str | None = _task_opt,
    json_output: bool = _json_opt,
) -> None:
    """List pull requests imported by `wade stats migrate`."""
    import json

    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.stats_view import render_prs

    flt, _ = _build_filter(since, None, project, all_projects, None, None, None, task)
    assert isinstance(flt, StatsFilter)
    rows = list(reversed(stats_service.list_pull_requests(flt)))
    if json_output:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in rows], indent=2))
    else:
        render_prs(rows)
    raise typer.Exit(0)


@stats_app.command()
def export(
    since: str = typer.Option("all", "--since", "-s", help=_SINCE_HELP),
    until: str | None = _until_opt,
    project: list[str] | None = _project_opt,
    all_projects: bool = _all_opt,
    tool: list[str] | None = _tool_opt,
    model: list[str] | None = _model_opt,
    kind: list[str] | None = _kind_opt,
    task: str | None = _task_opt,
    fmt: str = typer.Option("csv", "--format", "-f", help="csv, json or jsonl."),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Write to a file."
    ),
) -> None:
    """Export sessions (with resolved tokens and estimated cost) for spreadsheets/notebooks."""
    from wade.models.stats import StatsFilter
    from wade.services import stats_service
    from wade.ui.console import console

    flt, _ = _build_filter(since, until, project, all_projects, tool, model, kind, task)
    assert isinstance(flt, StatsFilter)
    try:
        data = stats_service.export_sessions(flt, fmt)
    except ValueError as exc:
        console.error(str(exc), markup=False)
        raise typer.Exit(2) from exc
    if output is None:
        typer.echo(data, nl=False)
    else:
        output.write_text(data, encoding="utf-8")
        console.success(f"Wrote {output}")
    raise typer.Exit(0)


@stats_app.command()
def migrate(
    limit: int = typer.Option(1000, "--limit", help="Max PRs and max tasks to read."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count what would be imported."),
    no_prs: bool = typer.Option(False, "--no-prs", help="Skip pull requests."),
    no_tasks: bool = typer.Option(False, "--no-tasks", help="Skip issues/tasks."),
    json_output: bool = _json_opt,
) -> None:
    """Import WADE's GitHub history (PRs + issues) into the local database.

    Reads every PR and task of the current project and stores their metadata
    and every usage table WADE wrote into them (planning, implementation and
    review sessions). Idempotent: re-run any time to refresh; sessions already
    present (recorded locally or imported before) are never duplicated.
    """
    from wade.config.loader import load_config
    from wade.providers.registry import get_provider
    from wade.services import stats_service
    from wade.ui.console import console

    cwd = Path.cwd()
    config = load_config(cwd)
    provider = None
    if not no_tasks:
        try:
            provider = get_provider(config)
        except Exception as exc:
            console.warn(f"Task provider unavailable, skipping tasks: {exc}")
    with console.status("Reading pull requests and tasks…"):
        result = stats_service.migrate_from_github(
            cwd,
            provider=provider,
            branch_prefix=config.project.branch_prefix,
            issue_label=config.project.issue_label,
            limit=limit,
            include_prs=not no_prs,
            include_tasks=not no_tasks,
            dry_run=dry_run,
        )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        verb = "Would import" if dry_run else "Imported"
        console.summary_table(
            [
                ("Project", result.project),
                ("Pull requests", f"{result.prs_seen} read · {result.wade_prs} WADE-managed"),
                ("Tasks", f"{result.tasks_seen} read · {result.wade_tasks} WADE-managed"),
                (
                    "AI sessions",
                    f"{verb} {result.sessions_imported}"
                    f" · {result.sessions_skipped} already present",
                ),
            ],
            title="wade stats migrate" + (" (dry run)" if dry_run else ""),
        )
        for err in result.errors:
            console.warn(err)
        if not result.errors and not dry_run:
            console.hint("Run `wade stats` or `wade stats ui` to explore.")
    raise typer.Exit(1 if result.errors and not (result.prs_seen or result.tasks_seen) else 0)


# Hidden alias: `sync` reads better for periodic refreshes.
stats_app.command("sync", hidden=True)(migrate)


@stats_app.command()
def ui(
    port: int = typer.Option(0, "--port", help="Port to bind (0 = pick a free one)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser."),
    migrate_first: bool = typer.Option(
        False, "--migrate", help="Run `wade stats migrate` for this project first."
    ),
) -> None:
    """Open the local dashboard (charts, filters, sessions table, insights)."""
    from wade.services.stats_dashboard import serve
    from wade.ui.console import console

    if migrate_first:
        with contextlib.suppress(typer.Exit):
            migrate(limit=1000, dry_run=False, no_prs=False, no_tasks=False, json_output=False)
    current = None
    try:
        from wade.services import stats_service

        current = stats_service.resolve_project(Path.cwd()).label
    except Exception:
        current = None
    serve(port=port, open_browser=not no_browser, default_project=current, log=console.info)
    raise typer.Exit(0)


@stats_app.command()
def info(json_output: bool = _json_opt) -> None:
    """Show where stats live, what is stored, and this project's storage mode."""
    import json

    from wade.config.loader import load_config
    from wade.services import stats_service
    from wade.stats.pricing import PRICING_AS_OF, default_pricing_path
    from wade.ui.console import console

    data = stats_service.store_info()
    try:
        storage = load_config(Path.cwd()).stats.storage.value
    except Exception:
        storage = "unknown"
    data["storage_mode"] = storage
    data["local_recording"] = stats_service.stats_enabled() and storage in ("both", "local")
    data["pricing_file"] = str(default_pricing_path())
    data["builtin_prices_as_of"] = PRICING_AS_OF
    if json_output:
        typer.echo(json.dumps(data, indent=2))
        raise typer.Exit(0)
    counts = data.get("counts", {})
    console.summary_table(
        [
            ("Database", data["path"] + ("" if data["exists"] else "  (not created yet)")),
            ("Storage mode", f"{storage}  (stats.storage in .wade.yml)"),
            ("Local recording", "on" if data["local_recording"] else "off"),
            (
                "Stored",
                ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) or "nothing",
            ),
            ("Pricing overrides", data["pricing_file"]),
        ],
        title="wade stats info",
    )
    raise typer.Exit(0)


@stats_app.command()
def prune(
    older_than: str = typer.Option(..., "--older-than", help="e.g. 180d, 1y or YYYY-MM-DD."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete stored sessions, events and PRs older than a cutoff."""
    from wade.services import stats_service
    from wade.ui import prompts
    from wade.ui.console import console

    try:
        cutoff = stats_service.parse_time_bound(older_than)
    except ValueError as exc:
        console.error(str(exc), markup=False)
        raise typer.Exit(2) from exc
    if cutoff is None:
        console.error("--older-than needs a concrete cutoff", markup=False)
        raise typer.Exit(2)
    if not yes and not prompts.confirm(
        f"Delete stats recorded before {cutoff:%Y-%m-%d}?", default=False
    ):
        raise typer.Exit(1)
    removed = stats_service.prune(cutoff)
    console.success(f"Removed {removed} sessions (and older events/PRs).")
    raise typer.Exit(0)

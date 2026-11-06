"""Terminal rendering for ``wade stats`` (Rich tables; presentation only)."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text

from wade.models.stats import (
    BreakdownRow,
    InsightLevel,
    PullRequestRecord,
    StatsReport,
    UsageTotals,
)
from wade.ui.console import console


def fmt_tokens(n: float | None) -> str:
    if n is None:
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1_000:.0f}K"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


def fmt_usd(x: float | None, *, covered: bool = True) -> str:
    if x is None or not covered:
        return "—"
    return f"${x:,.2f}" if x >= 0.01 or x == 0 else "<$0.01"


def fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_hours(h: float | None) -> str:
    if h is None:
        return "—"
    return f"{h / 24:.1f}d" if h >= 48 else f"{h:.1f}h"


def _delta(now: float, before: float | None) -> str:
    if not before:
        return ""
    change = (now - before) / before
    if abs(change) < 0.005:
        return ""
    arrow = "▲" if change > 0 else "▼"
    return f" {arrow}{abs(change) * 100:.0f}%"


def _breakdown_table(
    title: str, rows: list[BreakdownRow], *, limit: int = 10, timing: bool = True
) -> Table:
    """Breakdown table; ``timing=False`` drops Avg time/Fail when nothing was timed."""
    table = Table(title=title, title_justify="left", title_style="header.title", box=None)
    table.add_column(title.split(" ")[-1].capitalize(), no_wrap=True)
    names = ["Sessions", "Fresh", "Cache reads", "Cache %", "Est. cost"]
    if timing:
        names += ["Avg time", "Fail"]
    for name in names:
        table.add_column(name, justify="right")
    for row in rows[:limit]:
        t = row.totals
        cells: list[Text | str] = [
            Text(row.key),
            str(t.sessions),
            fmt_tokens(t.fresh_tokens),
            # A count next to an unknown share would read as "measured": hide both.
            fmt_tokens(t.cached_tokens) if t.cache_ratio is not None else "—",
            fmt_pct(t.cache_ratio),
            fmt_usd(t.cost_usd, covered=t.priced_tokens > 0),
        ]
        if timing:
            cells += [fmt_duration(t.avg_duration_s), fmt_pct(t.failure_rate)]
        table.add_row(*cells)
    if len(rows) > limit:
        rest = UsageTotals()
        for row in rows[limit:]:
            for field in ("sessions", "fresh_tokens", "cached_tokens", "cost_usd"):
                setattr(rest, field, getattr(rest, field) + getattr(row.totals, field))
        cells = [
            Text(f"{len(rows) - limit} more"),
            str(rest.sessions),
            fmt_tokens(rest.fresh_tokens),
            fmt_tokens(rest.cached_tokens) if rest.cached_tokens else "—",
            "",
            fmt_usd(rest.cost_usd),
        ]
        if timing:
            cells += ["", ""]
        table.add_row(*cells, style="dim")
    return table


def render_report(report: StatsReport, *, scope: str) -> None:
    out = console.out
    t = report.totals
    prev = report.previous_totals
    console.rule(f"wade stats · {scope}")
    rows: list[tuple[str, str]] = [
        ("AI sessions", f"{t.sessions}{_delta(t.sessions, prev.sessions if prev else None)}"),
        (
            "Fresh tokens",
            f"{fmt_tokens(t.fresh_tokens)}"
            f"{_delta(t.fresh_tokens, prev.fresh_tokens if prev else None)}"
            f"  (in {fmt_tokens(t.input_tokens)} · out {fmt_tokens(t.output_tokens)})",
        ),
        (
            "Cache reads",
            f"{fmt_tokens(t.cached_tokens)}  ({fmt_pct(t.cache_ratio)} of prompt tokens;"
            f" total incl. cache {fmt_tokens(t.total_tokens)})"
            if t.cached_tokens
            else "— (not reported by these tools)",
        ),
        (
            "Est. cost",
            f"{fmt_usd(t.cost_usd, covered=t.priced_tokens > 0)}"
            f"  ({fmt_pct(t.price_coverage)} of tokens priced)",
        ),
    ]
    migrated = t.sessions - t.local_sessions
    if t.timed_sessions:
        rows.append(("AI time", fmt_duration(t.duration_s)))
        rows.append(("Failure rate", fmt_pct(t.failure_rate)))
    elif t.sessions:
        rows.append(
            ("AI time / failures", "— (only recorded for sessions launched after upgrading)")
        )
    if migrated:
        rows.append(
            (
                "Sources",
                f"{t.local_sessions} recorded locally · {migrated} migrated from PR/issue bodies",
            )
        )
    if t.premium_requests:
        rows.append(("Premium requests", str(t.premium_requests)))
    flow = report.prs
    if flow.opened:
        rows.append(
            (
                "Pull requests",
                f"{flow.opened} opened · {flow.merged} merged · {flow.open} open"
                f" · median lead time {fmt_hours(flow.median_lead_time_h)}",
            )
        )
    if report.tasks_created:
        rows.append(
            (
                "Tasks",
                f"{report.tasks_created} created · {report.tasks_closed} closed"
                f" · median cycle time {fmt_hours(flow.median_cycle_time_h)}",
            )
        )
    console.summary_table(rows)
    out.print()
    timing = t.timed_sessions > 0 or t.finished > 0
    if report.by_kind:
        out.print(_breakdown_table("By workflow kind", report.by_kind, timing=timing))
        out.print()
    if report.by_tool:
        out.print(_breakdown_table("By AI tool", report.by_tool, timing=timing))
        out.print()
    if report.by_model:
        out.print(_breakdown_table("By model", report.by_model, timing=timing))
        out.print()
    if len(report.by_project) > 1:
        out.print(_breakdown_table("By project", report.by_project, timing=timing))
        out.print()
    if report.top_tasks:
        table = Table(
            title="Most expensive tasks", title_justify="left", title_style="header.title", box=None
        )
        for name, justify in (
            ("Task", "left"),
            ("Project", "left"),
            ("Sessions", "right"),
            ("Fresh", "right"),
            ("Est. cost", "right"),
            ("Reviews", "right"),
            ("PRs", "left"),
        ):
            table.add_column(name, justify=justify)  # type: ignore[arg-type]
        for row in report.top_tasks[:8]:
            table.add_row(
                Text(row.task_id),
                Text(row.project),
                str(row.totals.sessions),
                fmt_tokens(row.totals.fresh_tokens),
                fmt_usd(row.totals.cost_usd, covered=row.totals.priced_tokens > 0),
                str(row.review_sessions),
                Text(", ".join(f"#{n}" for n in row.pr_numbers) + (" ✓" if row.merged else "")),
            )
        out.print(table)
        out.print()
    render_insights(report)


def render_insights(report: StatsReport) -> None:
    if not report.insights:
        return
    console.rule("Insights")
    icons = {InsightLevel.GOOD: "✓", InsightLevel.WARNING: "!", InsightLevel.INFO: "·"}
    styles = {InsightLevel.GOOD: "green", InsightLevel.WARNING: "yellow", InsightLevel.INFO: "cyan"}
    for ins in report.insights:
        line = Text()
        line.append(f" {icons[ins.level]} ", style=styles[ins.level])
        line.append(ins.title, style="bold")
        console.out.print(line)
        console.out.print(Text(f"   {ins.detail}", style="dim"))


def render_sessions(rows: list[dict[str, Any]]) -> None:
    table = Table(box=None)
    for name, justify in (
        ("Started", "left"),
        ("Kind", "left"),
        ("Task", "left"),
        ("Tool", "left"),
        ("Model", "left"),
        ("Time", "right"),
        ("Fresh", "right"),
        ("Est. cost", "right"),
        ("Outcome", "left"),
        ("Source", "left"),
    ):
        table.add_column(name, justify=justify)  # type: ignore[arg-type]
    for r in rows:
        kind = r["kind"] + (f":{r['operation']}" if r.get("operation") else "")
        table.add_row(
            r["started_at"][:16].replace("T", " "),
            Text(kind),
            Text(str(r["task_id"] or "—")),
            Text(str(r["ai_tool"] or "—")),
            Text(str(r["model"] or "—")),
            fmt_duration(r["duration_s"]),
            fmt_tokens(r["fresh_tokens"]) if r["fresh_tokens"] else "—",
            fmt_usd(r["cost_usd"]) if r["cost_usd"] is not None else "—",
            r["outcome"],
            r["source"],
        )
    console.out.print(table)


def render_prs(prs: list[PullRequestRecord]) -> None:
    table = Table(box=None)
    for name, justify in (
        ("PR", "right"),
        ("Title", "left"),
        ("Task", "left"),
        ("State", "left"),
        ("Created", "left"),
        ("Lead time", "right"),
        ("+/-", "right"),
        ("WADE", "left"),
    ):
        table.add_column(name, justify=justify)  # type: ignore[arg-type]
    for pr in prs:
        size = (
            f"+{pr.additions or 0}/-{pr.deletions or 0}"
            if pr.additions is not None or pr.deletions is not None
            else "—"
        )
        table.add_row(
            f"#{pr.number}",
            Text(pr.title[:60]),
            Text(pr.task_id or "—"),
            "draft" if pr.is_draft and pr.state.upper() == "OPEN" else pr.state.lower(),
            pr.created_at.strftime("%Y-%m-%d") if pr.created_at else "—",
            fmt_hours(pr.lead_time_h),
            size,
            "✓" if pr.wade_managed else "",
        )
    console.out.print(table)

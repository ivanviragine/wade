"""Aggregate stored sessions and PRs into a :class:`StatsReport` (pure)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable

from wade.models.stats import (
    AISessionRecord,
    BreakdownRow,
    DailyPoint,
    PRFlowSummary,
    PullRequestRecord,
    StatsFilter,
    StatsOutcome,
    StatsReport,
    StatsSessionKind,
    TaskRecord,
    TaskRow,
    UsageTotals,
)
from wade.stats.pricing import PriceBook

TOP_TASKS = 15
_FINISHED = {StatsOutcome.SUCCESS, StatsOutcome.FAILED, StatsOutcome.TIMEOUT}
_FAILED = {StatsOutcome.FAILED, StatsOutcome.TIMEOUT}


def session_tokens(s: AISessionRecord) -> tuple[int, int, int, int]:
    """(input, output, cached, total) with breakdown fallbacks."""
    inp = s.input_tokens
    out = s.output_tokens
    cac = s.cached_tokens
    if s.models:
        inp = inp if inp is not None else sum(m.input_tokens for m in s.models)
        out = out if out is not None else sum(m.output_tokens for m in s.models)
        cac = cac if cac is not None else sum(m.cached_tokens for m in s.models)
    inp, out, cac = inp or 0, out or 0, cac or 0
    total = s.total_tokens if s.total_tokens is not None else inp + out + cac
    return inp, out, cac, total


def session_cost(s: AISessionRecord, prices: PriceBook) -> tuple[float, int, set[str]]:
    """(usd, priced_tokens, unpriced_model_names) for one session."""
    unpriced: set[str] = set()
    if s.models:
        usd = 0.0
        priced = 0
        for m in s.models:
            c = prices.cost(m.model, m.input_tokens, m.output_tokens, m.cached_tokens)
            if c is None:
                if m.input_tokens + m.output_tokens + m.cached_tokens:
                    unpriced.add(m.model)
                continue
            usd += c
            priced += m.input_tokens + m.output_tokens + m.cached_tokens
        return usd, priced, unpriced
    inp, out, cac, total = session_tokens(s)
    model = s.effective_model
    c = prices.cost(model, inp, out, cac)
    if c is None:
        if total:
            unpriced.add(model or "unknown")
        return 0.0, 0, unpriced
    return c, inp + out + cac, unpriced


def _add(t: UsageTotals, s: AISessionRecord, prices: PriceBook) -> set[str]:
    inp, out, cac, total = session_tokens(s)
    usd, priced, unpriced = session_cost(s, prices)
    t.sessions += 1
    if s.outcome in _FINISHED:
        t.finished += 1
    if s.outcome in _FAILED:
        t.failures += 1
    t.input_tokens += inp
    t.output_tokens += out
    t.cached_tokens += cac
    t.total_tokens += total
    t.premium_requests += s.premium_requests or 0
    t.cost_usd += usd
    t.priced_tokens += priced
    if s.duration_s is not None:
        t.duration_s += s.duration_s
        t.timed_sessions += 1
    return unpriced


def totals_for(sessions: Iterable[AISessionRecord], prices: PriceBook) -> UsageTotals:
    t = UsageTotals()
    for s in sessions:
        _add(t, s, prices)
    return t


def _breakdown(
    sessions: list[AISessionRecord],
    prices: PriceBook,
    key: Callable[[AISessionRecord], str],
) -> list[BreakdownRow]:
    groups: dict[str, UsageTotals] = defaultdict(UsageTotals)
    for s in sessions:
        _add(groups[key(s)], s, prices)
    rows = [BreakdownRow(key=k, totals=v) for k, v in groups.items()]
    rows.sort(key=lambda r: (r.totals.total_tokens, r.totals.sessions), reverse=True)
    return rows


def _model_breakdown(sessions: list[AISessionRecord], prices: PriceBook) -> list[BreakdownRow]:
    """Attribute tokens per model row; sessions without a breakdown go whole."""
    groups: dict[str, UsageTotals] = defaultdict(UsageTotals)
    for s in sessions:
        if not s.models:
            _add(groups[s.effective_model or "unknown"], s, prices)
            continue
        for m in s.models:
            part = s.model_copy(
                update={
                    "models": [m],
                    "model": m.model,
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "cached_tokens": m.cached_tokens,
                    "total_tokens": m.input_tokens + m.output_tokens + m.cached_tokens,
                    "premium_requests": m.premium_requests,
                }
            )
            _add(groups[m.model], part, prices)
    rows = [BreakdownRow(key=k, totals=v) for k, v in groups.items()]
    rows.sort(key=lambda r: (r.totals.total_tokens, r.totals.sessions), reverse=True)
    return rows


def _daily(sessions: list[AISessionRecord], prices: PriceBook) -> list[DailyPoint]:
    days: dict[str, DailyPoint] = {}
    for s in sessions:
        day = s.started_at.strftime("%Y-%m-%d")
        point = days.setdefault(day, DailyPoint(day=day))
        tool = s.ai_tool or "unknown"
        point.tokens[tool] = point.tokens.get(tool, 0) + session_tokens(s)[3]
        point.sessions += 1
        point.cost_usd += session_cost(s, prices)[0]
    return [days[d] for d in sorted(days)]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _pr_key(project: str, task_id: str | None) -> tuple[str, str] | None:
    return (project, task_id) if task_id else None


def build_report(
    sessions: list[AISessionRecord],
    prs: list[PullRequestRecord],
    prices: PriceBook,
    flt: StatsFilter | None = None,
    previous: list[AISessionRecord] | None = None,
    task_records: list[TaskRecord] | None = None,
    all_task_records: list[TaskRecord] | None = None,
) -> StatsReport:
    """Aggregate everything the CLI and dashboard show (insights added separately)."""
    report = StatsReport(filter=flt or StatsFilter())
    unpriced: set[str] = set()
    for s in sessions:
        unpriced |= _add(report.totals, s, prices)
    report.unpriced_models = sorted(unpriced)
    if previous is not None:
        report.previous_totals = totals_for(previous, prices)

    report.by_tool = _breakdown(sessions, prices, lambda s: s.ai_tool or "unknown")
    report.by_kind = _breakdown(
        sessions,
        prices,
        lambda s: (
            f"{s.kind.value}:{s.operation}"
            if s.kind is StatsSessionKind.DELEGATION and s.operation
            else s.kind.value
        ),
    )
    report.by_project = _breakdown(sessions, prices, lambda s: s.project.label)
    report.by_model = _model_breakdown(sessions, prices)
    report.daily = _daily(sessions, prices)

    # --- tasks ------------------------------------------------------------
    pr_by_task: dict[tuple[str, str], list[PullRequestRecord]] = defaultdict(list)
    for pr in prs:
        if (k := _pr_key(pr.project.label, pr.task_id)) is not None:
            pr_by_task[k].append(pr)

    tasks: dict[tuple[str, str], TaskRow] = {}
    for s in sessions:
        if not s.task_id:
            continue
        k = (s.project.label, s.task_id)
        row = tasks.setdefault(k, TaskRow(task_id=s.task_id, project=s.project.label))
        _add(row.totals, s, prices)
        kind = s.kind.value
        row.by_kind_tokens[kind] = row.by_kind_tokens.get(kind, 0) + session_tokens(s)[3]
        if s.kind is StatsSessionKind.REVIEW_PR_COMMENTS:
            row.review_sessions += 1
        if s.pr_number is not None and s.pr_number not in row.pr_numbers:
            row.pr_numbers.append(s.pr_number)
    for k, row in tasks.items():
        for pr in pr_by_task.get(k, []):
            if pr.number not in row.pr_numbers:
                row.pr_numbers.append(pr.number)
            row.merged = row.merged or pr.merged_at is not None
        row.pr_numbers.sort()
    report.top_tasks = sorted(
        tasks.values(), key=lambda r: (r.totals.cost_usd, r.totals.total_tokens), reverse=True
    )[:TOP_TASKS]

    # --- pull requests ----------------------------------------------------
    flow = PRFlowSummary()
    lead_all: list[float] = []
    lead_wade: list[float] = []
    lead_other: list[float] = []
    changed: list[float] = []
    for pr in prs:
        flow.opened += 1
        if pr.wade_managed:
            flow.wade_opened += 1
        if pr.merged_at is not None:
            flow.merged += 1
            if pr.wade_managed:
                flow.wade_merged += 1
            if (lt := pr.lead_time_h) is not None:
                lead_all.append(lt)
                (lead_wade if pr.wade_managed else lead_other).append(lt)
        elif pr.state.upper() == "CLOSED" or pr.closed_at is not None:
            flow.closed_unmerged += 1
        else:
            flow.open += 1
        if pr.additions is not None or pr.deletions is not None:
            changed.append(float((pr.additions or 0) + (pr.deletions or 0)))
    flow.median_lead_time_h = _median(lead_all)
    flow.wade_median_lead_time_h = _median(lead_wade)
    flow.other_median_lead_time_h = _median(lead_other)
    flow.median_changed_lines = _median(changed)

    # --- tasks (from `wade stats migrate`) -----------------------------------
    for task in task_records or []:
        report.tasks_created += 1
        if task.state.upper() == "CLOSED":
            report.tasks_closed += 1
    created_by_task = {
        (t.project.label, t.task_id): t.created_at for t in (all_task_records or task_records or [])
    }
    cycle: list[float] = []
    for pr in prs:
        if pr.merged_at is None or not pr.task_id:
            continue
        created = created_by_task.get((pr.project.label, pr.task_id))
        if created is not None and created <= pr.merged_at:
            cycle.append((pr.merged_at - created).total_seconds() / 3600)
    flow.median_cycle_time_h = _median(cycle)

    merged_rows = [r for r in tasks.values() if r.merged]
    if merged_rows:
        flow.tokens_per_merged_pr = sum(r.totals.total_tokens for r in merged_rows) / len(
            merged_rows
        )
        flow.cost_per_merged_pr = sum(r.totals.cost_usd for r in merged_rows) / len(merged_rows)
    with_pr = [r for r in tasks.values() if r.pr_numbers]
    if with_pr:
        flow.avg_review_sessions_per_pr = sum(r.review_sessions for r in with_pr) / len(with_pr)
    report.prs = flow
    return report

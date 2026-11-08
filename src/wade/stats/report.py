"""Aggregate stored sessions and PRs into a :class:`StatsReport` (pure)."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable

from wade.models.stats import (
    AISessionRecord,
    BreakdownRow,
    DailyPoint,
    ModelUsageRow,
    PRFlowSummary,
    PullRequestRecord,
    ReworkRow,
    StatsFilter,
    StatsOutcome,
    StatsReport,
    StatsSessionKind,
    StatsSource,
    TaskRecord,
    TaskRow,
    UsageTotals,
)
from wade.stats.pricing import PriceBook

TOP_TASKS = 15
REVIEW_HEAVY_ROUNDS = 5
_FINISHED = {StatsOutcome.SUCCESS, StatsOutcome.FAILED, StatsOutcome.TIMEOUT}
_FAILED = {StatsOutcome.FAILED, StatsOutcome.TIMEOUT}

# Tools whose reported *input* already includes cache reads (Copilot prints
# "53.7k in, 86 out, 33.8k cached", where the cached part is inside "in").
# Every other supported tool reports cache reads alongside fresh input
# (Codex: "input=5,213 (+ 18,304 cached)"). Normalized at read time, so stored
# rows stay exactly as recorded and dedupe fingerprints keep matching.
INPUT_INCLUDES_CACHED = frozenset({"copilot"})

UNKNOWN_MODEL = "unknown"


def clean_model_name(name: str | None) -> str | None:
    """Drop names that cannot be a model (old transcripts produced `1`, `20`…)."""
    if name is None:
        return None
    name = name.strip()
    return name if re.search(r"[A-Za-z]", name) else None


def normalize_session(s: AISessionRecord) -> AISessionRecord:
    """Read-time view with disjoint input/cached counts and sane model names."""
    inclusive = (s.ai_tool or "").lower() in INPUT_INCLUDES_CACHED
    models = []
    for m in s.models:
        name = clean_model_name(m.model) or UNKNOWN_MODEL
        inp = max(0, m.input_tokens - m.cached_tokens) if inclusive else m.input_tokens
        models.append(m.model_copy(update={"model": name, "input_tokens": inp}))
    merged: dict[str, ModelUsageRow] = {}
    for m in models:
        if m.model in merged:
            prev = merged[m.model]
            prev.input_tokens += m.input_tokens
            prev.output_tokens += m.output_tokens
            prev.cached_tokens += m.cached_tokens
            prev.premium_requests += m.premium_requests
        else:
            merged[m.model] = m
    update: dict[str, object] = {
        "models": list(merged.values()),
        "model": clean_model_name(s.model),
    }
    if inclusive and s.input_tokens is not None:
        update["input_tokens"] = max(0, s.input_tokens - (s.cached_tokens or 0))
    return s.model_copy(update=update)


def _components(s: AISessionRecord) -> tuple[int | None, int | None, int | None]:
    inp, out, cac = s.input_tokens, s.output_tokens, s.cached_tokens
    if s.models:
        inp = inp if inp is not None else sum(m.input_tokens for m in s.models)
        out = out if out is not None else sum(m.output_tokens for m in s.models)
        cac = cac if cac is not None else sum(m.cached_tokens for m in s.models)
    return inp, out, cac


def session_tokens(s: AISessionRecord) -> tuple[int, int, int, int]:
    """(input, output, cached, total) — total is always the sum of the parts.

    A tool's own "total" is ignored whenever any component is known: Codex's
    excludes cache reads while Copilot's and Claude's include them, so trusting
    it would make totals disagree with their own parts. It is only the fallback
    for rows that recorded nothing else.
    """
    inp, out, cac = _components(s)
    if inp is None and out is None and cac is None:
        return 0, 0, 0, s.total_tokens or 0
    inp, out, cac = inp or 0, out or 0, cac or 0
    return inp, out, cac, inp + out + cac


def session_fresh_tokens(s: AISessionRecord) -> int:
    """Input + output (no cache reads); a bare reported total when nothing else is known."""
    inp, out, cac = _components(s)
    if inp is None and out is None:
        return 0 if cac is not None else (s.total_tokens or 0)
    return (inp or 0) + (out or 0)


def session_cost(s: AISessionRecord, prices: PriceBook) -> tuple[float, int, int, dict[str, int]]:
    """(usd, priced_tokens, priced_fresh_tokens, {unpriced_model: fresh_tokens})."""
    unpriced: dict[str, int] = {}
    day = s.started_at.date()  # scheduled price changes apply by session date
    if s.models:
        usd = 0.0
        priced = priced_fresh = 0
        for m in s.models:
            c = prices.cost(m.model, m.input_tokens, m.output_tokens, m.cached_tokens, on=day)
            if c is None:
                if m.input_tokens + m.output_tokens + m.cached_tokens:
                    unpriced[m.model] = unpriced.get(m.model, 0) + m.input_tokens + m.output_tokens
                continue
            usd += c
            priced += m.input_tokens + m.output_tokens + m.cached_tokens
            priced_fresh += m.input_tokens + m.output_tokens
        return usd, priced, priced_fresh, unpriced
    inp, out, cac, total = session_tokens(s)
    model = s.effective_model
    c = prices.cost(model, inp, out, cac, on=day)
    if c is None:
        if total:
            unpriced[model or UNKNOWN_MODEL] = session_fresh_tokens(s)
        return 0.0, 0, 0, unpriced
    return c, inp + out + cac, inp + out, unpriced


def _add(t: UsageTotals, s: AISessionRecord, prices: PriceBook) -> dict[str, int]:
    inp, out, cac, total = session_tokens(s)
    usd, priced, priced_fresh, unpriced = session_cost(s, prices)
    t.sessions += 1
    if s.source is StatsSource.LOCAL:
        t.local_sessions += 1
    if s.outcome in _FINISHED:
        t.finished += 1
    if s.outcome in _FAILED:
        t.failures += 1
    t.input_tokens += inp
    t.output_tokens += out
    t.cached_tokens += cac
    if cac:
        t.cache_prompt_tokens += inp + cac
    t.total_tokens += total
    t.fresh_tokens += session_fresh_tokens(s)
    t.premium_requests += s.premium_requests or 0
    t.cost_usd += usd
    t.priced_tokens += priced
    t.priced_fresh_tokens += priced_fresh
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
    rows.sort(key=lambda r: (r.totals.fresh_tokens, r.totals.sessions), reverse=True)
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
    rows.sort(key=lambda r: (r.totals.fresh_tokens, r.totals.sessions), reverse=True)
    return rows


def _daily(sessions: list[AISessionRecord], prices: PriceBook) -> list[DailyPoint]:
    days: dict[str, DailyPoint] = {}
    for s in sessions:
        day = s.started_at.strftime("%Y-%m-%d")
        point = days.setdefault(day, DailyPoint(day=day))
        tool = s.ai_tool or "unknown"
        point.tokens[tool] = point.tokens.get(tool, 0) + session_fresh_tokens(s)
        point.sessions += 1
        point.cost_usd += session_cost(s, prices)[0]
    return [days[d] for d in sorted(days)]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _pr_key(project: str, task_id: str | None) -> tuple[str, str] | None:
    return (project, task_id) if task_id else None


def _implementing_model(impl: list[AISessionRecord], label: str | None) -> str | None:
    """Model that did most of a task's implementation (by fresh tokens)."""
    weight: dict[str, int] = defaultdict(int)
    for s in impl:
        if s.models:
            for m in s.models:
                if m.model != UNKNOWN_MODEL:
                    weight[m.model] += m.input_tokens + m.output_tokens
        elif s.model:
            weight[s.model] += session_fresh_tokens(s)
    if weight:
        return max(weight, key=lambda k: (weight[k], k))
    return clean_model_name(label)


def _size_bucket(size: int, cuts: list[float]) -> int:
    return sum(1 for c in cuts if size > c)


def _rework_by_model(
    sessions: list[AISessionRecord], prices: PriceBook, task_records: list[TaskRecord]
) -> list[ReworkRow]:
    """Attribute each task's PR-comment review rounds to its implementing model.

    Also accumulates each model's *size-expected* rounds: the mean rounds of all
    tasks in the same implementation-size quartile, so models that mostly get
    small (or large) tasks are compared fairly.
    """
    labels = {(t.project.label, t.task_id): t.implemented_model for t in task_records}
    per_task: dict[tuple[str, str], list[AISessionRecord]] = defaultdict(list)
    for s in sessions:
        if s.task_id:
            per_task[(s.project.label, s.task_id)].append(s)
    facts: list[tuple[str, list[AISessionRecord], list[AISessionRecord], int]] = []
    for key, group in per_task.items():
        impl = [s for s in group if s.kind is StatsSessionKind.IMPLEMENT]
        if not impl:
            continue  # rework is only meaningful relative to an implementation
        model = _implementing_model(impl, labels.get(key))
        if model is None:
            continue
        review = [s for s in group if s.kind is StatsSessionKind.REVIEW_PR_COMMENTS]
        facts.append((model, impl, review, sum(session_fresh_tokens(s) for s in impl)))

    cuts: list[float] = []
    if len(facts) >= 8:
        cuts = statistics.quantiles([f[3] for f in facts], n=4)
    bucket_rounds: dict[int, list[int]] = defaultdict(list)
    for _model, _impl, review, size in facts:
        bucket_rounds[_size_bucket(size, cuts)].append(len(review))
    bucket_mean = {b: sum(v) / len(v) for b, v in bucket_rounds.items()}

    rows: dict[str, ReworkRow] = {}
    for model, impl, review, size in facts:
        row = rows.setdefault(model, ReworkRow(model=model))
        row.tasks += 1
        row.review_rounds += len(review)
        row.expected_rounds += bucket_mean[_size_bucket(size, cuts)]
        row.tasks_reviewed += 1 if review else 0
        row.heavy_tasks += 1 if len(review) >= REVIEW_HEAVY_ROUNDS else 0
        row.implement_fresh_tokens += size
        row.review_fresh_tokens += sum(session_fresh_tokens(s) for s in review)
        row.implement_cost_usd += sum(session_cost(s, prices)[0] for s in impl)
        row.review_cost_usd += sum(session_cost(s, prices)[0] for s in review)
    return sorted(rows.values(), key=lambda r: (r.tasks, r.review_rounds), reverse=True)


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
    sessions = [normalize_session(s) for s in sessions]
    unpriced: dict[str, int] = defaultdict(int)
    for s in sessions:
        for name, fresh in _add(report.totals, s, prices).items():
            unpriced[name] += fresh
    # Biggest gaps first: those are the prices worth adding.
    report.unpriced_models = sorted(unpriced, key=lambda n: (-unpriced[n], n))
    if previous is not None:
        report.previous_totals = totals_for([normalize_session(s) for s in previous], prices)

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
    by_kind: dict[str, list[AISessionRecord]] = defaultdict(list)
    for s in sessions:
        by_kind[s.kind.value].append(s)
    report.by_kind_model = [
        BreakdownRow(key=f"{kind}|{row.key}", totals=row.totals)
        for kind, group in by_kind.items()
        for row in _model_breakdown(group, prices)
    ]
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
        row.by_kind_tokens[kind] = row.by_kind_tokens.get(kind, 0) + session_fresh_tokens(s)
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
        tasks.values(), key=lambda r: (r.totals.cost_usd, r.totals.fresh_tokens), reverse=True
    )[:TOP_TASKS]
    report.review_heavy_tasks = sorted(
        (r for r in tasks.values() if r.review_sessions >= REVIEW_HEAVY_ROUNDS),
        key=lambda r: (r.review_sessions, r.totals.cost_usd),
        reverse=True,
    )
    report.rework_by_model = _rework_by_model(
        sessions, prices, all_task_records or task_records or []
    )

    # --- pull requests ----------------------------------------------------
    flow = PRFlowSummary()
    lead_all: list[float] = []
    lead_wade: list[float] = []
    lead_other: list[float] = []
    changed: list[float] = []
    changed_wade: list[float] = []
    changed_other: list[float] = []
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
            size = float((pr.additions or 0) + (pr.deletions or 0))
            changed.append(size)
            if pr.merged_at is not None:
                (changed_wade if pr.wade_managed else changed_other).append(size)
    flow.median_lead_time_h = _median(lead_all)
    flow.wade_median_lead_time_h = _median(lead_wade)
    flow.other_median_lead_time_h = _median(lead_other)
    flow.median_changed_lines = _median(changed)
    flow.wade_median_changed_lines = _median(changed_wade)
    flow.other_median_changed_lines = _median(changed_other)

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
        flow.tokens_per_merged_pr = sum(r.totals.fresh_tokens for r in merged_rows) / len(
            merged_rows
        )
        flow.cost_per_merged_pr = sum(r.totals.cost_usd for r in merged_rows) / len(merged_rows)
    with_pr = [r for r in tasks.values() if r.pr_numbers]
    if with_pr:
        flow.avg_review_sessions_per_pr = sum(r.review_sessions for r in with_pr) / len(with_pr)
    report.prs = flow
    return report

"""Turn a :class:`StatsReport` into short, data-backed observations (pure).

Each rule fires only with enough data behind it (minimum session counts), so a
fresh database yields few insights rather than noisy ones. Rules are ordered by
how actionable they usually are.
"""

from __future__ import annotations

from wade.models.stats import Insight, InsightLevel, StatsReport

MIN_SESSIONS = 3


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _tokens(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


def _hours(h: float) -> str:
    return f"{h / 24:.1f} days" if h >= 48 else f"{h:.1f} h"


def build_insights(report: StatsReport) -> list[Insight]:
    out: list[Insight] = []
    t = report.totals
    if t.sessions == 0:
        return [
            Insight(
                title="No sessions recorded yet",
                detail=(
                    "Run `wade plan`, `wade implement` or `wade review` to start collecting, "
                    "or `wade stats sync` to backfill history from existing PR bodies."
                ),
            )
        ]

    # 1. Trend vs the previous window of equal length.
    prev = report.previous_totals
    if prev is not None and prev.fresh_tokens > 0 and t.fresh_tokens > 0:
        change = (t.fresh_tokens - prev.fresh_tokens) / prev.fresh_tokens
        if change >= 0.5:
            out.append(
                Insight(
                    level=InsightLevel.WARNING,
                    title=f"Fresh token usage up {_pct(change)} vs the previous period",
                    detail=(
                        f"{_tokens(t.fresh_tokens)} fresh tokens (input + output) now vs "
                        f"{_tokens(prev.fresh_tokens)} before, over {t.sessions} vs "
                        f"{prev.sessions} sessions."
                    ),
                )
            )
        elif change <= -0.2:
            out.append(
                Insight(
                    level=InsightLevel.GOOD,
                    title=f"Fresh token usage down {_pct(-change)} vs the previous period",
                    detail=(
                        f"{_tokens(t.fresh_tokens)} fresh tokens (input + output) now vs "
                        f"{_tokens(prev.fresh_tokens)} before."
                    ),
                )
            )

    # 2. Review rework: PR-comment sessions costing more than implementation.
    kinds = {r.key: r.totals for r in report.by_kind}
    impl = kinds.get("implement")
    review = kinds.get("review_pr_comments")
    if impl and review and impl.fresh_tokens and review.sessions >= MIN_SESSIONS:
        ratio = review.fresh_tokens / impl.fresh_tokens
        if ratio >= 0.5:
            out.append(
                Insight(
                    level=InsightLevel.WARNING if ratio >= 1 else InsightLevel.INFO,
                    title=f"Review rework is {ratio:.1f}x implementation spend",
                    detail=(
                        f"Addressing PR comments used {_tokens(review.fresh_tokens)} fresh tokens "
                        f"across {review.sessions} sessions vs {_tokens(impl.fresh_tokens)} for "
                        "implementation. Tighter plans or a stronger closing review usually "
                        "pay off here."
                    ),
                )
            )
    if (avg := report.prs.avg_review_sessions_per_pr) is not None and avg >= 1.5:
        out.append(
            Insight(
                level=InsightLevel.WARNING,
                title=f"{avg:.1f} review-comment rounds per PR on average",
                detail="Each round re-reads the diff; recurring feedback is worth a skill or "
                "knowledge entry.",
            )
        )

    # 3. Failure rate per tool.
    for row in report.by_tool:
        rate = row.totals.failure_rate
        if row.totals.finished >= MIN_SESSIONS and rate is not None and rate >= 0.25:
            out.append(
                Insight(
                    level=InsightLevel.WARNING,
                    title=f"{row.key} sessions fail {_pct(rate)} of the time",
                    detail=(
                        f"{row.totals.failures} of {row.totals.finished} finished sessions "
                        "exited non-zero or timed out."
                    ),
                )
            )

    # 4. Cache efficiency, best vs worst tool.
    cached = [
        r
        for r in report.by_tool
        if r.totals.sessions >= MIN_SESSIONS and r.totals.cache_ratio is not None
    ]
    if len(cached) >= 2:
        best = max(cached, key=lambda r: r.totals.cache_ratio or 0)
        worst = min(cached, key=lambda r: r.totals.cache_ratio or 0)
        if (best.totals.cache_ratio or 0) - (worst.totals.cache_ratio or 0) >= 0.2:
            out.append(
                Insight(
                    title=f"{best.key} reuses its prompt cache far more than {worst.key}",
                    detail=(
                        f"Cache hit share {_pct(best.totals.cache_ratio or 0)} vs "
                        f"{_pct(worst.totals.cache_ratio or 0)} of prompt tokens."
                    ),
                )
            )
    elif t.cache_ratio is not None and t.sessions >= MIN_SESSIONS:
        out.append(
            Insight(
                level=InsightLevel.GOOD if t.cache_ratio >= 0.5 else InsightLevel.INFO,
                title=f"{_pct(t.cache_ratio)} of prompt tokens came from cache",
                detail="Cached tokens are billed at a fraction of fresh input.",
            )
        )

    # 5. Model cost efficiency for implementation sessions.
    priced_models = [
        r
        for r in report.by_model
        if r.totals.sessions >= MIN_SESSIONS and r.totals.cost_usd > 0 and r.key != "unknown"
    ]
    if len(priced_models) >= 2:
        per = sorted(priced_models, key=lambda r: r.totals.cost_usd / r.totals.sessions)
        cheap, pricey = per[0], per[-1]
        c_cost = cheap.totals.cost_usd / cheap.totals.sessions
        p_cost = pricey.totals.cost_usd / pricey.totals.sessions
        if c_cost > 0 and p_cost / c_cost >= 2:
            out.append(
                Insight(
                    title=f"{pricey.key} costs {p_cost / c_cost:.1f}x more per session than "
                    f"{cheap.key}",
                    detail=f"≈${p_cost:.2f} vs ≈${c_cost:.2f} per session (estimated).",
                )
            )

    # 6. Concentration: one task dominating spend.
    if report.top_tasks and t.fresh_tokens:
        top = report.top_tasks[0]
        share = top.totals.fresh_tokens / t.fresh_tokens
        if share >= 0.25 and len(report.top_tasks) >= 3:
            out.append(
                Insight(
                    title=f"Task {top.task_id} alone used {_pct(share)} of all fresh tokens",
                    detail=(
                        f"{_tokens(top.totals.fresh_tokens)} fresh tokens over "
                        f"{top.totals.sessions} sessions — large tasks are cheaper when split."
                    ),
                )
            )

    # 7. Lead time, WADE vs other PRs.
    flow = report.prs
    if flow.wade_median_lead_time_h is not None and flow.other_median_lead_time_h:
        ratio = flow.wade_median_lead_time_h / flow.other_median_lead_time_h
        level = InsightLevel.GOOD if ratio < 1 else InsightLevel.INFO
        out.append(
            Insight(
                level=level,
                title=(
                    f"WADE PRs merge in {_hours(flow.wade_median_lead_time_h)} (median) vs "
                    f"{_hours(flow.other_median_lead_time_h)} for other PRs"
                ),
                detail=f"{flow.wade_merged} WADE and {flow.merged - flow.wade_merged} other "
                "merged PRs in this window.",
            )
        )
    elif flow.median_lead_time_h is not None:
        out.append(
            Insight(
                title=f"Median PR lead time {_hours(flow.median_lead_time_h)}",
                detail=f"From PR creation to merge, over {flow.merged} merged PRs.",
            )
        )
    if flow.cost_per_merged_pr:
        out.append(
            Insight(
                title=f"≈${flow.cost_per_merged_pr:.2f} of AI spend per merged task",
                detail=(
                    f"≈{_tokens(flow.tokens_per_merged_pr or 0)} fresh tokens across plan, "
                    "implement and review sessions (estimated)."
                ),
            )
        )

    # 8. Data-quality notes: cache reporting gaps and the price table.
    silent = [
        r.key
        for r in report.by_tool
        if r.totals.sessions >= MIN_SESSIONS
        and r.totals.input_tokens
        and not r.totals.cached_tokens
    ]
    if silent and len(silent) < len(report.by_tool):
        names = ", ".join(silent)
        out.append(
            Insight(
                title=f"No cache reads reported for {names}",
                detail=(
                    "Their transcripts didn't include cache usage, so cache share shows — and "
                    "their totals and costs exclude cache reads. Compare tools by fresh tokens."
                ),
            )
        )
    # 8. Data-quality note: costs are only as complete as the price table.
    if (cov := t.price_coverage) is not None and cov < 0.5:
        names = ", ".join(report.unpriced_models[:4])
        out.append(
            Insight(
                title=f"Only {_pct(cov)} of tokens have a known price",
                detail=(
                    f"Add prices for {names} to ~/.config/wade/pricing.yml to include them "
                    "in cost estimates."
                ),
            )
        )
    return out

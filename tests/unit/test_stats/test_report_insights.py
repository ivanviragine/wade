"""Report aggregation and insight rules (pure)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from wade.models.stats import (
    AISessionRecord,
    InsightLevel,
    ModelUsageRow,
    ProjectRef,
    PullRequestRecord,
    StatsOutcome,
    StatsReport,
    StatsSessionKind,
    TaskRecord,
    utc_now,
)
from wade.stats.insights import build_insights
from wade.stats.pricing import PriceBook
from wade.stats.report import build_report, normalize_session, session_cost, session_tokens

P = ProjectRef(root="/r", slug="o/r")
NOW = utc_now()


def _s(
    sid: str,
    kind: StatsSessionKind = StatsSessionKind.IMPLEMENT,
    tool: str = "claude",
    model: str | None = "claude-opus-5.5",
    inp: int = 1000,
    out: int = 100,
    cac: int = 0,
    task: str | None = "1",
    outcome: StatsOutcome = StatsOutcome.SUCCESS,
    **kw: object,
) -> AISessionRecord:
    return AISessionRecord.model_validate(
        {
            "id": sid,
            "project": P,
            "kind": kind,
            "ai_tool": tool,
            "model": model,
            "input_tokens": inp,
            "output_tokens": out,
            "cached_tokens": cac,
            "task_id": task,
            "outcome": outcome,
            "started_at": NOW - timedelta(hours=1),
            "ended_at": NOW,
            **kw,
        }
    )


def test_session_tokens_fall_back_to_breakdown() -> None:
    s = _s("a", inp=None, out=None, cac=None, models=[ModelUsageRow(model="m", input_tokens=5)])  # type: ignore[arg-type]
    assert session_tokens(s) == (5, 0, 0, 5)


def test_cost_prefers_breakdown_and_reports_unpriced() -> None:
    s = _s(
        "a",
        models=[
            ModelUsageRow(model="claude-opus-5.5", input_tokens=1_000_000),
            ModelUsageRow(model="mystery", input_tokens=10),
        ],
    )
    usd, priced, priced_fresh, unpriced = session_cost(s, PriceBook())
    assert usd == 4.0 and priced == priced_fresh == 1_000_000
    assert unpriced == {"mystery": 10}


def test_build_report_breakdowns_tasks_and_prs() -> None:
    sessions = [
        _s("p", kind=StatsSessionKind.PLAN, inp=500),
        _s("i", cac=3000),
        _s(
            "r1", kind=StatsSessionKind.REVIEW_PR_COMMENTS, tool="codex", model="gpt-5", pr_number=9
        ),
        _s("r2", kind=StatsSessionKind.REVIEW_PR_COMMENTS, tool="codex", model="gpt-5"),
        _s("f", tool="codex", model="gpt-5", outcome=StatsOutcome.FAILED, task="2"),
        _s("d", kind=StatsSessionKind.DELEGATION, operation="plan_review", inp=0, out=0),
    ]
    prs = [
        PullRequestRecord(
            project=P,
            number=9,
            task_id="1",
            wade_managed=True,
            state="MERGED",
            created_at=NOW - timedelta(hours=10),
            merged_at=NOW,
            additions=10,
            deletions=5,
        ),
        PullRequestRecord(
            project=P,
            number=10,
            state="MERGED",
            created_at=NOW - timedelta(hours=30),
            merged_at=NOW,
        ),
        PullRequestRecord(project=P, number=11, state="OPEN", created_at=NOW),
    ]
    tasks = [
        TaskRecord(project=P, task_id="1", state="CLOSED", created_at=NOW - timedelta(hours=20))
    ]
    report = build_report(sessions, prs, PriceBook(), task_records=tasks)

    assert report.totals.sessions == 6
    assert report.totals.failures == 1 and report.totals.finished == 6
    assert {r.key for r in report.by_kind} == {
        "plan",
        "implement",
        "review_pr_comments",
        "delegation:plan_review",
    }
    assert report.by_tool[0].key == "codex"  # ranked by fresh tokens, not cache reads
    assert report.unpriced_models == []  # gpt-5 and the Claude models are all priced

    top = {t.task_id: t for t in report.top_tasks}
    assert top["1"].review_sessions == 2 and top["1"].merged and top["1"].pr_numbers == [9]
    assert top["1"].by_kind_tokens["plan"] == 600

    flow = report.prs
    assert (flow.opened, flow.merged, flow.open, flow.wade_merged) == (3, 2, 1, 1)
    assert flow.wade_median_lead_time_h == 10 and flow.other_median_lead_time_h == 30
    assert flow.median_cycle_time_h == 20
    assert flow.avg_review_sessions_per_pr == 2
    assert report.tasks_created == 1 and report.tasks_closed == 1


def test_insights_empty_database() -> None:
    [only] = build_insights(StatsReport())
    assert "No sessions" in only.title


def test_insights_fire_on_rework_failures_and_trend() -> None:
    sessions = [_s(f"i{n}", task=str(n)) for n in range(3)]
    sessions += [
        _s(f"r{n}", kind=StatsSessionKind.REVIEW_PR_COMMENTS, inp=5000, task=str(n))
        for n in range(3)
    ]
    sessions += [
        _s(f"x{n}", tool="codex", model=None, outcome=StatsOutcome.FAILED, task=None)
        for n in range(3)
    ]
    previous = [_s("old", inp=100, out=10)]
    report = build_report(sessions, [], PriceBook(), previous=previous)
    titles = [i.title for i in build_insights(report)]
    assert any("Fresh token usage up" in t for t in titles)
    assert any("Review rework" in t for t in titles)
    assert any(t.startswith("codex sessions fail 100%") for t in titles)
    levels = {i.level for i in build_insights(report)}
    assert InsightLevel.WARNING in levels


def test_total_is_sum_of_parts_even_when_tool_reports_otherwise() -> None:
    # Codex reports total=input+output (excluding cache reads); Copilot includes them.
    codex = _s("c", tool="codex", inp=100, out=50, cac=10_000, total_tokens=150)
    assert session_tokens(codex) == (100, 50, 10_000, 10_150)
    bare = _s("b", inp=None, out=None, cac=None, total_tokens=777)  # type: ignore[arg-type]
    assert session_tokens(bare) == (0, 0, 0, 777)
    report = build_report([codex, bare], [], PriceBook())
    t = report.totals
    assert t.total_tokens == t.input_tokens + t.output_tokens + t.cached_tokens + 777
    assert t.fresh_tokens == 150 + 777


def test_cache_share_unknown_when_tool_reports_no_cache_reads() -> None:
    sessions = [_s(f"a{n}", tool="claude", cac=0) for n in range(3)]
    sessions += [_s(f"b{n}", tool="codex", model=None, cac=50_000) for n in range(3)]
    report = build_report(sessions, [], PriceBook())
    by_tool = {r.key: r.totals for r in report.by_tool}
    assert by_tool["claude"].cache_ratio is None
    assert by_tool["codex"].cache_ratio is not None and by_tool["codex"].cache_ratio > 0.9
    titles = [i.title for i in build_insights(report)]
    assert "No cache reads reported for claude" in titles
    assert not any("reuses its prompt cache" in t for t in titles)  # no bogus 97% vs 0%


def test_breakdowns_rank_by_fresh_tokens() -> None:
    heavy_cache = _s("h", tool="codex", model=None, inp=10, out=10, cac=1_000_000)
    heavy_work = _s("w", tool="claude", inp=50_000, out=5_000)
    report = build_report([heavy_cache, heavy_work], [], PriceBook())
    assert [r.key for r in report.by_tool] == ["claude", "codex"]
    assert report.totals.local_sessions == 2


def test_copilot_input_includes_cache_reads() -> None:
    # Copilot prints "53.7k in, 86 out, 33.8k cached" with cached inside "in".
    raw = _s(
        "c",
        tool="copilot",
        model=None,
        inp=53_700,
        out=86,
        cac=33_800,
        models=[
            ModelUsageRow(
                model="claude-opus-4.6", input_tokens=53_700, output_tokens=86, cached_tokens=33_800
            )
        ],
    )
    s = normalize_session(raw)
    assert s.input_tokens == 19_900 and s.models[0].input_tokens == 19_900
    report = build_report([raw], [], PriceBook())
    assert report.totals.fresh_tokens == 19_986
    assert report.totals.cache_ratio == pytest.approx(33_800 / 53_700)
    usd, *_ = session_cost(s, PriceBook())
    assert usd == pytest.approx((19_900 * 5 + 86 * 25 + 33_800 * 0.5) / 1e6)


def test_stray_cache_rows_do_not_make_a_cache_share() -> None:
    sessions = [_s(f"a{n}", tool="claude", cac=0) for n in range(5)]
    sessions.append(_s("stray", tool="claude", inp=10, cac=219))
    by_tool = {r.key: r.totals for r in build_report(sessions, [], PriceBook()).by_tool}
    assert by_tool["claude"].cache_ratio is None  # 219 cached tokens is not evidence


def test_numeric_model_names_become_unknown() -> None:
    junk = _s(
        "j",
        model=None,
        models=[ModelUsageRow(model="1", input_tokens=2), ModelUsageRow(model="3", input_tokens=4)],
    )
    report = build_report([junk], [], PriceBook())
    assert [r.key for r in report.by_model] == ["unknown"]
    assert report.unpriced_models == ["unknown"]
    assert not any("Add prices" in i.detail for i in build_insights(report))


def test_price_coverage_uses_fresh_tokens_and_ranks_unpriced_by_volume() -> None:
    sessions = [
        _s("p", model="claude-opus-5.5", inp=1000, out=0, cac=0),
        _s("big", tool="codex", model="mystery-big", inp=5000, out=0, cac=900_000),
        _s("small", tool="codex", model="mystery-small", inp=100, out=0, cac=0),
    ]
    report = build_report(sessions, [], PriceBook())
    assert report.totals.price_coverage == pytest.approx(1000 / 6100)
    assert report.unpriced_models == ["mystery-big", "mystery-small"]


def test_model_cost_insight_needs_enough_sessions() -> None:
    few = [_s(f"o{n}", model="claude-opus-4.1", inp=900_000) for n in range(3)]
    few += [_s(f"s{n}", model="claude-sonnet-5", inp=1000) for n in range(3)]
    titles = [i.title for i in build_insights(build_report(few, [], PriceBook()))]
    assert not any("more per session" in t for t in titles)


def test_model_cost_compared_only_within_a_workflow() -> None:
    # Cheap haiku planning vs expensive opus implementation: not comparable.
    sessions = [
        _s(f"p{n}", kind=StatsSessionKind.PLAN, model="claude-haiku-4.5") for n in range(10)
    ]
    sessions += [_s(f"i{n}", model="claude-opus-4.1", inp=500_000) for n in range(10)]
    titles = [i.title for i in build_insights(build_report(sessions, [], PriceBook()))]
    assert not any("costs" in t and "more" in t for t in titles)

    # Same workflow, two models with enough sessions: comparable.
    sessions += [_s(f"j{n}", model="claude-sonnet-5", inp=50_000) for n in range(10)]
    titles = [i.title for i in build_insights(build_report(sessions, [], PriceBook()))]
    assert any(t.startswith("In implementation sessions, claude-opus-4.1 costs") for t in titles), (
        titles
    )


def test_cache_comparison_needs_enough_sessions_per_tool() -> None:
    sessions = [_s(f"c{n}", tool="codex", model=None, cac=90_000) for n in range(10)]
    sessions += [_s(f"g{n}", tool="gemini", model=None, cac=1_000) for n in range(3)]
    titles = [i.title for i in build_insights(build_report(sessions, [], PriceBook()))]
    assert not any("reuses its prompt cache" in t for t in titles)


def test_pr_lead_time_insight_flags_size_mismatch() -> None:
    def pr(n: int, wade: bool, hours: float, size: int) -> PullRequestRecord:
        return PullRequestRecord(
            project=P,
            number=n,
            wade_managed=wade,
            state="MERGED",
            created_at=NOW - timedelta(hours=hours),
            merged_at=NOW,
            additions=size,
            deletions=0,
        )

    prs = [pr(1, True, 4, 400), pr(2, True, 5, 300), pr(3, False, 0.5, 2), pr(4, False, 0.4, 3)]
    report = build_report([_s("a")], prs, PriceBook())
    [lead] = [i for i in build_insights(report) if i.title.startswith("WADE PRs merge")]
    assert lead.title.endswith("which are much smaller")
    assert "aren't comparable" in lead.detail and "350 vs 2" in lead.detail

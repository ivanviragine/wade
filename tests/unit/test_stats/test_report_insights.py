"""Report aggregation and insight rules (pure)."""

from __future__ import annotations

from datetime import timedelta

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
from wade.stats.report import build_report, session_cost, session_tokens

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
    usd, priced, unpriced = session_cost(s, PriceBook())
    assert usd == 4.0 and priced == 1_000_000 and unpriced == {"mystery"}


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
    assert report.by_tool[0].key == "claude"
    assert "gpt-5" in report.unpriced_models

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
    assert any("Token usage up" in t for t in titles)
    assert any("Review rework" in t for t in titles)
    assert any(t.startswith("codex sessions fail 100%") for t in titles)
    levels = {i.level for i in build_insights(report)}
    assert InsightLevel.WARNING in levels

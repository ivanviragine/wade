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


def test_no_per_session_model_cost_comparisons() -> None:
    # Big models get big jobs; per-session cost ratios are confounded by size.
    sessions = [_s(f"h{n}", model="claude-haiku-4.5", inp=1_000) for n in range(10)]
    sessions += [_s(f"o{n}", model="claude-opus-4.1", inp=900_000) for n in range(10)]
    titles = [i.title for i in build_insights(build_report(sessions, [], PriceBook()))]
    assert not any("more per session" in t or ("costs" in t and "x more" in t) for t in titles)


def test_spend_concentration_by_model() -> None:
    sessions = [_s(f"o{n}", model="claude-opus-4.1", inp=900_000) for n in range(3)]
    sessions += [_s(f"s{n}", model="claude-sonnet-5", inp=50_000) for n in range(3)]
    sessions += [_s(f"h{n}", model="claude-haiku-4.5", inp=1_000) for n in range(3)]
    insights = build_insights(build_report(sessions, [], PriceBook()))
    [conc] = [i for i in insights if "of estimated spend" in i.title]
    assert conc.title.startswith("claude-opus-4.1 and claude-sonnet-5 account for")


def test_review_heavy_tasks_are_flagged() -> None:
    sessions = [
        _s(f"r{n}", kind=StatsSessionKind.REVIEW_PR_COMMENTS, task="515") for n in range(16)
    ]
    sessions += [_s("i", task="515"), _s("r", kind=StatsSessionKind.REVIEW_PR_COMMENTS, task="9")]
    report = build_report(sessions, [], PriceBook())
    assert [t.task_id for t in report.review_heavy_tasks] == ["515"]
    [heavy] = [i for i in build_insights(report) if "review rounds" in i.title]
    assert heavy.title == "1 task needed 5+ PR-comment review rounds"
    assert heavy.detail.startswith("515 (16 rounds,")


def test_rework_attributed_to_implementing_model() -> None:
    def task(tid: str, model: str, rounds: int) -> list[AISessionRecord]:
        out = [_s(f"i{tid}", task=tid, model=model, inp=10_000)]
        out += [
            _s(
                f"r{tid}{n}",
                kind=StatsSessionKind.REVIEW_PR_COMMENTS,
                task=tid,
                model="gpt-5",
                inp=500,
            )
            for n in range(rounds)
        ]
        return out

    sessions: list[AISessionRecord] = []
    for n in range(10):
        sessions += task(f"a{n}", "claude-opus-4.8", 3)
        sessions += task(f"b{n}", "claude-sonnet-5", 0 if n < 8 else 1)
    # Implementation without a recorded model falls back to the implemented-model label.
    sessions.append(_s("ix", task="x", model=None, inp=100))
    labels = [TaskRecord(project=P, task_id="x", implemented_model="claude-haiku-4.5")]
    report = build_report(sessions, [], PriceBook(), all_task_records=labels)
    rows = {r.model: r for r in report.rework_by_model}
    assert rows["claude-opus-4.8"].rounds_per_task == 3
    assert rows["claude-opus-4.8"].rework_ratio == pytest.approx(3 * 600 / 10_100)
    assert rows["claude-sonnet-5"].tasks == 10 and rows["claude-sonnet-5"].tasks_reviewed == 2
    assert rows["claude-haiku-4.5"].tasks == 1  # rework is keyed by implementer, not reviewer
    assert "gpt-5" not in rows
    titles = [i.title for i in build_insights(report)]
    # One size bucket holds all 21 tasks, so the norm is 32 rounds / 21 tasks.
    norm = 32 / 21
    assert rows["claude-opus-4.8"].rework_index == pytest.approx(30 / (10 * norm))
    assert rows["claude-sonnet-5"].rework_index == pytest.approx(2 / (10 * norm))
    assert any(t.startswith("Tasks implemented by claude-opus-4.8 needed 2.0x") for t in titles)


def test_rework_index_adjusts_for_task_size() -> None:
    # "small" model only ever gets small tasks, "big" only large ones; both match
    # the norm for their size, so neither should be flagged.
    sessions: list[AISessionRecord] = []
    for n in range(8):
        sessions.append(_s(f"si{n}", task=f"s{n}", model="claude-haiku-4.5", inp=1_000))
        sessions.append(
            _s(f"sr{n}", kind=StatsSessionKind.REVIEW_PR_COMMENTS, task=f"s{n}", inp=10)
        )
        sessions.append(_s(f"bi{n}", task=f"b{n}", model="claude-opus-4.8", inp=900_000))
        sessions += [
            _s(f"br{n}{k}", kind=StatsSessionKind.REVIEW_PR_COMMENTS, task=f"b{n}", inp=10)
            for k in range(4)
        ]
    rows = {r.model: r for r in build_report(sessions, [], PriceBook()).rework_by_model}
    assert (
        rows["claude-opus-4.8"].rounds_per_task == 4
        and rows["claude-haiku-4.5"].rounds_per_task == 1
    )
    assert rows["claude-opus-4.8"].rework_index == pytest.approx(1.0)
    assert rows["claude-haiku-4.5"].rework_index == pytest.approx(1.0)


def test_task_rows_carry_pr_and_task_links() -> None:
    sessions = [_s("a", task="1", pr_number=9), _s("b", task="1", pr_number=12)]
    prs = [
        PullRequestRecord(
            project=P,
            number=9,
            task_id="1",
            url="https://github.example.com/o/r/pull/9",  # GitHub's own URL wins
            state="MERGED",
            created_at=NOW,
            merged_at=NOW,
        )
    ]
    tasks = [TaskRecord(project=P, task_id="1", url="https://github.com/o/r/issues/1")]
    [row] = build_report(sessions, prs, PriceBook(), all_task_records=tasks).top_tasks
    assert row.pr_numbers == [9, 12]
    assert row.pr_urls == {
        9: "https://github.example.com/o/r/pull/9",
        12: "https://github.com/o/r/pull/12",  # no record: built from the owner/repo slug
    }
    assert row.task_url == "https://github.com/o/r/issues/1"


def test_no_guessed_links_without_a_repo_slug() -> None:
    local = ProjectRef(root="/no/remote")
    [row] = build_report([_s("a", task="1", pr_number=3, project=local)], [], PriceBook()).top_tasks
    assert row.pr_urls == {} and row.task_url is None


def test_cli_task_table_renders_hyperlinks() -> None:
    from wade.models.stats import TaskRow
    from wade.ui.stats_view import _pr_links

    text = _pr_links(
        TaskRow(task_id="1", project="o/r", pr_numbers=[4, 5], pr_urls={4: "https://x/4"})
    )
    assert text.plain == "#4, #5"
    links = [span.style.link for span in text.spans if hasattr(span.style, "link")]
    assert links == ["https://x/4"]  # #5 has no URL and stays plain


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TERM_PROGRAM": "iTerm.app"}, True),
        ({"TERM_PROGRAM": "vscode"}, True),
        ({"TERM_PROGRAM": "Apple_Terminal"}, False),
        ({"TERM": "xterm-kitty"}, True),
        ({"VTE_VERSION": "7600"}, True),
        ({"WT_SESSION": "abc"}, True),
        ({}, False),
        ({"TERM_PROGRAM": "Apple_Terminal", "WADE_HYPERLINKS": "1"}, True),
        ({"TERM_PROGRAM": "iTerm.app", "WADE_HYPERLINKS": "0"}, False),
    ],
)
def test_hyperlink_detection(env: dict[str, str], expected: bool) -> None:
    from wade.ui.stats_view import hyperlinks_supported

    assert hyperlinks_supported(env) is expected


def test_plain_url_fallback_lines() -> None:
    from wade.models.stats import TaskRow
    from wade.ui.stats_view import _pr_links, task_link_lines

    rows = [
        TaskRow(task_id="515", project="o/r", pr_numbers=[516], pr_urls={516: "https://x/516"}),
        TaskRow(task_id="9", project="/local", task_url="https://x/issues/9"),
        TaskRow(task_id="7", project="/local"),
    ]
    assert task_link_lines(rows) == ["   515  #516 https://x/516", "     9  https://x/issues/9"]
    plain = _pr_links(rows[0], hyperlink=False)
    assert plain.plain == "#516" and not any(getattr(sp.style, "link", None) for sp in plain.spans)

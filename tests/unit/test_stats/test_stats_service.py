"""Stats service: recording, storage modes, migration, delegations, export."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.models.ai import ModelBreakdown, TokenUsage

from wade.config.loader import parse_config_file
from wade.git.pr import PRLookup, PRRef
from wade.models.config import ProjectConfig, StatsConfig, StatsStorage
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.stats import (
    ProjectRef,
    PullRequestSnapshot,
    StatsEventKind,
    StatsFilter,
    StatsOutcome,
    StatsSessionKind,
    StatsSource,
)
from wade.models.task import Label, Task, TaskState
from wade.services import stats_service
from wade.services.implementation_service.core import _capture_post_session_usage
from wade.services.implementation_service.usage_tracking import (
    append_impl_usage_entry,
    append_review_usage_entry,
)
from wade.services.task_service import build_plan_summary_block
from wade.stats.store import StatsStore

PROJECT = ProjectRef(root="/work/repo", slug="acme/repo")


@pytest.fixture(autouse=True)
def _fixed_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats_service, "resolve_project", lambda _path: PROJECT)


def _usage(inp: int = 1000, out: int = 200, cac: int = 3000) -> TokenUsage:
    return TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        cached_tokens=cac,
        session_id="sess-1",
        model_breakdown=[
            ModelBreakdown(
                model="claude-opus-5.5", input_tokens=inp, output_tokens=out, cached_tokens=cac
            )
        ],
    )


def _sessions(db: Path) -> list:  # type: ignore[type-arg]
    with StatsStore(db) as store:
        return store.fetch_sessions()


# ---------------------------------------------------------------------------
# enablement + recording
# ---------------------------------------------------------------------------


def test_disabled_by_env_records_nothing(tmp_path: Path) -> None:
    # The suite-wide fixture sets WADE_STATS_DISABLE=1.
    tracker = stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude"
    )
    assert not tracker.active
    tracker.finish(exit_code=0)  # safe no-op
    assert not (tmp_path / "stats.db").exists()


@pytest.mark.parametrize(
    ("storage", "local", "github"),
    [("both", True, True), ("github", False, True), ("local", True, False), ("off", False, False)],
)
def test_storage_modes(stats_db: Path, storage: str, local: bool, github: bool) -> None:
    config = ProjectConfig(stats=StatsConfig(storage=StatsStorage(storage)))
    assert stats_service.stats_enabled(config) is local
    assert stats_service.github_usage_enabled(config) is github


def test_config_loader_parses_storage(tmp_path: Path) -> None:
    cfg = tmp_path / ".wade.yml"
    cfg.write_text("version: 2\nstats:\n  storage: local\n")
    assert parse_config_file(cfg).stats.storage is StatsStorage.LOCAL
    cfg.write_text("version: 2\n")
    assert parse_config_file(cfg).stats.storage is StatsStorage.BOTH


def test_begin_finish_records_usage(stats_db: Path, tmp_path: Path) -> None:
    tracker = stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT,
        cwd=tmp_path,
        ai_tool="claude",
        task_id="42",
        branch="feat/42-x",
        launch_mode="inline",
    )
    [running] = _sessions(stats_db)
    assert running.outcome is StatsOutcome.RUNNING  # visible even if wade crashes now
    tracker.set_usage(_usage())
    tracker.set_pr(7)
    tracker.finish(exit_code=0)
    tracker.finish(exit_code=1)  # idempotent
    [rec] = _sessions(stats_db)
    assert rec.outcome is StatsOutcome.SUCCESS and rec.exit_code == 0
    assert (rec.input_tokens, rec.cached_tokens, rec.total_tokens) == (1000, 3000, 4200)
    assert rec.model == "claude-opus-5.5" and rec.pr_number == 7
    assert rec.tool_session_id == "sess-1"
    assert rec.wade_version


def test_detached_and_failed_outcomes(stats_db: Path, tmp_path: Path) -> None:
    stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude"
    ).detached()
    stats_service.begin_session(
        kind=StatsSessionKind.REVIEW_PR_COMMENTS, cwd=tmp_path, ai_tool="codex"
    ).finish(exit_code=3)
    outcomes = sorted(s.outcome.value for s in _sessions(stats_db))
    assert outcomes == ["detached", "failed"]


def test_launch_outcome() -> None:
    assert stats_service.launch_outcome(True, 0) is None
    assert stats_service.launch_outcome(False, None) is StatsOutcome.FAILED
    assert stats_service.launch_outcome(False, 0) is StatsOutcome.UNKNOWN
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        assert stats_service.launch_outcome(False, None) is StatsOutcome.CANCELLED


def test_record_event(stats_db: Path, tmp_path: Path) -> None:
    stats_service.record_event(StatsEventKind.PR_MERGED, cwd=tmp_path, pr_number=5, task_id="1")
    with StatsStore(stats_db) as store:
        [ev] = store.fetch_events()
    assert ev.kind is StatsEventKind.PR_MERGED and ev.pr_number == 5


def test_recording_never_raises(stats_db: Path, tmp_path: Path) -> None:
    with patch.object(stats_service, "StatsStore", side_effect=OSError("disk full")):
        tracker = stats_service.begin_session(
            kind=StatsSessionKind.PLAN, cwd=tmp_path, ai_tool="claude"
        )
        stats_service.record_event(StatsEventKind.PR_READY, cwd=tmp_path)
    assert not tracker.active


def test_link_plan_tasks(stats_db: Path, tmp_path: Path) -> None:
    tracker = stats_service.begin_session(
        kind=StatsSessionKind.PLAN, cwd=tmp_path, ai_tool="claude"
    )
    tracker.finish(exit_code=0)
    stats_service.link_plan_tasks(["11", "12"], cwd=tmp_path)
    [rec] = _sessions(stats_db)
    assert rec.task_id == "11"
    with StatsStore(stats_db) as store:
        events = store.fetch_events()
    assert [e.task_id for e in events] == ["11", "12"]
    assert all(e.data["plan_session"] == rec.id for e in events)


def test_record_delegation(stats_db: Path, tmp_path: Path) -> None:
    req = DelegationRequest(
        mode=DelegationMode.HEADLESS,
        prompt="x",
        ai_tool="codex",
        cwd=tmp_path,
        operation="the implementation review",
        knowledge_task_ids=("9",),
    )
    started = datetime.now(UTC) - timedelta(minutes=2)
    stats_service.record_delegation(
        req, DelegationResult(success=False, feedback="", mode=req.mode, timed_out=True), started
    )
    stats_service.record_delegation(
        req,
        DelegationResult(success=False, feedback="", mode=req.mode, never_launched=True),
        started,
    )
    [rec] = _sessions(stats_db)
    assert rec.kind is StatsSessionKind.DELEGATION
    assert rec.operation == "implementation_review"
    assert rec.outcome is StatsOutcome.TIMEOUT and rec.task_id == "9"
    assert rec.duration_s is not None and rec.duration_s >= 100


# ---------------------------------------------------------------------------
# PR/issue body writers honour stats.storage
# ---------------------------------------------------------------------------


def _capture(tmp_path: Path, *, github_usage: bool, tracker: object | None = None) -> tuple:  # type: ignore[type-arg]
    transcript = tmp_path / "t.log"
    transcript.write_text("x")
    adapter = MagicMock()
    adapter.parse_transcript.return_value = _usage()
    provider = MagicMock()
    provider.read_task.return_value = Task(id="42", title="t", body="Issue\n")
    with (
        patch(
            "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
            return_value=PRLookup(found=True, pr=PRRef(number=7, state="OPEN")),
        ),
        patch("wade.services.implementation_service.core.git_pr.get_pr_body", return_value="PR\n"),
        patch(
            "wade.services.implementation_service.core.git_pr.update_pr_body", return_value=True
        ) as update_pr,
        patch("wade.services.implementation_service.core.console"),
    ):
        _capture_post_session_usage(
            transcript_path=transcript,
            adapter=adapter,
            repo_root=tmp_path,
            branch="feat/42-x",
            ai_tool="claude",
            model=None,
            issue_number="42",
            provider=provider,
            stats=tracker,  # type: ignore[arg-type]
            github_usage=github_usage,
        )
    return update_pr.call_args.args[2], provider.update_task.call_args.kwargs["body"]


def test_local_storage_keeps_usage_out_of_bodies(stats_db: Path, tmp_path: Path) -> None:
    tracker = stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude", task_id="42"
    )
    pr_body, issue_body = _capture(tmp_path, github_usage=False, tracker=tracker)
    for body in (pr_body, issue_body):
        assert "wade:impl-usage:start" not in body
        assert "sess-1" in body  # resume row is functional and always written
    tracker.finish(exit_code=0)
    [rec] = _sessions(stats_db)
    assert rec.total_tokens == 4200 and rec.pr_number == 7


def test_github_storage_writes_usage_tables(tmp_path: Path) -> None:
    pr_body, issue_body = _capture(tmp_path, github_usage=True)
    assert "wade:impl-usage:start" in pr_body and "wade:impl-usage:start" in issue_body


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _github_fixture() -> tuple[list[PullRequestSnapshot], list[Task]]:
    impl = TokenUsage(input_tokens=1000, output_tokens=200, cached_tokens=3000)
    review = TokenUsage(input_tokens=400, output_tokens=50)
    pr_body = append_impl_usage_entry("PR", ai_tool="claude", token_usage=impl)
    pr_body = append_review_usage_entry(pr_body, ai_tool="codex", token_usage=review)
    plan = build_plan_summary_block(
        ai_tool="claude", total_tokens=900, input_tokens=800, output_tokens=100
    )
    prs = [
        PullRequestSnapshot(
            number=7,
            title="feat: x",
            head_ref="feat/42-x",
            state="MERGED",
            created_at=T0,
            merged_at=T0 + timedelta(hours=5),
            additions=10,
            deletions=2,
            body=pr_body,
        ),
        PullRequestSnapshot(
            number=8, title="chore", head_ref="chore/y", state="OPEN", created_at=T0
        ),
    ]
    tasks = [
        # Issue 42 carries copies of the PR's usage + the planning block.
        Task(
            id="42",
            title="x",
            state=TaskState.CLOSED,
            body="Plan\n\n" + plan + "\n\n" + pr_body,
            labels=[Label(name="planned-by:claude"), Label(name="implemented-model:opus")],
            created_at=T0 - timedelta(hours=10),
        ),
        # Sibling issue from the same planning session: identical plan block.
        Task(id="43", title="y", body=plan, created_at=T0 - timedelta(hours=9)),
        Task(id="44", title="unrelated", body="plain"),
    ]
    return prs, tasks


def _migrate(db: Path, prs: list[PullRequestSnapshot], tasks: list[Task], **kw: object):  # type: ignore[no-untyped-def]
    provider = MagicMock()
    provider.list_tasks.return_value = tasks
    with patch("wade.git.pr.list_prs_for_stats", return_value=prs):
        return stats_service.migrate_from_github(
            Path("/work/repo"), provider=provider, branch_prefix="feat", db_path=db, **kw
        )


def test_migrate_imports_and_dedupes(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    prs, tasks = _github_fixture()
    result = _migrate(db, prs, tasks)
    assert (result.prs_seen, result.wade_prs, result.tasks_seen, result.wade_tasks) == (2, 1, 3, 2)
    # impl + review from the PR, one plan; issue copies and sibling plan are dupes.
    assert result.sessions_imported == 3
    assert result.sessions_skipped == 3

    with StatsStore(db) as store:
        sessions = store.fetch_sessions()
        pr_rows = store.fetch_pull_requests()
        task_rows = {t.task_id: t for t in store.fetch_tasks()}
    kinds = sorted((s.kind.value, s.source.value, s.task_id) for s in sessions)
    assert kinds == [
        ("implement", "pr_body", "42"),
        ("plan", "issue_body", "42"),
        ("review_pr_comments", "pr_body", "42"),
    ]
    assert {p.number: p.wade_managed for p in pr_rows} == {7: True, 8: False}
    assert task_rows["42"].planned_by == "claude"
    assert task_rows["42"].implemented_model == "opus"
    assert not task_rows["44"].wade_managed

    again = _migrate(db, prs, tasks)  # idempotent
    assert again.sessions_imported == 0
    assert len(_sessions(db)) == 3


def test_migrate_skips_sessions_recorded_locally(stats_db: Path, tmp_path: Path) -> None:
    prs, tasks = _github_fixture()
    tracker = stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude", task_id="42"
    )
    tracker.set_usage(TokenUsage(input_tokens=1000, output_tokens=200, cached_tokens=3000))
    tracker.finish(exit_code=0)
    result = _migrate(stats_db, prs, tasks)
    assert result.sessions_imported == 2  # review + plan only
    sources = sorted(s.source for s in _sessions(stats_db))
    assert sources.count(StatsSource.LOCAL) == 1


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    prs, tasks = _github_fixture()
    result = _migrate(db, prs, tasks, dry_run=True)
    assert result.dry_run and result.sessions_imported == 3
    with StatsStore(db) as store:
        assert store.counts()["ai_sessions"] == 0 and store.counts()["pull_requests"] == 0


def test_migrate_reports_listing_errors(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.list_tasks.side_effect = RuntimeError("no auth")
    with patch("wade.git.pr.list_prs_for_stats", side_effect=RuntimeError("gh missing")):
        result = stats_service.migrate_from_github(
            Path("/work/repo"), provider=provider, db_path=tmp_path / "m.db"
        )
    assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# reporting helpers
# ---------------------------------------------------------------------------


def test_parse_time_bound() -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert stats_service.parse_time_bound("7d", now=now) == now - timedelta(days=7)
    assert stats_service.parse_time_bound("12h", now=now) == now - timedelta(hours=12)
    assert stats_service.parse_time_bound("all") is None
    assert stats_service.parse_time_bound("2026-01-31") == datetime(2026, 1, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="Unrecognized"):
        stats_service.parse_time_bound("yesterday")


def test_report_and_export(stats_db: Path, tmp_path: Path) -> None:
    t = stats_service.begin_session(kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude")
    t.set_usage(_usage())
    t.finish(exit_code=0)
    report = stats_service.build_stats_report(
        StatsFilter(since=datetime.now(UTC) - timedelta(days=1))
    )
    assert report.totals.sessions == 1 and report.totals.cost_usd > 0
    assert report.previous_totals is not None and report.previous_totals.sessions == 0

    csv_text = stats_service.export_sessions(StatsFilter(), "csv")
    header, row = csv_text.strip().splitlines()
    assert header.startswith("id,project,kind")
    assert "claude-opus-5.5" in row
    rows = json.loads(stats_service.export_sessions(StatsFilter(), "json"))
    assert rows[0]["total_tokens"] == 4200
    with pytest.raises(ValueError):
        stats_service.export_sessions(StatsFilter(), "xml")

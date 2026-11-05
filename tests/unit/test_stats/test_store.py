"""Stats SQLite store: schema, round-trips, filters, dedupe, pruning."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from wade.models.stats import (
    AISessionRecord,
    ModelUsageRow,
    ProjectRef,
    PullRequestRecord,
    StatsEvent,
    StatsEventKind,
    StatsFilter,
    StatsOutcome,
    StatsSessionKind,
    TaskRecord,
    utc_now,
)
from wade.stats.store import SCHEMA_VERSION, StatsStore, default_db_path, from_db_ts, to_db_ts

API = ProjectRef(root="/work/api", slug="acme/api")
WEB = ProjectRef(root="/work/web", slug="acme/web")


def _session(sid: str, **kw: object) -> AISessionRecord:
    base: dict[str, object] = {
        "id": sid,
        "project": API,
        "kind": StatsSessionKind.IMPLEMENT,
        "ai_tool": "claude",
        "model": "claude-opus-5.5",
        "task_id": "42",
    }
    base.update(kw)
    return AISessionRecord.model_validate(base)


@pytest.fixture
def store(tmp_path: Path) -> StatsStore:
    s = StatsStore(tmp_path / "s.db")
    yield s  # type: ignore[misc]
    s.close()


def test_default_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WADE_STATS_DB", str(tmp_path / "x.db"))
    assert default_db_path() == tmp_path / "x.db"
    monkeypatch.delenv("WADE_STATS_DB")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_db_path() == tmp_path / "data" / "wade" / "stats.db"


def test_timestamp_codec_round_trips() -> None:
    now = utc_now()
    assert from_db_ts(to_db_ts(now)) == now
    assert from_db_ts(None) is None


def test_schema_version_and_newer_db_refused(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    StatsStore(path).close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.DatabaseError, match="upgrade wade"):
        StatsStore(path)


def test_session_round_trip_with_models_and_finish(store: StatsStore) -> None:
    rec = _session("a", models=[ModelUsageRow(model="m", input_tokens=10)])
    assert store.insert_session(rec)
    rec.ended_at = rec.started_at + timedelta(minutes=5)
    rec.outcome = StatsOutcome.SUCCESS
    rec.exit_code = 0
    rec.total_tokens = 99
    rec.models = [
        ModelUsageRow(model="m", input_tokens=10, output_tokens=2),
        ModelUsageRow(model="m", input_tokens=1),  # same model merges
    ]
    store.finish_session(rec)
    [got] = store.fetch_sessions()
    assert got.outcome is StatsOutcome.SUCCESS
    assert got.duration_s == 300
    assert got.total_tokens == 99
    assert got.models == [ModelUsageRow(model="m", input_tokens=11, output_tokens=2)]
    assert got.project == API


def test_dedupe_key_prevents_double_insert(store: StatsStore) -> None:
    assert store.insert_session(_session("a", dedupe_key="k"))
    assert not store.insert_session(_session("b", dedupe_key="k"))
    assert len(store.fetch_sessions()) == 1


def test_filters(store: StatsStore) -> None:
    now = utc_now()
    store.insert_session(_session("old", started_at=now - timedelta(days=40)))
    store.insert_session(_session("new", started_at=now - timedelta(days=1)))
    store.insert_session(_session("web", project=WEB, ai_tool="codex", model=None))
    store.insert_session(
        _session(
            "plan",
            kind=StatsSessionKind.PLAN,
            model=None,
            models=[ModelUsageRow(model="claude-haiku-5.5", input_tokens=5)],
        )
    )

    def ids(**kw: object) -> set[str]:
        return {s.id for s in store.fetch_sessions(StatsFilter.model_validate(kw))}

    assert ids(since=now - timedelta(days=7)) == {"new", "web", "plan"}
    assert ids(projects=["acme/web"]) == {"web"}
    assert ids(projects=["/work/api"]) == {"old", "new", "plan"}
    assert ids(tools=["codex"]) == {"web"}
    assert ids(kinds=["plan"]) == {"plan"}
    assert ids(models=["claude-haiku-5.5"]) == {"plan"}  # matched via breakdown
    assert ids(task_id="42") == {"old", "new", "web", "plan"}


def test_events_prs_tasks_and_counts(store: StatsStore) -> None:
    now = utc_now()
    store.insert_event(StatsEvent(kind=StatsEventKind.PR_CREATED, project=API, pr_number=7))
    [ev] = store.fetch_events()
    assert ev.kind is StatsEventKind.PR_CREATED and ev.pr_number == 7

    pr = PullRequestRecord(project=API, number=7, state="OPEN", created_at=now, task_id="42")
    store.upsert_pull_request(pr, now)
    store.upsert_pull_request(
        pr.model_copy(update={"state": "MERGED", "merged_at": now, "task_id": None}), now
    )
    [got] = store.fetch_pull_requests()
    assert got.state == "MERGED" and got.task_id == "42"  # task kept on refresh

    store.upsert_task(TaskRecord(project=API, task_id="42", labels=["planned-by:claude"]), now)
    [task] = store.fetch_tasks()
    assert task.labels == ["planned-by:claude"]
    counts = store.counts()
    assert counts["events"] == 1 and counts["pull_requests"] == 1 and counts["tasks"] == 1

    opts = store.distinct_values()
    assert opts["projects"] == ["acme/api"]


def test_set_session_task_only_fills_missing(store: StatsStore) -> None:
    store.insert_session(_session("p", task_id=None))
    store.insert_session(_session("q", task_id="1"))
    store.set_session_task("p", "9")
    store.set_session_task("q", "9")
    got = {s.id: s.task_id for s in store.fetch_sessions()}
    assert got == {"p": "9", "q": "1"}


def test_prune(store: StatsStore) -> None:
    now = utc_now()
    store.insert_session(_session("old", started_at=now - timedelta(days=400)))
    store.insert_session(_session("new"))
    assert store.prune_before(now - timedelta(days=365)) == 1
    assert [s.id for s in store.fetch_sessions()] == ["new"]

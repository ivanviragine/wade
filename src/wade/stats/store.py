"""SQLite persistence for local usage statistics.

One database per user (not per project): worktrees are created and removed
constantly, and history must outlive them. Rows carry the project they belong
to, so every query can be sliced per project.

Location (first match wins):

1. ``$WADE_STATS_DB`` — explicit file path;
2. ``$XDG_DATA_HOME/wade/stats.db``;
3. ``~/.local/share/wade/stats.db``.

Concurrency: several WADE processes (one per terminal) may write at once, so
the database runs in WAL mode with a busy timeout, and every write is a short
transaction. The schema is versioned with ``PRAGMA user_version``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    StatsSource,
    TaskRecord,
)

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    root        TEXT NOT NULL UNIQUE,
    slug        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_sessions (
    id               TEXT PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    kind             TEXT NOT NULL,
    operation        TEXT,
    source           TEXT NOT NULL,
    task_id          TEXT,
    branch           TEXT,
    worktree         TEXT,
    pr_number        INTEGER,
    ai_tool          TEXT,
    model            TEXT,
    effort           TEXT,
    permission_mode  TEXT,
    launch_mode      TEXT,
    resumed          INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    exit_code        INTEGER,
    outcome          TEXT NOT NULL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    cached_tokens    INTEGER,
    total_tokens     INTEGER,
    premium_requests INTEGER,
    tool_session_id  TEXT,
    wade_version     TEXT,
    dedupe_key       TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_sessions_started ON ai_sessions(started_at);
CREATE INDEX IF NOT EXISTS ix_sessions_project ON ai_sessions(project_id, started_at);
CREATE INDEX IF NOT EXISTS ix_sessions_task ON ai_sessions(project_id, task_id);

CREATE TABLE IF NOT EXISTS ai_session_models (
    session_id       TEXT NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
    model            TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cached_tokens    INTEGER NOT NULL DEFAULT 0,
    premium_requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, model)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    task_id     TEXT,
    pr_number   INTEGER,
    branch      TEXT,
    data        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS pull_requests (
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    number        INTEGER NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    branch        TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT '',
    is_draft      INTEGER NOT NULL DEFAULT 0,
    task_id       TEXT,
    wade_managed  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    merged_at     TEXT,
    closed_at     TEXT,
    additions     INTEGER,
    deletions     INTEGER,
    changed_files INTEGER,
    synced_at     TEXT NOT NULL,
    PRIMARY KEY (project_id, number)
);
CREATE INDEX IF NOT EXISTS ix_prs_created ON pull_requests(created_at);

CREATE TABLE IF NOT EXISTS tasks (
    project_id             INTEGER NOT NULL REFERENCES projects(id),
    task_id                TEXT NOT NULL,
    title                  TEXT NOT NULL DEFAULT '',
    url                    TEXT,
    state                  TEXT NOT NULL DEFAULT '',
    created_at             TEXT,
    updated_at             TEXT,
    labels                 TEXT NOT NULL DEFAULT '[]',
    planned_by             TEXT,
    planned_model          TEXT,
    implemented_by         TEXT,
    implemented_model      TEXT,
    review_addressed_by    TEXT,
    review_addressed_model TEXT,
    wade_managed           INTEGER NOT NULL DEFAULT 0,
    synced_at              TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id)
);
CREATE INDEX IF NOT EXISTS ix_tasks_created ON tasks(created_at);
"""


def default_db_path() -> Path:
    """Resolve the per-user stats database location."""
    explicit = os.environ.get("WADE_STATS_DB")
    if explicit:
        return Path(explicit).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "wade" / "stats.db"


# ---------------------------------------------------------------------------
# Timestamp codec — ISO-8601 UTC with a ``Z`` suffix sorts lexicographically,
# so range filters are plain string comparisons and SQLite's date() still works.
# ---------------------------------------------------------------------------

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_db_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_TS_FORMAT)


def from_db_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=UTC)


class StatsStore:
    """Thin typed wrapper over the stats SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA foreign_keys = ON")
            # Some filesystems (network mounts) refuse WAL; rollback journal works.
            with suppress(sqlite3.OperationalError):
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StatsStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"stats database {self.path} has schema v{version}; this wade understands "
                f"up to v{SCHEMA_VERSION} — upgrade wade or point WADE_STATS_DB elsewhere"
            )
        if version < 1:
            with self._tx() as conn:
                for statement in _split_sql(_SCHEMA_V1):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- projects ---------------------------------------------------------

    def _project_id(self, conn: sqlite3.Connection, project: ProjectRef, ts: str) -> int:
        row = conn.execute(
            "SELECT id, slug FROM projects WHERE root = ?", (project.root,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO projects (root, slug, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (project.root, project.slug, ts, ts),
            )
            return int(cur.lastrowid or 0)
        conn.execute(
            "UPDATE projects SET last_seen = MAX(last_seen, ?), slug = COALESCE(?, slug) "
            "WHERE id = ?",
            (ts, project.slug, row["id"]),
        )
        return int(row["id"])

    def list_projects(self) -> list[ProjectRef]:
        rows = self._conn.execute("SELECT root, slug FROM projects ORDER BY last_seen DESC")
        return [ProjectRef(root=r["root"], slug=r["slug"]) for r in rows]

    # -- sessions ---------------------------------------------------------

    def insert_session(self, rec: AISessionRecord) -> bool:
        """Insert a session; returns False when its ``dedupe_key`` already exists."""
        with self._tx() as conn:
            if rec.dedupe_key is not None:
                hit = conn.execute(
                    "SELECT 1 FROM ai_sessions WHERE dedupe_key = ?", (rec.dedupe_key,)
                ).fetchone()
                if hit is not None:
                    return False
            pid = self._project_id(conn, rec.project, to_db_ts(rec.started_at))
            conn.execute(
                """
                INSERT INTO ai_sessions (
                    id, project_id, kind, operation, source, task_id, branch, worktree,
                    pr_number, ai_tool, model, effort, permission_mode, launch_mode, resumed,
                    started_at, ended_at, exit_code, outcome, input_tokens, output_tokens,
                    cached_tokens, total_tokens, premium_requests, tool_session_id,
                    wade_version, dedupe_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.id,
                    pid,
                    rec.kind.value,
                    rec.operation,
                    rec.source.value,
                    rec.task_id,
                    rec.branch,
                    rec.worktree,
                    rec.pr_number,
                    rec.ai_tool,
                    rec.model,
                    rec.effort,
                    rec.permission_mode,
                    rec.launch_mode,
                    int(rec.resumed),
                    to_db_ts(rec.started_at),
                    to_db_ts(rec.ended_at) if rec.ended_at else None,
                    rec.exit_code,
                    rec.outcome.value,
                    rec.input_tokens,
                    rec.output_tokens,
                    rec.cached_tokens,
                    rec.total_tokens,
                    rec.premium_requests,
                    rec.tool_session_id,
                    rec.wade_version,
                    rec.dedupe_key,
                ),
            )
            self._replace_models(conn, rec.id, rec.models)
        return True

    def finish_session(self, rec: AISessionRecord) -> None:
        """Persist the end-of-session fields of an already-inserted session."""
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE ai_sessions SET
                    ended_at = ?, exit_code = ?, outcome = ?, model = ?, pr_number = ?,
                    input_tokens = ?, output_tokens = ?, cached_tokens = ?, total_tokens = ?,
                    premium_requests = ?, tool_session_id = ?
                WHERE id = ?
                """,
                (
                    to_db_ts(rec.ended_at) if rec.ended_at else None,
                    rec.exit_code,
                    rec.outcome.value,
                    rec.model,
                    rec.pr_number,
                    rec.input_tokens,
                    rec.output_tokens,
                    rec.cached_tokens,
                    rec.total_tokens,
                    rec.premium_requests,
                    rec.tool_session_id,
                    rec.id,
                ),
            )
            self._replace_models(conn, rec.id, rec.models)

    @staticmethod
    def _replace_models(
        conn: sqlite3.Connection, session_id: str, models: Iterable[ModelUsageRow]
    ) -> None:
        conn.execute("DELETE FROM ai_session_models WHERE session_id = ?", (session_id,))
        merged: dict[str, ModelUsageRow] = {}
        for row in models:
            prev = merged.get(row.model)
            if prev is None:
                merged[row.model] = row.model_copy()
            else:
                prev.input_tokens += row.input_tokens
                prev.output_tokens += row.output_tokens
                prev.cached_tokens += row.cached_tokens
                prev.premium_requests += row.premium_requests
        for row in merged.values():
            conn.execute(
                "INSERT INTO ai_session_models (session_id, model, input_tokens, output_tokens, "
                "cached_tokens, premium_requests) VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    row.model,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_tokens,
                    row.premium_requests,
                ),
            )

    def fetch_sessions(self, flt: StatsFilter | None = None) -> list[AISessionRecord]:
        flt = flt or StatsFilter()
        where, params = _session_where(flt)
        rows = self._conn.execute(
            f"""
            SELECT s.*, p.root AS project_root, p.slug AS project_slug
            FROM ai_sessions s JOIN projects p ON p.id = s.project_id
            {where}
            ORDER BY s.started_at
            """,
            params,
        ).fetchall()
        by_id: dict[str, list[ModelUsageRow]] = {}
        if rows:
            ids = [r["id"] for r in rows]
            for chunk_start in range(0, len(ids), 500):
                chunk = ids[chunk_start : chunk_start + 500]
                marks = ",".join("?" * len(chunk))
                for m in self._conn.execute(
                    f"SELECT * FROM ai_session_models WHERE session_id IN ({marks})", chunk
                ):
                    by_id.setdefault(m["session_id"], []).append(
                        ModelUsageRow(
                            model=m["model"],
                            input_tokens=m["input_tokens"],
                            output_tokens=m["output_tokens"],
                            cached_tokens=m["cached_tokens"],
                            premium_requests=m["premium_requests"],
                        )
                    )
        sessions = [_row_to_session(r, by_id.get(r["id"], [])) for r in rows]
        if flt.models:
            wanted = set(flt.models)
            sessions = [
                s
                for s in sessions
                if s.effective_model in wanted or any(m.model in wanted for m in s.models)
            ]
        return sessions

    # -- events -----------------------------------------------------------

    def insert_event(self, event: StatsEvent) -> None:
        ts = to_db_ts(event.ts)
        with self._tx() as conn:
            pid = self._project_id(conn, event.project, ts)
            conn.execute(
                "INSERT INTO events (project_id, ts, kind, task_id, pr_number, branch, data) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    pid,
                    ts,
                    event.kind.value,
                    event.task_id,
                    event.pr_number,
                    event.branch,
                    json.dumps(event.data, sort_keys=True, default=str),
                ),
            )

    def fetch_events(self, flt: StatsFilter | None = None) -> list[StatsEvent]:
        flt = flt or StatsFilter()
        clauses, params = _common_clauses(flt, ts_col="e.ts")
        if flt.task_id:
            clauses.append("e.task_id = ?")
            params.append(flt.task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT e.*, p.root AS project_root, p.slug AS project_slug FROM events e "
            f"JOIN projects p ON p.id = e.project_id {where} ORDER BY e.ts",
            params,
        )
        events: list[StatsEvent] = []
        for r in rows:
            try:
                kind = StatsEventKind(r["kind"])
            except ValueError:
                continue
            events.append(
                StatsEvent(
                    kind=kind,
                    project=ProjectRef(root=r["project_root"], slug=r["project_slug"]),
                    ts=from_db_ts(r["ts"]) or datetime.now(UTC),
                    task_id=r["task_id"],
                    pr_number=r["pr_number"],
                    branch=r["branch"],
                    data=json.loads(r["data"] or "{}"),
                )
            )
        return events

    # -- pull requests ----------------------------------------------------

    def upsert_pull_request(self, pr: PullRequestRecord, synced_at: datetime) -> None:
        ts = to_db_ts(synced_at)
        with self._tx() as conn:
            pid = self._project_id(conn, pr.project, ts)
            conn.execute(
                """
                INSERT INTO pull_requests (
                    project_id, number, title, url, branch, state, is_draft, task_id,
                    wade_managed, created_at, merged_at, closed_at, additions, deletions,
                    changed_files, synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, number) DO UPDATE SET
                    title = excluded.title, url = excluded.url, branch = excluded.branch,
                    state = excluded.state, is_draft = excluded.is_draft,
                    task_id = COALESCE(excluded.task_id, pull_requests.task_id),
                    wade_managed = MAX(excluded.wade_managed, pull_requests.wade_managed),
                    created_at = excluded.created_at, merged_at = excluded.merged_at,
                    closed_at = excluded.closed_at, additions = excluded.additions,
                    deletions = excluded.deletions, changed_files = excluded.changed_files,
                    synced_at = excluded.synced_at
                """,
                (
                    pid,
                    pr.number,
                    pr.title,
                    pr.url,
                    pr.branch,
                    pr.state,
                    int(pr.is_draft),
                    pr.task_id,
                    int(pr.wade_managed),
                    to_db_ts(pr.created_at) if pr.created_at else None,
                    to_db_ts(pr.merged_at) if pr.merged_at else None,
                    to_db_ts(pr.closed_at) if pr.closed_at else None,
                    pr.additions,
                    pr.deletions,
                    pr.changed_files,
                    ts,
                ),
            )

    def fetch_pull_requests(self, flt: StatsFilter | None = None) -> list[PullRequestRecord]:
        flt = flt or StatsFilter()
        clauses, params = _common_clauses(flt, ts_col="pr.created_at")
        if flt.task_id:
            clauses.append("pr.task_id = ?")
            params.append(flt.task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT pr.*, p.root AS project_root, p.slug AS project_slug FROM pull_requests pr "
            f"JOIN projects p ON p.id = pr.project_id {where} ORDER BY pr.created_at",
            params,
        )
        return [
            PullRequestRecord(
                project=ProjectRef(root=r["project_root"], slug=r["project_slug"]),
                number=r["number"],
                title=r["title"],
                url=r["url"],
                branch=r["branch"],
                state=r["state"],
                is_draft=bool(r["is_draft"]),
                task_id=r["task_id"],
                wade_managed=bool(r["wade_managed"]),
                created_at=from_db_ts(r["created_at"]),
                merged_at=from_db_ts(r["merged_at"]),
                closed_at=from_db_ts(r["closed_at"]),
                additions=r["additions"],
                deletions=r["deletions"],
                changed_files=r["changed_files"],
            )
            for r in rows
        ]

    # -- tasks ------------------------------------------------------------

    def upsert_task(self, task: TaskRecord, synced_at: datetime) -> None:
        ts = to_db_ts(synced_at)
        with self._tx() as conn:
            pid = self._project_id(conn, task.project, ts)
            conn.execute(
                """
                INSERT INTO tasks (
                    project_id, task_id, title, url, state, created_at, updated_at, labels,
                    planned_by, planned_model, implemented_by, implemented_model,
                    review_addressed_by, review_addressed_model, wade_managed, synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, task_id) DO UPDATE SET
                    title = excluded.title, url = excluded.url, state = excluded.state,
                    created_at = COALESCE(excluded.created_at, tasks.created_at),
                    updated_at = excluded.updated_at, labels = excluded.labels,
                    planned_by = excluded.planned_by, planned_model = excluded.planned_model,
                    implemented_by = excluded.implemented_by,
                    implemented_model = excluded.implemented_model,
                    review_addressed_by = excluded.review_addressed_by,
                    review_addressed_model = excluded.review_addressed_model,
                    wade_managed = MAX(excluded.wade_managed, tasks.wade_managed),
                    synced_at = excluded.synced_at
                """,
                (
                    pid,
                    task.task_id,
                    task.title,
                    task.url,
                    task.state,
                    to_db_ts(task.created_at) if task.created_at else None,
                    to_db_ts(task.updated_at) if task.updated_at else None,
                    json.dumps(task.labels),
                    task.planned_by,
                    task.planned_model,
                    task.implemented_by,
                    task.implemented_model,
                    task.review_addressed_by,
                    task.review_addressed_model,
                    int(task.wade_managed),
                    ts,
                ),
            )

    def fetch_tasks(self, flt: StatsFilter | None = None) -> list[TaskRecord]:
        flt = flt or StatsFilter()
        clauses, params = _common_clauses(flt, ts_col="t.created_at")
        if flt.task_id:
            clauses.append("t.task_id = ?")
            params.append(flt.task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT t.*, p.root AS project_root, p.slug AS project_slug FROM tasks t "
            f"JOIN projects p ON p.id = t.project_id {where} ORDER BY t.created_at",
            params,
        )
        return [
            TaskRecord(
                project=ProjectRef(root=r["project_root"], slug=r["project_slug"]),
                task_id=r["task_id"],
                title=r["title"],
                url=r["url"],
                state=r["state"],
                created_at=from_db_ts(r["created_at"]),
                updated_at=from_db_ts(r["updated_at"]),
                labels=json.loads(r["labels"] or "[]"),
                planned_by=r["planned_by"],
                planned_model=r["planned_model"],
                implemented_by=r["implemented_by"],
                implemented_model=r["implemented_model"],
                review_addressed_by=r["review_addressed_by"],
                review_addressed_model=r["review_addressed_model"],
                wade_managed=bool(r["wade_managed"]),
            )
            for r in rows
        ]

    def set_session_task(self, session_id: str, task_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE ai_sessions SET task_id = COALESCE(task_id, ?) WHERE id = ?",
                (task_id, session_id),
            )

    # -- maintenance ------------------------------------------------------

    def distinct_values(self) -> dict[str, list[str]]:
        """Filter-option values for the dashboard dropdowns."""

        def col(sql: str) -> list[str]:
            return [str(r[0]) for r in self._conn.execute(sql) if r[0] not in (None, "")]

        return {
            "projects": col("SELECT COALESCE(slug, root) FROM projects ORDER BY last_seen DESC"),
            "tools": col("SELECT DISTINCT ai_tool FROM ai_sessions ORDER BY 1"),
            "models": sorted(
                set(col("SELECT DISTINCT model FROM ai_sessions"))
                | set(col("SELECT DISTINCT model FROM ai_session_models"))
            ),
            "kinds": col("SELECT DISTINCT kind FROM ai_sessions ORDER BY 1"),
        }

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("projects", "ai_sessions", "events", "pull_requests", "tasks"):
            out[table] = int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return out

    def prune_before(self, cutoff: datetime) -> int:
        """Delete sessions/events/PRs older than *cutoff*; returns sessions removed."""
        ts = to_db_ts(cutoff)
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM ai_sessions WHERE started_at < ?", (ts,))
            removed = cur.rowcount
            conn.execute("DELETE FROM events WHERE ts < ?", (ts,))
            conn.execute(
                "DELETE FROM pull_requests WHERE COALESCE(closed_at, created_at) < ?", (ts,)
            )
        return removed


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


def _common_clauses(flt: StatsFilter, *, ts_col: str) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if flt.since is not None:
        clauses.append(f"{ts_col} >= ?")
        params.append(to_db_ts(flt.since))
    if flt.until is not None:
        clauses.append(f"{ts_col} < ?")
        params.append(to_db_ts(flt.until))
    if flt.projects:
        marks = ",".join("?" * len(flt.projects))
        clauses.append(f"(p.slug IN ({marks}) OR p.root IN ({marks}))")
        params.extend(flt.projects)
        params.extend(flt.projects)
    return clauses, params


def _session_where(flt: StatsFilter) -> tuple[str, list[Any]]:
    clauses, params = _common_clauses(flt, ts_col="s.started_at")
    if flt.tools:
        clauses.append(f"s.ai_tool IN ({','.join('?' * len(flt.tools))})")
        params.extend(flt.tools)
    if flt.kinds:
        clauses.append(f"s.kind IN ({','.join('?' * len(flt.kinds))})")
        params.extend(flt.kinds)
    if flt.task_id:
        clauses.append("s.task_id = ?")
        params.append(flt.task_id)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _row_to_session(r: sqlite3.Row, models: list[ModelUsageRow]) -> AISessionRecord:
    def enum_or(enum_cls: Any, value: str, default: Any) -> Any:
        try:
            return enum_cls(value)
        except ValueError:
            return default

    return AISessionRecord(
        id=r["id"],
        project=ProjectRef(root=r["project_root"], slug=r["project_slug"]),
        kind=enum_or(StatsSessionKind, r["kind"], StatsSessionKind.DELEGATION),
        operation=r["operation"],
        source=enum_or(StatsSource, r["source"], StatsSource.LOCAL),
        task_id=r["task_id"],
        branch=r["branch"],
        worktree=r["worktree"],
        pr_number=r["pr_number"],
        ai_tool=r["ai_tool"],
        model=r["model"],
        effort=r["effort"],
        permission_mode=r["permission_mode"],
        launch_mode=r["launch_mode"],
        resumed=bool(r["resumed"]),
        started_at=from_db_ts(r["started_at"]) or datetime.now(UTC),
        ended_at=from_db_ts(r["ended_at"]),
        exit_code=r["exit_code"],
        outcome=enum_or(StatsOutcome, r["outcome"], StatsOutcome.UNKNOWN),
        input_tokens=r["input_tokens"],
        output_tokens=r["output_tokens"],
        cached_tokens=r["cached_tokens"],
        total_tokens=r["total_tokens"],
        premium_requests=r["premium_requests"],
        tool_session_id=r["tool_session_id"],
        wade_version=r["wade_version"],
        dedupe_key=r["dedupe_key"],
        models=models,
    )

"""Tests for database engine, tables, and repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghaiw.db.engine import create_db_engine, init_db
from ghaiw.db.repositories import (
    AuditLogRepository,
    SessionRepository,
    TaskRepository,
    TokenUsageRepository,
    WorktreeRepository,
)
from ghaiw.db.tables import SessionRecord, TaskRecord, TokenUsageRecord, WorktreeRecord


@pytest.fixture
def db_engine(tmp_path: Path):
    """Create a test database engine."""
    db_path = tmp_path / ".ghaiw" / "ghaiw.db"
    engine = create_db_engine(db_path)
    init_db(engine)
    return engine


class TestEngine:
    def test_create_engine(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ghaiw" / "ghaiw.db"
        engine = create_db_engine(db_path)
        assert db_path.parent.exists()
        init_db(engine)
        assert db_path.exists()

    def test_init_db_idempotent(self, db_engine) -> None:
        # Should not raise on second call
        init_db(db_engine)


class TestTaskRepository:
    def test_create_and_get(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        record = TaskRecord(
            id="42",
            provider="github",
            title="Test task",
            body="Task body",
        )
        created = repo.create(record)
        assert created.id == "42"

        fetched = repo.get("42")
        assert fetched is not None
        assert fetched.title == "Test task"

    def test_get_nonexistent(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        assert repo.get("999") is None

    def test_update(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        repo.create(TaskRecord(id="42", provider="github", title="Original"))

        updated = repo.update("42", title="Updated", state="closed")
        assert updated is not None
        assert updated.title == "Updated"
        assert updated.state == "closed"

    def test_get_by_state(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        repo.create(TaskRecord(id="1", provider="github", title="Open", state="open"))
        repo.create(TaskRecord(id="2", provider="github", title="Closed", state="closed"))
        repo.create(TaskRecord(id="3", provider="github", title="Also open", state="open"))

        open_tasks = repo.get_by_state("open")
        assert len(open_tasks) == 2

    def test_upsert_insert(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        record = TaskRecord(id="42", provider="github", title="New task")
        result = repo.upsert(record)
        assert result.id == "42"

    def test_upsert_update(self, db_engine) -> None:
        repo = TaskRepository(db_engine)
        repo.create(TaskRecord(id="42", provider="github", title="Original"))
        updated = repo.upsert(TaskRecord(id="42", provider="github", title="Updated"))
        assert updated.title == "Updated"


class TestSessionRepository:
    def test_create_and_get(self, db_engine) -> None:
        TaskRepository(db_engine).create(TaskRecord(id="42", provider="github", title="Task"))

        repo = SessionRepository(db_engine)
        record = SessionRecord(
            task_id="42",
            session_type="implementation",
            ai_tool="claude",
        )
        created = repo.create(record)
        assert created.id is not None
        assert created.task_id == "42"

    def test_get_by_task(self, db_engine) -> None:
        TaskRepository(db_engine).create(TaskRecord(id="42", provider="github", title="Task"))
        repo = SessionRepository(db_engine)
        repo.create(SessionRecord(task_id="42", session_type="plan", ai_tool="claude"))
        repo.create(SessionRecord(task_id="42", session_type="impl", ai_tool="copilot"))

        sessions = repo.get_by_task("42")
        assert len(sessions) == 2


class TestTokenUsageRepository:
    def test_total_tokens_for_task(self, db_engine) -> None:
        task_repo = TaskRepository(db_engine)
        task_repo.create(TaskRecord(id="42", provider="github", title="Task"))

        session_repo = SessionRepository(db_engine)
        s1 = session_repo.create(SessionRecord(task_id="42", session_type="plan", ai_tool="claude"))
        s2 = session_repo.create(SessionRecord(task_id="42", session_type="impl", ai_tool="claude"))

        token_repo = TokenUsageRepository(db_engine)
        token_repo.create(TokenUsageRecord(session_id=s1.id, total_tokens=1000))
        token_repo.create(TokenUsageRecord(session_id=s2.id, total_tokens=5000))

        total = token_repo.total_tokens_for_task("42")
        assert total == 6000


class TestWorktreeRepository:
    def test_get_active(self, db_engine) -> None:
        repo = WorktreeRepository(db_engine)
        repo.create(WorktreeRecord(task_id="1", path="/wt/1", branch="feat/1", state="active"))
        repo.create(
            WorktreeRecord(
                task_id="2",
                path="/wt/2",
                branch="feat/2",
                state="stale_merged",
            )
        )

        active = repo.get_active()
        assert len(active) == 1
        assert active[0].task_id == "1"


class TestAuditLogRepository:
    def test_log_and_retrieve(self, db_engine) -> None:
        repo = AuditLogRepository(db_engine)
        repo.log("task.created", entity_type="task", entity_id="42")
        repo.log("worktree.created", entity_type="worktree", entity_id="wt-1")

        recent = repo.get_recent(limit=10)
        assert len(recent) == 2
        assert recent[0].action == "worktree.created"  # Most recent first

"""Tests verifying that DB write operations use explicit commits (transactions).

These tests prove that:
1. Write operations (INSERT/UPDATE/DELETE) are committed to the DB.
2. Read operations return None/empty for non-existent records without side effects.
3. Sequential writes are each individually committed and both persist.

Each assertion that proves a commit happened opens a *brand-new* session so that
no SQLModel identity-map / session cache can give a false positive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from ghaiw.db.engine import create_db_engine, init_db
from ghaiw.db.repositories import (
    AuditLogRepository,
    TaskRepository,
)
from ghaiw.db.tables import AuditLogRecord, TaskRecord


@pytest.fixture
def db_engine(tmp_path: Path):  # type: ignore[return]
    """Create a fresh, isolated test database engine."""
    db_path = tmp_path / ".ghaiw" / "ghaiw.db"
    engine = create_db_engine(db_path)
    init_db(engine)
    yield engine
    engine.dispose()


class TestWriteTransactions:
    """Verify that all repository write methods commit their changes."""

    def test_write_is_committed(self, db_engine) -> None:  # type: ignore[no-untyped-def]
        """Create a record via repository; a FRESH session must see the record.

        If session.commit() were missing, the fresh session would return None.
        """
        repo = TaskRepository(db_engine)
        record = TaskRecord(id="99", provider="github", title="Commit test")
        repo.create(record)

        # Open a completely separate session — bypasses any identity-map cache
        with Session(db_engine) as fresh_session:  # type: ignore[arg-type]
            fetched = fresh_session.get(TaskRecord, "99")

        assert fetched is not None
        assert fetched.title == "Commit test"

    def test_read_does_not_affect_writes(self, db_engine) -> None:  # type: ignore[no-untyped-def]
        """Read of a non-existent record returns None; no ghost rows are created.

        Guards against a regression where a read operation accidentally triggered
        a flush/write path.
        """
        repo = TaskRepository(db_engine)

        # Should return None without touching the DB
        result = repo.get("nonexistent-id")
        assert result is None

        # Confirm the table is still empty
        with Session(db_engine) as fresh_session:  # type: ignore[arg-type]
            statement = select(TaskRecord)
            all_records = list(fresh_session.exec(statement).all())

        assert all_records == [], "Read operation must not insert any rows"

    def test_sequential_writes_both_committed(self, db_engine) -> None:  # type: ignore[no-untyped-def]
        """Two sequential writes are each committed; both persist in a fresh session."""
        repo = TaskRepository(db_engine)
        repo.create(TaskRecord(id="1", provider="github", title="First"))
        repo.create(TaskRecord(id="2", provider="github", title="Second"))

        # Fresh session must see both records
        with Session(db_engine) as fresh_session:  # type: ignore[arg-type]
            statement = select(TaskRecord)
            all_records = list(fresh_session.exec(statement).all())

        assert len(all_records) == 2, "Both writes must have been committed"
        ids = {r.id for r in all_records}
        assert ids == {"1", "2"}

    def test_update_is_committed(self, db_engine) -> None:  # type: ignore[no-untyped-def]
        """An UPDATE via repository.update() is committed; fresh session sees new value."""
        repo = TaskRepository(db_engine)
        repo.create(TaskRecord(id="42", provider="github", title="Original"))

        repo.update("42", title="Updated")

        with Session(db_engine) as fresh_session:  # type: ignore[arg-type]
            fetched = fresh_session.get(TaskRecord, "42")

        assert fetched is not None
        assert fetched.title == "Updated", "UPDATE must be committed"

    def test_audit_log_write_is_committed(self, db_engine) -> None:  # type: ignore[no-untyped-def]
        """AuditLogRepository.log() commits its INSERT; fresh session sees the entry."""
        repo = AuditLogRepository(db_engine)
        repo.log("task.created", entity_type="task", entity_id="5")

        with Session(db_engine) as fresh_session:  # type: ignore[arg-type]
            statement = select(AuditLogRecord)
            all_records = list(fresh_session.exec(statement).all())

        assert len(all_records) == 1
        assert all_records[0].action == "task.created"
        assert all_records[0].entity_id == "5"

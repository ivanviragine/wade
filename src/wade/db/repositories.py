"""Repository classes — one per table, encapsulating all queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from wade.db.tables import (
    AuditLogRecord,
    DependencyRecord,
    PRRecord,
    SessionRecord,
    TaskRecord,
    TokenUsageRecord,
    WorktreeRecord,
)


class TaskRepository:
    """CRUD operations for tasks."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: TaskRecord) -> TaskRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get(self, task_id: str) -> TaskRecord | None:
        with Session(self.engine) as session:
            return session.get(TaskRecord, task_id)

    def get_by_state(self, state: str) -> list[TaskRecord]:
        with Session(self.engine) as session:
            statement = select(TaskRecord).where(TaskRecord.state == state)
            return list(session.exec(statement).all())

    def update(self, task_id: str, **kwargs: Any) -> TaskRecord | None:
        with Session(self.engine) as session:
            record = session.get(TaskRecord, task_id)
            if record is None:
                return None
            for key, value in kwargs.items():
                setattr(record, key, value)
            record.updated_at = datetime.now()
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def upsert(self, record: TaskRecord) -> TaskRecord:
        """Insert or update a task record."""
        with Session(self.engine) as session:
            existing = session.get(TaskRecord, record.id)
            if existing:
                for field in type(record).model_fields:
                    if field != "id":
                        setattr(existing, field, getattr(record, field))
                existing.synced_at = datetime.now()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            else:
                session.add(record)
                session.commit()
                session.refresh(record)
                return record


class SessionRepository:
    """CRUD operations for work sessions."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: SessionRecord) -> SessionRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get(self, session_id: str) -> SessionRecord | None:
        with Session(self.engine) as session:
            return session.get(SessionRecord, session_id)

    def get_by_task(self, task_id: str) -> list[SessionRecord]:
        with Session(self.engine) as session:
            statement = select(SessionRecord).where(SessionRecord.task_id == task_id)
            return list(session.exec(statement).all())

    def get_active(self) -> list[SessionRecord]:
        with Session(self.engine) as session:
            statement = select(SessionRecord).where(SessionRecord.ended_at.is_(None))  # type: ignore[union-attr]
            return list(session.exec(statement).all())

    def update(self, session_id: str, **kwargs: Any) -> SessionRecord | None:
        with Session(self.engine) as session:
            record = session.get(SessionRecord, session_id)
            if record is None:
                return None
            for key, value in kwargs.items():
                setattr(record, key, value)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


class TokenUsageRepository:
    """CRUD operations for token usage records."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: TokenUsageRecord) -> TokenUsageRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_by_session(self, session_id: str) -> TokenUsageRecord | None:
        with Session(self.engine) as session:
            statement = select(TokenUsageRecord).where(TokenUsageRecord.session_id == session_id)
            result = session.exec(statement).first()
            return result

    def total_tokens_for_task(self, task_id: str) -> int:
        """Sum total tokens across all sessions for a task."""
        with Session(self.engine) as session:
            statement = (
                select(TokenUsageRecord).join(SessionRecord).where(SessionRecord.task_id == task_id)
            )
            records = session.exec(statement).all()
            return sum(r.total_tokens or 0 for r in records)


class WorktreeRepository:
    """CRUD operations for worktree records."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: WorktreeRecord) -> WorktreeRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_active(self) -> list[WorktreeRecord]:
        with Session(self.engine) as session:
            statement = select(WorktreeRecord).where(WorktreeRecord.state == "active")
            return list(session.exec(statement).all())

    def get_by_task(self, task_id: str) -> WorktreeRecord | None:
        with Session(self.engine) as session:
            statement = (
                select(WorktreeRecord)
                .where(WorktreeRecord.task_id == task_id)
                .where(WorktreeRecord.state == "active")
            )
            return session.exec(statement).first()

    def update(self, worktree_id: str, **kwargs: Any) -> WorktreeRecord | None:
        with Session(self.engine) as session:
            record = session.get(WorktreeRecord, worktree_id)
            if record is None:
                return None
            for key, value in kwargs.items():
                setattr(record, key, value)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


class PRRepository:
    """CRUD operations for pull request records."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: PRRecord) -> PRRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_by_session(self, session_id: str) -> PRRecord | None:
        with Session(self.engine) as session:
            statement = select(PRRecord).where(PRRecord.session_id == session_id)
            return session.exec(statement).first()


class DependencyRepository:
    """CRUD operations for dependency records."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, record: DependencyRecord) -> DependencyRecord:
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_for_task(self, task_id: str) -> list[DependencyRecord]:
        """Get all dependencies where this task is involved."""
        with Session(self.engine) as session:
            statement = select(DependencyRecord).where(
                (DependencyRecord.from_task_id == task_id)
                | (DependencyRecord.to_task_id == task_id)
            )
            return list(session.exec(statement).all())

    def get_all(self) -> list[DependencyRecord]:
        with Session(self.engine) as session:
            return list(session.exec(select(DependencyRecord)).all())


class AuditLogRepository:
    """CRUD operations for the audit log."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def log(
        self,
        action: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: str | None = None,
    ) -> AuditLogRecord:
        record = AuditLogRecord(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_recent(self, limit: int = 50) -> list[AuditLogRecord]:
        with Session(self.engine) as session:
            statement = (
                select(AuditLogRecord).order_by(col(AuditLogRecord.timestamp).desc()).limit(limit)
            )
            return list(session.exec(statement).all())

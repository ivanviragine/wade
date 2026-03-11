"""SQLModel table definitions for the wade database.

Relationships are handled through the repository layer using explicit queries
rather than ORM navigation. This avoids SQLAlchemy mapper configuration
issues with generic types across Python versions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class TaskRecord(SQLModel, table=True):
    """Local cache/mirror of a task from the external provider."""

    __tablename__ = "tasks"

    id: str = Field(primary_key=True)
    provider: str
    title: str
    body: str = ""
    state: str = "open"
    complexity: str | None = None
    parent_id: str | None = None
    url: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    synced_at: datetime = Field(default_factory=datetime.now)


class SessionRecord(SQLModel, table=True):
    """An implementation session — one AI agent on one task."""

    __tablename__ = "sessions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="tasks.id")
    session_type: str  # "plan" or "implementation"
    ai_tool: str
    ai_model: str | None = None
    worktree_path: str | None = None
    branch_name: str | None = None
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    transcript_path: str | None = None


class TokenUsageRecord(SQLModel, table=True):
    """Token usage for a session."""

    __tablename__ = "token_usage"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="sessions.id")
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    premium_requests: int | None = None
    cost_estimate_usd: float | None = None


class ModelBreakdownRecord(SQLModel, table=True):
    """Per-model token breakdown within a session."""

    __tablename__ = "model_breakdown"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    usage_id: str = Field(foreign_key="token_usage.id")
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    premium_requests: int = 0


class PRRecord(SQLModel, table=True):
    """Pull request linked to a session."""

    __tablename__ = "pull_requests"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="sessions.id")
    pr_number: int
    pr_url: str
    branch: str
    state: str = "open"
    created_at: datetime = Field(default_factory=datetime.now)
    merged_at: datetime | None = None


class WorktreeRecord(SQLModel, table=True):
    """Tracked worktrees."""

    __tablename__ = "worktrees"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: str
    path: str
    branch: str
    state: str = "active"
    created_at: datetime = Field(default_factory=datetime.now)
    removed_at: datetime | None = None


class DependencyRecord(SQLModel, table=True):
    """Task dependency edges."""

    __tablename__ = "dependencies"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    from_task_id: str
    to_task_id: str
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class AuditLogRecord(SQLModel, table=True):
    """Audit log for all workflow actions."""

    __tablename__ = "audit_log"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    details: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

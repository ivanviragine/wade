"""Session domain models — ImplementationSession, WorktreeState, SyncResult."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from wade.models.ai import AIToolID, TokenUsage


class MergeStrategy(StrEnum):
    """How feature branches are merged into main."""

    PR = "PR"
    DIRECT = "direct"


class WorktreeState(StrEnum):
    """State of a git worktree."""

    ACTIVE = "active"
    STALE_EMPTY = "stale_empty"
    STALE_MERGED = "stale_merged"
    STALE_REMOTE_GONE = "stale_remote_gone"


class ImplementationSession(BaseModel):
    """An implementation session — one AI agent working on one task in one worktree."""

    id: str
    task_id: str
    worktree_path: Path
    branch_name: str
    ai_tool: AIToolID
    ai_model: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    token_usage: TokenUsage | None = None
    pr_number: int | None = None
    pr_url: str | None = None


class SessionRecord(BaseModel):
    """A parsed session row from a PR body — typed alternative to raw dicts."""

    phase: str
    ai_tool: str
    session_id: str


class SyncEventType(StrEnum):
    """Event types emitted during sync operations."""

    ERROR = "error"
    PREFLIGHT_OK = "preflight_ok"
    UP_TO_DATE = "up_to_date"
    DONE = "done"
    DRY_RUN = "dry_run"
    MERGED = "merged"
    CONFLICT = "conflict"
    CONFLICT_DIFF = "conflict_diff"


class SyncEvent(BaseModel):
    """A structured event emitted during a sync operation."""

    event: SyncEventType
    data: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.now)


class SyncResult(BaseModel):
    """Result of a work sync operation."""

    success: bool
    current_branch: str
    main_branch: str
    conflicts: list[str] = []
    commits_merged: int = 0
    events: list[SyncEvent] = []

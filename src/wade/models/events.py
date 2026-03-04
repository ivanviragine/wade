"""Typed event models for structured output."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Categories of workflow events."""

    # Sync events
    PREFLIGHT_OK = "preflight_ok"
    PREFLIGHT_FAIL = "preflight_fail"
    FETCH_OK = "fetch_ok"
    FETCH_FAIL = "fetch_fail"
    MERGE_OK = "merge_ok"
    MERGE_CONFLICT = "merge_conflict"
    ALREADY_UP_TO_DATE = "already_up_to_date"

    # Work events
    WORKTREE_CREATED = "worktree_created"
    WORKTREE_REMOVED = "worktree_removed"
    AI_LAUNCHED = "ai_launched"
    AI_EXITED = "ai_exited"
    PR_CREATED = "pr_created"
    PR_MERGED = "pr_merged"

    # Task events
    TASK_CREATED = "task_created"
    TASK_CLOSED = "task_closed"
    PLAN_STARTED = "plan_started"
    PLAN_COMPLETED = "plan_completed"
    DEPS_ANALYZED = "deps_analyzed"


class WorkflowEvent(BaseModel):
    """A typed workflow event for structured output (--json mode)."""

    event: EventType
    data: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.now)

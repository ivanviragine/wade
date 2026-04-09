"""SQLite data layer via SQLModel.

Infrastructure for persistent local state (sessions, token usage, worktrees, deps).
Tables and repositories are defined but not yet wired into the service layer —
all runtime state currently flows through GitHub Issues, PR bodies, and the filesystem.
This layer is forward-looking and will be integrated as local analytics and
session history features are added.
"""

from wade.db.engine import create_db_engine, get_or_create_engine, init_db
from wade.db.tables import (
    AuditLogRecord,
    DependencyRecord,
    ModelBreakdownRecord,
    PRRecord,
    SessionRecord,
    TaskRecord,
    TokenUsageRecord,
    WorktreeRecord,
)

__all__ = [
    "AuditLogRecord",
    "DependencyRecord",
    "ModelBreakdownRecord",
    "PRRecord",
    "SessionRecord",
    "TaskRecord",
    "TokenUsageRecord",
    "WorktreeRecord",
    "create_db_engine",
    "get_or_create_engine",
    "init_db",
]

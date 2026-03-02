"""Context variables for session-scoped structured logging."""

from __future__ import annotations

import structlog


def bind_session_context(*, session_id: str, task_id: str | None = None) -> None:
    """Bind session context variables for all subsequent log entries."""
    structlog.contextvars.bind_contextvars(
        session_id=session_id,
        task_id=task_id,
    )


def clear_session_context() -> None:
    """Clear all session context variables."""
    structlog.contextvars.clear_contextvars()

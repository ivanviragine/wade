"""Worktree domain model — a single ``git worktree list`` entry."""

from __future__ import annotations

from pydantic import BaseModel


class Worktree(BaseModel):
    """A single git worktree entry parsed from ``git worktree list --porcelain``.

    Attributes:
        path: Absolute path to the worktree. Always present — it is the first
            porcelain line for every entry.
        head: The checked-out commit SHA, or ``None`` for a bare/mid-operation
            entry that omits ``HEAD``.
        branch: The short ref name (e.g. ``main``) or the literal
            ``"(detached)"`` for detached-HEAD worktrees. ``None`` when the
            entry carries neither a branch nor a detached marker.
    """

    path: str
    head: str | None = None
    branch: str | None = None

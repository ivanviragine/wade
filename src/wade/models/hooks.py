"""Hook-related domain models — pure data shared by the hooks and service layers.

Lives in the leaf ``models`` layer so both the ``wade-hook`` CLI
(:mod:`wade.hooks`) and the bootstrap installer
(:mod:`wade.services.implementation_service.bootstrap`) can name the same guard
identifiers without the service layer importing the hooks layer (which the
layering rules forbid).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["StopGuard"]


class StopGuard(StrEnum):
    """The Stop-hook guards ``wade-hook stop --guard`` accepts.

    A single source of truth shared by the installer
    (:func:`wade.services.implementation_service.bootstrap._install_stop_hook`)
    and the CLI (``wade.hooks.cli._STOP_GUARDS``), so bootstrap cannot install a
    guard the CLI does not recognize and a typo is a type error rather than a
    silently fail-open unknown guard. A worktree is only ever one kind of session,
    so the two guards never share one.
    """

    SESSION_COMPLETE = "session-complete"  # impl/review sessions — nudge to run ``done``
    PLAN_COMPLETE = "plan-complete"  # plan sessions — nudge to write a valid plan file

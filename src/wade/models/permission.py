"""Permission/autonomy domain model — orthogonal to ``DelegationMode``.

``DelegationMode`` (``models/delegation.py``) governs *how* an AI tool is
dispatched (prompt / interactive / headless). ``PermissionMode`` governs *how
much* the tool is allowed to do without prompting — the autonomy axis crossby
exposes via the ``yolo`` / ``auto`` / ``accept_edits`` launch booleans.

The precedence ladder (most → least permissive) is owned by crossby, which
resolves per-tool flags and downgrades unsupported tiers with a warning:

    yolo > auto > accept-edits > default (> plan, driven separately)

``plan`` is intentionally **not** a ``PermissionMode`` value: WADE drives plan
mode through its own path (``plan_service`` passes ``plan_mode=True``), so the
user-facing autonomy enum is only the four values below.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class PermissionMode(StrEnum):
    """How much autonomy the AI tool is granted (crossby autonomy axis)."""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept-edits"
    AUTO = "auto"
    YOLO = "yolo"


class AutonomyLaunchKwargs(TypedDict):
    """crossby's autonomy boolean triplet, ready to splat into launch calls."""

    yolo: bool
    auto: bool
    accept_edits: bool


def coerce_permission_mode(value: str | PermissionMode | None) -> PermissionMode | None:
    """Coerce a raw value to ``PermissionMode``, or ``None`` if it is not one.

    Returns ``None`` for unset, unknown, or the excluded ``plan`` value —
    callers apply their own fallback (warn + ``default``). This never raises,
    so invalid config/CLI input degrades gracefully instead of erroring.

    Input is normalized (underscores → hyphens, lower-cased) before matching,
    so ``accept_edits`` (crossby's kwarg spelling) and ``Accept-Edits`` both
    resolve to :attr:`PermissionMode.ACCEPT_EDITS`.
    """
    if value is None:
        return None
    if isinstance(value, PermissionMode):
        return value
    try:
        return PermissionMode(value.replace("_", "-").lower())
    except (ValueError, AttributeError):
        # ValueError: unknown/excluded string (e.g. ``plan``). AttributeError:
        # a non-string slipped past the type hint (e.g. a raw bool/int from
        # untyped YAML) — honor the "never raises" contract and degrade to None.
        return None


def permission_mode_launch_kwargs(mode: PermissionMode) -> AutonomyLaunchKwargs:
    """Translate a ``PermissionMode`` into crossby's autonomy boolean triplet.

    Exactly one of ``yolo`` / ``auto`` / ``accept_edits`` is ``True`` for the
    non-default tiers; ``default`` yields all-``False``. WADE only forwards the
    requested tier — crossby owns capability-aware downgrades and warnings, so
    this must not gate on per-tool support. Returns a ``TypedDict`` so callers
    can ``**``-splat it straight into ``launch`` / ``build_launch_command``.
    """
    return {
        "yolo": mode is PermissionMode.YOLO,
        "auto": mode is PermissionMode.AUTO,
        "accept_edits": mode is PermissionMode.ACCEPT_EDITS,
    }

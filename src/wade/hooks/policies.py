"""Write-guard policies — pure predicates over a normalized :class:`HookEvent`.

These are the wade-owned *policy* half of the hook contract. All the per-tool
dialect parsing/emitting lives in ``crossby.hooks.runtime``; here we only decide
allow vs deny for a normalized event. Each predicate is dialect-agnostic and
side-effect free, so it is trivially unit-testable without a subprocess.

Two guards today:

- :func:`worktree_containment` — block writes outside the worktree root
  (installed only for tools that don't hard-sandbox writes; see
  ``AIToolCapabilities.sandboxes_writes``).
- :func:`plan_artifact_only` — during a plan session, allow writes only to plan
  artifacts (finer-grained than any directory sandbox, so always installed).
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import stat as stat_module
from pathlib import Path

from crossby.hooks.runtime import HookDecision, HookEvent

__all__ = [
    "GUARD_NAMES",
    "plan_artifact_only",
    "session_complete",
    "stop_nudge_marker_path",
    "worktree_containment",
]

# Guard names understood by the ``wade hook`` entry point. ``worktree`` / ``plan``
# are PreToolUse write guards; ``session-complete`` is a Stop guard.
GUARD_NAMES = ("worktree", "plan", "session-complete")

# Relative path of the marker that records the Stop guard already nudged this
# worktree. Lives under ``.wade/`` (gitignored per-session). The read side lives
# in :func:`session_complete`; the ``wade-hook`` CLI writes it after a block so
# the nudge is single-shot for *every* tool — not only Claude, which is the only
# tool that sends ``stop_hook_active``.
_STOP_NUDGE_MARKER = ".wade/stop-nudged"


def stop_nudge_marker_path(worktree_root: Path) -> Path:
    """Absolute path of the Stop-guard single-shot marker for ``worktree_root``."""
    return worktree_root / _STOP_NUDGE_MARKER


def _dir_fd_supported() -> bool:
    """True when the platform can open a no-follow dir handle for ``*at`` calls."""
    return hasattr(os, "O_DIRECTORY") and os.stat in os.supports_dir_fd


def stop_nudge_present(worktree_root: Path) -> bool:
    """True if a *trusted* single-shot marker exists — race-safe against symlinks.

    Opens ``.wade`` itself with ``O_DIRECTORY | O_NOFOLLOW`` (so a symlinked
    ``.wade`` fails outright) and stats the marker relative to that directory
    handle without following symlinks. Using a handle rather than re-resolving
    the path closes the TOCTOU window where ``.wade`` is swapped for a symlink
    between the check and the read. On platforms without ``dir_fd`` support the
    marker is ignored (treated as absent) rather than followed unsafely — a
    missed marker only costs one extra nudge, which is harmless.
    """
    if not _dir_fd_supported():
        return False
    marker = stop_nudge_marker_path(worktree_root)
    dir_fd = None
    try:
        dir_fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        st = os.stat(marker.name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return stat_module.S_ISREG(st.st_mode)


# Plan-session artifacts an agent may write while planning (basenames + globs).
_ALLOWED_PLAN_BASENAMES = (
    "PLAN.md",
    "PLAN-*.md",
    "prompt.txt",
    ".transcript",
    ".commit-msg",
    "PR-SUMMARY.md",
)

# Directory prefixes (posix-normalized) whose contents are plan artifacts.
_ALLOWED_PLAN_DIR_PREFIXES = (".claude/plans", ".wade/plans")


def _resolve_path(file_path: str) -> Path:
    """Resolve a file path to absolute — absolute as-is, relative against CWD."""
    p = Path(file_path)
    if p.is_absolute():
        return p.resolve()
    return (Path(os.getcwd()) / p).resolve()


def worktree_containment(event: HookEvent, *, worktree_root: Path) -> HookDecision:
    """Deny writes outside ``worktree_root`` — and writes we cannot locate at all.

    A non-write tool call is allowed (nothing to contain). A *write*, however, is
    denied unless its target resolves to a path inside the worktree: the PreToolUse
    matcher only fires this hook on write tools, so a write with a missing or
    unresolvable ``file_path`` is a write we can't verify is contained — failing
    open would let it through unchecked. (The ``wade-hook`` CLI short-circuits a
    genuinely *empty* payload to allow before reaching here, so this only sees
    events that actually described a write.)
    """
    if not event.is_write:
        return HookDecision.allow()

    if not event.file_path:
        return HookDecision.deny(
            "BLOCKED by worktree guard: a write was requested but no target path "
            "was present in the hook payload, so it cannot be verified as inside "
            f"the worktree at '{worktree_root.resolve()}'."
        )

    try:
        resolved = _resolve_path(event.file_path)
    except (OSError, ValueError):
        return HookDecision.deny(
            f"BLOCKED by worktree guard: cannot resolve write path "
            f"'{event.file_path}' to verify it is inside the worktree at "
            f"'{worktree_root.resolve()}'."
        )

    root = worktree_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return HookDecision.deny(
            f"BLOCKED by worktree guard: cannot write to '{event.file_path}'. "
            f"You should only edit files inside your worktree at '{root}'."
        )
    return HookDecision.allow()


def _is_plan_artifact(file_path: str) -> bool:
    """True if ``file_path`` is an allowed plan artifact (basename or dir prefix)."""
    normalized = posixpath.normpath(file_path.replace("\\", "/"))

    for prefix in _ALLOWED_PLAN_DIR_PREFIXES:
        norm_prefix = posixpath.normpath(prefix)
        if (
            normalized == norm_prefix
            or normalized.startswith(f"{norm_prefix}/")
            or f"/{norm_prefix}/" in f"/{normalized}/"
        ):
            return True

    basename = posixpath.basename(normalized)
    return any(fnmatch.fnmatch(basename, pattern) for pattern in _ALLOWED_PLAN_BASENAMES)


def plan_artifact_only(event: HookEvent, *, worktree_root: Path) -> HookDecision:
    """During a plan session, deny writes outside the worktree or to non-artifacts.

    Plan mode installs this guard *instead of* :func:`worktree_containment`, so it
    must enforce containment first — otherwise an artifact-named path outside the
    worktree (e.g. ``/etc/PLAN.md`` or ``/tmp/.claude/plans/x.md``) would escape on
    non-sandboxed tools. Containment also denies a write with no resolvable path,
    so only located, contained writes reach the plan-artifact allowlist below.

    A non-write tool call is allowed. The ``posixpath`` normalization collapses
    ``../`` traversal so escapes like ``.claude/plans/../../src/x.py`` are blocked.
    """
    containment = worktree_containment(event, worktree_root=worktree_root)
    if containment.action == "deny":
        return containment

    if not event.is_write or not event.file_path:
        return HookDecision.allow()

    if _is_plan_artifact(event.file_path):
        return HookDecision.allow()

    return HookDecision.deny(
        f"BLOCKED by plan-session guard: cannot write to '{event.file_path}'. "
        "In plan mode, only plan artifacts (PLAN.md, PLAN-*.md, prompt.txt, "
        ".transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, .wade/plans/*) "
        "may be written. Do NOT modify source code files."
    )


def session_complete(event: HookEvent, *, worktree_root: Path) -> HookDecision:
    """Stop-hook guard: nudge (once) if the session's closing artifacts are absent.

    Returns a ``deny`` decision — which the Stop path renders as *block the stop
    and feed the reason back* — when ``PR-SUMMARY.md`` is missing from the
    worktree, so the workflow's closing steps are enforced rather than merely
    requested by the skill. Otherwise allow.

    **Single-shot** (never loop the session), via two independent signals:

    - ``event.stop_hook_active`` — Claude sets this once its Stop hook has fired
      and blocked; other tools do not send it.
    - the ``.wade/stop-nudged`` marker (see :func:`stop_nudge_marker_path`) — the
      ``wade-hook`` CLI writes it after this guard blocks, so the second Stop is
      allowed on *any* tool, not just Claude. This predicate only *reads* the
      marker; the CLI owns the write (keeping this a side-effect-free decision).

    The message is deliberately ignorable so a legitimate pause (e.g. stopping to
    ask the user a question) costs at most one gentle nudge.

    The hook is only installed in wade worktree sessions on Stop-capable tools,
    so no session-detection is needed here.
    """
    if event.stop_hook_active:
        return HookDecision.allow()

    if (worktree_root / "PR-SUMMARY.md").is_file():
        return HookDecision.allow()

    if stop_nudge_present(worktree_root):
        return HookDecision.allow()  # already nudged once this worktree

    return HookDecision.deny(
        "Before finishing: PR-SUMMARY.md is not present in the worktree. If your work "
        "is complete, write PR-SUMMARY.md and run the session's `done` command "
        "(`wade implementation-session done` or `wade review-pr-comments-session done`) "
        "to sync, push, and open/update the PR. If you are pausing to ask a question "
        "or are still mid-task, disregard this and continue."
    )

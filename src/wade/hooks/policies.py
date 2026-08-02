"""Write-guard policies — pure predicates over a normalized :class:`HookEvent`.

These are the wade-owned *policy* half of the hook contract. All the per-tool
dialect parsing/emitting lives in ``crossby.hooks.runtime``; here we only decide
allow vs deny for a normalized event. Each predicate is dialect-agnostic and
side-effect free, so it is trivially unit-testable without a subprocess.

Guards today:

- :func:`worktree_containment` — block writes outside the worktree root
  (installed only for tools that don't hard-sandbox writes; see
  ``AIToolCapabilities.sandboxes_writes``).
- :func:`plan_artifact_only` — during a plan session, allow writes only to plan
  artifacts (finer-grained than any directory sandbox, so always installed).
- :func:`shell_containment` — the same two rules applied to a *shell command*
  rather than a tool-call file path. crossby's ``HookEvent.is_write`` is False
  for shell tool names by design (``SHELL_TOOL_NAMES``), so a shell call carries
  its target in ``command``, not ``file_path``, and the two path guards above
  would wave it through. This closes that channel.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shlex
import stat as stat_module
from pathlib import Path

from crossby.hooks.runtime import HookDecision, HookEvent

__all__ = [
    "GUARD_NAMES",
    "plan_artifact_only",
    "session_complete",
    "shell_containment",
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


# Shell separators that start a fresh command segment, so the next token is an
# executable name rather than one of the previous command's arguments.
_SEGMENT_SEPARATORS = frozenset({"&&", "||", "|", ";", "&", "|&"})

# A redirect operator, optionally fd-prefixed (``2>``) or fd-duplicating (``&>``),
# optionally with the target glued on (``>/tmp/x``). ``shlex`` keeps the operator
# and an unspaced target in a single token, so the target is group ``target``.
_REDIRECT_RE = re.compile(r"^(?:[0-9]*|&)(?P<op>>>|>\||>|<)(?P<target>.*)$")

# Commands whose in-place flag rewrites a file rather than emitting to stdout.
_IN_PLACE_FLAG_RE = re.compile(r"^--in-place(=.*)?$|^-i(\..*)?$")


def _looks_like_path(token: str) -> bool:
    """True when a token plausibly names a filesystem path rather than a flag/word.

    ``.`` and ``..`` count: a bare ``..`` carries no ``/`` but still escapes.
    """
    if not token or token.startswith("-"):
        return False
    if token in (".", ".."):
        return True
    return token.startswith(("/", "~")) or "/" in token


def _resolve_shell_path(token: str, *, base: Path) -> Path | None:
    """Resolve a command token to an absolute path, or None if it isn't resolvable.

    Expands ``~`` (``shlex`` does not) and resolves relative tokens against
    ``base`` — the agent's CWD when the payload reports one, else the worktree
    root — so ``../../etc/passwd`` is collapsed and caught.
    """
    try:
        expanded = os.path.expanduser(token)
        p = Path(expanded)
        return p.resolve() if p.is_absolute() else (base / p).resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _contained(path: Path, root: Path) -> bool:
    """True when ``path`` is inside ``root``."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def shell_containment(
    event: HookEvent,
    *,
    worktree_root: Path,
    plan_mode: bool = False,
) -> HookDecision:
    """Deny a shell command that writes outside the worktree (or outside plan artifacts).

    crossby routes shell tool calls (``bash``, ``shell``, ``exec_command``,
    ``powershell``, ``run_command``) through :attr:`HookEvent.command` and reports
    ``is_write=False`` for them, so :func:`worktree_containment` and
    :func:`plan_artifact_only` never inspect them. Without this predicate a single
    ``printf ... > ../main-repo/src/app.py`` defeats both guards.

    Rules, in order (any match denies):

    1. The command does not tokenize (unbalanced quotes) — **fail closed**.
    2. A redirect target (``>``, ``>>``, ``2>``, glued or spaced) resolves outside
       the root.
    3. ``cd`` / ``pushd`` targets a path outside the root — it would rebase every
       later relative write in the same command.
    4. Any remaining path-like argument resolves outside the root. The *executable*
       position of each segment is exempt, since binaries legitimately live in
       ``/usr/bin``, ``/bin``, ``/opt/homebrew/bin`` and denying those breaks every
       session.
    5. In plan mode only: any redirect, any in-place edit flag (``sed -i``,
       ``--in-place``), or a ``tee`` target whose path is not a plan artifact.

    **This is defense-in-depth, not a completeness guarantee.** It stops the
    non-obfuscated cases — the ones an agent actually produces — and is trivially
    defeatable by an adversary. Known residual gaps, all out of scope because a
    tokenizer cannot resolve them without executing the command:

    - **Env-var indirection** — ``$HOME/x``, ``${OUT}/x``. ``shlex`` does not expand
      variables, so the token stays literal and resolves to a nonsense relative path
      inside the root.
    - **Command substitution** — ``$(echo /etc)/passwd``, backticks.
    - **Subshells and here-docs** — ``(cd /etc && ...)``, ``<<EOF`` bodies.
    - **``$IFS`` and word-splitting tricks**, ``eval``, ``base64 -d | sh``.
    - **Symlinks inside the worktree** pointing out of it: ``Path.resolve`` follows
      them, so a symlinked *target* is caught, but a symlink created earlier in the
      same command is not.
    - **Interpreters given inline code** — ``python -c 'open("/etc/x","w")'``.

    Args:
        event: Normalized hook event; only :attr:`HookEvent.command` is read.
        worktree_root: The session worktree; writes must stay inside it.
        plan_mode: Apply the stricter plan-session rules (rule 5).
    """
    command = (event.command or "").strip()
    if not command:
        return HookDecision.allow()  # nothing to inspect

    root = worktree_root.resolve()
    base = root
    if event.cwd:
        cwd_resolved = _resolve_shell_path(event.cwd, base=root)
        if cwd_resolved is not None:
            base = cwd_resolved

    try:
        tokens = shlex.split(command)
    except ValueError:
        return HookDecision.deny(
            f"BLOCKED by {'plan-session' if plan_mode else 'worktree'} guard: could not "
            f"parse the shell command well enough to verify it stays inside the worktree "
            f"at '{root}' (unbalanced quotes). Denying to fail closed — rewrite it as a "
            "simpler, fully-quoted command."
        )

    def deny_outside(what: str, token: str) -> HookDecision:
        return HookDecision.deny(
            f"BLOCKED by worktree guard: the shell command's {what} ('{token}') resolves "
            f"outside the worktree at '{root}'. Run commands that read or write only "
            "inside your worktree."
        )

    def check_redirect_target(target: str, op: str) -> HookDecision | None:
        """Containment (always) + plan-artifact (plan mode, writes only) for a target."""
        resolved = _resolve_shell_path(target, base=base)
        if resolved is None or not _contained(resolved, root):
            return deny_outside("redirect target", target)
        # ``<`` only reads, so the plan-artifact allowlist does not apply to it.
        if plan_mode and op != "<" and not _is_plan_artifact_path(resolved, root):
            return HookDecision.deny(
                f"BLOCKED by plan-session guard: redirecting output to '{target}' "
                "would write a non-artifact file without going through a write tool. "
                "In plan mode only plan artifacts (PLAN.md, PLAN-*.md, prompt.txt, "
                ".transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, "
                ".wade/plans/*) may be written."
            )
        return None

    expect_executable = True
    prev: str | None = None
    pending_redirect: str | None = None
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            expect_executable = True
            prev = None
            pending_redirect = None
            continue

        redirect = _REDIRECT_RE.match(token)
        if redirect:
            op = redirect.group("op")
            target = redirect.group("target")
            prev = None
            if not target:
                # Spaced form (``> path``) — the target is the next token.
                pending_redirect = op
            elif not target.startswith("&"):
                # Glued form (``>/tmp/x``). ``2>&1`` duplicates an fd, names no path.
                pending_redirect = None
                denial = check_redirect_target(target, op)
                if denial is not None:
                    return denial
            else:
                pending_redirect = None
            continue

        if pending_redirect is not None:
            op, pending_redirect = pending_redirect, None
            if not token.startswith("&"):
                denial = check_redirect_target(token, op)
                if denial is not None:
                    return denial
            continue

        if expect_executable:
            # Only the basename matters: `/bin/cd` and `cd` are the same builtin,
            # and the executable's own path is exempt from containment (rule 4).
            prev = posixpath.basename(token)
            expect_executable = False
            continue

        if prev in ("cd", "pushd"):
            # Every non-flag argument to cd/pushd is a directory, including a bare
            # `..` that `_looks_like_path` would not otherwise recognize.
            if token.startswith("-"):
                continue
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not _contained(resolved, root):
                return deny_outside(f"{prev} target", token)
            prev = None
            continue

        if plan_mode and _IN_PLACE_FLAG_RE.match(token):
            return HookDecision.deny(
                f"BLOCKED by plan-session guard: in-place editing ('{token}') is not "
                "allowed in plan mode — it rewrites a file without going through a "
                "write tool. Only plan artifacts may be written."
            )

        if _looks_like_path(token):
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not _contained(resolved, root):
                return deny_outside("path argument", token)
            if plan_mode and prev == "tee" and not _is_plan_artifact_path(resolved, root):
                return HookDecision.deny(
                    f"BLOCKED by plan-session guard: 'tee' would write to '{token}', "
                    "which is not a plan artifact. In plan mode only plan artifacts "
                    "(PLAN.md, PLAN-*.md, prompt.txt, .transcript, .commit-msg, "
                    "PR-SUMMARY.md, .claude/plans/*, .wade/plans/*) may be written."
                )

    return HookDecision.allow()


def _is_plan_artifact_path(resolved: Path, root: Path) -> bool:
    """True when an already-contained absolute path is an allowed plan artifact."""
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return False
    return _is_plan_artifact(relative.as_posix())


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

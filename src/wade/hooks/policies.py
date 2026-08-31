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

Scratch allowlist: all three guards — via :func:`_contained` on the file-path
channel (:func:`worktree_containment`, :func:`plan_artifact_only`) and
:func:`_is_always_allowed_scratch` directly on the shell channel
(:func:`shell_containment`) — permit writes to system temp dirs (``/tmp``,
``$TMPDIR``) and a small exact allowlist of discard/console devices
(``/dev/null`` …), in **both** worktree and plan mode. This is shared,
ephemeral scratch space outside the worktree, not a plan artifact or the
tool's memory; see :func:`_is_always_allowed_scratch` for the union and
:func:`_temp_write_prefixes` / :data:`_ALWAYS_ALLOWED_DEVICES` for why the two
sets stay separate constants.

Memory allowlist (``allow_paths``): all three guards accept an ``allow_paths``
tuple — the *active* tool's own memory location for *this session*, resolved by
:func:`wade.hooks.cli._memory_allow_paths` (e.g. Claude
``~/.claude/projects/<encoded-worktree>/memory``). A write inside it is
permitted despite containment, and is exempt from the plan-artifact rule in plan
mode, so a guarded tool can persist memory outside the worktree. On the shell
channel this is honored for redirect targets and write-command operands — but
also, incidentally, for any *glued* flag value targeting the allow-root (e.g.
``curl -o <memory-path>``), since the glued-path check does not distinguish a
write flag from a read one (see :func:`shell_containment`'s rule 5); this only
*narrows* what such a command would otherwise be denied for, never widens it
past the allow-root itself. The allowlist is **deliberately narrow — never the
tool's config/auth home** (``~/.claude/settings.json`` holds the ``hooks`` block
these guards depend on; allowlisting the whole home would let a session strip
its own guard) **— but not uniformly scoped to "memory only" across tools**:
Claude's allow-root is memory alone; Cursor's and Codex's are, respectively, the
session's whole project dir and the tool's whole (cross-project) sessions tree,
because neither tool's storage draws a finer boundary to key on (see
:func:`wade.hooks.cli._memory_allow_paths` for the per-tool breakdown). Every
other out-of-worktree write, including the tool's own config, stays denied. The
per-tool memory locations are mirrored wade-side today (like the dialect maps in
:mod:`wade.hooks.cli`); they ultimately belong in crossby's
``AIToolCapabilities`` — a follow-up, not on this path.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shlex
import tempfile
from pathlib import Path

from crossby.hooks.runtime import HookDecision, HookEvent

from wade.models.hooks import PLAN_ISSUE_REF_FILE, SessionPhase, StopGuard
from wade.utils import markers

__all__ = [
    "GUARD_NAMES",
    "plan_artifact_only",
    "plan_complete",
    "session_complete",
    "session_start_context",
    "shell_containment",
    "stop_nudge_marker_path",
    "worktree_containment",
]


# Guard names understood by the ``wade hook`` entry point. ``worktree`` / ``plan``
# are PreToolUse write guards; the Stop guards come from :class:`StopGuard`.
GUARD_NAMES = ("worktree", "plan", *(g.value for g in StopGuard))

# Name of the single-shot flag marker (under ``.wade/``, gitignored per-session)
# that records the Stop guard already nudged this worktree. Unlike the sha-keyed
# ``done`` marker it is a plain single-shot flag, so it rides the generic
# ``flag_marker_*`` primitives in :mod:`wade.utils.markers` — sharing the
# race-safe dir-fd handling so the two implementations cannot drift. The read
# side lives in :func:`session_complete`; the ``wade-hook`` CLI writes it after a
# block so the nudge is single-shot for *every* tool — not only Claude, which is
# the only tool that sends ``stop_hook_active``.
_STOP_NUDGE_NAME = "stop-nudged"


def stop_nudge_marker_path(worktree_root: Path) -> Path:
    """Absolute path of the Stop-guard single-shot marker for ``worktree_root``."""
    return markers.flag_marker_path(worktree_root, _STOP_NUDGE_NAME)


def stop_nudge_present(worktree_root: Path) -> bool:
    """True if a *trusted* single-shot marker exists — race-safe against symlinks.

    Delegates to :func:`wade.utils.markers.flag_marker_present`, which opens
    ``.wade`` with ``O_DIRECTORY | O_NOFOLLOW`` and stats the marker relative to
    that handle without following symlinks (a symlinked ``.wade`` fails
    outright). On platforms without ``dir_fd`` support the marker is treated as
    absent rather than followed unsafely — a missed marker only costs one extra
    nudge, which is harmless.
    """
    return markers.flag_marker_present(worktree_root, _STOP_NUDGE_NAME)


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


def worktree_containment(
    event: HookEvent, *, worktree_root: Path, allow_paths: tuple[Path, ...] = ()
) -> HookDecision:
    """Deny writes outside ``worktree_root`` — and writes we cannot locate at all.

    A non-write tool call is allowed (nothing to contain). A *write*, however, is
    denied unless its target resolves to a path inside the worktree **or** inside
    an allowed memory subtree (``allow_paths`` — the active tool's own memory
    root; see :func:`wade.hooks.cli._memory_allow_paths`): the PreToolUse matcher
    only fires this hook on write tools, so a write with a missing or unresolvable
    ``file_path`` is a write we can't verify is contained — failing open would let
    it through unchecked. (The ``wade-hook`` CLI short-circuits a genuinely
    *empty* payload to allow before reaching here, so this only sees events that
    actually described a write.)

    Beyond the worktree itself, two kinds of out-of-worktree target are permitted,
    both via :func:`_contained`: an allowed memory subtree (``allow_paths``) and
    always-allowed scratch — system temp dirs and a small exact device allowlist
    (:func:`_is_always_allowed_scratch`) — shared, ephemeral scratch space that is
    safe to write even though it sits outside the worktree. This mirrors the shell
    channel's (:func:`shell_containment`) scratch exemption, so the two channels
    agree on what "contained" means.
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
    if _contained(resolved, root, allow_paths):
        return HookDecision.allow()
    return HookDecision.deny(
        f"BLOCKED by worktree guard: cannot write to '{event.file_path}'. "
        f"You should only edit files inside your worktree at '{root}'."
    )


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


def plan_artifact_only(
    event: HookEvent, *, worktree_root: Path, allow_paths: tuple[Path, ...] = ()
) -> HookDecision:
    """During a plan session, deny writes outside the worktree or to non-artifacts.

    Plan mode installs this guard *instead of* :func:`worktree_containment`, so it
    must enforce containment first — otherwise an artifact-named path outside the
    worktree (e.g. ``/etc/PLAN.md`` or ``/tmp/.claude/plans/x.md``) would escape on
    non-sandboxed tools. Containment also denies a write with no resolvable path,
    so only located, contained writes reach the plan-artifact allowlist below.

    ``allow_paths`` (the active tool's memory subtree) is threaded into
    containment so a memory write is not rejected as "outside the worktree", and
    then exempted from the plan-artifact rule **before** it is applied: a memory
    path resolves outside ``worktree_root``, so :func:`_is_plan_artifact` would
    never match it and it would be denied as a non-artifact. The exemption keeps
    memory writable in plan mode without loosening the artifact rule for
    in-worktree paths.

    System temp dirs and discard/console devices are exempted the same way,
    checked **after** the memory exemption and **before**
    :func:`_is_plan_artifact`, via :func:`_is_scratch_outside_worktree` rather
    than the plain :func:`_is_always_allowed_scratch`: a scratch path normally
    resolves outside ``worktree_root``, so the artifact check would otherwise
    deny it as a non-artifact — but when ``worktree_root`` itself sits under a
    system temp dir (an ephemeral clone, a CI job, a configured temp worktree
    directory), every in-worktree path *also* matches the temp-prefix test, so
    the exemption is only applied when the resolved target is outside
    ``worktree_root`` — otherwise ordinary in-worktree source writes would
    bypass the artifact allowlist entirely. ``resolved`` is computed
    unconditionally (not only when ``allow_paths`` is set) so this exemption
    applies even when no memory allowlist is configured for the session — the
    common case.

    A non-write tool call is allowed. The ``posixpath`` normalization collapses
    ``../`` traversal so escapes like ``.claude/plans/../../src/x.py`` are blocked.
    """
    containment = worktree_containment(event, worktree_root=worktree_root, allow_paths=allow_paths)
    if containment.action == "deny":
        return containment

    if not event.is_write or not event.file_path:
        return HookDecision.allow()

    try:
        resolved = _resolve_path(event.file_path)
    except (OSError, ValueError):
        resolved = None

    if resolved is not None:
        if allow_paths and _under_any(resolved, allow_paths):
            return HookDecision.allow()  # memory subtree — exempt from artifact rule
        if _is_scratch_outside_worktree(resolved, worktree_root.resolve()):
            return HookDecision.allow()  # temp/device scratch — exempt from artifact rule

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

# Characters a separator run is built from (``echo x|tee f``, ``a;;b``).
_SEPARATOR_CHARS = "|;&"

# A redirect operator standing alone as its own token, optionally fd-prefixed
# (``2>``) or fd-duplicating (``&>``), with a trailing ``&`` captured separately
# (``>&``). :func:`_tokenize` returns runs of ``<>|&`` glued together but splits a
# *leading* fd digit off (``2>&1`` -> ``2``/``>&``/``1``), so :func:`_normalize_tokens`
# re-attaches both ends.
_REDIRECT_OP_RE = re.compile(r"^(?P<head>(?:[0-9]*|&)(?:>>|>\||>|<))(?P<amp>&?)$")

# The tail of an fd duplication or close (``2>&1`` -> ``1``, ``>&-`` -> ``-``).
_FD_TAIL_RE = re.compile(r"^(?:[0-9]+-?|-)$")

# A redirect operator, optionally fd-prefixed (``2>``) or fd-duplicating (``&>``),
# optionally with the target glued on (``>/tmp/x``). ``shlex`` keeps the operator
# and an unspaced target in a single token, so the target is group ``target``.
_REDIRECT_RE = re.compile(r"^(?:[0-9]*|&)(?P<op>>>|>\||>|<)(?P<target>.*)$")

# A redirect target that duplicates or closes a file descriptor (``2>&1``, ``>&-``)
# rather than naming a file. Bash's ``>&word`` with a *filename* is a real write, so
# only these exact shapes may be skipped.
_FD_DUP_RE = re.compile(r"^&(?:[0-9]+-?|-)$")

# Commands whose in-place flag rewrites a file rather than emitting to stdout.
_IN_PLACE_FLAG_RE = re.compile(r"^--in-place(=.*)?$|^-i(\..*)?$")

# ...but only for commands where ``-i`` actually *means* in-place. It is an
# ordinary read flag almost everywhere else (``grep -i``, ``rg -i``, ``ls -i``,
# ``git commit -i``, ``ssh -i key``), and denying those broke plan sessions on
# perfectly innocent commands. An in-place tool missing from this set is a gap in
# rule 5 only — the worktree rules still apply, so it cannot escape the root.
_IN_PLACE_COMMANDS = frozenset({"sed", "gsed", "perl", "ruby", "yq"})


def _temp_write_prefixes() -> tuple[str, ...]:
    """Resolved system-temp path prefixes writes may always target.

    System temp dirs sit outside the worktree but are legitimate shared scratch
    space — agents and wade itself stage throwaway files there (headless-review
    logs, patches, ``mktemp`` output). Allowed in **both** worktree and plan mode,
    on **both** the shell channel (:func:`shell_containment`) and the file-path
    channel (:func:`worktree_containment` / :func:`plan_artifact_only`), via
    :func:`_is_always_allowed_scratch`. Resolved so macOS's ``/tmp``→``/private/tmp``
    and ``$TMPDIR`` (``/var/folders/…``, itself under a ``/private`` symlink) match
    the *resolved* path :func:`_contained` compares against; each ends in a
    separator so ``/tmp/`` never matches a sibling like ``/tmpfoo``.

    **Accepted-risk note:** unlike a discard device, a temp write persists real
    bytes to disk. Allowing it in *both* plan and impl mode opens a narrow
    cross-session channel — a compromised/prompt-injected plan session (no shell
    execution) could stage a file in ``$TMPDIR`` that a later impl session (which
    *does* have shell execution) reads or executes. Accepted because system temp is
    world-shared scratch already reachable by any local process on the machine, and
    impl mode already allowed it — but it is why :data:`_ALWAYS_ALLOWED_DEVICES` and
    these prefixes stay **separate constants** rather than one merged set (see that
    docstring).
    """
    prefixes: set[str] = set()
    for raw in ("/tmp", tempfile.gettempdir()):
        try:
            resolved = str(Path(raw).resolve())
        except (OSError, ValueError, RuntimeError):
            resolved = raw
        prefixes.add(resolved.rstrip("/") + "/")
    return tuple(sorted(prefixes))


# Known discard/console character devices — `>/dev/null 2>&1` is the single most
# common shell idiom an agent emits. Writing one persists nothing and touches no
# worktree/repo file, so writes to these exact paths are always allowed; every real
# project/source path outside the root stays contained. System temp dirs
# (:func:`_temp_write_prefixes`) are the other write exception — shared scratch
# space outside the worktree. Both exceptions are unioned by
# :func:`_is_always_allowed_scratch` and apply in worktree AND plan mode, on both
# the shell and file-path channels.
#
# This is an *exact* allowlist, deliberately not a `/dev/` prefix: Linux mounts
# writable filesystems under `/dev/` too (`/dev/shm` tmpfs, `/dev/mqueue`,
# `/dev/hugepages`), where a write persists a real file outside the worktree — a
# bare prefix match would wave `tee /dev/shm/out` straight through.
#
# The device set stays its own constant rather than folding into the temp
# prefixes: a device persists NOTHING (matched exactly, by design), while a temp
# write persists real bytes to disk (matched by prefix, over a whole shared
# directory tree) — conflating a persist-nothing sink with a real scratch file
# would blur that distinction even though both are now allowed in every mode. See
# :func:`_temp_write_prefixes` for the accepted-risk note this split preserves.
#
# Only self-resolving character-device *nodes* belong here — the caller matches the
# ``Path.resolve()``-d target, so the std-stream symlinks (`/dev/stdout` and friends
# → `/dev/fd/N` / `/proc/self/fd/N`) would never match anyway; following them to the
# real fd target is the safer behavior (a redirected fd could point at a real file).
_ALWAYS_ALLOWED_DEVICES = frozenset(
    {
        "/dev/null",
        "/dev/zero",
        "/dev/full",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
    }
)
_ALWAYS_ALLOWED_PATH_PREFIXES = _temp_write_prefixes()


def _is_always_allowed_device(path: Path) -> bool:
    """True when ``path`` is a known discard/console device (e.g. ``/dev/null``).

    Matches an *exact* allowlist (:data:`_ALWAYS_ALLOWED_DEVICES`), not a ``/dev/``
    prefix: Linux exposes writable filesystems under ``/dev/`` too — ``/dev/shm``
    (tmpfs), ``/dev/mqueue``, ``/dev/hugepages`` — where a write persists a real
    file *outside* the worktree. Only these enumerated sinks persist nothing and
    escape no worktree, so both worktree and plan mode allow them even though they
    are not plan artifacts. System temp dirs (:func:`_temp_write_prefixes`) are
    also allowed in every mode now, via the shared :func:`_is_always_allowed_scratch`
    union — but the two sets stay separate constants, since a device persists
    nothing while a temp write persists a real file; see
    :func:`_temp_write_prefixes` for the rationale that split preserves.
    """
    return str(path) in _ALWAYS_ALLOWED_DEVICES


# Commands whose path operands are *writes*, so their operands stay contained even
# after the read relaxation. In plan mode those operands must additionally be plan
# artifacts, otherwise `cp PLAN.md src/app.py` edits source without a write tool.
# Deletion counts as a write: `rm src/app.py` destroys source just as surely as
# overwriting it, and omitting it left the mildest command (`touch`) guarded while
# the most destructive one was not. `mkdir` is here for the same reason — it
# creates a directory, a write, and `mkdir /outside/dir` must not escape.
_PLAN_WRITE_COMMANDS = frozenset(
    {
        "tee",
        "cp",
        "mv",
        "touch",
        "install",
        "ln",
        "dd",
        "truncate",
        "patch",
        "rsync",
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "mkdir",
    }
)
# git subcommands that write the working tree or filesystem. `checkout`/`restore`/
# `apply`/`mv`/`rm`/`clean` overwrite or delete tracked files; `clone`/`init`/
# `worktree` create files at a positional target (`git worktree add /outside` is a
# literal worktree escape — exactly what this guard exists to prevent).
_GIT_WRITE_SUBCOMMANDS = frozenset(
    {"checkout", "restore", "apply", "mv", "rm", "clean", "clone", "init", "worktree"}
)


def _tokenize(command: str) -> list[str]:
    """Split a command into words, keeping quoted operands whole.

    ``shlex.split`` is not a shell parser: it strips quotes and leaves separators
    glued to their neighbours, so ``echo x|tee f`` came back as
    ``['echo', 'x|tee', 'f']``. Re-splitting on ``|`` afterwards fixed that but
    could not tell an operand's ``|`` from a real pipe — ``tee 'a|b' out.md`` split
    into ``a``/``;``/``b``, and the phantom ``;`` made ``out.md`` look like the next
    segment's *executable*, which is exempt from the path checks.

    ``punctuation_chars`` moves that decision into the lexer, which still knows
    what was quoted: separator runs become their own tokens only when unquoted.
    ``commenters`` is cleared to match ``shlex.split`` — otherwise a ``#`` in a
    filename or commit message would truncate the rest of the command.

    Raises:
        ValueError: The command does not tokenize (unbalanced quotes).
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _normalize_tokens(tokens: list[str]) -> list[str]:
    """Re-glue the redirect pieces :func:`_tokenize` split apart.

    ``punctuation_chars`` returns a run of ``<>|&`` as one token but breaks a
    *word* off either end, so ``2>&1`` arrives as ``2``/``>&``/``1``. Left alone,
    the ``1`` would read as an argument and the ``2`` as an operand — in plan mode
    ``tee 2>err.log`` would then be denied for "writing" a file named ``2``. Both
    ends are re-attached here so the scanner keeps seeing whole redirects:

    - fd prefix — ``['2', '>&']`` -> ``'2>&'``
    - fd dup/close tail — ``['2>&', '1']`` -> ``'2>&1'`` (names no file)
    - filename tail — ``['>&', 'out.md']`` -> ``'>'`` + ``'out.md'``, since bash's
      ``>&word`` with a filename is a real write, handled as a spaced redirect.

    A separator run that is not itself a known separator (``a ;; b``) collapses to
    ``;`` so it still ends the segment.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.isdigit() and i + 1 < len(tokens) and _REDIRECT_OP_RE.match(tokens[i + 1]):
            token += tokens[i + 1]
            i += 1
        redirect = _REDIRECT_OP_RE.match(token)
        if redirect is not None and redirect.group("amp"):
            tail = tokens[i + 1] if i + 1 < len(tokens) else None
            if tail is not None and _FD_TAIL_RE.match(tail):
                out.append(token + tail)
                i += 2
                continue
            token = redirect.group("head")
        elif token not in _SEGMENT_SEPARATORS and set(token) <= set(_SEPARATOR_CHARS) and token:
            token = ";"
        out.append(token)
        i += 1
    return out


def _looks_like_path(token: str) -> bool:
    """True when a token plausibly names a filesystem path rather than a flag/word.

    ``.`` and ``..`` count: a bare ``..`` carries no ``/`` but still escapes.
    """
    if not token or token.startswith("-"):
        return False
    if token in (".", ".."):
        return True
    return token.startswith(("/", "~")) or "/" in token


def _embedded_path(token: str) -> str | None:
    """Extract a path glued to a flag or operand (``--output=/tmp/x``, ``of=/tmp/x``).

    ``_looks_like_path`` rejects anything starting with ``-``, and ``of=/tmp/pwn``
    resolves as a *relative* path (so it lands "inside" the root). Both let a plain,
    non-obfuscated write escape, so pull the real path out of these shapes:

    - ``--output=/tmp/x`` / ``of=/tmp/x`` — value after the first ``=``
    - ``-o/tmp/x`` / ``-C/tmp/x`` — value from the first ``/`` of a short flag
    """
    if "=" in token:
        _, _, value = token.partition("=")
        return value if value and _looks_like_path(value) else None
    if token.startswith("-") and "/" in token:
        return token[token.index("/") :]
    return None


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


def _within(path: Path, base: Path) -> bool:
    """True when ``path`` is ``base`` itself or nested under it."""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _under_any(path: Path, roots: tuple[Path, ...]) -> bool:
    """True when ``path`` is (or is nested under) any root in ``roots``.

    Used for the per-tool **memory allowlist** (``allow_paths`` — see
    :func:`wade.hooks.cli._memory_allow_paths`): out-of-worktree subtrees the
    session's active tool may write to despite containment, so a guarded tool can
    persist its own memory. Deliberately narrow — only the memory subtree, never
    the tool's config/auth home.
    """
    return any(_within(path, r) for r in roots)


def _is_always_allowed_scratch(path: Path) -> bool:
    """True when ``path`` is always-allowed scratch: a known device OR a temp prefix.

    The union both write channels treat as safe to target even though it sits
    outside the worktree — a known discard/console device
    (:func:`_is_always_allowed_device`) or a system temp dir
    (:data:`_ALWAYS_ALLOWED_PATH_PREFIXES`). Single-sourced here so
    :func:`_contained` (file-path channel, via :func:`worktree_containment` and
    :func:`plan_artifact_only`) and the shell channel's plan-mode exemptions
    (:func:`shell_containment`'s ``check_redirect_target`` / ``check_non_artifact``)
    cannot drift on what counts as scratch.

    Deliberately unaware of ``worktree_root``: :func:`_contained` only needs "is
    this safe to write at all", and a path inside root is already safe via
    ``_within`` regardless of whether it also happens to match a temp prefix. The
    *plan-artifact* exemption call sites need a stricter, root-aware variant —
    see :func:`_is_scratch_outside_worktree`.
    """
    if _is_always_allowed_device(path):
        return True
    text = str(path)
    return any(text.startswith(prefix) for prefix in _ALWAYS_ALLOWED_PATH_PREFIXES)


def _is_temp_root(path: Path) -> bool:
    """True when ``path`` is exactly a system temp dir's root (``/tmp``, ``$TMPDIR``).

    Deliberately **not** folded into :func:`_is_always_allowed_scratch`, whose
    trailing-separator prefix match is used by every *write* check (redirect
    targets, write-command operands, the file-path channel) — treating the
    temp root itself as a valid write target there would let a destructive
    command target the shared directory wholesale (``rm -rf /tmp``, ``mv /tmp
    x``), a far larger blast radius than a single scratch file and one every
    other same-user process/session sharing that temp dir would feel. This
    predicate exists solely so ``cd``/``pushd`` — pure navigation, not a write
    — can land on the bare temp dir (``cd /tmp``) without also having to
    accept it as a write-command operand.
    """
    text = str(path)
    return any(text == prefix.rstrip("/") for prefix in _ALWAYS_ALLOWED_PATH_PREFIXES)


def _is_scratch_outside_worktree(path: Path, root: Path) -> bool:
    """True when ``path`` is always-allowed scratch AND lies outside ``root``.

    Guards the plan-artifact exemptions specifically (:func:`plan_artifact_only`;
    :func:`shell_containment`'s ``check_redirect_target`` / ``check_non_artifact``):
    if ``worktree_root`` itself resolves under a system temp dir — an ephemeral
    clone, a CI job, or a configured temp worktree directory — every path *inside*
    the worktree also matches the temp-prefix test in
    :func:`_is_always_allowed_scratch`. Without the ``not _within(path, root)``
    guard here, every in-worktree source write in such a worktree would be
    wrongly exempted from the plan-artifact allowlist, effectively disabling the
    plan-session write guard for that worktree.

    Baseline containment (:func:`_contained`) does not need this distinction: a
    path inside ``root`` is already allowed via ``_within`` regardless of whether
    the scratch check fires first, so the two never disagree there — only the
    artifact-exemption call sites, which treat "is scratch" as "is *outside* the
    worktree and therefore not subject to the artifact allowlist", need the
    ``root`` check.
    """
    return _is_always_allowed_scratch(path) and not _within(path, root)


def _contained(path: Path, root: Path, allow_paths: tuple[Path, ...] = ()) -> bool:
    """True when ``path`` is inside ``root``, always-allowed scratch, or a memory root.

    Always-allowed scratch (:func:`_is_always_allowed_scratch`) covers known
    discard/console devices (:data:`_ALWAYS_ALLOWED_DEVICES` — ``/dev/null`` …,
    matched exactly) and system temp dirs (``/tmp``, ``$TMPDIR`` via
    :data:`_ALWAYS_ALLOWED_PATH_PREFIXES`, matched by prefix) — shared scratch space
    that is safe to write even though it is outside ``root``. ``allow_paths`` are the
    active tool's memory subtrees (:func:`_under_any`), permitted despite being
    outside the worktree.
    """
    if _is_always_allowed_scratch(path):
        return True
    return _within(path, root) or _under_any(path, allow_paths)


def shell_containment(
    event: HookEvent,
    *,
    worktree_root: Path,
    plan_mode: bool = False,
    allow_paths: tuple[Path, ...] = (),
) -> HookDecision:
    """Deny a shell command that writes outside the worktree (or outside plan artifacts).

    crossby routes shell tool calls (``bash``, ``shell``, ``exec_command``,
    ``powershell``, ``run_command``) through :attr:`HookEvent.command` and reports
    ``is_write=False`` for them, so :func:`worktree_containment` and
    :func:`plan_artifact_only` never inspect them. Without this predicate a single
    ``printf ... > ../main-repo/src/app.py`` defeats both guards.

    **Reads outside the worktree are allowed; only writes are contained.** Reading
    a sibling repo (``cat ../crossby/x``, ``grep -r foo ../crossby``,
    ``git -C ../crossby log``) never mutates state, so a read operand may resolve
    anywhere. The guard's job is to keep *writes* inside the root.

    **System temp dirs and discard/console devices are write exceptions, in every
    mode.** ``/tmp``, ``$TMPDIR``, and a small exact allowlist of devices
    (``/dev/null``, ``/dev/zero`` …) resolve as "contained" even though they sit
    outside the root (:func:`_is_always_allowed_scratch`, unioning
    :func:`_temp_write_prefixes` / :data:`_ALWAYS_ALLOWED_DEVICES`). The device
    allowlist is *exact*, not a ``/dev/`` prefix — Linux mounts writable filesystems
    there too (``/dev/shm``), so ``tee /dev/shm/out`` stays denied. Both temp
    writes and device writes are additionally exempt from the plan-artifact rule in
    plan mode (:func:`_is_always_allowed_scratch`, checked in
    ``check_redirect_target`` / ``check_non_artifact`` below) — devices persist
    nothing, and temp writes are accepted as world-shared scratch already reachable
    by any local process (see :func:`_temp_write_prefixes` for the cross-session
    accepted-risk note this widening carries).

    **The active tool's memory allow-root is also writable.** ``allow_paths`` (the
    tool's own allow-root for this session — see
    :func:`wade.hooks.cli._memory_allow_paths`) is honored for **redirect
    targets** and **write-command operands**, so a shell redirect or a write
    command targeting it is contained even though it lives outside the root
    (a glued flag value pointing there also passes, incidentally, via the
    generic glued-path check in rule 5 below — that check cannot distinguish a
    write flag from a read one, so it does not enforce the "redirect targets and
    write-command operands only" framing as a hard boundary; this only narrows
    what such a command is denied for). In plan mode a memory path is additionally exempt from the
    plan-artifact rule — checked **before** :func:`_is_plan_artifact_path` (which
    reports any out-of-root path, memory included, as a non-artifact), so a
    plan-mode ``echo x > ~/.claude/.../y.md`` is allowed. ``cd``/``pushd`` and
    every git directory-redirect flag (``-C``, ``--work-tree=``, ``--git-dir=``)
    stay strict (memory writes are direct-path, not cd-relative), so the bypass
    never loosens directory context.

    Rules, in order (any match denies):

    1. The command does not tokenize (unbalanced quotes) — **fail closed**.
    2. An *output* redirect target (``>``, ``>>``, ``2>``, ``>&file``, glued or
       spaced) resolves outside the root. An *input* redirect (``< target``) only
       reads, so its target may live anywhere. Pure fd duplication (``2>&1``,
       ``>&-``) names no file and is skipped.
    3. ``cd`` / ``pushd`` targets a path outside the root — it would rebase every
       later relative write in the same command. A *bare* ``cd`` (or ``cd -``)
       counts: it lands in ``$HOME``/``$OLDPWD``, equally outside.
    4. An operand of a *write* command resolves outside the root. Write commands
       are :data:`_PLAN_WRITE_COMMANDS` (``tee``/``cp``/``mv``/``touch``/``mkdir``
       …), git write subcommands (:data:`_GIT_WRITE_SUBCOMMANDS`:
       ``checkout``/``clean``/``clone``/``init``/``worktree`` …), and in-place
       editors (:data:`_IN_PLACE_COMMANDS` carrying an ``-i`` flag). A *read*
       command's path operand is not checked — reads may resolve anywhere. The
       *executable* position of each segment is exempt, since binaries legitimately
       live in ``/usr/bin``, ``/bin``, ``/opt/homebrew/bin`` and denying those
       breaks every session. Character devices (``/dev/null``) are exempt too.
    5. A path glued to a flag or operand (``--output=/tmp/x``, ``-o/tmp/x``,
       ``of=/tmp/x``) resolves outside the root. The glued form is contained in
       *all* modes: a tokenizer cannot tell a glued read flag from a glued write
       flag, so both are denied — this conservatively denies a few glued *reads*
       too. Every git directory-redirect flag's glued/``=``-joined form — glued
       ``-C<dir>`` (e.g. ``git -C/tmp/other``), ``--work-tree=<dir>``,
       ``--git-dir=<dir>`` — is carved out of this rule and handled by rule 6
       instead.
    6. Any git directory-redirect flag — ``-C``, ``--work-tree``, or
       ``--git-dir``, spaced *or* glued/``=``-joined (git's own parser accepts
       both forms identically for all three) — where ``<dir>`` is outside the
       root **and** a git write subcommand (:data:`_GIT_WRITE_SUBCOMMANDS`)
       follows in the same segment. ``git -C <outside> log``/``show``/``status``/
       ``diff`` (read subcommands) stay allowed; ``git -C <outside> clean`` would
       otherwise delete untracked files outside with no later path operand to
       catch it — ``--work-tree``/``--git-dir`` are functionally the same
       redirection, just spelled differently. Checked with **strict** ``_within``
       against ``root`` only — neither ``allow_paths`` (the memory exception,
       rule 4/5's `_contained` call) nor the scratch exemption
       (:func:`_is_always_allowed_scratch`) applies to any of these six spellings,
       so ``git -C /tmp/x clean -fd`` stays denied even though a direct write to
       ``/tmp/x`` is scratch-exempt: a git write scoped to a redirected directory
       can touch every file under ``<dir>``, not just a single scratch/memory
       write. The identical blast radius reached the plain way — a prior
       ``cd``/``pushd`` to a directory outside root (necessarily scratch; a
       non-scratch outside target already denies at rule 3) redirects git's
       implicit working dir for the rest of the *whole command line*, not just
       the current segment (a real shell's cwd persists across ``&&``/``;``/``|``,
       unlike a per-invocation ``-C`` flag) — so ``cd /tmp/x && git clean -fd``
       gets the same denial even though it has no ``-C`` flag and ``clean`` has no
       path operand of its own to catch. A **same-segment** directory-redirect
       flag that resolves *inside* root overrides the buffered scratch ``cd`` for
       that one invocation, exactly like a real shell: ``cd /tmp/x && git -C
       /repo/wt clean -fd`` is allowed — git's own ``-C`` takes precedence over
       the shell's cwd, so the write lands in-root regardless of the earlier
       ``cd``. Tracked separately from ``git_dir_redirect_outside_token`` (which
       only fires for an *outside*-root flag) via ``git_dir_redirect_seen_in_root``,
       since "no redirect flag this segment" and "redirect flag present and
       in-root" both leave the outside-token at ``None`` but must be told apart.
    7. In plan mode only: any output redirect, an in-place edit flag (``sed -i``,
       ``--in-place``) on a command that *has* one (:data:`_IN_PLACE_COMMANDS` —
       ``grep -i`` and ``ls -i`` are ordinary read flags), or an operand of a write
       command whose path is not a plan artifact — **except always-allowed scratch
       outside the worktree** (a temp path or a device node like ``/dev/null``),
       which stays allowed in plan mode (:func:`_is_scratch_outside_worktree`): a
       device persists nothing, and a temp write outside the worktree is accepted
       world-shared scratch, so neither need be a plan artifact. The check is
       root-aware, not the plain :func:`_is_always_allowed_scratch`: a
       ``worktree_root`` that itself resolves under a system temp dir would
       otherwise make every in-worktree path match the temp prefix too, exempting
       ordinary source writes from the artifact rule.

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
    - **Wrapped write commands** — ``sudo rm /outside``, ``env FOO=bar cp a /outside``,
      ``xargs rm``, ``nice``/``nohup``/``time``/``command``/``doas``/``timeout`` …
      The wrapper becomes the detected ``command_name``, so the real writer behind it
      is invisible and its operands read as *reads*. Unwrapping is a clean fix (it
      would not re-block any read), but is deliberately not done here — the guard is
      best-effort, and unwrapping a partial wrapper list gives a false sense of
      completeness while ``python -c`` and the writers below still escape.
    - **Unenumerated write commands** — ``zip /outside/a.zip``, ``gzip``, ``split``,
      ``git bundle create /outside``, ``git format-patch -o /outside``,
      ``git archive -o /outside``. Only :data:`_PLAN_WRITE_COMMANDS` /
      :data:`_GIT_WRITE_SUBCOMMANDS` are contained; a writer outside those sets has
      its operands treated as reads. The sets cover the commands an agent actually
      produces; some others (``git bundle``/``git worktree`` have read *and* write
      subcommands) cannot be blanket-added without re-blocking their read forms.
    - **Spaced output flags on non-write commands** — ``curl -o /outside``,
      ``gcc -o /outside``, ``wget -O /outside``, ``sort --output /outside``. The
      *glued* forms stay caught by rule 5; the spaced form is a genuine write-escape
      the relaxation gives up, because a token after ``-o`` is a read as often as a
      write (``ls -o ../dir``) and a blanket "``-o`` value is a write target" rule
      would re-block the reads this guard now allows.
    - **Directory-context flags on non-git extractors/builders** —
      ``tar -C /outside -xf a.tar``, ``unzip -d /outside a.zip``, ``make -C /outside``.
      ``git -C`` is fixed (rule 6) only because git's write subcommands are
      enumerated; these have no create-vs-extract enumeration to key on.
    - **Conditional-write ``find``** — ``find ../outside -delete`` /
      ``-exec rm {} +``. ``find``'s positional argument is normally a *read* root
      (``find ../crossby -name '*.py'``), so listing ``find`` as a write command
      would re-block the reads this guard now allows; the write targets are the
      *found* files, not a single operand, so there is no cheap buffered fix.
    - **Plan mode only:** a write command outside :data:`_PLAN_WRITE_COMMANDS`, or
      an in-place editor outside :data:`_IN_PLACE_COMMANDS`. Neither can escape the
      root (rule 4 still contains enumerated writes) — they can only write a
      non-artifact inside it.
    - **``cd`` persisting across separate tool calls, not just within one command
      string.** ``cwd_outside_root_token`` (rule 6) only tracks a ``cd``/``pushd``
      seen *while scanning this one ``event.command``* — it starts fresh on every
      :func:`shell_containment` call. Some tools' shell tool (e.g. Claude Code's
      Bash) persist the working directory *across* separate tool calls, so
      ``cd /tmp/x`` in one call followed by a bare ``git clean -fd`` (no ``-C``, no
      ``cd``) in the **next** call reports ``event.cwd == /tmp/x`` — which ``base``
      trusts unconditionally (see the top of this function) — and escapes with no
      operand or directory-redirect flag in *that* command for any rule to catch.
      Pre-existing (the unconditional ``base = cwd_resolved`` trust predates this
      exemption work), not something this guard's single-command tokenizer can fix
      without tracking cwd across calls — a candidate follow-up, not a regression
      here.

    Args:
        event: Normalized hook event; only :attr:`HookEvent.command` is read.
        worktree_root: The session worktree; writes must stay inside it.
        plan_mode: Apply the stricter plan-session rules (rule 5).
        allow_paths: Out-of-worktree memory subtrees the active tool may write to
            (redirect targets / write-command operands only; empty = no bypass).
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
        tokens = _tokenize(command)
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
            f"outside the worktree at '{root}' — only paths inside the worktree can be "
            "verified as safe write targets. Reads outside are fine; rewrite this as a "
            "read, or keep the write inside your worktree."
        )

    def check_redirect_target(target: str, op: str) -> HookDecision | None:
        """Containment (writes) + plan-artifact (plan mode) for an output redirect."""
        # ``<`` only reads its target, so a ``<`` outside the worktree is fine.
        if op == "<":
            return None
        resolved = _resolve_shell_path(target, base=base)
        if resolved is None or not _contained(resolved, root, allow_paths):
            return deny_outside("redirect target", target)
        # Memory membership FIRST: a memory path is outside the root, so
        # ``_is_plan_artifact_path`` reports it as "not an artifact" and would deny
        # it — the ordering trap this check exists to avoid.
        if (
            plan_mode
            and not _under_any(resolved, allow_paths)
            and not _is_plan_artifact_path(resolved, root)
            and not _is_scratch_outside_worktree(resolved, root)
        ):
            return HookDecision.deny(
                f"BLOCKED by plan-session guard: redirecting output to '{target}' "
                "would write a non-artifact file without going through a write tool. "
                "In plan mode only plan artifacts (PLAN.md, PLAN-*.md, prompt.txt, "
                ".transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, "
                ".wade/plans/*) may be written."
            )
        return None

    def check_non_artifact(what: str, token: str, resolved: Path) -> HookDecision | None:
        """Plan mode: a contained path a write command targets must be an artifact.

        A memory path (``allow_paths``) is exempt — checked before
        :func:`_is_plan_artifact_path`, which reports any out-of-root path (memory
        included) as a non-artifact.
        """
        if (
            not plan_mode
            or _under_any(resolved, allow_paths)
            or _is_plan_artifact_path(resolved, root)
            or _is_scratch_outside_worktree(resolved, root)
        ):
            return None
        return HookDecision.deny(
            f"BLOCKED by plan-session guard: {what} would write to '{token}', which is "
            "not a plan artifact. In plan mode only plan artifacts (PLAN.md, PLAN-*.md, "
            "prompt.txt, .transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, "
            ".wade/plans/*) may be written."
        )

    expect_executable = True
    command_name: str | None = None
    is_write_command = False
    awaiting_cd_target = False
    pending_redirect: str | None = None
    # Spaced git directory-redirect flag (``-C <dir>``, ``--work-tree <dir>``,
    # ``--git-dir <dir>``): the next token is the directory (awaiting flag), and
    # once seen we remember it here iff it resolves outside the root. A read like
    # ``git -C <outside> log`` is fine; only a later git *write* subcommand
    # denies. Shared with the glued (``-C<dir>``) and ``=``-joined
    # (``--work-tree=<dir>``, ``--git-dir=<dir>``) forms below — all six
    # spellings are directory-redirect flags with the same escape shape.
    awaiting_git_dir_redirect = False
    git_dir_redirect_outside_token: str | None = None
    # True once THIS segment has seen a `-C`/`--work-tree`/`--git-dir` flag that
    # resolved *inside* root (spaced or glued/``=``-joined). Distinguishes "no
    # redirect flag in this segment" from "redirect flag present and in-root" —
    # both leave `git_dir_redirect_outside_token` at ``None``, but only the
    # latter should override a stale `cwd_outside_root_token` from an earlier
    # segment's scratch ``cd``: git's own `-C` overrides the shell's cwd for
    # that invocation, so ``cd /tmp/x && git -C /repo/wt clean -fd`` must not be
    # denied on the buffered ``cd`` alone. Reset at segment separators, same as
    # `git_dir_redirect_outside_token`.
    git_dir_redirect_seen_in_root = False
    # A successful `cd`/`pushd` to a directory outside the root (necessarily
    # always-allowed scratch — a non-scratch outside target denies immediately
    # below) redirects git's implicit working dir for every later segment of this
    # SAME shell command, exactly like a `-C`/`--work-tree`/`--git-dir` flag —
    # `cd /tmp/x && git clean -fd` has the identical blast radius as
    # `git -C /tmp/x clean -fd`. Unlike `git_dir_redirect_outside_token`, this is
    # NOT reset at segment separators: a real shell's cwd persists across
    # `&&`/`;`/`|`, so the buffered directory must too. Cleared when a later `cd`
    # returns inside the root.
    cwd_outside_root_token: str | None = None

    def end_segment() -> HookDecision | None:
        """A segment that ended while `cd` still wanted a target went to $HOME."""
        if awaiting_cd_target:
            return HookDecision.deny(
                f"BLOCKED by worktree guard: a bare '{command_name}' changes directory "
                f"to your home directory, which is outside the worktree at '{root}'. "
                "Later relative paths in the same command would resolve there."
            )
        return None

    for token in _normalize_tokens(tokens):
        if token in _SEGMENT_SEPARATORS:
            denial = end_segment()
            if denial is not None:
                return denial
            expect_executable = True
            command_name = None
            is_write_command = False
            awaiting_cd_target = False
            pending_redirect = None
            awaiting_git_dir_redirect = False
            git_dir_redirect_outside_token = None
            git_dir_redirect_seen_in_root = False
            continue

        redirect = _REDIRECT_RE.match(token)
        if redirect:
            op = redirect.group("op")
            target = redirect.group("target")
            if not target:
                # Spaced form (``> path``) — the target is the next token.
                pending_redirect = op
            elif _FD_DUP_RE.match(target):
                # ``2>&1`` / ``>&-`` duplicate or close an fd and name no file.
                pending_redirect = None
            else:
                # Glued form (``>/tmp/x``, and bash's ``>&file`` which is a real write).
                pending_redirect = None
                denial = check_redirect_target(target.lstrip("&"), op)
                if denial is not None:
                    return denial
            continue

        if pending_redirect is not None:
            op, pending_redirect = pending_redirect, None
            if not _FD_DUP_RE.match(token):
                denial = check_redirect_target(token.lstrip("&"), op)
                if denial is not None:
                    return denial
            continue

        if expect_executable:
            # Only the basename matters: `/bin/cd` and `cd` are the same builtin,
            # and the executable's own path is exempt from containment (rule 4).
            command_name = posixpath.basename(token)
            expect_executable = False
            awaiting_cd_target = command_name in ("cd", "pushd")
            is_write_command = command_name in _PLAN_WRITE_COMMANDS
            continue

        if awaiting_cd_target:
            # Every non-flag argument to cd/pushd is a directory, including a bare
            # `..` that `_looks_like_path` would not otherwise recognize. `-`/`~-`
            # jump to $OLDPWD, which we cannot resolve — treat as an escape.
            if token.startswith("-") and token != "-":
                continue
            if token in ("-", "~-"):
                return deny_outside(f"{command_name} target", token)
            awaiting_cd_target = False
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not (_contained(resolved, root) or _is_temp_root(resolved)):
                return deny_outside(f"{command_name} target", token)
            # Allowed either because it's in-worktree or (the only other way
            # `_contained` passes) always-allowed scratch — track which, so a
            # later git write subcommand in this same command line can be denied
            # the same way one reached via `-C` into scratch already is.
            cwd_outside_root_token = None if _within(resolved, root) else token
            # A real shell resolves later relative paths against the new cwd, not
            # the original one — without this, a relative git directory-redirect
            # flag (`git -C .`, `--work-tree .`, `--git-dir .`) after `cd`ing into
            # scratch would resolve against the stale `base` instead of the
            # scratch dir, wrongly reporting the redirect as in-root and bypassing
            # the `cwd_outside_root_token` deny above.
            base = resolved
            continue

        # Spaced git directory-redirect flag: buffer the directory from the NEXT
        # token. A read through it is fine (``git -C ../crossby log``,
        # ``git --work-tree ../crossby log``); a later git *write* subcommand in
        # the same segment turns a buffered *outside* dir into a denial. Checked
        # with strict ``_within`` against ``root`` only — NOT ``_contained`` (no
        # ``allow_paths``, no temp/device scratch exemption) — below: a git write
        # subcommand scoped to a directory can touch every file under it, a far
        # larger blast radius than a single scratch file, so a redirect into
        # ``/tmp``/``$TMPDIR`` stays denied even though a direct write there is
        # always-allowed scratch (:func:`_is_always_allowed_scratch`). git's own
        # parser (git.c) accepts ``-C``, ``--work-tree``, and ``--git-dir`` both
        # spaced and ``=``-joined identically, so all three must be buffered here
        # too — not just ``-C`` (a prior review already caught the missing
        # ``=``-joined ``--work-tree``/``--git-dir`` forms; this closes the
        # remaining spaced-without-``=`` gap for those same two flags).
        if awaiting_git_dir_redirect:
            awaiting_git_dir_redirect = False
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not _within(resolved, root):
                # Sticky once set: does NOT get cleared by a later in-root flag
                # in the same segment. A naive "last flag wins" reset is unsafe
                # here — repeated relative ``-C`` values chain from the
                # *preceding* ``-C`` (not from ``base``), so a later relative
                # ``-C .`` after an outside ``-C`` can resolve back to root
                # while git's real effective directory is still outside
                # (``git -C /tmp/x -C . clean -fd``); and ``--work-tree``/
                # ``--git-dir`` are independent settings, not alternatives, so
                # an in-root ``--git-dir`` does not override an outside
                # ``--work-tree`` (``git --work-tree=/outside
                # --git-dir=/repo/wt/.git clean -fd`` still cleans
                # ``/outside``). Correctly resolving either case needs
                # per-flag-type effective-directory tracking, not a shared
                # last-one-wins token — out of scope here; staying strict
                # (fail closed) is the safe trade-off, at the cost of
                # over-denying the narrower, legitimate `-C a -C b` case where
                # the final absolute `-C` truly does replace the first.
                git_dir_redirect_outside_token = token
            else:
                git_dir_redirect_seen_in_root = True
            continue

        if command_name == "git" and token in ("-C", "--work-tree", "--git-dir"):
            awaiting_git_dir_redirect = True
            continue

        # Glued ``-C<dir>`` (no space, e.g. ``-C..`` or ``-C/tmp/other``) and the
        # ``=``-joined ``--work-tree=<dir>`` / ``--git-dir=<dir>`` forms:
        # resolved directly from the same token instead of falling through to
        # the generic `_embedded_path` branch below. That branch checks
        # ``allow_paths`` (and, via `_contained`, temp/device scratch), which
        # would let e.g. ``git -C<memory-dir> clean -fd`` reach the memory
        # exception or ``git -C/tmp/x clean -fd`` reach the scratch exception —
        # a write reached through any directory-redirect flag can touch every
        # file under ``<dir>``, not just a direct memory/scratch write; all
        # spellings must stay as strict (``_within``, root only) as spaced
        # ``-C``.
        if command_name == "git" and token.startswith("-C") and token != "-C":
            git_dir_redirect = token[2:]
        elif command_name == "git" and token.startswith(("--work-tree=", "--git-dir=")):
            _, _, git_dir_redirect = token.partition("=")
        else:
            git_dir_redirect = None
        if git_dir_redirect is not None:
            resolved = _resolve_shell_path(git_dir_redirect, base=base)
            if resolved is None or not _within(resolved, root):
                # See the spaced-flag branch above: sticky, not cleared later.
                git_dir_redirect_outside_token = git_dir_redirect
            else:
                git_dir_redirect_seen_in_root = True
            continue

        # In-place editors (`sed -i`, `perl -i`, `yq -i`, …) rewrite their operands.
        # Plan mode denies the flag outright; *every* mode marks the command a writer
        # so its later operands are contained — otherwise the read relaxation would
        # let `sed -i '' s/a/b/ ../main/file` write outside in implementation mode.
        if command_name in _IN_PLACE_COMMANDS and _IN_PLACE_FLAG_RE.match(token):
            if plan_mode:
                return HookDecision.deny(
                    f"BLOCKED by plan-session guard: in-place editing ('{token}') is not "
                    "allowed in plan mode — it rewrites a file without going through a "
                    "write tool. Only plan artifacts may be written."
                )
            is_write_command = True
            continue

        if command_name == "git" and token in _GIT_WRITE_SUBCOMMANDS:
            is_write_command = True
            # A git write subcommand after a directory-redirect flag pointing
            # outside the root (``git -C /outside clean -fd``, ``git
            # -C/outside clean -fd``, ``git --work-tree=/outside clean -fd``,
            # ``git --git-dir=/outside clean -fd``) writes outside with no later
            # path operand to catch it — deny on the buffered directory.
            if git_dir_redirect_outside_token is not None:
                return deny_outside("git directory-redirect flag", git_dir_redirect_outside_token)
            # Same blast radius reached the plain way: `cd /tmp/x && git clean -fd`
            # has no `-C` flag and no path operand for `clean` to catch, but a
            # prior `cd` already moved git's implicit working dir outside root.
            # Skipped when THIS segment's own directory-redirect flag resolved
            # inside root (`git_dir_redirect_seen_in_root`): git's `-C` overrides
            # the shell's cwd for that invocation, so `cd /tmp/x && git -C
            # /repo/wt clean -fd` is a legitimate in-root write and must not be
            # denied on the stale buffered `cd` alone. A redirect flag resolving
            # *outside* root already returned above via
            # `git_dir_redirect_outside_token`, so reaching here with the flag
            # unset means either no redirect flag was present this segment (cwd
            # applies) or it was present and in-root (cwd is overridden).
            if cwd_outside_root_token is not None and not git_dir_redirect_seen_in_root:
                return deny_outside("prior 'cd' target", cwd_outside_root_token)

        # A path glued to a flag or operand (``--output=/tmp/x``, ``of=/tmp/x``)
        # is invisible to `_looks_like_path`, and `of=/tmp/x` would resolve
        # *relative*. Contained in every mode: a tokenizer cannot tell a glued
        # read flag from a glued write flag, so both are denied. Every git
        # directory-redirect flag (``-C<dir>``, ``--work-tree=<dir>``,
        # ``--git-dir=<dir>``) is handled above instead, before this branch.
        embedded = _embedded_path(token)
        if embedded is not None:
            resolved = _resolve_shell_path(embedded, base=base)
            if resolved is None or not _contained(resolved, root, allow_paths):
                return deny_outside("path argument", embedded)
            if is_write_command:
                denial = check_non_artifact(f"'{command_name}'", embedded, resolved)
                if denial is not None:
                    return denial
            continue

        if is_write_command and not token.startswith("-"):
            # Only a *write* command's operands are contained; a read command's path
            # operand may resolve anywhere — reading a sibling repo never mutates
            # state. Path-like or not: plan mode must catch `tee app.py`, not just
            # `tee src/app.py`.
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not _contained(resolved, root, allow_paths):
                return deny_outside("path argument", token)
            denial = check_non_artifact(f"'{command_name}'", token, resolved)
            if denial is not None:
                return denial

    return end_segment() or HookDecision.allow()


def _is_plan_artifact_path(resolved: Path, root: Path) -> bool:
    """True when an already-contained absolute path is an allowed plan artifact."""
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return False
    return _is_plan_artifact(relative.as_posix())


def session_complete(
    event: HookEvent,
    *,
    worktree_root: Path,
    commits_ahead: int = 0,
    done_marker_present: bool = False,
) -> HookDecision:
    """Stop-hook guard: nudge (once) to finalize via ``done`` when work is unfinished.

    Returns a ``deny`` decision — which the Stop path renders as *block the stop
    and feed the reason back* — when the branch has commits ahead of its base yet
    no current ``done`` marker exists, so the workflow's closing step (running
    ``done``) is enforced rather than merely requested by the skill. Otherwise
    allow.

    Keyed on the **same completion fact** the ``done`` command writes — the
    sha-keyed ``.wade/done@<HEAD>`` marker — so there is no split-brain between
    "did the closing artifact exist" (the old PR-SUMMARY check) and "did the
    session actually finalize". The git facts (``commits_ahead`` /
    ``done_marker_present``) are computed by the ``wade-hook`` CLI and passed in,
    keeping this predicate pure and off the hot per-edit import path.

    Allow conditions, in order:

    - ``event.stop_hook_active`` — Claude sets this once its Stop hook has fired
      and blocked; other tools do not send it. Prevents looping.
    - ``commits_ahead == 0`` — no authored work to finalize (adopts #318's
      higher-signal condition, so an early "stopping to ask a question" turn does
      not trigger the nudge).
    - ``done_marker_present`` — the session was already finalized via ``done``.
    - the ``.wade/stop-nudged`` single-shot marker — the ``wade-hook`` CLI writes
      it after this guard blocks, so the second Stop is allowed on *any* tool,
      not just Claude. This predicate only *reads* the marker; the CLI owns the
      write (keeping this a side-effect-free decision).

    The message is deliberately ignorable so a legitimate pause (e.g. stopping to
    ask the user a question) costs at most one gentle nudge.

    The hook is only installed in wade worktree sessions on Stop-capable tools,
    so no session-detection is needed here.
    """
    if event.stop_hook_active:
        return HookDecision.allow()

    if commits_ahead == 0:
        return HookDecision.allow()  # no work to finalize (#318 signal)

    if done_marker_present:
        return HookDecision.allow()  # completed via done

    if stop_nudge_present(worktree_root):
        return HookDecision.allow()  # already nudged once this worktree

    return HookDecision.deny(
        "Before finishing: run `wade implementation-session done` (or "
        "`wade review-pr-comments-session done`) to sync, push, and finalize the "
        "PR. If you are pausing to ask a question or are still mid-task, disregard "
        "this and continue."
    )


def plan_complete(
    event: HookEvent,
    *,
    worktree_root: Path,
    has_valid_plan: bool = False,
) -> HookDecision:
    """Stop-hook guard: nudge (once) when a plan session produced no valid plan.

    Mirrors :func:`session_complete` — a pure predicate fed a fact the
    ``wade-hook`` CLI computes (here ``has_valid_plan``: does the plan dir hold at
    least one ``PLAN*.md`` with no error-level diagnostics). Returns a ``deny``
    (which the Stop path renders as *block the stop and feed the reason back*) when
    the session is about to end with nothing wade can turn into an issue, so the
    plan-phase requirement is enforced rather than left to the agent's goodwill.

    Allow conditions, in order (same shape as ``session_complete``):

    - ``event.stop_hook_active`` — Claude sets this once its Stop hook has fired
      and blocked; other tools do not send it. Prevents looping.
    - ``has_valid_plan`` — the session already wrote a usable plan; nothing to nudge.
    - the ``.wade/stop-nudged`` single-shot marker — the CLI writes it after this
      guard blocks, so the second Stop is allowed on *any* tool, not just Claude.
      This predicate only *reads* the marker; the CLI owns the write. The marker
      is shared with ``session_complete`` because a worktree is a plan worktree or
      an impl worktree, never both, so the two guards never collide on it.

    The message is deliberately ignorable so a legitimate pause (e.g. stopping to
    ask the user a question mid-plan) costs at most one gentle nudge. The hook is
    only installed in plan-session worktrees on Stop-capable tools, so no
    session-detection is needed here.
    """
    if event.stop_hook_active:
        return HookDecision.allow()

    if has_valid_plan:
        return HookDecision.allow()  # a usable plan already exists

    if stop_nudge_present(worktree_root):
        return HookDecision.allow()  # already nudged once this worktree

    return HookDecision.deny(
        "Before finishing: write at least one valid `PLAN*.md` (a title with a "
        "conventional-commit prefix, e.g. `feat: ...`, plus a `## Complexity` "
        "section) to the plan directory so wade can create the issue. If you are "
        "pausing to ask a question or are still mid-plan, disregard this and continue."
    )


# --- Session-start context injection (#351) ---------------------------------

# Hard cap on the injected context payload. Deliberately small: this is a
# compact, say-it-once reminder re-injected on every SessionStart source
# (startup/resume/compact/clear/fork) to keep baseline adherence high — NOT a
# second copy of the launch-loaded workflow. The builder truncates to this so the
# acceptance budget can never regress.
_SESSION_CONTEXT_MAX_CHARS = 800

# Cap the echoed issue title so one very long title cannot dominate the budget.
_SESSION_CONTEXT_TITLE_MAX = 140

# ``write_plan_md`` writes ``# Issue #<id>: <title>`` as PLAN.md's first line.
_ISSUE_LINE_RE = re.compile(r"^#\s*Issue\s+#(\d+):\s*(.+)$")

# Phase -> the prompt template holding that phase's static instruction prose.
# The prose is AI-facing content, so it is sourced from ``templates/prompts/``
# (the repo's prompt source-of-truth per AGENTS.md "Prompts as .md Templates")
# rather than hard-coded here; :func:`session_start_context` stays limited to
# loading that prose, prepending the dynamic issue line, branding, and capping.
_SESSION_CONTEXT_TEMPLATES: dict[SessionPhase, str] = {
    SessionPhase.IMPLEMENT: "session-start-implement.md",
    SessionPhase.REVIEW: "session-start-review.md",
    SessionPhase.PLAN: "session-start-plan.md",
}


def _parse_issue_heading(path: Path) -> tuple[str, str] | None:
    """Return ``(issue_id, title)`` from ``path``'s first line, or ``None``.

    The first line must match ``# Issue #<id>: <title>`` — the heading shape
    ``write_plan_md`` emits into ``PLAN.md`` (impl/review) and ``plan_service``
    persists into ``.wade/plan-issue.md`` (plan). Reads the file directly — never
    through the ``wade.git`` layer, whose ``git.run`` debug line would print to the
    lean ``wade-hook`` entry's stdout and corrupt its decision-JSON contract (the
    #349 gotcha). A missing file, an unreadable one (including a non-UTF-8 /
    binary file), or a non-matching first line all yield ``None`` so the caller
    omits the issue line rather than failing — the contract holds on this
    function's own terms, not by accident of the caller's outer catch-all.
    """
    try:
        # utf-8-sig so a stray BOM on the first line never suppresses the issue
        # ref (decodes plain utf-8 unchanged when no BOM is present). A binary /
        # non-UTF-8 file raises UnicodeDecodeError (a ValueError) on read, not
        # OSError — caught here so the fail-open promise above is self-contained.
        with path.open(encoding="utf-8-sig") as fd:
            first_line = fd.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    match = _ISSUE_LINE_RE.match(first_line)
    if match is None:
        return None
    return match.group(1), match.group(2).strip()


def _issue_line(parsed: tuple[str, str]) -> str:
    """Render the ``Issue #<id> — <title>`` context line, capping a long title."""
    issue_id, title = parsed
    if len(title) > _SESSION_CONTEXT_TITLE_MAX:
        title = title[: _SESSION_CONTEXT_TITLE_MAX - 1].rstrip() + "…"
    return f"Issue #{issue_id} — {title}"


def _stale_base_line(count: int, phase: SessionPhase) -> str:
    """Render the fixed, short ``N commits behind`` staleness warning line (#407).

    Deliberately a single line of ~100 chars so it fits well under
    :data:`_SESSION_CONTEXT_MAX_CHARS` and is emitted *first*, meaning the tail-truncation
    below can never drop it or land its ``…`` mid-warning.

    The remedy points at the *phase's own* sync command — ``review-pr-comments-session`` for
    a REVIEW worktree, ``implementation-session`` otherwise — since both session groups
    provide ``sync`` and each passes the ``session_type`` its conflict-hint/stash handling
    needs (#408 review).
    """
    plural = "S" if count != 1 else ""
    sync_cmd = (
        "wade review-pr-comments-session sync"
        if phase is SessionPhase.REVIEW
        else "wade implementation-session sync"
    )
    return (
        f"⚠️ BRANCH IS {count} COMMIT{plural} BEHIND BASE — startup catchup did not advance "
        f"it; do NOT start work until you run `{sync_cmd}`."
    )


def session_start_context(worktree_root: Path, phase: SessionPhase) -> str | None:
    """Build the compact context re-injected at session start / resume / compaction.

    Returned as ``additionalContext`` (per each tool's dialect, by crossby's
    ``emit_decision``) so the task ref and the phase's closing gate stay in context
    over a long session — the largest single context loss being *compaction*, which
    fires ``SessionStart`` with ``source: "compact"``.

    Content by phase (authored *distinctly* from the always-loaded SKILL.md —
    these are pointers/reminders, not restatements — and sourced from
    ``templates/prompts/session-start-<phase>.md``, see
    :data:`_SESSION_CONTEXT_TEMPLATES`):

    - ``IMPLEMENT`` / ``REVIEW``: the issue ref parsed from ``PLAN.md`` (omitted if
      absent), plus a one-line pointer to the phase's ``done`` command and the gates
      it enforces. ``PLAN.md`` holds the full plan.
    - ``PLAN``: a detached worktree with no ``PLAN.md`` at the root; the issue ref
      of a ``wade plan --issue-id`` session (parsed from ``.wade/plan-issue.md``,
      which ``plan_service`` persists — omitted for a from-scratch plan), plus a
      reminder to write a valid ``PLAN*.md`` then run ``plan-session done``.

    Import-light and stdout-safe: it runs on the lean ``wade-hook`` entry point, so
    it reads the issue-ref file with a plain file read and touches nothing that
    prints. Returns ``None`` when there is nothing meaningful to say (runtime
    no-op). The result is hard-capped at :data:`_SESSION_CONTEXT_MAX_CHARS`.
    """
    lines: list[str] = []

    # #407: a ``.wade/stale_base`` marker means startup catchup could not advance the
    # branch onto its base. Emit the loud "N commits behind" warning FIRST so the
    # char-cap below (which truncates the *tail*) can never drop it or split its ``…``
    # mid-warning. Only IMPLEMENT/REVIEW worktrees ever carry the marker. The read is a
    # plain, stdout-safe file read (import-light leaf) — safe on the lean ``wade-hook``
    # SessionStart entry point (#349).
    if phase in (SessionPhase.IMPLEMENT, SessionPhase.REVIEW):
        from wade.utils.stale_base import read_stale_base

        stale = read_stale_base(worktree_root)
        if stale is not None and stale.behind > 0:
            lines.append(_stale_base_line(stale.behind, phase))

    # Where each phase's issue ref lives on disk (impl/review: the root PLAN.md;
    # plan: the metadata file a ``--issue-id`` session persists). ``None`` → no
    # issue line for this phase.
    issue_ref_path: Path | None = None
    if phase in (SessionPhase.IMPLEMENT, SessionPhase.REVIEW):
        issue_ref_path = worktree_root / "PLAN.md"
    elif phase is SessionPhase.PLAN:
        issue_ref_path = worktree_root / PLAN_ISSUE_REF_FILE

    if issue_ref_path is not None:
        parsed = _parse_issue_heading(issue_ref_path)
        if parsed is not None:
            lines.append(_issue_line(parsed))

    template_name = _SESSION_CONTEXT_TEMPLATES.get(phase)
    if template_name is not None:
        # Lazy import: the prose loader pulls in ``wade.skills.installer``, which
        # is NOT on the hot PreToolUse write path — only this SessionStart branch
        # reaches it, once per session start. It adds ~2ms and loads none of the
        # heavy modules the lean entry avoids (no crossby adapters, no CLI graph).
        from wade.skills.installer import load_prompt_template

        template_lines = [
            ln for ln in load_prompt_template(template_name).splitlines() if ln.strip()
        ]
        lines.extend(template_lines)

    if not lines:
        return None

    # Brand the payload so the injected block is recognizable in the transcript.
    if not lines[0].startswith("[wade]"):
        lines[0] = f"[wade] {lines[0]}"

    payload = "\n".join(lines)
    if len(payload) > _SESSION_CONTEXT_MAX_CHARS:
        payload = payload[: _SESSION_CONTEXT_MAX_CHARS - 1].rstrip() + "…"
    return payload

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
from pathlib import Path

from crossby.hooks.runtime import HookDecision, HookEvent

from wade.utils import markers

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

# Character devices are not worktree escapes — `>/dev/null 2>&1` is the single most
# common shell idiom an agent emits, and denying it breaks ordinary sessions.
_ALWAYS_ALLOWED_PATH_PREFIXES = ("/dev/",)

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


def _contained(path: Path, root: Path) -> bool:
    """True when ``path`` is inside ``root`` (or is an always-allowed device node)."""
    if str(path).startswith(_ALWAYS_ALLOWED_PATH_PREFIXES):
        return True
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

    **Reads outside the worktree are allowed; only writes are contained.** Reading
    a sibling repo (``cat ../crossby/x``, ``grep -r foo ../crossby``,
    ``git -C ../crossby log``) never mutates state, so a read operand may resolve
    anywhere. The guard's job is to keep *writes* inside the root.

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
       ``of=/tmp/x``, ``git -C/tmp/other``) resolves outside the root. The glued
       form is contained in *all* modes: a tokenizer cannot tell a glued read flag
       from a glued write flag, so both are denied — this conservatively denies a
       few glued *reads* too (e.g. ``git -C/tmp/other log``).
    6. Spaced ``git -C <dir>`` where ``<dir>`` is outside the root **and** a git
       write subcommand (:data:`_GIT_WRITE_SUBCOMMANDS`) follows in the same
       segment. ``git -C <outside> log``/``show``/``status``/``diff`` (read
       subcommands) stay allowed; ``git -C <outside> clean`` would otherwise delete
       untracked files outside with no later path operand to catch it.
    7. In plan mode only: any output redirect, an in-place edit flag (``sed -i``,
       ``--in-place``) on a command that *has* one (:data:`_IN_PLACE_COMMANDS` —
       ``grep -i`` and ``ls -i`` are ordinary read flags), or an operand of a write
       command whose path is not a plan artifact.

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
        if resolved is None or not _contained(resolved, root):
            return deny_outside("redirect target", target)
        if plan_mode and not _is_plan_artifact_path(resolved, root):
            return HookDecision.deny(
                f"BLOCKED by plan-session guard: redirecting output to '{target}' "
                "would write a non-artifact file without going through a write tool. "
                "In plan mode only plan artifacts (PLAN.md, PLAN-*.md, prompt.txt, "
                ".transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, "
                ".wade/plans/*) may be written."
            )
        return None

    def check_non_artifact(what: str, token: str, resolved: Path) -> HookDecision | None:
        """Plan mode: a contained path a write command targets must be an artifact."""
        if not plan_mode or _is_plan_artifact_path(resolved, root):
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
    # Spaced ``git -C <dir>``: the next token is the directory (awaiting flag), and
    # once seen we remember it here iff it resolves outside the root. A read like
    # ``git -C <outside> log`` is fine; only a later git *write* subcommand denies.
    awaiting_git_c_dir = False
    git_c_outside_token: str | None = None

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
            awaiting_git_c_dir = False
            git_c_outside_token = None
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
            if resolved is None or not _contained(resolved, root):
                return deny_outside(f"{command_name} target", token)
            continue

        # Spaced ``git -C <dir>``: buffer the directory. A read through it is fine
        # (``git -C ../crossby log``); a later git *write* subcommand in the same
        # segment turns a buffered *outside* dir into a denial. The glued
        # ``-C/outside`` form is caught by `_embedded_path` below in every mode.
        if awaiting_git_c_dir:
            awaiting_git_c_dir = False
            resolved = _resolve_shell_path(token, base=base)
            if resolved is None or not _contained(resolved, root):
                git_c_outside_token = token
            continue

        if command_name == "git" and token == "-C":
            awaiting_git_c_dir = True
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
            # A git write subcommand after a spaced ``-C`` pointing outside the root
            # (``git -C /outside clean -fd``) writes outside with no later path
            # operand to catch it — deny on the buffered directory.
            if git_c_outside_token is not None:
                return deny_outside("git -C directory", git_c_outside_token)

        # A path glued to a flag or operand (``--output=/tmp/x``, ``of=/tmp/x``,
        # ``git -C/tmp/other``) is invisible to `_looks_like_path`, and `of=/tmp/x`
        # would resolve *relative*. Contained in every mode: a tokenizer cannot tell
        # a glued read flag from a glued write flag, so both are denied.
        embedded = _embedded_path(token)
        if embedded is not None:
            resolved = _resolve_shell_path(embedded, base=base)
            if resolved is None or not _contained(resolved, root):
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
            if resolved is None or not _contained(resolved, root):
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

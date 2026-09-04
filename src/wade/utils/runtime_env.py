"""Best-effort probe of the AI runtime this process is running *inside*.

A ``wade`` command started from within an AI CLI session is a child of that
session's process. If the parent was launched under an OS sandbox (Codex
Seatbelt/Landlock, Cursor ``--sandbox enabled``), every descendant inherits that
boundary: no profile wade resolves, and no flag it passes to a child runtime,
can widen it. **An inner wade process can never escape an existing parent
sandbox.** The only fix is to relaunch the *outer* session with the desired
profile.

This module answers two deliberately independent questions:

1. **Which** AI CLI session are we inside? — ``detect_ai_cli_env``, the
   historical identity probe that drives the nested-launch guard. Unchanged.
2. **Is that session confined?** — a tri-state assessment that is ``UNKNOWN``
   unless a runtime actually publishes a signal, and is *never* inferred from
   tool identity.

Keeping them independent matters in both directions. Identity alone says nothing
about confinement (Codex runs sandboxed *or* unrestricted under the same
``CODEX_CLI`` marker), and a sandbox signal alone is still actionable even when
the tool cannot be named. Reporting a confident wrong cause is worse than
reporting none: the point of the diagnosis is to replace an opaque error with a
*trustworthy* one.

Sandbox signals read, and their confidence:

- ``CODEX_SANDBOX`` — **high confidence when present.** A published policy name
  (for example ``seatbelt``) reads as ``SANDBOXED``; a value naming an explicitly
  unconfined mode (``danger-full-access``) reads as ``UNRESTRICTED``. Codex
  currently publishes this marker for macOS Seatbelt, but not for its Linux
  Landlock sandbox. An absent marker is therefore ``UNKNOWN``, never
  ``UNRESTRICTED``: the process may be an older Codex or a Linux Landlock session
  that does not publish this signal.
- ``CODEX_SANDBOX_NETWORK_DISABLED`` — **secondary.** Network confinement is
  confinement, so a truthy value reads as ``SANDBOXED``. Consulted only when
  ``CODEX_SANDBOX`` is absent.
- **Every other runtime publishes nothing.** Verified for Claude Code, whose
  session environment carries no ``*SANDBOX*`` variable at all; Copilot, Cursor
  and Antigravity expose no documented signal either. ``UNKNOWN`` is the correct
  and final answer for them — a tool that *can* be sandboxed is not a tool that
  *is* sandboxed, and crossby's static ``sandboxes_writes`` capability describes
  the tool, not this process's actual boundary.

This is a leaf module: it imports nothing from wade, so any layer may use it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel

# Ordered identity probes; first match wins. The returned key is the *first*
# variable of each group, which is also the historical return value of
# ``detect_ai_cli_env`` — callers print it and tests pin it, so the mapping from
# a group to its key must stay stable.
_IDENTITY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CLAUDE_CODE", ("CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT")),
    ("COPILOT_CLI", ("COPILOT_CLI",)),
    # Codex's managed runtime exports the session/thread markers rather than
    # ``CODEX_CLI``. Keep the historical canonical key so all existing callers
    # and messages still identify it as Codex CLI.
    ("CODEX_CLI", ("CODEX_CLI", "CODEX_SESSION_ID", "CODEX_THREAD_ID")),
    ("CURSOR_CLI", ("CURSOR_CLI",)),
    ("ANTIGRAVITY_AGENT", ("ANTIGRAVITY_AGENT",)),
)

_RUNTIME_LABELS = {
    "CLAUDE_CODE": "Claude Code",
    "COPILOT_CLI": "Copilot CLI",
    "CODEX_CLI": "Codex CLI",
    "CURSOR_CLI": "Cursor CLI",
    "ANTIGRAVITY_AGENT": "Antigravity CLI",
}

#: Human stand-in when a sandbox signal is present but the tool cannot be named.
UNNAMED_RUNTIME_LABEL = "the enclosing AI runtime"

CODEX_SANDBOX_ENV = "CODEX_SANDBOX"
CODEX_NETWORK_DISABLED_ENV = "CODEX_SANDBOX_NETWORK_DISABLED"

#: Every variable this module reads for a *sandbox* verdict. Tests clear these so
#: a suite run from inside a sandboxed runtime still assesses deterministically.
SANDBOX_SIGNAL_ENV_VARS: tuple[str, ...] = (CODEX_SANDBOX_ENV, CODEX_NETWORK_DISABLED_ENV)

#: Codex markers that identify an enclosing Codex runtime. Test setup clears
#: these real-process markers so nested-launch tests stay deterministic when the
#: suite itself runs in Codex.
CODEX_IDENTITY_ENV_VARS: tuple[str, ...] = (
    "CODEX_CLI",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
)

# Values of ``CODEX_SANDBOX`` that name an explicitly *unconfined* mode. Anything
# else non-empty is treated as confinement — the fail-safe direction, since a
# false "sandboxed" costs a redundant relaunch hint while a false "unrestricted"
# restores exactly the opaque failure this module exists to explain.
_UNCONFINED_SANDBOX_VALUES = frozenset({"danger-full-access", "disabled", "none", "off"})

_FALSEY_ENV_VALUES = frozenset({"", "0", "false", "no", "off"})


class SandboxAssessment(StrEnum):
    """How confined the parent runtime is, as far as wade can actually tell."""

    SANDBOXED = "sandboxed"
    UNRESTRICTED = "unrestricted"
    UNKNOWN = "unknown"


class ParentRuntime(BaseModel, frozen=True):
    """The AI runtime this process is running inside, if any."""

    #: Identity key from :func:`detect_ai_cli_env`; ``None`` when unrecognised.
    env_var: str | None = None
    sandbox: SandboxAssessment = SandboxAssessment.UNKNOWN
    #: What produced a definite assessment, for diagnostics. ``None`` when
    #: ``sandbox`` is ``UNKNOWN``.
    signal: str | None = None

    @property
    def detected(self) -> bool:
        """Whether a *named* parent runtime was recognised."""
        return self.env_var is not None

    @property
    def is_sandboxed(self) -> bool:
        return self.sandbox is SandboxAssessment.SANDBOXED

    @property
    def label(self) -> str:
        """Human name for messages — never a raw guess."""
        if self.env_var is None:
            return UNNAMED_RUNTIME_LABEL
        return _RUNTIME_LABELS.get(self.env_var, self.env_var)


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().casefold() not in _FALSEY_ENV_VALUES


def detect_ai_cli_env(env: Mapping[str, str] | None = None) -> str | None:
    """Detect which AI CLI session we are running inside, if any.

    Returns the env-var name that triggered detection, or ``None``.

    When an AI agent calls ``wade implement`` from within its own session, we must
    not launch another AI instance (infinite nesting). Instead, create the
    worktree and print the path.
    """
    environ = _environ(env)
    for key, variables in _IDENTITY_SIGNALS:
        if any(environ.get(name) for name in variables):
            return key
    return None


def assess_parent_sandbox(
    env: Mapping[str, str] | None = None,
) -> tuple[SandboxAssessment, str | None]:
    """Read the environment for a sandbox verdict about the current process.

    Returns the assessment and the signal that produced it. Deliberately
    independent of tool identity — see the module docstring for the per-runtime
    signals and their confidence.
    """
    environ = _environ(env)
    raw = (environ.get(CODEX_SANDBOX_ENV) or "").strip()
    if raw:
        signal = f"{CODEX_SANDBOX_ENV}={raw}"
        if raw.casefold() in _UNCONFINED_SANDBOX_VALUES:
            return SandboxAssessment.UNRESTRICTED, signal
        return SandboxAssessment.SANDBOXED, signal
    if _is_truthy(environ.get(CODEX_NETWORK_DISABLED_ENV)):
        return SandboxAssessment.SANDBOXED, CODEX_NETWORK_DISABLED_ENV
    return SandboxAssessment.UNKNOWN, None


def parent_runtime(
    env_var: str | None,
    env: Mapping[str, str] | None = None,
) -> ParentRuntime:
    """Assess the sandbox boundary around an already-detected parent identity.

    Split from :func:`detect_parent_runtime` for the two call sites that resolve
    identity through their own module-level indirection.
    """
    assessment, signal = assess_parent_sandbox(env)
    return ParentRuntime(env_var=env_var, sandbox=assessment, signal=signal)


def detect_parent_runtime(env: Mapping[str, str] | None = None) -> ParentRuntime:
    """Identify the parent AI runtime and assess its sandbox boundary."""
    environ = _environ(env)
    return parent_runtime(detect_ai_cli_env(environ), environ)


def requires_unsandboxed_relaunch(
    *,
    resolved_sandbox: bool,
    parent: ParentRuntime,
) -> bool:
    """The one profile-mismatch predicate shared by every launch path.

    True only when this command resolved the *unrestricted* profile while the
    runtime it is running inside is *known* to be sandboxed — the one case where
    wade can state, rather than guess, that the requested profile is
    undeliverable from here.

    ``UNKNOWN`` never fires. Blocking or diagnosing on an unverifiable guess would
    either stop a session that works or assert a cause wade cannot support.
    """
    return not resolved_sandbox and parent.is_sandboxed


#: Shared preamble for the runnable remediation. The command follows on its own
#: line so it can be copied verbatim.
INHERITED_SANDBOX_HINT = "Open a new host terminal, then run:"


def inherited_sandbox_finding(parent: ParentRuntime, *, operation: str) -> str:
    """State the boundary and why the requested profile cannot be delivered."""
    return (
        f"{parent.label} is sandboxed, and a wade process cannot escape the sandbox of "
        f"the runtime it runs inside — {operation} cannot run unrestricted from here."
    )


def possible_inherited_sandbox_cause(parent: ParentRuntime) -> str:
    """Hedged wording for a generic denial-shaped launch failure.

    Generic OS denials never prove their cause: even a known parent boundary can
    coexist with a non-executable binary or broken local network configuration.
    """
    if parent.is_sandboxed:
        return (
            f"{parent.label} is sandboxed, but this generic denial can also come from "
            "executable permissions or network configuration. Check those first; if "
            "the failure persists, relaunch the outer session unrestricted."
        )

    if parent.sandbox is SandboxAssessment.UNRESTRICTED:
        return (
            f"{parent.label} explicitly reports an unrestricted runtime, so this denial is "
            "not inherited from a parent sandbox. Check executable permissions or network "
            "configuration."
        )

    subject = parent.label if parent.detected else UNNAMED_RUNTIME_LABEL
    return (
        f"This failure has the shape of a sandbox denial. wade cannot confirm whether "
        f"{subject} is sandboxed — it publishes no sandbox signal — but if it is, no "
        "wade command run inside it can widen that boundary; relaunch the outer "
        "session unrestricted instead."
    )


# Substrings of a failed launch that are consistent with an OS sandbox denial.
#
# Deliberately excluded: "command not found" / "no such file or directory". From
# inside a sandbox the host filesystem is exactly what is *not* observable, so
# "a binary that exists on the host but is missing here" is unverifiable at the
# point of failure — it is equally the signature of a genuinely uninstalled tool.
# A binary that resolves but cannot be *executed* is a different, checkable
# shape, and is matched.
_SANDBOX_DENIAL_MARKERS: tuple[str, ...] = (
    "operation not permitted",
    "permission denied",
    "eacces",
    "eperm",
    "read-only file system",
    "network is unreachable",
    "could not resolve host",
    "connection refused",
    "sandbox denied",
    "sandbox policy",
    "seatbelt",
    "landlock",
    "cannot execute",
    "exec format error",
    "not executable",
)

# Generic OS failures are compatible with an inherited boundary but do not prove
# it: a non-executable binary, local firewall, or malformed executable can emit
# the same text. Only these policy/runtime markers support a confident cause.
_EXPLICIT_SANDBOX_DENIAL_MARKERS: tuple[str, ...] = (
    "sandbox denied",
    "sandbox policy",
    "seatbelt",
    "landlock",
)


def looks_like_sandbox_denial(text: str) -> bool:
    """Whether a launch failure's text matches a known sandbox-denial shape.

    Only meaningful for a launch that never started a process; a running tool's
    output may quote any of these strings for unrelated reasons.
    """
    lowered = text.casefold()
    return any(marker in lowered for marker in _SANDBOX_DENIAL_MARKERS)


def has_explicit_sandbox_denial(text: str) -> bool:
    """Whether a failed launch names an OS sandbox policy explicitly."""
    lowered = text.casefold()
    return any(marker in lowered for marker in _EXPLICIT_SANDBOX_DENIAL_MARKERS)

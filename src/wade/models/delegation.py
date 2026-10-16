"""Delegation domain models — generic "delegate and wait" infrastructure."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from wade.models.config import DEFAULT_SANDBOX
from wade.models.permission import PermissionMode


class DelegationMode(StrEnum):
    """How a delegation request is executed."""

    PROMPT = "prompt"
    INTERACTIVE = "interactive"
    HEADLESS = "headless"


class DelegationRequest(BaseModel):
    """What to delegate and how."""

    mode: DelegationMode
    prompt: str
    ai_tool: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: Path | None = None
    # Default headless subprocess budget, in seconds. 600s (not 300s) so a
    # high-effort review/deps run over a large diff finishes rather than tripping
    # the budget mid-run. Override per command via ``ai.<command>.timeout``. The
    # review/deps services compute the real budget with ``effective_timeout``
    # (scales from payload size + effort unless a config value is set); this
    # default is the fallback for direct constructions that skip that path.
    timeout: int = 600
    # True when ``timeout`` came from an explicit ``ai.<command>.timeout`` rather
    # than from scaling. The headless path honors an explicit budget verbatim —
    # **no automatic retry** — because it is the escape hatch for orchestrators
    # with a hard tool-timeout (the user set it to stay under a fixed cap; a
    # retry would silently blow past that cap). Scaled budgets (this False) retry.
    explicit_timeout: bool = False
    output_file: Path | None = None
    trusted_dirs: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    # Resolved AI-runtime sandbox profile for the delegated launch. Delegated
    # runs used to hardcode the restrictive side; they now inherit the caller's
    # resolved profile, so a delegated reviewer keeps its own host credentials
    # under the unrestricted default (#478).
    sandbox: bool = DEFAULT_SANDBOX
    # Per-operation remediation context for the shared sandbox check in
    # ``delegate()`` (#480). Deps, standalone review and batch review all funnel
    # through one dispatcher, which therefore cannot know on its own whether to
    # tell the user to re-run `wade review implementation`, `wade task deps`, or a
    # batch command. Generic advice would defeat the point of the diagnosis, so
    # the caller supplies both: ``operation`` names what cannot run unrestricted,
    # ``relaunch_command`` is the exact line to type in a host terminal. Left
    # ``None`` by direct constructions that have no meaningful command to offer —
    # the finding is still reported, just without a copyable command.
    invocation_id: str | None = None
    # Service-verified ownership metadata, never inferred from model output.
    knowledge_plan_filename: str | None = None
    knowledge_task_ids: tuple[str, ...] = ()
    knowledge_delegation_digest: str | None = None
    operation: str | None = None
    relaunch_command: str | None = None


class DelegationResult(BaseModel):
    """Outcome of a delegation request."""

    success: bool
    feedback: str
    mode: DelegationMode
    exit_code: int = 0
    skipped: bool = False
    # An explicit human decline after an interactive session is not a crash or
    # a completed assessment, even when that session's process already ran.
    cancelled: bool = False
    # True only when a headless subprocess exceeded its budget. Stays
    # ``success=False`` (a timeout is still a non-success), but lets callers tell
    # a timeout — which may carry partial output and is worth retrying longer —
    # apart from a crash. A crash (CommandError / non-zero exit) keeps this False.
    timed_out: bool = False
    # True when no delegated process was ever started: an unknown/unsupported
    # tool, or a spawn that failed outright. A *non-zero exit* keeps this False —
    # that process ran, and telling a user to "restore the reviewer runtime" when
    # their reviewer started fine and then failed is wrong advice (#462). The
    # distinction is what lets a caller give trusted remediation instead of a
    # disjunction of possible causes, and is why an unattempted review consumes
    # no review-pass budget and opens no gate (#480).
    never_launched: bool = False
    # True only after dispatch has crossed the spawn boundary.  This is narrower
    # than ``never_launched``: an unknown tool or an unsupported mode starts no
    # process *and* never attempts one, while a missing binary can fail at the
    # spawn itself.  Failure reporters need that distinction before attributing
    # denial-shaped free-form text to an inherited sandbox.
    launch_attempted: bool = False
    # True only when an attempted external runtime was requested with the
    # unrestricted profile but is known to inherit a sandbox from its parent.
    # Preflight refusals keep this false, so callers cannot attribute unrelated
    # user-controlled error text to the parent sandbox.
    inherited_sandbox_profile_mismatch: bool = False
    # Copy the runnable remediation onto the result so a caller that reports a
    # later launch failure can repeat the same fully-resolved command instead of
    # falling back to a lossy operation-level default.
    relaunch_command: str | None = None
    # Bounded stderr from a headless child that exited 0 without stdout. Never
    # part of ``feedback``: generic delegations may legitimately print nothing,
    # but a caller that rejects empty output can surface the child's own reason.
    diagnostics: str = ""
    # True when a launched reviewer exited successfully without any assessment
    # text. Review callers convert that into a launched failure (#547): it
    # consumes a pass like any other launched failure but never certifies review.
    empty_assessment: bool = False
    # Session completion command a successful review hands off to; ``None``
    # outside a managed session, where no session ``done`` gate applies.
    completion_command: str | None = None

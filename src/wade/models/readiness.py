"""Pure session-readiness identifiers shared by CLI and service layers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, field_validator

from wade.models.config import AI_COMMAND_NAMES

# Set by ``wade plan`` **only** on its supported temp-directory fallback — when
# no planning worktree could be created (bootstrap failed, or the caller is not
# in a git repo at all) and the agent therefore runs from the caller's checkout
# writing plan files to a throwaway directory. Its presence is what lets the
# plan readiness check tell that legitimate mode apart from an agent that simply
# started in the main checkout.
PLAN_DIR_ENV_VAR = "WADE_PLAN_DIR"


class ReadinessPhase(StrEnum):
    """A WADE session phase whose capabilities can be checked before edits."""

    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    REVIEW_PR_COMMENTS = "review-pr-comments"
    DEPS = "deps"


class ReadinessFailure(StrEnum):
    """Stable capability names emitted by the session-readiness command."""

    GIT_METADATA_WRITE = "git_metadata_write"
    GITHUB_CLI_EXECUTABLE = "github_cli_executable"
    GITHUB_AUTHENTICATION = "github_authentication"
    GITHUB_API_REACHABILITY = "github_api_reachability"
    KNOWLEDGE_VOTE_STAGING = "knowledge_vote_staging"
    PLAN_OUTPUT_WRITE = "plan_output_write"


class ReadinessRequirements(BaseModel, frozen=True):
    """The runtime capabilities a specific agent-side session actually needs.

    This model intentionally describes the *child AI runtime*, not the parent
    ``wade plan`` / ``wade task deps`` command.  The parent can call a task
    provider after the child exits, while a detached planning/dependency agent
    only needs its own checkout and (when enabled) a local vote transport file.
    Keeping that distinction declarative prevents an implementation-only GitHub
    probe from accidentally becoming a blanket sandbox requirement.

    ``ai_command`` names the ``ai.<command>`` config section whose tool
    selection governs the phase, so the phase -> command mapping stays pure data
    here instead of being re-derived by whichever layer runs the check.
    """

    ai_command: str
    requires_git_metadata_write: bool = False
    requires_github: bool = False
    supports_staged_knowledge_votes: bool = False

    @field_validator("ai_command")
    @classmethod
    def _known_ai_command(cls, value: str) -> str:
        """Reject a command name ``get_ai_tool`` would silently ignore.

        An unknown section name is not an error there — it falls through to
        ``ai.default_tool`` — so a typo would quietly disable a phase's
        per-command tool override instead of failing.
        """
        if value not in AI_COMMAND_NAMES:
            raise ValueError(f"Unknown AI command section {value!r}: use one of {AI_COMMAND_NAMES}")
        return value


READINESS_REQUIREMENTS: dict[ReadinessPhase, ReadinessRequirements] = {
    # These worktrees author only plan/dependency artifacts and an optional
    # `.wade/` rating transport record. They do not fetch, push, or finalize a
    # PR themselves, so a correctly-contained runtime needs neither gitdir
    # writes nor GitHub credentials/network access.
    ReadinessPhase.PLAN: ReadinessRequirements(
        ai_command="plan",
        supports_staged_knowledge_votes=True,
    ),
    ReadinessPhase.DEPS: ReadinessRequirements(
        ai_command="deps",
        supports_staged_knowledge_votes=True,
    ),
    # These agents run fetch/sync/done and review-thread commands directly.
    # PR operations are GitHub-backed even when the task provider is Markdown
    # or ClickUp, so this is deliberately independent of provider selection.
    ReadinessPhase.IMPLEMENTATION: ReadinessRequirements(
        ai_command="implement",
        requires_git_metadata_write=True,
        requires_github=True,
    ),
    ReadinessPhase.REVIEW_PR_COMMENTS: ReadinessRequirements(
        ai_command="review_pr_comments",
        requires_git_metadata_write=True,
        requires_github=True,
    ),
}

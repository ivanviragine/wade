"""Pure session-readiness identifiers shared by CLI and service layers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from wade.models.workflow import SESSION_DEFINITIONS, SessionKind

# Set by ``wade plan`` **only** on its supported temp-directory fallback — when
# no planning worktree could be created (bootstrap failed, or the caller is not
# in a git repo at all) and the agent therefore runs from the caller's checkout
# writing plan files to a throwaway directory. Its presence is what lets the
# plan readiness check tell that legitimate mode apart from an agent that simply
# started in the main checkout.
PLAN_DIR_ENV_VAR = "WADE_PLAN_DIR"


# Compatibility name retained for one release.  This is an alias, not a second
# enum: identity comparisons and serialized values remain unchanged.
ReadinessPhase = SessionKind


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


READINESS_REQUIREMENTS: dict[ReadinessPhase, ReadinessRequirements] = {
    kind: ReadinessRequirements(
        ai_command=definition.ai_command.value,
        requires_git_metadata_write=definition.readiness.requires_git_metadata_write,
        requires_github=definition.readiness.requires_github,
        supports_staged_knowledge_votes=definition.readiness.supports_staged_knowledge_votes,
    )
    for kind, definition in SESSION_DEFINITIONS.items()
}

"""Pure session-readiness identifiers shared by CLI and service layers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


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


class ReadinessRequirements(BaseModel, frozen=True):
    """The runtime capabilities a specific agent-side session actually needs.

    This model intentionally describes the *child AI runtime*, not the parent
    ``wade plan`` / ``wade task deps`` command.  The parent can call a task
    provider after the child exits, while a detached planning/dependency agent
    only needs its own checkout and (when enabled) a local vote transport file.
    Keeping that distinction declarative prevents an implementation-only GitHub
    probe from accidentally becoming a blanket sandbox requirement.
    """

    requires_git_metadata_write: bool = False
    requires_github: bool = False
    supports_staged_knowledge_votes: bool = False


READINESS_REQUIREMENTS: dict[ReadinessPhase, ReadinessRequirements] = {
    # These worktrees author only plan/dependency artifacts and an optional
    # `.wade/` rating transport record. They do not fetch, push, or finalize a
    # PR themselves, so a correctly-contained runtime needs neither gitdir
    # writes nor GitHub credentials/network access.
    ReadinessPhase.PLAN: ReadinessRequirements(supports_staged_knowledge_votes=True),
    ReadinessPhase.DEPS: ReadinessRequirements(supports_staged_knowledge_votes=True),
    # These agents run fetch/sync/done and review-thread commands directly.
    # PR operations are GitHub-backed even when the task provider is Markdown
    # or ClickUp, so this is deliberately independent of provider selection.
    ReadinessPhase.IMPLEMENTATION: ReadinessRequirements(
        requires_git_metadata_write=True,
        requires_github=True,
    ),
    ReadinessPhase.REVIEW_PR_COMMENTS: ReadinessRequirements(
        requires_git_metadata_write=True,
        requires_github=True,
    ),
}

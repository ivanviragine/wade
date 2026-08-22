"""Pure session-readiness identifiers shared by CLI and service layers."""

from __future__ import annotations

from enum import StrEnum


class ReadinessPhase(StrEnum):
    """A WADE session phase whose capabilities can be checked before edits."""

    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    REVIEW_PR_COMMENTS = "review-pr-comments"
    DEPS = "deps"


class ReadinessFailure(StrEnum):
    """Stable capability names emitted by the session-readiness command."""

    GIT_METADATA_WRITE = "git_metadata_write"
    GITHUB_AUTHENTICATION = "github_authentication"
    GITHUB_API_REACHABILITY = "github_api_reachability"
    KNOWLEDGE_VOTE_STAGING = "knowledge_vote_staging"

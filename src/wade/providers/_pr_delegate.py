"""Shared mixin: delegate PR-review APIs to a GitHub provider.

Issue providers whose tasks live elsewhere (Markdown file, ClickUp, etc.)
still flow PRs through GitHub. Without this mixin they would raise
``NotImplementedError`` for review threads / PR comments and silently
degrade ``wade fetch-reviews`` and the auto-poll loop. Composing the mixin
makes those operations transparently delegate to a lazily-constructed
:class:`wade.providers.github.GitHubProvider`.

Usage::

    class ClickUpProvider(GitHubPRDelegateMixin, AbstractTaskProvider):
        def __init__(self, config=None, github_provider=None):
            super().__init__(config)
            self._init_pr_delegate(github_provider)
"""

from __future__ import annotations

from wade.models.review import PRReviewStatus, ReviewThread
from wade.providers.base import AbstractTaskProvider


class GitHubPRDelegateMixin:
    """Forwards PR-review thread / comment / status APIs to a GitHubProvider.

    Subclasses must call :meth:`_init_pr_delegate` (typically from their
    ``__init__``) to wire up the inner provider — pass ``None`` for the
    default lazy-constructed :class:`GitHubProvider`, or inject one for
    tests.
    """

    _pr_github: AbstractTaskProvider | None = None

    def _init_pr_delegate(
        self,
        github_provider: AbstractTaskProvider | None = None,
    ) -> None:
        """Wire the inner GitHub provider used for PR-review delegation."""
        self._pr_github = github_provider

    def _pr_gh(self) -> AbstractTaskProvider:
        """Return the inner GitHub provider, constructing it on first use."""
        if self._pr_github is None:
            from wade.providers.github import GitHubProvider

            self._pr_github = GitHubProvider()
        return self._pr_github

    # --- Delegated PR-review operations ---

    def get_pr_review_threads(self, pr_number: int) -> list[ReviewThread]:
        return self._pr_gh().get_pr_review_threads(pr_number)

    def resolve_review_thread(self, thread_id: str) -> bool:
        return self._pr_gh().resolve_review_thread(thread_id)

    def get_pr_issue_comments(self, pr_number: int) -> list[dict[str, str]]:
        return self._pr_gh().get_pr_issue_comments(pr_number)

    def get_pr_review_status(self, pr_number: int) -> PRReviewStatus:
        return self._pr_gh().get_pr_review_status(pr_number)

    def get_repo_nwo(self) -> str:
        return self._pr_gh().get_repo_nwo()

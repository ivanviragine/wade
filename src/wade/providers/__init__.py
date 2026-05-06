"""Task backend providers — ABC and implementations."""

from wade.models.config import ProviderID
from wade.providers.base import AbstractTaskProvider
from wade.providers.github import GitHubProvider
from wade.providers.registry import get_provider, register_provider, registered_provider_names

# Register built-in providers.
# GitHub is always available (relies only on the `gh` CLI).
register_provider(ProviderID.GITHUB, GitHubProvider)


# ClickUp needs httpx — register lazily so users who don't use it
# aren't blocked by a missing optional dependency.
def _load_clickup():  # type: ignore[no-untyped-def]
    from wade.providers.clickup import ClickUpProvider

    return ClickUpProvider


register_provider(ProviderID.CLICKUP, _load_clickup)


# Markdown provider — lazy import so users who don't use it pay no cost
# even on minimal installs.
def _load_markdown():  # type: ignore[no-untyped-def]
    from wade.providers.markdown import MarkdownIssueProvider

    return MarkdownIssueProvider


register_provider(ProviderID.MARKDOWN, _load_markdown)

__all__ = [
    "AbstractTaskProvider",
    "get_provider",
    "register_provider",
    "registered_provider_names",
]

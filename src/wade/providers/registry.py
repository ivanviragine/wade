"""Provider discovery — instantiate the right provider from config."""

from __future__ import annotations

from wade.models.config import ProjectConfig, ProviderID
from wade.providers.base import AbstractTaskProvider
from wade.providers.github import GitHubProvider


def get_provider(config: ProjectConfig | None = None) -> AbstractTaskProvider:
    """Get the configured task provider.

    Currently only GitHub is supported. Future providers (Linear, Jira, etc.)
    will be added here with a registry pattern similar to AI tools.
    """
    if config is None:
        return GitHubProvider()

    provider_name = config.provider.name

    if provider_name == ProviderID.GITHUB:
        return GitHubProvider()

    raise ValueError(f"Unknown provider: {provider_name}")

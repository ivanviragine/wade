"""Task backend providers — ABC and implementations."""

from wade.models.config import ProviderID
from wade.providers.base import AbstractTaskProvider
from wade.providers.clickup import ClickUpProvider
from wade.providers.github import GitHubProvider
from wade.providers.registry import get_provider, register_provider, registered_provider_names

# Register built-in providers
register_provider(ProviderID.GITHUB, GitHubProvider)
register_provider(ProviderID.CLICKUP, ClickUpProvider)

__all__ = [
    "AbstractTaskProvider",
    "get_provider",
    "register_provider",
    "registered_provider_names",
]

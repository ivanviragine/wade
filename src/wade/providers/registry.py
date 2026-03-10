"""Provider discovery — instantiate the right provider from config."""

from __future__ import annotations

from wade.models.config import ProjectConfig, ProviderID
from wade.providers.base import AbstractTaskProvider

_PROVIDER_FACTORIES: dict[ProviderID, type[AbstractTaskProvider]] = {}


def register_provider(provider_id: ProviderID, cls: type[AbstractTaskProvider]) -> None:
    """Register a provider class for a given ID."""
    _PROVIDER_FACTORIES[provider_id] = cls


def registered_provider_names() -> set[str]:
    """Return the set of provider name strings currently registered."""
    return {str(pid) for pid in _PROVIDER_FACTORIES}


def get_provider(config: ProjectConfig | None = None) -> AbstractTaskProvider:
    """Get the configured task provider.

    Instantiates the provider registered for ``config.provider.name``,
    passing the ``ProviderConfig`` to its constructor.
    """
    if config is None:
        from wade.providers.github import GitHubProvider

        return GitHubProvider()

    provider_id = config.provider.name

    if provider_id not in _PROVIDER_FACTORIES:
        supported = ", ".join(sorted(str(p) for p in _PROVIDER_FACTORIES))
        raise ValueError(f"Unknown provider: {provider_id}. Supported: {supported}")

    cls = _PROVIDER_FACTORIES[provider_id]
    return cls(config.provider)

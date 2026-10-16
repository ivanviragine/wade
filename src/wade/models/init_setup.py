"""Typed transient answers collected by the project initialization wizard."""

from pydantic import BaseModel

from wade.models.config import ComplexityModelMapping, ProviderID


class ProviderSetup(BaseModel):
    """Provider answers, including the wizard-only token-copy request."""

    name: ProviderID = ProviderID.GITHUB
    api_token_env: str | None = None
    settings: dict[str, str] = {}
    add_env_to_copy: bool = False


class HooksSetup(BaseModel):
    """Only the worktree hook fields the wizard prompts for."""

    post_worktree_create: str | None = None
    copy_to_worktree: list[str] = []


class ImplementationSetup(BaseModel):
    """Implementation-specific tool and model-tier selections."""

    tool: str | None = None
    model_mapping: ComplexityModelMapping = ComplexityModelMapping()

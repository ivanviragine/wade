"""Pydantic Settings model for configuration with env var overrides.

Environment variables use the GHAIW_ prefix:
  GHAIW_AI_TOOL=claude
  GHAIW_MERGE_STRATEGY=direct
"""

from __future__ import annotations

# Re-export the models — the schema IS the models
from ghaiw.models.config import (
    AICommandConfig,
    AIConfig,
    ComplexityModelMapping,
    HooksConfig,
    ProjectConfig,
    ProjectSettings,
    ProviderConfig,
    ProviderID,
)

__all__ = [
    "AICommandConfig",
    "AIConfig",
    "ComplexityModelMapping",
    "HooksConfig",
    "ProjectConfig",
    "ProjectSettings",
    "ProviderConfig",
    "ProviderID",
]

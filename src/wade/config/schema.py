"""Pydantic Settings model for configuration with env var overrides.

Environment variables use the WADE_ prefix:
  WADE_AI_TOOL=claude
  WADE_MERGE_STRATEGY=direct
"""

from __future__ import annotations

# Re-export the models — the schema IS the models
from wade.models.config import (
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

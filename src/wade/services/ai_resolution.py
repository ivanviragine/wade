"""Shared AI tool and model resolution logic."""

from __future__ import annotations

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import AIToolID
from wade.models.config import AICommandConfig, ProjectConfig

logger = structlog.get_logger()


def resolve_ai_tool(
    ai_tool: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    auto_detect: bool = True,
) -> str | None:
    """Resolve AI tool from args -> config -> detection.

    Fallback chain: explicit arg -> command-specific config -> global default
    -> auto-detect (when *auto_detect* is True).

    Set *auto_detect=False* when the caller handles multi-tool selection
    itself (e.g. TTY prompts in implement-task).
    """
    if ai_tool:
        return ai_tool

    config_tool = config.get_ai_tool(command)
    if config_tool:
        return config_tool

    if auto_detect:
        installed = AbstractAITool.detect_installed()
        if installed:
            return installed[0].value

    return None


def resolve_model(
    model: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
    complexity: str | None = None,
) -> str | None:
    """Resolve model from args -> config -> complexity -> default.

    Fallback chain:
      1. Explicit *model* arg (e.g. ``--model`` CLI flag)
      2. Command-specific config (``ai.<command>.model``)
      3. Complexity-based mapping (``models.<tool>.<complexity>``)
      4. Global default (``ai.default_model``)

    When *tool* is provided, the resolved model is checked for compatibility
    with that tool.  Incompatible models are dropped (returns ``None``).
    """
    resolved: str | None = model

    # 2. Command-specific config
    if not resolved:
        cmd_config = getattr(config.ai, command, None)
        if isinstance(cmd_config, AICommandConfig) and cmd_config.model:
            resolved = cmd_config.model

    # 3. Complexity-based mapping
    if not resolved and tool and complexity:
        resolved = config.get_complexity_model(tool, complexity)

    # 4. Global default
    if not resolved:
        resolved = config.ai.default_model

    # Compatibility gate
    if resolved and tool:
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            if not adapter.is_model_compatible(resolved):
                logger.info(
                    "model.incompatible",
                    model=resolved,
                    tool=tool,
                )
                return None
        except (ValueError, KeyError):
            pass

    return resolved

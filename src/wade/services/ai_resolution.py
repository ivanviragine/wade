"""Shared AI tool and model resolution logic."""

from __future__ import annotations

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import AIToolID
from wade.models.config import AICommandConfig, ProjectConfig

logger = structlog.get_logger()

_CUSTOM_OPTION = "Custom…"


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


def confirm_ai_selection(
    resolved_tool: str | None,
    resolved_model: str | None,
    *,
    tool_explicit: bool,
    model_explicit: bool,
) -> tuple[str | None, str | None]:
    """Interactively confirm (and optionally change) the resolved AI tool/model.

    Fires only when stdin is a TTY and at least one of the flags was not
    explicitly provided by the caller.  When both flags are explicit (e.g.
    because ``wade work batch`` passes ``--ai``/``--model`` to child calls),
    this is a no-op.

    Returns the (tool, model) pair after any user-driven changes.
    """
    from wade.ui import prompts
    from wade.ui.console import console

    # Skip when non-TTY, no tool resolved, or both flags were explicit.
    if not prompts.is_tty() or resolved_tool is None or (tool_explicit and model_explicit):
        return resolved_tool, resolved_model

    tool = resolved_tool
    model = resolved_model

    while True:
        # Display current selection
        console.kv("AI tool", tool)
        if model:
            console.kv("Model", model)

        # Build menu dynamically based on which flags were NOT explicit.
        # Once the user changes a value at the prompt it becomes "confirmed",
        # but we keep the menu until they explicitly choose Proceed.
        menu_items: list[str] = ["Proceed"]
        installed = AbstractAITool.detect_installed()
        can_change_tool = not tool_explicit and len(installed) > 1
        if can_change_tool:
            menu_items.append("Change AI tool")
        if not model_explicit:
            menu_items.append("Change model")

        if len(menu_items) == 1:
            # Nothing to change — nothing to confirm either, exit immediately.
            break

        idx = prompts.select("Confirm AI selection", menu_items)
        choice = menu_items[idx]

        if choice == "Proceed":
            break

        if choice == "Change AI tool":
            tool_names = [str(t) for t in installed]
            current_idx = tool_names.index(tool) if tool in tool_names else 0
            new_idx = prompts.select("Select AI tool", tool_names, default=current_idx)
            new_tool = tool_names[new_idx]
            if new_tool != tool:
                tool = new_tool
                # Tool changed — force model re-selection regardless of model_explicit.
                model = _prompt_model_selection(tool)

        elif choice == "Change model":
            model = _prompt_model_selection(tool)

    return tool, model


def _prompt_model_selection(tool: str) -> str | None:
    """Show a model picker for *tool* and return the chosen model (or None)."""
    from wade.data import get_models_for_tool
    from wade.ui import prompts

    models = get_models_for_tool(tool)
    choices = [*models, _CUSTOM_OPTION]
    idx = prompts.select(f"Select model for {tool}", choices)
    chosen = choices[idx]
    if chosen == _CUSTOM_OPTION:
        custom = prompts.input_prompt("Enter model name", allow_empty=True)
        return custom or None
    return chosen or None

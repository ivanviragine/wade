"""Shared AI tool and model resolution logic."""

from __future__ import annotations

import os

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import AIToolID, EffortLevel
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
    itself (e.g. TTY prompts in implement).
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


def resolve_effort(
    effort: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
    complexity: str | None = None,
) -> EffortLevel | None:
    """Resolve effort level from args -> env var -> config -> None.

    Fallback chain:
      1. Explicit *effort* arg (e.g. ``--effort`` CLI flag)
      2. ``WADE_EFFORT`` environment variable
      3. Command-specific config (``ai.<command>.effort``)
      4. Per-complexity-tier config (``models.<tool>.<tier>.effort``)
      5. Global config (``ai.effort``)

    When *tool* is provided and the tool does not support effort, a warning
    is logged and ``None`` is returned.
    """
    resolved: str | None = effort

    if not resolved:
        resolved = os.environ.get("WADE_EFFORT")

    # Command-specific config (ai.<command>.effort)
    if not resolved:
        cmd_config = getattr(config.ai, command, None)
        if isinstance(cmd_config, AICommandConfig) and cmd_config.effort:
            resolved = cmd_config.effort

    # Per-complexity-tier config (models.<tool>.<tier>.effort)
    if not resolved and tool and complexity:
        resolved = config.get_complexity_effort(tool, complexity)

    # Global config (ai.effort)
    if not resolved:
        resolved = config.ai.effort

    if not resolved:
        return None

    # Validate
    try:
        level = EffortLevel(resolved)
    except ValueError:
        logger.warning("effort.invalid_level", effort=resolved)
        return None

    # Check tool support
    if tool:
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            if not adapter.capabilities().supports_effort:
                logger.info("effort.unsupported_tool", tool=tool, effort=resolved)
                return None
        except (ValueError, KeyError):
            pass

    return level


def resolve_yolo(
    yolo: bool | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
) -> bool:
    """Resolve YOLO mode from args -> config -> False.

    Fallback chain:
      1. Explicit *yolo* arg (e.g. ``--yolo`` CLI flag)
      2. Command-specific config (``ai.<command>.yolo``)
      3. Global config (``ai.yolo``)

    When *tool* is provided and the tool does not support YOLO, a warning
    is logged and ``False`` is returned.
    """
    resolved: bool | None = yolo

    if resolved is None:
        resolved = config.get_yolo(command)

    if not resolved:
        return False

    # Check tool support
    if tool:
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            if not adapter.capabilities().supports_yolo:
                logger.warning("yolo.unsupported_tool", tool=tool)
                return False
        except (ValueError, KeyError):
            pass

    return True


def confirm_ai_selection(
    resolved_tool: str | None,
    resolved_model: str | None,
    *,
    tool_explicit: bool,
    model_explicit: bool,
    resolved_effort: EffortLevel | None = None,
    effort_explicit: bool = False,
    resolved_yolo: bool = False,
    yolo_explicit: bool = True,
) -> tuple[str | None, str | None, EffortLevel | None, bool]:
    """Interactively confirm (and optionally change) the resolved AI tool/model/effort/yolo.

    Fires only when stdin is a TTY and at least one of the flags was not
    explicitly provided by the caller.  When all flags are explicit (e.g.
    because ``wade implement-batch`` passes ``--ai``/``--model``/``--effort`` to
    child calls), this is a no-op.

    Returns the (tool, model, effort, yolo) tuple after any user-driven changes.
    """
    from wade.ui import prompts
    from wade.ui.console import console

    # Skip when non-TTY, no tool resolved, or all flags were explicit.
    all_explicit = tool_explicit and model_explicit and effort_explicit and yolo_explicit
    if not prompts.is_tty() or resolved_tool is None or all_explicit:
        return resolved_tool, resolved_model, resolved_effort, resolved_yolo

    tool = resolved_tool
    model = resolved_model
    effort = resolved_effort
    yolo = resolved_yolo

    while True:
        # Display current selection
        console.kv("AI tool", tool)
        if model:
            console.kv("Model", model)
        if effort:
            console.kv("Effort", effort.value)
        if yolo:
            console.kv("YOLO mode", "on")

        # Build menu dynamically based on which flags were NOT explicit.
        menu_items: list[str] = ["Proceed"]
        installed = AbstractAITool.detect_installed()
        can_change_tool = not tool_explicit and len(installed) > 1
        if can_change_tool:
            menu_items.append("Change AI tool")
        if not model_explicit:
            menu_items.append("Change model")

        # Show "Change effort" only when the tool supports it
        tool_supports_effort = False
        tool_supports_yolo = False
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            caps = adapter.capabilities()
            tool_supports_effort = caps.supports_effort
            tool_supports_yolo = caps.supports_yolo
        except (ValueError, KeyError):
            pass
        if not effort_explicit and tool_supports_effort:
            menu_items.append("Change effort")
        if not yolo_explicit and tool_supports_yolo:
            label = "Turn off YOLO mode" if yolo else "Turn on YOLO mode"
            menu_items.append(label)

        if len(menu_items) == 1:
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
                model = _prompt_model_selection(tool)
                # Clear stale effort/yolo when the new tool doesn't support them.
                try:
                    new_adapter = AbstractAITool.get(AIToolID(tool))
                    new_caps = new_adapter.capabilities()
                    if effort is not None and not new_caps.supports_effort:
                        effort = None
                    if yolo and not new_caps.supports_yolo:
                        yolo = False
                except (ValueError, KeyError):
                    effort = None
                    yolo = False

        elif choice == "Change model":
            model = _prompt_model_selection(tool)

        elif choice == "Change effort":
            effort = _prompt_effort_selection(effort)

        elif choice in ("Turn on YOLO mode", "Turn off YOLO mode"):
            yolo = not yolo

    return tool, model, effort, yolo


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


def _prompt_effort_selection(current: EffortLevel | None) -> EffortLevel | None:
    """Show an effort level picker and return the chosen level (or None)."""
    from wade.ui import prompts

    choices = ["(none — use tool default)", *[e.value for e in EffortLevel]]
    default_idx = 0
    if current:
        default_idx = [e.value for e in EffortLevel].index(current.value) + 1
    idx = prompts.select("Select effort level", choices, default=default_idx)
    if idx == 0:
        return None
    return EffortLevel(choices[idx])

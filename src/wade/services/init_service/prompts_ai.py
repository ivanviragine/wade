"""Init service AI-tool prompts — tool selection and per-tier model mapping.

Interactive prompts for choosing the AI tool, default model/effort/yolo, and the
complexity->model mapping. Imports ``_resolve_models`` from ``config_io``.
"""

from __future__ import annotations

from crossby.ai_tools import AbstractAITool
from crossby.config.defaults import get_defaults
from crossby.models.ai import AIToolID

from wade.models.config import ComplexityModelMapping
from wade.services.init_service.config_io import _resolve_models
from wade.ui.console import console

__all__ = [
    "_collect_model_options",
    "_prompt_ai_section",
    "_prompt_default_model",
    "_prompt_model_mapping",
    "_select_ai_tool",
    "_select_or_skip",
    "_suggest_model_for_tool",
]


def _select_or_skip(
    label: str,
    options: list[str],
    current: str | None = None,
) -> str | None:
    """Select from *options* with a uniform 'Skip (use default)' appended.

    Returns the chosen value, or ``None`` if the user chose Skip.
    When *current* is provided and matches an option it is pre-selected.
    """
    from wade.ui import prompts

    skip_label = "Skip (use default)"
    all_options = [*options, skip_label]

    default_idx = len(all_options) - 1  # default to Skip
    if current and current in all_options:
        default_idx = all_options.index(current)

    idx = prompts.select(label, all_options, default=default_idx)
    chosen = all_options[idx]
    return None if chosen == skip_label else chosen


def _select_ai_tool(
    requested: str | None,
    non_interactive: bool,
    *,
    current_tool: str | None = None,
) -> str | None:
    """Select an AI tool — from argument, detection, or interactive prompt."""
    from wade.ui import prompts

    if requested:
        try:
            AIToolID(requested)
            return requested
        except ValueError:
            valid = ", ".join(e.value for e in AIToolID)
            raise ValueError(f"Unknown AI tool: {requested!r} (valid: {valid})") from None

    # Detect installed tools
    installed = AbstractAITool.detect_installed()

    if not installed:
        console.warn("No AI CLI tools detected. You can set 'ai_tool' in .wade.yml later.")
        return None

    if len(installed) == 1:
        tool = installed[0]
        console.success(f"Detected AI tool: {tool}")
        return tool

    if non_interactive:
        tool = installed[0]
        console.info(f"Using AI tool: {tool}")
        return tool

    # Interactive: show menu with Skip option (rule shown by _prompt_ai_section)
    skip_label = "Skip (configure later)"
    items = [str(t) for t in installed] + [skip_label]

    default_idx = len(items) - 1  # default to Skip
    if current_tool and current_tool in items:
        default_idx = items.index(current_tool)

    idx = prompts.select("Select default AI tool", items, default=default_idx)

    if items[idx] == skip_label:
        return None

    selected = installed[idx]
    console.success(f"Selected AI tool: {selected}")
    return str(selected)


def _prompt_ai_section(
    ai_tool: str | None,
    non_interactive: bool,
    *,
    current_tool: str | None = None,
    current_model: str | None = None,
    current_effort: str | None = None,
    current_yolo: bool | None = None,
) -> tuple[str | None, str | None, str | None, bool | None]:
    """Run the AI wizard section: select default tool, model, effort, and yolo.

    The rule is shown here (before any detection messages) so both the
    single-tool and multi-tool cases are grouped under the same header.

    Returns ``(selected_tool, default_model, default_effort, default_yolo)``.
    """
    if not non_interactive:
        console.rule("AI")
    selected_tool = _select_ai_tool(ai_tool, non_interactive, current_tool=current_tool)
    if non_interactive or not selected_tool:
        return selected_tool, None, None, None
    mapping = _resolve_models(selected_tool)
    default_model = _prompt_default_model(
        selected_tool, mapping, non_interactive=False, current_model=current_model
    )

    default_effort: str | None = None
    default_yolo: bool | None = None
    try:
        adapter = AbstractAITool.get(selected_tool)
        caps = adapter.capabilities()

        # Prompt for default effort level (only when tool supports it)
        if caps.supports_effort:
            from crossby.models.ai import EffortLevel

            from wade.ui import prompts as ui_prompts

            effort_choices = ["(none — use tool default)", *[e.value for e in EffortLevel]]
            current_idx = 0
            if current_effort and current_effort in effort_choices:
                current_idx = effort_choices.index(current_effort)
            idx = ui_prompts.select(
                "Default reasoning effort level", effort_choices, default=current_idx
            )
            # "" sentinel signals explicit "none" so _patch_config can clear on force
            default_effort = effort_choices[idx] if idx > 0 else ""

        # Prompt for global yolo (only when tool supports it)
        if caps.supports_yolo:
            from wade.ui import prompts as ui_prompts

            default_yolo = ui_prompts.confirm(
                "Enable YOLO mode by default? (auto-accepts all AI session prompts)",
                default=current_yolo if current_yolo is not None else False,
            )
    except (ValueError, KeyError):
        pass

    return selected_tool, default_model, default_effort, default_yolo


def _prompt_model_mapping(
    tool: str | None,
    mapping: ComplexityModelMapping,
    non_interactive: bool,
) -> ComplexityModelMapping:
    """Let the user review/edit the complexity-to-model mapping (and per-tier effort).

    If non_interactive, returns the mapping unchanged.
    Falls back to Claude defaults when the tool has no model suggestions.

    Shows a select menu with available models for each tier, plus a Custom
    option for typing a model name directly.  When the tool supports effort,
    also asks for an effort level per tier (skippable).
    """
    from wade.ui import prompts

    if non_interactive:
        return mapping

    # Backfill missing tiers: tool defaults first, then Claude defaults
    tool_defaults = get_defaults(tool) if tool else ComplexityModelMapping()
    claude_defaults = get_defaults(AIToolID.CLAUDE)
    mapping = ComplexityModelMapping(
        easy=mapping.easy or tool_defaults.easy or claude_defaults.easy,
        medium=mapping.medium or tool_defaults.medium or claude_defaults.medium,
        complex=mapping.complex or tool_defaults.complex or claude_defaults.complex,
        very_complex=(
            mapping.very_complex or tool_defaults.very_complex or claude_defaults.very_complex
        ),
        easy_effort=mapping.easy_effort,
        medium_effort=mapping.medium_effort,
        complex_effort=mapping.complex_effort,
        very_complex_effort=mapping.very_complex_effort,
    )

    # Collect model IDs from the registry for the select menu
    available = _collect_model_options(tool)

    # Determine if tool supports effort prompts
    tool_supports_effort = False
    try:
        if tool:
            adapter = AbstractAITool.get(tool)
            tool_supports_effort = adapter.capabilities().supports_effort
    except (ValueError, KeyError):
        pass

    custom_label = "Custom..."
    tiers = [
        ("easy", "Easy tasks", mapping.easy or "", mapping.easy_effort),
        ("medium", "Medium tasks", mapping.medium or "", mapping.medium_effort),
        ("complex", "Complex tasks", mapping.complex or "", mapping.complex_effort),
        (
            "very_complex",
            "Very complex tasks",
            mapping.very_complex or "",
            mapping.very_complex_effort,
        ),
    ]

    result_models: list[str] = []
    result_efforts: list[str | None] = []

    for _tier_key, tier_label, tier_default, current_tier_effort in tiers:
        options = list(available)
        if tier_default and tier_default not in options:
            options.insert(0, tier_default)
        options.append(custom_label)

        default_idx = options.index(tier_default) if tier_default in options else 0
        chosen_idx = prompts.select(tier_label, options, default=default_idx)

        if options[chosen_idx] == custom_label:
            result_models.append(prompts.input_prompt(f"{tier_label} (model ID)", tier_default))
        else:
            result_models.append(options[chosen_idx])

        # Per-tier effort (capability-gated, skippable)
        tier_effort: str | None = current_tier_effort
        if tool_supports_effort:
            from crossby.models.ai import EffortLevel

            effort_choices = ["Skip (inherit defaults)", *[e.value for e in EffortLevel]]
            effort_default_idx = 0
            if current_tier_effort and current_tier_effort in effort_choices:
                effort_default_idx = effort_choices.index(current_tier_effort)
            effort_idx = prompts.select(
                f"  Effort for {tier_label.lower()}", effort_choices, default=effort_default_idx
            )
            tier_effort = effort_choices[effort_idx] if effort_idx > 0 else None
        result_efforts.append(tier_effort)

    return ComplexityModelMapping(
        easy=result_models[0] or mapping.easy,
        medium=result_models[1] or mapping.medium,
        complex=result_models[2] or mapping.complex,
        very_complex=result_models[3] or mapping.very_complex,
        easy_effort=result_efforts[0],
        medium_effort=result_efforts[1],
        complex_effort=result_efforts[2],
        very_complex_effort=result_efforts[3],
    )


def _collect_model_options(
    tool: str | None,
) -> list[str]:
    """Return the full flat model list from the registry for the select menu."""
    if not tool:
        return []
    from crossby.data import get_models_for_tool

    return get_models_for_tool(tool)


def _prompt_default_model(
    tool: str | None,
    model_mapping: ComplexityModelMapping,
    non_interactive: bool,
    *,
    current_model: str | None = None,
) -> str | None:
    """Prompt the user to select a default model for the AI tool.

    This is the fallback model used when no complexity tier is matched and
    no --model flag is passed. It is written to ai.default_model so every
    command inherits it unless an explicit per-command model override exists.

    The complexity tier mapping (easy/medium/complex/very_complex) is left
    unchanged.

    Returns the chosen model ID, or None if the user skips.
    """
    from wade.ui import prompts

    if non_interactive or not tool:
        return None

    available = _collect_model_options(tool)
    if not available:
        return None

    skip_label = "Skip (configure per-complexity)"
    options = [*available, skip_label]

    # Pre-select existing model when re-initializing; fall back to "complex" tier
    default_idx = 0
    if current_model and current_model in options:
        default_idx = options.index(current_model)
    elif model_mapping.complex and model_mapping.complex in options:
        default_idx = options.index(model_mapping.complex)

    idx = prompts.select(f"Select default model for {tool}", options, default=default_idx)
    if options[idx] == skip_label:
        return None

    selected = options[idx]
    console.success(f"Selected model: {selected}")
    return selected


def _suggest_model_for_tool(tool: str) -> str:
    """Get a suggested model for a tool — uses defaults, skips slow probing.

    During interactive init, probing has already happened in _resolve_models().
    Here we just need a quick suggestion, so we use cached defaults only.
    """
    defaults = get_defaults(tool)
    if defaults.complex:
        return defaults.complex

    # Fallback to Claude defaults for unknown tools
    fallback = get_defaults(AIToolID.CLAUDE)
    return fallback.complex or ""

"""Hardcoded defaults per AI tool — fallback when probing fails."""

from __future__ import annotations

from wade.models.ai import AIToolID
from wade.models.config import ComplexityModelMapping

# Default model mappings when tool probing fails or returns no recognized models
TOOL_DEFAULTS: dict[str, ComplexityModelMapping] = {
    AIToolID.CLAUDE: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-sonnet-4.6",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.7",
    ),
    AIToolID.COPILOT: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-sonnet-4.6",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.7",
    ),
    AIToolID.GEMINI: ComplexityModelMapping(
        easy="gemini-3-flash-preview",
        medium="gemini-3-pro-preview",
        complex="gemini-3-pro-preview",
        very_complex="gemini-3-pro-preview",
    ),
    AIToolID.CODEX: ComplexityModelMapping(
        easy="gpt-5.4-mini",
        medium="gpt-5.4",
        complex="gpt-5.4",
        very_complex="gpt-5.4",
    ),
    AIToolID.CURSOR: ComplexityModelMapping(
        easy="gemini-3-flash",
        medium="claude-4.6-sonnet-medium",
        complex="claude-4.6-sonnet-medium",
        very_complex="claude-opus-4-7-high",
    ),
    AIToolID.OPENCODE: ComplexityModelMapping(
        easy="anthropic/claude-haiku-4.5",
        medium="anthropic/claude-sonnet-4.6",
        complex="anthropic/claude-sonnet-4.6",
        very_complex="anthropic/claude-opus-4.7",
    ),
}


def get_defaults(tool: str) -> ComplexityModelMapping:
    """Get default model mapping for a tool.

    Returns empty mapping for unknown tools.
    """
    try:
        from wade.models.ai import AIToolID

        tool_id = AIToolID(tool)
        return TOOL_DEFAULTS.get(tool_id, ComplexityModelMapping())
    except ValueError:
        return ComplexityModelMapping()

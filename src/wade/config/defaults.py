"""Hardcoded defaults per AI tool — fallback when probing fails."""

from __future__ import annotations

from wade.models.ai import AIToolID
from wade.models.config import ComplexityModelMapping

# Default model mappings when tool probing fails or returns no recognized models
TOOL_DEFAULTS: dict[str, ComplexityModelMapping] = {
    AIToolID.CLAUDE: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-haiku-4.5",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.6",
    ),
    AIToolID.COPILOT: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-haiku-4.5",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.6",
    ),
    AIToolID.GEMINI: ComplexityModelMapping(
        easy="gemini-3-flash-preview",
        medium="gemini-3-flash-preview",
        complex="gemini-3-pro-preview",
        very_complex="gemini-3-pro-preview",
    ),
    AIToolID.CODEX: ComplexityModelMapping(
        easy="gpt-5.1-codex-mini",
        medium="gpt-5.1-codex-mini",
        complex="gpt-5.3-codex",
        very_complex="gpt-5.3-codex",
    ),
    AIToolID.CURSOR: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-haiku-4.5",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.6",
    ),
    AIToolID.OPENCODE: ComplexityModelMapping(
        easy="anthropic/claude-haiku-4.5",
        medium="anthropic/claude-haiku-4.5",
        complex="anthropic/claude-sonnet-4.6",
        very_complex="anthropic/claude-opus-4.6",
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

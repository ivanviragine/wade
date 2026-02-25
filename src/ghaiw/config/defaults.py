"""Hardcoded defaults per AI tool — fallback when probing fails.

Behavioral reference: lib/init.sh:_init_set_model_defaults_for_tool()
"""

from __future__ import annotations

from ghaiw.models.ai import AIToolID
from ghaiw.models.config import ComplexityModelMapping

# Default model mappings when tool probing fails or returns no recognized models
TOOL_DEFAULTS: dict[str, ComplexityModelMapping] = {
    AIToolID.CLAUDE: ComplexityModelMapping(
        easy="claude-haiku-4-5",
        medium="claude-haiku-4-5",
        complex="claude-sonnet-4-6",
        very_complex="claude-opus-4-6",
    ),
    AIToolID.COPILOT: ComplexityModelMapping(
        easy="claude-haiku-4.5",
        medium="claude-haiku-4.5",
        complex="claude-sonnet-4.6",
        very_complex="claude-opus-4.6",
    ),
    AIToolID.GEMINI: ComplexityModelMapping(
        easy="gemini-2.0-flash",
        medium="gemini-2.0-flash",
        complex="gemini-2.5-pro",
        very_complex="gemini-2.5-pro",
    ),
    AIToolID.CODEX: ComplexityModelMapping(
        easy="codex-mini-latest",
        medium="codex-mini-latest",
        complex="codex-mini-latest",
        very_complex="codex-mini-latest",
    ),
    AIToolID.OPENCODE: ComplexityModelMapping(
        easy="anthropic/claude-haiku-4-5",
        medium="anthropic/claude-haiku-4-5",
        complex="anthropic/claude-sonnet-4-6",
        very_complex="anthropic/claude-opus-4-6",
    ),
}


def get_defaults(tool: str) -> ComplexityModelMapping:
    """Get default model mapping for a tool.

    Returns empty mapping for unknown tools.
    """
    return TOOL_DEFAULTS.get(tool, ComplexityModelMapping())

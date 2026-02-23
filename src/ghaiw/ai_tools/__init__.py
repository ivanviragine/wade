"""AI tool adapters — ABC with self-registering concrete implementations.

Import all adapters here to trigger __init_subclass__ registration.
"""

from ghaiw.ai_tools.base import AbstractAITool, pick_best_model

# Import adapters to trigger registration
import ghaiw.ai_tools.antigravity  # noqa: F401
import ghaiw.ai_tools.claude  # noqa: F401
import ghaiw.ai_tools.codex  # noqa: F401
import ghaiw.ai_tools.copilot  # noqa: F401
import ghaiw.ai_tools.gemini  # noqa: F401

__all__ = ["AbstractAITool", "pick_best_model"]

"""AI tool adapters — ABC with self-registering concrete implementations.

Import all adapters here to trigger __init_subclass__ registration.
"""

# Import adapters to trigger registration
import wade.ai_tools.antigravity
import wade.ai_tools.claude
import wade.ai_tools.codex
import wade.ai_tools.copilot
import wade.ai_tools.cursor
import wade.ai_tools.gemini
import wade.ai_tools.opencode
import wade.ai_tools.vscode  # noqa: F401
from wade.ai_tools.base import AbstractAITool, pick_best_model

__all__ = ["AbstractAITool", "pick_best_model"]

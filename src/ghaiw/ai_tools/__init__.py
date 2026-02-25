"""AI tool adapters — ABC with self-registering concrete implementations.

Import all adapters here to trigger __init_subclass__ registration.
"""

# Import adapters to trigger registration
import ghaiw.ai_tools.antigravity
import ghaiw.ai_tools.claude
import ghaiw.ai_tools.codex
import ghaiw.ai_tools.copilot
import ghaiw.ai_tools.gemini
import ghaiw.ai_tools.opencode
import ghaiw.ai_tools.vscode  # noqa: F401
from ghaiw.ai_tools.base import AbstractAITool, pick_best_model

__all__ = ["AbstractAITool", "pick_best_model"]

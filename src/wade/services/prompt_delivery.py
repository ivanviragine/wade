"""Prompt delivery — clipboard fallback for tools without initial message support."""

from __future__ import annotations

import structlog
from crossby.ai_tools import AbstractAITool

from wade.ui.console import console
from wade.utils.clipboard import copy_to_clipboard

logger = structlog.get_logger()


def deliver_prompt_if_needed(adapter: AbstractAITool, prompt: str) -> None:
    """Copy prompt to clipboard and inform user if the tool can't accept initial messages.

    No-op for tools that support initial messages (Claude, Copilot, Gemini, etc.).
    For tools that don't (VS Code, Antigravity), copies the prompt to the clipboard
    and shows a hint. If clipboard fails, shows the full prompt for manual copy.
    """
    caps = adapter.capabilities()
    if caps.supports_initial_message:
        return

    tool_name = caps.display_name
    logger.info(
        "prompt_delivery.clipboard_fallback",
        tool=str(caps.tool_id),
        prompt_length=len(prompt),
    )

    if copy_to_clipboard(prompt):
        console.hint(f"Prompt copied to clipboard — paste it into {tool_name} to start")
    else:
        console.warn(f"{tool_name} does not support initial messages and clipboard is unavailable")
        console.hint("Copy the prompt below and paste it manually:")
        console.panel(prompt, title="Prompt")

"""Cross-platform clipboard operations via subprocess.

No pyperclip dependency — uses native commands directly.
"""

from __future__ import annotations

import shutil
import subprocess

import structlog

logger = structlog.get_logger()


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard.

    Tries pbcopy (macOS), then xclip, then xsel.
    Returns True if successful, False if no clipboard tool is available.
    """
    clipboard_tools = [
        ("pbcopy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ]

    for tool, args in clipboard_tools:
        if shutil.which(tool):
            try:
                subprocess.run(
                    [tool, *args],
                    input=text.encode(),
                    check=True,
                    capture_output=True,
                )
                logger.debug("clipboard.copied", tool=tool, length=len(text))
                return True
            except subprocess.CalledProcessError:
                continue

    logger.warning("clipboard.no_tool_available")
    return False

"""Terminal utilities — tab title, TTY detection, terminal launch.

Behavioral reference: lib/common.sh:_set_terminal_title(), _start_title_keeper()
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import structlog

logger = structlog.get_logger()


def is_tty() -> bool:
    """Check if stdout is connected to a terminal."""
    return sys.stdout.isatty()


def set_terminal_title(title: str) -> None:
    """Set the terminal tab title via OSC 0 escape sequence."""
    if not is_tty():
        return
    sys.stderr.write(f"\033]0;{title}\007")
    sys.stderr.flush()


def detect_terminal() -> str | None:
    """Detect the current terminal emulator.

    Returns one of: 'ghostty', 'iterm2', 'terminal.app', 'tmux', 'wezterm', None
    """
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if "ghostty" in term_program:
        return "ghostty"
    if "iterm" in term_program:
        return "iterm2"
    if "apple_terminal" in term_program:
        return "terminal.app"
    if "wezterm" in term_program:
        return "wezterm"

    if os.environ.get("TMUX"):
        return "tmux"

    return None


def launch_in_new_terminal(
    command: list[str],
    cwd: str | None = None,
    title: str | None = None,
) -> bool:
    """Launch a command in a new terminal window/tab.

    Tries Ghostty, iTerm2, Terminal.app, tmux in order.
    Returns True if launched successfully, False otherwise.
    """
    terminal = detect_terminal()
    env = os.environ.copy()

    if terminal == "ghostty" and shutil.which("ghostty"):
        cmd_str = " ".join(command)
        try:
            subprocess.Popen(
                ["ghostty", "-e", cmd_str],
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
            return True
        except OSError:
            pass

    if terminal == "tmux" and shutil.which("tmux"):
        tmux_cmd = ["tmux", "new-window"]
        if title:
            tmux_cmd.extend(["-n", title])
        if cwd:
            tmux_cmd.extend(["-c", cwd])
        tmux_cmd.append(" ".join(command))
        try:
            subprocess.run(tmux_cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            pass

    if sys.platform == "darwin":
        # Use osascript to open a new Terminal.app window
        cmd_str = " ".join(command)
        osa_script = f'tell application "Terminal" to do script "cd {cwd or "."} && {cmd_str}"'
        try:
            subprocess.run(
                ["osascript", "-e", osa_script],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            pass

    logger.warning("terminal.launch_failed", command=command)
    return False

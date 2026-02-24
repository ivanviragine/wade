"""Terminal utilities — tab title, TTY detection, terminal launch.

Behavioral reference: lib/common.sh:_set_terminal_title(), _start_title_keeper()
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time

import structlog

logger = structlog.get_logger()

_title_keeper_thread: threading.Thread | None = None
_title_keeper_running = False


def is_tty() -> bool:
    """Check if stdout is connected to a terminal."""
    return sys.stdout.isatty()


def set_terminal_title(title: str) -> None:
    """Set the terminal tab title via OSC 0 escape sequence."""
    if not is_tty():
        return
    sys.stderr.write(f"\033]0;{title}\007")
    sys.stderr.flush()


def compose_work_title(issue_id: str, issue_title: str) -> str:
    """Compose a terminal title for a work session.

    Format: "ghaiw work #42 — Feature Name"
    Behavioral ref: lib/work/terminal.sh:_work_compose_title()
    """
    max_title = 50
    title = issue_title[:max_title] + "..." if len(issue_title) > max_title else issue_title
    return f"ghaiw work #{issue_id} — {title}"


def start_title_keeper(title: str, interval: float = 2.0) -> None:
    """Start a background thread that re-asserts the terminal title periodically.

    Some tools (AI CLIs) may overwrite the terminal title during their session.
    This background thread re-sets it every ``interval`` seconds via stderr
    (so it works even when stdout is captured/piped).

    Behavioral ref: lib/common.sh:_start_title_keeper()
    """
    global _title_keeper_thread, _title_keeper_running

    stop_title_keeper()  # Stop any existing keeper

    if not is_tty():
        return

    _title_keeper_running = True

    def _keeper() -> None:
        while _title_keeper_running:
            try:
                sys.stderr.write(f"\033]0;{title}\007")
                sys.stderr.flush()
            except (OSError, ValueError):
                break
            time.sleep(interval)

    _title_keeper_thread = threading.Thread(target=_keeper, daemon=True)
    _title_keeper_thread.start()


def stop_title_keeper() -> None:
    """Stop the background title keeper thread."""
    global _title_keeper_running, _title_keeper_thread
    _title_keeper_running = False
    if _title_keeper_thread is not None:
        _title_keeper_thread.join(timeout=3.0)
        _title_keeper_thread = None


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

    if shutil.which("gnome-terminal"):
        return "gnome-terminal"

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

    if terminal == "ghostty":
        cmd_str = " ".join(shlex.quote(str(c)) for c in command)
        if sys.platform == "darwin":
            import tempfile

            with tempfile.NamedTemporaryFile(
                prefix="ghaiw-", dir="/tmp", suffix="", delete=False, mode="w"
            ) as f:
                tmp_path = f.name
                f.write(f"#!/usr/bin/env bash\ncd '{cwd or '.'}'\nexec {cmd_str}\n")
            os.chmod(tmp_path, 0o755)
            osa = f"""tell application "Ghostty" to activate
delay 0.3
tell application "System Events"
  tell process "Ghostty"
    tell menu bar 1
      tell menu bar item "File"
        tell menu "File"
          click menu item "New Tab"
        end tell
      end tell
    end tell
  end tell
end tell
delay 1.0
keystroke "{tmp_path}"
key code 36"""
            try:
                subprocess.run(["osascript", "-e", osa], check=True, capture_output=True)
                threading.Timer(15, lambda: os.unlink(tmp_path)).start()
                return True
            except subprocess.CalledProcessError:
                try:
                    subprocess.run(
                        [
                            "open",
                            "-na",
                            "Ghostty",
                            "--args",
                            f"--working-directory={cwd or '.'}",
                            "-e",
                            "bash",
                            "-c",
                            cmd_str,
                        ],
                        check=True,
                        capture_output=True,
                    )
                    return True
                except subprocess.CalledProcessError:
                    pass
        else:
            ghostty_bin = os.environ.get("GHOSTTY_BIN_DIR", "")
            ghostty_bin = f"{ghostty_bin}/ghostty" if ghostty_bin else "ghostty"
            try:
                subprocess.Popen(
                    [
                        ghostty_bin,
                        "+new-window",
                        "-e",
                        "bash",
                        "-c",
                        f"cd '{cwd or '.'}' && {cmd_str}",
                    ],
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

    if terminal == "iterm2" and sys.platform == "darwin":
        cmd_str = " ".join(shlex.quote(str(c)) for c in command)
        osa_script = (
            f'tell application "iTerm2" to create window with default profile '
            f"command \"cd '{cwd or '.'}' && {cmd_str}\""
        )
        try:
            subprocess.run(["osascript", "-e", osa_script], check=True, capture_output=True)
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

    if terminal == "gnome-terminal" and sys.platform != "darwin" and shutil.which("gnome-terminal"):
        cmd_str = " ".join(shlex.quote(str(c)) for c in command)
        try:
            subprocess.Popen(
                [
                    "gnome-terminal",
                    "--",
                    "bash",
                    "-c",
                    f"cd '{cwd or '.'}' && {cmd_str}; exec bash",
                ],
                start_new_session=True,
            )
            return True
        except OSError:
            pass

    logger.warning("terminal.launch_failed", command=command)
    return False

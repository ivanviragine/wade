"""Terminal utilities — tab title, TTY detection, terminal launch."""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence

import structlog

logger = structlog.get_logger()

_title_keeper_thread: threading.Thread | None = None
_title_keeper_running = False

_TERMINAL_TITLE_MAX_LEN = 50


def _truncate_terminal_title(text: str) -> str:
    return text[:_TERMINAL_TITLE_MAX_LEN] + "..." if len(text) > _TERMINAL_TITLE_MAX_LEN else text


def is_tty() -> bool:
    """Check if stdout is connected to a terminal."""
    return sys.stdout.isatty()


def set_terminal_title(title: str) -> None:
    """Set the terminal tab title via OSC 0 escape sequence."""
    if not is_tty():
        return
    sys.stderr.write(f"\033]0;{title}\007")
    sys.stderr.flush()


def compose_implement_title(issue_id: str, issue_title: str) -> str:
    """Compose a terminal title for an implementation session.

    Format: "wade implement #42 — Feature Name"
    """
    return f"wade implement #{issue_id} — {_truncate_terminal_title(issue_title)}"


def compose_review_title(issue_id: str, issue_title: str) -> str:
    """Compose a terminal title for a review pr-comments session.

    Format: "wade review pr-comments #42 — Feature Name"
    """
    return f"wade review pr-comments #{issue_id} — {_truncate_terminal_title(issue_title)}"


def compose_plan_title(issue_id: str | None, issue_title: str | None) -> str:
    """Compose a terminal title for a plan session.

    Format: "wade plan #42 — Feature Name" with issue, "wade plan" without.
    """
    if not issue_id:
        return "wade plan"
    title = _truncate_terminal_title(issue_title or "")
    return f"wade plan #{issue_id} — {title}" if title else f"wade plan #{issue_id}"


def start_title_keeper(title: str, interval: float = 2.0) -> None:
    """Start a background thread that re-asserts the terminal title periodically.

    Some tools (AI CLIs) may overwrite the terminal title during their session.
    This background thread re-sets it every ``interval`` seconds via stderr
    (so it works even when stdout is captured/piped).
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


def _safe_unlink(path: str) -> None:
    """Remove a file, ignoring errors if it's already gone."""
    with contextlib.suppress(OSError):
        os.unlink(path)


def _create_temp_script(command: list[str], cwd: str | None = None) -> str:
    """Create a temporary executable bash script that runs *command* in *cwd*.

    Returns the path to the script.  Caller is responsible for cleanup
    (typically via a ``threading.Timer`` calling ``_safe_unlink``).
    """
    import tempfile

    cmd_str = " ".join(shlex.quote(str(c)) for c in command)
    with tempfile.NamedTemporaryFile(prefix="wade-", suffix=".sh", delete=False, mode="w") as f:
        tmp_path = f.name
        f.write(f"#!/usr/bin/env bash\ncd {shlex.quote(cwd or '.')} || exit $?\nexec {cmd_str}\n")
    os.chmod(tmp_path, 0o700)
    return tmp_path


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
        if sys.platform == "darwin":
            tmp_path = _create_temp_script(command, cwd)
            try:
                subprocess.run(
                    ["open", "-na", "Ghostty", "--args", "-e", tmp_path],
                    check=True,
                    capture_output=True,
                )
                t = threading.Timer(15, lambda: _safe_unlink(tmp_path))
                t.daemon = True
                t.start()
                return True
            except subprocess.CalledProcessError:
                _safe_unlink(tmp_path)
        else:
            ghostty_bin = os.environ.get("GHOSTTY_BIN_DIR", "")
            ghostty_bin = f"{ghostty_bin}/ghostty" if ghostty_bin else "ghostty"
            tmp_path = _create_temp_script(command, cwd)
            try:
                subprocess.Popen(
                    [ghostty_bin, "+new-window", "-e", tmp_path],
                    start_new_session=True,
                )
                t = threading.Timer(15, lambda: _safe_unlink(tmp_path))
                t.daemon = True
                t.start()
                return True
            except OSError:
                _safe_unlink(tmp_path)

    if terminal == "tmux" and shutil.which("tmux"):
        tmux_cmd = ["tmux", "new-window"]
        if title:
            tmux_cmd.extend(["-n", title])
        if cwd:
            tmux_cmd.extend(["-c", cwd])
        tmux_cmd.append(" ".join(shlex.quote(str(c)) for c in command))
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

    if terminal == "wezterm" and shutil.which("wezterm"):
        cmd_str = " ".join(shlex.quote(str(c)) for c in command)
        wez_cmd = ["wezterm", "cli", "spawn", "--"]
        if cwd:
            wez_cmd = ["wezterm", "cli", "spawn", "--cwd", cwd, "--"]
        wez_cmd.extend(["bash", "-c", cmd_str])
        try:
            subprocess.run(wez_cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            pass

    if sys.platform == "darwin":
        tmp_script = _create_temp_script(command, cwd)
        osa_script = f'tell application "Terminal" to do script "{tmp_script}"'
        try:
            subprocess.run(
                ["osascript", "-e", osa_script],
                check=True,
                capture_output=True,
            )
            # Clean up temp script after a delay to let Terminal.app read it
            t = threading.Timer(15, lambda: _safe_unlink(tmp_script))
            t.daemon = True
            t.start()
            return True
        except subprocess.CalledProcessError:
            _safe_unlink(tmp_script)

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


# ---------------------------------------------------------------------------
# Batch launcher — open ONE window with N tabs
# ---------------------------------------------------------------------------

_BATCH_TAB_DELAY = 0.5  # seconds between tab operations in AppleScript


def launch_batch_in_terminals(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """Launch multiple commands in a single new window with one tab per command.

    *items* is a list of ``(command, cwd, title)`` tuples.
    Returns True if at least the first command was launched successfully.
    """
    if not items:
        return False

    if len(items) == 1:
        cmd, cwd, title = items[0]
        return launch_in_new_terminal(cmd, cwd=cwd, title=title)

    terminal = detect_terminal()

    if terminal == "ghostty":
        if sys.platform == "darwin":
            return _batch_ghostty_macos(items)
        return _batch_ghostty_linux(items)

    if terminal == "iterm2" and sys.platform == "darwin":
        return _batch_iterm2(items)

    if terminal == "tmux" and shutil.which("tmux"):
        return _batch_tmux(items)

    if terminal == "wezterm" and shutil.which("wezterm"):
        return _batch_wezterm(items)

    if terminal == "terminal.app" and sys.platform == "darwin":
        return _batch_terminal_app(items)

    if sys.platform == "darwin":
        # Unknown terminal on macOS — fall back to Terminal.app batch
        return _batch_terminal_app(items)

    if terminal == "gnome-terminal" and shutil.which("gnome-terminal"):
        return _batch_gnome_terminal(items)

    # Final fallback: launch each individually
    return _batch_fallback(items)


def _escape_applescript_string(s: str) -> str:
    """Escape a string for embedding in AppleScript double-quoted string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _batch_ghostty_macos(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """Ghostty macOS: AppleScript to create window + tabs."""
    scripts: list[str] = []
    for cmd, cwd, _title in items:
        scripts.append(_create_temp_script(cmd, cwd))

    # Build AppleScript: first command opens a new window, subsequent add tabs
    osa_lines = [
        # Launch first window via open -na (reliable new-window)
        f'do shell script "open -na Ghostty --args -e {shlex.quote(scripts[0])}"',
        f"delay {_BATCH_TAB_DELAY + 0.5}",
        'tell application "Ghostty" to activate',
        f"delay {_BATCH_TAB_DELAY}",
    ]

    for script_path in scripts[1:]:
        osa_lines.extend(
            [
                'tell application "System Events"',
                '  tell process "Ghostty"',
                "    tell menu bar 1",
                '      tell menu bar item "Shell"',
                '        tell menu "Shell"',
                '          click menu item "New Tab"',
                "        end tell",
                "      end tell",
                "    end tell",
                f"    delay {_BATCH_TAB_DELAY}",
                f'    set the clipboard to "{_escape_applescript_string(script_path)}"',
                '    keystroke "v" using command down',
                "    key code 36",
                "  end tell",
                "end tell",
                f"delay {_BATCH_TAB_DELAY}",
            ]
        )

    osa = "\n".join(osa_lines)
    try:
        subprocess.run(["osascript", "-e", osa], check=True, capture_output=True)
        # Schedule cleanup for all temp scripts
        for sp in scripts:
            t = threading.Timer(15, lambda p=sp: _safe_unlink(p))
            t.daemon = True
            t.start()
        return True
    except subprocess.CalledProcessError:
        logger.warning("terminal.batch_ghostty_macos_failed")
        for sp in scripts:
            _safe_unlink(sp)
        return False


def _batch_ghostty_linux(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """Ghostty Linux: separate windows (tabs not exposed via CLI)."""
    ghostty_bin = os.environ.get("GHOSTTY_BIN_DIR", "")
    ghostty_bin = f"{ghostty_bin}/ghostty" if ghostty_bin else "ghostty"
    launched = False
    for cmd, cwd, _title in items:
        tmp_path = _create_temp_script(cmd, cwd)
        try:
            subprocess.Popen(
                [ghostty_bin, "+new-window", "-e", tmp_path],
                start_new_session=True,
            )
            t = threading.Timer(15, lambda p=tmp_path: _safe_unlink(p))
            t.daemon = True
            t.start()
            launched = True
        except OSError:
            _safe_unlink(tmp_path)
        time.sleep(_BATCH_TAB_DELAY)
    return launched


def _batch_iterm2(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """iTerm2: AppleScript to create window + tabs."""
    scripts: list[str] = []
    for cmd, cwd, _title in items:
        scripts.append(_create_temp_script(cmd, cwd))

    # First item creates a new window; subsequent items create tabs in it
    osa_lines = [
        'tell application "iTerm2"',
        f'  set newWindow to (create window with default profile command "{scripts[0]}")',
    ]
    for sp in scripts[1:]:
        osa_lines.append(f'  tell newWindow to create tab with default profile command "{sp}"')
    osa_lines.append("end tell")
    osa = "\n".join(osa_lines)
    try:
        subprocess.run(["osascript", "-e", osa], check=True, capture_output=True)
        for sp in scripts:
            t = threading.Timer(15, lambda p=sp: _safe_unlink(p))
            t.daemon = True
            t.start()
        return True
    except subprocess.CalledProcessError:
        logger.warning("terminal.batch_iterm2_failed")
        for sp in scripts:
            _safe_unlink(sp)
        return False


def _batch_tmux(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """tmux: sequential new-window (tmux windows are tabs)."""
    launched = False
    for cmd, cwd, title in items:
        tmux_cmd = ["tmux", "new-window"]
        if title:
            tmux_cmd.extend(["-n", title])
        if cwd:
            tmux_cmd.extend(["-c", cwd])
        tmux_cmd.append(" ".join(shlex.quote(str(c)) for c in cmd))
        try:
            subprocess.run(tmux_cmd, check=True, capture_output=True)
            launched = True
        except subprocess.CalledProcessError:
            pass
    return launched


def _batch_wezterm(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """WezTerm: first via spawn --new-window, additional via spawn (adds tab)."""
    launched = False
    for i, (cmd, cwd, _title) in enumerate(items):
        cmd_str = " ".join(shlex.quote(str(c)) for c in cmd)
        if i == 0:
            wez_cmd = ["wezterm", "cli", "spawn", "--new-window"]
        else:
            wez_cmd = ["wezterm", "cli", "spawn"]
        if cwd:
            wez_cmd.extend(["--cwd", cwd])
        wez_cmd.extend(["--", "bash", "-c", cmd_str])
        try:
            subprocess.run(wez_cmd, check=True, capture_output=True)
            launched = True
        except subprocess.CalledProcessError:
            pass
    return launched


def _batch_terminal_app(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """Terminal.app: AppleScript do script in same window."""
    scripts: list[str] = []
    for cmd, cwd, _title in items:
        scripts.append(_create_temp_script(cmd, cwd))

    # First command creates a window; additional run in the same window (new tab)
    escaped_scripts = [_escape_applescript_string(sp) for sp in scripts]
    osa_lines = [
        'tell application "Terminal"',
        f'  do script "{escaped_scripts[0]}"',
        "  set theWindow to front window",
    ]
    for esp in escaped_scripts[1:]:
        osa_lines.append(f'  do script "{esp}" in theWindow')
    osa_lines.append("end tell")
    osa = "\n".join(osa_lines)
    try:
        subprocess.run(["osascript", "-e", osa], check=True, capture_output=True)
        for sp in scripts:
            t = threading.Timer(15, lambda p=sp: _safe_unlink(p))
            t.daemon = True
            t.start()
        return True
    except subprocess.CalledProcessError:
        logger.warning("terminal.batch_terminal_app_failed")
        for sp in scripts:
            _safe_unlink(sp)
        return False


def _batch_gnome_terminal(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """GNOME Terminal: single invocation with --window and --tab flags."""
    scripts: list[str] = []
    gnome_cmd: list[str] = ["gnome-terminal", "--window"]

    for i, (cmd, cwd, title) in enumerate(items):
        tmp_path = _create_temp_script(cmd, cwd)
        scripts.append(tmp_path)
        if i > 0:
            gnome_cmd.append("--tab")
        if title:
            gnome_cmd.extend(["--title", title])
        gnome_cmd.extend(["--", tmp_path])

    try:
        subprocess.Popen(gnome_cmd, start_new_session=True)
        for sp in scripts:
            t = threading.Timer(15, lambda p=sp: _safe_unlink(p))
            t.daemon = True
            t.start()
        return True
    except OSError:
        for sp in scripts:
            _safe_unlink(sp)
        logger.warning("terminal.batch_gnome_failed")
        return False


def _batch_fallback(
    items: Sequence[tuple[list[str], str | None, str | None]],
) -> bool:
    """Fallback: launch each command individually."""
    launched = False
    for cmd, cwd, title in items:
        if launch_in_new_terminal(cmd, cwd=cwd, title=title):
            launched = True
        time.sleep(_BATCH_TAB_DELAY)
    return launched

from __future__ import annotations

import subprocess
import sys
from unittest.mock import Mock

import pytest

from wade.utils.terminal import (
    detect_terminal,
    launch_batch_in_terminals,
    launch_in_new_terminal,
)

# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


def test_iterm2_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)

    assert detect_terminal() == "iterm2"


def test_gnome_terminal_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("wade.utils.terminal.shutil.which", _which)

    assert detect_terminal() == "gnome-terminal"


# ---------------------------------------------------------------------------
# iTerm2 tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific AppleScript behavior")
def test_iterm2_launch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: None)

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = run_mock.call_args.args[0]
    assert args[0] == "osascript"
    assert args[1] == "-e"
    assert 'tell application "iTerm2"' in args[2]
    assert "create window with default profile command" in args[2]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific AppleScript behavior")
def test_iterm2_launch_before_terminal_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: None)

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    assert run_mock.call_count == 1
    script = run_mock.call_args.args[0][2]
    assert 'tell application "iTerm2"' in script
    assert 'tell application "Terminal"' not in script


# ---------------------------------------------------------------------------
# GNOME Terminal tests
# ---------------------------------------------------------------------------


def test_gnome_terminal_launch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.sys.platform", "linux")

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("wade.utils.terminal.shutil.which", _which)

    popen_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.Popen", popen_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = popen_mock.call_args.args[0]
    assert args[0] == "gnome-terminal"
    assert args[1:4] == ["--", "bash", "-c"]
    assert "; exec bash" in args[4]


def test_gnome_terminal_not_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.sys.platform", "darwin")

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("wade.utils.terminal.shutil.which", _which)

    popen_mock = Mock(return_value=Mock())
    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.Popen", popen_mock)
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)

    launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    popen_calls = [c.args[0][0] for c in popen_mock.call_args_list]
    assert "gnome-terminal" not in popen_calls


# ---------------------------------------------------------------------------
# Ghostty macOS tests — now uses open -na Ghostty --args -e <tmp_script>
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty behavior")
def test_ghostty_macos_uses_open_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ghostty macOS launches via `open -na Ghostty --args -e <tmp_script>`."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    run_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    # Mock _create_temp_script to return a known path
    monkeypatch.setattr(
        "wade.utils.terminal._create_temp_script", lambda cmd, cwd=None: "/tmp/wade-test.sh"
    )

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    run_args = run_mock.call_args.args[0]
    assert run_args == ["open", "-na", "Ghostty", "--args", "-e", "/tmp/wade-test.sh"]


@pytest.mark.skipif(sys.platform == "darwin", reason="Linux-specific Ghostty subprocess behavior")
def test_ghostty_linux_uses_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.sys.platform", "linux")
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/usr/bin/ghostty")

    popen_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.Popen", popen_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)
    monkeypatch.setattr(
        "wade.utils.terminal._create_temp_script", lambda cmd, cwd=None: "/tmp/wade-test.sh"
    )

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = popen_mock.call_args.args[0]
    assert "+new-window" in args
    assert "/tmp/wade-test.sh" in args


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty behavior")
def test_ghostty_macos_temp_script_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies _create_temp_script is called and its output used."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    create_mock = Mock(return_value="/tmp/wade-test.sh")
    monkeypatch.setattr("wade.utils.terminal._create_temp_script", create_mock)

    run_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    create_mock.assert_called_once_with(["python", "-V"], "/tmp")
    # Timer scheduled for cleanup
    timer_mock.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty behavior")
def test_ghostty_macos_failure_cleans_temp_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """When open -na fails, tmp_path is cleaned up."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    monkeypatch.setattr(
        "wade.utils.terminal._create_temp_script", lambda cmd, cwd=None: "/tmp/wade-test.sh"
    )

    # open -na fails, then Terminal.app fallback also fails
    def _run_side_effect(*args: object, **kwargs: object) -> Mock:
        cmd = args[0]
        if isinstance(cmd, list):
            raise subprocess.CalledProcessError(1, cmd[0])
        return Mock()

    monkeypatch.setattr("wade.utils.terminal.subprocess.run", _run_side_effect)

    unlink_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal._safe_unlink", unlink_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is False
    unlink_mock.assert_any_call("/tmp/wade-test.sh")


# ---------------------------------------------------------------------------
# _create_temp_script tests
# ---------------------------------------------------------------------------


def test_create_temp_script_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify temp script has shebang, cd, and exec."""
    from wade.utils.terminal import _create_temp_script

    written: list[str] = []

    class _Tmp:
        name = "/tmp/wade-test.sh"

        def __enter__(self) -> _Tmp:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def write(self, data: str) -> int:
            written.append(data)
            return len(data)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **_: _Tmp())
    chmod_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal.os.chmod", chmod_mock)

    path = _create_temp_script(["python", "-V"], cwd="/tmp")

    assert path == "/tmp/wade-test.sh"
    assert chmod_mock.called
    assert written
    script = written[0]
    assert script.startswith("#!/usr/bin/env bash")
    assert "cd" in script
    assert "exec" in script


# ---------------------------------------------------------------------------
# Batch launcher tests
# ---------------------------------------------------------------------------


def test_batch_empty_returns_false() -> None:
    """Empty items list returns False."""
    assert launch_batch_in_terminals([]) is False


def test_batch_single_delegates_to_launch_in_new_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single item delegates to launch_in_new_terminal."""
    mock = Mock(return_value=True)
    monkeypatch.setattr("wade.utils.terminal.launch_in_new_terminal", mock)

    result = launch_batch_in_terminals([(["python", "-V"], "/tmp", "test")])

    assert result is True
    mock.assert_called_once_with(["python", "-V"], cwd="/tmp", title="test")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty batch behavior")
def test_batch_ghostty_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ghostty macOS batch uses AppleScript with window + tabs."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)

    script_counter = [0]

    def _fake_create(cmd: list[str], cwd: str | None = None) -> str:
        script_counter[0] += 1
        return f"/tmp/wade-batch-{script_counter[0]}.sh"

    monkeypatch.setattr("wade.utils.terminal._create_temp_script", _fake_create)

    run_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
        (["wade", "implement", "3"], "/repo", "wade #3"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is True
    # Should call osascript once with a compound AppleScript
    run_args = run_mock.call_args.args[0]
    assert run_args[0] == "osascript"
    osa = run_args[2]
    assert "open -na Ghostty" in osa
    assert "New Tab" in osa
    # Three temp scripts created, three timers for cleanup
    assert timer_mock.call_count == 3


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty batch behavior")
def test_batch_ghostty_macos_failure_cleans_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Ghostty macOS batch fails, all temp scripts are cleaned."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)

    script_counter = [0]

    def _fake_create(cmd: list[str], cwd: str | None = None) -> str:
        script_counter[0] += 1
        return f"/tmp/wade-batch-{script_counter[0]}.sh"

    monkeypatch.setattr("wade.utils.terminal._create_temp_script", _fake_create)

    def _run_fail(*args: object, **kwargs: object) -> Mock:
        raise subprocess.CalledProcessError(1, "osascript")

    monkeypatch.setattr("wade.utils.terminal.subprocess.run", _run_fail)

    unlink_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal._safe_unlink", unlink_mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is False
    assert unlink_mock.call_count == 2


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific iTerm2 batch behavior")
def test_batch_iterm2(monkeypatch: pytest.MonkeyPatch) -> None:
    """iTerm2 batch creates a window with tabs via AppleScript."""
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)

    script_counter = [0]

    def _fake_create(cmd: list[str], cwd: str | None = None) -> str:
        script_counter[0] += 1
        return f"/tmp/wade-batch-{script_counter[0]}.sh"

    monkeypatch.setattr("wade.utils.terminal._create_temp_script", _fake_create)

    run_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is True
    run_args = run_mock.call_args.args[0]
    assert run_args[0] == "osascript"
    osa = run_args[2]
    assert 'tell application "iTerm2"' in osa
    assert "create window with default profile command" in osa
    assert "create tab with default profile command" in osa


def test_batch_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    """tmux batch creates sequential new-window commands."""
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/usr/bin/tmux")

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is True
    assert run_mock.call_count == 2
    # Both calls should be tmux new-window
    for c in run_mock.call_args_list:
        args = c.args[0]
        assert args[0] == "tmux"
        assert args[1] == "new-window"


def test_batch_wezterm(monkeypatch: pytest.MonkeyPatch) -> None:
    """WezTerm batch uses wezterm cli spawn with --new-window for first tab."""
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/usr/bin/wezterm")

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is True
    assert run_mock.call_count == 2
    # First call uses --new-window; second does not
    first_args = run_mock.call_args_list[0].args[0]
    assert first_args[:3] == ["wezterm", "cli", "spawn"]
    assert "--new-window" in first_args
    second_args = run_mock.call_args_list[1].args[0]
    assert second_args[:3] == ["wezterm", "cli", "spawn"]
    assert "--new-window" not in second_args


def test_batch_fallback_loops_individual(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no terminal detected, falls back to individual launches."""
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.sys.platform", "linux")
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: None)

    mock = Mock(return_value=True)
    monkeypatch.setattr("wade.utils.terminal.launch_in_new_terminal", mock)

    items = [
        (["wade", "implement", "1"], "/repo", "wade #1"),
        (["wade", "implement", "2"], "/repo", "wade #2"),
    ]
    result = launch_batch_in_terminals(items)

    assert result is True
    assert mock.call_count == 2

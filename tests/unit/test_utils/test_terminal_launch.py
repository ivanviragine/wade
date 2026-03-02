from __future__ import annotations

import subprocess
import sys
from unittest.mock import Mock

import pytest

from wade.utils.terminal import detect_terminal, launch_in_new_terminal


def test_iterm2_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)

    assert detect_terminal() == "iterm2"


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


def test_gnome_terminal_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("wade.utils.terminal.shutil.which", _which)

    assert detect_terminal() == "gnome-terminal"


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


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty AppleScript behavior")
def test_ghostty_macos_uses_applescript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    run_mock = Mock(return_value=Mock())
    popen_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.subprocess.Popen", popen_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    run_args = run_mock.call_args.args[0]
    assert run_args[0] == "osascript"
    assert run_args[1] == "-e"
    assert 'tell application "Ghostty"' in run_args[2]
    popen_calls = [c.args[0][0] for c in popen_mock.call_args_list]
    assert "ghostty" not in popen_calls


@pytest.mark.skipif(sys.platform == "darwin", reason="Linux-specific Ghostty subprocess behavior")
def test_ghostty_linux_uses_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.sys.platform", "linux")
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/usr/bin/ghostty")

    popen_mock = Mock(return_value=Mock())
    monkeypatch.setattr("wade.utils.terminal.subprocess.Popen", popen_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = popen_mock.call_args.args[0]
    assert "+new-window" in args


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty AppleScript behavior")
def test_ghostty_macos_temp_script_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    written: list[str] = []

    class _Tmp:
        name = "/tmp/wade-test"

        def __enter__(self) -> _Tmp:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def write(self, data: str) -> int:
            written.append(data)
            return len(data)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **_: _Tmp())
    chmod_mock = Mock()
    run_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("wade.utils.terminal.os.chmod", chmod_mock)
    monkeypatch.setattr("wade.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("wade.utils.terminal.threading.Timer", timer_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    assert chmod_mock.called
    assert written
    script = written[0]
    assert script.startswith("#!/usr/bin/env bash")
    assert "exec" in script


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty fallback behavior")
def test_ghostty_macos_fallback_cleans_temp_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AppleScript fails, tmp_path is cleaned up before the open -na fallback."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    class _Tmp:
        name = "/tmp/wade-ghostty-test"

        def __enter__(self) -> _Tmp:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def write(self, data: str) -> int:
            return len(data)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **_: _Tmp())
    chmod_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal.os.chmod", chmod_mock)

    # AppleScript fails, then fallback succeeds
    def _run_side_effect(*args: object, **kwargs: object) -> Mock:
        cmd = args[0]
        if isinstance(cmd, list) and cmd[0] == "osascript":
            raise subprocess.CalledProcessError(1, "osascript")
        return Mock()

    monkeypatch.setattr("wade.utils.terminal.subprocess.run", _run_side_effect)

    unlink_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal._safe_unlink", unlink_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    unlink_mock.assert_called_once_with("/tmp/wade-ghostty-test")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty fallback behavior")
def test_ghostty_macos_both_fail_cleans_temp_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both AppleScript and open -na fallback fail, tmp_path is still cleaned up."""
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("wade.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    class _Tmp:
        name = "/tmp/wade-ghostty-test"

        def __enter__(self) -> _Tmp:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def write(self, data: str) -> int:
            return len(data)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **_: _Tmp())
    chmod_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal.os.chmod", chmod_mock)

    # Both AppleScript and fallback fail
    def _run_side_effect(*args: object, **kwargs: object) -> Mock:
        cmd = args[0]
        if isinstance(cmd, list):
            raise subprocess.CalledProcessError(1, cmd[0])
        return Mock()

    monkeypatch.setattr("wade.utils.terminal.subprocess.run", _run_side_effect)

    unlink_mock = Mock()
    monkeypatch.setattr("wade.utils.terminal._safe_unlink", unlink_mock)

    # Falls through to Terminal.app path, which also fails — overall returns False
    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is False
    unlink_mock.assert_any_call("/tmp/wade-ghostty-test")

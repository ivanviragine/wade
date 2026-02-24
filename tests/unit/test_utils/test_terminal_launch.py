from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest

from ghaiw.utils.terminal import detect_terminal, launch_in_new_terminal


def test_iterm2_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)

    assert detect_terminal() == "iterm2"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific AppleScript behavior")
def test_iterm2_launch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", lambda _: None)

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.run", run_mock)

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
    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", lambda _: None)

    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.run", run_mock)

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

    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", _which)

    assert detect_terminal() == "gnome-terminal"


def test_gnome_terminal_launch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.sys.platform", "linux")

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", _which)

    popen_mock = Mock(return_value=Mock())
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.Popen", popen_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = popen_mock.call_args.args[0]
    assert args[0] == "gnome-terminal"
    assert args[1:4] == ["--", "bash", "-c"]
    assert "; exec bash" in args[4]


def test_gnome_terminal_not_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.sys.platform", "darwin")

    def _which(name: str) -> str | None:
        return "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None

    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", _which)

    popen_mock = Mock(return_value=Mock())
    run_mock = Mock(return_value=Mock())
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.Popen", popen_mock)
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.run", run_mock)

    launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    popen_calls = [call.args[0][0] for call in popen_mock.call_args_list]
    assert "gnome-terminal" not in popen_calls


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty AppleScript behavior")
def test_ghostty_macos_uses_applescript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    run_mock = Mock(return_value=Mock())
    popen_mock = Mock(return_value=Mock())
    timer_mock = Mock()
    timer_mock.return_value.start = Mock()
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.Popen", popen_mock)
    monkeypatch.setattr("ghaiw.utils.terminal.threading.Timer", timer_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    run_args = run_mock.call_args.args[0]
    assert run_args[0] == "osascript"
    assert run_args[1] == "-e"
    assert 'tell application "Ghostty"' in run_args[2]
    popen_calls = [call.args[0][0] for call in popen_mock.call_args_list]
    assert "ghostty" not in popen_calls


@pytest.mark.skipif(sys.platform == "darwin", reason="Linux-specific Ghostty subprocess behavior")
def test_ghostty_linux_uses_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.sys.platform", "linux")
    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", lambda _: "/usr/bin/ghostty")

    popen_mock = Mock(return_value=Mock())
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.Popen", popen_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    args = popen_mock.call_args.args[0]
    assert "+new-window" in args


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific Ghostty AppleScript behavior")
def test_ghostty_macos_temp_script_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("ghaiw.utils.terminal.shutil.which", lambda _: "/opt/homebrew/bin/ghostty")

    written: list[str] = []

    class _Tmp:
        name = "/tmp/ghaiw-test"

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
    monkeypatch.setattr("ghaiw.utils.terminal.os.chmod", chmod_mock)
    monkeypatch.setattr("ghaiw.utils.terminal.subprocess.run", run_mock)
    monkeypatch.setattr("ghaiw.utils.terminal.threading.Timer", timer_mock)

    result = launch_in_new_terminal(["python", "-V"], cwd="/tmp")

    assert result is True
    assert chmod_mock.called
    assert written
    script = written[0]
    assert script.startswith("#!/usr/bin/env bash")
    assert "exec" in script

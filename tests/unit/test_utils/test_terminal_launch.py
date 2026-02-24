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

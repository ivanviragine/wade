"""Tests for subprocess utilities."""

from __future__ import annotations

import pytest

from ghaiw.utils.process import CommandError, run, run_silent


class TestRun:
    def test_success(self) -> None:
        result = run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_failure_raises(self) -> None:
        with pytest.raises(CommandError) as exc_info:
            run(["false"])
        assert exc_info.value.returncode != 0

    def test_failure_no_check(self) -> None:
        result = run(["false"], check=False)
        assert result.returncode != 0

    def test_command_not_found(self) -> None:
        with pytest.raises(CommandError) as exc_info:
            run(["nonexistent_command_xyz"])
        assert exc_info.value.returncode == 127


class TestRunSilent:
    def test_success(self) -> None:
        assert run_silent(["true"]) is True

    def test_failure(self) -> None:
        assert run_silent(["false"]) is False

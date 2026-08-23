"""Tests for ui/prompts.py — select() back navigation and index mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import questionary
import typer
from questionary.prompts.common import InquirerControl

from wade.ui.prompts import _enable_choice_wrapping, confirm, select


def _mock_questionary_result(return_value: object) -> MagicMock:
    """Return a MagicMock that simulates questionary.select(...).ask() = return_value."""
    question = MagicMock()
    question.ask.return_value = return_value
    return question


def _choice_window_wraps(question: questionary.Question) -> bool:
    """True if the question's InquirerControl window has wrap_lines enabled."""
    windows = question.application.layout.find_all_windows()
    ic_windows = [w for w in windows if isinstance(w.content, InquirerControl)]
    assert ic_windows, "expected at least one InquirerControl window"
    return all(bool(w.wrap_lines()) for w in ic_windows)


class TestSelect:
    """select() returns straightforward index mapping."""

    def test_first_item_returns_zero(self) -> None:
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("a")),
        ):
            result = select("Pick one", ["a", "b"])
        assert result == 0

    def test_second_item_returns_one(self) -> None:
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("b")),
        ):
            result = select("Pick one", ["a", "b"])
        assert result == 1


class TestConfirm:
    """confirm() yes/no mapping and Ctrl+C (cancel) handling."""

    def test_yes_returns_true(self) -> None:
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("Yes")),
        ):
            assert confirm("OK?") is True

    def test_no_returns_false(self) -> None:
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("No")),
        ):
            assert confirm("OK?", default=True) is False

    def test_cancel_raises_typer_exit_by_default(self) -> None:
        """Ctrl+C (questionary returns None) aborts the program by default."""
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result(None)),
            pytest.raises(typer.Exit),
        ):
            confirm("OK?")

    def test_cancel_returns_cancel_default_when_given(self) -> None:
        """An optional prompt returns cancel_default instead of aborting on Ctrl+C."""
        with (
            patch("wade.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result(None)),
        ):
            assert confirm("OK?", default=True, cancel_default=False) is False


class TestSelectNonTty:
    """select() returns default immediately when not a TTY."""

    def test_returns_default_when_no_tty(self) -> None:
        with patch("wade.ui.prompts.is_tty", return_value=False):
            result = select("Pick one", ["a", "b", "c"], default=2)
        assert result == 2

    def test_default_zero_when_no_tty(self) -> None:
        with patch("wade.ui.prompts.is_tty", return_value=False):
            result = select("Pick one", ["a", "b"])
        assert result == 0


class TestEnableChoiceWrapping:
    """_enable_choice_wrapping() flips wrap_lines on the picker choice window."""

    def test_select_wrapping_disabled_by_default(self) -> None:
        # Guards against a questionary default change that would make our
        # helper a no-op — the fix only matters because the default crops.
        question = questionary.select("Pick", choices=["short", "x" * 120])
        assert _choice_window_wraps(question) is False

    def test_select_window_wraps_after_helper(self) -> None:
        question = questionary.select("Pick", choices=["short", "x" * 120])
        _enable_choice_wrapping(question)
        assert _choice_window_wraps(question) is True

    def test_checkbox_window_wraps_after_helper(self) -> None:
        question = questionary.checkbox("Pick", choices=["short", "y" * 120])
        _enable_choice_wrapping(question)
        assert _choice_window_wraps(question) is True

    def test_helper_is_noop_when_find_all_windows_raises(self) -> None:
        # Fail-safe: a future questionary/prompt_toolkit change that breaks the
        # walk must degrade to today's crop behavior, not crash the picker.
        question = MagicMock()
        question.application.layout.find_all_windows.side_effect = RuntimeError("boom")
        _enable_choice_wrapping(question)  # must not raise

    def test_helper_is_noop_on_magicmock_question(self) -> None:
        # The existing select()/multi_select() tests patch questionary with a
        # bare MagicMock; the helper must tolerate it (iterating the mock's
        # find_all_windows() result raises TypeError, caught by the fail-safe).
        _enable_choice_wrapping(MagicMock())  # must not raise

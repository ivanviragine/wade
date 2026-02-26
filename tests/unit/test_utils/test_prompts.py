"""Tests for ui/prompts.py — select() back navigation and index mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghaiw.ui.prompts import select


def _mock_questionary_result(return_value: object) -> MagicMock:
    """Return a MagicMock that simulates questionary.select(...).ask() = return_value."""
    question = MagicMock()
    question.ask.return_value = return_value
    return question


class TestSelect:
    """select() returns straightforward index mapping."""

    def test_first_item_returns_zero(self) -> None:
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("a")),
        ):
            result = select("Pick one", ["a", "b"])
        assert result == 0

    def test_second_item_returns_one(self) -> None:
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("b")),
        ):
            result = select("Pick one", ["a", "b"])
        assert result == 1


class TestSelectNonTty:
    """select() returns default immediately when not a TTY."""

    def test_returns_default_when_no_tty(self) -> None:
        with patch("ghaiw.ui.prompts.is_tty", return_value=False):
            result = select("Pick one", ["a", "b", "c"], default=2)
        assert result == 2

    def test_default_zero_when_no_tty(self) -> None:
        with patch("ghaiw.ui.prompts.is_tty", return_value=False):
            result = select("Pick one", ["a", "b"])
        assert result == 0

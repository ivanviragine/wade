"""Tests for ui/prompts.py — select() back navigation and index mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghaiw.ui.prompts import _BACK_VALUE, select


def _mock_questionary_result(return_value: object) -> MagicMock:
    """Return a MagicMock that simulates questionary.select(...).ask() = return_value."""
    question = MagicMock()
    question.ask.return_value = return_value
    return question


class TestSelectBackNavigation:
    """select() with allow_back=True — back detection via sentinel identity."""

    def test_back_returns_minus_one(self) -> None:
        """Selecting ← Back returns -1 regardless of how questionary renders it."""
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result(_BACK_VALUE)),
        ):
            result = select("Pick one", ["a", "b"], allow_back=True)
        assert result == -1

    def test_first_item_returns_zero(self) -> None:
        """Selecting the first real item returns index 0."""
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("a")),
        ):
            result = select("Pick one", ["a", "b", "c"], allow_back=True)
        assert result == 0

    def test_second_item_returns_one(self) -> None:
        """Selecting the second real item returns index 1."""
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("b")),
        ):
            result = select("Pick one", ["a", "b", "c"], allow_back=True)
        assert result == 1

    def test_last_item_returns_correct_index(self) -> None:
        """Selecting the last item returns len(items)-1, not -1 (Python list[-1] trap)."""
        items = ["x", "y", "z"]
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch("questionary.select", return_value=_mock_questionary_result("z")),
        ):
            result = select("Pick one", items, allow_back=True)
        assert result == 2

    def test_unknown_result_falls_back_to_default(self) -> None:
        """If questionary returns an unexpected string, returns default."""
        with (
            patch("ghaiw.ui.prompts.is_tty", return_value=True),
            patch(
                "questionary.select",
                return_value=_mock_questionary_result("not_in_list"),
            ),
        ):
            result = select("Pick one", ["a", "b"], default=1, allow_back=True)
        assert result == 1


class TestSelectNoBack:
    """select() with allow_back=False (default) — straightforward index mapping."""

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
            result = select("Pick one", ["a", "b", "c"], default=2, allow_back=True)
        assert result == 2

    def test_default_zero_when_no_tty(self) -> None:
        with patch("ghaiw.ui.prompts.is_tty", return_value=False):
            result = select("Pick one", ["a", "b"])
        assert result == 0

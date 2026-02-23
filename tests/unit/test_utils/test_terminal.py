"""Tests for terminal utilities."""

from ghaiw.utils.terminal import (
    compose_work_title,
    start_title_keeper,
    stop_title_keeper,
)


class TestComposeWorkTitle:
    def test_basic(self) -> None:
        result = compose_work_title("42", "Add search command")
        assert "ghaiwpy work" in result
        assert "#42" in result
        assert "Add search command" in result

    def test_long_title_truncated(self) -> None:
        long_title = "A" * 100
        result = compose_work_title("1", long_title)
        assert "..." in result
        assert len(result) < 120

    def test_short_title_not_truncated(self) -> None:
        result = compose_work_title("1", "Short")
        assert "..." not in result


class TestTitleKeeper:
    def test_start_and_stop(self) -> None:
        """Title keeper can start and stop without errors."""
        # This won't actually write to terminal in test (not a TTY)
        start_title_keeper("test title")
        stop_title_keeper()

    def test_stop_without_start(self) -> None:
        """Stopping without starting is a no-op."""
        stop_title_keeper()

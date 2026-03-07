"""Tests for terminal utilities."""

from wade.utils.terminal import (
    compose_plan_title,
    compose_review_title,
    compose_work_title,
    start_title_keeper,
    stop_title_keeper,
)


class TestComposeWorkTitle:
    def test_basic(self) -> None:
        result = compose_work_title("42", "Add search command")
        assert "wade work" in result
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


class TestComposePlanTitle:
    def test_with_issue(self) -> None:
        result = compose_plan_title("42", "Add search command")
        assert "wade plan" in result
        assert "#42" in result
        assert "Add search command" in result

    def test_without_issue(self) -> None:
        result = compose_plan_title(None, None)
        assert result == "wade plan"

    def test_long_title_truncated(self) -> None:
        long_title = "A" * 100
        result = compose_plan_title("1", long_title)
        assert "..." in result
        assert len(result) < 120

    def test_short_title_not_truncated(self) -> None:
        result = compose_plan_title("1", "Short")
        assert "..." not in result


class TestComposeReviewTitle:
    def test_basic(self) -> None:
        result = compose_review_title("42", "Add search command")
        assert "wade address-reviews" in result
        assert "#42" in result
        assert "Add search command" in result

    def test_long_title_truncated(self) -> None:
        long_title = "A" * 100
        result = compose_review_title("1", long_title)
        assert "..." in result
        assert len(result) < 120

    def test_short_title_not_truncated(self) -> None:
        result = compose_review_title("1", "Short")
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

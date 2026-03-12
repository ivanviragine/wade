"""Tests that ``done`` commands show a review hint when reviews are enabled."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.services.plan_service import PlanValidationResult

runner = CliRunner()


def _config(
    *, review_plan_enabled: bool | None = None, review_impl_enabled: bool | None = None
) -> ProjectConfig:
    """Build a ProjectConfig with the specified review enablement."""
    return ProjectConfig(
        ai=AIConfig(
            review_plan=AICommandConfig(enabled=review_plan_enabled),
            review_implementation=AICommandConfig(enabled=review_impl_enabled),
        ),
    )


# ---------------------------------------------------------------------------
# plan-session done
# ---------------------------------------------------------------------------


class TestPlanSessionDoneReviewHint:
    """Review hint in ``wade plan-session done`` output."""

    @patch("wade.config.loader.load_config")
    @patch(
        "wade.services.plan_service.plan_done",
        return_value=PlanValidationResult(),
    )
    def test_hint_shown_when_review_enabled(
        self,
        _mock_plan_done: MagicMock,
        mock_load_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text("# Title\n\n## Complexity\nmedium\n")

        mock_load_config.return_value = _config(review_plan_enabled=True)
        result = runner.invoke(app, ["plan-session", "done", str(plan_dir)])
        assert result.exit_code == 0
        assert "wade review plan" in result.output

    @patch("wade.config.loader.load_config")
    @patch(
        "wade.services.plan_service.plan_done",
        return_value=PlanValidationResult(),
    )
    def test_hint_shown_when_review_not_explicitly_set(
        self,
        _mock_plan_done: MagicMock,
        mock_load_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """enabled=None (default) should still show the hint."""
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text("# Title\n\n## Complexity\nmedium\n")

        mock_load_config.return_value = _config(review_plan_enabled=None)
        result = runner.invoke(app, ["plan-session", "done", str(plan_dir)])
        assert result.exit_code == 0
        assert "wade review plan" in result.output

    @patch("wade.config.loader.load_config")
    @patch(
        "wade.services.plan_service.plan_done",
        return_value=PlanValidationResult(),
    )
    def test_hint_hidden_when_review_disabled(
        self,
        _mock_plan_done: MagicMock,
        mock_load_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text("# Title\n\n## Complexity\nmedium\n")

        mock_load_config.return_value = _config(review_plan_enabled=False)
        result = runner.invoke(app, ["plan-session", "done", str(plan_dir)])
        assert result.exit_code == 0
        assert "wade review plan" not in result.output


# ---------------------------------------------------------------------------
# implementation-session done
# ---------------------------------------------------------------------------


class TestImplementationSessionDoneReviewHint:
    """Review hint in ``wade implementation-session done`` output."""

    @patch("wade.config.loader.load_config")
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_hint_shown_when_review_enabled(
        self,
        _mock_done: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        mock_load_config.return_value = _config(review_impl_enabled=True)
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 0
        assert "wade review implementation" in result.output

    @patch("wade.config.loader.load_config")
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_hint_shown_when_review_not_explicitly_set(
        self,
        _mock_done: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        mock_load_config.return_value = _config(review_impl_enabled=None)
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 0
        assert "wade review implementation" in result.output

    @patch("wade.config.loader.load_config")
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_hint_hidden_when_review_disabled(
        self,
        _mock_done: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        mock_load_config.return_value = _config(review_impl_enabled=False)
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 0
        assert "wade review implementation" not in result.output

    @patch("wade.services.implementation_service.done", return_value=False)
    def test_no_hint_when_done_fails(
        self,
        _mock_done: MagicMock,
    ) -> None:
        """When done() returns False, no hint should appear."""
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 1
        assert "wade review implementation" not in result.output

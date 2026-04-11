from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wade.services import implementation_service

runner = CliRunner()


def test_resolve_plan_extracts_title(tmp_path: Path) -> None:
    plan_file = tmp_path / "PLAN.md"
    plan_file.write_text("# My Feature Plan\n\nBody\n", encoding="utf-8")
    expected_path = tmp_path / "worktree"

    with (
        patch(
            "wade.services.implementation_service.core.find_worktree_path",
            return_value=expected_path,
        ),
        patch(
            "wade.services.implementation_service.core.git_repo.get_current_branch",
            return_value="feat/42-my-feature-plan",
        ),
    ):
        result = implementation_service._resolve_worktree_from_plan(plan_file)

    assert result == (expected_path, "feat/42-my-feature-plan", "42")


def test_resolve_plan_no_heading_errors(tmp_path: Path) -> None:
    plan_file = tmp_path / "PLAN.md"
    plan_file.write_text("No heading here\nMore content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="# Title"):
        implementation_service._resolve_worktree_from_plan(plan_file)


def test_resolve_plan_finds_worktree_by_slug(tmp_path: Path) -> None:
    plan_file = tmp_path / "PLAN.md"
    plan_file.write_text("# My Feature Plan\n", encoding="utf-8")
    expected_path = tmp_path / "wt"

    with (
        patch(
            "wade.services.implementation_service.core.find_worktree_path",
            return_value=expected_path,
        ) as mock_find,
        patch(
            "wade.services.implementation_service.core.git_repo.get_current_branch",
            return_value="feat/99-my-feature-plan",
        ),
    ):
        implementation_service._resolve_worktree_from_plan(plan_file)

    assert mock_find.call_count == 1
    assert mock_find.call_args.args[0] == "my-feature-plan"


def test_resolve_plan_no_matching_worktree_errors(tmp_path: Path) -> None:
    plan_file = tmp_path / "PLAN.md"
    plan_file.write_text("# My Feature Plan\n", encoding="utf-8")

    with (
        patch("wade.services.implementation_service.core.find_worktree_path", return_value=None),
        pytest.raises(ValueError, match="wade worktree list"),
    ):
        implementation_service._resolve_worktree_from_plan(plan_file)


def test_resolve_plan_file_not_found_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(ValueError, match=str(missing)):
        implementation_service._resolve_worktree_from_plan(missing)


def test_done_with_plan_flag_resolves_and_delegates() -> None:
    from wade.models.config import ProjectConfig, ProjectSettings

    provider = MagicMock()
    with (
        patch(
            "wade.services.implementation_service.core.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ),
        patch("wade.services.implementation_service.core.get_provider", return_value=provider),
        patch(
            "wade.services.implementation_service.core.git_repo.get_repo_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "wade.services.implementation_service.core._resolve_worktree_from_plan",
            return_value=(Path("/tmp/wt"), "feat/42-my-plan", "42"),
        ) as mock_resolve,
        patch(
            "wade.services.implementation_service.core.find_worktree_path",
            return_value=Path("/tmp/wt"),
        ),
        patch(
            "wade.services.implementation_service.core.git_repo.get_current_branch",
            return_value="feat/42-my-plan",
        ),
        patch("wade.services.implementation_service.core.git_repo.is_clean", return_value=True),
        patch(
            "wade.services.implementation_service.core._done_via_pr", return_value=True
        ) as mock_done,
    ):
        result = implementation_service.done(plan_file=Path("PLAN.md"))

    mock_resolve.assert_called_once_with(Path("PLAN.md"), project_root=None)
    assert mock_done.called
    assert result is True


def test_done_plan_flag_error_returns_false() -> None:
    from wade.models.config import ProjectConfig

    with (
        patch(
            "wade.services.implementation_service.core.load_config", return_value=ProjectConfig()
        ),
        patch("wade.services.implementation_service.core.get_provider", return_value=MagicMock()),
        patch(
            "wade.services.implementation_service.core.git_repo.get_repo_root",
            return_value=Path("/tmp/repo"),
        ),
        patch(
            "wade.services.implementation_service.core._resolve_worktree_from_plan",
            side_effect=ValueError("No worktree found"),
        ),
    ):
        result = implementation_service.done(plan_file=Path("PLAN.md"))

    assert result is False


def test_cli_plan_flag_passes_to_service() -> None:
    from wade.cli.implementation_session import implementation_session_app

    with patch("wade.services.implementation_service.done", return_value=True) as mock_done:
        result = runner.invoke(implementation_session_app, ["done", "--plan", "/tmp/PLAN.md"])

    assert result.exit_code == 0
    mock_done.assert_called_once_with(
        target=None,
        plan_file=Path("/tmp/PLAN.md"),
        no_close=False,
        draft=False,
        no_cleanup=False,
    )


def test_done_plan_flag_shows_in_help() -> None:
    from wade.cli.implementation_session import implementation_session_app

    result = runner.invoke(implementation_session_app, ["done", "--help"])
    # Strip ANSI escape codes before checking — Rich/Typer may split
    # flags across styled spans (e.g. \x1b[1;36m-\x1b[0m\x1b[1;36m-plan\x1b[0m).
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)

    assert result.exit_code == 0
    assert "--plan" in plain

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ghaiw.services import work_service


def test_resolve_plan_extracts_title(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# My Feature Plan\n\nBody\n", encoding="utf-8")
    expected_path = tmp_path / "worktree"

    with (
        patch("ghaiw.services.work_service.find_worktree_path", return_value=expected_path),
        patch(
            "ghaiw.services.work_service.git_repo.get_current_branch",
            return_value="feat/42-my-feature-plan",
        ),
    ):
        result = work_service._resolve_worktree_from_plan(plan_file)

    assert result == (expected_path, "feat/42-my-feature-plan", "42")


def test_resolve_plan_no_heading_errors(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("No heading here\nMore content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="# Title"):
        work_service._resolve_worktree_from_plan(plan_file)


def test_resolve_plan_finds_worktree_by_slug(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# My Feature Plan\n", encoding="utf-8")
    expected_path = tmp_path / "wt"

    with (
        patch(
            "ghaiw.services.work_service.find_worktree_path", return_value=expected_path
        ) as mock_find,
        patch(
            "ghaiw.services.work_service.git_repo.get_current_branch",
            return_value="feat/99-my-feature-plan",
        ),
    ):
        work_service._resolve_worktree_from_plan(plan_file)

    assert mock_find.call_count == 1
    assert mock_find.call_args.args[0] == "my-feature-plan"


def test_resolve_plan_no_matching_worktree_errors(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# My Feature Plan\n", encoding="utf-8")

    with (
        patch("ghaiw.services.work_service.find_worktree_path", return_value=None),
        pytest.raises(ValueError, match="ghaiwpy work list"),
    ):
        work_service._resolve_worktree_from_plan(plan_file)


def test_resolve_plan_file_not_found_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(ValueError, match=str(missing)):
        work_service._resolve_worktree_from_plan(missing)

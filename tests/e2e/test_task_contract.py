"""Deterministic E2E contracts for task lifecycle CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _parse_json_output,
    _run,
    _seed_mock_issue,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestTaskCommands:
    """Test `wade task` subcommands via CLI subprocess."""

    def test_task_list(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        """wade task list should call gh issue list and show seeded issues."""
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=7,
            title="Seeded E2E issue",
            body="Seeded body",
            labels=["feature-plan"],
        )
        result = _run(["task", "list"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "Seeded E2E issue" in result.stdout
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "list", "--state", "open", "--label", "feature-plan"],
        )

    def test_task_list_json(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        """wade task list --json outputs strict JSON and calls gh issue list."""
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=8,
            title="JSON Seeded E2E issue",
            body="JSON seeded body",
            labels=["feature-plan"],
        )
        result = _run(["task", "list", "--json"], cwd=e2e_repo)
        assert result.returncode == 0
        parsed = _parse_json_output(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1
        first = parsed[0]
        assert isinstance(first, dict)
        assert {"number", "title", "state", "labels", "url"}.issubset(first)
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "list", "--state", "open", "--label", "feature-plan"],
        )


class TestNewTaskCommand:
    """Test `wade new-task` via CLI subprocess."""

    def test_new_task_non_interactive_creates_issue_with_labels_and_body(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """new-task should create a labeled issue with the provided body text."""
        title = "Contract test: non-interactive new-task"
        body = "This issue was created from deterministic e2e."
        result = _run(
            [
                "new-task",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "bug",
                "--label",
                "urgent",
            ],
            cwd=e2e_repo,
        )

        assert result.returncode == 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "create", "--title", title],
        )

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        created = state_data.get("issues", {}).get("1")
        assert isinstance(created, dict)
        assert created.get("title") == title
        assert created.get("body") == body
        labels = created.get("labels", [])
        assert isinstance(labels, list)
        assert {"feature-plan", "bug", "urgent"}.issubset(set(str(x) for x in labels))

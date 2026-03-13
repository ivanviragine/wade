"""Deterministic E2E contracts for task lifecycle CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _count_gh_calls,
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

    def test_task_deps_prompt_mode_without_configured_ai_prints_prompt_only(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Prompt-mode deps should not require AI config or create workflow side effects."""
        (e2e_repo / ".wade.yml").write_text(
            "\n".join(
                [
                    "version: 2",
                    "project:",
                    "  main_branch: main",
                    "  issue_label: feature-plan",
                    "  worktrees_dir: ../.worktrees",
                    "  branch_prefix: feat",
                    "  merge_strategy: PR",
                    "provider:",
                    "  name: github",
                    "hooks:",
                    "  copy_to_worktree:",
                    "    - .wade.yml",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=11,
            title="Auth layer",
            body="Login has to land first.",
            labels=["feature-plan"],
        )
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=12,
            title="Dashboard UI",
            body="Depends on auth state.",
            labels=["feature-plan"],
        )

        result = _run(["task", "deps", "11", "12", "--mode", "prompt"], cwd=e2e_repo)

        assert result.returncode == 0
        assert "## Issue #11: Auth layer" in result.stdout
        assert "## Issue #12: Dashboard UI" in result.stdout
        assert "Login has to land first." in result.stdout
        assert "Depends on auth state." in result.stdout
        assert "wade task deps" not in result.stdout
        assert "Analysis complete" not in result.stdout
        assert _count_gh_calls(mock_gh_cli["log_file"], ["issue", "edit"]) == 0
        assert _count_gh_calls(mock_gh_cli["log_file"], ["issue", "create"]) == 0
        deps_worktrees = list((e2e_repo.parent / ".worktrees" / e2e_repo.name).glob("deps-*"))
        assert deps_worktrees == []

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
    """Test `wade task create` via CLI subprocess."""

    def test_new_task_non_interactive_creates_issue_with_labels_and_body(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """task create should create a labeled issue with the provided body text."""
        title = "Contract test: non-interactive task create"
        body = "This issue was created from deterministic e2e."
        result = _run(
            [
                "task",
                "create",
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


class TestTaskLifecycleCommands:
    """Contract coverage for task read/update/close subcommands."""

    def test_task_read_json(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        """task read --json should return strict JSON with expected fields."""
        issue_number = 21
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Readable issue",
            body="Body from seeded issue",
            labels=["feature-plan"],
        )

        result = _run(["task", "read", str(issue_number), "--json"], cwd=e2e_repo)
        assert result.returncode == 0
        parsed = _parse_json_output(result.stdout)
        assert isinstance(parsed, dict)
        assert parsed.get("number") == str(issue_number)
        assert parsed.get("title") == "Readable issue"
        assert parsed.get("body") == "Body from seeded issue"
        assert parsed.get("state") == "open"
        assert isinstance(parsed.get("labels"), list)
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )

    def test_task_update_plan_file_and_comment(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """task update should edit issue body/title from plan file and add comment."""
        issue_number = 22
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Old title",
            body="Old body",
            labels=["feature-plan"],
        )

        plan_file = e2e_repo / "update-plan.md"
        plan_file.write_text(
            "\n".join(
                [
                    "# Updated issue title",
                    "",
                    "## Context / Problem",
                    "Updated body from plan file.",
                    "",
                    "## Tasks",
                    "- Update the issue",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run(
            [
                "task",
                "update",
                str(issue_number),
                "--plan-file",
                str(plan_file),
                "--comment",
                "Update comment from deterministic e2e",
            ],
            cwd=e2e_repo,
        )
        assert result.returncode == 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", str(issue_number), "--title", "Updated issue title"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            [
                "issue",
                "comment",
                str(issue_number),
                "--body",
                "Update comment from deterministic e2e",
            ],
        )

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        updated = state_data.get("issues", {}).get(str(issue_number))
        assert isinstance(updated, dict)
        assert updated.get("title") == "Updated issue title"
        assert "Updated body from plan file." in str(updated.get("body", ""))

    def test_task_close_changes_state_and_removes_in_progress_label(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """task close should add optional comment, remove in-progress label, and close issue."""
        issue_number = 23
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Closable issue",
            body="Will be closed",
            labels=["feature-plan", "in-progress"],
        )

        result = _run(
            [
                "task",
                "close",
                str(issue_number),
                "--comment",
                "Closing from deterministic e2e",
            ],
            cwd=e2e_repo,
        )
        assert result.returncode == 0

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "comment", str(issue_number), "--body", "Closing from deterministic e2e"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", str(issue_number), "--remove-label", "in-progress"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "close", str(issue_number)],
        )

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        closed = state_data.get("issues", {}).get(str(issue_number))
        assert isinstance(closed, dict)
        assert str(closed.get("state", "")).upper() == "CLOSED"
        labels = closed.get("labels", [])
        assert isinstance(labels, list)
        assert "in-progress" not in labels

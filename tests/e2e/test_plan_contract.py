"""Deterministic E2E contracts for planning workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _init_origin_remote,
    _run,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


def _install_fake_claude(mock_bin: Path) -> None:
    """Install a deterministic fake `claude` binary into the mocked PATH."""
    claude_script = mock_bin / "claude"
    claude_script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _find_plan_dir(argv: list[str]) -> Path | None:
    add_dirs: list[Path] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--add-dir" and i + 1 < len(argv):
            add_dirs.append(Path(argv[i + 1]))
            i += 2
            continue
        i += 1

    for path in reversed(add_dirs):
        if "wade-plan-" in str(path):
            return path
    if add_dirs:
        return add_dirs[-1]
    return None


plan_dir = _find_plan_dir(sys.argv[1:])
if plan_dir is not None:
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "001-deterministic-plan.md").write_text(
        "\\n".join(
            [
                "# Deterministic plan from fake claude",
                "",
                "## Complexity",
                "easy",
                "",
                "## Context / Problem",
                "Create one deterministic issue and draft PR.",
                "",
                "## Tasks",
                "- Generate a task from this plan file",
                "",
                "## Acceptance Criteria",
                "- The issue and draft PR are created",
            ]
        )
        + "\\n",
        encoding="utf-8",
    )

print("Session ID: fake-claude-session-001")
print("Total tokens: 256")
sys.exit(0)
""",
        encoding="utf-8",
    )
    claude_script.chmod(0o755)


class TestPlanCommand:
    """Test `wade plan` deterministic workflow using mocked gh + fake AI."""

    def test_plan_creates_issue_and_draft_pr_from_generated_plan(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """plan should create issue/PR side effects from AI-generated plan files."""
        _init_origin_remote(e2e_repo)
        _install_fake_claude(mock_gh_cli["mock_bin"])

        result = _run(["plan", "--ai", "claude", "--model", "claude-haiku-4.5"], cwd=e2e_repo)
        assert result.returncode == 0

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        issues = state_data.get("issues", {})
        prs = state_data.get("prs", {})
        assert isinstance(issues, dict)
        assert isinstance(prs, dict)
        assert len(issues) == 1, f"Expected 1 created issue, got: {issues!r}"
        assert len(prs) == 1, f"Expected 1 created PR, got: {prs!r}"

        issue = issues.get("1")
        assert isinstance(issue, dict)
        assert issue.get("title") == "Deterministic plan from fake claude"
        labels = issue.get("labels", [])
        assert isinstance(labels, list)
        assert "feature-plan" in labels

        pr = prs.get("1")
        assert isinstance(pr, dict)
        assert bool(pr.get("isDraft")) is True
        assert pr.get("head"), f"Expected PR head branch in mock state: {pr!r}"

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "create", "--title", "Deterministic plan from fake claude"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--draft"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", "1", "--body-file"],
        )


class TestPlanSessionDoneCommand:
    """Test `wade plan-session done` validation behavior."""

    def test_plan_session_done_fails_for_invalid_plan_dir(self, e2e_repo: Path) -> None:
        """plan-session done should fail when plan files are invalid."""
        invalid_dir = e2e_repo / "invalid-plans"
        invalid_dir.mkdir()
        (invalid_dir / "bad.md").write_text("## Missing title heading\n", encoding="utf-8")

        result = _run(["plan-session", "done", str(invalid_dir)], cwd=e2e_repo)
        assert result.returncode == 1
        assert "Plan validation failed" in result.stderr

    def test_plan_session_done_succeeds_for_valid_plan_dir(self, e2e_repo: Path) -> None:
        """plan-session done should pass when required sections are valid."""
        valid_dir = e2e_repo / "valid-plans"
        valid_dir.mkdir()
        (valid_dir / "good.md").write_text(
            "\n".join(
                [
                    "# Valid plan",
                    "",
                    "## Complexity",
                    "easy",
                    "",
                    "## Tasks",
                    "- Add a deterministic test",
                    "",
                    "## Acceptance Criteria",
                    "- Validation passes",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run(["plan-session", "done", str(valid_dir)], cwd=e2e_repo)
        assert result.returncode == 0
        assert "Plan validation passed" in result.stdout

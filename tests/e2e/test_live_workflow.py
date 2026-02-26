"""Live E2E tests against the real taskr GitHub repo.

These tests require:
  - gh CLI authenticated
  - RUN_LIVE_E2E=1 environment variable
  - Network access
  - The taskr repo at ~/Documents/workspace/taskr (or TASKR_REPO env var)
  - ghaiw CLI installed and in PATH

They exercise real ghaiw commands against the taskr GitHub project.
Run with: RUN_LIVE_E2E=1 uv run pytest tests/e2e/test_live_workflow.py -v

WARNING: These tests create and close real GitHub issues. Run
./scripts/reset.sh in taskr afterward to clean up.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

TASKR_REPO = Path(os.environ.get("TASKR_REPO", os.path.expanduser("~/Documents/workspace/taskr")))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_E2E") != "1",
    reason="Live E2E tests disabled (set RUN_LIVE_E2E=1)",
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result."""
    return subprocess.run(
        cmd,
        cwd=cwd or TASKR_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ghaiw(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a ghaiw command."""
    return _run(["ghaiw", *args], cwd=cwd)


@pytest.fixture(autouse=True)
def require_taskr_repo() -> None:
    """Skip if taskr repo doesn't exist."""
    if not TASKR_REPO.is_dir():
        pytest.skip(f"taskr repo not found at {TASKR_REPO}")
    if not (TASKR_REPO / ".git").is_dir():
        pytest.skip(f"{TASKR_REPO} is not a git repo")


@pytest.fixture(autouse=True)
def require_gh() -> None:
    """Skip if gh CLI is not authenticated."""
    result = _run(["gh", "auth", "status"])
    if result.returncode != 0:
        pytest.skip("gh CLI not authenticated")


@pytest.fixture(autouse=True)
def require_ghaiw() -> None:
    """Skip if ghaiw CLI is not available."""
    result = _run(["which", "ghaiw"])
    if result.returncode != 0:
        pytest.skip("ghaiw CLI not found in PATH")


class TestLiveGhaiwVersion:
    """Basic smoke tests for ghaiw CLI."""

    def test_version(self) -> None:
        result = _ghaiw("--version")
        assert result.returncode == 0
        assert "ghaiw" in result.stdout.lower()

    def test_help(self) -> None:
        result = _ghaiw("--help")
        assert result.returncode == 0
        assert "task" in result.stdout.lower()
        assert "work" in result.stdout.lower()


class TestLiveCheck:
    """Test ghaiw check against real taskr repo."""

    def test_check_main_checkout(self) -> None:
        """ghaiw check on main branch exits 2 and reports IN_MAIN_CHECKOUT."""
        _run(["git", "checkout", "main"])

        result = _ghaiw("check")
        assert result.returncode == 2
        assert "IN_MAIN_CHECKOUT" in result.stdout


class TestLiveCheckConfig:
    """Test ghaiw check-config against real taskr repo."""

    def test_check_config_if_exists(self) -> None:
        """Validate config if .ghaiw.yml exists."""
        if not (TASKR_REPO / ".ghaiw.yml").is_file():
            pytest.skip("No .ghaiw.yml in taskr")

        result = _ghaiw("check-config")
        # 0 = valid, 3 = invalid config
        assert result.returncode in (0, 3)


class TestLiveTaskLifecycle:
    """Test issue creation and listing via ghaiw.

    These tests create real GitHub issues and close them.
    """

    def test_task_create_and_close(self, tmp_path: Path) -> None:
        """Create an issue from a plan file, then close it."""
        plan = tmp_path / "test-PLAN.md"
        plan.write_text(
            """\
# E2E test issue — auto-cleanup

## Complexity

easy

## Description

This is an automated test issue created by ghaiw E2E tests.
It should be closed automatically. If you see this, the test may
have failed to clean up.

## Tasks

- [ ] This issue is for automated testing only
"""
        )

        # Create issue
        result = _ghaiw("task", "create", "--plan-file", str(plan), "--no-start")

        if result.returncode != 0:
            pytest.fail(f"task create failed (exit {result.returncode}): {result.stderr}")

        # Extract issue number from output
        import re

        match = re.search(r"#(\d+)", result.stdout + result.stderr)
        if not match:
            pytest.fail(f"Could not extract issue number from: {result.stdout}")

        issue_num = match.group(1)

        try:
            # List issues — our issue should appear
            list_result = _ghaiw("task", "list")
            assert list_result.returncode == 0

            # Read the issue
            read_result = _ghaiw("task", "read", issue_num)
            assert read_result.returncode == 0
        finally:
            # Always clean up: close the issue
            _ghaiw("task", "close", issue_num)

    def test_task_list(self) -> None:
        """ghaiw task list runs without error."""
        result = _ghaiw("task", "list")
        assert result.returncode == 0

"""Live E2E tests against a real GitHub repo.

These tests require:
  - gh CLI authenticated
  - RUN_LIVE_E2E=1 environment variable
  - Network access
  - A test repo at ~/Documents/workspace/ghaiw-e2e (or E2E_REPO env var)
  - ghaiw CLI installed and in PATH

They exercise real ghaiw commands against a GitHub project.
Run with: RUN_LIVE_E2E=1 uv run pytest tests/e2e/test_live_workflow.py -v

WARNING: These tests create and close real GitHub issues.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

E2E_REPO = Path(os.environ.get("E2E_REPO", os.path.expanduser("~/Documents/workspace/ghaiw-e2e")))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_E2E") != "1",
    reason="Live E2E tests disabled (set RUN_LIVE_E2E=1)",
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result."""
    return subprocess.run(
        cmd,
        cwd=cwd or E2E_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ghaiw(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a ghaiw command."""
    return _run(["ghaiw", *args], cwd=cwd)


@pytest.fixture(autouse=True)
def require_e2e_repo() -> None:
    """Skip if E2E repo doesn't exist."""
    if not E2E_REPO.is_dir():
        pytest.skip(f"E2E repo not found at {E2E_REPO}")
    if not (E2E_REPO / ".git").is_dir():
        pytest.skip(f"{E2E_REPO} is not a git repo")


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
    """Test ghaiw check against real E2E repo."""

    def test_check_main_checkout(self) -> None:
        """ghaiw check on main branch exits 2 and reports IN_MAIN_CHECKOUT."""
        _run(["git", "checkout", "main"])

        result = _ghaiw("check")
        assert result.returncode == 2
        assert "IN_MAIN_CHECKOUT" in result.stdout


class TestLiveCheckConfig:
    """Test ghaiw check-config against real E2E repo."""

    def test_check_config_if_exists(self) -> None:
        """Validate config if .ghaiw.yml exists."""
        if not (E2E_REPO / ".ghaiw.yml").is_file():
            pytest.skip("No .ghaiw.yml in E2E repo")

        result = _ghaiw("check-config")
        # 0 = valid, 3 = invalid config
        assert result.returncode in (0, 3)


class TestLiveTaskLifecycle:
    """Test issue lifecycle via ghaiw task read/list/close.

    Issues are created via gh CLI and exercised through ghaiw commands.
    """

    def test_task_lifecycle(self) -> None:
        """Create an issue via gh, exercise ghaiw read/close."""
        import re

        # Create issue directly via gh (ghaiw new-task is interactive)
        result = _run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                "E2E test issue — auto-cleanup",
                "--body",
                "Automated test issue from ghaiw E2E tests.",
                "--label",
                "easy",
            ]
        )
        if result.returncode != 0:
            pytest.fail(f"gh issue create failed (exit {result.returncode}): {result.stderr}")

        match = re.search(r"/issues/(\d+)", result.stdout + result.stderr)
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

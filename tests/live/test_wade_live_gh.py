"""Manual live tests for WADE behavior against real GitHub.

These tests are intentionally opt-in and require:
  - RUN_LIVE_GH_TESTS=1
  - WADE_LIVE_REPO (or E2E_REPO) pointing to a real repo using wade
  - gh CLI authentication
  - network access
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

_LIVE_REPO_ENV = os.environ.get("WADE_LIVE_REPO") or os.environ.get("E2E_REPO")
LIVE_REPO = Path(_LIVE_REPO_ENV).expanduser() if _LIVE_REPO_ENV else None

pytestmark = [
    pytest.mark.live_gh,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_GH_TESTS") != "1",
        reason="Live gh tests disabled (set RUN_LIVE_GH_TESTS=1)",
    ),
]


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd or LIVE_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _wade(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return _run(["wade", *args], cwd=cwd)


def _assert_ok(result: subprocess.CompletedProcess[str], context: str) -> None:
    assert result.returncode == 0, (
        f"{context} failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.fixture(autouse=True)
def require_live_repo() -> None:
    if LIVE_REPO is None:
        pytest.skip("WADE_LIVE_REPO or E2E_REPO is required for live gh tests")
    if not LIVE_REPO.is_dir():
        pytest.skip(f"Live repo not found at {LIVE_REPO}")
    git_check = subprocess.run(
        ["git", "-C", str(LIVE_REPO), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if git_check.returncode != 0:
        pytest.skip(f"{LIVE_REPO} is not a git worktree")
    if not (LIVE_REPO / ".wade.yml").is_file():
        pytest.skip(f"{LIVE_REPO} is missing .wade.yml")


@pytest.fixture(autouse=True)
def require_tools() -> None:
    gh = _run(["gh", "auth", "status"], cwd=None)
    if gh.returncode != 0:
        pytest.skip("gh CLI not authenticated")
    wade = _wade("--version", cwd=None)
    if wade.returncode != 0:
        pytest.skip("wade CLI not available in PATH")


@pytest.fixture(autouse=True)
def restore_original_branch(require_live_repo: None) -> None:
    """Restore the original branch after each live test."""
    branch_before = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    original = branch_before.stdout.strip() if branch_before.returncode == 0 else ""
    yield
    if original and original != "HEAD":
        _run(["git", "checkout", original])


class TestLiveWadeGH:
    def test_check_and_task_list(self) -> None:
        check_result = _wade("check")
        assert check_result.returncode in (0, 2)
        if check_result.returncode == 0:
            assert "IN_WORKTREE" in check_result.stdout
        else:
            assert "IN_MAIN_CHECKOUT" in check_result.stdout

        list_result = _wade("task", "list", "--json")
        _assert_ok(list_result, "wade task list --json")
        parsed = json.loads(list_result.stdout)
        assert isinstance(parsed, list)
        if parsed:
            first = parsed[0]
            assert isinstance(first, dict)
            assert {"number", "title", "state", "labels", "url"}.issubset(first)

    def test_issue_lifecycle_through_wade(self) -> None:
        unique = int(time.time())
        title = f"WADE live GH smoke {unique}"
        issue_num = ""

        created = _wade(
            "new-task",
            "--title",
            title,
            "--body",
            "Created by WADE live GH smoke test. Auto-cleanup.",
        )
        _assert_ok(created, "wade new-task")

        list_result = _wade("task", "list", "--json")
        _assert_ok(list_result, "wade task list --json")
        listed = json.loads(list_result.stdout)
        assert isinstance(listed, list)
        created_issue = next(
            (
                item
                for item in listed
                if isinstance(item, dict) and str(item.get("title", "")) == title
            ),
            None,
        )
        assert created_issue is not None, (
            "Could not find newly created issue in `wade task list --json` output.\n"
            f"title={title!r}\n"
            f"output={list_result.stdout!r}"
        )
        issue_num = str(created_issue["number"])

        try:
            read_result = _wade("task", "read", issue_num, "--json")
            _assert_ok(read_result, f"wade task read {issue_num}")
            read_payload = json.loads(read_result.stdout)
            assert isinstance(read_payload, dict)
            assert read_payload.get("title") == title

            close_result = _wade("task", "close", issue_num)
            _assert_ok(close_result, f"wade task close {issue_num}")

            state_result = _wade("task", "read", issue_num, "--json")
            _assert_ok(state_result, "wade task read --json (closed check)")
            state_payload = json.loads(state_result.stdout)
            assert isinstance(state_payload, dict)
            assert str(state_payload.get("state", "")).upper() == "CLOSED"
        finally:
            if issue_num:
                _wade("task", "close", issue_num)

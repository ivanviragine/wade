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
import sys
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


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd or LIVE_REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wade(
    *args: str,
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return _run(["wade", *args], cwd=cwd, timeout=timeout)


def _gh(
    *args: str,
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return _run(["gh", *args], cwd=cwd, timeout=timeout)


def _assert_ok(result: subprocess.CompletedProcess[str], context: str) -> None:
    assert result.returncode == 0, (
        f"{context} failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _recover_issue_number_by_title(title: str) -> str:
    """Best-effort lookup by title for cleanup when creation assertions fail mid-test."""
    recovery = _wade("task", "list", "--json")
    if recovery.returncode != 0:
        return ""
    try:
        payload = json.loads(recovery.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    recovered = next(
        (
            item
            for item in payload
            if isinstance(item, dict) and str(item.get("title", "")) == title
        ),
        None,
    )
    if not isinstance(recovered, dict):
        return ""
    return str(recovered.get("number", ""))


def _recover_pr_number_by_branch(branch_name: str) -> str:
    """Best-effort PR lookup by branch name for cleanup fallback."""
    if not branch_name:
        return ""
    view = _gh("pr", "view", branch_name, "--json", "number")
    if view.returncode != 0:
        return ""
    try:
        payload = json.loads(view.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    number = payload.get("number")
    if number is None:
        return ""
    return str(number)


def _record_cleanup_failure(
    failures: list[str],
    result: subprocess.CompletedProcess[str],
    context: str,
) -> None:
    if result.returncode != 0:
        failures.append(
            f"{context} failed with exit {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def _finalize_cleanup_failures(failures: list[str]) -> None:
    if not failures:
        return
    message = "Live-test cleanup failures:\n\n" + "\n\n".join(failures)
    if sys.exc_info()[0] is None:
        pytest.fail(message)
    print(message, file=sys.stderr)


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
        check_result = _wade("implementation-session", "check")
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
        unique = time.time_ns()
        title = f"WADE live GH smoke {unique}"
        issue_num = ""
        issue_closed = False
        cleanup_failures: list[str] = []

        created = _wade(
            "task",
            "create",
            "--title",
            title,
            "--body",
            "Created by WADE live GH smoke test. Auto-cleanup.",
        )
        _assert_ok(created, "wade task create")

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
            issue_closed = True

            state_result = _wade("task", "read", issue_num, "--json")
            _assert_ok(state_result, "wade task read --json (closed check)")
            state_payload = json.loads(state_result.stdout)
            assert isinstance(state_payload, dict)
            assert str(state_payload.get("state", "")).upper() == "CLOSED"
        finally:
            if not issue_num:
                issue_num = _recover_issue_number_by_title(title)
            if issue_num and not issue_closed:
                close_recovery = _wade("task", "close", issue_num)
                _record_cleanup_failure(
                    cleanup_failures,
                    close_recovery,
                    f"cleanup: wade task close {issue_num}",
                )
            _finalize_cleanup_failures(cleanup_failures)

    def test_implement_and_review_pr_comments_no_comment_path(self) -> None:
        """Exercise live implement + review pr-comments workflow (no unresolved comments)."""
        unique = time.time_ns()
        title = f"WADE live GH workflow smoke {unique}"
        issue_num = ""
        pr_number = ""
        branch_name = ""
        cleanup_failures: list[str] = []

        created = _wade(
            "task",
            "create",
            "--title",
            title,
            "--body",
            "Live workflow smoke: implement + review pr-comments no-comment path.",
        )
        _assert_ok(created, "wade task create (workflow smoke)")

        list_result = _wade("task", "list", "--json")
        _assert_ok(list_result, "wade task list --json (workflow smoke)")
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
            implement = _wade("implement", issue_num, "--cd", timeout=180)
            _assert_ok(implement, f"wade implement {issue_num} --cd")
            worktree_path = Path(implement.stdout.strip())
            assert worktree_path.is_dir(), (
                f"Expected worktree path from implement --cd: {worktree_path}"
            )

            branch_result = _run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=worktree_path,
            )
            _assert_ok(branch_result, "git rev-parse --abbrev-ref HEAD")
            branch_name = branch_result.stdout.strip()
            assert branch_name

            pr_view = _gh(
                "pr",
                "view",
                branch_name,
                "--json",
                "number,state,isDraft",
                timeout=120,
            )
            _assert_ok(pr_view, f"gh pr view {branch_name}")
            pr_payload = json.loads(pr_view.stdout)
            assert isinstance(pr_payload, dict)
            pr_number = str(pr_payload.get("number", ""))
            assert pr_number
            assert str(pr_payload.get("state", "")).upper() == "OPEN"
            assert bool(pr_payload.get("isDraft", False)) is True

            review = _wade("review", "pr-comments", issue_num, timeout=180)
            _assert_ok(review, f"wade review pr-comments {issue_num}")
            output = review.stdout + review.stderr
            assert "All review comments resolved" in output
        finally:
            if not issue_num:
                issue_num = _recover_issue_number_by_title(title)
            if not pr_number and branch_name:
                pr_number = _recover_pr_number_by_branch(branch_name)

            if pr_number:
                close_pr = _gh("pr", "close", pr_number, "--delete-branch", timeout=120)
                _record_cleanup_failure(
                    cleanup_failures,
                    close_pr,
                    f"cleanup: gh pr close {pr_number} --delete-branch",
                )
            if issue_num:
                close_issue = _wade("task", "close", issue_num)
                _record_cleanup_failure(
                    cleanup_failures,
                    close_issue,
                    f"cleanup: wade task close {issue_num}",
                )
                remove_wt = _wade("worktree", "remove", issue_num, "--force")
                _record_cleanup_failure(
                    cleanup_failures,
                    remove_wt,
                    f"cleanup: wade worktree remove {issue_num} --force",
                )
            _finalize_cleanup_failures(cleanup_failures)

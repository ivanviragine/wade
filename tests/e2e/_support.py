"""Shared helpers for deterministic E2E CLI contract tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest

WADE = "wade"
MAIN_BRANCH = "main"
_RUN_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USER",
)


class MockGhCli(TypedDict):
    """Runtime artifacts for the stateful mock gh fixture."""

    log_file: Path
    state_file: Path
    mock_bin: Path


def _run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a wade CLI command as a subprocess."""
    run_env = {key: os.environ[key] for key in _RUN_ENV_ALLOWLIST if key in os.environ}
    run_env.update(
        {key: value for key, value in os.environ.items() if key.startswith("WADE_MOCK_")}
    )
    run_env.setdefault("LANG", "C.UTF-8")
    run_env.setdefault("LC_ALL", run_env["LANG"])
    run_env.setdefault("NO_COLOR", "1")
    run_env.setdefault("PYTHONIOENCODING", "utf-8")
    run_env.setdefault("PYTHONUTF8", "1")
    run_env.setdefault("TERM", "dumb")
    if env:
        run_env.update(env)
    return subprocess.run(
        [WADE, *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
    )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _parse_json_output(stdout: str) -> Any:
    """Parse JSON from CLI stdout and fail if non-JSON text leaked."""
    stdout = stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "--json output is not valid JSON (structlog may have leaked to stdout).\n"
            f"Raw stdout:\n{stdout!r}\n"
            f"JSONDecodeError: {exc}"
        )


def _read_gh_log(log_file: Path) -> list[list[str]]:
    """Parse the mock gh CLI JSONL log into a list of arg lists."""
    if not log_file.exists():
        return []
    invocations: list[list[str]] = []
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            invocations.append(json.loads(line))
        except json.JSONDecodeError:
            # Backward compat: treat as flat string (space-separated)
            invocations.append(line.split())
    return invocations


def _assert_gh_called_with(
    log_file: Path,
    expected_args: list[str],
) -> None:
    """Assert at least one gh invocation contains expected args in order.

    This is an ordered-subsequence check (not exact equality), so tests remain
    stable across benign flag reordering while still rejecting wrong subcommands.
    """
    invocations = _read_gh_log(log_file)
    for inv in invocations:
        if _invocation_matches(inv, expected_args):
            return
    pytest.fail(f"No gh invocation contained all of {expected_args}.\nInvocations: {invocations}")


def _seed_mock_issue(
    state_file: Path,
    issue_number: int,
    title: str,
    body: str = "",
    issue_state: str = "OPEN",
    labels: list[str] | None = None,
) -> None:
    """Seed the mock gh state with a deterministic issue entry."""
    state_data = json.loads(state_file.read_text())
    state_data.setdefault("issues", {})
    state_data["issues"][str(issue_number)] = {
        "title": title,
        "body": body,
        "state": issue_state,
        "labels": labels or [],
    }
    state_data["next_issue"] = max(int(state_data.get("next_issue", 1)), issue_number + 1)
    state_file.write_text(json.dumps(state_data))


def _seed_mock_review_threads(
    state_file: Path,
    pr_number: int | str,
    threads: list[dict[str, Any]],
) -> None:
    """Seed mock GraphQL review thread state for a pull request number."""
    state_data = json.loads(state_file.read_text())
    state_data.setdefault("review_threads", {})
    review_threads = state_data["review_threads"]
    if not isinstance(review_threads, dict):
        pytest.fail(f"Mock gh state has invalid review_threads payload: {review_threads!r}")
    review_threads[str(pr_number)] = threads
    state_file.write_text(json.dumps(state_data))


def _seed_mock_pr(
    state_file: Path,
    pr_number: int,
    *,
    head_branch: str,
    title: str = "Seeded PR",
    body: str = "",
    pr_state: str = "OPEN",
    is_draft: bool = False,
    base_branch: str = MAIN_BRANCH,
) -> None:
    """Seed the mock gh state with a deterministic PR entry."""
    state_data = json.loads(state_file.read_text())
    state_data.setdefault("prs", {})
    prs = state_data["prs"]
    if not isinstance(prs, dict):
        pytest.fail(f"Mock gh state has invalid prs payload: {prs!r}")
    prs[str(pr_number)] = {
        "title": title,
        "body": body,
        "state": pr_state,
        "isDraft": is_draft,
        "base": base_branch,
        "head": head_branch,
        "url": f"https://github.com/test/e2e-project/pull/{pr_number}",
    }
    state_data["next_pr"] = max(int(state_data.get("next_pr", 1)), pr_number + 1)
    state_file.write_text(json.dumps(state_data))


def _count_gh_calls(
    log_file: Path,
    expected_args: list[str],
) -> int:
    """Count gh invocations that contain expected args in order."""
    invocations = _read_gh_log(log_file)
    return sum(1 for inv in invocations if _invocation_matches(inv, expected_args))


def _gh_call_count_total(log_file: Path) -> int:
    """Return total number of logged mock gh invocations."""
    return len(_read_gh_log(log_file))


def _invocation_matches(actual: list[str], expected: list[str]) -> bool:
    """Match command+subcommand exactly, then allow ordered-subsequence flags/args.

    This avoids false positives where unrelated commands happen to contain
    overlapping tokens.
    """
    if not expected:
        return True

    if not actual:
        return False

    if len(expected) == 1:
        return actual[0] == expected[0]

    if len(actual) < 2 or actual[0] != expected[0] or actual[1] != expected[1]:
        return False

    return _is_ordered_subsequence(actual[2:], expected[2:])


def _is_ordered_subsequence(actual: list[str], expected: list[str]) -> bool:
    """Return True when *expected* appears in *actual* with preserved order."""
    if not expected:
        return True
    i = 0
    for token in actual:
        if token == expected[i]:
            i += 1
            if i == len(expected):
                return True
    return False


def _find_mock_pr_number_by_head(state_file: Path, head_branch: str) -> str:
    """Return the PR number in mock state that matches the given head branch."""
    state_data = json.loads(state_file.read_text())
    prs = state_data.get("prs", {})
    if not isinstance(prs, dict):
        pytest.fail(f"Mock gh state has invalid prs payload: {prs!r}")
    for pr_number, pr_data in prs.items():
        if isinstance(pr_data, dict) and pr_data.get("head") == head_branch:
            return str(pr_number)
    pytest.fail(f"No PR found in mock gh state for head branch {head_branch!r}")


def _init_origin_remote(repo: Path) -> Path:
    """Create a local bare origin and push main so push-based workflows are testable."""
    remote_repo = repo.parent / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote_repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    _git(["remote", "add", "origin", str(remote_repo)], cwd=repo)
    _git(["push", "-u", "origin", MAIN_BRANCH], cwd=repo)
    return remote_repo


def _remote_has_branch(remote_repo: Path, branch_name: str) -> bool:
    """Check whether a branch exists in the local bare origin."""
    result = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch_name}"],
        cwd=remote_repo,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

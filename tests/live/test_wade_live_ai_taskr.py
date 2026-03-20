"""Manual live AI workflow tests against the real taskr repo.

This lane is intentionally destructive and repo-specific. It expects a dedicated
`taskr` sandbox repo with a working reset script and real GitHub remote state.
The wrapper script resets the repo before and after the run.

Required env:
  - RUN_LIVE_AI_TESTS=1
  - WADE_LIVE_REPO=/absolute/path/to/taskr
  - ANTHROPIC_API_KEY
Optional env:
  - WADE_LIVE_AI_TOOL (default: claude)
  - WADE_LIVE_AI_MODEL (default: claude-sonnet-4.6)
  - WADE_LIVE_AI_WORKFLOW_TIMEOUT (default: 300 seconds)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wade.models.delegation import DelegationMode, DelegationRequest
from wade.services.delegation_service import delegate

_LIVE_REPO_ENV = os.environ.get("WADE_LIVE_REPO") or os.environ.get("E2E_REPO")
LIVE_REPO = Path(_LIVE_REPO_ENV).expanduser() if _LIVE_REPO_ENV else None
AI_TOOL = os.environ.get("WADE_LIVE_AI_TOOL", "claude")
AI_MODEL = os.environ.get("WADE_LIVE_AI_MODEL", "claude-sonnet-4.6")
WADE_CLI = [sys.executable, "-m", "wade"]


def _parse_timeout(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


WORKFLOW_TIMEOUT = _parse_timeout(os.environ.get("WADE_LIVE_AI_WORKFLOW_TIMEOUT"), 300)
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "live"
GREET_HI_BODY = FIXTURES_DIR / "taskr_greet_hi_body.md"
GREET_HOWDY_BODY = FIXTURES_DIR / "taskr_greet_howdy_body.md"

pytestmark = [
    pytest.mark.live_ai,
    pytest.mark.live_gh,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_AI_TESTS") != "1",
        reason="Live AI tests disabled (set RUN_LIVE_AI_TESTS=1)",
    ),
]


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd or LIVE_REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wade(
    *args: str, cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return _run([*WADE_CLI, *args], cwd=cwd, timeout=timeout)


def _gh(
    *args: str, cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return _run(["gh", *args], cwd=cwd, timeout=timeout)


def _taskr_cli(
    *args: str, cwd: Path | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return _run(["uv", "run", "taskr", *args], cwd=cwd, timeout=timeout)


def _assert_ok(result: subprocess.CompletedProcess[str], context: str) -> None:
    assert result.returncode == 0, (
        f"{context} failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _assert_header_output(
    result: subprocess.CompletedProcess[str],
    *,
    expected_header: str,
    expected_contains: str,
    context: str,
) -> None:
    _assert_ok(result, context)
    lines = result.stdout.splitlines()
    assert lines, f"{context} produced no stdout"
    assert lines[0].strip() == expected_header, (
        f"{context} did not start with the expected greeting header.\n"
        f"expected={expected_header!r}\nstdout={result.stdout!r}"
    )
    assert expected_contains in result.stdout, (
        f"{context} did not include the expected command output.\n"
        f"expected_fragment={expected_contains!r}\nstdout={result.stdout!r}"
    )


def _assert_baseline_taskr_state(repo_root: Path) -> None:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
    _assert_ok(branch, "git rev-parse --abbrev-ref HEAD")
    assert branch.stdout.strip() == "main", (
        "taskr live workflow must start from main.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )

    status = _run(["git", "status", "--short"], cwd=repo_root)
    _assert_ok(status, "git status --short")
    assert not status.stdout.strip(), (
        "taskr live workflow must start from a clean repo.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )

    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    _assert_ok(head, "git rev-parse HEAD")
    reset_target = _run(["git", "rev-parse", "reset-target"], cwd=repo_root)
    _assert_ok(reset_target, "git rev-parse reset-target")
    assert head.stdout.strip() == reset_target.stdout.strip(), (
        "taskr live workflow must start from reset-target.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )

    open_issues = _gh("issue", "list", "--state", "open", "--json", "number")
    _assert_ok(open_issues, "gh issue list --state open")
    assert json.loads(open_issues.stdout) == [], (
        "taskr live workflow expects zero open issues at start.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )

    open_prs = _gh("pr", "list", "--state", "open", "--json", "number")
    _assert_ok(open_prs, "gh pr list --state open")
    assert json.loads(open_prs.stdout) == [], (
        "taskr live workflow expects zero open PRs at start.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )

    help_result = _taskr_cli(cwd=repo_root)
    _assert_ok(help_result, "uv run taskr")
    assert not help_result.stdout.startswith("Hi\n"), (
        "taskr baseline already includes the Hi header.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )
    assert not help_result.stdout.startswith("Howdy\n"), (
        "taskr baseline already includes the Howdy header.\n"
        "Run /Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh --yes first."
    )
    assert "Commands:" in help_result.stdout

    list_result = _taskr_cli("list", cwd=repo_root)
    _assert_ok(list_result, "uv run taskr list")
    assert not list_result.stdout.startswith("Hi\n")
    assert not list_result.stdout.startswith("Howdy\n")
    assert "No tasks." in list_result.stdout


def _issue_number_from_create_output(output: str) -> str:
    match = re.search(r"Created #(\d+)", output)
    assert match is not None, f"Could not parse created issue number from output:\n{output}"
    return match.group(1)


def _current_branch(cwd: Path) -> str:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    _assert_ok(branch, "git rev-parse --abbrev-ref HEAD")
    return branch.stdout.strip()


def _run_taskr_tests(cwd: Path) -> None:
    tests = _run(["./scripts/test.sh"], cwd=cwd, timeout=180)
    _assert_ok(tests, "./scripts/test.sh")


def _remove_known_agent_artifacts(cwd: Path) -> None:
    shutil.rmtree(cwd / ".cursor", ignore_errors=True)


def _delegate_issue_implementation(
    worktree_path: Path,
    issue_number: str,
    issue_body: str,
    commit_subject: str,
    expected_greeting: str,
) -> None:
    prompt = (
        f"You are implementing issue #{issue_number} in the taskr repo.\n"
        "If PLAN.md exists, use it. Otherwise follow the issue specification below.\n\n"
        "Issue specification:\n"
        f"{issue_body}\n\n"
        "Constraints:\n"
        "- Keep the diff small and targeted.\n"
        "- Do not modify scripts/ or AGENTS.md.\n"
        "- Do not add runtime dependencies.\n"
        "- Add or update tests as needed.\n"
        "- Run ./scripts/test.sh.\n"
        "- The greeting is a header on every command output, not a new subcommand.\n"
        "- Validate with: uv run taskr and uv run taskr list "
        f"(both must start with {expected_greeting!r}).\n"
        "- Preserve the existing command output after the greeting header.\n"
        f'- When tests pass, commit all changes with: git commit -m "{commit_subject}".\n'
        "Return a short summary of changed files and test status.\n"
    )
    result = delegate(
        DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt=prompt,
            ai_tool=AI_TOOL,
            model=AI_MODEL,
            cwd=worktree_path,
            timeout=WORKFLOW_TIMEOUT,
            allowed_commands=[
                "./scripts/test.sh *",
                "uv *",
                "git *",
                "python *",
                "cat *",
                "sed *",
                "ls *",
                "find *",
                "rg *",
                "grep *",
                "mkdir *",
                "rm *",
                "mv *",
                "cp *",
                "touch *",
            ],
        )
    )
    assert result.success, f"Live AI workflow delegation failed: {result.feedback!r}"

    _remove_known_agent_artifacts(worktree_path)
    clean = _run(["git", "status", "--short"], cwd=worktree_path)
    _assert_ok(clean, "git status --short (post-delegation)")
    assert clean.stdout.strip() == "", (
        f"Expected AI workflow to leave a clean worktree after committing.\nstatus={clean.stdout!r}"
    )

    head_subject = _run(["git", "log", "-1", "--format=%s"], cwd=worktree_path)
    _assert_ok(head_subject, "git log -1 --format=%s (post-delegation)")
    assert head_subject.stdout.strip() == commit_subject, (
        "Expected AI workflow to create the requested implementation commit.\n"
        f"expected={commit_subject!r}\nactual={head_subject.stdout.strip()!r}"
    )


def _create_issue_from_fixture(title: str, body_path: Path) -> str:
    created = _wade(
        "task",
        "create",
        "--title",
        title,
        "--body-file",
        str(body_path),
        timeout=120,
    )
    _assert_ok(created, f"wade task create --title {title}")
    return _issue_number_from_create_output(created.stdout)


def _implement_issue(
    issue_number: str, issue_body: str, commit_subject: str, expected_header: str
) -> tuple[Path, str]:
    implement = _wade("implement", issue_number, "--cd", timeout=180)
    _assert_ok(implement, f"wade implement {issue_number} --cd")
    worktree_path = Path(implement.stdout.strip())
    assert worktree_path.is_dir(), f"Expected worktree path from implement --cd: {worktree_path}"

    branch_name = _current_branch(worktree_path)
    assert branch_name, "Expected non-empty feature branch name"

    _delegate_issue_implementation(
        worktree_path,
        issue_number,
        issue_body,
        commit_subject,
        expected_header,
    )
    _run_taskr_tests(worktree_path)

    help_result = _taskr_cli(cwd=worktree_path, timeout=60)
    _assert_header_output(
        help_result,
        expected_header=expected_header,
        expected_contains="Commands:",
        context="uv run taskr (worktree)",
    )
    list_result = _taskr_cli("list", cwd=worktree_path, timeout=60)
    _assert_header_output(
        list_result,
        expected_header=expected_header,
        expected_contains="No tasks.",
        context="uv run taskr list (worktree)",
    )

    done = _wade("implementation-session", "done", cwd=worktree_path, timeout=180)
    _assert_ok(done, f"wade implementation-session done ({issue_number})")
    return worktree_path, branch_name


def _merge_pr_and_update_main(issue_number: str, branch_name: str, expected_header: str) -> str:
    pr_view = _gh("pr", "view", branch_name, "--json", "number,state,isDraft,url", timeout=120)
    _assert_ok(pr_view, f"gh pr view {branch_name}")
    pr_payload = json.loads(pr_view.stdout)
    assert isinstance(pr_payload, dict)
    pr_number = str(pr_payload.get("number", ""))
    assert pr_number, f"Expected PR number for branch {branch_name}"
    assert str(pr_payload.get("state", "")).upper() == "OPEN"

    merged = _gh("pr", "merge", pr_number, "--squash", "--delete-branch", timeout=180)
    _assert_ok(merged, f"gh pr merge {pr_number} --squash --delete-branch")

    checkout_main = _run(["git", "checkout", "main"], cwd=LIVE_REPO)
    _assert_ok(checkout_main, "git checkout main")
    pull = _run(["git", "pull", "--ff-only", "origin", "main"], cwd=LIVE_REPO, timeout=120)
    _assert_ok(pull, "git pull --ff-only origin main")

    _run_taskr_tests(LIVE_REPO)
    help_result = _taskr_cli(cwd=LIVE_REPO, timeout=60)
    _assert_header_output(
        help_result,
        expected_header=expected_header,
        expected_contains="Commands:",
        context="uv run taskr (main checkout)",
    )
    list_result = _taskr_cli("list", cwd=LIVE_REPO, timeout=60)
    _assert_header_output(
        list_result,
        expected_header=expected_header,
        expected_contains="No tasks.",
        context="uv run taskr list (main checkout)",
    )

    remove_worktree = _wade("worktree", "remove", issue_number, "--force", timeout=120)
    _assert_ok(remove_worktree, f"wade worktree remove {issue_number} --force")
    return pr_number


@pytest.fixture(autouse=True)
def require_live_taskr_repo() -> None:
    if LIVE_REPO is None:
        pytest.skip("WADE_LIVE_REPO or E2E_REPO is required for live taskr workflow tests")
    if not LIVE_REPO.is_dir():
        pytest.skip(f"Live repo not found at {LIVE_REPO}")
    git_check = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=LIVE_REPO)
    if git_check.returncode != 0:
        pytest.skip(f"{LIVE_REPO} is not a git worktree")
    if not (LIVE_REPO / ".wade.yml").is_file():
        pytest.skip(f"{LIVE_REPO} is missing .wade.yml")
    if not (LIVE_REPO / "scripts" / "reset.sh").is_file():
        pytest.skip(f"{LIVE_REPO} is missing scripts/reset.sh")
    if not (LIVE_REPO / "taskr" / "cli.py").is_file():
        pytest.skip(f"{LIVE_REPO} does not look like the taskr repo")
    pyproject_path = LIVE_REPO / "pyproject.toml"
    if not pyproject_path.is_file():
        pytest.skip(f"{LIVE_REPO} is missing pyproject.toml")
    if 'name = "taskr"' not in pyproject_path.read_text(encoding="utf-8"):
        pytest.skip(f"{LIVE_REPO} pyproject.toml is not the taskr package")


@pytest.fixture(autouse=True)
def require_live_workflow_tools() -> None:
    if AI_TOOL != "claude":
        pytest.skip(f"Taskr live AI workflow currently supports only claude (got {AI_TOOL!r})")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for the taskr live AI workflow")
    if not shutil.which("claude"):
        pytest.skip("claude CLI binary not found in PATH")
    if not shutil.which("gh"):
        pytest.skip("gh CLI binary not found in PATH")
    if not shutil.which("uv"):
        pytest.skip("uv CLI binary not found in PATH")
    wade = _run([*WADE_CLI, "--version"], cwd=None)
    if wade.returncode != 0:
        pytest.skip("Current checkout's wade CLI is not runnable")
    gh = _run(["gh", "auth", "status"], cwd=None)
    if gh.returncode != 0:
        pytest.skip("gh CLI not authenticated")


class TestLiveAITaskrWorkflow:
    def test_taskr_header_hi_then_howdy_workflow(self) -> None:
        """Exercise a real AI implementation workflow on taskr through two tiny issues."""
        assert LIVE_REPO is not None
        _assert_baseline_taskr_state(LIVE_REPO)

        unique = time.time_ns()
        hi_title = f"Add taskr greeting header ({unique})"
        howdy_title = f"Change taskr greeting header to Howdy ({unique})"

        hi_issue = _create_issue_from_fixture(hi_title, GREET_HI_BODY)
        _, hi_branch = _implement_issue(
            hi_issue,
            GREET_HI_BODY.read_text(encoding="utf-8"),
            commit_subject=f"feat: add taskr greeting header (#{hi_issue})",
            expected_header="Hi",
        )
        hi_pr = _merge_pr_and_update_main(hi_issue, hi_branch, expected_header="Hi")
        assert hi_pr

        howdy_issue = _create_issue_from_fixture(howdy_title, GREET_HOWDY_BODY)
        _, howdy_branch = _implement_issue(
            howdy_issue,
            GREET_HOWDY_BODY.read_text(encoding="utf-8"),
            commit_subject=f"fix: update taskr greeting header (#{howdy_issue})",
            expected_header="Howdy",
        )
        howdy_pr = _merge_pr_and_update_main(
            howdy_issue,
            howdy_branch,
            expected_header="Howdy",
        )
        assert howdy_pr

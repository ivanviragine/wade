"""End-to-end workflow chain tests via the ghaiwpy CLI binary.

Tests the full ghaiw lifecycle by invoking `ghaiwpy` as a subprocess,
exactly as a user would. This exercises CLI argument parsing, exit codes,
output formatting, and the full service stack end-to-end.

Uses real git repos and a mock gh CLI in PATH.
Requires `ghaiwpy` to be installed (e.g., `uv pip install -e .`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GHAIWPY = "ghaiwpy"


def _run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a ghaiwpy CLI command as a subprocess."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [GHAIWPY, *args],
        cwd=cwd,
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
    """Parse JSON from CLI stdout. Fails with an informative message if stdout is not pure JSON.

    When --json is used, stdout must be pure JSON. Any non-JSON output indicates
    structlog or other logging has leaked to stdout, which is a bug.
    """
    stdout = stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"--json output is not valid JSON (structlog may have leaked to stdout).\n"
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
    """Assert at least one gh invocation contains all expected args."""
    invocations = _read_gh_log(log_file)
    for inv in invocations:
        if all(arg in inv for arg in expected_args):
            return
    pytest.fail(f"No gh invocation contained all of {expected_args}.\nInvocations: {invocations}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def require_ghaiwpy() -> None:
    """Skip all tests if ghaiwpy is not installed."""
    if not shutil.which(GHAIWPY):
        pytest.skip("ghaiwpy CLI not found in PATH")


@pytest.fixture
def e2e_repo(tmp_path: Path) -> Path:
    """Create a fully initialized ghaiw project with config and initial commit.

    Simulates what a user would have after running `ghaiwpy init`.
    """
    repo = tmp_path / "project"
    repo.mkdir()

    # Init git
    _git(["init"], cwd=repo)
    _git(["config", "user.email", "e2e@test.com"], cwd=repo)
    _git(["config", "user.name", "E2E Test"], cwd=repo)

    # Create project files
    (repo / "README.md").write_text("# E2E Test Project\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text('print("hello")\n')

    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "Initial commit"], cwd=repo)

    # Create .ghaiw.yml
    (repo / ".ghaiw.yml").write_text(
        """\
version: 2

project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR

ai:
  default_tool: claude
"""
    )

    _git(["add", ".ghaiw.yml"], cwd=repo)
    _git(["commit", "-m", "Add ghaiw config"], cwd=repo)

    # Create .ghaiw/ dir (gitignored)
    (repo / ".ghaiw").mkdir()

    return repo


@pytest.fixture
def mock_gh_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Create a mock gh CLI that handles issue/PR/label/repo commands.

    Prepends the mock binary directory to PATH so ghaiwpy's subprocess
    calls to `gh` hit the mock instead.
    """
    mock_bin = tmp_path / "mock_bin"
    mock_bin.mkdir()
    log_file = tmp_path / "gh_log.jsonl"
    state_file = tmp_path / "gh_state.json"

    # Initialize state
    state = {"next_issue": 1, "next_pr": 1, "issues": {}, "prs": {}}
    state_file.write_text(json.dumps(state))

    gh_script = mock_bin / "gh"
    gh_script.write_text(
        f"""\
#!/usr/bin/env bash
# Mock gh CLI for E2E tests
set -euo pipefail

LOG="{log_file}"
STATE="{state_file}"

# Log the invocation as a JSON array for unambiguous parsing
printf '%s\n' "$(python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@")" >> "$LOG"

# Read state
state=$(cat "$STATE")

case "$1" in
    auth)
        echo "Logged in to github.com"
        ;;
    issue)
        case "$2" in
            create)
                title=""
                body=""
                while [[ $# -gt 0 ]]; do
                    case "$1" in
                        --title|-t) shift; title="$1" ;;
                        --body|-b)  shift; body="$1" ;;
                        --label|-l) shift ;;
                    esac
                    shift
                done
                num=$(echo "$state" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(d['next_issue'])")
                new_next=$((num + 1))
                state=$(echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['next_issue'] = $new_next
d['issues']['$num'] = {{'title': '''$title''', 'state': 'OPEN'}}
json.dump(d, sys.stdout)
")
                echo "$state" > "$STATE"
                echo "https://github.com/test/e2e-project/issues/$num"
                ;;
            list)
                echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
issues = []
for num, info in d.get('issues', {{}}).items():
    if info.get('state') == 'OPEN':
        issues.append({{
            'number': int(num),
            'title': info.get('title', ''),
            'state': 'OPEN',
            'labels': []
        }})
print(json.dumps(issues))
"
                ;;
            view)
                num="${{3:-1}}"
                echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
info = d.get('issues', {{}}).get('$num', {{}})
print(json.dumps({{
    'number': int('$num'),
    'title': info.get('title', 'Test issue'),
    'body': info.get('body', ''),
    'state': info.get('state', 'OPEN'),
    'labels': [],
    'url': f'https://github.com/test/e2e-project/issues/$num'
}}))
"
                ;;
            close)
                num="${{3:-1}}"
                state=$(echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if '$num' in d.get('issues', {{}}):
    d['issues']['$num']['state'] = 'CLOSED'
json.dump(d, sys.stdout)
")
                echo "$state" > "$STATE"
                ;;
        esac
        ;;
    pr)
        case "$2" in
            create)
                num=$(echo "$state" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(d['next_pr'])")
                new_next=$((num + 1))
                state=$(echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['next_pr'] = $new_next
d['prs']['$num'] = {{'state': 'OPEN'}}
json.dump(d, sys.stdout)
")
                echo "$state" > "$STATE"
                echo "https://github.com/test/e2e-project/pull/$num"
                ;;
            merge)
                ;;
            list)
                echo "[]"
                ;;
        esac
        ;;
    label)
        # No-op for label operations
        ;;
    repo)
        echo '{{"name": "e2e-project", "owner": {{"login": "test"}}}}'
        ;;
    *)
        echo "mock-gh: unknown command: $1" >&2
        ;;
esac
"""
    )
    gh_script.chmod(0o755)

    # Prepend mock bin to PATH so ghaiwpy subprocess calls hit the mock
    monkeypatch.setenv("PATH", f"{mock_bin}:{os.environ.get('PATH', '')}")

    return {
        "log_file": log_file,
        "state_file": state_file,
        "mock_bin": mock_bin,
    }


# ---------------------------------------------------------------------------
# Tests: Basic CLI commands
# ---------------------------------------------------------------------------


class TestCLIBasics:
    """Smoke tests for basic ghaiwpy commands."""

    def test_version(self) -> None:
        """ghaiwpy --version exits 0 and prints version string."""
        result = subprocess.run([GHAIWPY, "--version"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "ghaiw" in result.stdout.lower()

    def test_help(self) -> None:
        """ghaiwpy --help exits 0 and lists key subcommands."""
        result = subprocess.run([GHAIWPY, "--help"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert "task" in output
        assert "work" in output


# ---------------------------------------------------------------------------
# Tests: check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Test `ghaiwpy check` via CLI subprocess."""

    def test_check_in_main_checkout(self, e2e_repo: Path) -> None:
        """ghaiwpy check on main → exit 2, stdout contains IN_MAIN_CHECKOUT."""
        result = _run(["check"], cwd=e2e_repo)
        assert result.returncode == 2
        assert "IN_MAIN_CHECKOUT" in result.stdout

    def test_check_in_worktree(self, e2e_repo: Path) -> None:
        """ghaiwpy check in a worktree → exit 0, stdout contains IN_WORKTREE."""
        wt_dir = e2e_repo.parent / ".worktrees" / "42-test"
        _git(
            ["worktree", "add", "-b", "feat/42-test", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["check"], cwd=wt_dir)
        assert result.returncode == 0
        assert "IN_WORKTREE" in result.stdout

    def test_check_not_in_git(self, tmp_path: Path) -> None:
        """ghaiwpy check outside git → exit 1, stdout contains NOT_IN_GIT_REPO."""
        bare_dir = tmp_path / "not-a-repo"
        bare_dir.mkdir()

        result = _run(["check"], cwd=bare_dir)
        assert result.returncode == 1
        assert "NOT_IN_GIT_REPO" in result.stdout


# ---------------------------------------------------------------------------
# Tests: check-config command
# ---------------------------------------------------------------------------


class TestCheckConfigCommand:
    """Test `ghaiwpy check-config` via CLI subprocess."""

    def test_valid_config(self, e2e_repo: Path) -> None:
        """ghaiwpy check-config with valid .ghaiw.yml → exit 0."""
        result = _run(["check-config"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "VALID_CONFIG" in result.stdout

    def test_no_config(self, tmp_path: Path) -> None:
        """ghaiwpy check-config without .ghaiw.yml → exit 1."""
        bare = tmp_path / "empty"
        bare.mkdir()
        _git(["init"], cwd=bare)
        _git(["config", "user.email", "test@test.com"], cwd=bare)
        _git(["config", "user.name", "Test"], cwd=bare)
        (bare / "x.txt").write_text("x\n")
        _git(["add", "."], cwd=bare)
        _git(["commit", "-m", "init"], cwd=bare)

        result = _run(["check-config"], cwd=bare)
        assert result.returncode == 1
        assert "CONFIG_NOT_FOUND" in result.stdout


# ---------------------------------------------------------------------------
# Tests: task commands
# ---------------------------------------------------------------------------


class TestTaskCommands:
    """Test `ghaiwpy task` subcommands via CLI subprocess."""

    def test_task_create_from_plan(
        self, e2e_repo: Path, mock_gh_cli: dict[str, Any], tmp_path: Path
    ) -> None:
        """ghaiwpy task create --plan-file --no-start creates an issue."""
        plan = tmp_path / "PLAN.md"
        plan.write_text(
            """\
# Add search feature

## Complexity

easy

## Description

Add a search command to find tasks by keyword.

## Tasks

- [ ] Implement search logic
- [ ] Add CLI command
- [ ] Write tests
"""
        )

        result = _run(
            ["task", "create", "--plan-file", str(plan), "--no-start"],
            cwd=e2e_repo,
        )

        assert result.returncode == 0
        # Should mention the created issue number
        combined = result.stdout + result.stderr
        assert "#1" in combined or "issues/1" in combined

        # Verify mock gh was called with expected arguments
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "create", "--title"],
        )

    def test_task_list(self, e2e_repo: Path, mock_gh_cli: dict[str, Any]) -> None:
        """ghaiwpy task list exits 0."""
        result = _run(["task", "list"], cwd=e2e_repo)
        assert result.returncode == 0

    def test_task_list_json(self, e2e_repo: Path, mock_gh_cli: dict[str, Any]) -> None:
        """ghaiwpy task list --json outputs valid JSON array."""
        result = _run(["task", "list", "--json"], cwd=e2e_repo)
        assert result.returncode == 0
        # Extract the JSON portion (skip any non-JSON lines)
        parsed = _parse_json_output(result.stdout)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Tests: work sync command
# ---------------------------------------------------------------------------


class TestWorkSyncCommand:
    """Test `ghaiwpy work sync` via CLI subprocess."""

    def test_sync_clean_merge(self, e2e_repo: Path) -> None:
        """ghaiwpy work sync when main has diverged — clean merge."""
        # Create worktree
        wt_dir = e2e_repo.parent / ".worktrees" / "50-feature"
        _git(
            ["worktree", "add", "-b", "feat/50-feature", str(wt_dir)],
            cwd=e2e_repo,
        )

        # Feature work
        (wt_dir / "feature.py").write_text("# new feature\n")
        _git(["add", "."], cwd=wt_dir)
        _git(["commit", "-m", "feat: new feature"], cwd=wt_dir)

        # Main advances (no conflict)
        _git(["checkout", "main"], cwd=e2e_repo)
        (e2e_repo / "docs.md").write_text("# Docs\n")
        _git(["add", "."], cwd=e2e_repo)
        _git(["commit", "-m", "docs: add docs"], cwd=e2e_repo)

        # Sync via CLI
        result = _run(["work", "sync"], cwd=wt_dir)

        assert result.returncode == 0
        assert (wt_dir / "docs.md").exists()  # main's change merged in
        assert (wt_dir / "feature.py").exists()  # feature work preserved

    def test_sync_already_up_to_date(self, e2e_repo: Path) -> None:
        """ghaiwpy work sync when already up to date — no-op."""
        wt_dir = e2e_repo.parent / ".worktrees" / "51-uptodate"
        _git(
            ["worktree", "add", "-b", "feat/51-uptodate", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "sync"], cwd=wt_dir)
        assert result.returncode == 0

    def test_sync_conflict_exit_code(self, e2e_repo: Path) -> None:
        """ghaiwpy work sync with merge conflict → exit 2."""
        wt_dir = e2e_repo.parent / ".worktrees" / "60-conflict"
        _git(
            ["worktree", "add", "-b", "feat/60-conflict", str(wt_dir)],
            cwd=e2e_repo,
        )

        # Modify same file on both sides
        (wt_dir / "README.md").write_text("Feature version\n")
        _git(["add", "."], cwd=wt_dir)
        _git(["commit", "-m", "feat: update readme"], cwd=wt_dir)

        _git(["checkout", "main"], cwd=e2e_repo)
        (e2e_repo / "README.md").write_text("Main version\n")
        _git(["add", "."], cwd=e2e_repo)
        _git(["commit", "-m", "docs: update readme"], cwd=e2e_repo)

        result = _run(["work", "sync"], cwd=wt_dir)
        assert result.returncode == 2

        # Clean up merge state
        subprocess.run(["git", "merge", "--abort"], cwd=wt_dir, capture_output=True)

    def test_sync_json_output(self, e2e_repo: Path) -> None:
        """ghaiwpy work sync --json emits structured events."""
        wt_dir = e2e_repo.parent / ".worktrees" / "52-json"
        _git(
            ["worktree", "add", "-b", "feat/52-json", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "sync", "--json"], cwd=wt_dir)
        assert result.returncode == 0

        # Each non-empty line that starts with { should be valid JSON event
        json_lines = [
            line
            for line in result.stdout.strip().split("\n")
            if line.strip() and line.strip().startswith("{")
        ]
        assert len(json_lines) >= 1, f"Expected JSON events, got: {result.stdout!r}"
        for line in json_lines:
            parsed = json.loads(line)
            assert "event" in parsed

    def test_sync_from_main_rejected(self, e2e_repo: Path) -> None:
        """ghaiwpy work sync from main branch → exit 4 (preflight failure)."""
        result = _run(["work", "sync"], cwd=e2e_repo)
        assert result.returncode == 4


# ---------------------------------------------------------------------------
# Tests: work list command
# ---------------------------------------------------------------------------


class TestWorkListCommand:
    """Test `ghaiwpy work list` via CLI subprocess."""

    def test_list_empty(self, e2e_repo: Path) -> None:
        """ghaiwpy work list with no worktrees → exit 0."""
        result = _run(["work", "list"], cwd=e2e_repo)
        assert result.returncode == 0

    def test_list_with_worktrees(self, e2e_repo: Path) -> None:
        """ghaiwpy work list with worktrees → shows branch names in output."""
        for num, slug in [("10", "auth"), ("11", "db")]:
            wt_dir = e2e_repo.parent / ".worktrees" / f"{num}-{slug}"
            _git(
                ["worktree", "add", "-b", f"feat/{num}-{slug}", str(wt_dir)],
                cwd=e2e_repo,
            )

        result = _run(["work", "list"], cwd=e2e_repo)
        assert result.returncode == 0
        assert "10" in result.stdout or "auth" in result.stdout, (
            f"Expected worktree '10-auth' in output, got: {result.stdout!r}"
        )
        assert "11" in result.stdout or "db" in result.stdout, (
            f"Expected worktree '11-db' in output, got: {result.stdout!r}"
        )

    def test_list_json(self, e2e_repo: Path) -> None:
        """ghaiwpy work list --json outputs valid JSON array."""
        wt_dir = e2e_repo.parent / ".worktrees" / "20-test"
        _git(
            ["worktree", "add", "-b", "feat/20-test", str(wt_dir)],
            cwd=e2e_repo,
        )

        result = _run(["work", "list", "--json"], cwd=e2e_repo)
        assert result.returncode == 0
        parsed = _parse_json_output(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1

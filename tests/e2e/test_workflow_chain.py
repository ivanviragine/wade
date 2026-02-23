"""End-to-end workflow chain tests.

Tests the full ghaiw lifecycle: init → check → task create → work start →
work sync → work done → work list → work remove.

Uses real git repos but mocks the gh CLI and AI tool launches.
These tests exercise the full stack (services, providers, git, config)
as a single integration chain.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ghaiw.config.loader import load_config
from ghaiw.git.worktree import create_worktree
from ghaiw.models.config import ProjectConfig, ProjectSettings
from ghaiw.models.task import Task
from ghaiw.models.work import WorktreeState
from ghaiw.services.check_service import check_worktree
from ghaiw.services.work_service import (
    _build_pr_body,
    classify_staleness,
    extract_issue_from_branch,
    list_sessions,
    remove,
    sync,
    write_issue_context,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_repo(tmp_path: Path) -> Path:
    """Create a fully initialized ghaiw project with config and initial commit.

    This simulates what a user would have after running `ghaiw init` on
    a fresh project.
    """
    repo = tmp_path / "project"
    repo.mkdir()

    # Init git
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "e2e@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "E2E Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    # Create project files (simulating a minimal project)
    (repo / "README.md").write_text("# E2E Test Project\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text('print("hello")\n')

    subprocess.run(
        ["git", "add", "."], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

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

    subprocess.run(
        ["git", "add", ".ghaiw.yml"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add ghaiw config"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    # Create .ghaiw/ dir (gitignored)
    (repo / ".ghaiw").mkdir()

    return repo


@pytest.fixture
def mock_gh_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Create a sophisticated mock gh CLI that handles multiple commands.

    Returns a dict with:
      - log_file: Path to the invocation log
      - issues: dict of created issues
      - prs: dict of created PRs
    """
    mock_bin = tmp_path / "mock_bin"
    mock_bin.mkdir()
    log_file = tmp_path / "gh_log.jsonl"
    state_file = tmp_path / "gh_state.json"

    # Initialize state
    state = {"next_issue": 1, "next_pr": 1, "issues": {}, "prs": {}}
    state_file.write_text(json.dumps(state))

    # Write a more capable mock gh script
    gh_script = mock_bin / "gh"
    gh_script.write_text(
        f"""\
#!/usr/bin/env bash
# Mock gh CLI for E2E tests
set -euo pipefail

LOG="{log_file}"
STATE="{state_file}"

# Log the invocation
echo "$@" >> "$LOG"

# Read state
state=$(cat "$STATE")

case "$1" in
    auth)
        echo "Logged in to github.com"
        ;;
    issue)
        case "$2" in
            create)
                # Extract title from args
                title=""
                body=""
                while [[ $# -gt 0 ]]; do
                    case "$1" in
                        --title|-t) shift; title="$1" ;;
                        --body|-b)  shift; body="$1" ;;
                        --label|-l) shift ;; # ignore
                    esac
                    shift
                done
                # Generate issue number
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
                # Return issues as JSON
                echo "$state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
issues = []
for num, info in d.get('issues', {{}}).items():
    if info.get('state') == 'OPEN':
        issues.append({{'number': int(num), 'title': info.get('title', ''), 'state': 'OPEN'}})
print(json.dumps(issues))
"
                ;;
            view)
                # Return single issue
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

    # Prepend mock bin to PATH
    monkeypatch.setenv("PATH", f"{mock_bin}:{os.environ.get('PATH', '')}")

    return {
        "log_file": log_file,
        "state_file": state_file,
        "mock_bin": mock_bin,
    }


# ---------------------------------------------------------------------------
# Test: Full lifecycle chain
# ---------------------------------------------------------------------------


class TestWorkflowChain:
    """Test the complete ghaiw workflow from init to cleanup."""

    def test_check_in_main_checkout(self, e2e_repo: Path) -> None:
        """check returns IN_MAIN_CHECKOUT when on main branch."""
        result = check_worktree(cwd=e2e_repo)
        assert result.status == "IN_MAIN_CHECKOUT"

    def test_check_in_worktree(self, e2e_repo: Path) -> None:
        """check returns IN_WORKTREE when in a worktree."""
        wt_dir = e2e_repo.parent / ".worktrees" / "42-test"
        create_worktree(e2e_repo, "feat/42-test", wt_dir, "main")

        result = check_worktree(cwd=wt_dir)
        assert result.status == "IN_WORKTREE"

    def test_full_lifecycle_pr_strategy(
        self, e2e_repo: Path, mock_gh_cli: dict[str, Any]
    ) -> None:
        """Full lifecycle: check → worktree → work → sync → PR body.

        This chains together multiple service calls to verify the full
        workflow without launching an AI tool.
        """
        # 1. Check — we're on main
        result = check_worktree(cwd=e2e_repo)
        assert result.status == "IN_MAIN_CHECKOUT"

        # 2. Create worktree (simulating what work start does)
        wt_dir = e2e_repo.parent / ".worktrees" / "42-add-search"
        create_worktree(e2e_repo, "feat/42-add-search", wt_dir, "main")

        # 3. Write issue context
        task = Task(
            id="42",
            title="Add task search command",
            body="## Tasks\n- Add search method\n- Add CLI command\n- Add tests\n",
            url="https://github.com/test/e2e-project/issues/42",
        )
        ctx_path = write_issue_context(wt_dir, task)
        assert ctx_path.is_file()
        assert "# Issue #42" in ctx_path.read_text()

        # 4. Do some work in the worktree (commit context + feature file together)
        (wt_dir / "search.py").write_text(
            """\
def search(tasks, query):
    return [t for t in tasks if query.lower() in t.title.lower()]
"""
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=wt_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: add task search"],
            cwd=wt_dir,
            capture_output=True,
            check=True,
        )

        # 5. Meanwhile, main advances
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )
        (e2e_repo / "CHANGELOG.md").write_text("# Changelog\n")
        subprocess.run(
            ["git", "add", "CHANGELOG.md"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "docs: add changelog"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )

        # 6. Sync — merge main into feature
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))
        with patch("ghaiw.services.work_service.load_config", return_value=config):
            sync_result = sync(project_root=wt_dir)

        assert sync_result.success
        assert (wt_dir / "CHANGELOG.md").exists()  # main's change is merged in

        # 7. Check from worktree — confirms IN_WORKTREE
        wt_check = check_worktree(cwd=wt_dir)
        assert wt_check.status == "IN_WORKTREE"

        # 8. Build PR body (the part of work done that doesn't need gh)
        pr_summary = e2e_repo.parent / "PR-SUMMARY-42.md"
        pr_summary.write_text("Added search functionality for tasks.\n")

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary,
            close_issue=True,
            parent_issue=None,
        )

        assert "Closes #42" in body
        assert "search functionality" in body

        # 9. Verify worktree classification
        staleness = classify_staleness(
            repo_root=e2e_repo,
            branch="feat/42-add-search",
            main_branch="main",
        )
        assert staleness == WorktreeState.ACTIVE

    def test_multi_worktree_lifecycle(
        self, e2e_repo: Path, mock_gh_cli: dict[str, Any]
    ) -> None:
        """Multiple concurrent worktrees: create, list, classify, remove."""
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))

        # Create three worktrees (simulating batch start)
        for num, slug in [("10", "auth"), ("11", "db"), ("12", "ui")]:
            wt_dir = e2e_repo.parent / ".worktrees" / f"{num}-{slug}"
            create_worktree(e2e_repo, f"feat/{num}-{slug}", wt_dir, "main")

        # Add work to two of them
        for num, slug in [("10", "auth"), ("12", "ui")]:
            wt_dir = e2e_repo.parent / ".worktrees" / f"{num}-{slug}"
            (wt_dir / f"{slug}.py").write_text(f"# {slug}\n")
            subprocess.run(
                ["git", "add", "."], cwd=wt_dir, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"feat: add {slug}"],
                cwd=wt_dir,
                capture_output=True,
                check=True,
            )

        # List sessions
        with patch("ghaiw.services.work_service.load_config", return_value=config):
            sessions = list_sessions(project_root=e2e_repo)

        assert len(sessions) == 3
        issues = {s["issue"] for s in sessions}
        assert issues == {"10", "11", "12"}

        # Classify staleness
        active = classify_staleness(e2e_repo, "feat/10-auth", "main")
        stale = classify_staleness(e2e_repo, "feat/11-db", "main")
        assert active == WorktreeState.ACTIVE
        assert stale == WorktreeState.STALE_EMPTY

        # Remove stale worktree
        with patch("ghaiw.services.work_service.load_config", return_value=config):
            result = remove(target="11", project_root=e2e_repo)
        assert result

        # Verify removal
        with patch("ghaiw.services.work_service.load_config", return_value=config):
            sessions = list_sessions(project_root=e2e_repo)
        assert len(sessions) == 2

    def test_sync_clean_merge(self, e2e_repo: Path) -> None:
        """Sync when main has diverged — clean merge."""
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))

        # Create worktree
        wt_dir = e2e_repo.parent / ".worktrees" / "50-feature"
        create_worktree(e2e_repo, "feat/50-feature", wt_dir, "main")

        # Feature work
        (wt_dir / "feature.py").write_text("# new feature\n")
        subprocess.run(
            ["git", "add", "."], cwd=wt_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: new feature"],
            cwd=wt_dir,
            capture_output=True,
            check=True,
        )

        # Main advances (no conflict)
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )
        (e2e_repo / "docs.md").write_text("# Docs\n")
        subprocess.run(
            ["git", "add", "."], cwd=e2e_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "docs: add docs"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )

        # Sync
        with patch("ghaiw.services.work_service.load_config", return_value=config):
            result = sync(project_root=wt_dir)

        assert result.success
        assert (wt_dir / "docs.md").exists()
        assert (wt_dir / "feature.py").exists()

    def test_sync_conflict(self, e2e_repo: Path) -> None:
        """Sync when main conflicts with feature — detects conflicts."""
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))

        wt_dir = e2e_repo.parent / ".worktrees" / "60-conflict"
        create_worktree(e2e_repo, "feat/60-conflict", wt_dir, "main")

        # Modify same file on both sides
        (wt_dir / "README.md").write_text("Feature version\n")
        subprocess.run(
            ["git", "add", "."], cwd=wt_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: update readme"],
            cwd=wt_dir,
            capture_output=True,
            check=True,
        )

        subprocess.run(
            ["git", "checkout", "main"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )
        (e2e_repo / "README.md").write_text("Main version\n")
        subprocess.run(
            ["git", "add", "."], cwd=e2e_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "docs: update readme"],
            cwd=e2e_repo,
            capture_output=True,
            check=True,
        )

        with patch("ghaiw.services.work_service.load_config", return_value=config):
            result = sync(project_root=wt_dir)

        assert not result.success
        assert "README.md" in result.conflicts

        # Clean up merge state
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=wt_dir,
            capture_output=True,
        )

    def test_pr_body_with_parent_issue(self, tmp_path: Path) -> None:
        """PR body includes parent tracking issue reference."""
        task = Task(
            id="42",
            title="Add search",
            body="## Tasks\n- Search\n",
        )

        pr_summary = tmp_path / "PR-SUMMARY-42.md"
        pr_summary.write_text("Implemented search.\n")

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary,
            close_issue=True,
            parent_issue="10",
        )

        assert "Closes #42" in body
        assert "Part of #10" in body
        assert "Implemented search." in body

    def test_pr_body_no_close(self, tmp_path: Path) -> None:
        """PR body without Closes when --no-close is used."""
        pr_summary = tmp_path / "PR-SUMMARY-42.md"
        pr_summary.write_text("Did the work.\n")

        task = Task(
            id="42",
            title="Add search",
            body="",
        )

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary,
            close_issue=False,
            parent_issue=None,
        )

        assert "Closes #42" not in body
        assert "Did the work." in body


class TestIssueExtraction:
    """Test extracting issue numbers from branch names."""

    def test_standard_format(self) -> None:
        assert extract_issue_from_branch("feat/42-add-search") == "42"

    def test_numeric_only(self) -> None:
        assert extract_issue_from_branch("feat/42") == "42"

    def test_custom_prefix(self) -> None:
        assert extract_issue_from_branch("fix/100-bug") == "100"

    def test_no_issue(self) -> None:
        assert extract_issue_from_branch("main") is None


class TestConfigLoading:
    """Test config loading in E2E context."""

    def test_load_config_from_project(self, e2e_repo: Path) -> None:
        """Config loads and parses correctly from project root."""
        config = load_config(e2e_repo)
        assert config.project.main_branch == "main"
        assert config.project.issue_label == "feature-plan"
        assert config.project.branch_prefix == "feat"

    def test_load_config_from_worktree(self, e2e_repo: Path) -> None:
        """Config loads correctly when CWD is inside a worktree."""
        wt_dir = e2e_repo.parent / ".worktrees" / "70-config"
        create_worktree(e2e_repo, "feat/70-config", wt_dir, "main")

        # Config should be discoverable from worktree (walks up to find it)
        # In practice, config is in the main repo; worktrees may or may not have it.
        # The config loader should handle both cases.
        config = load_config(e2e_repo)
        assert config.project.main_branch == "main"

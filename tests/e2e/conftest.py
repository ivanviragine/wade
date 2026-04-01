"""Shared fixtures for deterministic E2E CLI contract tests."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.e2e._support import MAIN_BRANCH, WADE, MockGhCli, _git
from tests.e2e.mock_gh_script import MOCK_GH_SCRIPT


@pytest.fixture(autouse=True)
def require_wade() -> None:
    """Fail fast when deterministic e2e prerequisites are missing."""
    if not shutil.which(WADE):
        pytest.fail("wade CLI not found in PATH (required for deterministic e2e tests)")


@pytest.fixture
def e2e_repo(tmp_path: Path) -> Path:
    """Create a fully initialized wade project with config and initial commit."""
    repo = tmp_path / "project"
    repo.mkdir()

    _git(["init", "-b", MAIN_BRANCH], cwd=repo)
    _git(["config", "user.email", "e2e@test.com"], cwd=repo)
    _git(["config", "user.name", "E2E Test"], cwd=repo)

    (repo / "README.md").write_text("# E2E Test Project\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text('print("hello")\n', encoding="utf-8")

    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "Initial commit"], cwd=repo)

    (repo / ".wade.yml").write_text(
        f"""\
version: 2

project:
  main_branch: {MAIN_BRANCH}
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR

ai:
  default_tool: claude
""",
        encoding="utf-8",
    )

    # Commit .wade.yml and a basic .gitignore (tracked so --skip-worktree works in worktrees)
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(["add", ".wade.yml", ".gitignore"], cwd=repo)
    _git(["commit", "-m", "chore: initialize wade"], cwd=repo)

    # Create .wade/ self-ignoring runtime dir
    wade_dir = repo / ".wade"
    wade_dir.mkdir()
    (wade_dir / ".gitignore").write_text("*\n", encoding="utf-8")

    return repo


@pytest.fixture
def mock_gh_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MockGhCli:
    """Create a stateful mock gh CLI for deterministic contract tests."""
    mock_bin = tmp_path / "mock_bin"
    mock_bin.mkdir()
    log_file = tmp_path / "gh_log.jsonl"
    state_file = tmp_path / "gh_state.json"

    state = {
        "next_issue": 1,
        "next_pr": 1,
        "issues": {},
        "prs": {},
        "labels": {},
        "review_threads": {},
    }
    state_file.write_text(json.dumps(state), encoding="utf-8")

    gh_script = mock_bin / "gh"
    gh_script.write_text(MOCK_GH_SCRIPT, encoding="utf-8")
    gh_script.chmod(0o755)

    monkeypatch.setenv("WADE_MOCK_GH_LOG", str(log_file))
    monkeypatch.setenv("WADE_MOCK_GH_STATE", str(state_file))
    monkeypatch.setenv("PATH", f"{mock_bin}:{os.environ.get('PATH', '')}")

    return {
        "log_file": log_file,
        "state_file": state_file,
        "mock_bin": mock_bin,
    }

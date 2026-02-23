"""Shared test fixtures for ghaiw test suite."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit.

    Returns the path to the repo root.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    # Create initial commit so HEAD exists
    readme = repo / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


@pytest.fixture
def tmp_ghaiw_project(tmp_git_repo: Path) -> Path:
    """Create a temporary git repo with a .ghaiw.yml config file.

    Returns the path to the repo root.
    """
    config = tmp_git_repo / ".ghaiw.yml"
    config.write_text(
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
    return tmp_git_repo


@pytest.fixture
def monkeypatch_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Provide monkeypatch with common env var cleanup."""
    # Clear any GHAIW_ env vars that might leak from the test runner's environment
    for key in list(os.environ):
        if key.startswith("GHAIW_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def mock_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a mock gh CLI binary that records invocations.

    Returns the path to the invocation log file.
    """
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    log_file = tmp_path / "gh_invocations.log"

    gh_script = mock_bin / "gh"
    gh_script.write_text(
        f"""\
#!/usr/bin/env bash
echo "$@" >> "{log_file}"
echo '{{"number": 1, "url": "https://github.com/test/repo/issues/1"}}'
"""
    )
    gh_script.chmod(0o755)

    # Prepend mock bin to PATH
    monkeypatch.setenv("PATH", f"{mock_bin}:{os.environ.get('PATH', '')}")

    return log_file

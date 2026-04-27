"""Deterministic E2E contracts for admin workflows like `wade init`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.e2e._support import _git, _run

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


def _init_git_repo(repo: Path) -> None:
    """Create a raw git repo suitable for `wade init` contract testing."""
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "e2e@test.com"], cwd=repo)
    _git(["config", "user.name", "E2E Test"], cwd=repo)
    (repo / "README.md").write_text("# Admin contract repo\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "check.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "Initial commit"], cwd=repo)


class TestInitCommand:
    """Test `wade init` via CLI subprocess."""

    def test_init_writes_expected_defaults_without_legacy_permissions(self, tmp_path: Path) -> None:
        """init should write balanced medium defaults and not auto-add allowed commands."""
        repo = tmp_path / "admin-project"
        _init_git_repo(repo)

        result = _run(["init", "--yes", "--ai", "claude"], cwd=repo)

        assert result.returncode == 0
        config = yaml.safe_load((repo / ".wade.yml").read_text(encoding="utf-8"))
        assert config["ai"]["default_tool"] == "claude"
        assert config["models"]["claude"]["easy"] == {"model": "claude-haiku-4.5", "effort": None}
        assert config["models"]["claude"]["medium"] == {
            "model": "claude-sonnet-4.6",
            "effort": None,
        }
        assert config["models"]["claude"]["complex"] == {
            "model": "claude-sonnet-4.6",
            "effort": None,
        }
        assert config["models"]["claude"]["medium"] != config["models"]["claude"]["easy"]
        assert "permissions" not in config
        assert (repo / ".wade" / ".wade-managed").is_file()
        # AGENTS.md pointer is no longer written to main during init — only to worktrees
        assert not (repo / "AGENTS.md").is_file()
        assert not (repo / "CLAUDE.md").exists()

        # init no longer writes a committed gitignore block
        gitignore_path = repo / ".gitignore"
        if gitignore_path.is_file():
            gitignore = gitignore_path.read_text(encoding="utf-8")
            assert "# wade:start" not in gitignore

        # .wade/ should be self-ignoring
        assert (repo / ".wade" / ".gitignore").is_file()

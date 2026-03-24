"""Deterministic E2E contracts for project knowledge CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import _run

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


def _write_knowledge_config(
    repo: Path, *, enabled: bool = True, path: str = "docs/KNOWLEDGE.md"
) -> None:
    config_path = repo / ".wade.yml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\nknowledge:\n"
        + f"  enabled: {'true' if enabled else 'false'}\n"
        + f"  path: {path}\n",
        encoding="utf-8",
    )


class TestKnowledgeCommands:
    def test_knowledge_add_appends_entry_from_stdin(self, e2e_repo: Path) -> None:
        """knowledge add should append a formatted entry to the configured file."""
        _write_knowledge_config(e2e_repo)

        result = _run(
            ["knowledge", "add", "--session", "plan", "--issue", "7"],
            cwd=e2e_repo,
            input_text="Prefer labels over issue body metadata.\n",
        )

        assert result.returncode == 0
        assert "Knowledge entry " in result.stdout
        assert " added to KNOWLEDGE.md" in result.stdout
        knowledge_path = e2e_repo / "docs" / "KNOWLEDGE.md"
        assert knowledge_path.exists()
        knowledge_text = knowledge_path.read_text(encoding="utf-8")
        assert "| plan | Issue #7" in knowledge_text
        assert "Prefer labels over issue body metadata." in knowledge_text

    def test_knowledge_get_prints_contents_exactly_to_stdout(self, e2e_repo: Path) -> None:
        """knowledge get should print the file contents without extra formatting."""
        _write_knowledge_config(e2e_repo)
        knowledge_path = e2e_repo / "docs" / "KNOWLEDGE.md"
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        expected = "# Project Knowledge\n\nPrefer labels.\n"
        knowledge_path.write_text(expected, encoding="utf-8")

        result = _run(["knowledge", "get"], cwd=e2e_repo)

        assert result.returncode == 0
        assert result.stdout == expected
        assert result.stderr == ""

    def test_knowledge_get_missing_file_notices_on_stderr(self, e2e_repo: Path) -> None:
        """knowledge get should keep stdout clean when the file is missing."""
        _write_knowledge_config(e2e_repo)

        result = _run(["knowledge", "get"], cwd=e2e_repo)

        assert result.returncode == 0
        assert result.stdout == ""
        assert "No knowledge file found." in result.stderr

    def test_knowledge_get_disabled_exits_cleanly(self, e2e_repo: Path) -> None:
        """knowledge get should fail with a user-facing error when disabled."""
        _write_knowledge_config(e2e_repo, enabled=False)

        result = _run(["knowledge", "get"], cwd=e2e_repo)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "Knowledge capture is not enabled" in result.stderr

    def test_knowledge_rate_updates_sidecar_file(self, e2e_repo: Path) -> None:
        """knowledge rate should update the sidecar ratings file for an existing entry."""
        _write_knowledge_config(e2e_repo)
        knowledge_path = e2e_repo / "docs" / "KNOWLEDGE.md"
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_path.write_text(
            (
                "# Project Knowledge\n\n---\n\n## a1b2c3d4 | 2026-03-24 | plan\n\n"
                "Prefer labels.\n\n---\n"
            ),
            encoding="utf-8",
        )

        result = _run(["knowledge", "rate", "a1b2c3d4", "up"], cwd=e2e_repo)

        assert result.returncode == 0
        ratings_text = (e2e_repo / "docs" / "KNOWLEDGE.ratings.yml").read_text(encoding="utf-8")
        assert "a1b2c3d4:" in ratings_text
        assert "up: 1" in ratings_text

    def test_knowledge_rate_invalid_path_exits_cleanly(self, e2e_repo: Path) -> None:
        """knowledge rate should fail cleanly for configured paths outside the repo."""
        _write_knowledge_config(e2e_repo, path="../escape.md")

        result = _run(["knowledge", "rate", "a1b2c3d4", "up"], cwd=e2e_repo)

        assert result.returncode == 1
        assert (
            "Update .wade.yml so knowledge.path points to a file inside the current"
            in result.stdout
        )
        assert "must be inside project root" in result.stderr

"""Deterministic E2E contracts for project knowledge CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._support import _git, _run

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
        assert " added to " in result.stdout
        assert "KNOWLEDGE.md" in result.stdout
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
        # Ratings are now an append-only JSONL vote log (#358), not a counter YAML.
        ratings_text = (e2e_repo / "docs" / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        assert '"id": "a1b2c3d4"' in ratings_text
        assert '"dir": "up"' in ratings_text

    def test_detached_rate_stages_then_parent_handoff_preserves_vote(self, e2e_repo: Path) -> None:
        """A real detached child never dirties main and reaches its parent spool once."""
        from wade.models.config import KnowledgeConfig
        from wade.services.knowledge_service import (
            flush_staged_ratings,
            mark_throwaway_knowledge_session,
            read_ratings,
        )

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
        _git(["add", ".wade.yml", "docs/KNOWLEDGE.md"], cwd=e2e_repo)
        _git(["commit", "-m", "chore: seed detached knowledge"], cwd=e2e_repo)
        detached = e2e_repo.parent / "detached-plan"
        _git(["worktree", "add", "--detach", str(detached)], cwd=e2e_repo)
        # Stand in for the parent `wade plan` / `wade task deps` process: only a
        # marked worktree may stage votes, because only such a parent flushes them.
        mark_throwaway_knowledge_session(detached)

        result = _run(["knowledge", "rate", "a1b2c3d4", "up"], cwd=detached)

        assert result.returncode == 0
        staged = detached / ".wade" / "knowledge-ratings-staged.jsonl"
        assert staged.is_file()
        assert '"event_id"' in staged.read_text(encoding="utf-8")
        assert not (e2e_repo / "docs" / "KNOWLEDGE.ratings.jsonl").exists()
        assert not (detached / "docs" / "KNOWLEDGE.ratings.jsonl").exists()

        status = _run(["knowledge", "status"], cwd=detached)
        assert status.returncode == 0
        assert "1 detached-session rating vote(s) staged" in status.stdout

        handoff = flush_staged_ratings(
            detached,
            e2e_repo,
            KnowledgeConfig(enabled=True, path="docs/KNOWLEDGE.md"),
        )

        assert handoff.success
        assert handoff.appended_count == 1
        assert not staged.exists()
        ratings = read_ratings(e2e_repo / "docs" / "KNOWLEDGE.ratings.jsonl")
        assert ratings["a1b2c3d4"].up == 1

    def test_retained_worktree_votes_are_recovered_by_the_next_session(
        self, e2e_repo: Path
    ) -> None:
        """A handoff that failed once is picked back up from real `git worktree list`."""
        from wade.models.config import KnowledgeConfig
        from wade.services.knowledge_service import (
            flush_retained_staged_ratings,
            mark_throwaway_knowledge_session,
            read_ratings,
        )

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
        _git(["add", ".wade.yml", "docs/KNOWLEDGE.md"], cwd=e2e_repo)
        _git(["commit", "-m", "chore: seed retained knowledge"], cwd=e2e_repo)
        retained = e2e_repo.parent / "retained-plan"
        _git(["worktree", "add", "--detach", str(retained)], cwd=e2e_repo)
        mark_throwaway_knowledge_session(retained)

        assert _run(["knowledge", "rate", "a1b2c3d4", "up"], cwd=retained).returncode == 0
        staged = retained / ".wade" / "knowledge-ratings-staged.jsonl"
        assert staged.is_file()

        # The parent that should have flushed this exited without doing so. The
        # next plan/deps run sweeps it from the repo's own worktree list.
        config = KnowledgeConfig(enabled=True, path="docs/KNOWLEDGE.md")
        outcomes = flush_retained_staged_ratings(e2e_repo, config)

        assert [(o.success, o.appended_count) for o in outcomes] == [(True, 1)]
        assert outcomes[0].worktree == retained
        assert not staged.exists()
        assert read_ratings(e2e_repo / "docs" / "KNOWLEDGE.ratings.jsonl")["a1b2c3d4"].up == 1

        # Idempotent: a second sweep finds nothing left to hand off.
        assert flush_retained_staged_ratings(e2e_repo, config) == []

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

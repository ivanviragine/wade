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

    # Knowledge becomes enabled after the fixture's initial composition.
    # Explicitly refresh through the real service before exercising author writes.
    from wade.config.loader import load_config
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import compose_session

    compose_session(
        repo, repo, load_config(repo), kind=SessionKind.IMPLEMENTATION, task_id="42", refresh=True
    )


@pytest.fixture
def e2e_repo(e2e_repo: Path) -> Path:
    """Knowledge mutation contracts run in a real owning linked session."""
    from wade.config.loader import load_config
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import compose_session

    main = e2e_repo
    (main / ".gitignore").write_text("__pycache__/\n.wade/\n")
    _git(["add", ".gitignore"], cwd=main)
    _git(["commit", "-m", "chore: ignore fixture runtime"], cwd=main)
    root = main.parent / "owner-42"
    _git(["worktree", "add", "-b", "feat/42-knowledge", str(root)], cwd=main)
    compose_session(root, main, load_config(root), kind=SessionKind.IMPLEMENTATION, task_id="42")
    return root


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
        assert "| plan | kind: note | Issue #7" in knowledge_text
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
        assert '"id":"a1b2c3d4"' in ratings_text
        assert '"dir":"up"' in ratings_text

    def test_detached_feedback_survives_cleanup_without_main_spool(self, e2e_repo: Path) -> None:
        from wade.services.knowledge_proposals import proposals

        _write_knowledge_config(e2e_repo)
        path = e2e_repo / "docs/KNOWLEDGE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("## claim-a | 2026-10-14 | implementation\n\nDurable claim.\n\n---\n")
        _git(["add", ".wade.yml", "docs/KNOWLEDGE.md"], cwd=e2e_repo)
        _git(["commit", "-m", "docs: seed knowledge"], cwd=e2e_repo)
        detached = e2e_repo.parent / "detached-plan"
        _git(["worktree", "add", "--detach", str(detached)], cwd=e2e_repo)
        result = _run(["knowledge", "rate", "claim-a", "up"], cwd=detached)
        assert result.returncode == 0, result.stderr
        assert "pending owner-bound proposal" in result.stdout
        assert not (detached / "docs/KNOWLEDGE.ratings.jsonl").exists()
        assert not (e2e_repo / "docs/KNOWLEDGE.ratings.jsonl").exists()
        assert len(proposals(e2e_repo)) == 1
        status = _run(["knowledge", "status"], cwd=detached)
        assert "1 owner-bound knowledge proposal(s) pending" in status.stdout
        _git(["worktree", "remove", "--force", str(detached)], cwd=e2e_repo)
        assert len(proposals(e2e_repo)) == 1

    def test_legacy_retained_transport_is_never_automatically_adopted(self, e2e_repo: Path) -> None:
        from wade.models.config import KnowledgeConfig
        from wade.services.knowledge_service import (
            create_rating_event,
            flush_retained_staged_ratings,
            mark_throwaway_knowledge_session,
            stage_rating_event,
            staged_ratings_path,
        )

        _write_knowledge_config(e2e_repo)
        _git(["add", ".wade.yml"], cwd=e2e_repo)
        _git(["commit", "-m", "chore: knowledge configuration"], cwd=e2e_repo)
        retained = e2e_repo.parent / "retained-plan"
        _git(["worktree", "add", "--detach", str(retained)], cwd=e2e_repo)
        mark_throwaway_knowledge_session(retained)
        stage_rating_event(retained, create_rating_event("claim-a", "up"))
        original = staged_ratings_path(retained).read_bytes()
        config = KnowledgeConfig(enabled=True, path="docs/KNOWLEDGE.md")
        for _ in range(2):
            outcomes = flush_retained_staged_ratings(e2e_repo, config)
            assert [(o.success, o.appended_count) for o in outcomes] == [(False, 0)]
            assert staged_ratings_path(retained).read_bytes() == original
            assert not (e2e_repo / "docs/KNOWLEDGE.ratings.jsonl").exists()

    def test_committed_evidence_publication_and_squash_lifecycle(self, e2e_repo: Path) -> None:
        import json

        from wade.git.repo import main_checkout_root

        _write_knowledge_config(e2e_repo)
        path = e2e_repo / "docs/KNOWLEDGE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("## claim-a | 2026-10-14 | implementation\n\nApp prints hello.\n\n---\n")
        _git(["add", ".wade.yml", "docs/KNOWLEDGE.md"], cwd=e2e_repo)
        _git(["commit", "-m", "docs: source claim"], cwd=e2e_repo)
        check = _run(
            [
                "knowledge",
                "check",
                "claim-a",
                "current",
                "--file",
                "src/app.py#L1-L1",
                "--note",
                "Read committed app",
            ],
            cwd=e2e_repo,
        )
        assert check.returncode == 0, check.stderr
        receipt = json.loads(check.stdout)
        assert receipt["evidence"][0]["type"] == "git_file"
        _git(["add", "docs/KNOWLEDGE.ratings.jsonl"], cwd=e2e_repo)
        _git(["commit", "-m", "docs: record committed proof"], cwd=e2e_repo)
        _git(["branch", "integration-base"], cwd=e2e_repo)
        checked = _run(
            ["knowledge", "reconcile", "--json", "--base", "integration-base"], cwd=e2e_repo
        )
        assert checked.returncode == 0, checked.stderr
        entry = json.loads(checked.stdout)["entries"][0]
        assert entry["assessment"] == "checked-current" and entry["publication"] == "branch-local"
        main = main_checkout_root(e2e_repo)
        _git(["merge", "--squash", "feat/42-knowledge"], cwd=main)
        _git(["commit", "-m", "docs: squash knowledge into main"], cwd=main)
        published = _run(["knowledge", "reconcile", "--json"], cwd=e2e_repo)
        entry = json.loads(published.stdout)["entries"][0]
        assert entry["publication"] == "integrated"
        assert entry["assessments"][0]["publication"] == "integrated"
        assert entry["assessment"] == "checked-current"
        (e2e_repo / "src/app.py").write_text('print("changed")\n')
        _git(["commit", "-am", "fix: change app behavior"], cwd=e2e_repo)
        drift = _run(["knowledge", "reconcile", "--json"], cwd=e2e_repo)
        assert drift.returncode == 0
        assert json.loads(drift.stdout)["entries"][0]["assessment"] == "needs-recheck"

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

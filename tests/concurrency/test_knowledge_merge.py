"""Concurrency lane — #358: knowledge + ratings survive concurrent-branch merges.

Two parallel sessions each write to the shared knowledge/ratings files on their
own branch. When both branches merge into main the result must keep BOTH sides
with no conflict and no lost/double-counted data. #358 makes this hold by:

- committing a wade-managed ``.gitattributes`` that marks ``KNOWLEDGE.md`` and
  ``KNOWLEDGE.ratings.jsonl`` ``merge=union`` (keep both sides of a conflict);
- an append-only JSONL vote log (merging is concatenation → no vote lost);
- a byte-deterministic, idempotently-folded migration seed (two branches
  migrating the same legacy ``.ratings.yml`` do not double-count).

This lane replaces the pinned ``xfail`` guard that pre-#358 held the invariant.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.services.knowledge_service import read_ratings, record_rating

pytestmark = pytest.mark.concurrency

_GITATTRIBUTES = (
    "# wade:knowledge:start\n"
    "KNOWLEDGE.md merge=union\n"
    "KNOWLEDGE.ratings.jsonl merge=union\n"
    "# wade:knowledge:end\n"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_ok(cwd: Path, *args: str) -> None:
    result = _git(cwd, *args)
    assert result.returncode == 0, result.stderr


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-b", "main")
    _git_ok(repo, "config", "user.email", "t@t.com")
    _git_ok(repo, "config", "user.name", "t")
    # The wade-managed union block is a committed, tracked file (the server-side
    # backstop). Committing it in the base is what makes the two-branch merge clean.
    (repo / ".gitattributes").write_text(_GITATTRIBUTES, encoding="utf-8")
    return repo


def test_two_branch_knowledge_appends_both_survive_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    knowledge = repo / "KNOWLEDGE.md"
    knowledge.write_text("# Project Knowledge\n\n", encoding="utf-8")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: init knowledge + gitattributes")

    # Branch A appends entry A.
    _git_ok(repo, "checkout", "-b", "feat-a")
    knowledge.write_text(knowledge.read_text() + "## entry-a\nlearning A\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "test: add entry A")

    # Branch B (from main) appends entry B.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "checkout", "-b", "feat-b")
    knowledge.write_text(knowledge.read_text() + "## entry-b\nlearning B\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "test: add entry B")

    # Merge both into main.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "merge", "--no-edit", "feat-a")
    merge_b = _git(repo, "merge", "--no-edit", "feat-b")

    final = knowledge.read_text()
    assert merge_b.returncode == 0, "second merge conflicted"
    assert "## entry-a" in final
    assert "## entry-b" in final
    assert "<<<<<<<" not in final
    assert final.count("## entry-a") == 1
    assert final.count("## entry-b") == 1


def test_two_branch_rating_votes_both_survive_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ratings = repo / "KNOWLEDGE.ratings.jsonl"
    ratings.write_text("", encoding="utf-8")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: init ratings + gitattributes")

    # Branch A votes up on an entry.
    _git_ok(repo, "checkout", "-b", "vote-a")
    record_rating(ratings, "entry1", "up")
    _git_ok(repo, "commit", "-am", "test: vote up")

    # Branch B (from main) votes down on the same entry.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "checkout", "-b", "vote-b")
    record_rating(ratings, "entry1", "down")
    _git_ok(repo, "commit", "-am", "test: vote down")

    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "merge", "--no-edit", "vote-a")
    merge_b = _git(repo, "merge", "--no-edit", "vote-b")

    assert merge_b.returncode == 0, "vote-log merge conflicted"
    assert "<<<<<<<" not in ratings.read_text(encoding="utf-8")
    folded = read_ratings(ratings)
    # Both votes survive the merge — neither branch clobbered the other's counter.
    assert folded["entry1"].up == 1
    assert folded["entry1"].down == 1


def test_concurrent_tag_edit_and_append_union(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    knowledge = repo / "KNOWLEDGE.md"
    knowledge.write_text(
        "# Project Knowledge\n\n## abcd1234 | 2026-01-01 | plan\n\nbody\n\n---\n",
        encoding="utf-8",
    )
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: init knowledge + gitattributes")

    # Branch A rewrites the heading in place (a `tag add`-style edit).
    _git_ok(repo, "checkout", "-b", "tag-edit")
    edited = knowledge.read_text().replace(
        "## abcd1234 | 2026-01-01 | plan",
        "## abcd1234 | 2026-01-01 | plan | tags: git",
    )
    knowledge.write_text(edited, encoding="utf-8")
    _git_ok(repo, "commit", "-am", "test: tag add")

    # Branch B (from main) appends a new entry.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "checkout", "-b", "append")
    knowledge.write_text(
        knowledge.read_text() + "\n## ef567890 | 2026-01-02 | implementation\n\nnew\n\n---\n",
        encoding="utf-8",
    )
    _git_ok(repo, "commit", "-am", "test: append entry")

    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "merge", "--no-edit", "tag-edit")
    merge_b = _git(repo, "merge", "--no-edit", "append")

    final = knowledge.read_text()
    assert merge_b.returncode == 0, "tag-edit vs append merge conflicted"
    assert "<<<<<<<" not in final
    # Both changes land: the in-place tag edit and the appended entry.
    assert "tags: git" in final
    assert "## ef567890" in final
    assert final.count("## abcd1234") == 1


def test_two_branches_migrate_same_legacy_yaml_no_double_count(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Main carries a pre-#358 counter YAML (committed) and no .jsonl yet.
    legacy = repo / "KNOWLEDGE.ratings.yml"
    legacy.write_text("entry1:\n  up: 5\n  down: 1\n", encoding="utf-8")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: init legacy ratings + gitattributes")

    ratings = repo / "KNOWLEDGE.ratings.jsonl"

    # Branch A: a rate triggers the on-disk migration (seed + vote), then git rm's yml.
    _git_ok(repo, "checkout", "-b", "migrate-a")
    record_rating(ratings, "entry1", "up")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: migrate + vote up")

    # Branch B (from main): independently migrates the SAME legacy yml, then votes down.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "checkout", "-b", "migrate-b")
    record_rating(ratings, "entry1", "down")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: migrate + vote down")

    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "merge", "--no-edit", "migrate-a")
    merge_b = _git(repo, "merge", "--no-edit", "migrate-b")

    assert merge_b.returncode == 0, "migration merge conflicted"
    assert "<<<<<<<" not in ratings.read_text(encoding="utf-8")
    # Legacy yml is delete/delete → gone after both migrations merge.
    assert not legacy.exists()

    folded = read_ratings(ratings)
    # The seed (up:5, down:1) is counted ONCE despite both branches emitting it,
    # plus the two distinct votes (one up, one down) — NOT a doubled seed (up:10).
    assert folded["entry1"].up == 6  # 5 seed + 1 up vote
    assert folded["entry1"].down == 2  # 1 seed + 1 down vote

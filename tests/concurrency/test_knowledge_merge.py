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
    (repo / ".gitignore").write_text(".wade/\n")
    (repo / ".wade.yml").write_text(
        "version: 2\nproject:\n  main_branch: main\nknowledge:\n  enabled: true\n"
    )
    return repo


def _owned_branch(repo: Path, branch: str) -> Path:
    from wade.models.config import ProjectConfig
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import compose_session

    root = repo.parent / branch
    _git_ok(repo, "worktree", "add", "-b", branch, str(root), "main")
    compose_session(root, repo, ProjectConfig(), kind=SessionKind.IMPLEMENTATION, task_id="1")
    return root


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

    branch_a = _owned_branch(repo, "vote-a")
    branch_b = _owned_branch(repo, "vote-b")
    record_rating(branch_a / ratings.name, "entry1", "up")
    _git_ok(branch_a, "commit", "-am", "test: vote up")
    record_rating(branch_b / ratings.name, "entry1", "down")
    _git_ok(branch_b, "commit", "-am", "test: vote down")

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

    branch_a = _owned_branch(repo, "migrate-a")
    branch_b = _owned_branch(repo, "migrate-b")
    record_rating(branch_a / ratings.name, "entry1", "up")
    _git_ok(branch_a, "add", "-A")
    _git_ok(branch_a, "commit", "-m", "test: migrate + vote up")
    record_rating(branch_b / ratings.name, "entry1", "down")
    _git_ok(branch_b, "add", "-A")
    _git_ok(branch_b, "commit", "-m", "test: migrate + vote down")

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


def test_concurrent_assessment_union_is_independent_of_merge_order(tmp_path: Path) -> None:
    from wade.models.config import KnowledgeConfig
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = _init_repo(tmp_path)
    (repo / "source.py").write_text("answer = 42\n")
    (repo / "KNOWLEDGE.md").write_text(
        "## claim-a | 2026-10-14 | implementation\n\nAnswer is forty two.\n\n---\n"
    )
    (repo / "KNOWLEDGE.ratings.jsonl").write_text("")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: shared evidence baseline")
    left = _owned_branch(repo, "assessment-left")
    right = _owned_branch(repo, "assessment-right")
    config = KnowledgeConfig(enabled=True)
    check_entry(left, config, "claim-a", "current", files=("source.py",), note="left observation")
    check_entry(
        right, config, "claim-a", "contradicted", files=("source.py",), note="right observation"
    )
    for root in (left, right):
        _git_ok(root, "commit", "-am", "docs: independent assessment")
    _git_ok(repo, "branch", "merge-right", "main")
    _git_ok(repo, "checkout", "-b", "merge-left", "main")
    _git_ok(repo, "merge", "--no-edit", "assessment-left")
    _git_ok(repo, "merge", "--no-edit", "assessment-right")
    first = reconcile_knowledge(repo, config)
    _git_ok(repo, "checkout", "merge-right")
    _git_ok(repo, "merge", "--no-edit", "assessment-right")
    _git_ok(repo, "merge", "--no-edit", "assessment-left")
    second = reconcile_knowledge(repo, config)
    assert first.valid and second.valid
    assert first.entries[0].assessment == second.entries[0].assessment == "needs-recheck"
    assert [a.event for a in first.entries[0].assessments] == [
        a.event for a in second.entries[0].assessments
    ]


def test_concurrent_duplicate_normalization_converges_without_double_authority(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from wade.models.config import KnowledgeConfig
    from wade.services.knowledge_reconcile import reconcile_knowledge
    from wade.utils.knowledge_events import decode_history

    repo = _init_repo(tmp_path)
    (repo / "KNOWLEDGE.md").write_text(
        "## claim-a | 2026-10-14 | implementation\n\nSame claim.\n\n---\n\n"
        "## claim-z | 2026-10-14 | plan\n\nSame claim.\n\n---\n"
    )
    (repo / "KNOWLEDGE.ratings.jsonl").write_text("")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "test: duplicate baseline")
    left = _owned_branch(repo, "alias-left")
    right = _owned_branch(repo, "alias-right")
    config = KnowledgeConfig(enabled=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(
            pool.map(lambda root: reconcile_knowledge(root, config, apply=True), (left, right))
        )
    assert all(report.normalization_events_appended == 1 for report in reports)
    assert (left / "KNOWLEDGE.ratings.jsonl").read_bytes() == (
        right / "KNOWLEDGE.ratings.jsonl"
    ).read_bytes()
    for root in (left, right):
        _git_ok(root, "commit", "-am", "docs: normalize exact duplicate")
    _git_ok(repo, "merge", "--no-edit", "alias-left")
    _git_ok(repo, "merge", "--no-edit", "alias-right")
    history = decode_history((repo / "KNOWLEDGE.ratings.jsonl").read_text())
    assert not history.problems and len(history.events) == 1
    report = reconcile_knowledge(repo, config)
    assert all(entry.assessment == "unverified" for entry in report.entries)
    assert report.duplicate_groups[0].representative_id == "claim-a"

"""Concurrency lane — #358: two branches append knowledge, both survive a merge.

Two parallel sessions each append a knowledge entry (and a rating vote) to the
shared knowledge file on their own branch. When both branches merge into main
the result must contain BOTH entries with no duplicates and all votes preserved.

The merge fix for this belongs to #358, not #357. This test pins the invariant
now and is marked ``xfail(strict)`` so it turns into a hard pass — loudly — the
moment #358 lands, and never silently ossifies.

# TODO(#358): remove the xfail marker once the knowledge-merge fix lands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.concurrency


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_ok(cwd: Path, *args: str) -> None:
    result = _git(cwd, *args)
    assert result.returncode == 0, result.stderr


@pytest.mark.xfail(
    strict=True,
    reason="#358 knowledge-merge fix not in this PR — two concurrent appends "
    "currently conflict on a naive merge.",
)
def test_two_branch_knowledge_appends_both_survive_merge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(repo, "init", "-b", "main")
    _git_ok(repo, "config", "user.email", "t@t.com")
    _git_ok(repo, "config", "user.name", "t")

    knowledge = repo / "KNOWLEDGE.md"
    knowledge.write_text("# Project Knowledge\n\n")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "init knowledge")

    # Branch A appends entry A.
    _git_ok(repo, "checkout", "-b", "feat-a")
    knowledge.write_text(knowledge.read_text() + "## entry-a\nlearning A\n")
    _git_ok(repo, "commit", "-am", "add entry A")

    # Branch B (from main) appends entry B.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "checkout", "-b", "feat-b")
    knowledge.write_text(knowledge.read_text() + "## entry-b\nlearning B\n")
    _git_ok(repo, "commit", "-am", "add entry B")

    # Merge both into main.
    _git_ok(repo, "checkout", "main")
    _git_ok(repo, "merge", "--no-edit", "feat-a")
    merge_b = _git(repo, "merge", "--no-edit", "feat-b")

    final = knowledge.read_text()
    # The invariant #358 must guarantee: both entries present, no conflict
    # markers, no duplicates.
    assert merge_b.returncode == 0, "second merge conflicted"
    assert "## entry-a" in final
    assert "## entry-b" in final
    assert "<<<<<<<" not in final
    assert final.count("## entry-a") == 1
    assert final.count("## entry-b") == 1

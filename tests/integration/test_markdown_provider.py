"""Integration tests for the markdown task provider.

Exercises the full config → registry → provider path with a real ``.wade.yml``,
a real ``ISSUES.md``, and a real linked git worktree. Proves that the merge
flow's single ``provider.close_task(id)`` call lands in main's file even
when wade is invoked from a sibling worktree.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from wade.config.loader import parse_config_file
from wade.models.task import TaskState
from wade.providers.markdown import MarkdownIssueProvider, _resolve_main_worktree
from wade.providers.registry import get_provider


def _git_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ.get("PATH", ""),
    }


def _init_repo(path: Path) -> None:
    env = _git_env()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, env=env)
    (path / "README").write_text("seed")
    subprocess.run(["git", "add", "."], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, env=env)


@pytest.fixture
def md_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a main repo + linked worktree, both configured for the markdown provider.

    Returns ``(main_root, worktree_root)``. ``ISSUES.md`` is seeded with one
    open task #42 and committed to main so the worktree sees the same file.
    """
    # Clear the LRU on _resolve_main_worktree so different tmp_paths in the
    # same test session don't collide.
    _resolve_main_worktree.cache_clear()

    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)

    config = {
        "version": 2,
        "project": {"main_branch": "main"},
        "provider": {"name": "markdown", "settings": {"path": "ISSUES.md"}},
    }
    (main / ".wade.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (main / "ISSUES.md").write_text(
        "# Wade Issues\n\n## #42 Seed task\n\n<!-- wade\nstate: open\n-->\n\nSeed body.\n",
        encoding="utf-8",
    )
    env = _git_env()
    subprocess.run(["git", "add", "."], cwd=main, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=main, check=True, env=env)

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", str(wt), "-b", "feat/x"],
        cwd=main,
        check=True,
        env=env,
    )
    # Copy .wade.yml into the worktree (wade does this via the
    # copy_to_worktree hook; we replicate the steady state).
    (wt / ".wade.yml").write_text((main / ".wade.yml").read_text(), encoding="utf-8")

    return main, wt


class TestProviderViaRegistry:
    """Registry + config-loader hand off cleanly to MarkdownIssueProvider."""

    def test_load_config_returns_markdown_provider(self, md_repo) -> None:
        main, _ = md_repo
        config = parse_config_file(main / ".wade.yml")
        provider = get_provider(config)
        assert isinstance(provider, MarkdownIssueProvider)
        assert provider._path == (main / "ISSUES.md").resolve()


class TestMergeFlowEndsClosed:
    """Simulate what _merge_pr does post-merge: call provider.close_task."""

    def test_close_from_main_updates_main_file(self, md_repo) -> None:
        main, _ = md_repo
        config = parse_config_file(main / ".wade.yml")
        provider = get_provider(config)

        provider.close_task("42")

        # The actual file on disk now reflects closed state.
        text = (main / "ISSUES.md").read_text(encoding="utf-8")
        assert "state: closed" in text

        # And re-reading via a fresh provider sees the same.
        fresh = get_provider(parse_config_file(main / ".wade.yml"))
        assert fresh.read_task("42").state == TaskState.CLOSED

    def test_close_from_worktree_updates_main_file(self, md_repo) -> None:
        """The point of main-worktree resolution: close from a worktree,
        and main's ISSUES.md is what changes.
        """
        main, wt = md_repo
        config = parse_config_file(wt / ".wade.yml")
        provider = get_provider(config)

        # Resolved path must point at main's file, not the worktree's copy.
        assert provider._path == (main / "ISSUES.md").resolve()

        provider.close_task("42")

        # Main's file was updated.
        text = (main / "ISSUES.md").read_text(encoding="utf-8")
        assert "state: closed" in text

    def test_parallel_creates_from_two_worktrees_do_not_collide(self, md_repo) -> None:
        """Two providers (one rooted in main, one in worktree) both create
        tasks. Because they target the same file, IDs are distinct and the
        file is internally consistent — no duplicate sections.
        """
        main, wt = md_repo
        p_main = get_provider(parse_config_file(main / ".wade.yml"))
        p_wt = get_provider(parse_config_file(wt / ".wade.yml"))

        a = p_main.create_task("From main", "body A")
        b = p_wt.create_task("From worktree", "body B")

        assert a.id != b.id

        # Both providers see all three tasks (#42 seed + 2 new).
        for provider in (p_main, p_wt):
            ids = {t.id for t in provider.list_tasks(state=None)}
            assert {"42", a.id, b.id}.issubset(ids)


class TestFilePermissionsArePreserved:
    def test_close_does_not_strip_permissions(self, md_repo) -> None:
        main, _ = md_repo
        os.chmod(main / "ISSUES.md", 0o644)
        config = parse_config_file(main / ".wade.yml")
        provider = get_provider(config)

        provider.close_task("42")

        assert os.stat(main / "ISSUES.md").st_mode & 0o777 == 0o644

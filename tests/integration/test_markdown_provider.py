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
        """Two providers (one rooted in main, one in worktree) create tasks
        concurrently. The cross-process file lock must serialize the
        read-modify-write cycles so neither create is lost and both IDs
        are distinct.
        """
        from concurrent.futures import ThreadPoolExecutor

        main, wt = md_repo
        p_main = get_provider(parse_config_file(main / ".wade.yml"))
        p_wt = get_provider(parse_config_file(wt / ".wade.yml"))

        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(p_main.create_task, "From main", "body A")
            fb = ex.submit(p_wt.create_task, "From worktree", "body B")
            a = fa.result()
            b = fb.result()

        assert a.id != b.id

        # Both providers see all three tasks (#42 seed + 2 new), and the
        # underlying file has exactly one section per id (no clobbered writes).
        text = (main / "ISSUES.md").read_text(encoding="utf-8")
        assert text.count(f"## #{a.id} ") == 1
        assert text.count(f"## #{b.id} ") == 1
        for provider in (p_main, p_wt):
            ids = {t.id for t in provider.list_tasks(state=None)}
            assert {"42", a.id, b.id}.issubset(ids)

    def test_many_concurrent_creates_all_persist(self, md_repo) -> None:
        """Sanity: hammer the lock with N concurrent creates. Every task
        must survive the read-modify-write cycle (no lost updates) and
        every ID must be distinct.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        main, wt = md_repo
        providers = [
            get_provider(parse_config_file(main / ".wade.yml")),
            get_provider(parse_config_file(wt / ".wade.yml")),
        ]
        n = 12

        def _create(i: int):
            return providers[i % 2].create_task(f"Task {i}", f"body {i}")

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_create, i) for i in range(n)]
            tasks = [f.result() for f in as_completed(futures)]

        ids = [t.id for t in tasks]
        assert len(ids) == n
        assert len(set(ids)) == n  # All distinct.

        text = (main / "ISSUES.md").read_text(encoding="utf-8")
        for task_id in ids:
            assert text.count(f"## #{task_id} ") == 1


class TestFilePermissionsArePreserved:
    def test_close_does_not_strip_permissions(self, md_repo) -> None:
        main, _ = md_repo
        os.chmod(main / "ISSUES.md", 0o644)
        config = parse_config_file(main / ".wade.yml")
        provider = get_provider(config)

        provider.close_task("42")

        assert os.stat(main / "ISSUES.md").st_mode & 0o777 == 0o644


# Auto-commit/index isolation uses production providers and disposable real Git.
def _git_bytes(root: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, input=data
    ).stdout


def _auto_provider(main: Path, working: Path | None = None) -> MarkdownIssueProvider:
    _git_bytes(main, "config", "user.name", "Test User")
    _git_bytes(main, "config", "user.email", "test@example.com")
    _git_bytes(main, "config", "commit.gpgsign", "false")
    _git_bytes(main, "config", "core.hooksPath", str(main / ".git/hooks"))
    config = parse_config_file(main / ".wade.yml")
    config.provider.settings["auto_commit"] = "true"
    return MarkdownIssueProvider(config.provider, project_root=working or main)


def _stage_unrelated(main: Path) -> tuple[bytes, bytes]:
    path = main / "unrelated.txt"
    path.write_bytes(b"baseline\n")
    _git_bytes(main, "add", "unrelated.txt")
    _git_bytes(main, "commit", "-m", "unrelated baseline")
    path.write_bytes(b"staged user content\n")
    _git_bytes(main, "add", "unrelated.txt")
    path.write_bytes(b"unstaged user content\n")
    return _git_bytes(main, "show", ":unrelated.txt"), path.read_bytes()


def test_auto_close_commits_only_task_delta_from_linked_worktree(md_repo) -> None:
    main, working = md_repo
    provider = _auto_provider(main, working)
    staged, disk = _stage_unrelated(main)
    old_task = (main / "ISSUES.md").read_bytes()
    expected_task = old_task.replace(b"state: open", b"state: closed", 1)
    working_head = _git_bytes(working, "rev-parse", "HEAD")
    working_index = _git_bytes(working, "ls-files", "--stage", "-z")
    cached = _git_bytes(main, "diff", "--cached", "--binary")
    provider.close_task("42")
    assert (
        _git_bytes(main, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
        == b"ISSUES.md\0"
    )
    assert _git_bytes(main, "show", "HEAD:ISSUES.md") == expected_task
    assert _git_bytes(main, "show", ":unrelated.txt") == staged
    assert (main / "unrelated.txt").read_bytes() == disk
    assert _git_bytes(main, "diff", "--cached", "--binary") == cached
    assert _git_bytes(working, "rev-parse", "HEAD") == working_head
    assert _git_bytes(working, "ls-files", "--stage", "-z") == working_index
    assert (working / "ISSUES.md").read_bytes() == old_task


@pytest.mark.parametrize("dirty", ["staged", "unstaged", "partial", "assume", "skip"])
def test_dirty_task_file_closes_without_consuming_staging(md_repo, dirty: str) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    _stage_unrelated(main)
    task = main / "ISSUES.md"
    task.write_bytes(task.read_bytes() + b"\npreexisting staged body\n")
    if dirty in {"staged", "partial"}:
        _git_bytes(main, "add", "ISSUES.md")
    if dirty == "partial":
        task.write_bytes(task.read_bytes() + b"preexisting unstaged body\n")
    if dirty in {"assume", "skip"}:
        _git_bytes(
            main,
            "update-index",
            "--assume-unchanged" if dirty == "assume" else "--skip-worktree",
            "ISSUES.md",
        )
    before = task.read_bytes()
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    flags = _git_bytes(main, "ls-files", "-v", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert task.read_bytes() == before.replace(b"state: open", b"state: closed", 1)
    assert _git_bytes(main, "rev-parse", "HEAD") == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    assert _git_bytes(main, "ls-files", "-v", "-z") == flags


def test_rejected_auto_commit_preserves_index_and_disk_closure(md_repo) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    _stage_unrelated(main)
    hook = main / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert _git_bytes(main, "rev-parse", "HEAD") == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == index


@pytest.mark.parametrize("phase", ["pre-commit", "prepare-commit-msg", "commit-msg"])
def test_hook_cannot_expand_auto_commit_candidate(md_repo, phase: str) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    _stage_unrelated(main)
    hook = main / ".git/hooks" / phase
    hook.write_text("#!/bin/sh\ngit add -- unrelated.txt\n")
    hook.chmod(0o755)
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    disk = (main / "unrelated.txt").read_bytes()
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    after_head = _git_bytes(main, "rev-parse", "HEAD")
    if after_head != head:
        # Some Git versions snapshot the tree before preparation/message hooks.
        # Such a hook may run without contaminating the final candidate.
        assert phase != "pre-commit"
        assert (
            _git_bytes(main, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
            == b"ISSUES.md\n"
        )
        assert _git_bytes(main, "show", "HEAD:ISSUES.md") == (main / "ISSUES.md").read_bytes()
        expected = index.replace(
            _git_bytes(main, "rev-parse", f"{head.decode().strip()}:ISSUES.md").strip(),
            _git_bytes(main, "rev-parse", "HEAD:ISSUES.md").strip(),
        )
    else:
        expected = index
    assert _git_bytes(main, "ls-files", "--stage", "-z") == expected
    assert (main / "unrelated.txt").read_bytes() == disk


def test_task_service_closure_preserves_noncanonical_bytes(md_repo) -> None:
    from wade.services.task_service import close_task

    main, _ = md_repo
    provider = _auto_provider(main)
    original = (
        b"# untouched prelude  \r\n\r\n## #42:  Seed task  \r\n\r\n"
        b"<!-- wade\r\nowner:   user\r\nstate: open\r\nlabels: one,in-progress,  two\r\n-->\r\n"
        b"\r\nBody with spaces  \r\n\r\n## #99 - Other task\r\n\r\nUntouched body  "
    )
    (main / "ISSUES.md").write_bytes(original)
    _git_bytes(main, "add", "ISSUES.md")
    _git_bytes(main, "commit", "-m", "noncanonical tasks")
    _stage_unrelated(main)
    expected = original.replace(b"state: open", b"state: closed", 1).replace(
        b"labels: one,in-progress,  two", b"labels: one,  two", 1
    )
    assert close_task("42", config=parse_config_file(main / ".wade.yml"), provider=provider)
    assert (main / "ISSUES.md").read_bytes() == expected
    assert _git_bytes(main, "show", "HEAD:ISSUES.md") == expected
    assert _git_bytes(main, "show", ":unrelated.txt") == b"staged user content\n"


@pytest.mark.parametrize("path", ["nested tasks/ISSUES[1]*?.md", "-tasks.md"])
def test_auto_commit_uses_literal_custom_task_path(md_repo, path: str) -> None:
    from wade.models.config import ProviderConfig

    main, _ = md_repo
    _auto_provider(main)
    task = main / path
    task.parent.mkdir(exist_ok=True)
    task.write_bytes((main / "ISSUES.md").read_bytes())
    _git_bytes(main, "--literal-pathspecs", "add", "--", path)
    _git_bytes(main, "commit", "-m", "custom task path")
    index, disk = _stage_unrelated(main)
    provider = MarkdownIssueProvider(
        ProviderConfig(name="markdown", settings={"path": path, "auto_commit": "true"}),
        project_root=main,
    )
    provider.close_task("42")
    assert (
        _git_bytes(main, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
        == os.fsencode(path) + b"\0"
    )
    assert _git_bytes(main, "show", ":unrelated.txt") == index
    assert (main / "unrelated.txt").read_bytes() == disk


@pytest.mark.parametrize("state", ["untracked", "index-lock", "merge", "unborn", "no-git"])
def test_unprovable_auto_commit_closes_without_staging(md_repo, state: str) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    if state == "untracked":
        _git_bytes(main, "rm", "--cached", "ISSUES.md")
        _git_bytes(main, "commit", "-m", "untrack task file")
    elif state == "unborn":
        _git_bytes(main, "symbolic-ref", "HEAD", "refs/heads/unborn")
    elif state == "no-git":
        import shutil

        shutil.rmtree(main / ".git")
    else:
        (main / ".git" / ("index.lock" if state == "index-lock" else "MERGE_HEAD")).write_bytes(
            b"held"
        )
    head = None if state in {"unborn", "no-git"} else _git_bytes(main, "rev-parse", "HEAD")
    index = None if state == "no-git" else _git_bytes(main, "ls-files", "--stage", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    if head is not None:
        assert _git_bytes(main, "rev-parse", "HEAD") == head
    if index is not None:
        assert _git_bytes(main, "ls-files", "--stage", "-z") == index


@pytest.mark.parametrize("hooks_setting", ["relative", "absolute"])
def test_auto_commit_forwards_existing_hooks_and_reference_input(
    md_repo, hooks_setting: str
) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    hooks = main / "custom hooks"
    hooks.mkdir()
    _git_bytes(
        main,
        "config",
        "core.hooksPath",
        "custom hooks" if hooks_setting == "relative" else str(hooks),
    )
    log = main / "hook-events"
    hook = hooks / "reference-transaction"
    hook.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> hook-events\ncat >> hook-events\n')
    hook.chmod(0o755)
    pre = hooks / "pre-commit"
    pre.write_text('#!/bin/sh\nprintf "pre-commit\\n" >> hook-events\n')
    pre.chmod(0o755)
    original = _git_bytes(main, "rev-parse", "HEAD").strip()
    provider.close_task("42")
    new = _git_bytes(main, "rev-parse", "HEAD").strip()
    assert new != original
    events = log.read_bytes()
    transaction = original + b" " + new + b" refs/heads/main\n"
    assert events.startswith(b"pre-commit\nprepared\n")
    assert events.count(transaction) == 2
    assert b"committed\n" in events
    assert _git_bytes(main, "config", "core.hooksPath").strip() == os.fsencode(
        "custom hooks" if hooks_setting == "relative" else str(hooks)
    )
    assert pre.read_text().endswith(">> hook-events\n")


@pytest.mark.parametrize("mutation", ["veto-reference", "task-content", "task-mode"])
def test_final_guard_preserves_index_on_hook_rejection(md_repo, mutation: str) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    _stage_unrelated(main)
    hook = (
        main
        / ".git/hooks"
        / ("reference-transaction" if mutation == "veto-reference" else "pre-commit")
    )
    scripts = {
        "veto-reference": '#!/bin/sh\n[ "$1" != prepared ]\n',
        "task-content": '#!/bin/sh\nprintf "hook body\\n" >> ISSUES.md\ngit add -- ISSUES.md\n',
        "task-mode": "#!/bin/sh\ngit update-index --chmod=+x -- ISSUES.md\n",
    }
    hook.write_text(scripts[mutation])
    hook.chmod(0o755)
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert _git_bytes(main, "rev-parse", "HEAD") == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    assert not (main / ".git/index.lock").exists()


def test_staged_additions_and_deletions_survive_close(md_repo) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    (main / "delete-me").write_bytes(b"old\n")
    _git_bytes(main, "add", "delete-me")
    _git_bytes(main, "commit", "-m", "deletion baseline")
    _git_bytes(main, "rm", "delete-me")
    (main / "new-file").write_bytes(b"staged addition\n")
    _git_bytes(main, "add", "new-file")
    before = _git_bytes(main, "diff", "--cached", "--binary")
    provider.close_task("42")
    assert _git_bytes(main, "diff", "--cached", "--binary") == before
    assert (
        _git_bytes(main, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
        == b"ISSUES.md\n"
    )


def test_provider_lock_covers_commit_attempt(md_repo, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, current_thread

    import wade.providers.markdown as module

    main, wt = md_repo
    first = _auto_provider(main)
    second = _auto_provider(main, wt)
    with (main / "ISSUES.md").open("a") as stream:
        stream.write("\n## #43 Other\n\n<!-- wade\nstate: open\n-->\n\nOther body.\n")
    _git_bytes(main, "add", "ISSUES.md")
    _git_bytes(main, "commit", "-m", "second task")
    _stage_unrelated(main)
    paused, release, second_attempt = Event(), Event(), Event()
    actual_commit, actual_lock, actual_write = (
        module.commit_close,
        module.file_lock,
        module._atomic_write,
    )

    def commit(eligibility, closed, task_id):
        if task_id == "42":
            paused.set()
            assert release.wait(10)
        return actual_commit(eligibility, closed, task_id)

    @contextmanager
    def lock(path):
        if current_thread().name.endswith("_1"):
            second_attempt.set()
        with actual_lock(path):
            yield

    def write(path, content, **kwargs):
        if current_thread().name.endswith("_1"):
            assert release.is_set(), "second writer entered before first commit finished"
        return actual_write(path, content, **kwargs)

    monkeypatch.setattr(module, "commit_close", commit)
    monkeypatch.setattr(module, "file_lock", lock)
    monkeypatch.setattr(module, "_atomic_write", write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.close_task, "42")
        try:
            assert paused.wait(10)
            b = pool.submit(second.close_task, "43")
            assert second_attempt.wait(10)
            assert first.read_task("43").state == TaskState.OPEN
        finally:
            release.set()
        assert a.result(timeout=10).state == TaskState.CLOSED
        assert b.result(timeout=10).state == TaskState.CLOSED
    assert first.read_task("42").state == first.read_task("43").state == TaskState.CLOSED
    assert _git_bytes(main, "log", "-2", "--format=%s") == b"chore: close #43\nchore: close #42\n"
    assert _git_bytes(main, "show", ":unrelated.txt") == b"staged user content\n"


@pytest.mark.parametrize("outcome", ["success", "rejection", "exception"])
def test_hook_proxy_cleanup_on_every_commit_outcome(md_repo, monkeypatch, outcome: str) -> None:
    import wade.utils.git_close_commit as helper

    main, _ = md_repo
    provider = _auto_provider(main)
    _stage_unrelated(main)
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    created = []
    actual_directory, actual_git = helper.tempfile.TemporaryDirectory, helper._git

    def directory(*args, **kwargs):
        result = actual_directory(*args, **kwargs)
        created.append(Path(result.name))
        return result

    def git(root, *args, **kwargs):
        if outcome == "exception" and "commit" in args:
            raise OSError("forced commit process failure")
        return actual_git(root, *args, **kwargs)

    monkeypatch.setattr(helper.tempfile, "TemporaryDirectory", directory)
    monkeypatch.setattr(helper, "_git", git)
    if outcome == "rejection":
        hook = main / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert len(created) == 1
    assert all(not path.exists() for path in created)
    assert not (main / ".git/index.lock").exists()
    if outcome != "success":
        assert _git_bytes(main, "rev-parse", "HEAD") == head
        assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    # A later writer can acquire the provider lock after every outcome.
    assert provider.close_task("42").state == TaskState.CLOSED


def test_index_change_after_eligibility_skips_commit_without_restoring_user_work(
    md_repo, monkeypatch
) -> None:
    import wade.providers.markdown as module

    main, _ = md_repo
    provider = _auto_provider(main)
    head = _git_bytes(main, "rev-parse", "HEAD")
    actual_write = module._atomic_write
    new_index = []

    def write(path, content, **kwargs):
        actual_write(path, content, **kwargs)
        # Deterministic intervening Git writer: stage an independent task blob.
        blob = (
            _git_bytes(main, "hash-object", "-w", "--stdin", data=b"concurrent staged task data\n")
            .strip()
            .decode()
        )
        _git_bytes(main, "update-index", "--cacheinfo", "100644", blob, "ISSUES.md")
        new_index.append(_git_bytes(main, "ls-files", "--stage", "-z"))

    monkeypatch.setattr(module, "_atomic_write", write)
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert _git_bytes(main, "rev-parse", "HEAD") == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == new_index[0]
    assert _git_bytes(main, "show", ":ISSUES.md") == b"concurrent staged task data\n"


def test_ignored_submodule_cannot_expand_close_commit(md_repo, tmp_path: Path) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    source = tmp_path / "submodule-source"
    source.mkdir()
    _init_repo(source)
    _git_bytes(main, "-c", "protocol.file.allow=always", "submodule", "add", str(source), "sub")
    _git_bytes(main, "commit", "-am", "track submodule")
    sub = main / "sub"
    _git_bytes(sub, "config", "user.name", "test")
    _git_bytes(sub, "config", "user.email", "test@example.com")
    (sub / "README").write_text("new submodule commit")
    _git_bytes(sub, "commit", "-am", "advance submodule")
    _git_bytes(main, "config", "submodule.sub.ignore", "all")
    hook = main / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\ngit add -- sub\n")
    hook.chmod(0o755)
    head = _git_bytes(main, "rev-parse", "HEAD")
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert _git_bytes(main, "rev-parse", "HEAD") == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    assert (sub / "README").read_text() == "new submodule commit"


def test_replacement_objects_skip_auto_commit_without_consuming_index(md_repo) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    (main / "user.txt").write_bytes(b"original user bytes\n")
    _git_bytes(main, "add", "user.txt")
    _git_bytes(main, "commit", "-m", "user baseline")
    head = _git_bytes(main, "rev-parse", "HEAD").decode().strip()
    (main / "user.txt").write_bytes(b"replacement user bytes\n")
    _git_bytes(main, "add", "user.txt")
    tree = _git_bytes(main, "write-tree").decode().strip()
    replacement = _git_bytes(main, "commit-tree", tree, data=b"replacement\n").decode().strip()
    _git_bytes(main, "replace", head, replacement)
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    provider.close_task("42")
    assert provider.read_task("42").state == TaskState.CLOSED
    assert _git_bytes(main, "rev-parse", "HEAD").decode().strip() == head
    assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    assert (
        _git_bytes(main, "--no-replace-objects", "show", "HEAD:user.txt")
        == b"original user bytes\n"
    )
    assert (main / "user.txt").read_bytes() == b"replacement user bytes\n"


@pytest.mark.parametrize("auto_commit", [False, True])
@pytest.mark.parametrize("metadata", [False, True])
def test_cr_only_close_preserves_raw_bytes_and_read_semantics(
    md_repo, auto_commit: bool, metadata: bool
) -> None:
    main, _ = md_repo
    provider = _auto_provider(main)
    provider._auto_commit = auto_commit
    block = b"<!-- wade\rstate: open\rlabels: one,in-progress,two\r-->\r\r" if metadata else b""
    original = (
        b"# untouched\r\r## #42 Seed task\r\r"
        + block
        + b"Body bytes  \r\r## #99 Other task\r\rUnchanged other body\r"
    )
    task = main / "ISSUES.md"
    task.write_bytes(original)
    _git_bytes(main, "add", "ISSUES.md")
    _git_bytes(main, "commit", "-m", "CR-only tasks")
    _stage_unrelated(main)
    index = _git_bytes(main, "ls-files", "--stage", "-z")
    before_other = original[original.index(b"## #99") :]
    result = provider.close_task("42")
    assert result.state == TaskState.CLOSED
    assert provider.read_task("42").state == TaskState.CLOSED
    assert "in-progress" not in {label.name for label in provider.read_task("42").labels}
    closed = task.read_bytes()
    assert b"\n" not in closed
    assert closed[closed.index(b"## #99") :] == before_other
    assert b"Body bytes  \r\r" in closed
    if metadata:
        assert closed == original.replace(b"state: open", b"state: closed", 1).replace(
            b"labels: one,in-progress,two", b"labels: one,two", 1
        )
    if auto_commit:
        assert _git_bytes(main, "show", "HEAD:ISSUES.md") == closed
        assert (
            _git_bytes(main, "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD")
            == b"ISSUES.md\n"
        )
    else:
        assert _git_bytes(main, "ls-files", "--stage", "-z") == index
    assert _git_bytes(main, "show", ":unrelated.txt") == b"staged user content\n"

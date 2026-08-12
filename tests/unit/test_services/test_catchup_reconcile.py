"""Real-git integration tests for the catchup migration reconcile (#407 Part B).

Reproduces the #386 knowledge-store-migration straddle: a feature branch scaffolded
from a *pre-migration* base, where ``origin/main`` has since advanced to track
``.gitattributes`` / ``KNOWLEDGE.*`` and rewrite ``.gitignore``. Startup ``catchup``
must reconcile the wade-owned collisions and ADVANCE — instead of silently aborting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git import branch as git_branch
from wade.git import repo as git_repo
from wade.models.config import KnowledgeConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service.bootstrap import write_worktree_gitignore
from wade.services.implementation_service.sync import (
    _backup_untracked_files,
    _files_blocking_merge,
    _is_discardable_untracked,
    _pointer_diff_is_only_block,
    _reconcile_skip_worktree_collision,
    catchup,
    sync,
)
from wade.skills.installer import ensure_knowledge_merge_attributes
from wade.skills.pointer import MARKER_END, MARKER_START

_SYNC = "wade.services.implementation_service.sync"


def _cfg() -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(main_branch="main"),
        knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "T")
    # Isolate from a developer's global commit.gpgsign/tag.gpgsign so real-git commits
    # here never fail for signing reasons unrelated to the code under test.
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "tag.gpgsign", "false")


def _build_straddle(
    tmp_path: Path,
    *,
    ignore_knowledge: bool = True,
    extra_migration_untracked: str | None = None,
) -> Path:
    """Build origin + a feature worktree straddling the knowledge-store migration.

    Returns the worktree path (checked out on ``feat/1-x`` at the pre-migration base,
    with an untracked ``.gitattributes`` and a ``--skip-worktree`` ``.gitignore`` block —
    the bootstrap state), with ``origin/main`` advanced past the migration.
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")

    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(origin))

    # --- Pre-migration base on main ---
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    ignore_lines = ["*.pyc\n"]
    if ignore_knowledge:
        ignore_lines = ["KNOWLEDGE.md\n", "KNOWLEDGE.ratings.jsonl\n", *ignore_lines]
    (repo / ".gitignore").write_text("".join(ignore_lines), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pre-migration base")
    _git(repo, "push", "origin", "main")

    # Feature branch from the pre-migration base.
    _git(repo, "branch", "feat/1-x")

    # --- Migration commit on main: track .gitattributes + KNOWLEDGE.*, rewrite .gitignore ---
    cfg = _cfg()
    ensure_knowledge_merge_attributes(repo, cfg)  # writes .gitattributes union block
    (repo / "KNOWLEDGE.md").write_text("# Project Knowledge\n\nmain content\n", encoding="utf-8")
    (repo / "KNOWLEDGE.ratings.jsonl").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")  # dropped KNOWLEDGE lines
    if extra_migration_untracked is not None:
        target = repo / extra_migration_untracked
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("main-owned file\n", encoding="utf-8")
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-m", "feat: knowledge-store migration (#386)")
    _git(repo, "push", "origin", "main")

    # --- Feature worktree at the pre-migration base ---
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt), "feat/1-x")

    # Simulate bootstrap: untracked .gitattributes union block + skip-worktree'd
    # .gitignore worktree block.
    ensure_knowledge_merge_attributes(wt, cfg)
    write_worktree_gitignore(wt)
    if git_repo.is_file_tracked(wt, ".gitignore"):
        git_repo.skip_worktree_file(wt, ".gitignore")

    return wt


class TestMigrationStraddleAdvances:
    def test_catchup_reconciles_and_advances(self, tmp_path: Path) -> None:
        wt = _build_straddle(tmp_path)
        # Sanity: the branch really is behind before catchup.
        _git(wt, "fetch", "origin")
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 1

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(project_root=wt)

        assert result.success is True, result.events
        assert result.commits_merged >= 1
        # The branch is now caught up onto current main across the migration.
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 0
        # Main's now-tracked KNOWLEDGE.md was merged in.
        assert (wt / "KNOWLEDGE.md").is_file()
        assert git_repo.is_file_tracked(wt, "KNOWLEDGE.md")
        # The wade-managed merge=union block survives on .gitattributes.
        gitattrs = (wt / ".gitattributes").read_text(encoding="utf-8")
        assert "merge=union" in gitattrs
        assert "wade:knowledge:start" in gitattrs
        # The --skip-worktree .gitignore worktree block was re-injected after the merge.
        gitignore = (wt / ".gitignore").read_text(encoding="utf-8")
        assert "wade:worktree:start" in gitignore
        # ...and main's .gitignore change (dropped KNOWLEDGE lines) actually landed.
        assert "KNOWLEDGE.md" not in gitignore.split("# wade:worktree:start")[0]


class TestCatchupDryRunPreservesFiles:
    def test_dry_run_does_not_delete_reconcile_targets(self, tmp_path: Path) -> None:
        """--dry-run is documented as 'Preview without merging' — it must not delete the
        untracked migration files it would otherwise reconcile (#407 review)."""
        wt = _build_straddle(tmp_path)
        before = (wt / ".gitattributes").read_text(encoding="utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(dry_run=True, project_root=wt)

        assert result.success is True, result.events
        assert (wt / ".gitattributes").read_text(encoding="utf-8") == before
        # Nothing was actually merged.
        _git(wt, "fetch", "origin")
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 1


class TestCatchupRestoresOnIncompleteMerge:
    def test_conflict_unrelated_to_reconcile_restores_deleted_files(self, tmp_path: Path) -> None:
        """If the reconcile deletes the migration-owned untracked files but the merge then
        aborts for an unrelated reason (a real conflict elsewhere), the deleted files must
        come back rather than vanish with no merge to show for it (#407 review)."""
        wt = _build_straddle(tmp_path)
        repo = tmp_path / "repo"

        # A further commit on main, conflicting with a further commit on the feature
        # branch, on a file untouched by the migration itself.
        (repo / "README.md").write_text("main change\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "main: touch README")
        _git(repo, "push", "origin", "main")

        (wt / "README.md").write_text("feature change\n", encoding="utf-8")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", "feat: touch README")

        before = (wt / ".gitattributes").read_text(encoding="utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(project_root=wt)

        assert result.success is False
        assert "README.md" in result.conflicts
        # The reconciled untracked file must be restored, not lost, since the merge
        # that was meant to replace it never completed.
        assert (wt / ".gitattributes").read_text(encoding="utf-8") == before


class TestNonWadeUntrackedAborts:
    def test_collision_with_non_wade_path_aborts_and_preserves(self, tmp_path: Path) -> None:
        from wade.models.session import SyncEventType

        wt = _build_straddle(tmp_path, extra_migration_untracked="NOTES.md")
        # Create the colliding NON-wade untracked file locally (main also adds it). A
        # root-level file is reported per-path by ``git status`` (a fully-untracked new
        # dir would collapse to ``?? dir/`` and never be probed).
        (wt / "NOTES.md").write_text("local untracked — must NOT be deleted\n", "utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(project_root=wt)

        assert result.success is False
        untracked_events = [e for e in result.events if e.event == SyncEventType.UNTRACKED_CONFLICT]
        assert len(untracked_events) == 1
        # Neither the non-wade file nor the wade-owned .gitattributes was deleted.
        assert (wt / "NOTES.md").read_text(encoding="utf-8").startswith("local untracked")
        assert (wt / ".gitattributes").is_file()
        # Still behind — catchup did NOT advance (Part A surfaces this loudly upstream).
        _git(wt, "fetch", "origin")
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 1


class TestSyncNeverBlindDeletes:
    def test_sync_preserves_local_untracked_knowledge(self, tmp_path: Path) -> None:
        """Mid-session sync() must NOT delete an untracked KNOWLEDGE.md that may hold
        real agent-authored entries — the catchup delete-reconcile must not leak in."""
        from wade.models.session import SyncEventType

        # Pre-migration base does NOT ignore KNOWLEDGE.md, so a local untracked copy
        # collides with main's now-tracked version.
        wt = _build_straddle(tmp_path, ignore_knowledge=False)
        real = "# Project Knowledge\n\n## entry\nAGENT-AUTHORED, uncommitted — do not lose.\n"
        (wt / "KNOWLEDGE.md").write_text(real, encoding="utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = sync(project_root=wt)

        assert result.success is False
        assert any(e.event == SyncEventType.UNTRACKED_CONFLICT for e in result.events)
        # The agent's uncommitted knowledge is intact — sync() never blind-deletes it.
        assert (wt / "KNOWLEDGE.md").read_text(encoding="utf-8") == real


class TestCatchupPreservesResumedKnowledge:
    def test_catchup_aborts_when_untracked_knowledge_has_real_content(self, tmp_path: Path) -> None:
        """catchup() runs on EVERY `wade implement` (incl. resume). An untracked
        KNOWLEDGE.md holding real, uncommitted agent entries must NOT be blind-deleted by
        the migration reconcile — the content-gate aborts and preserves it (#407)."""
        from wade.models.session import SyncEventType

        wt = _build_straddle(tmp_path, ignore_knowledge=False)
        # Agent-authored, uncommitted entries not present in main's incoming KNOWLEDGE.md.
        real = "# Project Knowledge\n\n## resumed entry\nUNIQUE agent content, uncommitted.\n"
        (wt / "KNOWLEDGE.md").write_text(real, encoding="utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(project_root=wt)

        assert result.success is False
        assert any(e.event == SyncEventType.UNTRACKED_CONFLICT for e in result.events)
        # Preserved (not deleted) even though it is a "wade-owned" path.
        assert (wt / "KNOWLEDGE.md").read_text(encoding="utf-8") == real
        # And catchup did not advance — Part A surfaces the staleness loudly upstream.
        _git(wt, "fetch", "origin")
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 1

    def test_catchup_advances_when_untracked_knowledge_is_subset_of_main(
        self, tmp_path: Path
    ) -> None:
        """A local untracked KNOWLEDGE.md whose content main already contains adds nothing
        — it is discardable, so catchup still advances across the migration."""
        wt = _build_straddle(tmp_path, ignore_knowledge=False)
        # main's KNOWLEDGE.md is "# Project Knowledge\n\nmain content\n" — a strict subset.
        (wt / "KNOWLEDGE.md").write_text("# Project Knowledge\n", encoding="utf-8")

        with patch(f"{_SYNC}.load_config", return_value=_cfg()):
            result = catchup(project_root=wt)

        assert result.success is True, result.events
        _git(wt, "fetch", "origin")
        assert git_branch.commits_ahead(wt, "origin/main", "feat/1-x") == 0


class TestIsDiscardableUntracked:
    def _commit_main_knowledge(self, tmp_path: Path, content: str) -> Path:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add knowledge")
        return repo

    def test_gitattributes_missing_locally_is_discardable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        assert _is_discardable_untracked(repo, ".gitattributes", "HEAD") is True

    def test_gitattributes_with_only_wade_block_is_discardable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        ensure_knowledge_merge_attributes(repo, _cfg())  # writes ONLY the wade block
        assert _is_discardable_untracked(repo, ".gitattributes", "HEAD") is True

    def test_gitattributes_with_unrelated_local_rule_not_discardable(self, tmp_path: Path) -> None:
        """A user-authored rule alongside wade's marker must NOT be silently discarded when
        the incoming base doesn't have it (#407 review)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _git(repo, "commit", "--allow-empty", "-m", "init")  # HEAD has no .gitattributes
        ensure_knowledge_merge_attributes(repo, _cfg())
        gitattrs = repo / ".gitattributes"
        gitattrs.write_text(
            "*.bin filter=lfs\n" + gitattrs.read_text(encoding="utf-8"), encoding="utf-8"
        )
        assert _is_discardable_untracked(repo, ".gitattributes", "HEAD") is False

    def test_gitattributes_with_unrelated_rule_also_upstream_is_discardable(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.bin filter=lfs\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add gitattributes")
        ensure_knowledge_merge_attributes(repo, _cfg())  # appends the wade block locally
        assert _is_discardable_untracked(repo, ".gitattributes", "HEAD") is True

    def test_empty_local_is_discardable(self, tmp_path: Path) -> None:
        repo = self._commit_main_knowledge(tmp_path, "# K\n\nmain\n")
        (repo / "KNOWLEDGE.md").write_text("   \n", encoding="utf-8")
        assert _is_discardable_untracked(repo, "KNOWLEDGE.md", "HEAD") is True

    def test_subset_of_main_is_discardable(self, tmp_path: Path) -> None:
        repo = self._commit_main_knowledge(tmp_path, "# K\n\nline-a\nline-b\n")
        (repo / "KNOWLEDGE.md").write_text("# K\nline-a\n", encoding="utf-8")  # all lines in main
        assert _is_discardable_untracked(repo, "KNOWLEDGE.md", "HEAD") is True

    def test_unique_local_line_not_discardable(self, tmp_path: Path) -> None:
        repo = self._commit_main_knowledge(tmp_path, "# K\n\nline-a\n")
        (repo / "KNOWLEDGE.md").write_text("# K\nline-a\nLOCAL-ONLY\n", encoding="utf-8")
        assert _is_discardable_untracked(repo, "KNOWLEDGE.md", "HEAD") is False

    def test_duplicate_local_line_not_discardable(self, tmp_path: Path) -> None:
        # #408: an append-only log (e.g. .ratings.jsonl) can carry the SAME record twice
        # while the incoming base has it once. Set semantics would call this discardable and
        # silently drop the duplicate vote; multiset semantics correctly refuse the delete.
        repo = self._commit_main_knowledge(tmp_path, "# K\n\nvote-x\n")
        (repo / "KNOWLEDGE.md").write_text("# K\nvote-x\nvote-x\n", encoding="utf-8")
        assert _is_discardable_untracked(repo, "KNOWLEDGE.md", "HEAD") is False

    def test_missing_incoming_not_discardable(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "KNOWLEDGE.md").write_text("local content\n", encoding="utf-8")
        # No KNOWLEDGE.md at HEAD → no incoming fallback → refuse to delete.
        assert _is_discardable_untracked(repo, "KNOWLEDGE.md", "HEAD") is False


class TestBackupUntrackedFiles:
    def test_returns_none_when_any_path_cannot_be_read(self, tmp_path: Path) -> None:
        # #408: a partial backup would let the reconcile delete an un-backed-up file with no
        # way to restore it. Any unreadable path aborts the WHOLE backup (returns None) so the
        # caller skips the reconcile rather than stranding data.
        (tmp_path / "present.txt").write_text("data\n", encoding="utf-8")
        assert _backup_untracked_files(tmp_path, ["present.txt", "missing.txt"]) is None

    def test_returns_full_map_when_all_paths_read(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
        assert _backup_untracked_files(tmp_path, ["a.txt", "b.txt"]) == {
            "a.txt": b"a\n",
            "b.txt": b"b\n",
        }


class TestNestedUntrackedCollision:
    def test_detects_file_in_wholly_untracked_directory(self, tmp_path: Path) -> None:
        # #408: a custom knowledge path such as docs/LEARNINGS.md, untracked inside a wholly
        # untracked docs/ dir, is reported by `git status` only as `?? docs/`. Expanding
        # untracked dirs (--untracked-files=all) is what lets the collision be detected here
        # rather than surfacing later as a hard merge abort.
        from wade.git.stash import detect_untracked_collisions

        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")
        _git(repo, "branch", "feat")
        # main advances to TRACK a nested docs/LEARNINGS.md.
        (repo / "docs").mkdir()
        (repo / "docs" / "LEARNINGS.md").write_text("upstream\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add nested learnings")
        # Back on the pre-migration base: the whole docs/ dir exists only as untracked.
        _git(repo, "checkout", "feat")
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "LEARNINGS.md").write_text("local\n", encoding="utf-8")
        assert "docs/LEARNINGS.md" in detect_untracked_collisions(repo, "main")


class TestPointerGuard:
    def _commit_pointer_file(self, repo: Path, filename: str, tail: str) -> None:
        _init_repo(repo)
        (repo / filename).write_text("# Project\n\nOriginal content.\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"add {filename}")
        (repo / filename).write_text(
            "# Project\n\nOriginal content.\n" + tail,
            encoding="utf-8",
        )

    def test_diff_only_pointer_true(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        # Only the managed pointer block was appended (the bootstrap state).
        self._commit_pointer_file(
            repo, "AGENTS.md", f"\n{MARKER_START}\n## Git Workflow\npointer\n{MARKER_END}\n"
        )
        assert _pointer_diff_is_only_block(repo, "AGENTS.md") is True

    def test_diff_only_pointer_true_for_claude_md(self, tmp_path: Path) -> None:
        # CLAUDE.md is a pointer target too (project with no AGENTS.md).
        repo = tmp_path / "repo"
        self._commit_pointer_file(
            repo, "CLAUDE.md", f"\n{MARKER_START}\n## Git Workflow\npointer\n{MARKER_END}\n"
        )
        assert _pointer_diff_is_only_block(repo, "CLAUDE.md") is True

    def test_diff_with_real_edit_false(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        # A REAL edit alongside the pointer block — must not be treated as strip-safe.
        self._commit_pointer_file(
            repo,
            "AGENTS.md",
            f"\nA REAL human edit.\n{MARKER_START}\n## Git Workflow\npointer\n{MARKER_END}\n",
        )
        assert _pointer_diff_is_only_block(repo, "AGENTS.md") is False

    def test_reconcile_aborts_when_pointer_has_real_edit(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        self._commit_pointer_file(
            repo,
            "AGENTS.md",
            f"\nREAL edit.\n{MARKER_START}\n## Git Workflow\npointer\n{MARKER_END}\n",
        )
        # A real AGENTS.md edit must make the reconcile defer (return None) rather than
        # silently discard it → Part A surfaces the staleness loudly instead.
        assert _reconcile_skip_worktree_collision(repo, ["AGENTS.md"]) is None


class TestFilesBlockingMerge:
    def test_parses_tab_indented_file_list(self) -> None:
        err = (
            "git merge origin/main failed (exit 1): error: Your local changes to the "
            "following files would be overwritten by merge:\n"
            "\t.gitignore\n\tAGENTS.md\n"
            "Please commit your changes or stash them before you merge.\nAborting\n"
        )
        assert _files_blocking_merge(err) == [".gitignore", "AGENTS.md"]

    def test_returns_empty_for_unrelated_error(self) -> None:
        assert _files_blocking_merge("fatal: some other git error") == []

    def test_reconcile_returns_none_when_non_managed_file_also_blocks(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        # A non-managed file in the blocking set → do not reconcile (defer to Part A).
        blocking = [".gitignore", "src/app.py"]
        assert _reconcile_skip_worktree_collision(repo, blocking) is None


class TestCatchupRetryReusesResolvedRef:
    """#408 review: the skip-worktree-collision retry in ``_catchup_merge`` must reuse the
    exact merge ref resolved for the first attempt. Re-resolving on retry would let a first
    attempt whose fetch failed (falling back to the local base branch) get silently swapped
    for a stale cached ``origin/<main>`` on retry — recreating the #407 silent
    stale-session failure this whole feature exists to prevent.
    """

    @patch(f"{_SYNC}._reconcile_skip_worktree_collision")
    @patch(f"{_SYNC}.git_sync")
    @patch(f"{_SYNC}.git_branch")
    @patch(f"{_SYNC}.git_repo")
    def test_retry_uses_local_fallback_ref_when_first_fetch_failed(
        self,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        mock_reconcile: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.git.repo import GitError
        from wade.models.session import SyncResult
        from wade.services.implementation_service.sync import _catchup_merge

        mock_repo.has_remote.return_value = True
        mock_sync.fetch_origin.side_effect = GitError("network unreachable")

        blocking_error = GitError(
            "git merge main failed (exit 1): error: Your local changes to the following "
            "files would be overwritten by merge:\n\t.gitignore\nAborting\n"
        )
        restore = MagicMock()
        mock_reconcile.return_value = restore
        mock_sync.merge_branch.side_effect = [
            blocking_error,
            SyncResult(
                success=True, current_branch="feat/1-x", main_branch="main", commits_merged=2
            ),
        ]

        seen_refs: list[str] = []

        def commits_ahead(_repo_root: Path, ref: str, _current: str) -> int:
            seen_refs.append(ref)
            # A stale cached origin/main (from an earlier, unrelated fetch) that already
            # looks merged — the bug this guards against would query THIS ref on retry
            # and silently report 0 (falsely up to date).
            return 0 if ref == "origin/main" else 2

        mock_branch.commits_ahead.side_effect = commits_ahead

        events: list[tuple[str, dict[str, object]]] = []

        def emit(event: str, **data: object) -> None:
            events.append((event, data))

        result = _catchup_merge(
            tmp_path,
            tmp_path,
            "feat/1-x",
            "main",
            emit,
            dry_run=False,
            json_output=True,
            already_fetched=False,
        )

        # Fetch attempted exactly once — the retry reuses the ref resolved up front
        # instead of re-deciding fetch state (and does NOT fetch a second time).
        assert mock_sync.fetch_origin.call_count == 1
        # Both the pre-merge behind-count and the merge itself, on BOTH attempts, used
        # the local fallback ref — never the stale cached origin/main.
        assert seen_refs == ["main", "main"]
        merge_refs = [call.args[1] for call in mock_sync.merge_branch.call_args_list]
        assert merge_refs == ["main", "main"]
        assert result.success is True
        assert result.commits_merged == 2
        restore.assert_called_once()

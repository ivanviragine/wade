"""Unit tests for the ``done`` completion gates and their escape hatches (#349)."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from wade.git import branch as git_branch
from wade.models.config import AICommandConfig, AIConfig, DoneConfig, ProjectConfig, ProjectSettings
from wade.models.review import ReviewComment, ReviewThread
from wade.models.session import SyncResult
from wade.services.implementation_service.done import (
    _behind_count,
    _gate_pr_summary,
    _gate_resolved_threads,
    _gate_review_ran,
    _gate_sync,
    _is_placeholder_pr_summary,
)
from wade.utils import markers

# The package re-exports the ``done``/``sync`` *functions*, shadowing the
# submodule attributes, so import the module objects explicitly for patching.
done_mod = importlib.import_module("wade.services.implementation_service.done")
sync_mod = importlib.import_module("wade.services.implementation_service.sync")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# PR-SUMMARY gate
# ---------------------------------------------------------------------------


class TestPrSummaryGate:
    def _config(self, *, require: bool = True) -> ProjectConfig:
        return ProjectConfig(done=DoneConfig(require_pr_summary=require))

    def test_refuses_when_missing(self, tmp_path: Path) -> None:
        assert _gate_pr_summary(self._config(), tmp_path) is False

    def test_passes_with_real_summary(self, tmp_path: Path) -> None:
        (tmp_path / "PR-SUMMARY.md").write_text("## Summary\n\nReal work was done here.\n")
        assert _gate_pr_summary(self._config(), tmp_path) is True

    def test_refuses_on_placeholder(self, tmp_path: Path) -> None:
        (tmp_path / "PR-SUMMARY.md").write_text(
            "## What was done\n[High-level summary in 2-3 sentences]\n"
        )
        assert _gate_pr_summary(self._config(), tmp_path) is False

    def test_hatch_disables_gate(self, tmp_path: Path) -> None:
        # No PR-SUMMARY.md at all, but the hatch is off → gate passes.
        assert _gate_pr_summary(self._config(require=False), tmp_path) is True


class TestPlaceholderDetection:
    def test_empty_is_placeholder(self) -> None:
        assert _is_placeholder_pr_summary("   \n  ") is True

    def test_headings_only_is_placeholder(self) -> None:
        assert _is_placeholder_pr_summary("## Summary\n\n## Changes\n\n---\n") is True

    def test_bracket_placeholder_detected(self) -> None:
        assert _is_placeholder_pr_summary("## Notes\n[Optional: anything]\n") is True

    def test_real_prose_not_placeholder(self) -> None:
        assert _is_placeholder_pr_summary("## Summary\n\nAdded the gate and tests.\n") is False

    def test_bullet_list_not_placeholder(self) -> None:
        assert _is_placeholder_pr_summary("## Changes\n\n- Added X\n- Fixed Y\n") is False


# ---------------------------------------------------------------------------
# review-ran gate
# ---------------------------------------------------------------------------


class TestReviewRanGate:
    def test_refuses_without_marker(self, tmp_path: Path) -> None:
        assert _gate_review_ran(ProjectConfig(), tmp_path, "abc123", skip_review=False) is False

    def test_passes_with_marker(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "reviewed", "abc123")
        assert _gate_review_ran(ProjectConfig(), tmp_path, "abc123", skip_review=False) is True

    def test_marker_for_other_sha_refuses(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "reviewed", "old")
        assert _gate_review_ran(ProjectConfig(), tmp_path, "new", skip_review=False) is False

    def test_skip_review_hatch(self, tmp_path: Path) -> None:
        assert _gate_review_ran(ProjectConfig(), tmp_path, "abc", skip_review=True) is True

    def test_require_review_hatch(self, tmp_path: Path) -> None:
        config = ProjectConfig(done=DoneConfig(require_review=False))
        assert _gate_review_ran(config, tmp_path, "abc", skip_review=False) is True

    def test_auto_skipped_when_reviews_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            ai=AIConfig(review_implementation=AICommandConfig(enabled=False)),
        )
        assert _gate_review_ran(config, tmp_path, "abc", skip_review=False) is True


# ---------------------------------------------------------------------------
# review-thread gate (review-pr-comments sessions)
# ---------------------------------------------------------------------------


def _thread(resolved: bool) -> ReviewThread:
    return ReviewThread(id="t1", is_resolved=resolved, comments=[ReviewComment(body="please fix")])


class TestResolvedThreadsGate:
    def _provider(self, threads: list[ReviewThread] | Exception) -> MagicMock:
        provider = MagicMock()
        if isinstance(threads, Exception):
            provider.get_pr_review_threads.side_effect = threads
        else:
            provider.get_pr_review_threads.return_value = threads
        return provider

    def _lookup(self, *, open_: bool = True) -> MagicMock:
        lookup = MagicMock()
        lookup.lookup_failed = False
        lookup.is_open = open_
        lookup.pr = MagicMock(number=7) if open_ else None
        return lookup

    def test_refuses_on_unresolved(self, monkeypatch, tmp_path: Path) -> None:
        provider = self._provider([_thread(resolved=False)])
        monkeypatch.setattr(done_mod.git_pr, "get_pr_for_branch", lambda *a, **k: self._lookup())
        assert _gate_resolved_threads(ProjectConfig(), provider, tmp_path, "feat/x") is False

    def test_passes_when_all_resolved(self, monkeypatch, tmp_path: Path) -> None:
        provider = self._provider([_thread(resolved=True)])
        monkeypatch.setattr(done_mod.git_pr, "get_pr_for_branch", lambda *a, **k: self._lookup())
        assert _gate_resolved_threads(ProjectConfig(), provider, tmp_path, "feat/x") is True

    def test_transient_fetch_failure_non_blocking(self, monkeypatch, tmp_path: Path) -> None:
        provider = self._provider(RuntimeError("gh boom"))
        monkeypatch.setattr(done_mod.git_pr, "get_pr_for_branch", lambda *a, **k: self._lookup())
        # A flaky provider must not trap completion.
        assert _gate_resolved_threads(ProjectConfig(), provider, tmp_path, "feat/x") is True

    def test_lookup_failure_non_blocking(self, monkeypatch, tmp_path: Path) -> None:
        lookup = MagicMock(lookup_failed=True, is_open=False, pr=None)
        monkeypatch.setattr(done_mod.git_pr, "get_pr_for_branch", lambda *a, **k: lookup)
        assert _gate_resolved_threads(ProjectConfig(), MagicMock(), tmp_path, "feat/x") is True

    def test_hatch_disables_gate(self, tmp_path: Path) -> None:
        config = ProjectConfig(done=DoneConfig(require_resolved_threads=False))
        # No provider call should be needed when the gate is off.
        assert _gate_resolved_threads(config, MagicMock(), tmp_path, "feat/x") is True


# ---------------------------------------------------------------------------
# sync gate + commits_ahead argument-order (both call sites)
# ---------------------------------------------------------------------------


def _repo_ahead_and_behind(root: Path) -> None:
    """Repo where ``feat`` is 2 commits ahead of ``main`` and 1 behind it."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@e.st")
    _git(root, "config", "user.name", "T")
    (root / "m1.txt").write_text("m1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "m1")
    _git(root, "checkout", "-b", "feat")
    for name in ("f1", "f2"):
        (root / f"{name}.txt").write_text(f"{name}\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", name)
    _git(root, "checkout", "main")
    (root / "m2.txt").write_text("m2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "m2")
    _git(root, "checkout", "feat")


class TestCommitsAheadArgumentOrder:
    """Pin the OPPOSITE role assignments used by the sync gate vs the Stop hook."""

    def test_commits_ahead_semantics(self, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        _repo_ahead_and_behind(repo)
        # branch-in-branch-position → how far AHEAD feat is of main.
        assert git_branch.commits_ahead(repo, "feat", "main") == 2
        # base-in-branch-position → how far BEHIND feat is of main.
        assert git_branch.commits_ahead(repo, "main", "feat") == 1

    def test_behind_count_uses_base_in_branch_position(self, tmp_path: Path) -> None:
        # The sync gate measures "behind" — origin/<main> (falls back to <main>)
        # in the branch position.
        repo = tmp_path / "r"
        _repo_ahead_and_behind(repo)
        assert _behind_count(repo, "main", "feat") == 1

    def test_stop_hook_ahead_count_opposite_order(self, tmp_path: Path) -> None:
        # The Stop hook measures "ahead" — the session branch in the branch
        # position (the opposite ref order from the sync gate).
        from wade.hooks.cli import _stop_git_facts

        repo = tmp_path / "r"
        _repo_ahead_and_behind(repo)
        ahead, done_present = _stop_git_facts(repo)
        assert ahead == 2
        assert done_present is False


class TestSyncGate:
    def _config(self, *, require: bool = True) -> ProjectConfig:
        return ProjectConfig(
            project=ProjectSettings(main_branch="main"), done=DoneConfig(require_sync=require)
        )

    def test_passes_when_up_to_date(self, tmp_path: Path, monkeypatch) -> None:
        # main == branch tip → behind 0 → gate passes without syncing.
        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@e.st")
        _git(repo, "config", "user.name", "T")
        (repo / "a.txt").write_text("a\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "a")
        _git(repo, "checkout", "-b", "feat")
        monkeypatch.setattr(done_mod.git_sync, "fetch_origin", lambda *a, **k: None)
        assert _gate_sync(self._config(), repo, repo, "feat", "main", "implementation") is True

    def test_auto_syncs_when_behind(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo_ahead_and_behind(repo)  # feat is 1 behind main
        monkeypatch.setattr(done_mod.git_sync, "fetch_origin", lambda *a, **k: None)
        called = {}

        def _fake_sync(**kwargs: object) -> SyncResult:
            called.update(kwargs)
            return SyncResult(success=True, current_branch="feat", main_branch="main")

        monkeypatch.setattr(sync_mod, "sync", _fake_sync)
        assert _gate_sync(self._config(), repo, repo, "feat", "main", "implementation") is True
        assert called["main_branch"] == "main"

    def test_refuses_on_conflict(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "r"
        _repo_ahead_and_behind(repo)
        monkeypatch.setattr(done_mod.git_sync, "fetch_origin", lambda *a, **k: None)

        def _conflict_sync(**kwargs: object) -> SyncResult:
            return SyncResult(
                success=False, current_branch="feat", main_branch="main", conflicts=["a.txt"]
            )

        monkeypatch.setattr(sync_mod, "sync", _conflict_sync)
        assert _gate_sync(self._config(), repo, repo, "feat", "main", "implementation") is False

    def test_hatch_disables_gate(self, tmp_path: Path) -> None:
        config = self._config(require=False)
        assert _gate_sync(config, tmp_path, tmp_path, "feat", "main", "implementation") is True

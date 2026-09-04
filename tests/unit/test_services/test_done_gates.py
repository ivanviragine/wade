"""Unit tests for the ``done`` completion gates and their escape hatches (#349)."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git import branch as git_branch
from wade.models.config import (
    AICommandConfig,
    AIConfig,
    DoneConfig,
    ProjectConfig,
    ProjectSettings,
    ProviderConfig,
    ProviderID,
)
from wade.models.review import ReviewComment, ReviewThread
from wade.models.session import SyncResult
from wade.models.session_manifest import ResolvedBinding, ReviewOutcome, SessionManifest
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.task import Task
from wade.models.workflow import AICommandKey, DelegationKind, SessionKind
from wade.services.implementation_service.done import (
    _behind_count,
    _gate_knowledge_valid,
    _gate_pr_summary,
    _gate_pr_title,
    _gate_resolved_threads,
    _gate_review_ran,
    _gate_sync,
    _is_placeholder_pr_summary,
    _run_completion_gates,
    _title_fix_hint,
)
from wade.services.review_record_service import write_review_record
from wade.skills.materializer import compute_session_bundle_digest
from wade.skills.validation import inspect_skill

# The package re-exports the ``done``/``sync`` *functions*, shadowing the
# submodule attributes, so import the module objects explicitly for patching.
done_mod = importlib.import_module("wade.services.implementation_service.done")
sync_mod = importlib.import_module("wade.services.implementation_service.sync")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _materialize_review_bundle(root: Path) -> ResolvedBinding:
    session = root / ".wade" / "session"
    skill_dir = session / "skills" / "builtin" / "code-review"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (session / "WORKFLOW.md").write_text("# Implementation workflow\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# Code review methodology\n", encoding="utf-8")
    inspected = inspect_skill(skill_dir, project_root=session)
    return ResolvedBinding.from_skills(
        (
            ResolvedSkill(
                canonical_ref="builtin:code-review",
                source_path="templates/skills/code-review",
                materialized_path=".wade/session/skills/builtin/code-review",
                content_digest=inspected.digest,
                files=inspected.files,
            ),
        )
    )


def _record_review(
    root: Path,
    commit: str,
    *,
    outcome: ReviewOutcome = ReviewOutcome.REVIEWED,
    session_kind: SessionKind = SessionKind.IMPLEMENTATION,
) -> None:
    session = root / ".wade" / "session"
    binding = _materialize_review_bundle(root)
    manifest = SessionManifest(
        session=session_kind,
        workflow_revision=1,
        bundle_digest=compute_session_bundle_digest(session),
        task_id="42",
        ai_command=(
            AICommandKey.REVIEW_PR_COMMENTS
            if session_kind is SessionKind.REVIEW_PR_COMMENTS
            else AICommandKey.IMPLEMENT
        ),
        bindings={SkillSlot.WORK: binding, SkillSlot.REVIEW: binding},
    )
    (session / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    assert (
        write_review_record(
            root,
            delegation=DelegationKind.CODE_REVIEW,
            commit=commit,
            binding=binding,
            outcome=outcome,
        )
        is not None
    )


def _update_manifest(root: Path, **updates: object) -> None:
    path = root / ".wade/session/manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


class TestPrTitleGate:
    """`_gate_pr_title` blocks a non-conventional issue title (both session types)."""

    def _provider(self, title: str) -> MagicMock:
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title=title)
        return provider

    def test_passes_for_conventional_title(self) -> None:
        provider = self._provider("feat: add the thing")
        assert _gate_pr_title(ProjectConfig(), provider, "42") is True

    def test_blocks_non_conventional_title(self) -> None:
        provider = self._provider("E3: Session-start context injection")
        assert _gate_pr_title(ProjectConfig(), provider, "42") is False

    def test_read_failure_is_non_blocking(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = RuntimeError("gh boom")
        # A flaky provider read must not trap completion — _done_via_pr surfaces
        # the hard read error later.
        assert _gate_pr_title(ProjectConfig(), provider, "42") is True

    def test_hatch_disables_gate(self) -> None:
        config = ProjectConfig(done=DoneConfig(require_conventional_title=False))
        provider = MagicMock()
        assert _gate_pr_title(config, provider, "42") is True
        provider.read_task.assert_not_called()

    def test_markup_in_title_does_not_crash(self, capsys) -> None:
        # The rejected title is echoed back through Rich-rendering console methods.
        # A stray `[/]` is markup that "has nothing to close" and raises
        # MarkupError when parsed — the gate must render it literally instead of
        # crashing after the (successful) validation work. See KNOWLEDGE.md.
        provider = self._provider("[/] not conventional")
        assert _gate_pr_title(ProjectConfig(), provider, "42") is False
        out = capsys.readouterr()
        combined = out.out + out.err
        # Rendered literally — the raw bracket text survives to the output.
        assert "[/] not conventional" in combined


class TestTitleFixHint:
    """`_title_fix_hint` points at the configured provider's title-update path."""

    def test_github_uses_gh_issue_edit(self) -> None:
        config = ProjectConfig(provider=ProviderConfig(name=ProviderID.GITHUB))
        hint = _title_fix_hint(config, "42")
        assert "gh issue edit 42" in hint

    def test_clickup_does_not_use_gh(self) -> None:
        config = ProjectConfig(provider=ProviderConfig(name=ProviderID.CLICKUP))
        hint = _title_fix_hint(config, "42")
        assert "gh issue edit" not in hint
        assert "ClickUp" in hint

    def test_markdown_does_not_use_gh(self) -> None:
        config = ProjectConfig(provider=ProviderConfig(name=ProviderID.MARKDOWN))
        hint = _title_fix_hint(config, "42")
        assert "gh issue edit" not in hint
        assert "Markdown" in hint


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
    def test_refuses_without_receipt(self, tmp_path: Path) -> None:
        assert _gate_review_ran(ProjectConfig(), tmp_path, "a" * 40, skip_review=False) is False

    def test_passes_with_matching_receipt(self, tmp_path: Path) -> None:
        head = "a" * 40
        _record_review(tmp_path, head)
        assert _gate_review_ran(ProjectConfig(), tmp_path, head, skip_review=False) is True

    def test_tampered_workflow_invalidates_matching_receipt(self, tmp_path: Path, capsys) -> None:
        head = "a" * 40
        _record_review(tmp_path, head)
        (tmp_path / ".wade" / "session" / "WORKFLOW.md").write_text(
            "# Tampered workflow\n", encoding="utf-8"
        )

        assert _gate_review_ran(ProjectConfig(), tmp_path, head, skip_review=False) is False
        text = _captured_text(capsys)
        assert "bundle failed integrity validation" in text
        assert "wade session refresh-skills" in text

    def test_stale_workflow_revision_invalidates_matching_receipt(
        self, tmp_path: Path, capsys
    ) -> None:
        head = "a" * 40
        _record_review(tmp_path, head)
        _update_manifest(tmp_path, workflow_revision=999)

        assert _gate_review_ran(ProjectConfig(), tmp_path, head, skip_review=False) is False
        assert "bundle failed integrity validation" in _captured_text(capsys)

    def test_wrong_session_kind_invalidates_matching_receipt(self, tmp_path: Path, capsys) -> None:
        head = "a" * 40
        _record_review(tmp_path, head)
        _update_manifest(
            tmp_path,
            session=SessionKind.REVIEW_PR_COMMENTS.value,
            ai_command=AICommandKey.REVIEW_PR_COMMENTS.value,
        )

        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                head,
                skip_review=False,
                session_type="implementation",
            )
            is False
        )
        assert "bundle failed integrity validation" in _captured_text(capsys)

    def test_wrong_ai_command_invalidates_matching_receipt(self, tmp_path: Path, capsys) -> None:
        head = "a" * 40
        _record_review(tmp_path, head)
        _update_manifest(tmp_path, ai_command=AICommandKey.REVIEW_PR_COMMENTS.value)

        assert _gate_review_ran(ProjectConfig(), tmp_path, head, skip_review=False) is False
        assert "bundle failed integrity validation" in _captured_text(capsys)

    def test_receipt_for_other_sha_refuses(self, tmp_path: Path) -> None:
        _record_review(tmp_path, "a" * 40)
        assert _gate_review_ran(ProjectConfig(), tmp_path, "b" * 40, skip_review=False) is False

    def test_unattempted_review_leaves_the_gate_closed(self, tmp_path: Path) -> None:
        """A reviewer that never launched opens nothing (#480).

        The record exists so the state is auditable, but it is not a review: an
        infrastructure failure must not be able to certify a commit.
        """
        head = "a" * 40
        _record_review(tmp_path, head, outcome=ReviewOutcome.UNATTEMPTED)

        assert _gate_review_ran(ProjectConfig(), tmp_path, head, skip_review=False) is False

    def test_unattempted_review_leaves_the_pr_comments_gate_closed(self, tmp_path: Path) -> None:
        """The other session type shares this gate and must refuse too (#480).

        ``review-pr-comments`` keeps the unbounded fast-path-or-refuse behavior,
        so it has no cap branch to fall through — but the branch it *does* take
        is the same classification, and an unattempted record must not satisfy it.
        """
        head = "a" * 40
        _record_review(
            tmp_path,
            head,
            outcome=ReviewOutcome.UNATTEMPTED,
            session_kind=SessionKind.REVIEW_PR_COMMENTS,
        )

        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                head,
                skip_review=False,
                session_type="review-pr-comments",
            )
            is False
        )

    def test_unattempted_review_does_not_advance_the_pass_cap(self, tmp_path: Path, capsys) -> None:
        """It must not spend budget either — the cap exists to bound real review→fix
        cycles, and letting a failed launch burn one would make ``done`` stop
        requiring review for an infrastructure problem (#462)."""
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.UNATTEMPTED)
        _record_review(tmp_path, "2" * 40, outcome=ReviewOutcome.UNATTEMPTED)

        config = ProjectConfig(done=DoneConfig(max_review_passes=2))
        assert _gate_review_ran(config, tmp_path, "3" * 40, skip_review=False) is False
        assert "pass 0 of 2" in _captured_text(capsys)

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


def _captured_text(capsys) -> str:
    """Combined stdout+stderr with whitespace collapsed (survives rich wrapping).

    ``console.error``/``warn`` go to stderr while ``hint``/``detail`` go to
    stdout, and rich soft-wraps long lines — so join both streams and normalize
    whitespace before substring-matching a phrase.
    """
    captured = capsys.readouterr()
    return " ".join((captured.out + "\n" + captured.err).split())


class TestReviewRanCap:
    """The code-enforced review-pass cap (``done.max_review_passes``, default 2) on
    the implementation path (#384)."""

    def test_refuses_before_cap_with_pass_count(self, tmp_path: Path, capsys) -> None:
        # One prior pass, no exact-sha marker, limit 2 → refuse "pass 1 of 2".
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.TIMED_OUT)
        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                "a" * 40,
                skip_review=False,
                session_type="implementation",
            )
            is False
        )
        text = _captured_text(capsys)
        assert "review pass 1 of 2" in text
        assert "--skip-review" in text

    def test_passes_at_cap_with_notice(self, tmp_path: Path, capsys) -> None:
        # Two distinct reviewed commits reach the cap → complete anyway + notice.
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.TIMED_OUT)
        _record_review(tmp_path, "2" * 40, outcome=ReviewOutcome.TIMED_OUT)
        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                "a" * 40,
                skip_review=False,
                session_type="implementation",
            )
            is True
        )
        text = _captured_text(capsys)
        assert "safety limit reached (2 of 2)" in text
        assert "not re-reviewed" in text.lower()
        assert "--skip-review" in text

    def test_tampered_skill_cannot_satisfy_pass_cap(self, tmp_path: Path, capsys) -> None:
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.TIMED_OUT)
        _record_review(tmp_path, "2" * 40, outcome=ReviewOutcome.TIMED_OUT)
        skill = tmp_path / ".wade" / "session" / "skills" / "builtin" / "code-review"
        (skill / "SKILL.md").write_text("# Tampered methodology\n", encoding="utf-8")

        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                "a" * 40,
                skip_review=False,
                session_type="implementation",
            )
            is False
        )
        text = _captured_text(capsys)
        assert "bundle failed integrity validation" in text
        assert "safety limit reached" not in text

    def test_exact_sha_fast_path_wins_over_cap(self, tmp_path: Path, capsys) -> None:
        # An exact-sha reviewed receipt passes even when the review-pass cap is
        # already exhausted — the fast path precedes the cap check.
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.TIMED_OUT)
        _record_review(tmp_path, "2" * 40, outcome=ReviewOutcome.TIMED_OUT)
        head = "a" * 40
        _record_review(tmp_path, head)
        assert (
            _gate_review_ran(
                ProjectConfig(), tmp_path, head, skip_review=False, session_type="implementation"
            )
            is True
        )
        # Took the exact-sha fast path, not the cap branch (no safety-limit notice).
        assert "safety limit reached" not in _captured_text(capsys)

    def test_custom_max_review_passes_honored(self, tmp_path: Path) -> None:
        config = ProjectConfig(done=DoneConfig(max_review_passes=3))
        _record_review(tmp_path, "1" * 40, outcome=ReviewOutcome.TIMED_OUT)
        _record_review(tmp_path, "2" * 40, outcome=ReviewOutcome.TIMED_OUT)
        # 2 passes < limit 3 → still refuses.
        assert (
            _gate_review_ran(
                config, tmp_path, "a" * 40, skip_review=False, session_type="implementation"
            )
            is False
        )
        _record_review(tmp_path, "3" * 40, outcome=ReviewOutcome.TIMED_OUT)
        # 3 passes == limit 3 → passes.
        assert (
            _gate_review_ran(
                config, tmp_path, "a" * 40, skip_review=False, session_type="implementation"
            )
            is True
        )

    def test_fail_safe_count_never_false_caps(self, tmp_path: Path) -> None:
        # A `.wade` that is a regular file makes the listing fail → count 0 →
        # refuse (never a false "cap reached").
        (tmp_path / ".wade").write_text("")
        assert (
            _gate_review_ran(
                ProjectConfig(), tmp_path, "head", skip_review=False, session_type="implementation"
            )
            is False
        )

    def test_review_pr_comments_path_never_caps(self, tmp_path: Path) -> None:
        # Even with passes >= limit, the review-pr-comments path keeps the
        # unbounded fast-path-or-refuse behavior — the cap is impl-only.
        _record_review(
            tmp_path,
            "1" * 40,
            outcome=ReviewOutcome.TIMED_OUT,
            session_kind=SessionKind.REVIEW_PR_COMMENTS,
        )
        _record_review(
            tmp_path,
            "2" * 40,
            outcome=ReviewOutcome.TIMED_OUT,
            session_kind=SessionKind.REVIEW_PR_COMMENTS,
        )
        assert (
            _gate_review_ran(
                ProjectConfig(),
                tmp_path,
                "a" * 40,
                skip_review=False,
                session_type="review-pr-comments",
            )
            is False
        )


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


# ---------------------------------------------------------------------------
# _run_completion_gates dispatch order (load-bearing)
# ---------------------------------------------------------------------------


class TestRunCompletionGatesOrder:
    """Pin the fixed gate order per session type.

    The order is load-bearing: review-ran must run against the pre-sync HEAD
    *before* the sync gate can advance it, and review sessions must run the
    unresolved-threads gate first. Testing each gate in isolation would not catch
    a reordering of the calls inside ``_run_completion_gates``.
    """

    def _order(self, session_type: str) -> list[str]:
        calls: list[str] = []

        def _record(name: str):
            return lambda *a, **k: (calls.append(name), True)[1]

        with (
            patch.object(done_mod, "_gate_pr_title", side_effect=_record("pr_title")),
            patch.object(done_mod, "_gate_pr_summary", side_effect=_record("pr_summary")),
            patch.object(
                done_mod, "_gate_resolved_threads", side_effect=_record("resolved_threads")
            ),
            patch.object(done_mod, "_gate_review_ran", side_effect=_record("review_ran")),
            patch.object(
                done_mod,
                "_gate_documentation_decision",
                side_effect=_record("documentation"),
            ),
            patch.object(done_mod, "_gate_sync", side_effect=_record("sync")),
            patch.object(done_mod, "_gate_knowledge_valid", side_effect=_record("knowledge_valid")),
        ):
            assert (
                _run_completion_gates(
                    session_type=session_type,
                    config=ProjectConfig(),
                    provider=MagicMock(),
                    repo_root=Path("/repo"),
                    worktree_root=Path("/wt"),
                    branch="feat/x",
                    main_branch="main",
                    issue_number="42",
                    pre_sync_head="abc123",
                    skip_review=False,
                )
                is True
            )
        return calls

    def test_implementation_runs_title_pr_summary_review_sync_then_knowledge(self) -> None:
        # Title gate runs first (block earliest on a bad title, before any PR
        # mutation); knowledge validation runs LAST — after sync merges the base
        # branch (the local merge=union point where KNOWLEDGE.md could be corrupted).
        assert self._order("implementation") == [
            "pr_title",
            "pr_summary",
            "review_ran",
            "documentation",
            "sync",
            "knowledge_valid",
        ]

    def test_review_runs_title_summary_threads_review_docs_sync_then_knowledge(self) -> None:
        assert self._order("review-pr-comments") == [
            "pr_title",
            "pr_summary",
            "resolved_threads",
            "review_ran",
            "documentation",
            "sync",
            "knowledge_valid",
        ]


class TestKnowledgeValidGate:
    """`_gate_knowledge_valid` refuses a structurally corrupt knowledge file (#358)."""

    def _config(self, tmp_path: Path, *, enabled: bool) -> ProjectConfig:
        from wade.models.config import KnowledgeConfig

        return ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=enabled, path="KNOWLEDGE.md"),
        )

    def test_noop_when_knowledge_disabled(self, tmp_path: Path) -> None:
        # A corrupt file is ignored entirely when knowledge is off.
        (tmp_path / "KNOWLEDGE.md").write_text(
            "## dup | 2026-01-01 | plan\n\na\n\n---\n## dup | 2026-01-01 | plan\n\nb\n\n---\n",
            encoding="utf-8",
        )
        assert _gate_knowledge_valid(self._config(tmp_path, enabled=False), tmp_path) is True

    def test_passes_for_valid_file(self, tmp_path: Path) -> None:
        (tmp_path / "KNOWLEDGE.md").write_text(
            "# Project Knowledge\n\n## abcd1234 | 2026-01-01 | plan\n\nbody\n\n---\n",
            encoding="utf-8",
        )
        assert _gate_knowledge_valid(self._config(tmp_path, enabled=True), tmp_path) is True

    def test_passes_when_file_missing(self, tmp_path: Path) -> None:
        assert _gate_knowledge_valid(self._config(tmp_path, enabled=True), tmp_path) is True

    def test_refuses_duplicate_entry_id(self, tmp_path: Path) -> None:
        (tmp_path / "KNOWLEDGE.md").write_text(
            "# Project Knowledge\n\n"
            "## abcd1234 | 2026-01-01 | plan\n\none\n\n---\n"
            "## abcd1234 | 2026-01-01 | plan | tags: git\n\ntwo\n\n---\n",
            encoding="utf-8",
        )
        assert _gate_knowledge_valid(self._config(tmp_path, enabled=True), tmp_path) is False

    def test_refuses_unresolved_conflict_markers(self, tmp_path: Path) -> None:
        # validate_knowledge_file rejects unresolved VCS conflict markers too (a non-union
        # merge backstop) — protect that second structural-validation path from regression.
        (tmp_path / "KNOWLEDGE.md").write_text(
            "# Project Knowledge\n\n"
            "## abcd1234 | 2026-01-01 | plan\n\n"
            "<<<<<<< HEAD\none\n=======\ntwo\n>>>>>>> branch\n\n---\n",
            encoding="utf-8",
        )
        assert _gate_knowledge_valid(self._config(tmp_path, enabled=True), tmp_path) is False

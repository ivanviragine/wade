"""Deterministic E2E contracts for implement and done flows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _count_gh_calls,
    _find_mock_pr_number_by_head,
    _git,
    _init_origin_remote,
    _remote_has_branch,
    _run,
    _seed_mock_issue,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestImplementTaskCommand:
    """Test `wade implement` via CLI subprocess."""

    def test_implement_task_cd_bootstraps_worktree_and_draft_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --cd should create worktree, PLAN.md, and bootstrap draft PR."""
        issue_number = 42
        issue_title = "Add deterministic contract coverage"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Add E2E contract tests\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)

        branch_name = "feat/42-add-deterministic-contract-coverage"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        assert expected_worktree.is_dir()
        assert _remote_has_branch(origin_repo, branch_name)

        plan_file = expected_worktree / "PLAN.md"
        assert plan_file.is_file()
        plan_text = plan_file.read_text(encoding="utf-8")
        assert f"Issue #{issue_number}" in plan_text
        assert issue_title in plan_text

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--head", branch_name, "--draft"],
        )
        assert (
            _count_gh_calls(
                mock_gh_cli["log_file"],
                ["pr", "create", "--head", branch_name],
            )
            == 1
        )

    def test_implement_task_fails_for_unknown_issue_without_pr_side_effects(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement should fail fast for unknown issues and avoid PR creation."""
        result = _run(["implement", "999", "--cd"], cwd=e2e_repo)

        assert result.returncode != 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "999"],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "create"]) == 0

    def test_implement_task_cd_runs_setup_worktree_hook(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --cd should execute configured post-worktree hook."""
        issue_number = 44
        issue_title = "Run setup hook from implement"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Validate setup-worktree hook\n",
        )

        config_path = e2e_repo / ".wade.yml"
        original_config = config_path.read_text(encoding="utf-8")
        config_path.write_text(
            original_config
            + "\n"
            + "hooks:\n"
            + "  post_worktree_create: scripts/setup-worktree.sh\n",
            encoding="utf-8",
        )

        scripts_dir = e2e_repo / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        hook_script = scripts_dir / "setup-worktree.sh"
        hook_script.write_text(
            "#!/usr/bin/env sh\nset -eu\necho hook-ran > .hook-ran\n",
            encoding="utf-8",
        )
        hook_script.chmod(0o755)

        _git(["add", "scripts/setup-worktree.sh"], cwd=e2e_repo)
        _git(["commit", "-m", "test: add setup-worktree hook"], cwd=e2e_repo)

        _init_origin_remote(e2e_repo)
        branch_name = "feat/44-run-setup-hook-from-implement"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree
        hook_marker = expected_worktree / ".hook-ran"
        assert hook_marker.is_file()
        assert hook_marker.read_text(encoding="utf-8").strip() == "hook-ran"

    def test_implement_task_cd_does_not_copy_knowledge_files(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --cd must NOT copy the knowledge file or ratings sidecar (#358).

        They are tracked files — a new worktree gets the committed version from its
        checkout. Copying main's (possibly dirty) copy over it is exactly the stale
        snapshot #358 removes, so an *uncommitted* knowledge file must not appear in
        the worktree.
        """
        issue_number = 46
        issue_title = "Knowledge files not copied into worktree"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Verify managed knowledge file is not copied\n",
        )

        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8")
            + "\n"
            + "knowledge:\n"
            + "  enabled: true\n"
            + "  path: docs/LEARNINGS.md\n",
            encoding="utf-8",
        )

        knowledge_dir = e2e_repo / "docs"
        knowledge_dir.mkdir(exist_ok=True)
        knowledge_text = (
            "# Project Knowledge\n\n---\n\n## a1b2c3d4 | 2026-03-24 | plan\n\n"
            "Prefer labels.\n\n---\n"
        )
        # Written but NOT committed — with copying removed, an uncommitted knowledge
        # file does not reach the worktree.
        (knowledge_dir / "LEARNINGS.md").write_text(knowledge_text, encoding="utf-8")

        _init_origin_remote(e2e_repo)
        branch_name = "feat/46-knowledge-files-not-copied-into-worktree"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == expected_worktree

        # .wade.yml is still copied (internal), but the knowledge file is not.
        copied_config = (expected_worktree / ".wade.yml").read_text(encoding="utf-8")
        assert "knowledge:" in copied_config
        assert "path: docs/LEARNINGS.md" in copied_config
        assert not (expected_worktree / "docs" / "LEARNINGS.md").exists()


class TestWorkDoneCommand:
    """Test `wade implementation-session done` via CLI subprocess."""

    def test_work_done_updates_existing_draft_pr_and_pushes_branch(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implementation-session done should push branch and update draft PR path."""
        issue_number = 43
        issue_title = "fix: finalize work done command contract"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Finish implementation\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)

        branch_name = "feat/43-fix-finalize-work-done-command-contract"

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Implemented feature and validated behavior.\\n", encoding="utf-8"
        )
        (worktree_path / "implementation.txt").write_text("work done contract\\n", encoding="utf-8")
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", f"feat: complete #{issue_number}"], cwd=worktree_path)
        assert _git(["status", "--porcelain"], cwd=worktree_path).stdout.strip() == ""

        # --skip-review bypasses the review-ran completion gate — this contract
        # exercises the done→PR mechanics, not the review gate (covered by unit tests).
        result = _run(["implementation-session", "done", "--skip-review"], cwd=worktree_path)
        assert result.returncode == 0
        assert _remote_has_branch(origin_repo, branch_name)
        pr_number = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", str(issue_number)],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "edit", pr_number, "--body"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "ready", pr_number],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", str(issue_number), "--remove-label", "in-progress"],
        )
        assert (
            _count_gh_calls(
                mock_gh_cli["log_file"],
                ["pr", "create", "--head", branch_name],
            )
            == 1
        )

    def test_work_done_links_parent_tracking_issue_from_backticked_checklist(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implementation-session done should detect parent issues from modern checklist refs."""
        tracking_issue = 100
        issue_number = 45
        issue_title = "fix: finalize parent issue detection"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=tracking_issue,
            title="Tracking: rollout",
            body="## Tasks\n  - [ ] `#45`\n",
            labels=["feature-plan"],
        )
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Finish implementation\n",
            labels=["feature-plan"],
        )
        _init_origin_remote(e2e_repo)

        branch_name = "feat/45-fix-finalize-parent-issue-detection"

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Finished implementation with parent tracking linkage.\n",
            encoding="utf-8",
        )
        (worktree_path / "implementation.txt").write_text(
            "tracking parent contract\n", encoding="utf-8"
        )
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", f"feat: complete #{issue_number}"], cwd=worktree_path)

        result = _run(["implementation-session", "done", "--skip-review"], cwd=worktree_path)
        assert result.returncode == 0

        pr_number = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)
        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        prs = state_data.get("prs", {})
        assert isinstance(prs, dict)
        pr_data = prs.get(pr_number)
        assert isinstance(pr_data, dict)
        assert "Part of #100" in str(pr_data.get("body", ""))

    def test_done_escapes_review_loop_after_two_passes(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """#384: review→commit→done refuses once, then completes on the 2nd pass.

        The default review mode is ``prompt`` (self-review, no AI subprocess), so
        each ``wade review implementation`` exits 2 and records a delegation-backed
        ``review-pass@<HEAD>`` marker. Committing after each review invalidates the
        exact-sha ``reviewed`` marker — the exact loop the cap must bound.
        """
        issue_number = 84
        issue_title = "fix: bound the review loop"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Cap the review loop\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)
        branch_name = "feat/84-fix-bound-the-review-loop"

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Bounded the implementation-session review loop with a 2-pass cap.\n",
            encoding="utf-8",
        )

        def _commit(content: str) -> None:
            (worktree_path / "impl.txt").write_text(content, encoding="utf-8")
            _git(["add", "-A"], cwd=worktree_path)
            _git(["commit", "-m", f"feat: impl {content.strip()}"], cwd=worktree_path)

        # --- Pass 1: review, commit AFTER the review, then done must REFUSE. ---
        _commit("v1\n")
        review1 = _run(["review", "implementation"], cwd=worktree_path)
        assert review1.returncode == 2, review1.stdout + review1.stderr

        _commit("v2\n")  # new commit → the reviewed@<sha> marker is now stale
        done1 = _run(["implementation-session", "done"], cwd=worktree_path)
        out1 = " ".join((done1.stdout + done1.stderr).split())
        assert done1.returncode != 0, out1
        assert "review pass 1 of 2" in out1
        # Nothing was finalized on the refusal.
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "ready"]) == 0

        # --- Pass 2: review again, commit again — done SUCCEEDS at the cap. ---
        review2 = _run(["review", "implementation"], cwd=worktree_path)
        assert review2.returncode == 2, review2.stdout + review2.stderr

        _commit("v3\n")  # still a newer, un-reviewed commit — but the cap is hit
        done2 = _run(["implementation-session", "done"], cwd=worktree_path)
        out2 = " ".join((done2.stdout + done2.stderr).split())
        assert done2.returncode == 0, out2
        assert "safety limit reached" in out2
        assert _remote_has_branch(origin_repo, branch_name)

        # #367: completing at the cap without a fresh review is recorded in the PR
        # body as a cap-reached note, not a clean "reviewed" line.
        body = _pr_body_for_branch(mock_gh_cli["state_file"], branch_name)
        assert "<!-- wade:review-status:start -->" in body
        assert "cap reached" in body
        assert "✅ Reviewed" not in body

    def test_review_pass_count_survives_second_implement(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """#384: the review-pass count persists across a second `wade implement`.

        The markers live in the worktree's ``.wade/`` and no code path (bootstrap
        included) clears the ``review-pass@*`` family, so the idempotent
        worktree-reuse re-run must not reset the cap to 0.
        """
        issue_number = 85
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Persist review passes",
            body="## Tasks\n- Keep the count\n",
        )
        _init_origin_remote(e2e_repo)

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        # Simulate a completed delegation-backed review pass.
        marker = worktree_path / ".wade" / ("review-pass@" + "d" * 40)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")

        again_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert again_result.returncode == 0
        assert Path(again_result.stdout.strip()) == worktree_path
        # The marker (and thus the pass count) survived the re-bootstrap.
        assert marker.is_file()

    def test_work_done_fails_when_managed_claude_files_were_force_committed(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implementation-session done should block tracked wade-managed Claude files."""
        issue_number = 47
        # Conventional title so the fixture stays realistic and independent of gate
        # ordering — the tracked-managed-files check runs before the title gate
        # today, but a conventional title keeps this test asserting the intended
        # failure even if that order ever changes.
        issue_title = "fix: block tracked managed Claude files"
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Guard against tracked managed files\n",
        )
        _init_origin_remote(e2e_repo)

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        managed_claude_dir = worktree_path / ".claude"
        assert managed_claude_dir.is_dir()
        # Force-add gitignored .claude files to simulate accidental tracking.
        # Also stage any other untracked non-gitignored files wade created (e.g.
        # hook configs in .cursor/, .github/) so the working tree is
        # clean before calling done.
        _git(["add", "-f", ".claude"], cwd=worktree_path)
        _git(["add", "."], cwd=worktree_path)
        _git(
            ["commit", "-m", "test: accidentally track managed claude files"],
            cwd=worktree_path,
        )
        status = _git(["status", "--porcelain"], cwd=worktree_path)
        assert status.stdout.strip() == "", status.stdout

        result = _run(["implementation-session", "done"], cwd=worktree_path)
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "Wade-managed files are tracked in git" in output
        assert ".claude/skills/implementation-session/SKILL.md" in output
        assert "git rm --cached .claude/skills/implementation-session/SKILL.md" in output
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "edit"]) == 0
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "ready"]) == 0


def _pr_body_for_branch(state_file: Path, branch_name: str) -> str:
    """Return the stored PR body for the given head branch from mock gh state."""
    pr_number = _find_mock_pr_number_by_head(state_file, branch_name)
    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    prs = state_data.get("prs", {})
    assert isinstance(prs, dict)
    pr_data = prs.get(pr_number)
    assert isinstance(pr_data, dict)
    return str(pr_data.get("body", ""))


class TestReviewStatusBlockContract:
    """#367: `done` projects the review status into the durable PR body.

    Complements the unit tests (pure classifier + renderer) by exercising the
    config wiring end to end — the ``done.require_review``/``--skip-review``/
    reviewed-marker paths all reach the PR body a human reviewer actually sees.
    """

    _MARKER = "<!-- wade:review-status:start -->"

    def _bootstrap(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
        issue_number: int,
        issue_title: str,
    ) -> tuple[Path, str]:
        """implement → worktree → PR-SUMMARY + one impl commit. Returns (wt, branch)."""
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Do the work\n",
        )
        _init_origin_remote(e2e_repo)

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0, start_result.stdout + start_result.stderr
        worktree_path = Path(start_result.stdout.strip())
        assert worktree_path.is_dir()

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Implemented the change and validated behavior.\n", encoding="utf-8"
        )
        (worktree_path / "impl.txt").write_text("work\n", encoding="utf-8")
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", f"feat: complete #{issue_number}"], cwd=worktree_path)

        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path).stdout.strip()
        return worktree_path, branch

    def test_skip_review_records_skip_notice_in_pr_body(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """A deliberate --skip-review bypass is visible in the PR body (#367)."""
        worktree_path, branch = self._bootstrap(
            e2e_repo, mock_gh_cli, 361, "fix: record the review skip"
        )

        result = _run(["implementation-session", "done", "--skip-review"], cwd=worktree_path)
        assert result.returncode == 0, result.stdout + result.stderr

        body = _pr_body_for_branch(mock_gh_cli["state_file"], branch)
        assert self._MARKER in body
        assert "Review skipped" in body
        assert "--skip-review" in body

    def test_reviewed_run_records_reviewed_line_in_pr_body(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """A normal reviewed run shows a "Reviewed at <sha>" line (#367)."""
        worktree_path, branch = self._bootstrap(
            e2e_repo, mock_gh_cli, 362, "fix: record the reviewed status"
        )

        # A real self-review pass (default prompt mode, exit 2) writes the
        # sha-keyed reviewed@<HEAD> marker the done gate later reads.
        review = _run(["review", "implementation"], cwd=worktree_path)
        assert review.returncode == 2, review.stdout + review.stderr
        head = _git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()

        result = _run(["implementation-session", "done"], cwd=worktree_path)
        assert result.returncode == 0, result.stdout + result.stderr

        body = _pr_body_for_branch(mock_gh_cli["state_file"], branch)
        assert self._MARKER in body
        assert f"Reviewed at `{head[:7]}`" in body

    def test_require_review_disabled_records_gate_disabled_note(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """`done.require_review: false` surfaces a "review gate disabled" note (#367).

        Exercises the config wiring end to end (not just the pure classifier): the
        toggle is set in the project config the worktree inherits, and done — with
        no review run at all — still records why the gate did not apply.
        """
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\ndone:\n  require_review: false\n",
            encoding="utf-8",
        )

        worktree_path, branch = self._bootstrap(
            e2e_repo, mock_gh_cli, 363, "fix: reflect the disabled review gate"
        )

        result = _run(["implementation-session", "done"], cwd=worktree_path)
        assert result.returncode == 0, result.stdout + result.stderr

        body = _pr_body_for_branch(mock_gh_cli["state_file"], branch)
        assert self._MARKER in body
        assert "Review gate disabled" in body
        assert "done.require_review: false" in body

    def test_reviews_disabled_records_gate_disabled_note(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """`ai.review_implementation.enabled: false` surfaces the gate-disabled note (#367).

        Distinct config path from `done.require_review: false`: reviews are off
        project-wide, so the review-ran gate auto-skips — done still records *why*
        (the `review_implementation.enabled: false` wording) in the PR body.
        """
        config_path = e2e_repo / ".wade.yml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "ai:\n  default_tool: claude\n",
                "ai:\n  default_tool: claude\n  review_implementation:\n    enabled: false\n",
            ),
            encoding="utf-8",
        )
        # Guard against a silent replace-miss producing a confusing downstream fail.
        assert "review_implementation:" in config_path.read_text(encoding="utf-8")

        worktree_path, branch = self._bootstrap(
            e2e_repo, mock_gh_cli, 364, "fix: reflect reviews disabled"
        )

        result = _run(["implementation-session", "done"], cwd=worktree_path)
        assert result.returncode == 0, result.stdout + result.stderr

        body = _pr_body_for_branch(mock_gh_cli["state_file"], branch)
        assert self._MARKER in body
        assert "Review gate disabled" in body
        assert "review_implementation.enabled: false" in body

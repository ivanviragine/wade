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


def _record_implementation_docs(worktree_path: Path) -> None:
    """Record the mandatory current-commit documentation decision for a fixture."""

    result = _run(
        [
            "implementation-session",
            "docs",
            "--not-needed",
            "E2E fixture has no documentation impact",
        ],
        cwd=worktree_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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

    def test_implement_cd_with_base_creates_draft_pr_from_base(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """implement --base develop should cut the branch from and target the PR at develop."""
        issue_number = 42
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Add base branch support",
            body="## Tasks\n- do it\n",
        )
        origin_repo = _init_origin_remote(e2e_repo)
        # A real, pushable base branch to cut work from.
        _git(["branch", "develop", "main"], cwd=e2e_repo)
        _git(["push", "origin", "develop"], cwd=e2e_repo)

        branch_name = "feat/42-add-base-branch-support"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        result = _run(["implement", str(issue_number), "--base", "develop", "--cd"], cwd=e2e_repo)
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == expected_worktree
        assert _remote_has_branch(origin_repo, branch_name)

        # Draft PR targets develop.
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--base", "develop", "--head", branch_name, "--draft"],
        )
        pr_num = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)
        state = json.loads(mock_gh_cli["state_file"].read_text())
        assert state["prs"][pr_num]["base"] == "develop"

        # The resolved base is persisted so sync/done merge into develop, not main.
        base_file = expected_worktree / ".wade" / "base_branch"
        assert base_file.read_text(encoding="utf-8").strip() == "develop"

    def test_implement_cd_base_override_retargets_existing_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """A later --base override retargets the existing draft PR and re-pins the worktree base."""
        issue_number = 42
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Add base branch support",
            body="## Tasks\n- do it\n",
        )
        _init_origin_remote(e2e_repo)
        _git(["branch", "develop", "main"], cwd=e2e_repo)
        _git(["push", "origin", "develop"], cwd=e2e_repo)

        branch_name = "feat/42-add-base-branch-support"
        expected_worktree = (
            e2e_repo.parent / ".worktrees" / e2e_repo.name / branch_name.replace("/", "-")
        )

        # First run bootstraps a draft PR targeting main.
        first = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert first.returncode == 0, first.stderr
        pr_num = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)
        state = json.loads(mock_gh_cli["state_file"].read_text())
        assert state["prs"][pr_num]["base"] == "main"

        # Second run overrides the base → the PR is retargeted via `gh pr edit --base`.
        second = _run(["implement", str(issue_number), "--base", "develop", "--cd"], cwd=e2e_repo)
        assert second.returncode == 0, second.stderr

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "edit", str(pr_num), "--base", "develop"],
        )
        state = json.loads(mock_gh_cli["state_file"].read_text())
        assert state["prs"][pr_num]["base"] == "develop"
        base_file = expected_worktree / ".wade" / "base_branch"
        assert base_file.read_text(encoding="utf-8").strip() == "develop"

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

    def test_implement_cd_snapshots_main_only_custom_skills_under_fixed_workflow(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Custom methodology is copied, while WADE still owns every required step."""
        issue_number = 43
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Use project session methods",
            body="## Tasks\n- Exercise dynamic skills\n",
        )
        work_skill = e2e_repo / ".agents/skills/domain-implementation"
        work_skill.mkdir(parents=True)
        (work_skill / "SKILL.md").write_text(
            "---\n"
            "name: domain-implementation\n"
            "description: Implement this project's domain behavior.\n"
            "---\n\n"
            "Trace domain invariants from input through persistence.\n",
            encoding="utf-8",
        )
        (work_skill / "reference").mkdir()
        (work_skill / "reference/invariants.md").write_text(
            "# Domain invariants\n",
            encoding="utf-8",
        )
        review_skill = e2e_repo / ".claude/skills/security-review"
        review_skill.mkdir(parents=True)
        (review_skill / "SKILL.md").write_text(
            "---\n"
            "name: security-review\n"
            "description: Review trust boundaries and authorization.\n"
            "---\n\n"
            "Follow untrusted data across each authorization boundary.\n",
            encoding="utf-8",
        )
        _init_origin_remote(e2e_repo)

        result = _run(
            [
                "implement",
                str(issue_number),
                "--cd",
                "--skill",
                "project:domain-implementation",
                "--review-skill",
                "project:security-review",
            ],
            cwd=e2e_repo,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        worktree = Path(result.stdout.strip())
        manifest = json.loads((worktree / ".wade/session/manifest.json").read_text())
        assert manifest["bindings"]["work"]["skills"][0]["canonical_ref"] == (
            "project:domain-implementation"
        )
        assert manifest["bindings"]["review"]["skills"][0]["canonical_ref"] == (
            "project:security-review"
        )

        work_snapshot = (
            worktree / ".wade/session/skills/project/agents-skills/domain-implementation"
        )
        assert (work_snapshot / "reference/invariants.md").is_file()
        assert not work_snapshot.is_symlink()
        workflow = (worktree / ".wade/session/WORKFLOW.md").read_text(encoding="utf-8")
        for required in (
            "**Check readiness.**",
            "**Apply the WORK methodology.**",
            "**Method review.**",
            "**Documentation [mandatory decision].**",
            "**Sync.**",
            "**Done.**",
        ):
            assert required in workflow

    def test_unknown_custom_skill_fails_before_provider_mutation(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """An unresolved active ref may create a local worktree, but no remote state."""
        issue_number = 45
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Reject missing session method",
            body="## Tasks\n- Fail before mutation\n",
        )
        _init_origin_remote(e2e_repo)

        result = _run(
            ["implement", str(issue_number), "--cd", "--skill", "project:missing"],
            cwd=e2e_repo,
        )

        assert result.returncode != 0
        assert "was not found" in result.stderr
        for mutation in (
            ["pr", "create"],
            ["pr", "edit"],
            ["issue", "edit"],
            ["issue", "comment"],
            ["label", "create"],
        ):
            assert _count_gh_calls(mock_gh_cli["log_file"], mutation) == 0

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
        _record_implementation_docs(worktree_path)
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

    def _done_ready_worktree(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
        *,
        issue_number: int,
        title: str,
    ) -> Path:
        """Seed an issue, start a session, and commit work so `done` can finalize."""
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=title,
            body="## Tasks\n- Finish implementation\n",
        )
        _init_origin_remote(e2e_repo)

        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        worktree_path = Path(start_result.stdout.strip())

        (worktree_path / "PR-SUMMARY.md").write_text("Bot trigger contract.\n", encoding="utf-8")
        (worktree_path / "implementation.txt").write_text("bot trigger\n", encoding="utf-8")
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", f"feat: complete #{issue_number}"], cwd=worktree_path)
        _record_implementation_docs(worktree_path)
        return worktree_path

    def test_done_offers_bot_triggers_without_posting(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """#464: auto_trigger off + no TTY → nothing posted, but the offer is surfaced."""
        issue_number = 47
        worktree_path = self._done_ready_worktree(
            e2e_repo,
            mock_gh_cli,
            issue_number=issue_number,
            title="fix: offer bot review triggers after done",
        )

        result = _run(["implementation-session", "done", "--skip-review"], cwd=worktree_path)

        assert result.returncode == 0
        out = " ".join((result.stdout + result.stderr).split())
        assert "Bot review triggers NOT posted" in out
        assert f"wade review trigger {issue_number}" in out
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment"]) == 0

    def test_done_trigger_bots_flag_posts_every_enabled_bot(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """#464: `--trigger-bots` posts the triggers even with auto_trigger off."""
        issue_number = 48
        branch_name = "feat/48-fix-post-bot-review-triggers-from-done"
        worktree_path = self._done_ready_worktree(
            e2e_repo,
            mock_gh_cli,
            issue_number=issue_number,
            title="fix: post bot review triggers from done",
        )

        result = _run(
            ["implementation-session", "done", "--skip-review", "--trigger-bots"],
            cwd=worktree_path,
        )

        assert result.returncode == 0
        pr_number = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment", pr_number]) == 3
        for body in ("@coderabbitai review", "@codex review", "bugbot run"):
            _assert_gh_called_with(
                mock_gh_cli["log_file"], ["pr", "comment", pr_number, "--body", body]
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
        _record_implementation_docs(worktree_path)

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
        each ``wade review implementation`` exits 2 without certifying itself.
        The explicit acknowledgement records the completed self-review. Committing
        afterward invalidates the exact-commit success — the loop the active-binding
        pass cap must bound.
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
        ack1 = _run(["review", "implementation", "--ack-self-review"], cwd=worktree_path)
        assert ack1.returncode == 0, ack1.stdout + ack1.stderr

        _commit("v2\n")  # new commit → the prior review record is now stale
        done1 = _run(["implementation-session", "done"], cwd=worktree_path)
        out1 = " ".join((done1.stdout + done1.stderr).split())
        assert done1.returncode != 0, out1
        assert "review pass 1 of 2" in out1
        # Nothing was finalized on the refusal.
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "ready"]) == 0

        # --- Pass 2: review again, commit again — done SUCCEEDS at the cap. ---
        review2 = _run(["review", "implementation"], cwd=worktree_path)
        assert review2.returncode == 2, review2.stdout + review2.stderr
        ack2 = _run(["review", "implementation", "--ack-self-review"], cwd=worktree_path)
        assert ack2.returncode == 0, ack2.stdout + ack2.stderr

        _commit("v3\n")  # still a newer, un-reviewed commit — but the cap is hit
        _record_implementation_docs(worktree_path)
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

    def test_review_records_survive_second_implement(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Binding-aware review history persists across a second `wade implement`.

        Durable records live outside ``.wade/session``. Idempotent worktree reuse
        may re-bootstrap compatibility files but must preserve review history.
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

        review = _run(["review", "implementation"], cwd=worktree_path)
        assert review.returncode == 0, review.stdout + review.stderr
        records = sorted((worktree_path / ".wade/reviews").glob("*.json"))
        assert records
        before = {path.name: path.read_bytes() for path in records}

        again_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert again_result.returncode == 0
        assert Path(again_result.stdout.strip()) == worktree_path
        after = {
            path.name: path.read_bytes()
            for path in sorted((worktree_path / ".wade/reviews").glob("*.json"))
        }
        assert after == before

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
        assert ".claude/skills/task/SKILL.md" in output
        assert "git rm --cached .claude/skills/task/SKILL.md" in output
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
        _record_implementation_docs(worktree_path)

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

        # Default prompt mode emits the review but cannot certify itself; the
        # separate acknowledgement writes the record the done gate later reads.
        review = _run(["review", "implementation"], cwd=worktree_path)
        assert review.returncode == 2, review.stdout + review.stderr
        acknowledged = _run(["review", "implementation", "--ack-self-review"], cwd=worktree_path)
        assert acknowledged.returncode == 0, acknowledged.stdout + acknowledged.stderr
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


def _codex_pre_tool_use_entries(worktree_path: Path) -> list[dict]:
    """Return Codex's PreToolUse hook entries from a bootstrapped worktree."""
    hooks_file = worktree_path / ".codex" / "hooks.json"
    assert hooks_file.is_file(), "bootstrap must write Codex hooks"
    return json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


class TestImplementSandboxProfileContract:
    """`wade implement` installs its guards under both sandbox profiles (#478).

    The default flip to ``unrestricted`` removes Codex's native write sandbox, so
    these assert end-to-end that WADE's own containment does not go with it: the
    worktree guard is installed either way, and *widens* when the sandbox is off.
    """

    @staticmethod
    def _bootstrap(e2e_repo: Path, mock_gh_cli: MockGhCli, *flag: str) -> Path:
        issue_number = 42
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title="Add deterministic contract coverage",
            body="## Tasks\n- Add E2E contract tests\n",
        )
        _init_origin_remote(e2e_repo)

        result = _run(["implement", str(issue_number), "--cd", *flag], cwd=e2e_repo)
        assert result.returncode == 0, result.stdout + result.stderr
        worktree = Path(result.stdout.strip())
        assert worktree.is_dir()
        return worktree

    def test_unrestricted_profile_installs_the_full_worktree_guard(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        worktree = self._bootstrap(e2e_repo, mock_gh_cli, "--no-sandbox")

        entries = _codex_pre_tool_use_entries(worktree)
        assert entries, "the worktree guard must be installed"
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
        assert any("--guard worktree" in c for c in commands)
        # With no OS sandbox, the matcher must NOT be narrowed to the shell token
        # — nothing else would cover tool-call writes outside the worktree.
        assert {entry.get("matcher") for entry in entries} != {"Bash"}

        # The Stop-hook completion reminder is unaffected by the profile.
        assert "session-complete" in (worktree / ".codex" / "hooks.json").read_text("utf-8")

    def test_sandboxed_profile_keeps_the_shell_narrowing(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        worktree = self._bootstrap(e2e_repo, mock_gh_cli, "--sandbox")

        entries = _codex_pre_tool_use_entries(worktree)
        assert entries, "the worktree guard must still be installed"
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
        assert any("--guard worktree" in c for c in commands)
        # Codex's workspace-write sandbox covers tool-call writes, so the guard
        # narrows to the shell token it does not cover (/tmp, $TMPDIR redirects).
        assert {entry.get("matcher") for entry in entries} == {"Bash"}
        assert "session-complete" in (worktree / ".codex" / "hooks.json").read_text("utf-8")

    def test_default_profile_matches_the_unrestricted_one(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        # No flag, no config: the terminal default is unrestricted, so the guard
        # must come out wide rather than narrowed.
        worktree = self._bootstrap(e2e_repo, mock_gh_cli)
        entries = _codex_pre_tool_use_entries(worktree)
        assert {entry.get("matcher") for entry in entries} != {"Bash"}

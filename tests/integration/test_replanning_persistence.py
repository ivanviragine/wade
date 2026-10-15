"""Ordinary replanning persists reviewed input before real managed cleanup.

Only provider/GitHub and the native runtime process are simulated. Git worktree
creation/removal, frozen bundle, native import/review/completion, persistence,
attachment, bootstrap and finalization execute their production code.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ConfigDict, Field, create_model

from wade.models.config import (
    AICommandConfig,
    AIConfig,
    KnowledgeConfig,
    ProjectConfig,
    ProjectSettings,
)
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.interactive_plan import InteractivePlanState
from wade.models.plan_bundle import PlanBundle
from wade.models.task import Task
from wade.services import interactive_plan_service as interactive
from wade.services import plan_service
from wade.services.implementation_service.draft_pr import extract_plan_from_pr_body

_PLAN = (
    "# fix: replacement requirements\n\n## Complexity\ncomplex\n\n"
    "## Context\nNew replacement requirements.\n\n## Tasks\n- [ ] Persist replacement.\n\n"
    "## Acceptance Criteria\n- [ ] Next implementation receives replacement.\n"
)
_BRANCH = "feat/42-fix-existing-task"
_PLAN_BLOCK = "<!-- wade:plan:start -->\nObsolete requirements.\n<!-- wade:plan:end -->"


class RemoteState(BaseModel):
    pr_body: str = "User PR introduction.\n\n" + _PLAN_BLOCK + "\n\n## Requirements\nKeep this."
    issue_body: str = "Original issue body."
    pr_writes: int = 0
    fresh_pr: bool = False
    pr_creates: int = 0
    planning_root: Path | None = None
    failure: str | None = None
    native_runs: int = 0
    branch: str = _BRANCH
    title: str = "fix: existing task"
    pr_title: str = "fix: existing task"
    plan_markdown: str = _PLAN
    managed: bool = True
    service_review: bool = False
    original_manifest: bytes = b""
    original_review: bytes = b""
    original_binding: bytes = b""
    base: str = "main"
    lookups: list[str] = Field(default_factory=list)
    stale_state: str | None = None
    edited_prs: list[str] = Field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def replanning(
    tmp_git_repo: Path,
) -> Iterator[tuple[Path, ProjectConfig, MagicMock, RemoteState]]:
    repo = tmp_git_repo
    _git(repo, "branch", "-M", "main")
    _git(repo, "branch", _BRANCH)
    config = ProjectConfig(
        project=ProjectSettings(main_branch="main"),
        ai=AIConfig(default_tool="claude", review_plan=AICommandConfig(enabled=True)),
        knowledge=KnowledgeConfig(enabled=False),
    )
    state = RemoteState()
    provider = MagicMock()

    def read_task(*_args: object) -> Task:
        if state.failure == "issue-read" and state.planning_root is not None:
            raise RuntimeError("required issue read failed")
        return Task(id="42", title=state.title, body=state.issue_body)

    def update_task(*_args: object, **kwargs: object) -> None:
        if state.failure == "issue-write":
            raise RuntimeError("required issue write failed")
        if "body" in kwargs:
            state.issue_body = str(kwargs["body"])

    provider.read_task.side_effect = read_task
    provider.update_task.side_effect = update_task

    def gh(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("pr", "view"):
            if args[-1] == "body":
                return subprocess.CompletedProcess(
                    args,
                    int(state.failure == "pr-read"),
                    json.dumps({"body": state.pr_body}),
                    "read failed",
                )
            state.lookups.append(args[2])
            if state.fresh_pr and state.pr_creates == 0:
                return subprocess.CompletedProcess(args, 1, "", "no pull requests found for branch")
            if state.failure == "lookup":
                return subprocess.CompletedProcess(args, 1, "", "not authorized")
            if state.stale_state is not None and args[2] == _BRANCH:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        {
                            "number": 8,
                            "state": state.stale_state,
                            "title": "Old closed PR",
                            "baseRefName": "main",
                            "isDraft": False,
                        }
                    ),
                    "",
                )
            if state.failure == "listing" or args[2] != state.branch:
                return subprocess.CompletedProcess(args, 1, "", "no pull requests found for branch")
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "number": 7,
                        "url": "https://example.test/pull/7",
                        "title": state.pr_title,
                        "state": "OPEN",
                        "isDraft": True,
                        "baseRefName": state.base,
                    }
                ),
                "",
            )
        if args[:2] == ("pr", "create"):
            state.pr_creates += 1
            state.pr_body = args[args.index("--body") + 1]
            state.base = args[args.index("--base") + 1]
            state.pr_title = args[args.index("--title") + 1]
            return subprocess.CompletedProcess(args, 0, "https://example.test/pull/7\n", "")
        if args[:2] == ("pr", "list"):
            if state.fresh_pr and state.pr_creates == 0:
                return subprocess.CompletedProcess(args, 0, "[]", "")
            return subprocess.CompletedProcess(
                args,
                int(state.failure == "listing"),
                json.dumps(
                    [
                        {
                            "number": 7,
                            "url": "https://example.test/pull/7",
                            "headRefName": state.branch,
                            "state": "OPEN",
                            "isDraft": True,
                            "updatedAt": "2026-10-01T00:00:00Z",
                        }
                    ]
                ),
                "listing failed",
            )
        if args[:2] == ("pr", "edit"):
            state.edited_prs.append(args[2])
            if "--body" in args:
                state.pr_writes += 1
                if state.failure == "pr-write":
                    return subprocess.CompletedProcess(args, 1, "", "write failed")
                state.pr_body = args[args.index("--body") + 1]
            elif "--base" in args:
                state.base = args[args.index("--base") + 1]
            elif "--title" in args:
                if state.failure == "pr-title":
                    return subprocess.CompletedProcess(args, 1, "", "title write failed")
                state.pr_title = args[args.index("--title") + 1]
            else:
                raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    def native_process(_argv: list[str], *_args: object, **kwargs: object) -> int:
        root = Path(str(kwargs["cwd"]))
        state.planning_root = root
        state.native_runs += 1
        if state.managed:
            assert str(root) in _git(repo, "worktree", "list", "--porcelain")
        assert (root / ".wade/session/manifest.json").is_file()
        interactive.import_artifact(root, state.plan_markdown)
        if state.service_review:
            from wade.services.review_delegation_service import review_plan

            with patch(
                "wade.services.review_delegation_service.delegate",
                return_value=DelegationResult(
                    success=True,
                    feedback="No material actionable findings.",
                    mode=DelegationMode.HEADLESS,
                    exit_code=0,
                ),
            ):
                reviewed = review_plan(
                    str(root / ".wade/plans/PLAN.md"),
                    project_root=root,
                    ai_tool="codex",
                    model="gpt-6.1-sol",
                    effort="high",
                    mode="headless",
                    permission_mode="default",
                    sandbox=True,
                )
            assert reviewed.success
        else:
            interactive.record_review(
                root / ".wade/plans/PLAN.md", state.plan_markdown, self_review=False
            )
        interactive.complete(root)
        state.original_manifest = (root / ".wade/session/manifest.json").read_bytes()
        state.original_review = (root / ".wade/plans/interactive-reviews.json").read_bytes()
        if state.managed:
            state.original_binding = (root / ".wade/plans/handoff-binding.json").read_bytes()
        # A user edits the issue while the native planning session is active.
        state.issue_body += "\nCurrent user edit."
        return 0

    with (
        patch("wade.services.plan_service.load_config", return_value=config),
        patch("wade.services.review_delegation_service.load_config", return_value=config),
        patch("wade.services.plan_service.get_provider", return_value=provider),
        patch("wade.services.plan_service.prompts.is_tty", return_value=False),
        patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
        patch("crossby.utils.process.run_with_transcript", side_effect=native_process),
        patch("wade.git.pr._run_gh", side_effect=gh),
        patch("wade.services.plan_service.set_terminal_title"),
        patch("wade.services.plan_service.start_title_keeper"),
        patch("wade.services.plan_service.stop_title_keeper"),
    ):
        yield repo, config, provider, state


def test_normal_replanning_persists_reviewed_body_before_managed_cleanup(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
) -> None:
    repo, _config, provider, state = replanning
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.planning_root is not None
    expected = _PLAN.split("\n", 1)[1].strip()
    assert extract_plan_from_pr_body(state.pr_body) == expected
    assert state.pr_writes == 1
    assert "User PR introduction." in state.pr_body
    assert "## Requirements\nKeep this." in state.pr_body
    assert "Current user edit." in state.issue_body
    assert state.issue_body.count("**Full plan**: PR #7") == 1
    assert not state.planning_root.exists()
    assert str(state.planning_root) not in _git(repo, "worktree", "list", "--porcelain")
    provider.create_task.assert_not_called()


def test_normal_replanning_preserves_issue_edits_made_during_native_session(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
) -> None:
    repo, _config, _provider, state = replanning
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert "Current user edit." in state.issue_body


@pytest.mark.parametrize(
    "failure",
    [
        "pr-read",
        "pr-write",
        "pr-title",
        "issue-read",
        "issue-write",
        "lookup",
        "listing",
        "malformed",
    ],
)
def test_normal_failure_retains_original_managed_handoff_and_supported_recovery(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], failure: str
) -> None:
    repo, _config, provider, state = replanning
    state.failure = failure
    if failure == "pr-title":
        state.title = "fix: renamed title"
    original_body = state.pr_body
    if failure == "malformed":
        state.pr_body += "\n<!-- wade:plan:end -->"
    assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None and root.exists()
    assert str(root) in _git(repo, "worktree", "list", "--porcelain")
    assert (root / ".wade/plans/PLAN.md").read_text() == _PLAN
    assert (root / ".wade/plans/handoff-binding.json").is_file()
    assert (root / ".wade/plans/handoff-progress.json").is_file()
    assert (root / ".wade/session/manifest.json").read_bytes() == state.original_manifest
    assert (root / ".wade/plans/interactive-reviews.json").read_bytes() == state.original_review
    assert (root / ".wade/plans/handoff-binding.json").read_bytes() == state.original_binding
    if failure in {"pr-read", "lookup", "listing", "malformed"}:
        assert state.pr_writes == 0
    provider.create_task.assert_not_called()
    state.failure = None
    if failure == "malformed":
        state.pr_body = original_body
    state.issue_body += "\nNew edit before recovery."
    # No copied artifact or reconstructed receipt: recover the registered root.
    assert plan_service.plan(project_root=repo, recover=root)
    assert state.native_runs == 1
    assert extract_plan_from_pr_body(state.pr_body) == _PLAN.split("\n", 1)[1].strip()
    assert "Current user edit." in state.issue_body
    assert "New edit before recovery." in state.issue_body
    assert state.issue_body.count("**Full plan**: PR #7") == 1
    assert not root.exists()
    assert str(root) not in _git(repo, "worktree", "list", "--porcelain")
    provider.create_task.assert_not_called()


@pytest.mark.parametrize("identity", ["renamed", "remote-only", "stale-closed", "stale-merged"])
def test_normal_replanning_uses_stable_live_pr_identity(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], identity: str
) -> None:
    repo, _config, provider, state = replanning
    if identity == "renamed":
        state.title = "fix: renamed task title"
    elif identity == "remote-only":
        _git(repo, "update-ref", "refs/remotes/origin/" + _BRANCH, "HEAD")
        _git(repo, "branch", "-D", _BRANCH)
    else:
        # Local branch has no open PR; discover another open head by issue ID.
        state.branch = "feat/42-live-old-title"
        state.stale_state = "CLOSED" if identity == "stale-closed" else "MERGED"
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert extract_plan_from_pr_body(state.pr_body) == _PLAN.split("\n", 1)[1].strip()
    assert state.pr_title == state.title
    assert set(state.edited_prs) == {"7"}
    assert state.issue_body.count("**Full plan**: PR #7") == 1
    provider.create_task.assert_not_called()


@pytest.mark.parametrize("body", ["", "User prose without any managed plan.\n"])
def test_normal_replanning_adds_missing_plan_without_reconstructing_user_body(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], body: str
) -> None:
    repo, _config, _provider, state = replanning
    state.pr_body = body
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert extract_plan_from_pr_body(state.pr_body) == _PLAN.split("\n", 1)[1].strip()
    if body:
        assert state.pr_body.startswith(body)
    else:
        assert state.pr_body.startswith("Implements #42\n")


def test_replanning_rejects_oversized_replacement_without_trimming_unrelated_usage(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
) -> None:
    repo, _config, _provider, state = replanning
    state.plan_markdown += "\n" + "x" * 65_536
    state.pr_body += (
        "\n<!-- wade:impl-usage:start -->\n### Session 1\nKeep old usage.\n"
        "### Session 2\nKeep recent usage.\n<!-- wade:impl-usage:end -->"
    )
    original = state.pr_body
    assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.pr_body == original
    assert state.pr_writes == 0
    assert state.planning_root is not None and state.planning_root.exists()
    assert (state.planning_root / ".wade/plans/PLAN.md").read_text() == state.plan_markdown


@pytest.mark.parametrize("marker_case", ["ordinary", "outside-start", "inside-end"])
def test_replan_replacement_reaches_next_real_implementation_startup(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    marker_case: str,
) -> None:
    from wade.services.implementation_service import core

    repo, config, provider, state = replanning
    if marker_case == "outside-start":
        state.pr_body = "```markdown\n<!-- wade:plan:start -->\n```\n\n" + state.pr_body
    elif marker_case == "inside-end":
        state.plan_markdown += "\n\n```markdown\n<!-- wade:plan:end -->\n```\n"
    origin = repo.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], capture_output=True, check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main", _BRANCH)
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    old_root = state.planning_root
    assert old_root is not None and not old_root.exists()
    with (
        patch("wade.services.implementation_service.core.load_config", return_value=config),
        patch("wade.services.implementation_service.core.get_provider", return_value=provider),
    ):
        result = core.start(
            "42",
            project_root=repo,
            ai_tool="claude",
            model="test-model",
            ai_explicit=True,
            model_explicit=True,
            cd_only=True,
        )
    assert result.success
    from wade.git.worktree import list_worktrees

    implementation_root = next(Path(wt.path) for wt in list_worktrees(repo) if wt.branch == _BRANCH)
    materialized = (implementation_root / "PLAN.md").read_text()
    assert (
        materialized
        == "# Issue #42: fix: existing task\n\n"
        + state.plan_markdown.split("\n", 1)[1].strip()
        + "\n"
    )
    assert "Obsolete requirements." not in materialized
    assert state.native_runs == 1


def test_filesystem_fallback_failure_keeps_original_output(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tempfile

    from wade.git.repo import GitError

    repo, _config, _provider, state = replanning
    state.managed = False
    state.failure = "pr-read"
    monkeypatch.setattr(tempfile, "tempdir", str(repo.parent))
    with patch(
        "wade.git.worktree.create_detached_worktree",
        side_effect=GitError("fixture cannot create worktree"),
    ):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.planning_root is not None and state.planning_root.exists()
    assert (state.planning_root / ".wade/plans/PLAN.md").read_text() == _PLAN
    assert (
        state.planning_root / ".wade/plans/interactive-reviews.json"
    ).read_bytes() == state.original_review
    assert state.planning_root.parent == repo.parent
    assert state.pr_writes == 0


@pytest.mark.parametrize(
    "broken",
    [
        "```markdown\nUnclosed fence.",
        "<!-- wade:plan:start -->\nAgain.\n<!-- wade:plan:end -->",
        "<!-- wade:future:start -->\n<!-- wade:nested:start -->\n"
        "<!-- wade:future:end -->\n<!-- wade:nested:end -->",
    ],
)
def test_existing_structural_ambiguity_preserves_handoff_and_remote_body(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    broken: str,
) -> None:
    repo, _config, _provider, state = replanning
    state.pr_body += "\n\n" + broken
    original = state.pr_body
    assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.pr_writes == 0
    assert state.pr_body == original
    assert state.planning_root is not None and state.planning_root.exists()
    assert (
        state.planning_root / ".wade/plans/interactive-reviews.json"
    ).read_bytes() == state.original_review


@pytest.mark.parametrize("fenced", [False, True])
def test_new_plan_owned_marker_examples_require_fences(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    fenced: bool,
) -> None:
    repo, _config, _provider, state = replanning
    example = "<!-- wade:future:start -->\nExample.\n<!-- wade:future:end -->"
    state.plan_markdown += "\n\n" + ("```markdown\n" + example + "\n```" if fenced else example)
    original = state.pr_body
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude") is fenced
    if fenced:
        assert (
            extract_plan_from_pr_body(state.pr_body)
            == state.plan_markdown.split("\n", 1)[1].strip()
        )
    else:
        assert state.pr_writes == 0
        assert state.pr_body == original
        assert state.planning_root is not None and state.planning_root.exists()


@pytest.mark.parametrize("tail", ["\n\n", "\n\n**Full plan**: PR #70"])
def test_fresh_issue_link_preserves_bytes_and_distinguishes_other_pr_numbers(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    tail: str,
) -> None:
    repo, _config, _provider, state = replanning
    state.issue_body += tail
    expected_prefix = state.issue_body + "\nCurrent user edit."
    assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.issue_body.startswith(expected_prefix)
    assert "\n\n**Full plan**: PR #7" in state.issue_body


@pytest.mark.parametrize("confirmed", [False, True])
def test_renamed_task_retarget_guards_and_reconciles_the_selected_head(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    confirmed: bool,
) -> None:
    from wade.models.task import PlanFile, Task

    repo, config, provider, state = replanning
    state.title = "fix: renamed task"
    state.base = "develop"
    _git(repo, "branch", "develop")
    _git(repo, "branch", "release")
    feature = repo.parent / "inflight"
    _git(repo, "worktree", "add", str(feature), _BRANCH)
    (feature / ".wade").mkdir()
    pin = feature / ".wade/base_branch"
    pin.write_text("develop\n")
    artifact = repo.parent / "new-plan.md"
    artifact.write_text(_PLAN + "\n## Base Branch\nrelease\n")
    issue = Task(id="42", title=state.title, body=state.issue_body)
    with (
        patch("wade.services.plan_service.prompts.is_tty", return_value=confirmed),
        patch("wade.services.plan_service.prompts.confirm", return_value=confirmed),
        patch("wade.git.repo.push_branch") as push,
    ):
        result = plan_service._attach_plan_to_existing_issue(
            provider,
            config,
            issue,
            PlanFile.from_markdown(artifact),
            repo,
            refresh_existing_plan=True,
        )
    assert result is confirmed
    assert state.lookups == [_BRANCH]
    if confirmed:
        assert state.base == "release"
        assert pin.read_text() == "release\n"
        assert state.pr_title == state.title
        assert state.pr_writes == 1
        push.assert_called_once_with(repo, _BRANCH, force=True)
    else:
        assert state.base == "develop"
        assert pin.read_text() == "develop\n"
        assert state.pr_writes == 0
        push.assert_not_called()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_repeated_replanning_preserves_full_unrelated_body_and_source_offsets(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    newline: str,
    final_newline: bool,
) -> None:
    repo, _config, _provider, state = replanning
    before = "User intro.\n\n```markdown\n## Summary\n<!-- wade:plan:start -->\n```\n\n"
    after = (
        "\n\n## Human Requirements\nKeep exactly.\n"
        "<!-- wade:legacyPlan:start -->\n## Summary\nLegacy payload.\n"
        "<!-- wade:legacyPlan:end -->\n"
        "<!-- wade:deps:start -->\nDependencies.\n<!-- wade:deps:end -->"
    ) + ("\n" if final_newline else "")
    state.pr_body = (before + _PLAN_BLOCK + after).replace("\n", newline)
    expected = (
        before.replace("\n", newline)
        + (
            "<!-- wade:plan:start -->\n"
            + _PLAN.split("\n", 1)[1].strip()
            + "\n<!-- wade:plan:end -->"
        )
        + after.replace("\n", newline)
    )
    for _ in range(2):
        assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
        assert state.pr_body == expected
        assert state.issue_body.count("**Full plan**: PR #7") == 1
    assert state.native_runs == 2


@pytest.mark.parametrize("malformed", [False, True])
def test_generic_bootstrap_resume_keeps_existing_plan_without_refresh(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    malformed: bool,
) -> None:
    from wade.services.implementation_service.draft_pr import bootstrap_draft_pr

    repo, config, _provider, state = replanning
    if malformed:
        state.pr_body += "\n<!-- wade:plan:end -->"
    original = state.pr_body
    result = bootstrap_draft_pr("42", state.title, "Unexpected replacement.", config, repo)
    assert result == {"number": 7, "url": "https://example.test/pull/7"}
    assert state.pr_body == original
    assert state.pr_writes == 0


@pytest.mark.parametrize("failure", ["pr-read", "pr-write", "pr-title", "malformed"])
def test_failed_refresh_cannot_leave_retarget_and_worktree_pin_divergent(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], failure: str
) -> None:
    repo, _config, _provider, state = replanning
    state.title = "fix: renamed task"
    state.base = "develop"
    state.failure = failure
    state.plan_markdown += "\n## Base Branch\nrelease\n"
    if failure == "malformed":
        state.pr_body += "\n<!-- wade:plan:end -->"
    _git(repo, "branch", "develop")
    _git(repo, "branch", "release")
    feature = repo.parent / "inflight"
    _git(repo, "worktree", "add", str(feature), _BRANCH)
    (feature / ".wade").mkdir()
    pin = feature / ".wade/base_branch"
    pin.write_text("develop\n")
    with (
        patch("wade.services.plan_service.prompts.is_tty", return_value=True),
        patch("wade.services.plan_service.prompts.confirm", return_value=True),
        patch("wade.services.plan_service.prompts.select", return_value=0),
        patch("wade.git.repo.push_branch"),
    ):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    assert state.base == pin.read_text().strip()
    assert state.base == "develop"
    assert state.planning_root is not None and state.planning_root.exists()
    assert str(state.planning_root) in _git(repo, "worktree", "list", "--porcelain")
    assert (state.planning_root / ".wade/plans/interactive-reviews.json").read_bytes() == (
        state.original_review
    )


@pytest.mark.parametrize("fenced", [False, True])
def test_fresh_pr_creation_accepts_only_extractable_plan_ownership(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], fenced: bool
) -> None:
    repo, _config, _provider, state = replanning
    state.fresh_pr = True
    example = "<!-- wade:future:start -->\nExample.\n<!-- wade:future:end -->"
    state.plan_markdown += "\n\n" + ("```markdown\n" + example + "\n```" if fenced else example)
    with patch("wade.git.repo.push_branch") as push:
        assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude") is fenced
    if fenced:
        assert state.pr_creates == 1
        expected = state.plan_markdown.split("\n", 1)[1].strip()
        assert extract_plan_from_pr_body(state.pr_body) == expected
        assert state.planning_root is not None and not state.planning_root.exists()
        push.assert_called_once()
    else:
        assert state.pr_creates == 0
        push.assert_not_called()
        assert state.planning_root is not None and state.planning_root.exists()
        assert str(state.planning_root) in _git(repo, "worktree", "list", "--porcelain")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_existing_full_plan_link_with_crlf_is_preserved_without_duplicates(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], newline: str
) -> None:
    repo, _config, _provider, state = replanning
    state.issue_body = "User body." + newline + "**Full plan**: PR #7 \t" + newline
    original = state.issue_body
    for _ in range(2):
        assert plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
        assert state.issue_body.startswith(original)
        assert state.issue_body.count("**Full plan**: PR #7") == 1


# Strict test-local legacy field surface: these reproduce the 1.2.20 rejection
# without installing another WADE or loading private target-project artifacts.
_LegacyBundle = create_model(
    "LegacyPlanBundle",
    __config__=ConfigDict(extra="forbid"),
    **{
        name: (field.annotation, field)
        for name, field in PlanBundle.model_fields.items()
        if name != "knowledge_proposals"
    },
)
_LegacyState = create_model(
    "LegacyInteractivePlanState",
    __config__=ConfigDict(extra="forbid"),
    **{
        name: ((_LegacyBundle | None, None) if name == "bundle" else (field.annotation, field))
        for name, field in InteractivePlanState.model_fields.items()
        if name != "refresh_pending"
    },
)


def _owned_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in (root / ".wade").rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_schema_rejection_retains_registered_original_before_progress_and_recovers(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
) -> None:
    repo, _config, provider, state = replanning
    state.service_review = True
    original_collect = interactive.collect
    snapshots: dict[str, bytes] = {}

    def legacy_collect(root: Path) -> object:
        (root / ".wade/plans/raw-native-output.txt").write_text("Public synthetic native output.\n")
        snapshots.update(_owned_bytes(root))
        with patch.object(
            InteractivePlanState,
            "model_validate_json",
            side_effect=_LegacyState.model_validate_json,
        ):
            return original_collect(root)

    with patch.object(interactive, "collect", side_effect=legacy_collect):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None
    assert root.is_dir(), (
        "Schema rejection must not replace the registered owner with a salvage copy"
    )
    assert str(root) in _git(repo, "worktree", "list", "--porcelain")
    assert _owned_bytes(root) == snapshots
    assert not (root / ".wade/plans/handoff-progress.json").exists()
    provider.update_task.assert_not_called()
    provider.create_task.assert_not_called()
    provider.comment_on_task.assert_not_called()
    assert state.pr_writes == 0 and state.pr_creates == 0
    # Repeating incompatible recovery neither rewrites state nor cleans it up.
    for _ in range(2):
        with patch.object(
            InteractivePlanState,
            "model_validate_json",
            side_effect=_LegacyState.model_validate_json,
        ):
            assert not plan_service.plan(project_root=repo, recover=root)
        assert _owned_bytes(root) == snapshots
        assert str(root) in _git(repo, "worktree", "list", "--porcelain")
    # Use the same original launch binding and current service review evidence.
    # Missing progress is legitimate: compatible recovery initializes it safely.
    assert plan_service.plan(project_root=repo, recover=root)
    assert not root.exists()
    assert str(root) not in _git(repo, "worktree", "list", "--porcelain")
    assert state.pr_writes == 1
    provider.create_task.assert_not_called()


@pytest.mark.parametrize(
    "corruption",
    [
        "future-state",
        "future-version",
        "malformed-state",
        "malformed-reviews",
        "future-reviews-invalid-plan",
        "malformed-reviews-invalid-plan",
        "future-reviews-invalid-member-set",
    ],
)
@pytest.mark.parametrize("knowledge_enabled", [False, True])
def test_normal_collection_retains_bad_metadata_without_leaking_or_staging(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
    capsys: pytest.CaptureFixture[str],
    corruption: str,
    knowledge_enabled: bool,
) -> None:
    repo, config, provider, state = replanning
    state.service_review = True
    config.knowledge.enabled = knowledge_enabled
    if knowledge_enabled:
        state.plan_markdown = (
            "<!-- wade:plan-bundle:v2 -->\n```json\n"
            + json.dumps(
                {
                    "plans": [{"filename": "PLAN.md", "markdown": _PLAN, "depends_on": []}],
                    "knowledge_proposals": [],
                }
            )
            + "\n```"
        )
    snapshots: dict[str, bytes] = {}
    original_collect = interactive.collect

    def corrupt_collect(root: Path) -> object:
        path = (
            root
            / ".wade/plans"
            / (interactive.REVIEWS if "reviews" in corruption else interactive.STATE)
        )
        if corruption.startswith("malformed"):
            path.write_text('{"SECRET-JSON-KEY": SECRET-JSON-VALUE')
        else:
            payload = json.loads(path.read_text())
            if corruption == "future-version":
                payload["version"] = 999
            else:
                payload["SECRET-EXTRA-KEY"] = "SECRET-EXTRA-VALUE"
            path.write_text(json.dumps(payload))
        if corruption.endswith("invalid-plan"):
            (root / ".wade/plans/PLAN.md").write_text("Public plan without its required H1.\n")
        elif corruption.endswith("invalid-member-set"):
            (root / ".wade/plans/PLAN-extra.md").write_text(_PLAN)
        snapshots.update(_owned_bytes(root))
        return original_collect(root)

    with (
        patch.object(interactive, "collect", side_effect=corrupt_collect),
        patch.object(
            plan_service, "_prepare_plan_handoff", wraps=plan_service._prepare_plan_handoff
        ) as prepare,
        patch("wade.services.knowledge_proposals.stage_proposal") as stage,
    ):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None and root.exists()
    assert _owned_bytes(root) == snapshots
    assert str(root) in _git(repo, "worktree", "list", "--porcelain")
    assert not (root / ".wade/plans/handoff-progress.json").exists()
    prepare.assert_not_called()
    stage.assert_not_called()
    provider.update_task.assert_not_called()
    provider.create_task.assert_not_called()
    provider.comment_on_task.assert_not_called()
    assert state.pr_writes == 0 and state.pr_creates == 0
    output = capsys.readouterr()
    combined = output.out + output.err
    assert "SECRET" not in combined
    assert "Uncollectable" in combined
    assert "writer version unavailable" in combined
    assert "wade plan --recover" in combined
    if corruption.startswith("malformed"):
        assert "json_invalid" in combined
    elif corruption == "future-version":
        assert "literal_error" in combined
    else:
        assert "extra_forbidden" in combined


@pytest.mark.parametrize(
    "corruption",
    [
        "second-state",
        "missing-binding",
        "future-binding",
        "malformed-binding",
        "future-progress",
        "malformed-progress",
        "mismatched-progress",
    ],
)
def test_second_parent_collection_and_launch_progress_failures_retain_original(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], corruption: str
) -> None:
    repo, config, provider, state = replanning
    state.service_review = True
    original_collect = interactive.collect_with_state
    collections = 0
    snapshot: dict[str, bytes] = {}

    def corrupt_second(root: Path) -> object:
        nonlocal collections
        collections += 1
        if collections == 2:
            plans = root / ".wade/plans"
            if corruption == "second-state":
                path = plans / interactive.STATE
                payload = json.loads(path.read_bytes())
                payload["PRIVATE-KEY"] = "PRIVATE-VALUE"
                path.write_text(json.dumps(payload))
            elif corruption.endswith("binding"):
                path = plans / plan_service.HANDOFF_BINDING_FILE
                if corruption == "missing-binding":
                    path.unlink()
                elif corruption == "malformed-binding":
                    path.write_text('{"PRIVATE": PRIVATE')
                else:
                    payload = json.loads(path.read_bytes())
                    payload["PRIVATE-KEY"] = "PRIVATE-VALUE"
                    path.write_text(json.dumps(payload))
            else:
                # Only valid original launch metadata may initialize progress.
                current, _ = original_collect(root)
                progress = plan_service._ensure_handoff_progress(
                    root, current.session_id, None, config
                )
                path = plans / plan_service.HANDOFF_PROGRESS_FILE
                if corruption == "malformed-progress":
                    path.write_text('{"PRIVATE": PRIVATE')
                else:
                    payload = json.loads(progress.model_dump_json())
                    if corruption == "mismatched-progress":
                        payload["session_id"] = "different-session"
                    else:
                        payload["PRIVATE-KEY"] = "PRIVATE-VALUE"
                    path.write_text(json.dumps(payload))
            snapshot.update(_owned_bytes(root))
        return original_collect(root)

    with (
        patch.object(interactive, "collect_with_state", side_effect=corrupt_second),
        patch.object(plan_service, "_preserve_generated_plans") as salvage,
        patch.object(plan_service, "_remove_planning_worktree") as cleanup,
        patch.object(
            plan_service, "list_state_files_strict", side_effect=ValueError("PRIVATE")
        ) as enumeration,
        patch("wade.services.knowledge_proposals.stage_proposal") as stage,
    ):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None and root.exists()
    assert collections == 2
    assert _owned_bytes(root) == snapshot
    assert str(root) in _git(repo, "worktree", "list", "--porcelain")
    salvage.assert_not_called()
    cleanup.assert_not_called()
    enumeration.assert_not_called()
    stage.assert_not_called()
    assert not plan_service.plan(project_root=repo, recover=root)
    assert _owned_bytes(root) == snapshot
    assert root.exists()
    provider.update_task.assert_not_called()
    provider.create_task.assert_not_called()
    provider.comment_on_task.assert_not_called()
    assert state.pr_writes == 0 and state.pr_creates == 0


def test_retained_owner_explicit_refresh_requires_new_service_review_and_done(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState],
) -> None:
    from wade.models.interactive_plan import InteractivePlanReviews
    from wade.services.review_delegation_service import review_plan

    repo, _config, _provider, state = replanning
    state.service_review = True
    original_collect = interactive.collect

    def legacy_collect(root: Path) -> object:
        with patch.object(
            InteractivePlanState,
            "model_validate_json",
            side_effect=_LegacyState.model_validate_json,
        ):
            return original_collect(root)

    with patch.object(interactive, "collect", side_effect=legacy_collect):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None and root.exists()
    binding = (root / ".wade/plans/handoff-binding.json").read_bytes()
    plan = (root / ".wade/plans/PLAN.md").read_bytes()
    assert interactive.refresh_handoff(root) == "native"
    assert (root / ".wade/plans/handoff-binding.json").read_bytes() == binding
    assert (root / ".wade/plans/PLAN.md").read_bytes() == plan
    refreshed = InteractivePlanState.model_validate_json(
        (root / ".wade/plans" / interactive.STATE).read_bytes()
    )
    assert not refreshed.completed and refreshed.review_required
    assert (
        InteractivePlanReviews.model_validate_json(
            (root / ".wade/plans" / interactive.REVIEWS).read_bytes()
        ).plans
        == {}
    )
    assert not plan_service.plan(project_root=repo, recover=root)
    with pytest.raises(ValueError, match="Review is required"):
        interactive.complete(root)
    with patch(
        "wade.services.review_delegation_service.delegate",
        return_value=DelegationResult(
            success=True,
            feedback="No material actionable findings.",
            mode=DelegationMode.HEADLESS,
            exit_code=0,
        ),
    ):
        reviewed = review_plan(
            str(root / ".wade/plans/PLAN.md"),
            project_root=root,
            ai_tool="codex",
            model="gpt-6.1-sol",
            effort="high",
            mode="headless",
            permission_mode="default",
            sandbox=True,
        )
    assert reviewed.success
    assert not plan_service.plan(project_root=repo, recover=root)
    interactive.complete(root)
    assert plan_service.plan(project_root=repo, recover=root)
    assert not root.exists()
    assert state.pr_writes == 1


@pytest.mark.parametrize("owner_kind", ["copied", "attached"])
def test_retention_never_authorizes_copied_or_attached_owner(
    replanning: tuple[Path, ProjectConfig, MagicMock, RemoteState], owner_kind: str
) -> None:
    import shutil

    repo, _config, provider, state = replanning
    state.service_review = True
    original_collect = interactive.collect

    def legacy_collect(root: Path) -> object:
        with patch.object(
            InteractivePlanState,
            "model_validate_json",
            side_effect=_LegacyState.model_validate_json,
        ):
            return original_collect(root)

    with patch.object(interactive, "collect", side_effect=legacy_collect):
        assert not plan_service.plan(project_root=repo, issue_id="42", ai_tool="claude")
    root = state.planning_root
    assert root is not None and root.exists()
    snapshot = _owned_bytes(root)
    if owner_kind == "copied":
        recovery = repo.parent / "copied-owner"
        shutil.copytree(root / ".wade", recovery / ".wade")
    else:
        recovery = root
        _git(root, "checkout", "-b", "attached-planning-output")
    with patch.object(plan_service, "_remove_planning_worktree") as cleanup:
        assert not plan_service.plan(project_root=repo, recover=recovery)
    cleanup.assert_not_called()
    assert _owned_bytes(root) == snapshot
    assert _owned_bytes(recovery) == snapshot
    assert root.exists() and recovery.exists()
    provider.update_task.assert_not_called()
    provider.create_task.assert_not_called()
    provider.comment_on_task.assert_not_called()
    assert state.pr_writes == 0 and state.pr_creates == 0

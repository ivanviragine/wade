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
from pydantic import BaseModel, Field

from wade.models.config import (
    AICommandConfig,
    AIConfig,
    KnowledgeConfig,
    ProjectConfig,
    ProjectSettings,
)
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
    planning_root: Path | None = None
    failure: str | None = None
    native_runs: int = 0
    branch: str = _BRANCH
    title: str = "fix: existing task"
    pr_title: str = "fix: existing task"
    plan_markdown: str = _PLAN
    managed: bool = True
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
        if args[:2] == ("pr", "list"):
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

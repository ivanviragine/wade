"""Opaque ClickUp IDs across real CLI, provider, Git, and GitHub PR boundaries (#490).

Real ``wade`` subprocesses drive a real ``ClickUpProvider`` (HTTP transport only
is a stateful fixture), real disposable Git repositories/worktrees with a local
bare origin, and the stateful mock ``gh``. Branch resolution, bootstrap,
dependency persistence, review-cycle state and completion are never mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._clickup_support import make_task, read_log, read_state, seed_state, write_launcher
from tests.e2e._support import (
    MockGhCli,
    _find_mock_pr_number_by_head,
    _git,
    _init_origin_remote,
    _read_gh_log,
    _remote_has_branch,
    _run,
    _seed_mock_issue,
    _seed_mock_pr,
    _seed_mock_review_threads,
)

pytestmark = [pytest.mark.e2e_docker, pytest.mark.contract]

OPAQUE = "abc1234"
OPAQUE_TOKEN = "idv1_61626331323334"
CLICKUP_CONFIG = """
provider:
  name: clickup
  settings:
    list_id: list-1
    team_id: team-1
    space_id: space-1

done:
  require_review: false
"""


class ClickUp:
    """Paths and environment for one fixture ClickUp workspace."""

    def __init__(self, tmp_path: Path) -> None:
        self.state_path = tmp_path / "clickup.json"
        self.log_path = tmp_path / "clickup.log"
        self.env = {
            "WADE_MOCK_CLICKUP_STATE": str(self.state_path),
            "WADE_MOCK_CLICKUP_LOG": str(self.log_path),
            "CLICKUP_API_TOKEN": "nonsecret-test-token",
        }

    def state(self) -> dict[str, Any]:
        return read_state(self.state_path)

    def write(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state))

    def log(self) -> list[dict[str, Any]]:
        return read_log(self.log_path)

    def writes_to(self, task_id: str) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.log()
            if entry["method"] != "GET" and entry["parts"][2:4] == ["task", task_id]
        ]


@pytest.fixture
def clickup(e2e_repo: Path, mock_gh_cli: MockGhCli, tmp_path: Path) -> ClickUp:
    config = e2e_repo / ".wade.yml"
    config.write_text(config.read_text() + CLICKUP_CONFIG)
    _git(["add", ".wade.yml"], cwd=e2e_repo)
    _git(["commit", "-m", "test: select ClickUp and an explicit review policy"], cwd=e2e_repo)
    _init_origin_remote(e2e_repo)
    workspace = ClickUp(tmp_path)
    seed_state(workspace.state_path, {})
    # Import the fixture Codex binary for planning, then layer the ClickUp launcher.
    from tests.e2e.test_plan_contract import _install_fake_codex

    _install_fake_codex(mock_gh_cli["mock_bin"])
    write_launcher(mock_gh_cli["mock_bin"])
    return workspace


def _wade(args: list[str], cwd: Path, clickup: ClickUp, **env: str) -> Any:
    result = _run(args, cwd=cwd, env={**clickup.env, **env}, timeout=120)
    return result


def _ok(result: Any) -> Any:
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _gh_task_calls(gh: MockGhCli) -> list[list[str]]:
    """GitHub issue CRUD/closing operations — never allowed for ClickUp tasks."""
    return [call for call in _read_gh_log(gh["log_file"]) if call and call[0] == "issue"]


def _complete_work(worktree: Path, clickup: ClickUp, session: str, message: str) -> str:
    (worktree / "PR-SUMMARY.md").write_text("Preserve exact provider identity.\n")
    (worktree / "work.txt").write_text(message + "\n")
    _git(["add", "work.txt"], cwd=worktree)
    _git(["commit", "-m", f"fix: {message}"], cwd=worktree)
    _ok(_wade([session, "docs", "--not-needed", "Fixture change only"], worktree, clickup))
    return _git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


@pytest.mark.parametrize("closed_status", ["closed", "shipped"])
def test_plan_created_opaque_task_full_lifecycle_closes_original_clickup_id(
    e2e_repo: Path, mock_gh_cli: MockGhCli, clickup: ClickUp, closed_status: str
) -> None:
    """Plan → implement → title drift re-entry → done → remote-only review → merge/close."""
    state = clickup.state()
    state["statuses"] = [
        {"status": "to do", "type": "open"},
        {"status": "in progress", "type": "custom"},
        {"status": closed_status, "type": "closed"},
    ]
    clickup.write(state)

    # --- Planning: two opaque tasks, a declared dependency, and a tracking task.
    config = e2e_repo / ".wade.yml"
    config.write_text(
        config.read_text().replace("ai:\n", "ai:\n  review_plan:\n    enabled: false\n", 1)
    )
    _git(["commit", "-am", "test: explicit plan-review policy"], cwd=e2e_repo)
    _git(["push", "origin", "main"], cwd=e2e_repo)
    _ok(
        _wade(
            ["plan", "--ai", "codex", "--model", "gpt-5.4", "--yolo"],
            e2e_repo,
            clickup,
            WADE_MOCK_MULTI="1",
        )
    )
    tasks = clickup.state()["tasks"]
    assert set(tasks) == {"abc1234", "def5678", "parent789"}
    assert "**Depends on:** clickup:abc1234" in tasks["def5678"]["markdown_description"]
    assert "**Blocks:** clickup:def5678" in tasks["abc1234"]["markdown_description"]
    tracking = tasks["parent789"]
    assert tracking["name"] == "Tracking: clickup:abc1234, clickup:def5678"
    assert "- [ ] clickup:abc1234 — " in tracking["markdown_description"]
    assert "- [ ] clickup:def5678 — " in tracking["markdown_description"]
    gh_state = json.loads(mock_gh_cli["state_file"].read_text())
    heads = sorted(pr["head"] for pr in gh_state["prs"].values())
    assert heads == [
        f"feat/{OPAQUE_TOKEN}-feat-deterministic-native-plan",
        "feat/idv1_64656635363738-feat-deterministic-second-plan",
    ]
    branch = f"feat/{OPAQUE_TOKEN}-feat-deterministic-native-plan"
    pr = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch)
    draft_body = gh_state["prs"][pr]["body"]
    assert draft_body.startswith(
        f"Implements [clickup:{OPAQUE}](https://app.clickup.com/t/{OPAQUE})"
    )
    assert "#abc1234" not in draft_body and "Closes" not in draft_body

    # --- Implementation start materializes the planned draft PR's plan.
    first = _ok(_wade(["implement", OPAQUE, "--cd"], e2e_repo, clickup))
    worktree = Path(first.stdout.strip())
    assert _git(["branch", "--show-current"], cwd=worktree).stdout.strip() == branch
    plan_md = (worktree / "PLAN.md").read_text()
    assert plan_md.startswith(f"# Issue #{OPAQUE}: feat: deterministic native plan")
    assert "- Test it." in plan_md
    manifest = json.loads((worktree / ".wade/session/manifest.json").read_text())
    assert manifest["task_id"] == OPAQUE

    # --- Title drift: re-entry reuses the same task, branch, worktree and PR.
    state = clickup.state()
    state["tasks"][OPAQUE]["name"] = "feat: renamed without identity change"
    clickup.write(state)
    again = _ok(_wade(["implement", OPAQUE, "--cd"], e2e_repo, clickup))
    assert Path(again.stdout.strip()) == worktree
    gh_state = json.loads(mock_gh_cli["state_file"].read_text())
    assert len(gh_state["prs"]) == 2  # No duplicate draft for the renamed task.

    # --- Completion through the existing draft: provider-aware, non-GitHub closure line.
    _complete_work(worktree, clickup, "implementation-session", "first change")
    _ok(_wade(["implementation-session", "done", "--no-trigger-bots"], worktree, clickup))
    gh_state = json.loads(mock_gh_cli["state_file"].read_text())
    body = gh_state["prs"][pr]["body"]
    assert gh_state["prs"][pr]["isDraft"] is False
    assert gh_state["prs"][pr]["title"] == "feat: renamed without identity change"
    assert body.startswith(
        f"Part of clickup:parent789\nCompletes [clickup:{OPAQUE}](https://app.clickup.com/t/{OPAQUE})"
    )
    assert "Closes #" not in body and "Implements" not in body
    assert "<!-- wade:plan:start -->" in body  # Embedded plan preserved.
    assert _remote_has_branch(e2e_repo.parent / "origin.git", branch)

    # --- Ordinary remote-only review recovery: no local worktree or branch remains.
    _git(["worktree", "remove", "--force", str(worktree)], cwd=e2e_repo)
    _git(["branch", "-D", branch], cwd=e2e_repo)
    thread = {
        "id": "PRRT_opaque",
        "isResolved": False,
        "isOutdated": False,
        "comments": [
            {
                "author": "alice",
                "body": "Name the identity codec.",
                "path": "work.txt",
                "line": 1,
                "url": "https://example.com/review/opaque",
            }
        ],
    }
    _seed_mock_review_threads(mock_gh_cli["state_file"], pr, [thread])
    review = _ok(_wade(["review", "pr-comments", OPAQUE], e2e_repo, clickup, CODEX_CLI="1"))
    assert "Skipping AI launch" in review.stdout + review.stderr
    review_root = Path(
        next(
            line.split()[0]
            for line in _git(["worktree", "list"], cwd=e2e_repo).stdout.splitlines()
            if f"[{branch}]" in line
        )
    )
    cycle_file = review_root / ".wade/review-cycles" / f"review-cycle@{OPAQUE_TOKEN}.json"
    cycle = json.loads(cycle_file.read_text())
    assert cycle["issue_number"] == OPAQUE and cycle["pr_number"] == int(pr)
    baseline = cycle["baseline_commit"]

    # A refresh after an edit retains the pre-edit baseline and feedback history.
    _complete_work(review_root, clickup, "review-pr-comments-session", "address feedback")
    second = {**thread, "id": "PRRT_opaque_2"}
    second["comments"] = [{**thread["comments"][0], "body": "Also keep 0042 distinct."}]
    _seed_mock_review_threads(mock_gh_cli["state_file"], pr, [thread, second])
    _ok(_wade(["review", "pr-comments", OPAQUE], e2e_repo, clickup, CODEX_CLI="1"))
    refreshed = json.loads(cycle_file.read_text())
    assert refreshed["baseline_commit"] == baseline
    assert "Name the identity codec." in refreshed["feedback"]
    assert "Also keep 0042 distinct." in refreshed["feedback"]

    # PR review/fetch resolution by the exact opaque ID, then resolve the feedback.
    fetched = _ok(_wade(["review-pr-comments-session", "fetch", OPAQUE], review_root, clickup))
    assert "Name the identity codec." in fetched.stdout
    for thread_id in ("PRRT_opaque", "PRRT_opaque_2"):
        _ok(_wade(["review-pr-comments-session", "resolve", thread_id], review_root, clickup))
    (review_root / "PR-SUMMARY.md").write_text("Addressed review feedback.\n")
    _ok(
        _wade(
            ["review-pr-comments-session", "docs", "--not-needed", "Fixture change only"],
            review_root,
            clickup,
        )
    )
    _ok(_wade(["review-pr-comments-session", "done", "--no-trigger-bots"], review_root, clickup))
    assert not cycle_file.exists()

    # --- The existing merge menu closes the original ClickUp ID via its provider.
    assert clickup.state()["tasks"][OPAQUE]["status"]["type"] != "closed"
    _ok(_wade(["smart-start", OPAQUE], e2e_repo, clickup, WADE_MOCK_TTY_SELECT="Merge PR"))
    gh_state = json.loads(mock_gh_cli["state_file"].read_text())
    assert gh_state["prs"][pr]["state"] == "MERGED"
    closed = clickup.state()["tasks"][OPAQUE]["status"]
    assert closed == {"status": closed_status, "type": "closed"}
    status_writes = [
        entry["payload"] for entry in clickup.writes_to(OPAQUE) if "status" in entry["payload"]
    ]
    assert status_writes[-1] == {"status": closed_status}
    assert clickup.state()["tasks"]["def5678"]["status"]["type"] == "open"

    # GitHub served only PRs/reviews; it never touched a ClickUp task.
    assert _gh_task_calls(mock_gh_cli) == []
    assert len([c for c in _read_gh_log(mock_gh_cli["log_file"]) if c[:2] == ["pr", "create"]]) == 2


def test_numeric_external_id_fallback_completion_honors_no_close(
    e2e_repo: Path, mock_gh_cli: MockGhCli, clickup: ClickUp
) -> None:
    """A numeric ClickUp ID gets no GitHub closing keyword, and --no-close keeps it open."""
    seed_state(clickup.state_path, {"42": make_task("42", "fix: numeric external task")})
    _seed_mock_issue(mock_gh_cli["state_file"], 42, "Unrelated GitHub issue 42")
    first = _ok(_wade(["implement", "42", "--cd"], e2e_repo, clickup))
    worktree = Path(first.stdout.strip())
    branch = "feat/42-fix-numeric-external-task"
    assert _git(["branch", "--show-current"], cwd=worktree).stdout.strip() == branch

    # The draft disappeared (e.g. closed outside WADE): fallback creation must not
    # emit GitHub issue syntax for an external task.
    state = json.loads(mock_gh_cli["state_file"].read_text())
    state["prs"] = {}
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    _complete_work(worktree, clickup, "implementation-session", "numeric change")
    _ok(
        _wade(
            ["implementation-session", "done", "--no-close", "--no-trigger-bots"],
            worktree,
            clickup,
        )
    )
    pr = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch)
    body = json.loads(mock_gh_cli["state_file"].read_text())["prs"][pr]["body"]
    assert body.startswith("Implements [clickup:42](https://app.clickup.com/t/42)")
    assert "#42" not in body and "Closes" not in body and "Completes" not in body

    _ok(_wade(["smart-start", "42"], e2e_repo, clickup, WADE_MOCK_TTY_SELECT="Merge PR"))
    assert json.loads(mock_gh_cli["state_file"].read_text())["prs"][pr]["state"] == "MERGED"
    assert clickup.state()["tasks"]["42"]["status"]["type"] != "closed"
    assert not [e for e in clickup.writes_to("42") if e["payload"].get("status") == "closed"]
    gh_issue = json.loads(mock_gh_cli["state_file"].read_text())["issues"]["42"]
    assert gh_issue["state"] == "OPEN"
    assert _gh_task_calls(mock_gh_cli) == []


def test_stale_and_malformed_branches_never_select_another_task(
    e2e_repo: Path, mock_gh_cli: MockGhCli, clickup: ClickUp
) -> None:
    """Exact decoding: stale same-task, look-alike, and malformed branches are not adopted."""
    seed_state(
        clickup.state_path,
        {
            OPAQUE: make_task(OPAQUE, "fix: current title"),
            "0042": make_task("0042", "fix: leading zeros"),
            "xyz9999": make_task("xyz9999", "fix: unrelated task"),
        },
    )
    for name in ("feat/idv1_zz-malformed", "feat/abc1234-raw-opaque", "feat/42-not-0042"):
        _git(["branch", name, "main"], cwd=e2e_repo)
        _git(["push", "origin", name], cwd=e2e_repo)
    stale = f"feat/{OPAQUE_TOKEN}-old-title"
    _git(["branch", stale, "main"], cwd=e2e_repo)
    _git(["push", "origin", stale], cwd=e2e_repo)
    _seed_mock_pr(mock_gh_cli["state_file"], 7, head_branch=stale, pr_state="CLOSED")
    live = f"feat/{OPAQUE_TOKEN}-live-title"
    _git(["branch", live, "main"], cwd=e2e_repo)
    _git(["push", "origin", live], cwd=e2e_repo)
    _seed_mock_pr(
        mock_gh_cli["state_file"],
        8,
        head_branch=live,
        is_draft=True,
        body=(
            "Implements clickup:abc1234\n\n"
            "<!-- wade:plan:start -->\nLive plan.\n<!-- wade:plan:end -->"
        ),
    )

    opaque = _ok(_wade(["implement", OPAQUE, "--cd"], e2e_repo, clickup))
    worktree = Path(opaque.stdout.strip())
    assert _git(["branch", "--show-current"], cwd=worktree).stdout.strip() == live
    assert "Live plan." in (worktree / "PLAN.md").read_text()

    zeros = _ok(_wade(["implement", "0042", "--cd"], e2e_repo, clickup))
    zero_tree = Path(zeros.stdout.strip())
    assert (
        _git(["branch", "--show-current"], cwd=zero_tree).stdout.strip()
        == "feat/idv1_30303432-fix-leading-zeros"
    )
    assert len(json.loads(mock_gh_cli["state_file"].read_text())["prs"]) == 3

    # An explicit target must agree with the branch being finalized.
    _complete_work(zero_tree, clickup, "implementation-session", "zeros")
    # (``xyz9999`` exists in ClickUp but has no worktree, so done stays here.)
    writes_before = len(clickup.writes_to(OPAQUE)) + len(clickup.writes_to("0042"))
    refused = _wade(["implementation-session", "done", "xyz9999"], zero_tree, clickup)
    assert refused.returncode == 1
    assert "does not match branch" in refused.stdout + refused.stderr
    assert not [c for c in _read_gh_log(mock_gh_cli["log_file"]) if c[:2] == ["pr", "edit"]]
    assert len(clickup.writes_to(OPAQUE)) + len(clickup.writes_to("0042")) == writes_before
    assert not clickup.writes_to("xyz9999")
    assert not _remote_has_branch(e2e_repo.parent / "origin.git", "feat/idv1_30303432-x")
    assert _gh_task_calls(mock_gh_cli) == []


def test_failed_lookups_and_provider_errors_never_duplicate_or_mutate(
    e2e_repo: Path, mock_gh_cli: MockGhCli, clickup: ClickUp
) -> None:
    """Unknown PR state and provider outages refuse before any branch/PR/task mutation."""
    seed_state(clickup.state_path, {OPAQUE: make_task(OPAQUE, "fix: guarded lifecycle")})

    # Provider read failure: nothing is created anywhere.
    state = clickup.state()
    state["fail"] = [f"GET task/{OPAQUE}"]
    clickup.write(state)
    failed = _wade(["implement", OPAQUE, "--cd"], e2e_repo, clickup)
    assert failed.returncode == 1
    assert not _git(["branch", "--list", "feat/*"], cwd=e2e_repo).stdout.strip()
    state = clickup.state()
    state["fail"] = []
    clickup.write(state)

    # An incomplete open-PR listing cannot prove absence: no duplicate draft.
    for number in range(100, 1100):
        _seed_mock_pr(mock_gh_cli["state_file"], number, head_branch=f"other/{number}")
    incomplete = _wade(["implement", OPAQUE, "--cd"], e2e_repo, clickup)
    assert incomplete.returncode == 1, incomplete.stdout + incomplete.stderr
    assert "Could not list open PRs" in incomplete.stdout + incomplete.stderr
    assert not [c for c in _read_gh_log(mock_gh_cli["log_file"]) if c[:2] == ["pr", "create"]]
    assert not _remote_has_branch(
        e2e_repo.parent / "origin.git", f"feat/{OPAQUE_TOKEN}-fix-guarded-lifecycle"
    )
    assert not clickup.writes_to(OPAQUE)
    assert _gh_task_calls(mock_gh_cli) == []


def test_unknown_and_malformed_tracking_references_are_not_children(
    e2e_repo: Path, mock_gh_cli: MockGhCli, clickup: ClickUp
) -> None:
    """Tracking checklists decode only complete references of the provider namespace."""
    body = (
        "## Execution Plan\n\n"
        f"- [ ] clickup:{OPAQUE} — real child\n"
        "- [ ] clickup:zz%ZZ — malformed escape\n"
        "- [ ] #123abc — truncated look-alike\n"
        "- [ ] github:42 — other namespace\n"
    )
    seed_state(
        clickup.state_path,
        {
            "track1": make_task("track1", "Tracking: one child", body),
            OPAQUE: make_task(OPAQUE, "fix: tracked child"),
        },
    )
    result = _wade(["review", "batch", "track1", "--mode", "prompt"], e2e_repo, clickup)
    output = result.stdout + result.stderr
    reads = {entry["parts"][3] for entry in clickup.log() if entry["parts"][2:3] == ["task"]}
    # The complete legacy token ``#123abc`` is a ClickUp ID, never truncated to
    # ``123``; malformed escapes and other namespaces are not references at all.
    assert reads <= {"track1", OPAQUE, "123abc"}, output
    assert {OPAQUE, "123abc"} <= reads, output
    assert not reads & {"123", "42", "zz%ZZ", "zz"}
    assert _gh_task_calls(mock_gh_cli) == []

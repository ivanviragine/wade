"""Deterministic E2E contracts for planning workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e._support import (
    MockGhCli,
    _assert_gh_called_with,
    _gh_call_count_total,
    _init_origin_remote,
    _run,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


def _install_fake_codex(mock_bin: Path) -> None:
    """Speak native app-server wire output; the published collector is not mocked."""
    codex_script = mock_bin / "codex"
    codex_script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys
import json
import os
from pathlib import Path

if "--version" in sys.argv:
    print(os.environ.get("WADE_MOCK_CODEX_VERSION", "codex-cli 0.154.0"))
    sys.exit(0)
assert "app-server" in sys.argv
plan = "# feat: deterministic native plan\\n\\n## Complexity\\neasy\\n\\n## Tasks\\n- Test it.\\n"
plans = [{"filename": "PLAN-one.md", "markdown": plan}]
if os.environ.get("WADE_MOCK_MULTI"):
    plans.append({"filename": "PLAN-two.md", "markdown": plan.replace("native plan", "second plan"),
                  "depends_on": ["PLAN-one.md"]})
artifact = '<!-- wade:plan-bundle:v1 -->\\n```json\\n' + json.dumps(
    {"plans": plans, "knowledge_votes": []}) + '\\n```'
artifact = os.environ.get("WADE_MOCK_ARTIFACT", artifact)
def emit(message):
    print(json.dumps(message), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if "id" not in msg:
        continue
    result = {}
    if method == "collaborationMode/list":
        result = {"data": [{"mode": "plan", "name": "Plan"}]}
    elif method == "thread/start":
        p = msg["params"]
        assert p["sandbox"] == "workspace-write"
        assert p["approvalPolicy"] == "on-request"
        assert p["config"]["sandbox_workspace_write"] == {
            "network_access": False, "writable_roots": []}
        assert Path(p["cwd"]) == Path.cwd()
        result = {"thread": {"id": "native-thread"}, "model": "gpt-5.4"}
    elif method == "turn/start":
        assert msg["params"]["collaborationMode"]["mode"] == "plan"
        prompt = msg["params"]["input"][0]["text"]
        assert not prompt.startswith("/plan") and "WORKFLOW.md" in prompt
        result = {"turn": {"id": "native-turn"}}
    emit({"id": msg["id"], "result": result})
    if method == "turn/start":
        emit({"method": "item/completed", "params": {"threadId": "native-thread",
              "turnId": "native-turn", "item": {"type": "plan", "id": "native-artifact",
              "text": artifact}}})
        emit({"method": "turn/completed", "params": {"threadId": "native-thread",
              "turn": {"id": "native-turn", "status": "completed"}}})
""",
        encoding="utf-8",
    )
    codex_script.chmod(0o755)


def _disable_plan_review(repo: Path) -> None:
    """Explicit policy opt-out, not a fabricated prompt-mode review receipt."""
    config = repo / ".wade.yml"
    config.write_text(config.read_text() + "  review_plan:\n    enabled: false\n")


class TestPlanCommand:
    """Test `wade plan` deterministic workflow using mocked gh + fake AI."""

    def test_plan_creates_issue_and_draft_pr_from_generated_plan(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """plan should create issue/PR side effects from AI-generated plan files."""
        _init_origin_remote(e2e_repo)
        _install_fake_codex(mock_gh_cli["mock_bin"])
        _disable_plan_review(e2e_repo)

        result = _run(["plan", "--ai", "codex", "--model", "gpt-5.4"], cwd=e2e_repo)
        assert result.returncode == 0, result.stdout + result.stderr

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        issues = state_data.get("issues", {})
        prs = state_data.get("prs", {})
        assert isinstance(issues, dict)
        assert isinstance(prs, dict)
        assert len(issues) == 1, f"Expected 1 created issue, got: {issues!r}"
        assert len(prs) == 1, f"Expected 1 created PR, got: {prs!r}"

        issue = issues.get("1")
        assert isinstance(issue, dict)
        assert issue.get("title") == "feat: deterministic native plan"
        assert '"artifact_source": "protocol_event"' in issue["body"]
        assert "native-thread" in issue["body"]
        assert "token usage are unavailable" in issue["body"]
        labels = issue.get("labels", [])
        assert isinstance(labels, list)
        assert "feature-plan" in labels

        pr = prs.get("1")
        assert isinstance(pr, dict)
        assert bool(pr.get("isDraft")) is True
        assert pr.get("head"), f"Expected PR head branch in mock state: {pr!r}"

        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "create", "--title", "feat: deterministic native plan"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "create", "--draft"],
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "edit", "1", "--body-file"],
        )

    def test_native_bundle_creates_two_tasks_and_declared_dependency(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        _init_origin_remote(e2e_repo)
        _install_fake_codex(mock_gh_cli["mock_bin"])
        _disable_plan_review(e2e_repo)
        result = _run(
            ["plan", "--ai", "codex", "--model", "gpt-5.4", "--yolo"],
            cwd=e2e_repo,
            env={"WADE_MOCK_MULTI": "1"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        state = json.loads(mock_gh_cli["state_file"].read_text())
        assert len(state["prs"]) == 2
        assert len(state["issues"]) == 3  # Two tasks plus their dependency tracking issue.
        assert "#1" in state["issues"]["2"]["body"]
        assert "native-thread" in state["issues"]["1"]["body"]

    @pytest.mark.parametrize("version", ["unknown", "codex-cli 0.1.0"])
    def test_version_preflight_has_no_worktree_or_provider_mutations(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli, version: str
    ) -> None:
        _install_fake_codex(mock_gh_cli["mock_bin"])
        before = json.loads(mock_gh_cli["state_file"].read_text())
        result = _run(
            ["plan", "--ai", "codex", "--model", "gpt-5.4"],
            cwd=e2e_repo,
            env={"WADE_MOCK_CODEX_VERSION": version},
        )
        assert result.returncode == 1
        assert "preflight failed" in result.stderr
        assert json.loads(mock_gh_cli["state_file"].read_text()) == before
        assert not (e2e_repo.parent / ".worktrees").exists()

    def test_malformed_returned_artifact_creates_nothing_and_is_recoverable(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        _init_origin_remote(e2e_repo)
        _install_fake_codex(mock_gh_cli["mock_bin"])
        before = json.loads(mock_gh_cli["state_file"].read_text())
        result = _run(
            ["plan", "--ai", "codex", "--model", "gpt-5.4"],
            cwd=e2e_repo,
            env={"WADE_MOCK_ARTIFACT": "Not a valid WADE plan"},
        )
        assert result.returncode == 1
        state = json.loads(mock_gh_cli["state_file"].read_text())
        assert state["issues"] == before["issues"]
        assert state["prs"] == before["prs"]
        assert "Plan files:" in result.stdout + result.stderr

    def test_native_terminal_collection_requires_actual_input(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        binary = mock_gh_cli["mock_bin"] / "claude"
        binary.write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 9\necho "2.1.263"\n')
        binary.chmod(0o755)
        result = _run(["plan", "--ai", "claude", "--model", "claude-sonnet-4.6"], cwd=e2e_repo)
        assert result.returncode == 1
        assert "attached terminal" in result.stderr
        assert not (e2e_repo.parent / ".worktrees").exists()


class TestPlanSessionDoneCommand:
    """Test `wade plan-session done` validation behavior."""

    def test_plan_session_done_fails_for_invalid_plan_dir(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """plan-session done should fail when plan files are invalid."""
        invalid_dir = e2e_repo / "invalid-plans"
        invalid_dir.mkdir()
        (invalid_dir / "PLAN-bad.md").write_text("## Missing title heading\n", encoding="utf-8")

        before_calls = _gh_call_count_total(mock_gh_cli["log_file"])
        before_state = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        result = _run(["plan-session", "done", str(invalid_dir)], cwd=e2e_repo)
        assert result.returncode == 1
        assert "Plan validation failed" in result.stderr
        after_calls = _gh_call_count_total(mock_gh_cli["log_file"])
        after_state = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        assert after_calls == before_calls, "plan-session done validation should not invoke gh"
        assert after_state.get("issues", {}) == before_state.get("issues", {})
        assert after_state.get("prs", {}) == before_state.get("prs", {})

    def test_plan_session_done_succeeds_for_valid_plan_dir(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """plan-session done should pass when required sections are valid."""
        valid_dir = e2e_repo / "valid-plans"
        valid_dir.mkdir()
        (valid_dir / "PLAN-good.md").write_text(
            "\n".join(
                [
                    "# feat: valid plan",
                    "",
                    "## Complexity",
                    "easy",
                    "",
                    "## Tasks",
                    "- Add a deterministic test",
                    "",
                    "## Acceptance Criteria",
                    "- Validation passes",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        before_calls = _gh_call_count_total(mock_gh_cli["log_file"])
        before_state = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        result = _run(["plan-session", "done", str(valid_dir)], cwd=e2e_repo)
        assert result.returncode == 0
        assert "Plan validation passed" in result.stdout
        after_calls = _gh_call_count_total(mock_gh_cli["log_file"])
        after_state = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        assert after_calls == before_calls, "plan-session done validation should not invoke gh"
        assert after_state.get("issues", {}) == before_state.get("issues", {})
        assert after_state.get("prs", {}) == before_state.get("prs", {})


class TestPlanSandboxProfileContract:
    """`wade plan` exposes the sandbox profile on its CLI surface (#478).

    ``plan`` had no network flag before this change; it is a launch path like any
    other, so it must accept and document ``--sandbox``/``--no-sandbox``. Scope
    is deliberately the parsed surface: ``--help`` returns before resolution, and
    driving a real plan run here would need a stubbed AI launch and provider,
    duplicating coverage the unit suite already owns — sandbox resolution and
    launch forwarding in ``test_ai_resolution`` /
    ``test_implementation_launch_context``, and the profile-independent plan
    guard in ``test_bootstrap_allowlist``.
    """

    @pytest.mark.parametrize("flag", ["--sandbox", "--no-sandbox"])
    def test_plan_accepts_the_sandbox_flag(self, e2e_repo: Path, flag: str) -> None:
        result = _run(["plan", flag, "--help"], cwd=e2e_repo)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_issue_scoped_sandbox_recovery_argv_is_valid(self, e2e_repo: Path) -> None:
        """The recovery hint must use plan's ``--issue`` option, not an operand."""
        result = _run(["plan", "--issue", "42", "--no-sandbox", "--help"], cwd=e2e_repo)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_plan_help_documents_both_directions(self, e2e_repo: Path) -> None:
        result = _run(["plan", "--help"], cwd=e2e_repo)
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--sandbox" in combined
        assert "--no-sandbox" in combined

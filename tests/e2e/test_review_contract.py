"""Deterministic E2E contracts for review delegation workflows."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

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
    _seed_mock_pr_commit_pushed_at,
    _seed_mock_review_threads,
)

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


def _install_fake_claude(mock_bin: Path) -> None:
    """Install a deterministic fake `claude` binary into the mocked PATH."""
    claude_script = mock_bin / "claude"
    claude_script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


log_path = os.environ.get("WADE_FAKE_CLAUDE_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]))
        f.write("\\n")

argv = sys.argv[1:]
if "--print" in argv:
    print("FAKE REVIEW FEEDBACK")
    sys.exit(0)

prompt = next((arg for arg in argv if "Write your output to:" in arg), "")
match = re.search(r"Write your output to:\\s*(.+?)\\nWhen done, exit the session\\.", prompt, re.S)
if not match:
    print("Interactive prompt missing output path", file=sys.stderr)
    sys.exit(1)

output_path = Path(match.group(1).strip())
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text("FAKE REVIEW FEEDBACK\\n", encoding="utf-8")
print("FAKE REVIEW FEEDBACK")
sys.exit(0)
""",
        encoding="utf-8",
    )
    claude_script.chmod(0o755)


def _read_fake_claude_log(log_file: Path) -> list[list[str]]:
    """Read JSONL argv entries emitted by the fake claude binary."""
    if not log_file.exists():
        return []
    invocations: list[list[str]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        invocations.append(json.loads(line))
    return invocations


def _count_mock_prs(state_file: Path) -> int:
    """Return the number of PRs currently stored in mock gh state."""
    state = json.loads(state_file.read_text(encoding="utf-8"))
    prs = state.get("prs", {})
    assert isinstance(prs, dict)
    return len(prs)


def _flag_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def _assert_claude_headless_config(argv: list[str], expected_effort: str) -> None:
    model_value = _flag_value(argv, "--model")
    assert model_value, f"Expected --model argument in delegated invocation: {argv!r}"
    assert model_value.replace(".", "-") == "claude-haiku-4-5"

    effort_value = _flag_value(argv, "--effort")
    assert effort_value, f"Expected --effort argument in delegated invocation: {argv!r}"
    assert effort_value == expected_effort


def _assert_claude_interactive_config(argv: list[str]) -> None:
    assert "--print" not in argv
    assert argv, "Expected delegated interactive invocation arguments"
    prompt_arg = next((arg for arg in argv if "Write your output to:" in arg), None)
    assert prompt_arg is not None
    assert "When done, exit the session." in prompt_arg


def _set_review_enabled(e2e_repo: Path, config_key: str, enabled: bool) -> None:
    """Set ai.<config_key>.enabled in .wade.yml using structured YAML mutation."""
    config_path = e2e_repo / ".wade.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    ai_config = config.setdefault("ai", {})
    assert isinstance(ai_config, dict)
    cmd_config = ai_config.setdefault(config_key, {})
    assert isinstance(cmd_config, dict)
    cmd_config["enabled"] = enabled
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _remove_default_ai_tool(e2e_repo: Path) -> None:
    """Remove global/default review AI tool config from .wade.yml."""
    config_path = e2e_repo / ".wade.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    ai_config = config.setdefault("ai", {})
    assert isinstance(ai_config, dict)
    ai_config.pop("default_tool", None)
    for command_name in ("review_plan", "review_implementation", "review_batch"):
        cmd_config = ai_config.get(command_name)
        if isinstance(cmd_config, dict):
            cmd_config.pop("tool", None)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _bootstrap_review_target(
    e2e_repo: Path,
    mock_gh_cli: MockGhCli,
    issue_number: int,
    issue_title: str,
    branch_name: str,
) -> tuple[Path, str]:
    """Create issue + draft PR + worktree and return (worktree_path, pr_number)."""
    _seed_mock_issue(
        mock_gh_cli["state_file"],
        issue_number=issue_number,
        title=issue_title,
        body="## Tasks\n- Address review feedback\n",
    )
    _init_origin_remote(e2e_repo)

    start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
    assert start_result.returncode == 0
    worktree_path = Path(start_result.stdout.strip())
    assert worktree_path.is_dir()

    pr_number = _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)
    return worktree_path, pr_number


def _bootstrap_batch_review_targets(
    e2e_repo: Path,
    mock_gh_cli: MockGhCli,
    *,
    tracking_issue_number: int,
    child_specs: list[tuple[int, str, str]],
) -> str:
    """Create tracking and child issues for deterministic batch review tests.

    child_specs items are ``(issue_number, issue_title, branch_name)``.
    """
    checklist_lines = ["## Tasks"]
    _init_origin_remote(e2e_repo)
    for issue_number, issue_title, branch_name in child_specs:
        checklist_lines.append(f"- [ ] #{issue_number}")
        _seed_mock_issue(
            mock_gh_cli["state_file"],
            issue_number=issue_number,
            title=issue_title,
            body="## Tasks\n- Implement batch child\n",
        )
        start_result = _run(["implement", str(issue_number), "--cd"], cwd=e2e_repo)
        assert start_result.returncode == 0
        _find_mock_pr_number_by_head(mock_gh_cli["state_file"], branch_name)

    _seed_mock_issue(
        mock_gh_cli["state_file"],
        issue_number=tracking_issue_number,
        title=f"Batch tracking #{tracking_issue_number}",
        body="\n".join(checklist_lines) + "\n",
    )
    return str(tracking_issue_number)


class TestReviewPlanCommand:
    """Test `wade review plan` deterministic headless delegation contracts."""

    def test_review_plan_headless_runs_fake_ai(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review plan --mode headless should execute AI with model+effort flags."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"

        plan_file = e2e_repo / "PLAN-review.md"
        plan_file.write_text(
            "\n".join(
                [
                    "# Review contract plan",
                    "",
                    "## Tasks",
                    "- Validate deterministic review behavior",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run(
            [
                "review",
                "plan",
                str(plan_file),
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--model",
                "claude-haiku-4.5",
                "--effort",
                "high",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        assert "FAKE REVIEW FEEDBACK" in result.stdout

        invocations = _read_fake_claude_log(log_file)
        assert len(invocations) == 1
        argv = invocations[0]
        assert "--print" in argv
        _assert_claude_headless_config(argv, expected_effort="high")
        assert any("Review contract plan" in token for token in argv)

    def test_review_plan_disabled_skips_ai(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review plan should short-circuit when ai.review_plan.enabled is false."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"

        _set_review_enabled(e2e_repo, "review_plan", False)

        plan_file = e2e_repo / "PLAN-disabled.md"
        plan_file.write_text("# Disabled review plan\n", encoding="utf-8")

        result = _run(
            [
                "review",
                "plan",
                str(plan_file),
                "--mode",
                "headless",
                "--ai",
                "claude",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "Review skipped" in output
        assert "ai.review_plan.enabled" in output
        assert _read_fake_claude_log(log_file) == []

    def test_review_plan_prompt_without_ai_config_prints_prompt_only(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Prompt-mode plan review should not require AI config or invoke AI binaries."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"
        _remove_default_ai_tool(e2e_repo)

        plan_file = e2e_repo / "PLAN-prompt.md"
        plan_file.write_text("# Prompt review plan\n\nCheck self-review.\n", encoding="utf-8")

        result = _run(
            ["review", "plan", str(plan_file), "--mode", "prompt"],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 2
        output = result.stdout + result.stderr
        assert "Prompt review plan" in output
        assert "SELF-REVIEW" in output
        assert _read_fake_claude_log(log_file) == []


class TestReviewImplementationCommand:
    """Test `wade review implementation` deterministic headless contracts."""

    def test_review_implementation_headless_uses_staged_diff(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review implementation --staged should include git diff in AI prompt."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"

        app_file = e2e_repo / "src" / "app.py"
        app_file.write_text('print("hello from staged review test")\n', encoding="utf-8")
        _git(["add", str(app_file.relative_to(e2e_repo))], cwd=e2e_repo)

        result = _run(
            [
                "review",
                "implementation",
                "--staged",
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--model",
                "claude-haiku-4.5",
                "--effort",
                "medium",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        assert "FAKE REVIEW FEEDBACK" in result.stdout

        invocations = _read_fake_claude_log(log_file)
        assert len(invocations) == 1
        argv = invocations[0]
        assert "--print" in argv
        _assert_claude_headless_config(argv, expected_effort="medium")
        assert any("diff --git" in token for token in argv), (
            "Expected staged git diff content to be included in delegated prompt."
        )

    def test_review_implementation_no_diff_skips_ai(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review implementation with no changes should return success without AI call."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"

        result = _run(
            [
                "review",
                "implementation",
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--model",
                "claude-haiku-4.5",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        assert "No committed, staged, or unstaged changes to review" in (
            result.stdout + result.stderr
        )
        assert _read_fake_claude_log(log_file) == []

    def test_review_implementation_disabled_skips_ai(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review implementation should short-circuit when disabled in config."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"

        _set_review_enabled(e2e_repo, "review_implementation", False)

        app_file = e2e_repo / "src" / "app.py"
        app_file.write_text('print("should not be reviewed")\n', encoding="utf-8")
        _git(["add", str(app_file.relative_to(e2e_repo))], cwd=e2e_repo)

        result = _run(
            [
                "review",
                "implementation",
                "--staged",
                "--mode",
                "headless",
                "--ai",
                "claude",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "Review skipped" in output
        assert "ai.review_implementation.enabled" in output
        assert _read_fake_claude_log(log_file) == []

    def test_review_implementation_prompt_without_ai_config_prints_prompt_only(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Prompt-mode implementation review should not require AI config or invoke AI."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"
        _remove_default_ai_tool(e2e_repo)

        app_file = e2e_repo / "src" / "app.py"
        app_file.write_text('print("prompt-mode implementation review")\n', encoding="utf-8")
        _git(["add", str(app_file.relative_to(e2e_repo))], cwd=e2e_repo)

        result = _run(
            ["review", "implementation", "--staged", "--mode", "prompt"],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 2
        output = result.stdout + result.stderr
        assert "diff --git" in output
        assert "SELF-REVIEW" in output
        assert "--ack-self-review" in output
        reviews_dir = e2e_repo / ".wade/reviews"
        assert not list(reviews_dir.glob("review@code-review@*.json"))

        acknowledged = _run(
            ["review", "implementation", "--staged", "--ack-self-review"],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert acknowledged.returncode == 0
        assert "Self-review acknowledged" in acknowledged.stdout + acknowledged.stderr
        records = list(reviews_dir.glob("review@code-review@*.json"))
        assert len(records) == 1
        assert json.loads(records[0].read_text(encoding="utf-8"))["outcome"] == "reviewed"
        assert _read_fake_claude_log(log_file) == []


class TestInheritedSandboxReviewContract:
    """`wade review implementation` inside a sandboxed parent runtime (#480).

    Two "never launched" shapes are exercised, because they must not be reported
    the same way:

    - a **denial-shaped spawn failure** — a ``claude`` on PATH that resolves but
      cannot be executed, which is what an inherited sandbox looks like at the
      point of failure. This is the only shape that earns the confident,
      runtime-naming diagnosis.
    - a **capability refusal** — ``vscode`` exposes no headless mode, so the
      guard returns before any process exists. Nothing touched the OS, so the
      sandbox must not be blamed even when the parent is known to be sandboxed
      (#481 review).

    Both are reproducible without depending on which binaries happen to sit on
    the runner's PATH.
    """

    _SANDBOXED_PARENT: ClassVar[dict[str, str]] = {
        "CODEX_CLI": "1",
        "CODEX_SANDBOX": "seatbelt",
    }

    def _stage_a_change(self, e2e_repo: Path) -> None:
        app_file = e2e_repo / "src" / "app.py"
        app_file.write_text('print("inherited sandbox review")\n', encoding="utf-8")
        _git(["add", str(app_file.relative_to(e2e_repo))], cwd=e2e_repo)

    def _blocked_tool_env(self, mock_gh_cli: MockGhCli, tool: str = "claude") -> dict[str, str]:
        """Sandboxed parent + a tool that resolves on PATH but cannot be exec'd.

        Exec fails with ``EACCES``, so the failure text carries "permission
        denied" — the denial shape wade requires before attributing a failed
        review to the parent sandbox.

        PATH is narrowed to the mocked bin plus the directories this run genuinely
        needs (``wade`` itself and ``git``), because the exec search does *not*
        stop at the first candidate: with the ambient PATH intact, a real
        ``claude`` further along it would be found and actually run.
        """
        mock_bin = mock_gh_cli["mock_bin"]
        blocked = mock_bin / tool
        blocked.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        blocked.chmod(0o644)

        required = [str(mock_bin)]
        for binary in ("wade", "git"):
            resolved = shutil.which(binary)
            assert resolved is not None, f"{binary} must be on PATH for this contract to run"
            required.append(str(Path(resolved).parent))
        path = os.pathsep.join([*required, "/usr/bin", "/bin"])
        assert shutil.which(tool, path=path) is None, (
            f"an executable {tool} on the narrowed PATH would run for real"
        )
        return {**self._SANDBOXED_PARENT, "PATH": path}

    def _review(
        self,
        e2e_repo: Path,
        env: dict[str, str],
        *,
        ai: str = "vscode",
        sandbox: bool | None = None,
        staged: bool = True,
    ) -> str:
        args = [
            "review",
            "implementation",
            "--mode",
            "headless",
            "--ai",
            ai,
        ]
        if staged:
            args.insert(2, "--staged")
        if sandbox is True:
            args.append("--sandbox")
        elif sandbox is False:
            args.append("--no-sandbox")
        result = _run(
            args,
            cwd=e2e_repo,
            env=env,
        )
        return result.stdout + result.stderr

    def test_names_the_runtime_and_prints_a_runnable_command(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        self._stage_a_change(e2e_repo)
        env = self._blocked_tool_env(mock_gh_cli)

        output = " ".join(self._review(e2e_repo, env, ai="claude").split())

        # A bare EACCES is not enough to blame the known boundary; the fixture
        # deliberately makes the fake binary non-executable.
        assert "Codex CLI is sandboxed" in output
        assert "executable permissions or network configuration" in output
        assert "could not reach its own host credentials" not in output
        assert "no review-pass budget was consumed" in output

    def test_a_capability_refusal_is_not_blamed_on_the_sandbox(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """The parent is sandboxed, but this reviewer never reached the OS.

        ``vscode`` has no headless mode; wade refused before spawning anything.
        Attributing that to inaccessible host credentials would be a confident
        wrong cause and would hide the remediation that actually applies.
        """
        self._stage_a_change(e2e_repo)

        output = " ".join(self._review(e2e_repo, self._SANDBOXED_PARENT).split())

        assert "Codex CLI is sandboxed and the implementation review never started" not in output
        assert "Review did not complete, so no review-pass budget was consumed." in output

    def test_missing_tool_is_not_blamed_on_the_sandbox(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """A configuration error must stay on the generic remediation path."""
        self._stage_a_change(e2e_repo)

        output = " ".join(
            self._review(e2e_repo, self._SANDBOXED_PARENT, ai="not-a-real-tool").split()
        )

        assert "Codex CLI is sandboxed and the implementation review never started" not in output
        assert "Review did not complete, so no review-pass budget was consumed." in output

    def test_compatible_sandbox_request_is_not_told_to_relaunch(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """A ``sandbox=True`` request has no unrestricted-profile mismatch."""
        self._stage_a_change(e2e_repo)
        env = self._blocked_tool_env(mock_gh_cli, "codex")

        output = " ".join(self._review(e2e_repo, env, ai="codex", sandbox=True).split())

        assert "Codex CLI is sandboxed and the implementation review never started" not in output
        assert "Review did not complete, so no review-pass budget was consumed." in output

    def test_the_gate_stays_closed_with_an_unattempted_record(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        self._stage_a_change(e2e_repo)

        self._review(e2e_repo, self._SANDBOXED_PARENT)

        records = list((e2e_repo / ".wade/reviews").glob("review@code-review@*.json"))
        assert len(records) == 1
        payload = json.loads(records[0].read_text(encoding="utf-8"))
        assert payload["outcome"] == "unattempted"
        # The two properties `done` reads: neither certifies the commit nor
        # spends a review→fix cycle.
        assert payload["consumes_pass"] is False

    def test_an_unattempted_record_keeps_implementation_done_closed(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """An auditable launch failure must never certify the implementation commit."""
        issue_number = 480
        worktree_path, _ = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="fix: keep unattempted reviews out of done",
            branch_name="feat/480-fix-keep-unattempted-reviews-out-of-done",
        )
        (worktree_path / "PR-SUMMARY.md").write_text(
            "Exercise the unattempted-review completion gate.\n",
            encoding="utf-8",
        )
        (worktree_path / "implementation.txt").write_text("blocked review\n", encoding="utf-8")
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", "fix: preserve the unattempted review gate"], cwd=worktree_path)
        docs = _run(
            [
                "implementation-session",
                "docs",
                "--not-needed",
                "E2E fixture has no documentation impact",
            ],
            cwd=worktree_path,
        )
        assert docs.returncode == 0, docs.stdout + docs.stderr

        review_output = self._review(
            worktree_path,
            self._blocked_tool_env(mock_gh_cli),
            ai="claude",
            staged=False,
        )
        assert "no review-pass budget was consumed" in " ".join(review_output.split()).lower()
        records = list((worktree_path / ".wade/reviews").glob("review@code-review@*.json"))
        assert len(records) == 1
        assert json.loads(records[0].read_text(encoding="utf-8"))["outcome"] == "unattempted"

        done = _run(["implementation-session", "done"], cwd=worktree_path)
        output = " ".join((done.stdout + done.stderr).split())
        assert done.returncode != 0, output
        assert "Review has not run for the current commit" in output

    def test_an_unknown_parent_keeps_the_hedged_wording(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """Same failure, no sandbox signal — wade must not assert the cause."""
        self._stage_a_change(e2e_repo)

        output = " ".join(self._review(e2e_repo, {"CODEX_CLI": "1"}).split())

        assert "is sandboxed" not in output
        assert "Review did not complete, so no review-pass budget was consumed." in output


class TestReviewPrCommentsCommand:
    """Test `wade review pr-comments` deterministic contracts."""

    def test_review_pr_comments_actionable_threads_in_ai_env_skip_nested_launch(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review pr-comments should avoid nested AI launch when CODEX_CLI is detected."""
        issue_number = 51
        issue_title = "Address actionable review comments"
        branch_name = "feat/51-address-actionable-review-comments"
        worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title=issue_title,
            branch_name=branch_name,
        )

        _seed_mock_review_threads(
            mock_gh_cli["state_file"],
            pr_number=pr_number,
            threads=[
                {
                    "id": "PRRT_actionable",
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": [
                        {
                            "author": "alice",
                            "body": "Refactor this conditional.",
                            "path": "src/app.py",
                            "line": 4,
                            "url": "https://example.com/review/0",
                        }
                    ],
                }
            ],
        )

        result = _run(
            ["review", "pr-comments", str(issue_number)],
            cwd=e2e_repo,
            env={"CODEX_CLI": "1"},
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "Skipping AI launch: already inside AI session" in output
        assert str(worktree_path) in output
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["api", "graphql", "-F", f"pr={pr_number}"],
        )

    def test_review_pr_comments_fails_for_unknown_issue_without_graphql_side_effects(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review pr-comments should fail fast when the issue does not exist."""
        result = _run(["review", "pr-comments", "999"], cwd=e2e_repo)

        assert result.returncode != 0
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["issue", "view", "999"],
        )
        assert _count_gh_calls(mock_gh_cli["log_file"], ["api", "graphql"]) == 0

    def test_review_pr_comments_no_actionable_threads(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        """review pr-comments should return success when all threads are already resolved."""
        issue_number = 52
        issue_title = "Address review comments workflow"
        branch_name = "feat/52-address-review-comments-workflow"
        _worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title=issue_title,
            branch_name=branch_name,
        )

        _seed_mock_review_threads(
            mock_gh_cli["state_file"],
            pr_number=pr_number,
            threads=[
                {
                    "id": "PRRT_resolved",
                    "isResolved": True,
                    "isOutdated": False,
                    "comments": [
                        {
                            "author": "alice",
                            "body": "Already fixed",
                            "path": "src/app.py",
                            "line": 1,
                            "url": "https://example.com/review/1",
                        }
                    ],
                }
            ],
        )

        result = _run(["review", "pr-comments", str(issue_number)], cwd=e2e_repo)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "All review comments resolved" in output
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["api", "graphql", "-F", f"pr={pr_number}"],
        )

    def test_review_pr_comments_waits_for_expected_bot_within_window(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        """A commit with no bot review yet must NOT report all-clear (#448).

        With a commit pushed inside the arrival window and none of the default
        enabled bots having reviewed it, ``review pr-comments`` must surface the
        awaited bot rather than "All review comments resolved".
        """
        from datetime import UTC, datetime, timedelta

        issue_number = 53
        branch_name = "feat/53-await-the-review-bot"
        _worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="Await the review bot",
            branch_name=branch_name,
        )
        # Commit pushed ~3 min ago: past the 120s freshness grace, still well inside
        # the 300s default arrival window → the bots are AWAITING (blocking).
        pushed = (datetime.now(UTC) - timedelta(seconds=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed_mock_pr_commit_pushed_at(mock_gh_cli["state_file"], pr_number, pushed)

        result = _run(["review", "pr-comments", str(issue_number)], cwd=e2e_repo)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "All review comments resolved" not in output
        assert "coderabbit" in output

    def test_review_pr_comments_reports_missing_bot_after_window(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        """Past the arrival window, a never-arriving bot is reported, not swallowed (#448)."""
        from datetime import UTC, datetime, timedelta

        issue_number = 54
        branch_name = "feat/54-report-the-missing-bot"
        _worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="Report the missing bot",
            branch_name=branch_name,
        )
        # Commit pushed 20 min ago: past every default window (arrival 300s / ack
        # 900s) → the bots are MISSING, surfaced explicitly and no longer blocking.
        pushed = (datetime.now(UTC) - timedelta(seconds=1200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed_mock_pr_commit_pushed_at(mock_gh_cli["state_file"], pr_number, pushed)

        result = _run(["review", "pr-comments", str(issue_number)], cwd=e2e_repo)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "No review from" in output


class TestReviewBatchCommand:
    """Test `wade review batch` deterministic contracts."""

    def test_review_batch_defaults_to_interactive_mode(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review batch without --mode should launch the interactive AI lane by default."""
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"
        tracking_id = _bootstrap_batch_review_targets(
            e2e_repo,
            mock_gh_cli,
            tracking_issue_number=66,
            child_specs=[
                (64, "Interactive batch child one", "feat/64-interactive-batch-child-one"),
                (65, "Interactive batch child two", "feat/65-interactive-batch-child-two"),
            ],
        )

        result = _run(
            ["review", "batch", tracking_id],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "BATCH REVIEW COMPLETE" in output
        integration_pr_number = _find_mock_pr_number_by_head(
            mock_gh_cli["state_file"], "batch-review/66"
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "comment", integration_pr_number],
        )
        invocations = _read_fake_claude_log(log_file)
        assert len(invocations) == 1
        _assert_claude_interactive_config(invocations[0])

    def test_review_batch_disabled_skips_before_creating_integration_state(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        tracking_issue = 69
        tracking_id = _bootstrap_batch_review_targets(
            e2e_repo,
            mock_gh_cli,
            tracking_issue_number=tracking_issue,
            child_specs=[
                (67, "Disabled batch child one", "feat/67-disabled-batch-child-one"),
                (68, "Disabled batch child two", "feat/68-disabled-batch-child-two"),
            ],
        )
        _set_review_enabled(e2e_repo, "review_batch", False)
        pr_count_before = _count_mock_prs(mock_gh_cli["state_file"])

        result = _run(["review", "batch", tracking_id], cwd=e2e_repo)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "Review skipped" in output
        assert "ai.review_batch.enabled" in output
        assert _count_mock_prs(mock_gh_cli["state_file"]) == pr_count_before
        assert not _remote_has_branch(e2e_repo.parent / "origin.git", "batch-review/69")
        branch_listing = _git(["branch", "--list", "batch-review/69"], cwd=e2e_repo)
        assert branch_listing.stdout.strip() == ""

    def test_review_batch_prompt_without_ai_config_does_not_post_self_review_prompt_to_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"
        tracking_issue = 70
        tracking_id = _bootstrap_batch_review_targets(
            e2e_repo,
            mock_gh_cli,
            tracking_issue_number=tracking_issue,
            child_specs=[
                (71, "Batch child one", "feat/71-batch-child-one"),
                (72, "Batch child two", "feat/72-batch-child-two"),
            ],
        )
        _remove_default_ai_tool(e2e_repo)
        _git(["add", ".wade.yml"], cwd=e2e_repo)
        _git(["commit", "-m", "chore: remove default ai tool for test"], cwd=e2e_repo)

        result = _run(
            ["review", "batch", tracking_id, "--mode", "prompt"],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 2
        output = result.stdout + result.stderr
        assert "SELF-REVIEW" in output
        integration_pr_number = _find_mock_pr_number_by_head(
            mock_gh_cli["state_file"], "batch-review/70"
        )
        assert integration_pr_number
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment"]) == 0
        assert _read_fake_claude_log(log_file) == []

    def test_review_batch_headless_posts_ai_feedback_to_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        _install_fake_claude(mock_gh_cli["mock_bin"])
        log_file = e2e_repo / ".fake-claude-log.jsonl"
        tracking_id = _bootstrap_batch_review_targets(
            e2e_repo,
            mock_gh_cli,
            tracking_issue_number=80,
            child_specs=[
                (81, "Headless batch child one", "feat/81-headless-batch-child-one"),
                (82, "Headless batch child two", "feat/82-headless-batch-child-two"),
            ],
        )

        result = _run(
            [
                "review",
                "batch",
                tracking_id,
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--model",
                "claude-haiku-4.5",
                "--effort",
                "low",
            ],
            cwd=e2e_repo,
            env={"WADE_FAKE_CLAUDE_LOG": str(log_file)},
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "BATCH REVIEW COMPLETE" in output
        integration_pr_number = _find_mock_pr_number_by_head(
            mock_gh_cli["state_file"], "batch-review/80"
        )
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["pr", "comment", integration_pr_number],
        )
        invocations = _read_fake_claude_log(log_file)
        assert len(invocations) == 1
        argv = invocations[0]
        assert "--print" in argv
        _assert_claude_headless_config(argv, expected_effort="low")
        assert any("Tracking issue: #80" in token for token in argv)


class TestReviewTriggerCommand:
    """Deterministic contracts for `wade review trigger` (#431)."""

    def test_trigger_posts_all_enabled_bots(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        """With the default bot_review config, every bot's trigger is posted."""
        issue_number = 90
        _worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="Trigger all bots",
            branch_name="feat/90-trigger-all-bots",
        )

        result = _run(["review", "trigger", str(issue_number)], cwd=e2e_repo)

        assert result.returncode == 0
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment", pr_number]) == 3
        for body in ("@coderabbitai review", "@codex review", "bugbot run"):
            _assert_gh_called_with(
                mock_gh_cli["log_file"], ["pr", "comment", pr_number, "--body", body]
            )

    def test_trigger_dry_run_posts_nothing(self, e2e_repo: Path, mock_gh_cli: MockGhCli) -> None:
        issue_number = 91
        _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="Trigger dry run",
            branch_name="feat/91-trigger-dry-run",
        )

        result = _run(["review", "trigger", str(issue_number), "--dry-run"], cwd=e2e_repo)

        assert result.returncode == 0
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment"]) == 0

    def test_trigger_bot_subset_posts_only_named(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        issue_number = 92
        _worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title="Trigger subset",
            branch_name="feat/92-trigger-subset",
        )

        result = _run(["review", "trigger", str(issue_number), "--bot", "codex"], cwd=e2e_repo)

        assert result.returncode == 0
        assert _count_gh_calls(mock_gh_cli["log_file"], ["pr", "comment", pr_number]) == 1
        _assert_gh_called_with(
            mock_gh_cli["log_file"], ["pr", "comment", pr_number, "--body", "@codex review"]
        )


class TestReviewPrCommentsSessionCommands:
    """Test review-pr-comments-session deterministic contracts."""

    def test_fetch_outputs_structured_markdown(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        """fetch should print actionable comments grouped by file with thread IDs."""
        issue_number = 53
        issue_title = "Fetch review comment context"
        branch_name = "feat/53-fetch-review-comment-context"
        worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title=issue_title,
            branch_name=branch_name,
        )

        thread_id = "PRRT_fetch_1"
        _seed_mock_review_threads(
            mock_gh_cli["state_file"],
            pr_number=pr_number,
            threads=[
                {
                    "id": thread_id,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": [
                        {
                            "author": "alice",
                            "body": "Please improve error handling.",
                            "path": "src/app.py",
                            "line": 12,
                            "url": "https://example.com/review/2",
                        },
                        {
                            "author": "bob",
                            "body": "Agreed.",
                            "path": "src/app.py",
                            "line": 12,
                            "url": "https://example.com/review/3",
                        },
                    ],
                }
            ],
        )

        # Run from the session worktree: `fetch` enforces the same phase
        # readiness check as the rest of the lifecycle (#462), so the main
        # checkout is correctly rejected.
        result = _run(
            ["review-pr-comments-session", "fetch", str(issue_number)],
            cwd=worktree_path,
        )

        assert result.returncode == 0
        assert "# Review Comments to Address" in result.stdout
        assert f"`{thread_id}`" in result.stdout
        assert "`src/app.py:12`" in result.stdout
        assert "@alice" in result.stdout
        assert "@bob" in result.stdout
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["api", "graphql", "-F", f"pr={pr_number}"],
        )

    def test_resolve_marks_thread_resolved_via_graphql(
        self, e2e_repo: Path, mock_gh_cli: MockGhCli
    ) -> None:
        """resolve should call GraphQL mutation and persist resolved state in mock gh."""
        issue_number = 54
        issue_title = "Resolve review thread from session"
        branch_name = "feat/54-resolve-review-thread-from-session"
        worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title=issue_title,
            branch_name=branch_name,
        )

        thread_id = "PRRT_resolve_1"
        _seed_mock_review_threads(
            mock_gh_cli["state_file"],
            pr_number=pr_number,
            threads=[
                {
                    "id": thread_id,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": [
                        {
                            "author": "alice",
                            "body": "Nit: rename variable",
                            "path": "src/app.py",
                            "line": 7,
                        }
                    ],
                }
            ],
        )

        result = _run(
            ["review-pr-comments-session", "resolve", thread_id],
            cwd=worktree_path,
        )

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert re.search(rf"Thread\s+{re.escape(thread_id)}\s+resolved", output)
        _assert_gh_called_with(
            mock_gh_cli["log_file"],
            ["api", "graphql", "-f", f"threadId={thread_id}"],
        )

        state_data = json.loads(mock_gh_cli["state_file"].read_text(encoding="utf-8"))
        review_threads = state_data.get("review_threads", {})
        assert isinstance(review_threads, dict)
        threads = review_threads.get(str(pr_number), [])
        assert isinstance(threads, list)
        matched = [t for t in threads if isinstance(t, dict) and t.get("id") == thread_id]
        assert len(matched) == 1
        assert bool(matched[0].get("isResolved")) is True

    def test_done_updates_existing_draft_pr(
        self,
        e2e_repo: Path,
        mock_gh_cli: MockGhCli,
    ) -> None:
        """review-pr-comments-session done should push and update the draft PR path."""
        issue_number = 55
        issue_title = "fix: finalize review comments session"
        branch_name = "feat/55-fix-finalize-review-comments-session"
        worktree_path, pr_number = _bootstrap_review_target(
            e2e_repo=e2e_repo,
            mock_gh_cli=mock_gh_cli,
            issue_number=issue_number,
            issue_title=issue_title,
            branch_name=branch_name,
        )

        (worktree_path / "PR-SUMMARY.md").write_text(
            "Addressed review feedback and validated behavior.\n",
            encoding="utf-8",
        )
        (worktree_path / "review-fixes.txt").write_text(
            "resolved review comments\n",
            encoding="utf-8",
        )
        _git(["add", "-A"], cwd=worktree_path)
        _git(["commit", "-m", "fix: address review comments"], cwd=worktree_path)

        docs = _run(
            [
                "review-pr-comments-session",
                "docs",
                "--not-needed",
                "E2E fixture has no documentation impact",
            ],
            cwd=worktree_path,
        )
        assert docs.returncode == 0, docs.stdout + docs.stderr

        # --skip-review bypasses the review-ran gate; this contract exercises the
        # done→PR mechanics (thread gate + push + PR update), not the review gate.
        result = _run(["review-pr-comments-session", "done", "--skip-review"], cwd=worktree_path)

        assert result.returncode == 0
        origin_repo = e2e_repo.parent / "origin.git"
        assert _remote_has_branch(origin_repo, branch_name)
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

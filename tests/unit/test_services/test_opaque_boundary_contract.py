"""Opaque IDs remain data at parser, display and shell-command boundaries (#490)."""

import shlex
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from wade.cli.task import task_app
from wade.git.pr import PRLookup, PRRef
from wade.models.config import ProjectConfig, ProjectSettings, ProviderConfig
from wade.models.deps import DependencyEdge
from wade.models.task import PlanFile, Task, parse_task_refs
from wade.services.deps_service import apply_deps_to_issues, parse_deps_output
from wade.services.implementation_service.done import _gate_pr_title
from wade.services.implementation_service.lifecycle import _close_task_after_merge
from wade.services.plan_service import _base_retarget_is_safe
from wade.services.review_recovery import RecoveryFailure, RecoveryOutcome
from wade.services.review_service import _report_recovery_failure
from wade.ui.console import _theme, console


@pytest.mark.parametrize("task_id", ["a->b", "a`b", "`edge`", "#hash", "*", "1.", "[/]", "a,b"])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_dependency_endpoints_are_resolved_before_syntax(
    task_id: str, wrapped: bool, reverse: bool
) -> None:
    source, target = ("child", task_id) if reverse else (task_id, "child")
    edge = f"{source} -> {target}"
    if wrapped:
        edge = f"`` {edge} ``"
    edges = parse_deps_output(edge + " # reason", {task_id, "child", "a", "ab", "edge"})
    assert [(e.from_task, e.to_task, e.reason) for e in edges] == [(source, target, "reason")]


@pytest.mark.parametrize("width", [40, 80])
def test_created_task_hint_is_a_copyable_command(width: int) -> None:
    task_id = "[red]" + "x" * 45 + "'&"
    output = StringIO()
    with (
        patch(
            "wade.services.task_service.create_task",
            return_value=Task(id=task_id, title="fix: task"),
        ),
        patch.object(console, "out", Console(file=output, width=width, theme=_theme)),
    ):
        result = CliRunner().invoke(task_app, ["create", "--title", "fix: task", "--body", "Body"])
    assert result.exit_code == 0, result.output
    commands = [line for line in output.getvalue().splitlines() if line.startswith("wade ")]
    assert commands == [shlex.join(["wade", "implement", task_id])]
    assert shlex.split(commands[0]) == ["wade", "implement", task_id]


def test_unknown_backtick_id_cannot_become_a_different_task() -> None:
    assert parse_deps_output("a`b -> child", {"ab", "child"}) == []


@pytest.mark.parametrize("suffix", ["/b", ".b", ":b", "+b", "[b", "`b", "%ZZ", "é"])
def test_malformed_qualified_reference_never_selects_a_prefix(suffix: str) -> None:
    assert parse_task_refs(f"clickup:a{suffix}", "clickup") == []


def test_qualified_reference_sentence_and_link_delimiters() -> None:
    assert parse_task_refs(
        "See clickup:a. Then (`clickup:b`) and [clickup:c](url).", "clickup"
    ) == ["a", "b", "c"]


def test_ambiguous_compact_arrow_is_rejected() -> None:
    assert parse_deps_output("a->b->c", {"a", "b->c", "a->b", "c"}) == []
    edges = parse_deps_output("1->2\n`3` -> `4`", {"1", "2", "3", "4"})
    assert [(e.from_task, e.to_task) for e in edges] == [("1", "2"), ("3", "4")]


@pytest.mark.parametrize(
    "body",
    ["## Tasks\n- Work\n", "<!-- wade:deps:start -->\nunsafe", "x" * 66000],
    ids=["normal", "malformed", "over-budget"],
)
@pytest.mark.parametrize("task_id", ["[/]", "[red]"])
def test_dependency_write_result_and_diagnostics_are_literal(
    body: str, task_id: str, capsys
) -> None:
    provider = MagicMock()
    provider.read_task.return_value = Task(id=task_id, title="fix: task", body=body)
    updated = apply_deps_to_issues(
        provider,
        [task_id],
        [DependencyEdge(from_task="parent", to_task=task_id)],
        provider_name="clickup",
    )
    if "unsafe" in body:
        assert updated == 0
        provider.update_task.assert_not_called()
    else:
        assert updated == 1
        provider.update_task.assert_called_once()
        assert provider.update_task.call_args.args[0] == task_id
    output = capsys.readouterr()
    assert task_id in output.out + output.err


@pytest.mark.parametrize("read_fails", [False, True])
def test_title_gate_diagnostics_preserve_id(read_fails: bool, capsys) -> None:
    provider = MagicMock()
    provider.read_task.return_value = Task(id="[/]", title="unconventional")
    if read_fails:
        provider.read_task.side_effect = ValueError("[/] unavailable")
    assert (
        _gate_pr_title(ProjectConfig(provider=ProviderConfig(name="clickup")), provider, "[/]")
        is read_fails
    )
    output = capsys.readouterr()
    assert "[/]" in output.out + output.err


@pytest.mark.parametrize("width", [40, 80])
@pytest.mark.parametrize(
    "operation", ["review-recovery", "merge-close", "retarget", "removed-base"]
)
def test_recovery_and_retarget_commands_are_literal(
    tmp_path: Path, width: int, operation: str
) -> None:
    task_id = "[red]" + "x" * 45 + "'&"
    output = StringIO()
    config = ProjectConfig(
        project=ProjectSettings(main_branch="main"), provider=ProviderConfig(name="clickup")
    )
    with patch.object(console, "out", Console(file=output, width=width, theme=_theme)):
        if operation == "review-recovery":
            _report_recovery_failure(
                RecoveryOutcome(failure=RecoveryFailure.NO_BRANCH), task_id, tmp_path
            )
            expected = ["wade", "implement", task_id]
        elif operation == "merge-close":
            _close_task_after_merge(MagicMock(), task_id, None, None)
            expected = ["wade", "task", "close", task_id]
        else:
            plan = PlanFile(
                path=tmp_path / "plan.md",
                title="feat: task",
                body="Work",
                base_branch=None if operation == "removed-base" else "develop",
            )
            with (
                patch(
                    "wade.git.branch.make_branch_name",
                    return_value="feat/42-x",
                ),
                patch(
                    "wade.git.pr.get_pr_for_branch",
                    return_value=PRLookup(
                        found=True, pr=PRRef(number=9, url="u", state="OPEN", baseRefName="old")
                    ),
                ),
                patch("wade.services.plan_service._branch_work_in_flight", return_value=True),
                patch("wade.services.plan_service.prompts.is_tty", return_value=False),
            ):
                assert _base_retarget_is_safe(
                    config, Task(id=task_id, title="feat: task"), plan, tmp_path, yolo=True
                ) is (operation == "removed-base")
            expected = [
                "wade",
                "implement",
                task_id,
                "--base",
                "main" if operation == "removed-base" else "develop",
            ]
    commands = [line for line in output.getvalue().splitlines() if line.startswith("wade ")]
    assert commands == [shlex.join(expected)]
    assert shlex.split(commands[0]) == expected

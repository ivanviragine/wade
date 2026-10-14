"""Projects CLI validates complete overrides and delegates typed requests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from wade.cli.project import project_app
from wade.models.project import ProjectError, ProjectResult


@pytest.mark.parametrize("command", ["items", "rank", "pick", "sync"])
@pytest.mark.parametrize("terminal", ["dumb", "xterm-256color"])
def test_all_commands_discoverable(
    command: str, terminal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", terminal)
    monkeypatch.setenv("NO_COLOR", "1")
    if terminal == "dumb":
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    else:
        monkeypatch.setenv("FORCE_COLOR", "1")
    result = CliRunner().invoke(project_app, [command, "--help"])
    assert result.exit_code == 0
    assert "--json" in unstyle(result.stdout)


@pytest.mark.parametrize(
    "args",
    [
        ["items", "--owner", "partial"],
        ["items", "--sort", "effort"],
        ["items", "--kind", "garbage"],
        ["sync", "--apply", "--dry-run"],
    ],
)
def test_invalid_arguments_do_not_dispatch(args: list[str]) -> None:
    with patch("wade.cli.project.execute") as execute:
        result = CliRunner().invoke(project_app, args)
    assert result.exit_code == 1 and not execute.called and result.stdout == ""


def test_complete_target_override_and_error_stderr() -> None:
    with patch("wade.cli.project.execute", side_effect=ProjectError("missing board")) as execute:
        result = CliRunner().invoke(
            project_app,
            [
                "items",
                "--owner",
                "org",
                "--owner-kind",
                "organization",
                "--number",
                "7",
                "--hostname",
                "ghe.example.com",
                "--columns",
                "title,number",
                "--sort",
                "title:desc",
                "--filter",
                "title=A",
            ],
        )
    assert result.exit_code == 1 and result.stdout == "" and "missing board" in result.stderr
    request = execute.call_args.args[0]
    assert request.target.owner == "org" and request.target.number == 7
    assert request.target.hostname == "ghe.example.com" and request.columns == ["title", "number"]
    assert request.sort[0].direction == "desc" and request.filters == ["title=A"]


def rendered_result(title: str, *, failed: bool = False, raw: str = "plain") -> ProjectResult:
    return ProjectResult.model_validate(
        {
            "operation": "sync",
            "project": {
                "id": "BOARD",
                "owner": "org",
                "kind": "organization",
                "number": 1,
                "hostname": "github.com",
                "title": "Board",
                "url": "https://github.com/orgs/org/projects/1",
                "viewer_can_update": True,
            },
            "columns": ["title"],
            "rows": [
                {
                    "item": {
                        "id": "I1",
                        "project_id": "BOARD",
                        "kind": "Issue",
                        "repository": "org/repo",
                        "number": 1,
                    },
                    "values": {"title": title},
                    "contributions": [
                        {
                            "field": "factor",
                            "field_id": "FACTOR",
                            "raw": raw,
                            "normalized": 1,
                            "weight": 1,
                            "weighted": 1,
                        }
                    ],
                }
            ],
            "operations": [
                {
                    "item_id": "I1",
                    "identity": "org/repo:Issue:1",
                    "field_id": "SCORE",
                    "field": "score",
                    "old": None,
                    "new": 100,
                    "status": "confirmed",
                },
                {
                    "item_id": "I1",
                    "identity": "org/repo:Issue:1",
                    "field_id": "RANK",
                    "field": "rank",
                    "old": None,
                    "new": 1,
                    "status": "failed" if failed else "confirmed",
                    "message": "Permission denied [red] for rank" if failed else None,
                },
            ],
            "visibility": "Visible board",
            "success": not failed,
        }
    )


@pytest.mark.parametrize(
    "title,raw", [("Handle [/bold]", "plain"), ("Keep [red] literal", "plain"), ("Task", "[/bold]")]
)
def test_human_tables_preserve_external_text_and_confirmed_writes(title: str, raw: str) -> None:
    with patch("wade.cli.project.execute", return_value=rendered_result(title, raw=raw)):
        result = CliRunner().invoke(project_app, ["sync", "--apply"])
    assert result.exit_code == 0
    plain = unstyle(result.stdout)
    assert title in plain and raw in plain and "confirmed" in plain


@pytest.mark.parametrize("json_output", [False, True])
def test_sync_failure_keeps_actionable_message_and_stream_contract(json_output: bool) -> None:
    import json

    with patch("wade.cli.project.execute", return_value=rendered_result("Task", failed=True)):
        result = CliRunner().invoke(
            project_app, ["sync", "--apply", *(["--json"] if json_output else [])]
        )
    assert result.exit_code == 1
    if json_output:
        assert (
            json.loads(result.stdout)["operations"][1]["message"]
            == "Permission denied [red] for rank"
        )
        assert result.stderr == ""
    else:
        assert "Permission denied [red] for rank" in result.stderr
        assert "confirmed" in unstyle(result.stdout) and "failed" in unstyle(result.stdout)

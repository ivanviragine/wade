"""Projects CLI validates complete overrides and delegates typed requests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wade.cli.project import project_app
from wade.models.project import ProjectError


@pytest.mark.parametrize("command", ["items", "rank", "pick", "sync"])
def test_all_commands_discoverable(command: str) -> None:
    result = CliRunner().invoke(project_app, [command, "--help"])
    assert result.exit_code == 0 and "--json" in result.stdout


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

"""Task-valued CLI arguments carry exact provider IDs, not integers (#490)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wade.cli.main import app
from wade.cli.task import task_app
from wade.models.delegation import DelegationMode, DelegationResult

runner = CliRunner()


@pytest.mark.parametrize(
    ("args", "service", "expected"),
    [
        (["read", "abc1234"], "wade.services.task_service.read_task", "abc1234"),
        (["read", "0042"], "wade.services.task_service.read_task", "0042"),
        (
            ["update", "ABC1234", "--comment", "x"],
            "wade.services.task_service.update_task",
            "ABC1234",
        ),
        (["close", "abc1234"], "wade.services.task_service.close_task", "abc1234"),
    ],
)
def test_task_commands_pass_the_raw_id(args: list[str], service: str, expected: str) -> None:
    with patch(service, return_value=True) as call:
        result = runner.invoke(task_app, args)
    assert result.exit_code == 0, result.output
    assert call.call_args.args[0] == expected


def test_task_deps_accepts_opaque_ids() -> None:
    graph = MagicMock()
    with patch("wade.services.deps_service.analyze_deps", return_value=graph) as analyze:
        result = runner.invoke(task_app, ["deps", "abc1234", "def5678"])
    assert result.exit_code == 0, result.output
    assert analyze.call_args.kwargs["issue_numbers"] == ["abc1234", "def5678"]


def test_implement_batch_accepts_opaque_ids() -> None:
    with patch("wade.services.implementation_service.batch", return_value=True) as batch:
        result = runner.invoke(app, ["implement-batch", "abc1234", "0042"])
    assert result.exit_code == 0, result.output
    assert batch.call_args.kwargs["issue_numbers"] == ["abc1234", "0042"]


def test_review_batch_accepts_an_opaque_tracking_id() -> None:
    with patch(
        "wade.services.batch_review_service.review_batch",
        return_value=DelegationResult(success=True, feedback="ok", mode=DelegationMode.HEADLESS),
    ) as review:
        result = runner.invoke(app, ["review", "batch", "parent789"])
    assert result.exit_code == 0, result.output
    assert review.call_args.args[0] == "parent789"


def test_chain_resume_hint_quotes_ids() -> None:
    from wade.models.session import ImplementResult

    with (
        patch(
            "wade.services.implementation_service.start",
            return_value=ImplementResult(success=True, branch_name="feat/idv1_61-x"),
        ),
        patch("wade.ui.prompts.confirm", return_value=False),
    ):
        result = runner.invoke(app, ["implement", "abc1234", "--chain", "def5678,x;y"])
    assert result.exit_code == 0, result.output
    assert "wade implement def5678 --base feat/idv1_61-x --chain 'x;y'" in " ".join(
        result.output.split()
    )

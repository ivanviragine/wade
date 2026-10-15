"""WADE owns task delivery through Crossby's public terminal startup events."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.ai_tools import InteractiveLaunchEvent, InteractiveLaunchEventKind, InteractiveSession
from crossby.models.ai import AIToolID, PlanSessionRequest

from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.permission import PermissionMode
from wade.models.workflow import SessionKind
from wade.services import interactive_plan_service as handoff
from wade.services.plan_service import run_interactive_planning_session
from wade.services.session_composition_service import compose_session


@pytest.mark.parametrize(
    "events",
    [
        ["plan_ready", "message_submitted"],
        [],
        ["plan_ready"],
        ["message_submitted"],
        ["plan_ready", "plan_ready"],
        ["plan_ready", "message_submitted", "message_submitted"],
        ["wrong_tool"],
    ],
)
def test_codex_deferred_delivery_and_completion_gate(tmp_path: Path, events: list[str]) -> None:
    config = ProjectConfig(ai=AIConfig(review_plan=AICommandConfig(enabled=False)))
    compose_session(tmp_path, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
    port = MagicMock(spec=InteractiveSession)

    def native_run(command, working_dir, **kwargs):
        assert working_dir == tmp_path
        assert command[0] == "codex"
        assert "app-server" not in command
        assert "/plan" not in command
        assert kwargs["prompt"] is None
        assert callable(kwargs["on_event"])
        for kind in events:
            kwargs["on_event"](
                InteractiveLaunchEvent(
                    kind=InteractiveLaunchEventKind.PLAN_READY
                    if kind == "wrong_tool"
                    else InteractiveLaunchEventKind(kind),
                    tool_id=AIToolID.CLAUDE if kind == "wrong_tool" else AIToolID.CODEX,
                ),
                port,
            )
        handoff.import_artifact(tmp_path, "# fix: native Codex plan\n\n## Complexity\neasy\n")
        handoff.complete(tmp_path)
        return 0

    with (
        patch("crossby.utils.versioning.detect_binary_version", return_value=(0, 154, 0)),
        patch("crossby.ai_tools.codex_terminal.run_terminal_plan", side_effect=native_run),
    ):
        arguments = dict(
            request=PlanSessionRequest(prompt="preflight", working_dir=tmp_path),
            permission_mode=PermissionMode.DEFAULT,
            config=config,
            issue_context="# Task\nPlan the greeting change.",
            session_bundle=str(tmp_path / ".wade/session"),
            source_root=str(tmp_path),
        )
        if events == ["plan_ready", "message_submitted"]:
            bundle, _ = run_interactive_planning_session(
                "codex", str(tmp_path / ".wade/plans"), **arguments
            )
            assert len(bundle.plans) == 1
            port.send_message.assert_called_once()
            prompt = port.send_message.call_args.args[0]
            assert prompt == (tmp_path / ".wade/plans/prompt.txt").read_text()
            assert "WORKFLOW.md" in prompt and not prompt.startswith("/plan")
        else:
            with pytest.raises(ValueError):
                run_interactive_planning_session(
                    "codex", str(tmp_path / ".wade/plans"), **arguments
                )
            assert port.send_message.call_count <= 1

"""Wiring tests for the four implementation-session launch sites (#423, #478).

Proves ``implementation_service.start()`` threads the absolute ``working_dir``,
the unconditional ``network_access`` pin, and the resolved ``sandbox`` profile
into every Codex launch/resume builder: inline initial (``launch``), inline
resume (``build_resume_command``), detached initial (``build_launch_command``),
and detached resume. A spy adapter whose build/launch call aborts the run (so the
heavy post-session flow never runs) lets each test assert only the kwargs the
call site passed.

Companion to ``test_codex_worktree_launch_context.py`` (which proves the real
crossby Codex adapter *acts* on those kwargs) and the review-side wiring tests in
``test_review_service.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import PRLookup, PRRef
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig, ProjectSettings
from wade.models.permission import PermissionMode
from wade.models.task import Task, TaskState
from wade.models.worktree import Worktree
from wade.services.implementation_service import start

_CORE = "wade.services.implementation_service.core"


@contextlib.contextmanager
def _driven_start(
    worktree_path: Path,
    *,
    sandbox_config: bool | None = None,
    implement_sandbox_config: bool | None = None,
    plan_sandbox_config: bool | None = None,
    detected_env: str | None = None,
    resolved_tool: str = "codex",
    terminal_launch_succeeds: bool = True,
) -> Iterator[MagicMock]:
    """Patch impl start()'s prerequisites and yield the spy AI adapter.

    Every heavy step between ``start()`` entry and the AI launch is stubbed so the
    flow reaches the launch site with ``resolved_tool="codex"``, an existing
    worktree at *worktree_path*, and the ``sandbox`` profile resolved from config.
    ``plan_sandbox_config`` sets ``ai.plan.sandbox`` — the *planner's* profile,
    which the plan-handoff predicate compares against this session's.
    """
    config = ProjectConfig(
        ai=AIConfig(
            sandbox=sandbox_config,
            implement=AICommandConfig(sandbox=implement_sandbox_config),
            plan=AICommandConfig(sandbox=plan_sandbox_config),
        ),
        project=ProjectSettings(main_branch="main", branch_prefix="feat"),
    )
    task = Task(id="42", title="Fix the widget", body="broken", state=TaskState.OPEN)
    spy = MagicMock()

    stack = contextlib.ExitStack()
    p = stack.enter_context
    p(patch(f"{_CORE}.load_config", return_value=config))
    p(patch(f"{_CORE}.get_provider", return_value=MagicMock()))
    p(patch(f"{_CORE}.git_repo.get_repo_root", return_value=worktree_path.parent / "repo"))
    p(patch(f"{_CORE}._resolve_task_target", return_value=task))
    p(
        patch(
            "wade.services.implementation_service.batch.check_tracking_issue_and_batch",
            return_value=None,
        )
    )
    p(patch(f"{_CORE}.git_branch.make_branch_name", return_value="feat/42-fix-the-widget"))
    p(
        patch(
            f"{_CORE}.git_pr.get_pr_for_branch",
            return_value=PRLookup(
                found=True,
                pr=PRRef(number=99, url="https://x", state="OPEN", isDraft=True),
            ),
        )
    )
    p(patch(f"{_CORE}.git_pr.get_pr_body", return_value="body"))
    p(patch(f"{_CORE}.extract_plan_from_pr_body", return_value="PLAN"))
    p(
        patch(
            f"{_CORE}.git_worktree.list_worktrees",
            return_value=[Worktree(path=str(worktree_path), branch="feat/42-fix-the-widget")],
        )
    )
    p(patch(f"{_CORE}.write_plan_md"))
    p(patch(f"{_CORE}.bootstrap_worktree"))
    p(patch(f"{_CORE}._catchup_and_surface_staleness", return_value=None))
    p(patch(f"{_CORE}.build_implementation_prompt", return_value="PROMPT"))
    p(patch(f"{_CORE}.resolve_ai_tool", return_value=resolved_tool))
    p(patch(f"{_CORE}.resolve_model", return_value=None))
    p(patch(f"{_CORE}.resolve_effort", return_value=None))
    p(
        patch(
            f"{_CORE}.confirm_ai_selection",
            return_value=(resolved_tool, None, None, PermissionMode.DEFAULT),
        )
    )
    p(patch(f"{_CORE}._detect_ai_cli_env", return_value=detected_env))
    p(patch(f"{_CORE}.set_terminal_title"))
    p(patch(f"{_CORE}.start_title_keeper"))
    p(patch(f"{_CORE}.stop_title_keeper"))
    p(patch(f"{_CORE}.deliver_prompt_if_needed"))
    terminal_launch = p(
        patch(f"{_CORE}.launch_in_new_terminal", return_value=terminal_launch_succeeds)
    )
    p(patch("crossby.ai_tools.AbstractAITool.get", return_value=spy))
    with stack:
        spy.terminal_launch = terminal_launch
        yield spy


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


class TestImplementationLaunchContext:
    """All four core.py launch sites thread working_dir + network pin + sandbox."""

    def test_inline_initial_launch(self, worktree: Path) -> None:
        # Inline initial → adapter.launch(); abort right after the call.
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42")
        kwargs = spy.launch.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["sandbox"] is True

    def test_inline_initial_sandbox_defaults_off(self, worktree: Path) -> None:
        # With config unset and no flag, the session launches unrestricted so
        # delegated child tools keep their own host credentials (#478).
        with _driven_start(worktree) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42")
        assert spy.launch.call_args.kwargs["sandbox"] is False

    def test_inline_initial_global_sandbox_enables_confinement(self, worktree: Path) -> None:
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42")
        assert spy.launch.call_args.kwargs["sandbox"] is True

    def test_inline_initial_flag_overrides_config(self, worktree: Path) -> None:
        # --no-sandbox (sandbox=False) overrides a config that turns it on.
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42", sandbox=False)
        assert spy.launch.call_args.kwargs["sandbox"] is False

    def test_network_access_is_pinned_on_regardless_of_profile(self, worktree: Path) -> None:
        """Network is no longer a wade-managed axis — it is on in both profiles.

        It still has to be *passed*: crossby's ``network_access`` defaults to
        ``False``, so a sandboxed launch that omitted it would pin
        ``sandbox_workspace_write.network_access=false`` and take the network away
        from the very lifecycle (fetch/push, ``gh``) that requires it.
        """
        for profile in (True, False):
            with _driven_start(worktree, sandbox_config=profile) as spy:
                spy.launch.side_effect = RuntimeError("stop-after-capture")
                start(target="42")
            assert spy.launch.call_args.kwargs["network_access"] is True

    def test_inline_resume(self, worktree: Path) -> None:
        # Inline resume → build_resume_command(); abort at run_with_transcript.
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.build_resume_command.return_value = ["codex", "resume"]
            with patch(
                "wade.utils.process.run_with_transcript",
                side_effect=RuntimeError("stop-after-capture"),
            ):
                start(target="42", resume_session_id="sess-1", resume_ai_tool="codex")
        kwargs = spy.build_resume_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["sandbox"] is True
        assert kwargs["network_access"] is True

    def test_detached_initial_launch(self, worktree: Path) -> None:
        # Detached initial → build_launch_command(); launch_in_new_terminal stubbed True.
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.build_launch_command.return_value = ["codex"]
            start(target="42", detach=True)
        kwargs = spy.build_launch_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["sandbox"] is True

    def test_detached_resume(self, worktree: Path) -> None:
        # Detached resume → build_resume_command(); _resume_autonomy_args appended.
        with _driven_start(worktree, sandbox_config=True) as spy:
            spy.build_resume_command.return_value = ["codex", "resume"]
            spy.build_launch_command.return_value = ["codex"]
            start(target="42", detach=True, resume_session_id="sess-1", resume_ai_tool="codex")
        kwargs = spy.build_resume_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["sandbox"] is True

    @pytest.mark.parametrize(
        ("global_sandbox", "implement_sandbox"),
        [(None, None), (True, False), (None, False)],
        ids=["default-policy", "global-policy", "implement-policy"],
    )
    def test_sandboxed_planner_handoff_launches_fresh_unrestricted_context(
        self,
        worktree: Path,
        global_sandbox: bool | None,
        implement_sandbox: bool | None,
    ) -> None:
        """A sandboxed Codex planner cannot become unrestricted in-process.

        The sandbox is a launch-time OS property, so honoring an unrestricted
        implementation profile requires a fresh detached process. Each case
        reaches ``implement`` unrestricted by a different route; the planner is
        pinned sandboxed throughout.
        """
        with _driven_start(
            worktree,
            sandbox_config=global_sandbox,
            implement_sandbox_config=implement_sandbox,
            plan_sandbox_config=True,
            detected_env="CODEX_CLI",
        ) as spy:
            spy.build_launch_command.return_value = ["codex"]

            result = start(target="42", plan_handoff=True)

        assert result.success is True
        assert spy.build_launch_command.call_args.kwargs["working_dir"] == worktree
        assert spy.terminal_launch.call_args.kwargs["wait_for_ready"] is True
        spy.launch.assert_not_called()

    def test_forced_handoff_branch_resolves_to_the_permissive_profile(self, worktree: Path) -> None:
        """Guards the polarity flip between the retired pin and this profile.

        The old predicate forced ``network_access=True`` — *permissive*. A
        token-level rename to ``sandbox=True`` would force *restrictive* and
        silently invert the intent, re-creating exactly the confinement the
        handoff exists to escape. The forced value must be ``False``.
        """
        with _driven_start(
            worktree,
            plan_sandbox_config=True,
            detected_env="CODEX_CLI",
        ) as spy:
            spy.build_launch_command.return_value = ["codex"]
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        assert spy.build_launch_command.call_args.kwargs["sandbox"] is False

    def test_matching_profiles_keep_the_nested_launch_guard(self, worktree: Path) -> None:
        """No profile mismatch, no reason to spawn a second process.

        Both sessions sandboxed: the planner's process already has the boundary
        implementation wants, so the ordinary nested-launch guard applies.
        """
        with _driven_start(
            worktree,
            sandbox_config=True,
            plan_sandbox_config=True,
            detected_env="CODEX_CLI",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_unrestricted_planner_keeps_the_nested_launch_guard(self, worktree: Path) -> None:
        """An already-unrestricted planner has nothing to escape.

        This is the default-config case: with ``ai.sandbox`` unset both sessions
        resolve unrestricted, so the retired network-pin handoff would have fired
        here and now correctly does not.
        """
        with _driven_start(worktree, detected_env="CODEX_CLI") as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_failed_fresh_codex_plan_handoff_fails_closed_with_restart_command(
        self, worktree: Path
    ) -> None:
        """The handoff must never fall through to an inline planner context."""
        with (
            _driven_start(
                worktree,
                plan_sandbox_config=True,
                detected_env="CODEX_CLI",
                terminal_launch_succeeds=False,
            ) as spy,
            patch(f"{_CORE}.console") as mock_console,
        ):
            spy.build_launch_command.return_value = ["codex"]

            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.launch.assert_not_called()
        mock_console.detail.assert_any_call("wade implement 42 --no-sandbox")

    @pytest.mark.parametrize("command_error", [ValueError, KeyError])
    def test_fresh_codex_plan_handoff_fails_closed_when_command_build_fails(
        self, worktree: Path, command_error: type[Exception]
    ) -> None:
        """A builder failure must not degrade to an unconfigured ``codex`` command."""
        with (
            _driven_start(
                worktree,
                plan_sandbox_config=True,
                detected_env="CODEX_CLI",
            ) as spy,
            patch(f"{_CORE}.console") as mock_console,
            patch(f"{_CORE}.launch_in_new_terminal") as mock_launch,
        ):
            spy.build_launch_command.side_effect = command_error("build failed")

            result = start(target="42", plan_handoff=True)

        assert result.success is False
        mock_launch.assert_not_called()
        spy.launch.assert_not_called()
        mock_console.detail.assert_any_call("wade implement 42 --no-sandbox")

    def test_non_handoff_codex_session_keeps_nested_launch_guard(self, worktree: Path) -> None:
        """Ordinary ``wade implement`` calls still do not recursively launch Codex."""
        with _driven_start(
            worktree,
            plan_sandbox_config=True,
            detected_env="CODEX_CLI",
        ) as spy:
            result = start(target="42")

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_non_codex_plan_handoff_keeps_nested_launch_guard(self, worktree: Path) -> None:
        """A plan handoff to another implementation tool cannot escape the guard."""
        with _driven_start(
            worktree,
            plan_sandbox_config=True,
            detected_env="CODEX_CLI",
            resolved_tool="claude",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_other_ai_session_marker_keeps_nested_launch_guard(self, worktree: Path) -> None:
        """Only a Codex-originated handoff gets the fresh-context exception."""
        with _driven_start(
            worktree,
            plan_sandbox_config=True,
            detected_env="CLAUDE_CODE",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

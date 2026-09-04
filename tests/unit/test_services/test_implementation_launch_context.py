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
from wade.utils.runtime_env import CODEX_SANDBOX_ENV

_CORE = "wade.services.implementation_service.core"
_UI_CONSOLE = "wade.ui.console.console"


@contextlib.contextmanager
def _driven_start(
    worktree_path: Path,
    *,
    sandbox_config: bool | None = None,
    implement_sandbox_config: bool | None = None,
    detected_env: str | None = None,
    resolved_tool: str = "codex",
    terminal_launch_succeeds: bool = True,
) -> Iterator[MagicMock]:
    """Patch impl start()'s prerequisites and yield the spy AI adapter.

    Every heavy step between ``start()`` entry and the AI launch is stubbed so the
    flow reaches the launch site with ``resolved_tool="codex"``, an existing
    worktree at *worktree_path*, and the ``sandbox`` profile resolved from config.
    ``detected_env`` controls the nested-session identity probe independently of
    the real sandbox signal inherited by this process.
    """
    config = ProjectConfig(
        ai=AIConfig(
            sandbox=sandbox_config,
            implement=AICommandConfig(sandbox=implement_sandbox_config),
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
        # Detached launches take the same unconditional network pin as inline
        # ones — omitting it here would silently strip fetch/push from every
        # `wade implement --detach` session.
        assert kwargs["network_access"] is True

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
    def test_sandboxed_parent_handoff_fails_closed_without_spawning_a_terminal(
        self,
        worktree: Path,
        global_sandbox: bool | None,
        implement_sandbox: bool | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A sandboxed parent cannot become unrestricted in-process.

        The sandbox is a launch-time OS property, so honoring an unrestricted
        implementation profile requires a host-terminal relaunch. A terminal
        launched by this process is still its descendant and cannot prove it
        escaped the OS boundary. Each case reaches ``implement`` unrestricted by
        a different route.
        """
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with _driven_start(
            worktree,
            sandbox_config=global_sandbox,
            implement_sandbox_config=implement_sandbox,
            detected_env="CODEX_CLI",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.build_launch_command.assert_not_called()
        spy.terminal_launch.assert_not_called()
        spy.launch.assert_not_called()

    def test_sandboxed_handoff_does_not_claim_a_child_can_become_permissive(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The host command, not a child flag, is the only permissive remedy."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with (
            _driven_start(worktree, detected_env="CODEX_CLI") as spy,
            patch(f"{_CORE}.console") as mock_console,
        ):
            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.build_launch_command.assert_not_called()
        mock_console.detail.assert_any_call(
            "wade implement 42 --base main --no-sandbox --ai codex --permission-mode default",
            markup=False,
        )

    def test_a_planner_profile_does_not_describe_the_enclosing_runtime(
        self, worktree: Path
    ) -> None:
        """The planner child has exited before this accepted handoff runs."""
        with _driven_start(worktree, detected_env="CODEX_CLI") as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_matching_profiles_keep_the_nested_launch_guard(self, worktree: Path) -> None:
        """No profile mismatch, no reason to spawn a second process.

        The enclosing runtime already has the boundary implementation wants, so
        the ordinary nested-launch guard applies.
        """
        with _driven_start(
            worktree,
            sandbox_config=True,
            detected_env="CODEX_CLI",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_unknown_parent_keeps_the_nested_launch_guard(self, worktree: Path) -> None:
        """An unassessed enclosing runtime has nothing proven to escape.

        This default-config case must not infer confinement from a planner that
        has already exited.
        """
        with _driven_start(worktree, detected_env="CODEX_CLI") as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_sandbox_signal_without_an_ai_session_does_not_force_a_fresh_handoff(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale sandbox variable in a host shell must not require a new terminal."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with _driven_start(worktree) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")

            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.build_launch_command.assert_not_called()
        spy.terminal_launch.assert_not_called()
        spy.launch.assert_called_once()

    def test_identityless_sandbox_signal_warns_before_an_inline_launch(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sandbox signal remains actionable when the identity marker is absent."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with (
            _driven_start(worktree) as spy,
            patch(_UI_CONSOLE) as mock_console,
        ):
            spy.launch.side_effect = RuntimeError("stop-after-capture")

            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.launch.assert_called_once()
        assert "enclosing AI runtime" in str(mock_console.warn.call_args_list)
        mock_console.detail.assert_any_call(
            "wade implement 42 --base main --no-sandbox --ai codex --permission-mode default",
            markup=False,
        )

    def test_sandboxed_plan_handoff_never_uses_terminal_readiness_as_proof(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The handoff must never fall through to an inline planner context."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with (
            _driven_start(worktree, detected_env="CODEX_CLI") as spy,
            patch(f"{_CORE}.console") as mock_console,
        ):
            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.terminal_launch.assert_not_called()
        spy.launch.assert_not_called()
        mock_console.detail.assert_any_call(
            "wade implement 42 --base main --no-sandbox --ai codex --permission-mode default",
            markup=False,
        )

    def test_sandboxed_plan_handoff_does_not_build_an_inheriting_command(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No child command can provide the host boundary the handoff needs."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with (
            _driven_start(
                worktree,
                detected_env="CODEX_CLI",
            ) as spy,
            patch(f"{_CORE}.console") as mock_console,
            patch(f"{_CORE}.launch_in_new_terminal") as mock_launch,
        ):
            result = start(target="42", plan_handoff=True)

        assert result.success is False
        mock_launch.assert_not_called()
        spy.launch.assert_not_called()
        mock_console.detail.assert_any_call(
            "wade implement 42 --base main --no-sandbox --ai codex --permission-mode default",
            markup=False,
        )

    def test_non_handoff_codex_session_keeps_nested_launch_guard(self, worktree: Path) -> None:
        """Ordinary ``wade implement`` calls still do not recursively launch Codex."""
        with _driven_start(
            worktree,
            detected_env="CODEX_CLI",
        ) as spy:
            result = start(target="42")

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_handoff_to_another_tool_also_requires_a_host_terminal(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The target tool is not what makes the sandbox inheritable (#480).

        This case previously took the nested-launch guard, because the predicate
        required the *implementation* tool to be Codex. A Claude session started
        from a sandboxed Codex runtime inherits that sandbox exactly as a Codex
        one would — and, being separately authenticated, is the case that loses
        its host login most visibly. It must not pretend a child terminal fixes
        that boundary.
        """
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with _driven_start(
            worktree,
            detected_env="CODEX_CLI",
            resolved_tool="claude",
        ) as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is False
        spy.build_launch_command.assert_not_called()
        spy.terminal_launch.assert_not_called()
        spy.launch.assert_not_called()

    def test_an_unidentified_parent_does_not_force_a_fresh_context(self, worktree: Path) -> None:
        """A tool identity without a sandbox signal is not confinement evidence."""
        with _driven_start(worktree, detected_env="CLAUDE_CODE") as spy:
            result = start(target="42", plan_handoff=True)

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()

    def test_nested_session_without_a_handoff_is_told_it_cannot_elevate(
        self, worktree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard prints the worktree path — and now says what it cannot deliver.

        Nothing launches here, so there is no failure to diagnose. But the agent
        is about to work in a worktree under a boundary the resolved profile said
        it would not have, and silence is what let that pass unnoticed (#480).
        """
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with (
            _driven_start(worktree, detected_env="CODEX_CLI") as spy,
            # The shared emitter resolves ``console`` lazily from ``wade.ui``, so
            # patching the importing module would miss it.
            patch(_UI_CONSOLE) as mock_console,
        ):
            result = start(target="42")

        assert result.success is True
        spy.build_launch_command.assert_not_called()
        spy.launch.assert_not_called()
        mock_console.detail.assert_any_call(
            "wade implement 42 --base main --no-sandbox --ai codex --permission-mode default",
            markup=False,
        )
        assert "Codex CLI" in str(mock_console.warn.call_args_list)

    def test_an_unknown_parent_assessment_says_nothing(self, worktree: Path) -> None:
        """No signal, no claim — wade does not assert a boundary it cannot see."""
        with (
            _driven_start(worktree, detected_env="CODEX_CLI") as spy,
            patch(_UI_CONSOLE) as mock_console,
        ):
            result = start(target="42")

        assert result.success is True
        spy.launch.assert_not_called()
        assert mock_console.warn.call_count == 0
        for call in mock_console.detail.call_args_list:
            assert "--no-sandbox" not in str(call)

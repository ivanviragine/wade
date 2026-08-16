"""Wiring tests for the four implementation-session launch sites (#423).

Proves ``implementation_service.start()`` threads the absolute ``working_dir``
and the resolved ``network_access`` into every Codex launch/resume builder:
inline initial (``launch``), inline resume (``build_resume_command``), detached
initial (``build_launch_command``), and detached resume. A spy adapter whose
build/launch call aborts the run (so the heavy post-session flow never runs)
lets each test assert only the kwargs the call site passed.

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
from wade.models.config import AIConfig, ProjectConfig, ProjectSettings
from wade.models.permission import PermissionMode
from wade.models.task import Task, TaskState
from wade.models.worktree import Worktree
from wade.services.implementation_service import start

_CORE = "wade.services.implementation_service.core"


@contextlib.contextmanager
def _driven_start(worktree_path: Path, *, network_access_config: bool) -> Iterator[MagicMock]:
    """Patch impl start()'s prerequisites and yield the spy AI adapter.

    Every heavy step between ``start()`` entry and the AI launch is stubbed so the
    flow reaches the launch site with ``resolved_tool="codex"``, an existing
    worktree at *worktree_path*, and ``network_access`` resolved from config.
    """
    config = ProjectConfig(
        ai=AIConfig(network_access=network_access_config),
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
    p(patch(f"{_CORE}.resolve_ai_tool", return_value="codex"))
    p(patch(f"{_CORE}.resolve_model", return_value=None))
    p(patch(f"{_CORE}.resolve_effort", return_value=None))
    p(
        patch(
            f"{_CORE}.confirm_ai_selection",
            return_value=("codex", None, None, PermissionMode.DEFAULT),
        )
    )
    p(patch(f"{_CORE}._detect_ai_cli_env", return_value=None))
    p(patch(f"{_CORE}.set_terminal_title"))
    p(patch(f"{_CORE}.start_title_keeper"))
    p(patch(f"{_CORE}.stop_title_keeper"))
    p(patch(f"{_CORE}.deliver_prompt_if_needed"))
    p(patch(f"{_CORE}.launch_in_new_terminal", return_value=True))
    p(patch("crossby.ai_tools.AbstractAITool.get", return_value=spy))
    with stack:
        yield spy


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


class TestImplementationLaunchContext:
    """All four core.py launch sites thread working_dir + resolved network_access."""

    def test_inline_initial_launch(self, worktree: Path) -> None:
        # Inline initial → adapter.launch(); abort right after the call.
        with _driven_start(worktree, network_access_config=True) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42")
        kwargs = spy.launch.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["network_access"] is True

    def test_inline_initial_network_defaults_off(self, worktree: Path) -> None:
        # With config off and no flag, the explicit pin resolves to False.
        with _driven_start(worktree, network_access_config=False) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42")
        assert spy.launch.call_args.kwargs["network_access"] is False

    def test_inline_initial_flag_overrides_config(self, worktree: Path) -> None:
        # --no-network (network_access=False) overrides config True.
        with _driven_start(worktree, network_access_config=True) as spy:
            spy.launch.side_effect = RuntimeError("stop-after-capture")
            start(target="42", network_access=False)
        assert spy.launch.call_args.kwargs["network_access"] is False

    def test_inline_resume(self, worktree: Path) -> None:
        # Inline resume → build_resume_command(); abort at run_with_transcript.
        with _driven_start(worktree, network_access_config=True) as spy:
            spy.build_resume_command.return_value = ["codex", "resume"]
            with patch(
                "wade.utils.process.run_with_transcript",
                side_effect=RuntimeError("stop-after-capture"),
            ):
                start(target="42", resume_session_id="sess-1", resume_ai_tool="codex")
        kwargs = spy.build_resume_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["network_access"] is True

    def test_detached_initial_launch(self, worktree: Path) -> None:
        # Detached initial → build_launch_command(); launch_in_new_terminal stubbed True.
        with _driven_start(worktree, network_access_config=True) as spy:
            spy.build_launch_command.return_value = ["codex"]
            start(target="42", detach=True)
        kwargs = spy.build_launch_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["network_access"] is True

    def test_detached_resume(self, worktree: Path) -> None:
        # Detached resume → build_resume_command(); _resume_autonomy_args appended.
        with _driven_start(worktree, network_access_config=True) as spy:
            spy.build_resume_command.return_value = ["codex", "resume"]
            spy.build_launch_command.return_value = ["codex"]
            start(target="42", detach=True, resume_session_id="sess-1", resume_ai_tool="codex")
        kwargs = spy.build_resume_command.call_args.kwargs
        assert kwargs["working_dir"] == worktree
        assert kwargs["network_access"] is True

"""Deterministic ID/file/worktree target precedence and runtime headings (#490)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.hooks.policies import _parse_issue_heading
from wade.models.config import ProjectConfig, ProjectSettings, ProviderConfig
from wade.models.task import Task
from wade.services.implementation_service._shared import WorktreeTarget, is_explicit_plan_path
from wade.services.implementation_service.bootstrap import write_plan_md
from wade.services.implementation_service.core import _resolve_task_target
from wade.services.implementation_service.done import done
from wade.utils.task_identity import encode_task_id


class TestExplicitPlanPaths:
    @pytest.mark.parametrize("target", ["./plan", "../plan", "~/plan", "/abs/plan.md", "./a/b"])
    def test_anchored_paths_select_plan_files(self, target: str) -> None:
        assert is_explicit_plan_path(target)

    @pytest.mark.parametrize(
        "target",
        ["42", "abc1234", "#42", "DEV-123", "plan", "a/b", "plans/a", ".plan", "~plan", "PLAN.md"],
    )
    def test_bare_tokens_are_task_ids(self, target: str) -> None:
        assert not is_explicit_plan_path(target)


class TestResolveTaskTarget:
    @pytest.mark.parametrize("read_fails", [False, True])
    @pytest.mark.parametrize("task_id", ["~clickup490", "~clickup490/task"])
    def test_tilde_id_never_expands_a_home_directory(self, task_id: str, read_fails: bool) -> None:
        provider = MagicMock()
        expected = Task(id=task_id, title="fix: tilde id")
        provider.read_task.return_value = expected
        if read_fails:
            provider.read_task.side_effect = ValueError("Task not found")
        config = ProjectConfig(provider=ProviderConfig(name="clickup"))
        with patch.object(Path, "expanduser", side_effect=RuntimeError("Unknown user")) as expand:
            task = _resolve_task_target(task_id, provider, config)
        assert task == (None if read_fails else expected)
        provider.read_task.assert_called_once_with(task_id)
        expand.assert_not_called()

    def test_explicit_home_path_expands_to_a_plan_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# feat: home plan\n")
        provider = MagicMock()
        created = Task(id="new1", title="feat: home plan")
        with patch(
            "wade.services.task_service.create_from_plan_file", return_value=created
        ) as create:
            assert _resolve_task_target("~/plan.md", provider, ProjectConfig()) == created
        create.assert_called_once_with(plan_path, config=ProjectConfig(), provider=provider)
        provider.read_task.assert_not_called()

    def test_bare_id_is_never_a_same_named_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "abc1234").write_text("# feat: not a plan\n")
        provider = MagicMock()
        provider.read_task.return_value = Task(id="abc1234", title="fix: opaque")
        with patch("wade.services.task_service.create_from_plan_file") as create:
            task = _resolve_task_target("abc1234", provider, ProjectConfig())
        assert task is not None and task.id == "abc1234"
        provider.read_task.assert_called_once_with("abc1234")
        create.assert_not_called()

    def test_explicit_path_selects_the_plan_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "abc1234").write_text("# feat: a plan\n")
        provider = MagicMock()
        created = Task(id="new1", title="feat: a plan")
        with patch(
            "wade.services.task_service.create_from_plan_file", return_value=created
        ) as create:
            task = _resolve_task_target("./abc1234", provider, ProjectConfig())
        assert task == created
        create.assert_called_once()
        provider.read_task.assert_not_called()

    def test_slash_bearing_id_is_never_a_same_named_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").write_text("# feat: not a plan\n")
        provider = MagicMock()
        provider.read_task.return_value = Task(id="a/b", title="fix: slash id")
        with patch("wade.services.task_service.create_from_plan_file") as create:
            task = _resolve_task_target("a/b", provider, ProjectConfig())
        assert task is not None and task.id == "a/b"
        provider.read_task.assert_called_once_with("a/b")
        create.assert_not_called()

    def test_only_one_hash_prefix_is_removed(self) -> None:
        provider = MagicMock()
        provider.read_task.return_value = Task(id="#x", title="fix: hash")
        _resolve_task_target("##x", provider, ProjectConfig())
        provider.read_task.assert_called_once_with("#x")


class TestRuntimeHeading:
    @pytest.mark.parametrize("task_id", ["42", "abc1234", "0042", "a:b"])
    def test_plan_md_heading_round_trips_through_the_hook_reader(
        self, tmp_path: Path, task_id: str
    ) -> None:
        path = write_plan_md(tmp_path, Task(id=task_id, title="fix: title: colon"), "Body")
        assert _parse_issue_heading(path) == (task_id, "fix: title: colon")

    def test_legacy_numeric_heading_is_still_read(self, tmp_path: Path) -> None:
        path = tmp_path / "PLAN.md"
        path.write_text("# Issue #42: Legacy title\n")
        assert _parse_issue_heading(path) == ("42", "Legacy title")


_DONE = "wade.services.implementation_service.done"


class TestDoneTargetAgreement:
    def _done(
        self, tmp_path: Path, target: str, branch: str, match: WorktreeTarget | None
    ) -> tuple[bool, MagicMock]:
        config = ProjectConfig(project=ProjectSettings(main_branch="main"))
        with (
            patch(f"{_DONE}.load_config", return_value=config),
            patch(f"{_DONE}.get_provider"),
            patch(f"{_DONE}.git_repo.get_repo_root", return_value=tmp_path),
            patch(f"{_DONE}.match_worktree_target", return_value=match),
            patch(f"{_DONE}.git_repo.is_worktree", return_value=True),
            patch(f"{_DONE}.git_repo.get_current_branch", return_value=branch),
            patch(f"{_DONE}.git_repo.is_clean", return_value=True),
            patch(f"{_DONE}._check_tracked_managed_files", return_value=[]),
            patch(f"{_DONE}.git_repo.rev_parse", return_value="deadbeef"),
            patch(f"{_DONE}._run_completion_gates", return_value=True) as gates,
            patch(f"{_DONE}._done_via_pr", return_value=True),
            patch(f"{_DONE}.git_repo.unskip_worktree_file"),
            patch(f"{_DONE}.strip_worktree_gitignore"),
        ):
            ok = done(target, project_root=tmp_path)
        return ok, gates

    def test_mismatched_target_refuses_before_any_gate(self, tmp_path: Path) -> None:
        ok, gates = self._done(tmp_path, "abc1234", "feat/idv1_30303432-zeros", None)
        assert ok is False
        gates.assert_not_called()

    def test_matching_opaque_target_proceeds(self, tmp_path: Path) -> None:
        branch = "feat/idv1_61626331323334-x"
        match = WorktreeTarget(path=tmp_path, branch=branch, task_id="abc1234", by_identity=True)
        ok, gates = self._done(tmp_path, "abc1234", branch, match)
        assert ok is True
        assert gates.call_args.kwargs["issue_number"] == "abc1234"

    @pytest.mark.parametrize("task_id", ["[/]", "[red]"])
    def test_markup_like_id_reaches_pr_finalization(
        self, tmp_path: Path, task_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        branch = "feat/idv1_" + task_id.encode("utf-8").hex() + "-x"
        match = WorktreeTarget(path=tmp_path, branch=branch, task_id=task_id, by_identity=True)
        ok, gates = self._done(tmp_path, task_id, branch, match)
        assert ok is True
        assert gates.call_args.kwargs["issue_number"] == task_id
        assert f"done {task_id}" in capsys.readouterr().out

    def test_worktree_name_target_uses_the_branch_identity(self, tmp_path: Path) -> None:
        branch = "feat/idv1_61626331323334-x"
        match = WorktreeTarget(path=tmp_path, branch=branch, task_id="abc1234")
        ok, gates = self._done(tmp_path, "feat-idv1_61626331323334-x", branch, match)
        assert ok is True
        assert gates.call_args.kwargs["issue_number"] == "abc1234"

    def test_non_task_worktree_name_refuses(self, tmp_path: Path) -> None:
        match = WorktreeTarget(path=tmp_path, branch="scratch")
        ok, gates = self._done(tmp_path, "scratch", "scratch", match)
        assert ok is False
        gates.assert_not_called()

    def test_slash_bearing_id_is_not_a_plan_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").write_text("# feat: not a plan\n")
        branch = f"feat/{encode_task_id('a/b')}-x"
        match = WorktreeTarget(path=tmp_path, branch=branch, task_id="a/b", by_identity=True)
        with patch("wade.services.task_service.create_from_plan_file") as create:
            ok, gates = self._done(tmp_path, "a/b", branch, match)
        create.assert_not_called()
        assert ok is True
        assert gates.call_args.kwargs["issue_number"] == "a/b"

"""Tests for smart_start service — PR-state-aware issue routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import PRLookup, PRRef
from wade.git.repo import GitError
from wade.models.review import PollOutcome
from wade.models.task import Task, TaskState
from wade.models.worktree import Worktree
from wade.services.smart_start import SmartStartContext, _run_review_pr_comments, smart_start


def _make_task() -> Task:
    return Task(id="42", title="Fix the widget", state=TaskState.OPEN, body="")


@pytest.fixture(autouse=True)
def _default_no_open_pr() -> object:
    """Default the issue→open-PR resolver to 'none' so routing tests don't spawn a
    real ``gh pr list`` in the not-open-PR path. The resume-by-open-PR test
    overrides this with its own patch.
    """
    with patch("wade.services.smart_start.find_open_pr_branch_for_issue", return_value=None):
        yield


class TestSmartStartNoPR:
    """When no PR exists, smart_start falls through to implement."""

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch(
        "wade.services.smart_start.git_pr.get_pr_for_branch",
        return_value=PRLookup(found=False),
    )
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_no_pr_runs_implement_task(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()


class TestSmartStartMergedPR:
    """When PR is merged, shows info message."""

    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_merged_pr_returns_true(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(found=True, pr=PRRef(number=99, state="MERGED"))

        result = smart_start("42", project_root=tmp_path)

        assert result is True


class TestSmartStartOpenPR:
    """When an open PR exists, presents a contextual menu."""

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_continue_working_runs_implement_task(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(
            found=True, pr=PRRef(number=99, state="OPEN", isDraft=False)
        )

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
    @patch("wade.ui.prompts.select")
    @patch("wade.ui.prompts.is_tty", return_value=False)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_non_tty_open_pr_defaults_without_prompting(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Non-interactive smart-start should take the default action explicitly."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(
            found=True,
            pr=PRRef(
                number=99,
                state="OPEN",
                isDraft=False,
                url="https://example/pr/99",
            ),
        )

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_select.assert_not_called()
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start._run_review_pr_comments", return_value=True)
    @patch("wade.ui.prompts.select", return_value=1)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_review_pr_comments_runs_review_service(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_review: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(
            found=True, pr=PRRef(number=99, state="OPEN", isDraft=False)
        )

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_review.assert_called_once()

    @patch("wade.services.smart_start._merge_pr")
    @patch("wade.ui.prompts.select", return_value=2)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_merge_calls_merge_pr(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_merge: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        provider = mock_get_provider.return_value
        provider.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(
            found=True, pr=PRRef(number=99, state="OPEN", isDraft=False)
        )

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_merge.assert_called_once()


class TestSmartStartDraftPR:
    """When a draft PR exists, shows context-aware menu based on worktree presence."""

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_draft_pr_no_worktree_shows_start_implementation(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Draft PR with no worktree shows 'Start implementation' and calls _run_implement_task."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(found=True, pr=PRRef(number=99, state="OPEN", isDraft=True))
        mock_worktrees.return_value = []

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Verify that prompts.select was called with a menu containing "Start implementation"
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        assert "Start implementation" in call_args[0][1]
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in call_args[0][1]
        assert "Merge PR" not in call_args[0][1]
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[Worktree(branch="feat/42-fix", path="/tmp/wt")],
    )
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_draft_pr_with_worktree_shows_continue_working(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Draft PR with worktree shows 'Continue working' and calls _run_implement_task."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(found=True, pr=PRRef(number=99, state="OPEN", isDraft=True))

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Verify that prompts.select was called with a menu containing "Continue working"
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        assert "Continue working" in call_args[0][1]
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in call_args[0][1]
        assert "Merge PR" not in call_args[0][1]
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_draft_pr_no_review_pr_comments_or_merge_options(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_is_tty: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Draft PR never shows 'Review PR comments' or 'Merge PR' options."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = PRLookup(found=True, pr=PRRef(number=99, state="OPEN", isDraft=True))
        mock_worktrees.return_value = []

        smart_start("42", project_root=tmp_path)

        # Verify that prompts.select was called
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        menu_options = call_args[0][1]

        # Verify that only 1 option is present (either "Start implementation" or "Continue working")
        assert len(menu_options) == 1
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in menu_options
        assert "Merge PR" not in menu_options


class TestSmartStartTrackingDetection:
    """When a tracking issue is detected, smart_start redirects to batch."""

    @patch("wade.services.smart_start.check_tracking_issue_and_batch", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_issue_calls_batch(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_confirm: MagicMock,
        mock_batch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracking issue with confirmed batch → calls batch() with child IDs."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169, #171",
            body="- [ ] #167\n- [ ] #169\n- [x] #171\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is True
        mock_batch.assert_called_once()
        assert mock_batch.call_args.args[0].id == "173"

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=False)
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_issue_declined_returns_false(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_confirm: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracking issue with declined batch → returns False, no batch call."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169",
            body="- [ ] #167\n- [ ] #169\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is False
        mock_implement.assert_not_called()

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch(
        "wade.services.smart_start.git_pr.get_pr_for_branch",
        return_value=PRLookup(found=False),
    )
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_regular_issue_not_affected(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Regular issue proceeds to implement, no batch redirect."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.check_tracking_issue_and_batch", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_forwards_ai_params(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_confirm: MagicMock,
        mock_batch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """AI tool/model/yolo parameters are forwarded to batch()."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167",
            body="- [ ] #167\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        smart_start(
            "173",
            ai_tool="claude",
            model="opus",
            project_root=tmp_path,
            ai_explicit=True,
            model_explicit=True,
            yolo=True,
        )

        call_kwargs = mock_batch.call_args.kwargs
        assert call_kwargs["ai_tool"] == "claude"
        assert call_kwargs["model"] == "opus"
        assert call_kwargs["ai_explicit"] is True
        assert call_kwargs["model_explicit"] is True
        assert call_kwargs["yolo"] is True

    @patch("wade.services.smart_start.check_tracking_issue_and_batch", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_issue_backticked_refs_calls_batch(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_confirm: MagicMock,
        mock_batch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Backticked checklist refs still redirect to batch with unchecked items only."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169, #171",
            body="- [ ] `#167`\n  - [ ] #169\n- [x] `#171`\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is True
        mock_batch.assert_called_once()
        assert mock_batch.call_args.args[0].id == "173"

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch(
        "wade.services.smart_start.git_pr.get_pr_for_branch",
        return_value=PRLookup(found=False),
    )
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/173-tracking")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_issue_no_unchecked_items_falls_through(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracking issue with all items checked → falls through to implement."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169",
            body="- [x] #167\n- [x] #169\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch(
        "wade.services.smart_start.git_pr.get_pr_for_branch",
        return_value=PRLookup(found=False),
    )
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/173-tracking")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_issue_uppercase_checked_items_falls_through(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracking issue with all items checked (uppercase X) → falls through to implement."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169",
            body="- [X] #167\n- [X] #169\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start.check_tracking_issue_and_batch", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_tracking_plain_refs_triggers_batch(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_confirm: MagicMock,
        mock_batch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracking issue with plain #N refs (no checklist) → batch with all refs."""
        mock_repo_root.return_value = tmp_path
        tracking_task = Task(
            id="173",
            title="Tracking: #167, #169",
            body="Children: #167, #169\n",
        )
        mock_get_provider.return_value.read_task.return_value = tracking_task

        result = smart_start("173", project_root=tmp_path)

        assert result is True
        mock_batch.assert_called_once()
        assert mock_batch.call_args.args[0].id == "173"

    @patch("wade.services.implementation_service.start")
    @patch(
        "wade.services.smart_start.git_pr.get_pr_for_branch",
        return_value=PRLookup(found=False),
    )
    @patch("wade.services.smart_start.resolve_task_branch", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_effort_forwarded_to_implement_task(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_do_start: MagicMock,
        tmp_path: Path,
    ) -> None:
        """effort/effort_explicit are forwarded on the normal implement path."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_do_start.return_value = MagicMock(success=True)

        smart_start("42", project_root=tmp_path, effort="high", effort_explicit=True)

        mock_do_start.assert_called_once()
        call_kwargs = mock_do_start.call_args.kwargs
        assert call_kwargs["effort"] == "high"
        assert call_kwargs["effort_explicit"] is True


class TestSmartStartResolvesByIssueNumber:
    """Regression: a retitled issue must still resolve to its existing branch/PR.

    ``done`` rewrites the issue title to add the required conventional-commit
    prefix, so re-running ``wade <id>`` would re-slugify to a *different* branch.
    smart_start must look up the PR by the branch the worktree actually lives on
    (resolved via the stable issue number) instead of the drifted reconstruction,
    or it would miss the live PR and send an in-flight task back to implement.
    """

    _SHARED = "wade.services.implementation_service._shared"

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_looks_up_pr_by_existing_branch_not_retitled_slug(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_config.return_value.project.branch_prefix = "feat"
        # The issue title was rewritten to conventional form after implement; its
        # frozen branch still carries the original slug.
        frozen_branch = "feat/104-llm-safety-gate-original-slug"
        mock_get_provider.return_value.read_task.return_value = Task(
            id="104",
            title="fix: LLM safety gate always returns 6 unique numbers",
            state=TaskState.OPEN,
            body="",
        )
        mock_pr.return_value = PRLookup(
            found=True, pr=PRRef(number=228, state="OPEN", isDraft=False)
        )

        with (
            patch(f"{self._SHARED}.git_repo.get_current_branch", return_value="main"),
            patch(
                f"{self._SHARED}.git_worktree.list_worktrees",
                return_value=[Worktree(path=str(tmp_path / "wt"), branch=frozen_branch)],
            ),
        ):
            # cd_only short-circuits the interactive menu once the open PR is found.
            result = smart_start("104", project_root=tmp_path, cd_only=True)

        assert result is True
        # The PR was resolved by the branch the worktree lives on — not the
        # branch a fresh slug of the new title would produce.
        mock_pr.assert_called_once_with(tmp_path, frozen_branch)

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_resumes_open_pr_when_resolved_branch_is_closed(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Ambiguity settled by PR state: a stale closed branch must not shadow the
        issue's live open PR on another branch (#428 review)."""
        mock_repo_root.return_value = tmp_path
        mock_config.return_value.project.branch_prefix = "feat"
        stale_branch = "feat/42-closed-pr-branch"
        live_branch = "feat/42-open-pr-branch"
        mock_get_provider.return_value.read_task.return_value = _make_task()

        def pr_by_branch(_repo_root: Path, branch: str) -> PRLookup:
            if branch == live_branch:
                return PRLookup(found=True, pr=PRRef(number=9, state="OPEN", isDraft=False))
            return PRLookup(found=True, pr=PRRef(number=7, state="CLOSED"))

        mock_pr.side_effect = pr_by_branch

        with (
            patch(f"{self._SHARED}.git_repo.get_current_branch", return_value="main"),
            patch(
                f"{self._SHARED}.git_worktree.list_worktrees",
                return_value=[Worktree(path=str(tmp_path / "wt"), branch=stale_branch)],
            ),
            patch(
                "wade.services.smart_start.find_open_pr_branch_for_issue",
                return_value=live_branch,
            ),
        ):
            result = smart_start("42", project_root=tmp_path, cd_only=True)

        assert result is True
        # Re-looked up the live open PR's branch rather than treating the issue as
        # closed and starting fresh.
        assert mock_pr.call_args_list[-1].args == (tmp_path, live_branch)


class TestRunReviewPrComments:
    """Tests for _run_review_pr_comments polling outcomes."""

    @patch("wade.services.review_service.start", return_value=True)
    @patch(
        "wade.services.review_service.poll_for_reviews",
        return_value=PollOutcome.COMMENTS_FOUND,
    )
    def test_comments_found_calls_review_start(
        self,
        mock_poll: MagicMock,
        mock_start: MagicMock,
        tmp_path: Path,
    ) -> None:
        """COMMENTS_FOUND outcome delegates to review_service.start(), forwarding
        the explicit --no-sandbox profile so the review session cannot re-resolve it."""
        provider = MagicMock()
        ctx = SmartStartContext(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            effort=None,
            effort_explicit=False,
            yolo=None,
            sandbox=False,
        )
        result = _run_review_pr_comments(
            ctx,
            repo_root=tmp_path,
            branch_name="feat/42-fix",
            pr_number=99,
            issue_number="42",
            worktree_path=None,
            provider=provider,
        )
        assert result is True
        mock_poll.assert_called_once_with(provider, tmp_path, 99, "feat/42-fix")
        mock_start.assert_called_once_with(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            ai_explicit=False,
            model_explicit=False,
            yolo=None,
            permission_mode=None,
            permission_mode_explicit=False,
            sandbox=False,
        )

    @patch("wade.services.review_service._quiet_next_steps_prompt")
    @patch(
        "wade.services.review_service.poll_for_reviews",
        return_value=PollOutcome.QUIET_TIMEOUT,
    )
    def test_quiet_timeout_calls_quiet_next_steps(
        self,
        mock_poll: MagicMock,
        mock_quiet: MagicMock,
        tmp_path: Path,
    ) -> None:
        """QUIET_TIMEOUT outcome calls _quiet_next_steps_prompt(), forwarding the
        explicit --sandbox profile so a later re-launch preserves the decision."""
        provider = MagicMock()
        ctx = SmartStartContext(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            effort=None,
            effort_explicit=False,
            yolo=None,
            sandbox=True,
        )
        result = _run_review_pr_comments(
            ctx,
            repo_root=tmp_path,
            branch_name="feat/42-fix",
            pr_number=99,
            issue_number="42",
            worktree_path=None,
            provider=provider,
        )
        assert result is True
        mock_quiet.assert_called_once_with(
            tmp_path,
            "feat/42-fix",
            "42",
            None,
            99,
            provider,
            ai_tool=None,
            model=None,
            detach=False,
            ai_explicit=False,
            model_explicit=False,
            permission_mode=None,
            permission_mode_explicit=False,
            sandbox=True,
        )


class TestSmartStartGitError:
    """When not in a git repo, falls through to implement."""

    @patch("wade.services.smart_start.SmartStartContext.run_implement", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root", side_effect=GitError("nope"))
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_git_error_falls_through(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()


class TestSmartStartContextCarriesSandbox:
    """The profile survives every route the numeric ``wade <N>`` shorthand takes.

    ``smart-start`` fans out to implement, batch, or review pr-comments. The
    profile lives on :class:`SmartStartContext` precisely so no route can drop
    it; these assert the field reaches each dispatch.
    """

    @staticmethod
    def _ctx(tmp_path: Path, sandbox: bool | None) -> SmartStartContext:
        return SmartStartContext(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            effort=None,
            effort_explicit=False,
            yolo=None,
            sandbox=sandbox,
        )

    @pytest.mark.parametrize("sandbox", [True, False, None])
    def test_run_implement_forwards_the_profile(self, tmp_path: Path, sandbox: bool | None) -> None:
        ctx = self._ctx(tmp_path, sandbox)
        with patch("wade.services.implementation_service.start") as mock_start:
            mock_start.return_value = MagicMock(success=True)
            assert ctx.run_implement() is True
        assert mock_start.call_args.kwargs["sandbox"] is sandbox

    def test_context_defaults_to_unset(self, tmp_path: Path) -> None:
        # Unset must stay ``None`` (not ``False``) so each route re-resolves from
        # config rather than freezing the terminal default at the shorthand.
        ctx = SmartStartContext(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            effort=None,
            effort_explicit=False,
            yolo=None,
        )
        assert ctx.sandbox is None

    @pytest.mark.parametrize("sandbox", [True, False, None])
    def test_smart_start_seeds_the_context_from_its_argument(
        self, tmp_path: Path, sandbox: bool | None
    ) -> None:
        # The tracking-issue/batch route reads ``ctx.sandbox``; assert the value
        # given to ``smart_start()`` actually lands on the context it builds.
        captured: list[SmartStartContext] = []

        def _capture(task, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs)
            return False

        with (
            patch("wade.services.smart_start.load_config"),
            patch("wade.services.smart_start.get_provider") as mock_provider,
            patch("wade.services.smart_start.git_repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.smart_start.check_tracking_issue_and_batch", _capture),
        ):
            mock_provider.return_value.read_task.return_value = MagicMock()
            smart_start(target="42", project_root=tmp_path, sandbox=sandbox)

        assert captured, "the batch-redirect check must have been reached"
        assert captured[0]["sandbox"] is sandbox

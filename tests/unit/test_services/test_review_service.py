"""Tests for review service — address-reviews orchestration, usage blocks, and labels."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.repo import GitError
from wade.models.ai import ModelBreakdown, TokenUsage
from wade.models.review import ReviewComment, ReviewThread
from wade.models.task import Task, TaskState
from wade.services.review_service import (
    _capture_review_session_usage,
    _post_review_lifecycle,
    _recover_worktree,
    build_review_prompt,
    fetch_reviews,
    resolve_thread,
    start,
)
from wade.services.work_service import (
    REVIEW_USAGE_MARKER_END,
    REVIEW_USAGE_MARKER_START,
    _strip_review_usage_block,
    build_review_usage_block,
)

# ---------------------------------------------------------------------------
# build_review_usage_block
# ---------------------------------------------------------------------------


class TestBuildReviewUsageBlock:
    def test_basic_block_with_tool_and_model(self) -> None:
        block = build_review_usage_block(ai_tool="claude", model="claude-sonnet-4-6")
        assert REVIEW_USAGE_MARKER_START in block
        assert REVIEW_USAGE_MARKER_END in block
        assert "## Token Usage (Review)" in block
        assert "`claude`" in block
        assert "`claude-sonnet-4-6`" in block

    def test_block_with_token_usage(self) -> None:
        usage = TokenUsage(
            total_tokens=10000,
            input_tokens=8000,
            output_tokens=2000,
        )
        block = build_review_usage_block(ai_tool="claude", token_usage=usage)
        assert "10,000" in block
        assert "8,000" in block
        assert "2,000" in block

    def test_block_no_tokens(self) -> None:
        block = build_review_usage_block(ai_tool="claude")
        assert "*unavailable*" in block

    def test_block_with_cached_tokens(self) -> None:
        usage = TokenUsage(
            total_tokens=15000,
            input_tokens=10000,
            output_tokens=3000,
            cached_tokens=2000,
        )
        block = build_review_usage_block(ai_tool="claude", token_usage=usage)
        assert "Cached tokens" in block

    def test_block_with_premium_requests(self) -> None:
        usage = TokenUsage(
            total_tokens=5000,
            premium_requests=3,
        )
        block = build_review_usage_block(ai_tool="claude", token_usage=usage)
        assert "Premium requests" in block
        assert "**3**" in block

    def test_multi_model_block(self) -> None:
        usage = TokenUsage(
            total_tokens=20000,
            input_tokens=15000,
            output_tokens=5000,
            model_breakdown=[
                ModelBreakdown(
                    model="claude-sonnet-4-6",
                    input_tokens=10000,
                    output_tokens=3000,
                ),
                ModelBreakdown(
                    model="claude-haiku-4-5",
                    input_tokens=5000,
                    output_tokens=2000,
                ),
            ],
        )
        block = build_review_usage_block(ai_tool="claude", token_usage=usage)
        assert "`claude-sonnet-4-6`" in block
        assert "`claude-haiku-4-5`" in block

    def test_empty_block(self) -> None:
        block = build_review_usage_block()
        assert REVIEW_USAGE_MARKER_START in block
        assert REVIEW_USAGE_MARKER_END in block
        assert "## Token Usage (Review)" in block


class TestStripReviewUsageBlock:
    def test_strip_removes_block(self) -> None:
        body = (
            "Content before\n\n"
            f"{REVIEW_USAGE_MARKER_START}\n"
            "## Token Usage (Review)\n"
            f"{REVIEW_USAGE_MARKER_END}\n\n"
            "Content after"
        )
        result = _strip_review_usage_block(body)
        assert REVIEW_USAGE_MARKER_START not in result
        assert "Content before" in result
        assert "Content after" in result

    def test_strip_noop_without_block(self) -> None:
        body = "Just some content"
        assert _strip_review_usage_block(body) == body

    def test_strip_preserves_impl_block(self) -> None:
        """Review strip should not touch implementation usage blocks."""
        from wade.services.work_service import IMPL_USAGE_MARKER_END, IMPL_USAGE_MARKER_START

        body = (
            f"{IMPL_USAGE_MARKER_START}\n## Impl\n{IMPL_USAGE_MARKER_END}\n\n"
            f"{REVIEW_USAGE_MARKER_START}\n## Review\n{REVIEW_USAGE_MARKER_END}\n"
        )
        result = _strip_review_usage_block(body)
        assert IMPL_USAGE_MARKER_START in result
        assert REVIEW_USAGE_MARKER_START not in result


# ---------------------------------------------------------------------------
# build_review_prompt
# ---------------------------------------------------------------------------


class TestBuildReviewPrompt:
    def test_renders_template(self, tmp_path: Path) -> None:
        task = Task(id="42", title="Fix bug")
        # Create a fake templates dir
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        template = prompts_dir / "review-context.md"
        template.write_text(
            "PR #{pr_number} for #{issue_number}: {issue_title}\n"
            "{comment_count} comments in {file_count} files\n"
        )

        with patch("wade.skills.installer.get_templates_dir", return_value=tmp_path):
            result = build_review_prompt(
                task=task,
                pr_number=99,
                comment_count=5,
                file_count=3,
            )

        assert "PR #99" in result
        assert "#42" in result
        assert "Fix bug" in result
        assert "5 comments" in result
        assert "3 files" in result


# ---------------------------------------------------------------------------
# review_service.start — integration-style tests with mocks
# ---------------------------------------------------------------------------


class TestReviewServiceStart:
    """Tests for the start() orchestration function."""

    @pytest.fixture()
    def mock_provider(self) -> MagicMock:
        provider = MagicMock()
        provider.read_task.return_value = Task(
            id="42",
            title="Fix the widget",
            body="Widget is broken",
            state=TaskState.OPEN,
        )
        return provider

    @pytest.fixture()
    def mock_setup(self, tmp_path: Path, mock_provider: MagicMock) -> dict[str, MagicMock]:
        """Set up common mocks for start() tests."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        worktree_path = tmp_path / "wt"
        worktree_path.mkdir()

        patches = {
            "load_config": patch(
                "wade.services.review_service.load_config",
                return_value=MagicMock(
                    project=MagicMock(
                        branch_prefix="feat",
                        main_branch="main",
                    ),
                ),
            ),
            "get_provider": patch(
                "wade.services.review_service.get_provider",
                return_value=mock_provider,
            ),
            "get_repo_root": patch(
                "wade.services.review_service.git_repo.get_repo_root",
                return_value=repo_root,
            ),
            "make_branch_name": patch(
                "wade.services.review_service.git_branch.make_branch_name",
                return_value="feat/42-fix-the-widget",
            ),
            "list_worktrees": patch(
                "wade.services.review_service.git_worktree.list_worktrees",
                return_value=[{"path": str(worktree_path), "branch": "feat/42-fix-the-widget"}],
            ),
            "get_pr_for_branch": patch(
                "wade.services.review_service.git_pr.get_pr_for_branch",
                return_value={
                    "number": 99,
                    "url": "https://...",
                    "state": "OPEN",
                    "isDraft": False,
                },
            ),
            "bootstrap_worktree": patch(
                "wade.services.review_service.bootstrap_worktree",
            ),
            "resolve_ai_tool": patch(
                "wade.services.review_service.resolve_ai_tool",
                return_value=None,
            ),
            "resolve_model": patch(
                "wade.services.review_service.resolve_model",
                return_value=None,
            ),
            "confirm_ai_selection": patch(
                "wade.services.review_service.confirm_ai_selection",
                return_value=(None, None),
            ),
            "_detect_ai_cli_env": patch(
                "wade.services.review_service._detect_ai_cli_env",
                return_value=None,
            ),
            "_post_review_lifecycle": patch(
                "wade.services.review_service._post_review_lifecycle",
            ),
        }

        started = {k: p.start() for k, p in patches.items()}

        yield started  # type: ignore[misc]

        for p in patches.values():
            p.stop()

    def test_no_worktree_returns_false(self, tmp_path: Path, mock_provider: MagicMock) -> None:
        """start() should fail if no worktree and no remote branch for recovery."""
        with (
            patch("wade.services.review_service.load_config"),
            patch("wade.services.review_service.get_provider", return_value=mock_provider),
            patch(
                "wade.services.review_service.git_repo.get_repo_root",
                return_value=tmp_path,
            ),
            patch(
                "wade.services.review_service.git_branch.make_branch_name",
                return_value="feat/42-fix",
            ),
            patch(
                "wade.services.review_service.git_worktree.list_worktrees",
                return_value=[],
            ),
            patch(
                "wade.services.review_service._recover_worktree",
                return_value=None,
            ),
        ):
            result = start(target="42")
            assert result is False

    def test_no_pr_returns_false(self, tmp_path: Path, mock_setup: dict[str, MagicMock]) -> None:
        """start() should fail if no PR exists for the branch."""
        mock_setup["get_pr_for_branch"].return_value = None
        result = start(target="42")
        assert result is False

    def test_merged_pr_returns_false(
        self, tmp_path: Path, mock_setup: dict[str, MagicMock]
    ) -> None:
        """start() should fail if the PR is already merged."""
        mock_setup["get_pr_for_branch"].return_value = {
            "number": 99,
            "state": "MERGED",
        }
        result = start(target="42")
        assert result is False

    def test_no_actionable_comments_returns_true(
        self, tmp_path: Path, mock_setup: dict[str, MagicMock], mock_provider: MagicMock
    ) -> None:
        """start() should succeed with a message if all comments are resolved."""
        mock_provider.get_pr_review_threads.return_value = [
            ReviewThread(is_resolved=True, comments=[ReviewComment(body="resolved")]),
        ]
        result = start(target="42")
        assert result is True

    def test_launches_ai_with_actionable_comments(
        self, tmp_path: Path, mock_setup: dict[str, MagicMock], mock_provider: MagicMock
    ) -> None:
        """start() should proceed to AI launch when actionable comments exist."""
        mock_provider.get_pr_review_threads.return_value = [
            ReviewThread(
                comments=[
                    ReviewComment(
                        author="alice",
                        body="Fix this",
                        path="main.py",
                        line=10,
                    )
                ]
            ),
        ]

        result = start(target="42")
        assert result is True

        # Verify that no REVIEW-COMMENTS.md was written (AI uses wade fetch-reviews)
        wt_paths = list(tmp_path.glob("wt"))
        assert len(wt_paths) == 1
        review_file = wt_paths[0] / "REVIEW-COMMENTS.md"
        assert not review_file.exists()


# ---------------------------------------------------------------------------
# _capture_review_session_usage
# ---------------------------------------------------------------------------


class TestCaptureReviewSessionUsage:
    def test_returns_none_without_transcript(self) -> None:
        adapter = MagicMock()
        result = _capture_review_session_usage(
            transcript_path=None,
            adapter=adapter,
            repo_root=Path("/repo"),
            branch="feat/42",
            ai_tool="claude",
            model=None,
        )
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        adapter = MagicMock()
        result = _capture_review_session_usage(
            transcript_path=tmp_path / "nonexistent.log",
            adapter=adapter,
            repo_root=Path("/repo"),
            branch="feat/42",
            ai_tool="claude",
            model=None,
        )
        assert result is None

    def test_returns_model_from_transcript(self, tmp_path: Path) -> None:
        transcript = tmp_path / "transcript.log"
        transcript.write_text("some transcript content")

        adapter = MagicMock()
        adapter.parse_transcript.return_value = TokenUsage(
            total_tokens=5000,
            input_tokens=3000,
            output_tokens=2000,
            model_breakdown=[
                ModelBreakdown(
                    model="claude-sonnet-4-6",
                    input_tokens=3000,
                    output_tokens=2000,
                )
            ],
        )

        with (
            patch("wade.services.review_service.git_pr.get_pr_for_branch", return_value=None),
        ):
            result = _capture_review_session_usage(
                transcript_path=transcript,
                adapter=adapter,
                repo_root=Path("/repo"),
                branch="feat/42",
                ai_tool="claude",
                model=None,
            )

        assert result == "claude-sonnet-4-6"

    def test_uses_explicit_model(self, tmp_path: Path) -> None:
        transcript = tmp_path / "transcript.log"
        transcript.write_text("some transcript content")

        adapter = MagicMock()
        adapter.parse_transcript.return_value = TokenUsage(
            total_tokens=5000,
            session_id="sess-123",
        )

        with (
            patch("wade.services.review_service.git_pr.get_pr_for_branch", return_value=None),
        ):
            result = _capture_review_session_usage(
                transcript_path=transcript,
                adapter=adapter,
                repo_root=Path("/repo"),
                branch="feat/42",
                ai_tool="claude",
                model="claude-opus-4-6",
            )

        assert result == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# fetch_reviews()
# ---------------------------------------------------------------------------


class TestFetchReviews:
    """Tests for the fetch_reviews() subcommand function."""

    def _make_task(self) -> Task:
        return Task(id="42", title="Fix the widget", state=TaskState.OPEN, body="")

    @patch("wade.services.review_service.filter_actionable_threads")
    @patch("wade.services.review_service.git_pr.get_pr_for_branch")
    @patch("wade.services.review_service.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.review_service.git_repo.get_repo_root")
    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_outputs_formatted_markdown(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_filter: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_repo_root.return_value = tmp_path
        provider = mock_get_provider.return_value
        provider.read_task.return_value = self._make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN"}
        threads = [
            ReviewThread(
                id="PRRT_123",
                comments=[ReviewComment(author="alice", body="Fix this", path="a.py", line=1)],
            )
        ]
        mock_filter.return_value = threads
        provider.get_pr_review_threads.return_value = threads

        result = fetch_reviews("42", project_root=tmp_path)

        assert result is True
        captured = capsys.readouterr()
        assert "Fix this" in captured.out
        assert "@alice" in captured.out

    @patch("wade.services.review_service.filter_actionable_threads")
    @patch("wade.services.review_service.git_pr.get_pr_for_branch")
    @patch("wade.services.review_service.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.review_service.git_repo.get_repo_root")
    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_no_comments_prints_message(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_filter: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_repo_root.return_value = tmp_path
        provider = mock_get_provider.return_value
        provider.read_task.return_value = self._make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN"}
        provider.get_pr_review_threads.return_value = []
        mock_filter.return_value = []

        result = fetch_reviews("42", project_root=tmp_path)

        assert result is True
        captured = capsys.readouterr()
        assert "No unresolved" in captured.out

    @patch("wade.services.review_service.git_pr.get_pr_for_branch", return_value=None)
    @patch("wade.services.review_service.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.review_service.git_repo.get_repo_root")
    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_no_pr_returns_false(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        provider = mock_get_provider.return_value
        provider.read_task.return_value = self._make_task()

        result = fetch_reviews("42", project_root=tmp_path)

        assert result is False

    @patch("wade.services.review_service.git_repo.get_repo_root", side_effect=GitError("nope"))
    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_not_in_repo_returns_false(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = fetch_reviews("42", project_root=tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# resolve_thread()
# ---------------------------------------------------------------------------


class TestResolveThread:
    """Tests for the resolve_thread() subcommand function."""

    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_success_returns_true(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
    ) -> None:
        provider = mock_get_provider.return_value
        provider.resolve_review_thread.return_value = True

        result = resolve_thread("PRRT_abc123")

        assert result is True
        provider.resolve_review_thread.assert_called_once_with("PRRT_abc123")

    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_failure_returns_false(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
    ) -> None:
        provider = mock_get_provider.return_value
        provider.resolve_review_thread.return_value = False

        result = resolve_thread("PRRT_abc123")

        assert result is False

    @patch("wade.services.review_service.get_provider")
    @patch("wade.services.review_service.load_config")
    def test_not_implemented_returns_false(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
    ) -> None:
        provider = mock_get_provider.return_value
        provider.resolve_review_thread.side_effect = NotImplementedError

        result = resolve_thread("PRRT_abc123")

        assert result is False


# ---------------------------------------------------------------------------
# _recover_worktree()
# ---------------------------------------------------------------------------


class TestRecoverWorktree:
    """Tests for the _recover_worktree() helper."""

    def _make_config(self) -> MagicMock:
        from wade.models.config import ProjectConfig

        config = MagicMock(spec=ProjectConfig)
        config.project = MagicMock()
        config.project.worktrees_dir = ""
        config.project.branch_prefix = "feat"
        config.project.main_branch = "main"
        return config

    @patch("wade.services.review_service.git_worktree.checkout_existing_branch_worktree")
    @patch("wade.services.review_service._resolve_worktrees_dir")
    @patch("wade.services.review_service.git_repo.rev_parse")
    @patch("wade.services.review_service.git_repo.fetch_ref")
    def test_success_returns_worktree_path(
        self,
        mock_fetch: MagicMock,
        mock_rev_parse: MagicMock,
        mock_resolve_wt: MagicMock,
        mock_checkout: MagicMock,
        tmp_path: Path,
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        worktrees_dir = tmp_path / "worktrees"
        worktrees_dir.mkdir()
        mock_resolve_wt.return_value = worktrees_dir
        expected_path = worktrees_dir / "repo" / "feat-42-fix"
        mock_checkout.return_value = expected_path

        result = _recover_worktree(repo_root, "feat/42-fix", self._make_config())

        assert result == expected_path
        mock_fetch.assert_called_once()
        mock_rev_parse.assert_called_once()
        mock_checkout.assert_called_once()

    @patch("wade.services.review_service.git_repo.fetch_ref", side_effect=GitError("no remote"))
    def test_no_remote_branch_returns_none(
        self,
        mock_fetch: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = _recover_worktree(tmp_path, "feat/42-fix", self._make_config())
        assert result is None

    @patch("wade.services.review_service.git_repo.rev_parse", side_effect=GitError("not found"))
    @patch("wade.services.review_service.git_repo.fetch_ref")
    def test_branch_not_local_returns_none(
        self,
        mock_fetch: MagicMock,
        mock_rev_parse: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = _recover_worktree(tmp_path, "feat/42-fix", self._make_config())
        assert result is None


# ---------------------------------------------------------------------------
# _post_review_lifecycle()
# ---------------------------------------------------------------------------


class TestPostReviewLifecycle:
    """Tests for the _post_review_lifecycle() helper."""

    @patch("wade.services.review_service._merge_pr")
    @patch("wade.ui.prompts.select", return_value=0)
    def test_merge_choice_calls_merge_pr(
        self,
        mock_select: MagicMock,
        mock_merge: MagicMock,
        tmp_path: Path,
    ) -> None:
        provider = MagicMock()
        _post_review_lifecycle(tmp_path, "feat/42", "42", tmp_path / "wt", 99, provider)
        mock_merge.assert_called_once_with(tmp_path, "feat/42", 99, "42", tmp_path / "wt", provider)

    @patch("wade.services.review_service._merge_pr")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_wait_choice_shows_hint(
        self,
        mock_select: MagicMock,
        mock_merge: MagicMock,
        tmp_path: Path,
    ) -> None:
        provider = MagicMock()
        _post_review_lifecycle(tmp_path, "feat/42", "42", tmp_path / "wt", 99, provider)
        mock_merge.assert_not_called()

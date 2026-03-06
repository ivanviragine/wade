"""Tests for review service — address-reviews orchestration, usage blocks, and labels."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.ai import ModelBreakdown, TokenUsage
from wade.models.review import ReviewComment, ReviewThread
from wade.models.task import Task, TaskState
from wade.services.review_service import (
    _capture_review_session_usage,
    build_review_prompt,
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
        }

        started = {k: p.start() for k, p in patches.items()}

        yield started  # type: ignore[misc]

        for p in patches.values():
            p.stop()

    def test_no_worktree_returns_false(self, tmp_path: Path, mock_provider: MagicMock) -> None:
        """start() should fail if no worktree exists for the issue."""
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

    def test_writes_review_comments_file(
        self, tmp_path: Path, mock_setup: dict[str, MagicMock], mock_provider: MagicMock
    ) -> None:
        """start() should write REVIEW-COMMENTS.md to the worktree."""
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

        # Find the worktree path used
        wt_paths = list(tmp_path.glob("wt"))
        assert len(wt_paths) == 1
        review_file = wt_paths[0] / "REVIEW-COMMENTS.md"
        assert review_file.is_file()
        content = review_file.read_text()
        assert "Fix this" in content
        assert "main.py" in content


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

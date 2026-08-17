"""Tests for bot-review triggering (#431).

Covers the manual ``review_service.trigger_bot_reviews`` service (per-bot
posting, partial-failure isolation, output-state contract, ``--bot`` subset
selection + enabled override, unknown-name error, dry-run) and the ``done``
auto-trigger hook (``_auto_trigger_bot_reviews``: once-per-bot-per-sha,
retry-only-failed, skip-when-disabled, and the manual-vs-auto marker separation).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.models.config import BotReviewConfig, ProjectConfig, ReviewBotConfig
from wade.models.review import BotTriggerOutcome
from wade.services.implementation_service.done import _auto_trigger_bot_reviews
from wade.services.review_service import trigger_bot_reviews
from wade.utils import markers


def _config(
    *, auto_trigger: bool = False, bots: list[ReviewBotConfig] | None = None
) -> ProjectConfig:
    review = (
        BotReviewConfig(auto_trigger=auto_trigger, bots=bots)
        if bots is not None
        else BotReviewConfig(auto_trigger=auto_trigger)
    )
    return ProjectConfig(bot_review=review)


def _open_pr(number: int = 99) -> PRLookup:
    return PRLookup(found=True, pr=PRRef(number=number, url="https://x", state="OPEN"))


@contextmanager
def _mock_service(
    config: ProjectConfig,
    *,
    lookup: PRLookup | None = None,
    comment: MagicMock | None = None,
    tmp_path: Path,
) -> Iterator[MagicMock]:
    """Patch the resolution/plumbing seams of ``trigger_bot_reviews``."""
    provider = MagicMock()
    provider.read_task.return_value = MagicMock(id="42", title="feat: widget")
    comment = comment or MagicMock()
    with (
        patch("wade.services.review_service.load_config", return_value=config),
        patch("wade.services.review_service.get_provider", return_value=provider),
        patch("wade.services.review_service.git_repo.get_repo_root", return_value=tmp_path),
        patch(
            "wade.services.review_service._resolve_task_branch",
            return_value="feat/42-widget",
        ),
        patch(
            "wade.services.review_service.git_pr.get_pr_for_branch",
            return_value=lookup or _open_pr(),
        ),
        patch("wade.services.review_service.git_pr.comment_on_pr", comment),
    ):
        yield comment


# ---------------------------------------------------------------------------
# trigger_bot_reviews — happy path & output contract
# ---------------------------------------------------------------------------


class TestTriggerBotReviews:
    def test_posts_each_enabled_bot(self, tmp_path: Path) -> None:
        config = _config()
        with _mock_service(config, tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42")
        assert report.exit_code == 0
        assert [r.outcome for r in report.results] == [BotTriggerOutcome.POSTED] * 3
        posted = [c.args[2] for c in comment.call_args_list]
        assert posted == ["@coderabbitai review", "@codex review", "bugbot run"]

    def test_disabled_bot_reported_skipped_not_posted(self, tmp_path: Path) -> None:
        config = _config(
            bots=[
                ReviewBotConfig(name="coderabbit", trigger="@coderabbitai review", enabled=True),
                ReviewBotConfig(name="codex", trigger="@codex review", enabled=False),
            ]
        )
        with _mock_service(config, tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42")
        by_name = {r.name: r.outcome for r in report.results}
        assert by_name == {
            "coderabbit": BotTriggerOutcome.POSTED,
            "codex": BotTriggerOutcome.SKIPPED_DISABLED,
        }
        assert comment.call_count == 1  # codex never posted
        assert report.exit_code == 0

    def test_partial_failure_isolated_and_exits_zero(self, tmp_path: Path) -> None:
        """One bot raising must not abort the others; partial failure is exit 0."""
        comment = MagicMock(
            side_effect=[Exception("boom"), None, None]  # coderabbit fails, rest ok
        )
        with _mock_service(_config(), comment=comment, tmp_path=tmp_path):
            report = trigger_bot_reviews("42")
        outcomes = {r.name: r.outcome for r in report.results}
        assert outcomes["coderabbit"] is BotTriggerOutcome.FAILED
        assert outcomes["codex"] is BotTriggerOutcome.POSTED
        assert outcomes["bugbot"] is BotTriggerOutcome.POSTED
        assert comment.call_count == 3  # all three attempted despite the first raising
        assert report.exit_code == 0  # partial failure still succeeds

    def test_all_failed_exits_nonzero(self, tmp_path: Path) -> None:
        comment = MagicMock(side_effect=Exception("boom"))
        with _mock_service(_config(), comment=comment, tmp_path=tmp_path):
            report = trigger_bot_reviews("42")
        assert all(r.outcome is BotTriggerOutcome.FAILED for r in report.results)
        assert report.all_attempts_failed is True
        assert report.exit_code == 1

    def test_failure_with_markup_error_text_renders_safely(self, tmp_path: Path) -> None:
        """Provider/exception text in ``status_line()`` is escaped, not parsed as markup.

        A bot ``name`` is now a validated safe identifier, but the failure
        ``status_line`` still embeds untrusted ``str(e)`` — a stray Rich control
        token there must not raise ``MarkupError`` on the ``warn`` render path.
        """
        comment = MagicMock(side_effect=Exception("boom [/] unbalanced"))
        with _mock_service(_config(), comment=comment, tmp_path=tmp_path):
            report = trigger_bot_reviews("42")
        assert all(r.outcome is BotTriggerOutcome.FAILED for r in report.results)
        assert report.exit_code == 1

    def test_provider_read_error_with_markup_renders_safely(self, tmp_path: Path) -> None:
        """Provider read error text is rendered markup-disabled → structured exit-1."""
        provider = MagicMock()
        provider.read_task.side_effect = Exception("boom [/] token")
        with (
            patch("wade.services.review_service.load_config", return_value=_config()),
            patch("wade.services.review_service.get_provider", return_value=provider),
            patch("wade.services.review_service.git_repo.get_repo_root", return_value=tmp_path),
        ):
            report = trigger_bot_reviews("42")
        assert report.resolution_error is not None
        assert report.exit_code == 1

    def test_status_line_contract(self) -> None:
        from wade.models.review import BotTriggerResult

        assert (
            BotTriggerResult(name="a", trigger="t", outcome=BotTriggerOutcome.POSTED).status_line()
            == "a: posted"
        )
        assert (
            BotTriggerResult(
                name="a", trigger="t", outcome=BotTriggerOutcome.SKIPPED_DISABLED
            ).status_line()
            == "a: skipped (disabled)"
        )
        assert (
            BotTriggerResult(name="a", trigger="t", outcome=BotTriggerOutcome.DRY_RUN).status_line()
            == "a: would post (dry-run)"
        )
        assert (
            BotTriggerResult(
                name="a", trigger="t", outcome=BotTriggerOutcome.FAILED, error="x"
            ).status_line()
            == "a: failed: x"
        )


# ---------------------------------------------------------------------------
# trigger_bot_reviews — --bot selection
# ---------------------------------------------------------------------------


class TestTriggerSelection:
    def test_bot_subset_restricts_posting(self, tmp_path: Path) -> None:
        with _mock_service(_config(), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", selected_bots=["codex"])
        assert [r.name for r in report.results] == ["codex"]
        assert report.results[0].outcome is BotTriggerOutcome.POSTED
        assert comment.call_count == 1
        assert comment.call_args.args[2] == "@codex review"

    def test_bot_selection_overrides_disabled(self, tmp_path: Path) -> None:
        config = _config(
            bots=[ReviewBotConfig(name="codex", trigger="@codex review", enabled=False)]
        )
        with _mock_service(config, tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", selected_bots=["codex"])
        # Explicit request beats enabled:false — posted, not skipped.
        assert report.results[0].outcome is BotTriggerOutcome.POSTED
        assert comment.call_count == 1

    def test_repeated_bot_value_deduped(self, tmp_path: Path) -> None:
        with _mock_service(_config(), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", selected_bots=["codex", "codex"])
        assert [r.name for r in report.results] == ["codex"]
        assert comment.call_count == 1

    def test_unknown_bot_errors_without_posting(self, tmp_path: Path) -> None:
        with _mock_service(_config(), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", selected_bots=["nope"])
        assert report.unknown_bots == ["nope"]
        assert report.valid_bot_names == ["coderabbit", "codex", "bugbot"]
        assert report.results == []
        assert comment.call_count == 0
        assert report.exit_code == 1

    def test_unknown_bot_error_renders_markup_names_safely(self, tmp_path: Path) -> None:
        """`--bot` values are unvalidated user input; a markup token must not crash.

        Configured names are validated safe, but the rejected ``--bot`` value is
        echoed into markup-enabled error/hint output and could carry a Rich token.
        """
        with _mock_service(_config(), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", selected_bots=["[/]"])
        assert report.unknown_bots == ["[/]"]
        assert comment.call_count == 0
        assert report.exit_code == 1


# ---------------------------------------------------------------------------
# trigger_bot_reviews — dry-run & resolution failures
# ---------------------------------------------------------------------------


class TestTriggerDryRunAndResolution:
    def test_dry_run_posts_nothing(self, tmp_path: Path) -> None:
        with _mock_service(_config(), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42", dry_run=True)
        assert all(r.outcome is BotTriggerOutcome.DRY_RUN for r in report.results)
        assert comment.call_count == 0
        assert report.exit_code == 0

    def test_no_open_pr_exits_nonzero(self, tmp_path: Path) -> None:
        with _mock_service(_config(), lookup=PRLookup(found=False), tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42")
        assert report.resolution_error is not None
        assert comment.call_count == 0
        assert report.exit_code == 1

    def test_merged_pr_is_not_open_exits_nonzero(self, tmp_path: Path) -> None:
        merged = PRLookup(found=True, pr=PRRef(number=5, state="MERGED"))
        with _mock_service(_config(), lookup=merged, tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42")
        assert report.resolution_error is not None
        assert comment.call_count == 0
        assert report.exit_code == 1

    def test_lookup_failure_exits_nonzero(self, tmp_path: Path) -> None:
        failed = PRLookup(found=False, lookup_failed=True)
        with _mock_service(_config(), lookup=failed, tmp_path=tmp_path) as comment:
            report = trigger_bot_reviews("42")
        assert report.resolution_error is not None
        assert comment.call_count == 0
        assert report.exit_code == 1


# ---------------------------------------------------------------------------
# Auto-trigger in done (_auto_trigger_bot_reviews)
# ---------------------------------------------------------------------------


class TestAutoTrigger:
    def _run(
        self,
        config: ProjectConfig,
        tmp_path: Path,
        *,
        comment: MagicMock | None = None,
        sha: str = "abc123",
    ) -> MagicMock:
        comment = comment or MagicMock()
        with (
            patch(
                "wade.services.implementation_service.done.git_repo.rev_parse",
                return_value=sha,
            ),
            patch(
                "wade.services.implementation_service.done.git_pr.comment_on_pr",
                comment,
            ),
        ):
            _auto_trigger_bot_reviews(config, tmp_path, "feat/42-x", 99, tmp_path)
        return comment

    def test_disabled_when_auto_trigger_false(self, tmp_path: Path) -> None:
        comment = self._run(_config(auto_trigger=False), tmp_path)
        assert comment.call_count == 0

    def test_posts_enabled_bots_when_on(self, tmp_path: Path) -> None:
        comment = self._run(_config(auto_trigger=True), tmp_path)
        assert comment.call_count == 3

    def test_skips_disabled_bots(self, tmp_path: Path) -> None:
        config = _config(
            auto_trigger=True,
            bots=[
                ReviewBotConfig(name="coderabbit", trigger="@coderabbitai review", enabled=True),
                ReviewBotConfig(name="codex", trigger="@codex review", enabled=False),
            ],
        )
        comment = self._run(config, tmp_path)
        assert comment.call_count == 1
        assert comment.call_args.args[2] == "@coderabbitai review"

    def test_once_per_bot_per_sha(self, tmp_path: Path) -> None:
        """A second run on the same sha posts nothing further (marker guard)."""
        config = _config(auto_trigger=True)
        first = self._run(config, tmp_path, sha="sha1")
        assert first.call_count == 3
        # Markers now exist for all three bots at sha1.
        assert markers.marker_present(tmp_path, "bot-triggered-coderabbit", "sha1")
        second = self._run(config, tmp_path, sha="sha1")
        assert second.call_count == 0

    def test_new_sha_triggers_again(self, tmp_path: Path) -> None:
        config = _config(auto_trigger=True)
        self._run(config, tmp_path, sha="sha1")
        second = self._run(config, tmp_path, sha="sha2")
        assert second.call_count == 3

    def test_only_failed_bot_retries(self, tmp_path: Path) -> None:
        """A bot whose post failed writes no marker, so it retries next run."""
        config = _config(auto_trigger=True)
        # coderabbit fails, codex + bugbot succeed on the first run.
        first = MagicMock(side_effect=[Exception("boom"), None, None])
        self._run(config, tmp_path, comment=first, sha="sha1")
        assert not markers.marker_present(tmp_path, "bot-triggered-coderabbit", "sha1")
        assert markers.marker_present(tmp_path, "bot-triggered-codex", "sha1")
        # Second run on the same sha: only coderabbit (no marker) is retried.
        second = self._run(config, tmp_path, sha="sha1")
        assert second.call_count == 1
        assert second.call_args.args[2] == "@coderabbitai review"

    def test_marker_write_failure_warns_not_silent_success(self, tmp_path: Path) -> None:
        """A failed marker write is surfaced, not reported as durable success.

        The comment is already posted, but ``write_marker`` returning ``False``
        means the anti-spam marker is absent — so warn (a later same-sha done may
        re-post) rather than print the success detail.
        """
        config = _config(
            auto_trigger=True,
            bots=[ReviewBotConfig(name="codex", trigger="@codex review", enabled=True)],
        )
        comment = MagicMock()
        with (
            patch(
                "wade.services.implementation_service.done.git_repo.rev_parse",
                return_value="sha1",
            ),
            patch(
                "wade.services.implementation_service.done.git_pr.comment_on_pr",
                comment,
            ),
            patch(
                "wade.services.implementation_service.done.markers.write_marker",
                return_value=False,
            ),
            patch("wade.services.implementation_service.done.console") as mock_console,
        ):
            mock_console.escape_markup.side_effect = lambda s: s
            _auto_trigger_bot_reviews(config, tmp_path, "feat/42-x", 99, tmp_path)
        assert comment.call_count == 1  # comment still posted
        assert mock_console.warn.call_count == 1  # failure surfaced
        assert mock_console.detail.call_count == 0  # not a durable-success report

    def test_manual_trigger_does_not_write_auto_markers(self, tmp_path: Path) -> None:
        """Manual command leaves auto markers untouched, so a same-sha done still fires."""
        with (
            _mock_service(_config(auto_trigger=True), tmp_path=tmp_path),
            patch("wade.services.review_service.git_repo.get_repo_root", return_value=tmp_path),
        ):
            trigger_bot_reviews("42")
        # No auto markers written by the manual path.
        assert not markers.marker_present(tmp_path, "bot-triggered-coderabbit", "sha1")
        # A same-sha done auto-trigger therefore still posts.
        comment = self._run(_config(auto_trigger=True), tmp_path, sha="sha1")
        assert comment.call_count == 3

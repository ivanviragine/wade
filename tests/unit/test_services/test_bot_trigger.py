"""Tests for bot-review triggering (#431, #464).

Covers the manual ``review_service.trigger_bot_reviews`` service (per-bot
posting, partial-failure isolation, output-state contract, ``--bot`` subset
selection + enabled override, unknown-name error, dry-run), the ``done`` hook
(``_maybe_trigger_bot_reviews``: once-per-bot-per-sha, retry-only-failed,
skip-when-disabled, the ``--trigger-bots`` / ``--no-trigger-bots`` override, the
``offer_on_done`` offer path, and the manual-vs-automatic marker separation), and
the shared menu helpers in ``services.bot_trigger``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.git.repo import GitError
from wade.models.config import BotReviewConfig, ProjectConfig, ReviewBotConfig
from wade.models.review import BotTriggerOutcome
from wade.services import bot_trigger
from wade.services.implementation_service.done import _maybe_trigger_bot_reviews
from wade.services.review_service import trigger_bot_reviews
from wade.utils import markers


def _config(
    *,
    auto_trigger: bool = False,
    offer_on_done: bool = True,
    bots: list[ReviewBotConfig] | None = None,
) -> ProjectConfig:
    review = (
        BotReviewConfig(auto_trigger=auto_trigger, offer_on_done=offer_on_done, bots=bots)
        if bots is not None
        else BotReviewConfig(auto_trigger=auto_trigger, offer_on_done=offer_on_done)
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
# Automatic / offered triggering in done (_maybe_trigger_bot_reviews)
# ---------------------------------------------------------------------------


def _run_done_hook(
    config: ProjectConfig,
    tmp_path: Path,
    *,
    comment: MagicMock | None = None,
    sha: str = "abc123",
    trigger_bots: bool | None = None,
) -> MagicMock:
    """Drive ``done``'s trigger hook with git plumbing stubbed out.

    ``prompts.is_tty()`` is False under pytest, so the offer path takes its
    non-interactive branch unless a test patches it.
    """
    comment = comment or MagicMock()
    with (
        patch("wade.services.bot_trigger.git_repo.rev_parse", return_value=sha),
        patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
    ):
        _maybe_trigger_bot_reviews(config, tmp_path, "feat/42-x", 99, tmp_path, "42", trigger_bots)
    return comment


class TestAutoTrigger:
    def _run(
        self,
        config: ProjectConfig,
        tmp_path: Path,
        *,
        comment: MagicMock | None = None,
        sha: str = "abc123",
    ) -> MagicMock:
        return _run_done_hook(config, tmp_path, comment=comment, sha=sha)

    def test_disabled_when_auto_trigger_false(self, tmp_path: Path) -> None:
        comment = self._run(_config(auto_trigger=False, offer_on_done=False), tmp_path)
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
            patch("wade.services.bot_trigger.git_repo.rev_parse", return_value="sha1"),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
            patch("wade.services.bot_trigger.markers.write_marker", return_value=False),
            patch("wade.services.bot_trigger.console") as mock_console,
        ):
            mock_console.escape_markup.side_effect = lambda s: s
            _maybe_trigger_bot_reviews(config, tmp_path, "feat/42-x", 99, tmp_path, "42")
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

    def test_unresolvable_sha_posts_nothing(self, tmp_path: Path) -> None:
        """No sha means the markers cannot dedupe — post nothing rather than spam."""
        comment = MagicMock()
        with (
            patch(
                "wade.services.bot_trigger.git_repo.rev_parse",
                side_effect=GitError("detached"),
            ),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
        ):
            _maybe_trigger_bot_reviews(
                _config(auto_trigger=True), tmp_path, "feat/42-x", 99, tmp_path, "42"
            )
        assert comment.call_count == 0


# ---------------------------------------------------------------------------
# The offer path + the --trigger-bots / --no-trigger-bots override (#464)
# ---------------------------------------------------------------------------


class TestDoneTriggerOffer:
    def test_offer_reports_instead_of_posting(self, tmp_path: Path) -> None:
        """auto_trigger off + offer on + non-TTY: nothing posted, offer surfaced."""
        with patch("wade.services.implementation_service.done.console") as mock_console:
            mock_console.escape_markup.side_effect = lambda s: s
            comment = _run_done_hook(_config(), tmp_path)
        assert comment.call_count == 0
        offered = " ".join(str(c.args[0]) for c in mock_console.info.call_args_list)
        assert "wade review trigger 42" in offered
        assert "coderabbit" in offered

    def test_offer_silent_when_opted_out(self, tmp_path: Path) -> None:
        with patch("wade.services.implementation_service.done.console") as mock_console:
            mock_console.escape_markup.side_effect = lambda s: s
            comment = _run_done_hook(_config(offer_on_done=False), tmp_path)
        assert comment.call_count == 0
        assert mock_console.info.call_count == 0

    def test_offer_silent_when_no_bot_enabled(self, tmp_path: Path) -> None:
        config = _config(
            bots=[ReviewBotConfig(name="codex", trigger="@codex review", enabled=False)]
        )
        with patch("wade.services.implementation_service.done.console") as mock_console:
            mock_console.escape_markup.side_effect = lambda s: s
            comment = _run_done_hook(config, tmp_path)
        assert comment.call_count == 0
        assert mock_console.info.call_count == 0

    def test_offer_not_repeated_for_an_already_triggered_commit(self, tmp_path: Path) -> None:
        """A second done at the same sha neither posts nor re-asks."""
        _run_done_hook(_config(auto_trigger=True), tmp_path, sha="sha1")
        with patch("wade.services.implementation_service.done.console") as mock_console:
            mock_console.escape_markup.side_effect = lambda s: s
            comment = _run_done_hook(_config(), tmp_path, sha="sha1")
        assert comment.call_count == 0
        assert mock_console.info.call_count == 0

    def test_tty_confirm_accepted_posts(self, tmp_path: Path) -> None:
        with (
            patch("wade.services.implementation_service.done.prompts.is_tty", return_value=True),
            patch("wade.services.implementation_service.done.prompts.confirm", return_value=True),
        ):
            comment = _run_done_hook(_config(), tmp_path)
        assert comment.call_count == 3
        assert markers.marker_present(tmp_path, "bot-triggered-coderabbit", "abc123")

    def test_tty_confirm_declined_posts_nothing_and_leaves_no_marker(self, tmp_path: Path) -> None:
        """Declining must not write markers — the next done offers again."""
        with (
            patch("wade.services.implementation_service.done.prompts.is_tty", return_value=True),
            patch("wade.services.implementation_service.done.prompts.confirm", return_value=False),
        ):
            comment = _run_done_hook(_config(), tmp_path)
        assert comment.call_count == 0
        assert not markers.marker_present(tmp_path, "bot-triggered-coderabbit", "abc123")

    def test_tty_confirm_cancelled_is_declined_not_an_abort(self, tmp_path: Path) -> None:
        """Ctrl+C at the offer must not raise — done() runs after the PR finalize.

        A raised ``typer.Exit`` here would abort ``done()`` (reporting failure and
        skipping worktree cleanup) even though the push/PR already succeeded. The
        real ``prompts.confirm`` runs with ``cancel_default=False``, so a cancelled
        prompt (questionary returns ``None``) is treated as a decline (#464 review).
        """
        with (
            patch("wade.services.implementation_service.done.prompts.is_tty", return_value=True),
            # questionary returning None simulates Ctrl+C at the confirm.
            patch("questionary.select") as mock_select,
        ):
            mock_select.return_value.ask.return_value = None
            comment = _run_done_hook(_config(), tmp_path)
        assert comment.call_count == 0
        assert not markers.marker_present(tmp_path, "bot-triggered-coderabbit", "abc123")

    def test_flag_posts_even_with_auto_trigger_off(self, tmp_path: Path) -> None:
        comment = _run_done_hook(_config(offer_on_done=False), tmp_path, trigger_bots=True)
        assert comment.call_count == 3

    def test_flag_suppresses_auto_trigger(self, tmp_path: Path) -> None:
        comment = _run_done_hook(_config(auto_trigger=True), tmp_path, trigger_bots=False)
        assert comment.call_count == 0

    def test_flag_on_an_already_triggered_commit_says_so(self, tmp_path: Path) -> None:
        """An explicit request that the markers swallow is reported, not silent."""
        _run_done_hook(_config(auto_trigger=True), tmp_path, sha="sha1")
        with patch("wade.services.implementation_service.done.console") as mock_console:
            mock_console.escape_markup.side_effect = lambda s: s
            comment = _run_done_hook(_config(), tmp_path, sha="sha1", trigger_bots=True)
        assert comment.call_count == 0
        said = " ".join(str(c.args[0]) for c in mock_console.detail.call_args_list)
        assert "wade review trigger 42" in said


# ---------------------------------------------------------------------------
# Shared post-session menu helpers (services.bot_trigger)
# ---------------------------------------------------------------------------


class TestMenuHelpers:
    def _entry(
        self, config: ProjectConfig, tmp_path: Path, *, sha: str = "abc123", suffix: str = ""
    ) -> tuple[ProjectConfig | None, str | None]:
        with patch("wade.services.bot_trigger.git_repo.rev_parse", return_value=sha):
            return bot_trigger.menu_entry(
                tmp_path, "feat/42-x", tmp_path, suffix=suffix, config=config
            )

    def test_entry_lists_pending_bots(self, tmp_path: Path) -> None:
        config, label = self._entry(_config(), tmp_path, suffix=", then wait")
        assert config is not None
        assert label == "Trigger bot reviews (coderabbit, codex, bugbot), then wait"

    def test_entry_hidden_when_opted_out(self, tmp_path: Path) -> None:
        assert self._entry(_config(offer_on_done=False), tmp_path) == (None, None)

    def test_entry_hidden_when_every_bot_already_triggered(self, tmp_path: Path) -> None:
        _run_done_hook(_config(auto_trigger=True), tmp_path, sha="sha1")
        assert self._entry(_config(), tmp_path, sha="sha1") == (None, None)

    def test_entry_hidden_when_sha_unresolvable(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.bot_trigger.git_repo.rev_parse", side_effect=GitError("detached")
        ):
            entry = bot_trigger.menu_entry(tmp_path, "feat/42-x", tmp_path, config=_config())
        assert entry == (None, None)

    def test_entry_only_names_bots_still_pending(self, tmp_path: Path) -> None:
        """A bot triggered by `done` drops out of the menu; the rest stay."""
        config = _config(
            bots=[
                ReviewBotConfig(name="coderabbit", trigger="@coderabbitai review"),
                ReviewBotConfig(name="codex", trigger="@codex review"),
            ]
        )
        assert markers.write_marker(tmp_path, "bot-triggered-coderabbit", "sha1")
        _, label = self._entry(config, tmp_path, sha="sha1")
        assert label == "Trigger bot reviews (codex)"

    def test_post_pending_posts_and_marks(self, tmp_path: Path) -> None:
        comment = MagicMock()
        with (
            patch("wade.services.bot_trigger.git_repo.rev_parse", return_value="sha1"),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
        ):
            pending = bot_trigger.post_pending_triggers(
                _config(), tmp_path, "feat/42-x", 99, tmp_path
            )
        # Posting a trigger means a review is now pending for this commit.
        assert pending is True
        assert markers.marker_present(tmp_path, "bot-triggered-codex", "sha1")

    def test_post_pending_is_a_noop_once_triggered(self, tmp_path: Path) -> None:
        comment = MagicMock()
        with (
            patch("wade.services.bot_trigger.git_repo.rev_parse", return_value="sha1"),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
        ):
            first = bot_trigger.post_pending_triggers(
                _config(), tmp_path, "feat/42-x", 99, tmp_path
            )
            second = bot_trigger.post_pending_triggers(
                _config(), tmp_path, "feat/42-x", 99, tmp_path
            )
        # Both report "a review is pending": the first posts them, the second finds
        # them already recorded for this sha (no re-post — proven by call_count).
        assert (first, second) == (True, True)
        assert comment.call_count == 3

    def test_post_pending_false_when_every_post_fails(self, tmp_path: Path) -> None:
        """A GitHub/API outage (every trigger post raises) means nothing pending.

        The menu-side callers fall through into a wait-for-review poll only when a
        trigger is actually pending — a ``False`` here keeps them from silently
        waiting for a review no bot was successfully asked for (#464 review).
        """
        with (
            patch("wade.services.bot_trigger.git_repo.rev_parse", return_value="sha1"),
            patch(
                "wade.services.bot_trigger.git_pr.comment_on_pr",
                side_effect=RuntimeError("gh down"),
            ),
        ):
            pending = bot_trigger.post_pending_triggers(
                _config(), tmp_path, "feat/42-x", 99, tmp_path
            )
        assert pending is False
        assert not markers.marker_present(tmp_path, "bot-triggered-codex", "sha1")

    def test_post_pending_false_when_sha_unresolvable(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.bot_trigger.git_repo.rev_parse", side_effect=GitError("detached")
        ):
            pending = bot_trigger.post_pending_triggers(
                _config(), tmp_path, "feat/42-x", 99, tmp_path
            )
        assert pending is False


# ---------------------------------------------------------------------------
# post_bot_triggers race-safety (#464 review): under-lock re-check + fallback
# ---------------------------------------------------------------------------


class TestPostBotTriggersRaceSafety:
    def test_under_lock_recheck_skips_a_bot_marked_in_between(self, tmp_path: Path) -> None:
        """A bot recorded after the caller's pending set was built is skipped.

        Simulates a concurrent done/menu process on the same worktree posting +
        recording coderabbit@sha1 after this caller computed its pending list:
        the re-check *inside* ``post_bot_triggers``'s lock must drop it, so the
        PR gets no duplicate comment (once-per-bot-per-sha under concurrency).
        """
        config = _config(auto_trigger=True)
        assert markers.write_marker(tmp_path, "bot-triggered-coderabbit", "sha1")
        comment = MagicMock()
        with patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment):
            posted = bot_trigger.post_bot_triggers(
                tmp_path, 99, config.bot_review.bots, marker_root=tmp_path, sha="sha1"
            )
        names = [c.args[2] for c in comment.call_args_list]
        assert names == ["@codex review", "bugbot run"]  # coderabbit skipped, not re-posted
        assert posted == 2

    def test_posts_when_lock_primitive_unavailable(self, tmp_path: Path) -> None:
        """A broken file_lock degrades to posting, never fails an otherwise-complete done."""
        config = _config(auto_trigger=True)
        comment = MagicMock()
        with (
            patch(
                "wade.services.bot_trigger.file_lock",
                MagicMock(side_effect=OSError("temp dir unwritable")),
            ),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
        ):
            posted = bot_trigger.post_bot_triggers(
                tmp_path, 99, config.bot_review.bots, marker_root=tmp_path, sha="sha1"
            )
        assert posted == 3  # all three still posted despite the lock being unavailable
        assert markers.marker_present(tmp_path, "bot-triggered-codex", "sha1")

    def test_lock_release_error_does_not_repost(self, tmp_path: Path) -> None:
        """An OSError during lock *release* must not re-run posting (no double-post).

        Worst case: a bot's comment posts, its marker write fails, then
        ``file_lock`` cleanup raises. Re-running the section would re-post that
        un-marked bot — so a release error returns the already-computed count
        instead. Distinguishes a release failure (body ran) from an acquisition
        failure (body must still run).
        """
        config = _config(
            auto_trigger=True,
            bots=[ReviewBotConfig(name="codex", trigger="@codex review", enabled=True)],
        )
        comment = MagicMock()

        @contextmanager
        def _lock_release_boom(_path: Path) -> Iterator[None]:
            yield
            raise OSError("close failed on release")

        with (
            patch("wade.services.bot_trigger.file_lock", _lock_release_boom),
            patch("wade.services.bot_trigger.git_pr.comment_on_pr", comment),
            patch("wade.services.bot_trigger.markers.write_marker", return_value=False),
        ):
            posted = bot_trigger.post_bot_triggers(
                tmp_path, 99, config.bot_review.bots, marker_root=tmp_path, sha="sha1"
            )
        assert comment.call_count == 1  # posted exactly once, not re-run after release error
        assert posted == 1

"""Tests for expected-bot completion gating (#448).

Covers the bot-name -> login matcher, reaction acknowledgement, the per-bot
arrival computation, and how the arrival map gates ``review_covers_latest_commit``
/ ``is_all_clear`` and drives ``format_review_status_summary``.

``compute_bot_arrivals`` is a *pure* function that returns the per-bot map; the
service assigns it onto ``status.bot_arrivals``. These tests do the same via the
``_arrivals`` helper so the model's ``blocking_bots`` / ``missing_bots`` reflect it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wade.models.review import (
    BotArrivalState,
    BotReaction,
    PRReview,
    PRReviewStatus,
    bot_login_matches,
    compute_bot_arrivals,
    format_review_status_summary,
    login_is_known_bot,
    newest_signal_for_bot,
)

_NOW = datetime(2026, 8, 10, 10, 10, 0, tzinfo=UTC)
_COMMIT = datetime(2026, 8, 10, 10, 0, 0, tzinfo=UTC)  # 600s before _NOW


def _annotate(
    status: PRReviewStatus,
    *,
    arrival_timeout: int,
    ack_timeout: int,
    now: datetime = _NOW,
    window_starts: dict[str, datetime] | None = None,
) -> PRReviewStatus:
    """Compute + assign the arrival map, mirroring ``annotate_bot_expectations``."""
    status.bot_arrivals = compute_bot_arrivals(
        status,
        now=now,
        arrival_timeout=arrival_timeout,
        ack_timeout=ack_timeout,
        window_starts=window_starts,
    )
    return status


class TestBotLoginMatches:
    """Bot name -> login matching, verified against real GitHub actor logins."""

    def test_coderabbit(self) -> None:
        assert bot_login_matches("coderabbit", "coderabbitai[bot]")
        assert bot_login_matches("coderabbit", "CodeRabbitAI")

    def test_codex_bare_and_bracket_forms(self) -> None:
        # GraphQL drops the [bot] suffix; REST keeps it — both must match.
        assert bot_login_matches("codex", "chatgpt-codex-connector")
        assert bot_login_matches("codex", "chatgpt-codex-connector[bot]")

    def test_bugbot_loose_match(self) -> None:
        assert bot_login_matches("bugbot", "cursor[bot]")
        assert bot_login_matches("bugbot", "bugbot")

    def test_no_match_for_unrelated_login(self) -> None:
        assert not bot_login_matches("coderabbit", "octocat")
        assert not bot_login_matches("codex", "coderabbitai[bot]")

    def test_unknown_bot_falls_back_to_name_substring(self) -> None:
        assert bot_login_matches("copilot", "github-copilot[bot]")
        assert not bot_login_matches("copilot", "octocat")

    def test_short_generic_name_requires_exact_login(self) -> None:
        # "ai" is too short to substring-match safely — would over-match otherwise.
        assert not bot_login_matches("ai", "coderabbitai[bot]")
        assert bot_login_matches("ai", "ai")
        assert bot_login_matches("ai", "ai[bot]")


class TestLoginIsKnownBot:
    def test_matches_known_bots_bracket_and_bracketless(self) -> None:
        assert login_is_known_bot("coderabbitai[bot]")
        assert login_is_known_bot("chatgpt-codex-connector")  # bracket-less
        assert login_is_known_bot("chatgpt-codex-connector[bot]")
        assert login_is_known_bot("cursor[bot]")

    def test_rejects_human_login(self) -> None:
        assert not login_is_known_bot("octocat")


class TestBotReaction:
    def test_positive_reactions_are_acknowledgements(self) -> None:
        for content in ("thumbs_up", "eyes", "rocket", "+1", "heart", "hooray"):
            assert BotReaction(login="x[bot]", content=content).is_acknowledgement

    def test_negative_reactions_are_not_acknowledgements(self) -> None:
        for content in ("thumbs_down", "confused", "-1", ""):
            assert not BotReaction(login="x[bot]", content=content).is_acknowledgement


class TestNewestSignalForBot:
    def test_none_when_no_signal(self) -> None:
        status = PRReviewStatus(latest_commit_pushed_at=_COMMIT)
        assert newest_signal_for_bot(status, "coderabbit") is None

    def test_review_submitted_at(self) -> None:
        ts = _COMMIT + timedelta(minutes=1)
        status = PRReviewStatus(
            reviews=[PRReview(author="chatgpt-codex-connector", submitted_at=ts, is_bot=True)]
        )
        assert newest_signal_for_bot(status, "codex") == ts

    def test_coderabbit_summary_marker_attributed(self) -> None:
        ts = _COMMIT + timedelta(minutes=2)
        status = PRReviewStatus(bot_status_ts=ts)
        assert newest_signal_for_bot(status, "coderabbit") == ts
        # Codex must NOT inherit CodeRabbit's summary marker.
        assert newest_signal_for_bot(status, "codex") is None


class TestComputeBotArrivals:
    def test_arrived_when_signal_covers_head(self) -> None:
        status = PRReviewStatus(
            expected_bots=["codex"],
            latest_commit_pushed_at=_COMMIT,
            reviews=[
                PRReview(
                    author="chatgpt-codex-connector",
                    submitted_at=_COMMIT + timedelta(minutes=1),
                    is_bot=True,
                )
            ],
        )
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        assert status.bot_arrivals["codex"].state == BotArrivalState.ARRIVED
        assert status.blocking_bots == []
        assert status.review_covers_latest_commit

    def test_awaiting_within_window(self) -> None:
        status = PRReviewStatus(expected_bots=["coderabbit"], latest_commit_pushed_at=_COMMIT)
        _annotate(status, arrival_timeout=3600, ack_timeout=7200)
        assert status.bot_arrivals["coderabbit"].state == BotArrivalState.AWAITING
        assert status.blocking_bots == ["coderabbit"]
        assert not status.review_covers_latest_commit
        assert not status.is_all_clear

    def test_missing_past_window(self) -> None:
        status = PRReviewStatus(expected_bots=["bugbot"], latest_commit_pushed_at=_COMMIT)
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        assert status.bot_arrivals["bugbot"].state == BotArrivalState.MISSING
        assert status.blocking_bots == []
        assert status.missing_bots == ["bugbot"]
        # Past-window bot no longer blocks all-clear.
        assert status.review_covers_latest_commit
        assert status.is_all_clear

    def test_reaction_extends_to_ack_window(self) -> None:
        # 600s since commit — past arrival_timeout=300, but within ack_timeout=900.
        status = PRReviewStatus(
            expected_bots=["codex"],
            latest_commit_pushed_at=_COMMIT,
            bot_reactions=[BotReaction(login="chatgpt-codex-connector[bot]", content="thumbs_up")],
        )
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        assert status.bot_arrivals["codex"].state == BotArrivalState.ACKNOWLEDGED
        assert status.blocking_bots == ["codex"]
        assert status.acknowledged_bots == ["codex"]
        assert not status.is_all_clear

    def test_reaction_still_missing_past_ack_window(self) -> None:
        # A reaction extends but does not remove the ceiling — past ack_timeout it
        # is MISSING, not blocking forever.
        status = PRReviewStatus(
            expected_bots=["codex"],
            latest_commit_pushed_at=_COMMIT,
            bot_reactions=[BotReaction(login="chatgpt-codex-connector[bot]", content="eyes")],
        )
        _annotate(status, arrival_timeout=100, ack_timeout=300)  # 600s > 300 ack ceiling
        assert status.bot_arrivals["codex"].state == BotArrivalState.MISSING

    def test_unknown_commit_never_blocks(self) -> None:
        status = PRReviewStatus(expected_bots=["coderabbit", "codex", "bugbot"])
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        assert status.blocking_bots == []
        assert status.review_covers_latest_commit

    def test_window_start_uses_later_of_commit_and_trigger(self) -> None:
        # Bot triggered only 60s before now — window measured from the trigger, so
        # it is still AWAITING despite the commit being older than arrival_timeout.
        trigger = _NOW - timedelta(seconds=60)
        status = PRReviewStatus(expected_bots=["coderabbit"], latest_commit_pushed_at=_COMMIT)
        _annotate(
            status, arrival_timeout=300, ack_timeout=900, window_starts={"coderabbit": trigger}
        )
        assert status.bot_arrivals["coderabbit"].state == BotArrivalState.AWAITING

    def test_stale_signal_blocks_until_window(self) -> None:
        # CodeRabbit reviewed an EARLIER commit — signal predates HEAD, so it is not
        # arrived-for-HEAD. Within a long window it blocks.
        status = PRReviewStatus(
            expected_bots=["coderabbit"],
            latest_commit_pushed_at=_COMMIT,
            bot_status_ts=_COMMIT - timedelta(minutes=30),
        )
        _annotate(status, arrival_timeout=3600, ack_timeout=7200)
        assert status.bot_arrivals["coderabbit"].state == BotArrivalState.AWAITING
        assert not status.review_covers_latest_commit

    def test_disabled_bot_absent_from_expectation(self) -> None:
        # Only expected_bots are considered — a config with codex disabled leaves
        # it out of the map entirely, so it never blocks.
        status = PRReviewStatus(expected_bots=["coderabbit"], latest_commit_pushed_at=_COMMIT)
        _annotate(status, arrival_timeout=3600, ack_timeout=7200)
        assert "codex" not in status.bot_arrivals


class TestExpectationAwareSummary:
    """format_review_status_summary surfaces awaited / missing bots (#448)."""

    def test_awaited_bot_shown_and_no_all_clear(self) -> None:
        status = PRReviewStatus(expected_bots=["coderabbit"], latest_commit_pushed_at=_COMMIT)
        _annotate(status, arrival_timeout=3600, ack_timeout=7200)
        messages = format_review_status_summary(status)
        joined = " ".join(m for _, m in messages)
        assert "coderabbit" in joined
        assert "Waiting" in joined
        assert not any("nothing to address" in m for _, m in messages)

    def test_acknowledged_bot_reported_as_reviewing(self) -> None:
        status = PRReviewStatus(
            expected_bots=["codex"],
            latest_commit_pushed_at=_COMMIT,
            bot_reactions=[BotReaction(login="chatgpt-codex-connector[bot]", content="thumbs_up")],
        )
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        messages = format_review_status_summary(status)
        joined = " ".join(m for _, m in messages).lower()
        assert "acknowledged" in joined

    def test_missing_bot_reported(self) -> None:
        status = PRReviewStatus(expected_bots=["bugbot"], latest_commit_pushed_at=_COMMIT)
        _annotate(status, arrival_timeout=300, ack_timeout=900)
        messages = format_review_status_summary(status)
        joined = " ".join(m for _, m in messages)
        assert "bugbot" in joined
        assert "No review" in joined

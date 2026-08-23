"""Review service — address PR review comments in an existing worktree.

Orchestrates: fetch review threads, format comments, launch AI tool,
post-session token tracking, and label management.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.hooks import SessionPhase
from wade.models.permission import PermissionMode, permission_mode_launch_kwargs
from wade.models.review import (
    BotArrivalState,
    BotTriggerOutcome,
    BotTriggerReport,
    BotTriggerResult,
    PollOutcome,
    PRReviewStatus,
    ReviewBotStatus,
    ReviewState,
    compute_bot_arrivals,
    detect_coderabbit_review_status,
    filter_actionable_threads,
    filter_unresolved_threads,
    format_review_threads_markdown,
)
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services import bot_trigger
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_network_access,
    resolve_permission_mode,
)
from wade.services.implementation_service import (
    _detect_ai_cli_env,
    _merge_pr,
    _resolve_worktrees_dir,
    append_review_usage_entry,
    bootstrap_worktree,
    extract_issue_from_branch,
)
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.review_settle import compute_effective_settle, latest_signal_ts
from wade.services.task_service import add_review_addressed_by_labels
from wade.ui.console import console
from wade.utils.body_markers import enforce_body_budget, update_body_preserving_markers
from wade.utils.markdown import append_session_to_body
from wade.utils.terminal import (
    compose_review_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Simple subcommands — used by AI agents during review sessions
# ---------------------------------------------------------------------------


def _find_existing_branch_for_issue(
    repo_root: Path, issue: str, preferred: str | None = None
) -> str | None:
    """Return the name of an existing branch that belongs to *issue*, or ``None``.

    Matches by the stable issue *number* — first the worktrees, then local and
    remote branches — never by a freshly slugified title. The slug is frozen into
    the branch at ``wade implement`` time, so a title edited afterward (commonly:
    the issue renamed to conventional-commit form when its PR opens) would make a
    reconstructed name drift from the real branch. A worktree's branch is
    preferred because it is the exact ref checked out for the issue.

    When more than one branch carries the issue number (e.g. a closed PR was
    retitled and implementation restarted), selection is deterministic:
    ``list_branch_names`` returns an unordered set, so prefer *preferred* — the
    name reconstructed from the issue's *current* title, i.e. the freshest
    branch — and otherwise fall back to sorted order rather than returning a
    hash-ordered (across-run nondeterministic) element.
    """
    with contextlib.suppress(GitError):
        for wt in git_worktree.list_worktrees(repo_root):
            if wt.branch and extract_issue_from_branch(wt.branch) == issue:
                return wt.branch

    # No live worktree (e.g. it was cleaned up while the PR stayed open) — fall
    # back to any local or remote branch carrying the issue number so the
    # remote-recovery path fetches the *real* branch rather than a drifted name.
    with contextlib.suppress(GitError):
        matches: set[str] = set()
        for name in git_branch.list_branch_names(repo_root):
            short = name[len("origin/") :] if name.startswith("origin/") else name
            if extract_issue_from_branch(short) == issue:
                matches.add(short)
        if preferred is not None and preferred in matches:
            return preferred
        if matches:
            return sorted(matches)[0]

    return None


def _resolve_task_branch(config: ProjectConfig, task: Task, repo_root: Path) -> str:
    """Resolve the branch name for a task by its stable issue *number*.

    Resolution order:

    1. the currently checked-out branch, when it already carries this issue
       (an in-worktree caller — authoritative);
    2. an existing worktree / local / remote branch carrying this issue number
       (see :func:`_find_existing_branch_for_issue`);
    3. a name reconstructed from the current title — only when nothing for this
       issue exists yet (first-time creation).

    Resolving by number rather than by re-slugifying ``task.title`` is
    deliberate: the branch slug is frozen at ``wade implement`` time, so a title
    edited afterward regenerates a *different* name and orphans the real
    worktree/PR — the "No worktree or remote branch found for issue #N" failure
    this guards against.
    """
    issue = str(int(task.id))

    try:
        current_branch = git_repo.get_current_branch(repo_root)
        if extract_issue_from_branch(current_branch) == issue:
            return current_branch
    except GitError:
        pass

    # Reconstruct the current-title name once: it is both the tiebreaker preference
    # for _find_existing_branch_for_issue (prefer the freshest branch when an issue
    # has several) and the fallback when no branch for the issue exists yet.
    reconstructed = git_branch.make_branch_name(
        config.project.branch_prefix,
        int(task.id),
        task.title,
    )
    existing = _find_existing_branch_for_issue(repo_root, issue, preferred=reconstructed)
    return existing if existing is not None else reconstructed


def fetch_reviews(
    target: str,
    project_root: Path | None = None,
) -> bool:
    """Fetch unresolved PR review comments and print formatted markdown to stdout.

    This is a tool for AI agents — it outputs structured markdown that the agent
    can read and act on.

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    # Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Could not read issue #{issue_number}: {e}")
        return False

    # Resolve the branch by issue number (worktree / local / remote), not by
    # re-slugifying the title — the title may have drifted since implement.
    branch_name = _resolve_task_branch(config, task, repo_root)

    lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
    if lookup.lookup_failed:
        console.error(f"Could not look up the PR for branch {branch_name} — try again shortly.")
        return False
    if not lookup.is_open or lookup.pr is None:
        console.error(f"No open PR found for branch {branch_name}")
        return False

    pr_number = lookup.pr.number

    # Fetch comprehensive review status
    status = get_comprehensive_review_status(provider, pr_number)
    if status.fetch_failed:
        print("Review status fetch failed — status may be incomplete. Try again shortly.")
        return False

    # Expectation gating (#448): this AI-agent-facing command must not print
    # "No unresolved review comments found." while an enabled bot hasn't reviewed
    # HEAD. ``repo_root`` is the worktree here (cwd), so its ``.wade/`` trigger
    # markers seed the arrival windows.
    annotate_bot_expectations(status, config, marker_root=repo_root)

    # all_unresolved_threads covers both actionable (non-outdated) and outdated threads;
    # falls back to actionable_threads for providers that don't set it.
    all_threads = status.effective_unresolved_threads
    outdated = [t for t in all_threads if t.is_outdated]

    if not all_threads:
        if status.bot_status == ReviewBotStatus.PAUSED:
            print("No unresolved review comments found, but CodeRabbit review is paused.")
            print("Comments may arrive when the review is resumed.")
        elif status.bot_status == ReviewBotStatus.IN_PROGRESS:
            print("No unresolved review comments found, but CodeRabbit is still reviewing.")
            print("Try fetching again shortly.")
        elif status.blocking_bots:
            # An expected bot has not reviewed HEAD within its window — name it
            # rather than falsely reporting nothing to address.
            for name in status.blocking_bots:
                arrival = status.bot_arrivals.get(name)
                if arrival and arrival.state == BotArrivalState.ACKNOWLEDGED:
                    print(
                        f"No unresolved review comments found yet, but {name} acknowledged"
                        " the PR and is still reviewing — comments may still arrive."
                    )
                else:
                    print(
                        f"No unresolved review comments found yet, but {name} has not"
                        " reviewed the latest commit — a review may still arrive."
                    )
        elif not status.review_covers_latest_commit:
            print(
                "No unresolved review comments found, but the latest commit has not"
                " been reviewed yet — an updated review may still arrive."
            )
        else:
            print("No unresolved review comments found.")
            for name in status.missing_bots:
                print(f"⚠ No review from {name} arrived within its window — proceeding.")

        # Show PR-level review info even when no threads
        if status.changes_requested_by:
            names = ", ".join(f"@{a}" for a in status.changes_requested_by)
            print(f"\nNote: Changes requested by {names} (PR-level review).")
            for review in status.latest_reviews_by_author.values():
                if review.state == ReviewState.CHANGES_REQUESTED and review.body:
                    print(f"\n@{review.author}'s review:\n{review.body}")
        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            print(f"\nAwaiting review from {names}.")
        return True

    # Output formatted markdown to stdout (for AI consumption).
    # Includes both actionable (non-outdated) and outdated threads; outdated ones are annotated.
    print(format_review_threads_markdown(all_threads))

    # Append PR-level changes_requested review bodies (if any)
    if status.changes_requested_by:
        pr_level_reviews = [
            r
            for r in status.latest_reviews_by_author.values()
            if r.state == ReviewState.CHANGES_REQUESTED and r.body
        ]
        if pr_level_reviews:
            print("\n## PR-Level Changes Requested\n")
            for review in pr_level_reviews:
                print(f"### @{review.author}'s review\n\n{review.body}\n")
        else:
            names = ", ".join(f"@{a}" for a in status.changes_requested_by)
            print(f"\n> **Note:** Changes also requested by {names} (PR-level review, no body).\n")

    if outdated:
        print(
            f"\n> **Note:** {len(outdated)} thread(s) above are outdated"
            " — they reference code that has since changed."
        )

    return True


def trigger_bot_reviews(
    target: str,
    *,
    selected_bots: list[str] | None = None,
    dry_run: bool = False,
    project_root: Path | None = None,
) -> BotTriggerReport:
    """Post external-bot review-trigger comments on an issue's PR (#431).

    Resolves issue → branch → **open** PR, then posts each configured bot's
    trigger phrase as a PR comment via ``git_pr.comment_on_pr``. That primitive
    is fail-fast (it raises ``GhCliError``), so each post is wrapped in its own
    try/except — one failing bot never aborts the rest.

    - ``selected_bots`` (``--bot``, repeatable) restricts to a named subset and
      **overrides** each named bot's ``enabled: false`` — an explicit request
      beats the config default. An unknown name is a hard error: no posts are
      made and the report lists the valid configured names.
    - ``dry_run`` reports what *would* be posted without posting.

    This manual path never reads or writes the auto-trigger
    ``.wade/bot-triggered-<bot>@<sha>`` markers — those belong solely to the
    ``done`` auto-trigger path — so an explicit trigger always fires and never
    suppresses a later same-SHA ``done`` auto-trigger.

    Returns a :class:`BotTriggerReport`; the caller derives the exit code from
    it (non-zero only when the PR can't be resolved, an unknown ``--bot`` name is
    given, or **every** attempted post fails).
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return BotTriggerReport(resolution_error="not inside a git repository")

    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        # Provider-controlled error text — disable markup so a bracket token in it
        # can't raise MarkupError instead of returning the structured exit-1 report.
        console.error(f"Could not read issue #{issue_number}: {e}", markup=False)
        return BotTriggerReport(resolution_error=f"could not read issue #{issue_number}")

    branch_name = _resolve_task_branch(config, task, repo_root)

    lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
    if lookup.lookup_failed:
        console.error(f"Could not look up the PR for branch {branch_name} — try again shortly.")
        return BotTriggerReport(resolution_error="PR lookup failed")
    if not lookup.is_open or lookup.pr is None:
        console.error_with_fix(
            f"No open PR found for branch {branch_name}",
            f"Run `wade implementation-session done` for #{task.id} to open a PR first",
        )
        return BotTriggerReport(resolution_error="no open PR")

    pr_number = lookup.pr.number
    bots = config.bot_review.bots
    configured_names = {bot.name for bot in bots}

    if selected_bots:
        unknown = [name for name in selected_bots if name not in configured_names]
        if unknown:
            valid = [bot.name for bot in bots]
            # `unknown` is arbitrary `--bot` user input (not the validated config
            # names), so it may carry Rich control tokens; escape before rendering
            # to markup-enabled console output so a stray token can't raise
            # MarkupError. `valid` is already safe, but escaping it is harmless.
            unknown_display = ", ".join(console.escape_markup(name) for name in unknown)
            valid_display = (
                ", ".join(console.escape_markup(name) for name in valid) if valid else "(none)"
            )
            console.error(f"Unknown bot name(s): {unknown_display}.")
            console.hint(f"Configured bots: {valid_display}")
            return BotTriggerReport(
                pr_number=pr_number, unknown_bots=unknown, valid_bot_names=valid
            )
        # Explicit selection overrides enabled:false. Iterate config order,
        # filtered to the requested set, so repeated --bot values are deduped and
        # ordering is deterministic.
        wanted = set(selected_bots)
        to_trigger = [bot for bot in bots if bot.name in wanted]
        force_enabled = True
    else:
        to_trigger = list(bots)
        force_enabled = False

    results: list[BotTriggerResult] = []
    for bot in to_trigger:
        if not force_enabled and not bot.enabled:
            results.append(
                BotTriggerResult(
                    name=bot.name, trigger=bot.trigger, outcome=BotTriggerOutcome.SKIPPED_DISABLED
                )
            )
            continue
        if dry_run:
            results.append(
                BotTriggerResult(
                    name=bot.name, trigger=bot.trigger, outcome=BotTriggerOutcome.DRY_RUN
                )
            )
            continue
        try:
            git_pr.comment_on_pr(repo_root, pr_number, bot.trigger)
            results.append(
                BotTriggerResult(
                    name=bot.name, trigger=bot.trigger, outcome=BotTriggerOutcome.POSTED
                )
            )
        except Exception as e:  # comment_on_pr is fail-fast — isolate per bot.
            logger.warning("review.bot_trigger_failed", bot=bot.name, error=str(e))
            results.append(
                BotTriggerResult(
                    name=bot.name,
                    trigger=bot.trigger,
                    outcome=BotTriggerOutcome.FAILED,
                    error=str(e),
                )
            )

    report = BotTriggerReport(pr_number=pr_number, results=results)

    console.rule(f"review trigger #{task.id}")
    console.kv("PR", f"#{pr_number}")
    for result in results:
        # status_line() embeds the configured bot name (and, on failure, the
        # error text) — escape it so a Rich control token can't raise MarkupError
        # in this markup-enabled output (even on --dry-run).
        line = console.escape_markup(result.status_line())
        if result.outcome is BotTriggerOutcome.FAILED:
            console.warn(line)
        else:
            console.detail(line)
    if report.all_attempts_failed:
        console.error("All bot triggers failed — see the errors above.")
    return report


def resolve_thread(
    thread_id: str,
    project_root: Path | None = None,
) -> bool:
    """Mark a PR review thread as resolved on GitHub.

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    try:
        success = provider.resolve_review_thread(thread_id)
    except NotImplementedError:
        console.error("Resolving review threads is not supported by this provider.")
        return False
    except Exception as e:
        console.error(f"Failed to resolve thread: {e}")
        return False

    if success:
        console.success(f"Thread {thread_id} resolved.")
    else:
        console.error(f"Failed to resolve thread {thread_id}.")
    return success


def count_unresolved_threads(
    project_root: Path | None = None,
) -> int | None:
    """Count unresolved, actionable review threads for the current branch's PR.

    Returns:
        Number of unresolved threads, or None if the check could not be performed
        (no git repo, no branch, no PR, provider error).
    """
    status = get_review_status(project_root)
    if status is None or status.fetch_failed:
        return None
    return len(status.actionable_threads)


def get_review_status(
    project_root: Path | None = None,
) -> PRReviewStatus | None:
    """Fetch comprehensive PR review status for the current branch's PR.

    Returns:
        A :class:`PRReviewStatus` with all review data, or ``None`` if the
        check could not be performed (no git repo, no branch, no PR, provider
        error, or provider doesn't support comprehensive status).
    """
    config = load_config(project_root)
    provider = get_provider(config)

    try:
        cwd = project_root or Path.cwd()
        repo_root = git_repo.get_repo_root(cwd)
        branch = git_repo.get_current_branch(repo_root)
    except (FileNotFoundError, GitError):
        return None

    issue_number = extract_issue_from_branch(branch)
    if not issue_number:
        return None

    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if not lookup.is_open or lookup.pr is None:
        return None

    pr_number = lookup.pr.number

    try:
        status = provider.get_pr_review_status(pr_number)
    except NotImplementedError:
        # Fallback: use legacy thread-only approach
        status = _fallback_review_status(provider, pr_number)
    except Exception:
        return None
    # Expectation gating (#448): annotate so ``is_all_clear`` /
    # ``format_review_status_summary`` (e.g. the ``done`` status recap) reflect
    # which enabled bots have not yet reviewed HEAD. ``repo_root`` is the worktree,
    # so its ``.wade/`` trigger markers seed the arrival windows.
    annotate_bot_expectations(status, config, marker_root=repo_root)
    return status


def get_comprehensive_review_status(
    provider: AbstractTaskProvider,
    pr_number: int,
) -> PRReviewStatus:
    """Fetch comprehensive PR review status using provider with fallback.

    Unlike :func:`get_review_status`, this accepts explicit parameters instead
    of resolving from the current branch. Used by ``start()`` and
    ``fetch_reviews()`` where the PR is already known.
    """
    try:
        return provider.get_pr_review_status(pr_number)
    except NotImplementedError:
        return _fallback_review_status(provider, pr_number)
    except Exception:
        logger.debug("review.comprehensive_status_failed", exc_info=True)
        return PRReviewStatus(fetch_failed=True)


def _fallback_review_status(
    provider: AbstractTaskProvider,
    pr_number: int,
) -> PRReviewStatus:
    """Build a PRReviewStatus from legacy thread-only + bot-status APIs.

    Used when the provider doesn't support ``get_pr_review_status()``.
    """
    try:
        all_threads = provider.get_pr_review_threads(pr_number)
    except Exception:
        return PRReviewStatus(fetch_failed=True)

    actionable = filter_actionable_threads(all_threads)
    all_unresolved = filter_unresolved_threads(all_threads)
    bot_status, bot_status_ts = _check_review_bot_status(provider, pr_number)

    return PRReviewStatus(
        actionable_threads=actionable,
        all_unresolved_threads=all_unresolved,
        bot_status=bot_status,
        bot_status_ts=bot_status_ts,
    )


def annotate_bot_expectations(
    status: PRReviewStatus,
    config: ProjectConfig,
    *,
    marker_root: Path | None = None,
    now: datetime | None = None,
) -> PRReviewStatus:
    """Populate expected-bot arrival state onto *status* in place (#448).

    Sets ``expected_bots`` (the enabled ``bot_review.bots`` names WADE expects to
    review) and computes the per-bot ``bot_arrivals`` map from the config
    arrival/ack timeouts. When ``marker_root`` is given, ``.wade/bot-triggered-
    <name>@*`` trigger markers seed each bot's arrival-window start; otherwise the
    window starts at the commit push time (the documented marker-absent fallback).

    A status that failed to fetch, or a config with no enabled bots, is left
    untouched — expectation gating is off in that case and the model behaves
    exactly as before this fix. Returns *status* for chaining.
    """
    if status.fetch_failed:
        return status
    enabled = [bot.name for bot in config.bot_review.bots if bot.enabled]
    status.expected_bots = enabled
    if not enabled:
        status.bot_arrivals = {}
        return status
    window_starts = (
        _bot_trigger_window_starts(marker_root, enabled) if marker_root is not None else {}
    )
    status.bot_arrivals = compute_bot_arrivals(
        status,
        now=now or datetime.now(UTC),
        arrival_timeout=config.bot_review.arrival_timeout,
        ack_timeout=config.bot_review.ack_timeout,
        window_starts=window_starts,
    )
    return status


def _bot_trigger_window_starts(marker_root: Path, bot_names: list[str]) -> dict[str, datetime]:
    """Newest ``.wade/bot-triggered-<name>@*`` marker mtime per bot (#448).

    The auto-trigger path writes one marker per bot per commit sha; its mtime is
    when WADE (re-)triggered that bot. Used as the per-bot arrival-window start so
    a bot triggered *after* the commit push is waited for from the trigger rather
    than the earlier push. Best-effort: an unreadable marker or missing ``.wade/``
    directory yields no entry, and ``compute_bot_arrivals`` then falls back to the
    commit push time.
    """
    starts: dict[str, datetime] = {}
    try:
        entries = list((marker_root / ".wade").iterdir())
    except OSError:
        return starts
    for name in bot_names:
        prefix = f"bot-triggered-{name}@"
        latest: datetime | None = None
        for entry in entries:
            if not entry.name.startswith(prefix):
                continue
            try:
                mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if latest is None or mtime > latest:
                latest = mtime
        if latest is not None:
            starts[name] = latest
    return starts


def poll_for_reviews(
    provider: AbstractTaskProvider,
    repo_root: Path,
    pr_number: int,
    branch: str,
    *,
    poll_interval: int = 60,
    bot_settle: int = 60,
    human_settle: int = 120,
    quiet_timeout: int = 600,
    config: ProjectConfig | None = None,
    marker_root: Path | None = None,
) -> PollOutcome:
    """Poll for new PR review comments, blocking until a terminal condition is reached.

    Checks every ``poll_interval`` seconds.  Returns a :class:`PollOutcome`:

    * ``COMMENTS_FOUND`` — actionable threads appeared; a settle period has elapsed.
    * ``QUIET_TIMEOUT`` — the PR has been quiet for ``quiet_timeout`` seconds after
      the latest commit aged past the grace period.
    * ``PR_CLOSED`` — the PR was merged or closed externally.
    * ``INTERRUPTED`` — the user pressed Ctrl+C.

    Expected-bot gating (#448): when ``config`` is provided, each cycle annotates
    the status with the enabled ``bot_review.bots`` expectation. While any expected
    bot has not posted a review covering HEAD *within its arrival window* the loop
    keeps waiting (reporting per-bot progress) and never returns ``REVIEW_COMPLETE``
    or lets the quiet-timeout fire with a clean "done". Once a bot's window elapses
    it stops blocking and is surfaced as missing. When ``config`` is ``None`` (a
    caller that predates this gating) the loop behaves as before. ``marker_root``
    identifies the worktree whose ``.wade/`` contains the trigger markers; it
    defaults to ``repo_root`` for callers already running inside that worktree.
    The marker-absent commit-push fallback is used for arrival-window starts (see
    ``compute_bot_arrivals``).
    """
    console.info("Waiting for review comments... (Ctrl+C to stop)")

    quiet_start: float | None = None

    try:
        while True:
            lookup = git_pr.get_pr_for_branch(repo_root, branch)
            # B2: a transient lookup failure must NOT end the wait — treat it
            # exactly like a fetch_failed below (reset the quiet timer, sleep,
            # retry). PR_CLOSED is reserved for an actual CLOSED/MERGED state or
            # a PR that genuinely no longer exists.
            if lookup.lookup_failed:
                quiet_start = None
                console.detail("PR lookup failed — retrying shortly...")
                time.sleep(poll_interval)
                continue
            if not lookup.found:
                console.info("PR is no longer open. Stopping poll.")
                return PollOutcome.PR_CLOSED
            if lookup.is_closed_or_merged:
                console.info(
                    f"PR #{pr_number} was {lookup.state.lower()} externally. Stopping poll."
                )
                return PollOutcome.PR_CLOSED

            status = get_comprehensive_review_status(provider, pr_number)

            if status.fetch_failed:
                quiet_start = None  # reset on transient failure
                console.detail("Fetch failed — retrying shortly...")
                time.sleep(poll_interval)
                continue

            # Expected-bot expectation (#448): compute per-bot arrival so the gate
            # and messaging below can tell "every expected bot has reviewed HEAD"
            # from "still waiting on X". Off when config is None. ``repo_root`` is
            # the worktree in the common in-worktree poll, so its ``.wade/`` trigger
            # markers seed the arrival windows (missing dir → commit-push fallback).
            if config is not None:
                annotate_bot_expectations(status, config, marker_root=marker_root or repo_root)

            if status.bot_status == ReviewBotStatus.IN_PROGRESS:
                quiet_start = None  # bot is active; reset quiet timer
                console.detail("Bot review in progress — checking again shortly...")
                time.sleep(poll_interval)
                continue

            eff_threads = status.effective_unresolved_threads
            if (
                not eff_threads
                and not status.has_changes_requested
                and not status.pending_reviewers
                and status.review_covers_latest_commit
                and (status.bot_status == ReviewBotStatus.COMPLETED or status.expected_bots)
            ):
                # Completion. A ``COMPLETED`` marker carries no info about *which*
                # commit was reviewed, and (#448) an expected bot may never have
                # posted at all — so completion requires ``review_covers_latest_commit``
                # (every expected bot arrived, or its window elapsed). Any bot that
                # never showed up is surfaced before we complete, never silently
                # swallowed into "done".
                if status.missing_bots:
                    names = ", ".join(f"`{b}`" for b in status.missing_bots)
                    console.warn(
                        f"Proceeding without a review from {names} — arrival window elapsed."
                    )
                console.info("Review bot completed — no actionable comments found.")
                return PollOutcome.REVIEW_COMPLETE

            if eff_threads or status.has_changes_requested:
                count = len(eff_threads)
                is_bot = status.bot_status is not None
                settle = bot_settle if is_bot else human_settle
                reviewer_type = "bot" if is_bot else "reviewer"

                settle_now = datetime.now(UTC)
                latest = latest_signal_ts(status)
                eff_settle = compute_effective_settle(
                    status, settle, poll_interval, settle_now, latest
                )

                if eff_threads:
                    if latest is None:
                        console.info(
                            f"Found {count} new review comment(s)."
                            f" Waiting {settle}s for {reviewer_type} to finish..."
                        )
                    elif eff_settle == 0:
                        age = int((settle_now - latest).total_seconds())
                        console.info(
                            f"Found {count} review comment(s) (newest {age}s old)"
                            f" — proceeding without settle wait."
                        )
                    else:
                        age = int((settle_now - latest).total_seconds())
                        console.info(
                            f"Found {count} review comment(s) (newest {age}s old)."
                            f" Waiting {eff_settle}s for {reviewer_type} to finish..."
                        )
                else:
                    names = ", ".join(f"@{a}" for a in status.changes_requested_by)
                    if latest is None:
                        console.info(
                            f"Changes requested by {names}."
                            f" Waiting {settle}s for {reviewer_type} to finish..."
                        )
                    elif eff_settle == 0:
                        age = int((settle_now - latest).total_seconds())
                        console.info(
                            f"Changes requested by {names} (newest {age}s old)"
                            f" — proceeding without settle wait."
                        )
                    else:
                        age = int((settle_now - latest).total_seconds())
                        console.info(
                            f"Changes requested by {names} (newest {age}s old)."
                            f" Waiting {eff_settle}s for {reviewer_type} to finish..."
                        )

                if eff_settle > 0:
                    time.sleep(eff_settle)
                return PollOutcome.COMMENTS_FOUND

            # Expected-bot gating (#448): an enabled bot that has not reviewed HEAD
            # within its arrival window keeps the loop waiting (never a clean
            # QUIET_TIMEOUT "done"), reporting per-bot progress. Once the window
            # elapses the bot becomes MISSING and stops blocking, so this cannot
            # hang forever. Takes precedence over the quiet-timeout logic below.
            if status.blocking_bots:
                quiet_start = None  # an expected bot is still awaited; hold the timer
                for name in status.blocking_bots:
                    arrival = status.bot_arrivals.get(name)
                    progress = (
                        f" ({arrival.waited_seconds}s/{arrival.window_seconds}s)"
                        if arrival and arrival.window_seconds
                        else ""
                    )
                    if arrival and arrival.state == BotArrivalState.ACKNOWLEDGED:
                        console.detail(
                            f"{name} acknowledged the PR and is still reviewing{progress}"
                            f" — next check in {poll_interval}s..."
                        )
                    else:
                        console.detail(
                            f"Waiting for {name} to review the latest commit{progress}"
                            f" — next check in {poll_interval}s..."
                        )
                time.sleep(poll_interval)
                continue

            # A bot signal exists but predates HEAD (stale coverage, legacy path) —
            # explain rather than go quiet. Under expectation gating this is already
            # handled by ``blocking_bots`` above.
            if not status.review_covers_latest_commit and not status.expected_bots:
                console.detail(
                    "A review covers an earlier commit, but the latest commit has not"
                    f" been reviewed yet — next check in {poll_interval}s..."
                )

            # No review signals, no bot blocking — apply quiet-timeout logic.
            if status.pending_reviewers:
                names = ", ".join(
                    f"@{r.name}{' (team)' if r.is_team else ''}" for r in status.pending_reviewers
                )
                console.detail(f"Awaiting review from {names} — next check in {poll_interval}s...")

            if status.is_commit_fresh():
                # Commit too recent; reset quiet timer and keep polling.
                quiet_start = None
                console.detail(
                    f"Commit is too recent for review — next check in {poll_interval}s..."
                )
            else:
                # Commit is old enough; start or advance the quiet timer.
                now = time.time()
                if quiet_start is None:
                    quiet_start = now
                elapsed = now - quiet_start
                if elapsed >= quiet_timeout:
                    # Never exit with a clean "done" while an expected bot never
                    # showed up (#448): name the missing bots so QUIET_TIMEOUT can't
                    # be read as "all bots reviewed".
                    if status.missing_bots:
                        names = ", ".join(f"`{b}`" for b in status.missing_bots)
                        console.warn(f"No review arrived from {names} within its window.")
                    console.info(
                        f"PR has been quiet for {int(elapsed)}s "
                        "with no new comments. Stopping poll."
                    )
                    return PollOutcome.QUIET_TIMEOUT
                console.detail(f"No new comments yet — next check in {poll_interval}s...")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.info("Polling stopped.")
        return PollOutcome.INTERRUPTED


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def start(
    target: str,
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    detach: bool = False,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    network_access: bool | None = None,
) -> bool:
    """Start a review-addressing session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Find existing worktree (or recover from remote branch)
    3. Find PR for the branch (error if missing or merged)
    4. Quick-check for unresolved review threads
    5. Install review-pr-comments-session skill, build prompt, launch AI
    6. Post-session: capture token usage, update PR, add labels
    7. Post-review lifecycle: "Merge PR" / "Wait for new reviews"

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    # Fold the --yolo alias into permission_mode up front so every downstream
    # path — including the pre-resolution quiet-exit menu that recurses into
    # start() — carries the same intent. An explicit permission_mode wins.
    if permission_mode is None and yolo:
        permission_mode = PermissionMode.YOLO.value
        permission_mode_explicit = True

    # 1. Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Could not read issue #{issue_number}: {e}")
        return False

    console.rule(f"review pr-comments #{task.id}")
    console.kv("Issue", console.issue_ref(task.id, task.title))

    # 2. Find existing worktree for the issue (or recover from remote branch)
    branch_name = _resolve_task_branch(config, task, repo_root)

    existing_wt = next(
        (
            Path(wt.path)
            for wt in git_worktree.list_worktrees(repo_root)
            if wt.branch == branch_name
        ),
        None,
    )

    if existing_wt:
        worktree_path = existing_wt
    else:
        # Try to recover: create worktree from the remote branch
        recovered = _recover_worktree(repo_root, branch_name, config)
        if not recovered:
            console.error_with_fix(
                f"No worktree or remote branch found for issue #{task.id}",
                f"Run `wade implement {task.id}` first to create a worktree",
            )
            return False
        worktree_path = recovered

    console.kv("Worktree", str(worktree_path))

    # 3. Find PR for the branch
    lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
    if lookup.lookup_failed:
        console.error_with_fix(
            f"Could not look up the PR for branch {branch_name}",
            "Transient gh error — try again shortly",
        )
        return False
    if not lookup.found or lookup.pr is None:
        console.error_with_fix(
            f"No open PR found for branch {branch_name}",
            "Run `wade implementation-session done` from the worktree to create a PR first",
        )
        return False

    pr_number = lookup.pr.number
    pr_state = lookup.state.upper()

    # Reject any non-open PR before fetching review status — matching
    # fetch_reviews' is_open gate. A CLOSED (not just MERGED) PR is not
    # actionable and must not continue into review operations.
    if not lookup.is_open:
        if pr_state == "MERGED":
            console.error(f"PR #{pr_number} is already merged — nothing to address.")
        else:
            console.error(
                f"PR #{pr_number} is {pr_state.lower() or 'not open'} — nothing to address."
            )
        return False

    console.kv("PR", f"#{pr_number}")

    # 4. Quick-check for unresolved review threads via comprehensive status
    console.step("Checking for review comments...")
    status = get_comprehensive_review_status(provider, pr_number)
    if status.fetch_failed:
        console.warn("Review status fetch failed — status may be incomplete. Try again shortly.")
        return False
    # Expectation gating (#448): don't emit "All review comments resolved" while an
    # enabled bot hasn't reviewed HEAD. Seed arrival windows from the worktree's
    # ``.wade/`` trigger markers.
    annotate_bot_expectations(status, config, marker_root=worktree_path)
    # Use all_unresolved_threads (actionable + outdated) as the broader review signal;
    # falls back to actionable_threads for providers that don't set it.
    effective_threads = status.effective_unresolved_threads
    file_paths = {
        t.first_comment.path for t in effective_threads if t.first_comment and t.first_comment.path
    }
    file_count = len(file_paths) + (
        1 if any(t.first_comment and not t.first_comment.path for t in effective_threads) else 0
    )
    comment_count = len(effective_threads)

    # --- No review signals: check bot status, commit freshness, quiet-exit ---
    if not effective_threads and not status.has_changes_requested:
        if status.bot_status == ReviewBotStatus.PAUSED:
            console.warn(
                "CodeRabbit review is paused — comments may arrive when resumed.\n"
                f"    Run `wade review trigger {task.id}` to post a fresh bot review trigger."
            )
            return True
        if status.bot_status == ReviewBotStatus.IN_PROGRESS:
            console.warn("CodeRabbit is still reviewing — try again shortly.")
            return True

        # No blocking conditions — message depends on commit freshness, expected-bot
        # arrival, and whether a bot review actually covers the latest commit.
        if status.is_commit_fresh():
            console.info(
                "No review comments found yet — the latest commit is less"
                " than 2 minutes old. Review may still arrive."
            )
        elif status.blocking_bots:
            # An expected bot has not reviewed HEAD within its window (#448) — name
            # it instead of falsely reporting all-clear.
            for name in status.blocking_bots:
                arrival = status.bot_arrivals.get(name)
                progress = (
                    f" ({arrival.waited_seconds}s/{arrival.window_seconds}s)"
                    if arrival and arrival.window_seconds
                    else ""
                )
                if arrival and arrival.state == BotArrivalState.ACKNOWLEDGED:
                    console.info(
                        f"{name} acknowledged the PR and is still reviewing{progress}"
                        " — comments may still arrive."
                    )
                else:
                    console.info(f"Still waiting for {name} to review the latest commit{progress}.")
        elif not status.review_covers_latest_commit:
            console.info(
                "No review comments found yet, but the latest commit has not"
                " been reviewed yet — an updated review may still arrive."
            )
        elif not status.pending_reviewers:
            if status.missing_bots:
                names = ", ".join(status.missing_bots)
                console.warn(f"⚠ No review from {names} arrived within its window — proceeding.")
            else:
                console.success("All review comments resolved — nothing to address! 🎉")

        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            console.info(f"Awaiting review from {names}.")
            return True

        # Offer the shared quiet-exit menu: keep polling / merge / exit.
        _quiet_next_steps_prompt(
            repo_root,
            branch_name,
            task.id,
            worktree_path,
            pr_number,
            provider,
            ai_tool=ai_tool,
            model=model,
            detach=detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            permission_mode=permission_mode,
            permission_mode_explicit=permission_mode_explicit,
            network_access=network_access,
            config=config,
        )
        return True

    # --- We have review signals: proceed with session ---
    if effective_threads:
        outdated_count = sum(1 for t in effective_threads if t.is_outdated)
        outdated_note = f" ({outdated_count} on outdated code)" if outdated_count else ""
        console.info(
            f"Found {comment_count} unresolved comment(s)"
            f" across {file_count} location(s){outdated_note}"
        )
    else:
        # Only PR-level changes_requested, no inline threads
        names = ", ".join(f"@{a}" for a in status.changes_requested_by)
        console.info(f"Changes requested by {names} (PR-level review) — launching review session")

    # 5. Re-bootstrap skills (ensures review-pr-comments-session skill is installed)
    from wade.skills.installer import REVIEW_SKILLS

    bootstrap_worktree(
        worktree_path, config, repo_root, skills=REVIEW_SKILLS, session_phase=SessionPhase.REVIEW
    )

    # 6. Resolve AI tool, model, effort, and autonomy under the dedicated
    # ``review_pr_comments`` config key (#389) so this auto-launched session
    # honors ``ai.review_pr_comments.*`` rather than inheriting ``ai.implement.*``.
    #
    # Gate every inherited value on explicitness. The implementation flow
    # forwards its *already-resolved* tool / model / permission-mode (concrete
    # values, e.g. ``"copilot"`` / ``"default"``, never ``None``); passed through
    # unconditionally they short-circuit the resolvers (which honor a non-``None``
    # first arg) and shadow ``ai.review_pr_comments`` entirely — so switching the
    # command key alone is a no-op. Honor an inherited value only when the user
    # set it explicitly (``--ai`` / ``--model`` / ``--permission-mode`` /
    # ``--yolo``); otherwise pass ``None`` so the review config (then global
    # ``ai.*``, then ``default`` / auto-detect) governs. (``effort`` is never
    # inherited as a concrete value — no caller forwards it — so it needs no gate.)
    effective_ai_tool = ai_tool if ai_explicit else None
    effective_model = model if model_explicit else None
    effective_pm = permission_mode if permission_mode_explicit else None
    resolved_tool = resolve_ai_tool(effective_ai_tool, config, "review_pr_comments")
    resolved_model = resolve_model(
        effective_model,
        config,
        "review_pr_comments",
        tool=resolved_tool,
        complexity=task.complexity.value if task.complexity else None,
    )
    resolved_effort = resolve_effort(
        effort,
        config,
        "review_pr_comments",
        tool=resolved_tool,
        complexity=task.complexity.value if task.complexity else None,
    )
    resolved_permission_mode = resolve_permission_mode(
        effective_pm, yolo, config, "review_pr_comments"
    )
    # Codex sandbox network policy (default disabled); always pinned explicitly
    # at launch so ambient Codex config can never silently enable it.
    resolved_network_access = resolve_network_access(network_access, config, "review_pr_comments")

    if not detach:
        (
            resolved_tool,
            resolved_model,
            resolved_effort,
            resolved_permission_mode,
        ) = confirm_ai_selection(
            resolved_tool,
            resolved_model,
            tool_explicit=ai_explicit,
            model_explicit=model_explicit,
            resolved_effort=resolved_effort,
            effort_explicit=effort_explicit,
            resolved_permission_mode=resolved_permission_mode,
            permission_mode_explicit=permission_mode_explicit or yolo is not None,
        )

    # 7. Build review prompt
    prompt = build_review_prompt(
        task=task,
        pr_number=pr_number,
        comment_count=comment_count,
        file_count=file_count,
    )

    snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
    console.panel(snippet, title="Review Prompt (preview)")

    # AI-initiated start guard
    detected_env = _detect_ai_cli_env()
    if detected_env:
        logger.info(
            "review.ai_launch_skipped",
            reason="inside_ai_cli",
            env_var=detected_env,
        )
        console.info(
            f"Skipping AI launch: already inside AI session (detected via {detected_env})."
        )
        console.detail(f"Worktree ready at: {worktree_path}")
        print(str(worktree_path))
        return True

    # Set terminal title
    review_title = compose_review_title(task.id, task.title)
    set_terminal_title(review_title)
    start_title_keeper(review_title)

    # Transcript capture
    transcript_path: Path | None = None
    try:
        transcript_dir = tempfile.mkdtemp(prefix="wade-review-")
        transcript_path = Path(transcript_dir) / f"transcript-review-{task.id}.log"
        console.hint(f"Transcript: {transcript_path}")
    except OSError:
        logger.warning("review.transcript_dir_failed")

    # 8. Detach mode
    if detach and resolved_tool:
        try:
            detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
            deliver_prompt_if_needed(detach_adapter, prompt)
            cmd = detach_adapter.build_launch_command(
                model=resolved_model,
                trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                initial_message=prompt,
                effort=resolved_effort,
                working_dir=worktree_path,
                network_access=resolved_network_access,
                **permission_mode_launch_kwargs(resolved_permission_mode),
            )
        except (ValueError, KeyError):
            cmd = [resolved_tool]

        console.step(f"Launching {resolved_tool} in new terminal...")
        if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=review_title):
            console.success(f"Detached review session for #{task.id}")
            stop_title_keeper()
            return True
        console.warn("Could not launch in new terminal — falling back to inline")

    # 9. Launch AI tool inline
    if resolved_tool:
        console.step(f"Launching {resolved_tool}...")

        adapter: AbstractAITool | None = None
        launch_completed = False
        detected_model: str | None = None
        try:
            adapter = AbstractAITool.get(AIToolID(resolved_tool))

            deliver_prompt_if_needed(adapter, prompt)
            exit_code = adapter.launch(
                working_dir=worktree_path,
                model=resolved_model,
                prompt=prompt,
                transcript_path=transcript_path,
                trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                effort=resolved_effort,
                network_access=resolved_network_access,
                **permission_mode_launch_kwargs(resolved_permission_mode),
            )
            launch_completed = True
            logger.info("review.ai_exited", exit_code=exit_code, tool=resolved_tool)

            if not adapter.capabilities().blocks_until_exit:
                from wade.ui import prompts as ui_prompts

                console.empty()
                if not ui_prompts.confirm("Have you finished the review session?", default=True):
                    console.info(
                        "Worktree preserved — run"
                        " 'wade review-pr-comments-session done'"
                        " when ready."
                    )
                    launch_completed = False
        except (ValueError, KeyError):
            console.warn(f"Unknown AI tool: {resolved_tool}")
        except Exception as e:
            console.warn(f"AI tool launch failed: {e}")
        finally:
            stop_title_keeper()

            if (
                adapter is not None
                and launch_completed
                and adapter.capabilities().blocks_until_exit
            ):
                detected_model = _capture_review_session_usage(
                    transcript_path=transcript_path,
                    adapter=adapter,
                    repo_root=repo_root,
                    branch=branch_name,
                    ai_tool=resolved_tool,
                    model=resolved_model,
                    issue_number=task.id,
                    provider=provider,
                )

        if launch_completed:
            effective_model = resolved_model or detected_model
            try:
                add_review_addressed_by_labels(provider, task.id, resolved_tool, effective_model)
            except Exception as e:
                console.warn(f"Could not apply review-addressed-by labels: {e}")
                logger.warning("review.review_addressed_by_labels_failed", error=str(e))

            # 10. Post-review lifecycle: "Merge PR" / "Wait for new reviews"
            _post_review_lifecycle(
                repo_root,
                branch_name,
                task.id,
                worktree_path,
                pr_number,
                provider,
                ai_tool=resolved_tool,
                model=effective_model,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                permission_mode=resolved_permission_mode.value,
                permission_mode_explicit=permission_mode_explicit,
                network_access=network_access,
                config=config,
            )
    else:
        console.info(
            "No AI tool configured — use `wade review-pr-comments-session fetch` to view comments."
        )
        console.detail(f"cd {worktree_path}")
        stop_title_keeper()

        # 10. Post-review lifecycle (no AI tool — user addressed manually)
        _post_review_lifecycle(
            repo_root,
            branch_name,
            task.id,
            worktree_path,
            pr_number,
            provider,
            ai_tool=ai_tool,
            model=model,
            detach=detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            permission_mode=permission_mode,
            permission_mode_explicit=permission_mode_explicit,
            network_access=network_access,
            config=config,
        )

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recover_worktree(
    repo_root: Path,
    branch_name: str,
    config: object,
) -> Path | None:
    """Try to recover a worktree from an existing remote branch.

    If the branch exists on the remote (e.g. from a PR), fetch it and
    create a new worktree pointing at it.

    Returns the worktree path on success, or None if the branch doesn't exist.
    """
    from wade.models.config import ProjectConfig

    assert isinstance(config, ProjectConfig)

    # Fetch the branch from remote
    try:
        git_repo.fetch_ref(repo_root, "origin", f"{branch_name}:{branch_name}")
    except GitError:
        logger.debug("review.fetch_branch_failed", branch=branch_name)
        return None

    # Verify the branch exists locally after fetch
    try:
        git_repo.rev_parse(repo_root, branch_name)
    except GitError:
        return None

    # Build worktree path
    worktrees_dir = _resolve_worktrees_dir(config, repo_root)
    repo_name = repo_root.name
    worktree_path = worktrees_dir / repo_name / branch_name.replace("/", "-")

    if worktree_path.exists():
        logger.debug("review.worktree_dir_exists", path=str(worktree_path))
        return None

    console.step(f"Recovering worktree from remote branch {branch_name}...")
    try:
        result = git_worktree.checkout_existing_branch_worktree(
            repo_root, branch_name, worktree_path
        )
        console.success(f"Recovered worktree at {result}")
        return result
    except Exception as e:
        logger.warning("review.worktree_recovery_failed", error=str(e))
        return None


def _quiet_next_steps_prompt(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    pr_number: int,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort: str | None = None,
    effort_explicit: bool = False,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    network_access: bool | None = None,
    config: ProjectConfig | None = None,
) -> None:
    """Shared next-steps menu for quiet PRs: keep polling, merge, or exit.

    Used both when ``wade review pr-comments <issue>`` finds nothing to address
    and when the polling loop hits the quiet timeout.

    ``network_access`` carries the caller's explicit ``--network`` / ``--no-network``
    (``None`` = unset) into a "keep polling → comments found" re-launch, so a later
    session preserves that decision instead of silently re-resolving to config.
    """
    from wade.ui import prompts

    if not prompts.is_tty():
        return

    while True:
        allow_merge = True
        status = get_comprehensive_review_status(provider, pr_number)
        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            console.info(
                f"Awaiting review from {names}. Merge is unavailable while review is pending."
            )
            allow_merge = False

        console.empty()
        options = (
            ["Keep polling", "Merge PR", "Exit without merging"]
            if allow_merge
            else ["Keep polling", "Exit without merging"]
        )
        # A quiet PR is exactly where an un-triggered bot shows up as silence, so
        # offer the trigger before the user decides to keep waiting (#464). The
        # entry is appended, never inserted, so the existing choices keep their
        # indexes. Hidden once every enabled bot has fired for this commit.
        bot_config, trigger_option = bot_trigger.menu_entry(
            repo_root, branch, worktree_path, config=config
        )
        # Index bound at append time so a later option can't steal this branch.
        trigger_index = -1
        if trigger_option:
            trigger_index = len(options)
            options.append(trigger_option)
        choice = prompts.select(f"PR #{pr_number} — what next?", options)

        if bot_config is not None and choice == trigger_index:
            if bot_trigger.post_pending_triggers(
                bot_config, repo_root, branch, pr_number, worktree_path or repo_root
            ):
                # The freshly written trigger markers reset each bot's arrival
                # window. Retain the config even when this prompt was reached
                # from an ordinary poll (where ``config`` is None), so a later
                # "Keep polling" tracks the newly requested bots.
                config = bot_config
            continue  # Re-display the menu — the user can now keep polling.

        if choice == 0:  # Keep polling
            outcome = poll_for_reviews(
                provider,
                repo_root,
                pr_number,
                branch,
                config=config,
                marker_root=worktree_path or repo_root,
            )
            if outcome == PollOutcome.COMMENTS_FOUND:
                if issue_number:
                    _ = start(
                        str(issue_number),
                        ai_tool=ai_tool,
                        model=model,
                        project_root=repo_root,
                        detach=detach,
                        ai_explicit=ai_explicit,
                        model_explicit=model_explicit,
                        effort=effort,
                        effort_explicit=effort_explicit,
                        permission_mode=permission_mode,
                        permission_mode_explicit=permission_mode_explicit,
                        network_access=network_access,
                    )
                return
            elif outcome in (PollOutcome.QUIET_TIMEOUT, PollOutcome.REVIEW_COMPLETE):
                continue  # Show menu again
            else:  # INTERRUPTED or PR_CLOSED
                return
        elif allow_merge and choice == 1:  # Merge PR
            _merge_pr(repo_root, branch, pr_number, issue_number, worktree_path, provider)
            return
        else:  # Exit without merging
            return


def _post_review_lifecycle(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    pr_number: int,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    network_access: bool | None = None,
    config: ProjectConfig | None = None,
) -> None:
    """Post-review lifecycle menu: Merge PR or wait for new reviews.

    Effort is intentionally *not* threaded through here (#389). A review session
    never carries an explicit effort — ``wade review pr-comments`` has no
    ``--effort`` flag and the post-``done`` auto-launch never passes one — so on a
    "wait for new reviews" re-launch the recursed ``start()`` re-resolves effort
    from ``ai.review_pr_comments.effort`` (config governs). That matches how a
    non-explicit ``permission_mode`` re-resolves under the gating in ``start``.

    ``network_access`` *is* threaded (unlike effort): ``wade review pr-comments``
    has a ``--network`` / ``--no-network`` flag, so an explicit pin must survive a
    "wait for new reviews" re-launch rather than re-resolve to config. ``None``
    (unset) still re-resolves, matching the non-explicit ``permission_mode`` path.
    """
    from wade.ui import prompts

    if not prompts.is_tty():
        return

    console.empty()
    # Same offer as the post-implementation menu (#464): the review session's
    # `done` pushed fixups, and with `auto_trigger` off nothing asked the bots to
    # look again — which is precisely when "wait for new reviews" waits forever.
    bot_config, trigger_option = bot_trigger.menu_entry(
        repo_root, branch, worktree_path, suffix=", then wait", config=config
    )
    options = ["Merge PR", "Wait for new reviews"]
    # Index bound at append time so a later option can't steal this branch.
    trigger_index = -1
    if trigger_option:
        trigger_index = len(options)
        options.append(trigger_option)

    choice = prompts.select(f"PR #{pr_number} — what next?", options)

    if bot_config is not None and choice == trigger_index:
        if bot_trigger.post_pending_triggers(
            bot_config, repo_root, branch, pr_number, worktree_path or repo_root
        ):
            choice = 1  # ...then fall through into the wait-for-new-reviews flow.
        else:
            # Every trigger post failed (e.g. a GitHub outage) — no bot was asked
            # to review, so don't drop into a wait for a review no one requested
            # (the exact silent-wait this option exists to avoid).
            return

    if choice == 1:  # Wait for new reviews
        outcome = poll_for_reviews(
            provider,
            repo_root,
            pr_number,
            branch,
            config=config,
            marker_root=worktree_path or repo_root,
        )
        if outcome == PollOutcome.COMMENTS_FOUND:
            if issue_number:
                _ = start(
                    str(issue_number),
                    ai_tool=ai_tool,
                    model=model,
                    project_root=repo_root,
                    detach=detach,
                    ai_explicit=ai_explicit,
                    model_explicit=model_explicit,
                    permission_mode=permission_mode,
                    permission_mode_explicit=permission_mode_explicit,
                    network_access=network_access,
                )
        elif outcome in (PollOutcome.QUIET_TIMEOUT, PollOutcome.REVIEW_COMPLETE):
            _quiet_next_steps_prompt(
                repo_root,
                branch,
                issue_number,
                worktree_path,
                pr_number,
                provider,
                ai_tool=ai_tool,
                model=model,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                permission_mode=permission_mode,
                permission_mode_explicit=permission_mode_explicit,
                network_access=network_access,
                config=config,
            )
        return

    # Merge flow — reuse the same merge logic as post-implementation lifecycle
    _merge_pr(repo_root, branch, pr_number, issue_number, worktree_path, provider)


def _check_review_bot_status(
    provider: AbstractTaskProvider,
    pr_number: int,
) -> tuple[ReviewBotStatus | None, datetime | None]:
    """Check if a review bot (e.g. CodeRabbit) has a pending review on the PR.

    Returns ``(status, updated_at)`` — the CodeRabbit summary comment's
    ``updated_at`` participates in commit-staleness detection.
    """
    try:
        comments = provider.get_pr_issue_comments(pr_number)
    except Exception:
        logger.debug("review.bot_status_check_failed", exc_info=True)
        return None, None
    return detect_coderabbit_review_status(comments)


def build_review_prompt(
    task: Task,
    pr_number: int,
    comment_count: int,
    file_count: int,
) -> str:
    """Build the initial prompt for a review session."""
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "review-pr-comments.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Review prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        issue_number=task.id,
        issue_title=task.title,
        pr_number=pr_number,
        comment_count=comment_count,
        file_count=file_count,
    )


def _capture_review_session_usage(
    transcript_path: Path | None,
    adapter: AbstractAITool,
    repo_root: Path,
    branch: str,
    ai_tool: str,
    model: str | None,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
) -> str | None:
    """Post-AI-exit processing: parse transcript, update PR and issue with review usage.

    Returns the primary model detected from the transcript.
    """
    if not transcript_path or not transcript_path.is_file():
        return None

    try:
        usage = adapter.parse_transcript(transcript_path)
    except Exception as e:
        logger.warning("review.transcript_parse_failed", error=str(e))
        return None

    has_tokens = usage and (usage.total_tokens or usage.input_tokens)
    has_session = usage and usage.session_id
    if not has_tokens and not has_session:
        logger.warning("review.no_token_usage", transcript=str(transcript_path))
        console.warn(f"No token usage found in transcript: {transcript_path}")
        return None

    effective_model = model or (
        usage.model_breakdown[0].model if usage and usage.model_breakdown else None
    )

    # Update PR body with review usage stats — only on an OPEN PR (a
    # merged/closed PR is not ours to rewrite; a lookup failure is transient).
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.is_open and lookup.pr is not None:
        pr_number = lookup.pr.number
        assert usage is not None
        usage_val = usage
        session_id = usage.session_id

        def _review_transform(body: str) -> str:
            # Rewrite only wade's own review-usage / sessions marker blocks so a
            # concurrent edit elsewhere survives (A4).
            body = append_review_usage_entry(
                body, ai_tool=ai_tool, model=effective_model, token_usage=usage_val
            )
            if has_session and session_id is not None:
                body = append_session_to_body(
                    body, phase="Review", ai_tool=ai_tool, session_id=session_id
                )
            return body

        try:
            if update_body_preserving_markers(
                read_body=lambda: git_pr.get_pr_body(repo_root, pr_number),
                write_body=lambda b: git_pr.update_pr_body(repo_root, pr_number, b),
                transform=_review_transform,
                warn=console.warn,
                label=f"PR #{pr_number} body",
            ):
                console.success("Updated PR with review usage stats.")
                logger.info(
                    "review.usage_updated",
                    pr=pr_number,
                    total_tokens=usage.total_tokens if usage else None,
                )
        except Exception:
            logger.debug("review.pr_body_read_failed", exc_info=True)
    else:
        logger.debug("review.no_pr_for_branch", branch=branch)

    # Update issue body with review usage stats
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            new_body = task.body
            assert usage is not None
            new_body = append_review_usage_entry(
                new_body,
                ai_tool=ai_tool,
                model=effective_model,
                token_usage=usage,
            )
            if has_session:
                assert usage is not None and usage.session_id is not None
                new_body = append_session_to_body(
                    new_body, phase="Review", ai_tool=ai_tool, session_id=usage.session_id
                )
            new_body = enforce_body_budget(
                new_body, warn=console.warn, label=f"issue #{issue_number} body"
            )
            provider.update_task(str(issue_number), body=new_body)
            console.success("Updated issue with review usage stats.")
            logger.info("review.usage_issue_updated", issue=issue_number)

    return effective_model

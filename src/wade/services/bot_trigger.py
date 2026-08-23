"""Marker-aware external-bot review triggering (#431, #464).

Two distinct paths post the trigger phrases configured under ``bot_review.bots``:

- **Explicit** — ``wade review trigger <issue>``
  (:func:`wade.services.review_service.trigger_bot_reviews`). Resolves issue →
  branch → PR itself and **ignores** the per-sha markers: an explicit request
  always fires.
- **Marker-aware** — this module. Used by every surface that offers the trigger
  as a side effect of finishing a session (``done``'s auto-trigger/offer, the
  post-session menus). The caller already holds the PR number, so there is no
  issue resolution here; instead each bot fires **at most once per commit sha**,
  recorded by a ``.wade/bot-triggered-<name>@<sha>`` marker, so repeated
  ``done`` / menu passes on the same commit never re-spam the PR. The
  check -> post -> marker section runs under a cross-process lock with an
  under-lock re-check (:func:`post_bot_triggers`), so the guarantee holds even
  when two such processes act on one worktree at once, not just against
  sequential re-runs.

Keeping the two paths separate is deliberate: the manual command never writes
these markers, so an explicit trigger can never suppress a later same-sha
automatic one (and vice versa).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import ProjectConfig, ReviewBotConfig
from wade.ui.console import console
from wade.utils import markers
from wade.utils.filelock import file_lock

logger = structlog.get_logger()

__all__ = [
    "MENU_LABEL",
    "format_bot_names",
    "marker_name",
    "menu_entry",
    "pending_bots",
    "pending_names",
    "post_bot_triggers",
    "post_pending_triggers",
    "resolve_branch_sha",
]

#: Shared prefix for the post-session menu entry, so every menu that offers the
#: triggers reads identically.
MENU_LABEL = "Trigger bot reviews"


def marker_name(bot_name: str) -> str:
    """Marker basename recording that *bot_name* was triggered at a given sha.

    ``bot_name`` is a validated safe identifier (:func:`wade.models.config.
    is_valid_bot_name`), so this can never escape ``.wade/``.
    """
    return f"bot-triggered-{bot_name}"


def _trigger_lock_path(marker_root: Path, sha: str) -> Path:
    """Lock-file identity serializing the check -> post -> marker section.

    Keyed by *marker_root* (the worktree whose ``.wade/`` holds the anti-spam
    markers) and *sha*, so two ``done`` / post-session-menu processes acting on
    the same worktree at the same commit rendezvous on one lock — closing the
    TOCTOU window where both read a marker absent, both post, and both record it
    (only afterward). :func:`wade.utils.filelock.file_lock` locks a temp-dir
    sibling keyed by this path's resolved identity and never creates the path
    itself, so this is a name to hash, not a file that appears in ``.wade/``.
    """
    return marker_root / f".wade-bot-trigger@{sha}"


def resolve_branch_sha(repo_root: Path, branch: str) -> str | None:
    """Resolve *branch*'s tip sha, or ``None`` when git cannot (never raises).

    Every caller uses the sha only to key the anti-spam markers, so an
    unresolvable branch means "cannot dedupe" — callers skip triggering rather
    than risk posting the same comment on every pass.
    """
    try:
        return git_repo.rev_parse(repo_root, branch)
    except GitError:
        logger.debug("bot_trigger.sha_resolve_failed", branch=branch, exc_info=True)
        return None


def pending_bots(config: ProjectConfig, marker_root: Path, sha: str) -> list[ReviewBotConfig]:
    """Enabled bots that have **not** been triggered at *sha* yet.

    Disabled bots are never included — an explicit ``wade review trigger --bot
    <name>`` is the only way to fire one of those.
    """
    return [
        bot
        for bot in config.bot_review.bots
        if bot.enabled and not markers.marker_present(marker_root, marker_name(bot.name), sha)
    ]


def format_bot_names(bots: list[ReviewBotConfig]) -> str:
    """Comma-joined bot names for a console line or a menu label.

    ``escape_markup`` is belt-and-suspenders: a ``name`` is a validated safe
    identifier, so escaping is a no-op today — which is why the same rendering is
    safe both inside a Rich-styled string and in a questionary menu label (where
    escaped markup would otherwise show up literally).
    """
    return ", ".join(console.escape_markup(bot.name) for bot in bots)


def pending_names(
    config: ProjectConfig, repo_root: Path, pr_number: int, marker_root: Path
) -> str | None:
    """Bot names a post-session menu should offer to trigger, or ``None`` (#464).

    ``None`` — hide the menu entry — when the user opted out
    (``bot_review.offer_on_done: false``), no bot is enabled, the PR head SHA is
    unresolvable (markers cannot dedupe), or every enabled bot was already
    triggered at that head (including by ``done``'s auto-trigger moments
    earlier). Callers compose their own label around the returned names so each
    menu can say what happens next ("…, then wait").
    """
    if not config.bot_review.offer_on_done:
        return None
    if not any(bot.enabled for bot in config.bot_review.bots):
        return None
    sha = git_pr.get_pr_head_sha(repo_root, pr_number)
    if sha is None:
        return None
    pending = pending_bots(config, marker_root, sha)
    return format_bot_names(pending) if pending else None


def menu_entry(
    repo_root: Path,
    pr_number: int,
    worktree_path: Path | None,
    *,
    suffix: str = "",
    config: ProjectConfig | None = None,
) -> tuple[ProjectConfig | None, str | None]:
    """Build the optional "Trigger bot reviews" entry for a post-session menu (#464).

    Returns ``(config, label)``. ``label`` is ``None`` whenever the entry must be
    hidden (see :func:`pending_names`) — including when anything about resolving
    it fails: an offer is a convenience, so it degrades to "menu unchanged"
    rather than breaking the merge/wait menu it decorates. The resolved config is
    handed back so the caller can post without loading it a second time; it is
    ``None`` exactly when ``label`` is.

    *suffix* lets each menu spell out what happens after the post (e.g.
    ``", then wait"``), since some menus fall straight through into polling and
    others re-display themselves.
    """
    try:
        from wade.config.loader import load_config

        resolved = config or load_config(repo_root)
        names = pending_names(resolved, repo_root, pr_number, worktree_path or repo_root)
    except Exception:
        logger.debug("bot_trigger.menu_entry_failed", exc_info=True)
        return None, None
    if not names:
        return None, None
    return resolved, f"{MENU_LABEL} ({names}){suffix}"


def post_pending_triggers(
    config: ProjectConfig,
    repo_root: Path,
    pr_number: int,
    marker_root: Path,
) -> bool:
    """Post triggers for bots not yet triggered at GitHub's current PR head.

    The menu-side counterpart of :func:`pending_names` — it re-resolves the PR
    head SHA and pending set at post time rather than trusting what the menu was
    built from. This keeps markers aligned with the commit the PR presents for
    review even if another terminal advances the local branch without pushing.
    The outer re-check is a fast path and the "already posted for this commit"
    notice; the authoritative dedup is the under-lock re-check inside
    :func:`post_bot_triggers`.

    Returns whether **a bot review is now pending for this commit** — i.e. whether
    a caller that falls through into a wait-for-review poll is justified in doing
    so. ``True`` when at least one trigger was posted just now, or when every
    enabled bot was already triggered for this sha (concurrently recorded by
    another ``done``/menu). ``False`` when nothing is pending: the branch tip could
    not be resolved, or every post attempt failed (e.g. a GitHub/API outage) — in
    which case waiting would be a silent wait for a review no bot was asked for.
    """
    sha = git_pr.get_pr_head_sha(repo_root, pr_number)
    if sha is None:
        console.warn("Could not resolve the PR head — skipping the bot review triggers.")
        return False
    pending = pending_bots(config, marker_root, sha)
    if not pending:
        console.detail("Bot review triggers were already posted for this commit.")
        return True
    return post_bot_triggers(repo_root, pr_number, pending, marker_root=marker_root, sha=sha) > 0


def post_bot_triggers(
    repo_root: Path,
    pr_number: int,
    bots: list[ReviewBotConfig],
    *,
    marker_root: Path,
    sha: str,
) -> int:
    """Post each bot's trigger comment on the PR, recording a per-sha marker.

    Best-effort and per-bot isolated: ``comment_on_pr`` is fail-fast (it raises),
    so each post is wrapped individually — one bot's failure never stops the
    others, and no failure here is ever propagated to the caller (this runs after
    an otherwise-complete ``done`` push / from a menu, where an already-finalized
    PR must not be reported as a failure).

    **Once per bot per sha, even under concurrency.** The whole
    check -> post -> marker section runs under a cross-process
    :func:`wade.utils.filelock.file_lock` keyed on the worktree + sha
    (:func:`_trigger_lock_path`), and each bot's marker is re-checked *inside*
    that lock. Without this, two ``done`` / menu processes on the same worktree
    could both read a marker absent (in :func:`pending_bots`), both post, and
    both record it — double-spamming the PR. The lock serializes them so the
    second sees the first's marker and skips. ``file_lock`` blocks (never fails)
    and is released by the OS if a holder dies, so there is no stuck-reservation
    failure mode; if the lock primitive itself is unavailable, this degrades to
    the pre-lock check-then-act path rather than fail an otherwise-complete
    ``done``.

    A bot's marker is written **only after** its post succeeds, so a failed bot
    retries on the next pass while a succeeded one stays quiet. If the marker
    *write* itself fails, the comment is already posted but the trigger is not
    durably recorded — that is warned about rather than reported as plain
    success, because a later pass at the same sha may re-post it.

    Provider/exception text is untrusted (it can carry Rich control tokens that
    would raise ``MarkupError`` in the markup-enabled console), so it is escaped
    before rendering; bot names are escaped too, as belt-and-suspenders.

    Returns the number of trigger comments actually posted.
    """

    def _post_all() -> int:
        posted = 0
        for bot in bots:
            # Re-check under the lock (not just at the caller's pending-set
            # computation): a concurrent done/menu process on the same worktree
            # may have posted and recorded this bot@sha in between. This is what
            # upholds the once-per-bot-per-sha guarantee under process-level
            # parallelism, not merely against sequential re-runs.
            if markers.marker_present(marker_root, marker_name(bot.name), sha):
                continue
            safe_name = console.escape_markup(bot.name)
            try:
                git_pr.comment_on_pr(repo_root, pr_number, bot.trigger)
            except Exception as e:  # fail-fast primitive — isolate + best-effort.
                console.warn(
                    f"Could not trigger {safe_name} review: {console.escape_markup(str(e))}"
                )
                logger.warning("bot_trigger.post_failed", bot=bot.name, error=str(e))
                continue
            posted += 1
            if markers.write_marker(marker_root, marker_name(bot.name), sha):
                console.detail(f"Triggered {safe_name} review.")
            else:
                console.warn(
                    f"Triggered {safe_name} review, but could not record its "
                    "anti-spam marker — a repeat at this commit may re-post it."
                )
                logger.warning("bot_trigger.marker_write_failed", bot=bot.name, sha=sha)
        return posted

    posted: int | None = None
    try:
        with file_lock(_trigger_lock_path(marker_root, sha)):
            posted = _post_all()
    except OSError:
        if posted is not None:
            # The protected section already completed under the lock; the error
            # came from *releasing* it (``file_lock`` does not suppress a failing
            # ``os.close``). Re-running ``_post_all`` here would re-post any bot
            # whose comment succeeded but whose marker write failed, so return
            # the count we already have rather than risk a duplicate.
            logger.warning("bot_trigger.lock_release_failed", sha=sha, exc_info=True)
            return posted
        # Lock *acquisition* failed (e.g. an unwritable temp dir) — degrade to
        # the pre-lock check-then-act path rather than fail an otherwise-complete
        # done. The per-bot marker re-check still runs; only cross-process
        # serialization is lost, reverting to best-effort anti-spam.
        logger.warning("bot_trigger.lock_unavailable", sha=sha, exc_info=True)
        return _post_all()
    return posted

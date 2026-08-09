"""Implementation session completion — create/finish PR or merge directly."""

from __future__ import annotations

import contextlib
from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import sync as git_sync
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.implementation_service._shared import (
    extract_issue_from_branch,
    find_worktree_path,
)
from wade.services.implementation_service.bootstrap import (
    _check_tracked_managed_files,
    _format_uncommitted_summary,
    _get_dirty_file_paths,
    _identify_session_dirty_files,
    strip_worktree_gitignore,
)
from wade.services.implementation_service.core import _resolve_worktree_from_plan
from wade.services.implementation_service.lifecycle import (
    SUMMARY_MARKER_END,
    SUMMARY_MARKER_START,
    _apply_pr_refs,
    _build_pr_body,
    _strip_summary_section,
)
from wade.services.implementation_service.usage_tracking import IMPL_USAGE_MARKER_START
from wade.services.task_service import remove_in_progress_label
from wade.ui import prompts
from wade.ui.console import console
from wade.utils import markers
from wade.utils.body_markers import build_marked_block, update_body_preserving_markers
from wade.utils.conventional import (
    conventional_title_error,
    is_conventional_title,
)
from wade.utils.markdown import remove_marker_block

logger = structlog.get_logger()

__all__ = [
    "_done_via_pr",
    "done",
]

# Placeholder sentinels from templates/skills/.../reference/pr-summary-format.md.
# Their presence means the file is still the template stub, not a real summary.
_PR_SUMMARY_PLACEHOLDERS = ("[high-level summary", "[optional:")


def done(
    target: str | None = None,
    plan_file: Path | None = None,
    no_close: bool = False,
    draft: bool = False,
    project_root: Path | None = None,
    *,
    session_type: str = "implementation",
    skip_review: bool = False,
) -> bool:
    """Complete a session — run the completion gates, push, and finalize the PR.

    Detects the current branch, extracts the issue number, runs the completion
    gates (parameterized by ``session_type``), and delegates to ``_done_via_pr``
    (PR is the only merge strategy since #357 retired ``direct``).

    Both ``implementation-session done`` and ``review-pr-comments-session done``
    call this same service, so the gate set branches on ``session_type``:

    - ``"implementation"`` — PR-SUMMARY, review-ran (vs pre-sync HEAD), then
      auto-sync (may advance HEAD).
    - ``"review-pr-comments"`` — unresolved review threads, then review-ran.

    After all gates pass, ``_done_via_pr`` writes ``.wade/done@<post-sync HEAD>``
    immediately before pushing, so ``done``'s own push satisfies the pre-push
    backstop and the Stop hook sees the session as finalized.

    Args:
        target: Optional issue number, worktree name, or plan file.
            If None, detects from current branch.
        no_close: Don't close the issue on merge.
        draft: Create PR as draft.
        project_root: Repository root.
        session_type: ``"implementation"`` or ``"review-pr-comments"`` — selects
            the gate set.
        skip_review: Escape hatch for the review-ran gate (``--skip-review``).
    """
    config = load_config(project_root)
    provider = get_provider(config)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    resolved_wt_path: Path | None = None
    if plan_file is not None:
        try:
            resolved_wt_path, resolved_branch, issue_num = _resolve_worktree_from_plan(
                plan_file, project_root=project_root
            )
            console.step("Resolved from plan:")
            console.detail(f"Worktree: {resolved_wt_path}")
            console.detail(f"Branch: {resolved_branch}")
            target = issue_num
        except ValueError as e:
            console.error(str(e))
            return False

    # If target is a plan file, create issue first (skip if target looks like a number)
    if target and not target.isdigit():
        target_path = Path(target).expanduser()
        if target_path.is_file():
            from wade.services.task_service import create_from_plan_file
            from wade.utils.conventional import ConventionalTitleError

            console.info(f"Creating issue from plan file: {target}")
            # create_from_plan_file now hard-validates the plan's `# Title` — a
            # non-conventional title raises. Surface it as a clean, actionable
            # message (not a traceback) with a non-zero exit.
            try:
                task = create_from_plan_file(target_path, config=config, provider=provider)
            except ConventionalTitleError as e:
                # Title comes from the plan file — disable Rich markup so bracket
                # tokens in it are shown literally rather than parsed as markup.
                console.error(str(e), markup=False)
                console.hint(
                    f"Fix the plan file's `# Title` heading in {target} to a "
                    "conventional-commit title, then re-run done."
                )
                return False
            if not task:
                return False
            target = task.id

    wt_path: Path | None = resolved_wt_path

    if wt_path is not None:
        cwd = wt_path

    # If target specifies a worktree, navigate to it and extract issue number
    if target and wt_path is None:
        wt_path = find_worktree_path(target, project_root=repo_root)
        if wt_path:
            cwd = wt_path
            # Replace non-numeric target with the issue number from the branch
            if not target.isdigit():
                try:
                    wt_branch = git_repo.get_current_branch(wt_path)
                    extracted = extract_issue_from_branch(wt_branch)
                    if extracted:
                        target = extracted
                except GitError:
                    pass

    # If running from inside a linked worktree with no explicit target,
    # use cwd as the worktree path so PR-SUMMARY.md lookup works.
    if wt_path is None and git_repo.is_worktree(cwd):
        wt_path = cwd

    # Detect branch and issue
    try:
        branch = git_repo.get_current_branch(cwd)
    except GitError:
        console.error_with_fix(
            "Cannot determine current branch",
            "Check that HEAD is not detached",
        )
        return False

    issue_number = target or extract_issue_from_branch(branch)
    if not issue_number:
        console.error(f"Cannot extract issue number from branch: {branch}")
        return False

    # Check clean — keep the worktree gitignore block in place so wade
    # artifacts (PLAN.md, .wade/, etc.) stay hidden from git status.
    # Strip it only after the gate passes.
    if not git_repo.is_clean(cwd):
        detail_str = _format_uncommitted_summary(cwd)
        dirty_paths = _get_dirty_file_paths(cwd)
        session_files = _identify_session_dirty_files(dirty_paths)
        console.error(f"Working tree is dirty ({detail_str})")
        if session_files:
            console.warn(
                "The following dirty files are wade session artifacts"
                " \N{EM DASH} do NOT commit them."
            )
            console.hint("Restore with: git checkout -- <file>")
            for sf in session_files:
                console.detail(sf)
            console.empty()
        console.hint(
            "Commit or stash your non-session changes first"
            if session_files
            else "Commit or stash your changes first"
        )
        return False

    # Clean gate passed. IMPORTANT (A3): keep the worktree gitignore block and
    # its skip-worktree bit in place until the PR finalize actually succeeds. If
    # we stripped now and a later step failed (tracked-managed gate, push, PR API
    # error), a retry would see the un-hidden session artifacts (PLAN.md, .wade/)
    # as a dirty tree and fail the clean gate — leaving `done` un-retryable. The
    # strip is deferred to the very end, gated on success.

    # Check for tracked wade-managed files that should never be committed. This
    # inspects the git index (not .gitignore), so it is safe to run before the
    # strip.
    tracked_managed = _check_tracked_managed_files(cwd)
    if tracked_managed:
        console.error("Wade-managed files are tracked in git — these must not be committed")
        for path in tracked_managed:
            console.detail(f"  {path}")
        console.info("Untrack them with:")
        for path in tracked_managed:
            console.detail(f"  git rm --cached {path}")
        console.info("Then commit the removal and re-run done.")
        return False

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            console.error("Cannot detect main branch")
            return False

    # Check for stacked base branch metadata (written by start() for chain execution).
    # When present, target the parent branch instead of main — same as sync().
    if wt_path:
        base_branch_file = wt_path / ".wade" / "base_branch"
        if base_branch_file.is_file():
            stored_base = base_branch_file.read_text().strip()
            if stored_base and git_branch.branch_exists(repo_root, stored_base):
                main_branch = stored_base

    # Completion gates run AFTER the clean + tracked-managed gates and BEFORE
    # finalize. The worktree root is where PR-SUMMARY.md and .wade/ markers live.
    worktree_root = wt_path or cwd

    # The review-ran gate checks the sha the agent actually reviewed — the
    # PRE-SYNC HEAD. Capturing it before the (impl-only) auto-sync means a clean
    # main-merge does not spuriously invalidate the review just performed.
    # Resolve ``branch`` (not ``cwd``'s HEAD), matching ``_write_done_marker``: a
    # ``done <target>`` run from the main checkout leaves ``cwd`` there, where
    # HEAD is not the branch tip — resolving the branch ref keeps both shas
    # consistent so the gate compares against the same commit the marker keys to.
    try:
        pre_sync_head = git_repo.rev_parse(cwd, branch)
    except GitError:
        console.error("Cannot resolve the branch tip to run the completion gates.")
        return False

    if not _run_completion_gates(
        session_type=session_type,
        config=config,
        provider=provider,
        repo_root=repo_root,
        worktree_root=worktree_root,
        branch=branch,
        main_branch=main_branch,
        issue_number=issue_number,
        pre_sync_head=pre_sync_head,
        skip_review=skip_review,
    ):
        return False

    console.rule(f"done #{issue_number}")

    ok = _done_via_pr(
        repo_root=repo_root,
        branch=branch,
        issue_number=issue_number,
        main_branch=main_branch,
        close_issue=not no_close,
        draft=draft,
        config=config,
        worktree_path=wt_path,
    )
    if not ok:
        # Finalize failed — leave the worktree exactly as we found it (gitignore
        # block + skip-worktree still in place) so the user can fix and re-run.
        return False

    # Success only: strip the worktree gitignore block and restore .gitignore
    # visibility now that there is nothing left to retry. The PR is already
    # updated at this point, so a filesystem cleanup failure (read-only file,
    # permission change, removed worktree dir) must NOT turn an already-finalized
    # PR into a reported failure — warn and continue instead.
    try:
        git_repo.unskip_worktree_file(cwd, ".gitignore")
        strip_worktree_gitignore(cwd)
    except OSError as e:
        console.warn(f"Could not clean up the worktree gitignore block: {e}")
        console.hint("Remove the `# wade:worktree:start` block from .gitignore manually.")
        logger.warning("implementation.gitignore_strip_failed", error=str(e), exc_info=True)

    return True


# ---------------------------------------------------------------------------
# Completion gates (#349) — parameterized by session type
# ---------------------------------------------------------------------------


def _run_completion_gates(
    *,
    session_type: str,
    config: ProjectConfig,
    provider: AbstractTaskProvider,
    repo_root: Path,
    worktree_root: Path,
    branch: str,
    main_branch: str,
    issue_number: str,
    pre_sync_head: str,
    skip_review: bool,
) -> bool:
    """Run the gate set for ``session_type`` in the fixed order. True ⇒ proceed.

    Order matters: auto-sync (implementation only) can advance HEAD via a merge
    commit, so the review-ran gate — which is keyed to the sha the agent actually
    reviewed — runs against the **pre-sync HEAD**, before sync. A clean,
    zero-conflict merge of main is therefore accepted without a fresh review (a
    main-merge is not new authored work).

    The PR-title gate runs first for **both** session types (shared, parameterized
    ``done()`` — knowledge 851bb6ec): it blocks the earliest on a non-conventional
    issue title, before any push/PR mutation. Its complementary sync (pushing a
    corrected title onto an open PR) lives in ``_done_via_pr``.
    """
    if session_type == "review-pr-comments":
        if not _gate_pr_title(config, provider, issue_number):
            return False
        if not _gate_resolved_threads(config, provider, repo_root, branch):
            return False
        # review-pr-comments keeps the unbounded fast-path-or-refuse behavior:
        # the review-pass cap (#384) is scoped to the implementation path only.
        if not _gate_review_ran(
            config, worktree_root, pre_sync_head, skip_review, session_type=session_type
        ):
            return False
        return _gate_knowledge_valid(config, worktree_root)

    # Default: implementation session.
    if not _gate_pr_title(config, provider, issue_number):
        return False
    if not _gate_pr_summary(config, worktree_root):
        return False
    if not _gate_review_ran(
        config, worktree_root, pre_sync_head, skip_review, session_type=session_type
    ):
        return False
    if not _gate_sync(config, repo_root, worktree_root, branch, main_branch, session_type):
        return False
    # Runs LAST — after sync merges the base branch into the worktree, the local
    # `merge=union` point where a structural corruption of KNOWLEDGE.md could be
    # introduced. Validating here keeps a union-corrupted file from shipping.
    return _gate_knowledge_valid(config, worktree_root)


def _gate_knowledge_valid(config: ProjectConfig, worktree_root: Path) -> bool:
    """Refuse when the knowledge file is structurally corrupt (e.g. a union merge).

    ``merge=union`` keeps both sides of a conflict with no structural awareness, so a
    rewrite-in-place knowledge edit diverging from an append can leave a malformed
    ``KNOWLEDGE.md`` (duplicate entry headings) that merges cleanly and would ship
    undetected. This gate runs :func:`validate_knowledge_file` on the worktree's own
    knowledge file — the one about to be pushed and merged — so such corruption is
    caught before it reaches main. No-op when knowledge is disabled.
    """
    if not config.knowledge.enabled:
        return True
    from wade.utils.knowledge_file import (
        resolve_knowledge_path,
        validate_knowledge_file,
    )

    try:
        path = resolve_knowledge_path(worktree_root, config.knowledge)
    except ValueError:
        return True  # misconfigured knowledge.path is not this gate's concern
    problems = validate_knowledge_file(path)
    if not problems:
        return True
    console.error(
        f"{config.knowledge.path} failed structural validation — a merge may have corrupted it."
    )
    for problem in problems:
        console.detail(problem)
    console.hint("Repair the knowledge file (dedupe entries / fix headings), commit, then re-run.")
    return False


def _title_fix_hint(config: ProjectConfig, issue_number: str) -> str:
    """Provider-aware instruction for correcting a non-conventional task title.

    The task-provider abstraction means the "task" is a GitHub issue, a ClickUp
    task, or a row in the central Markdown file — so ``gh issue edit`` is correct
    only for the GitHub provider. For the others it would fail, leave ``done``
    blocked, or (worst case) mutate an unrelated GitHub issue with the same id.
    Point the user at the configured provider's own title-update path instead.
    """
    from wade.models.config import ProviderID

    suffix = "(choose feat/fix/... — wade won't guess), then re-run done."
    if config.provider.name == ProviderID.CLICKUP:
        return f"Fix it: rename task {issue_number} in ClickUp to `<type>: ...` {suffix}"
    if config.provider.name == ProviderID.MARKDOWN:
        return (
            f"Fix it: edit task {issue_number}'s title in the tasks Markdown file "
            f"to `<type>: ...` {suffix}"
        )
    return f'Fix it: `gh issue edit {issue_number} --title "<type>: ..."` {suffix}'


def _gate_pr_title(
    config: ProjectConfig,
    provider: AbstractTaskProvider,
    issue_number: str,
) -> bool:
    """Refuse when the issue title is not a conventional-commit title.

    The PR title is derived from the issue title verbatim (``_done_via_pr`` opens
    the PR with ``task.title`` and syncs an existing PR to it), so a
    non-conventional issue title fails the ``PR Title Lint`` CI check. Blocking
    here — before push and before any PR mutation — keeps a bad title from ever
    reaching a PR. wade never guesses a prefix (``feat`` vs ``fix`` is not
    deterministic); the human/agent owns the title *content*, code owns the
    *format*.

    A provider read failure is non-blocking, consistent with the other lookup
    gates: ``_done_via_pr`` reads the same issue and surfaces a hard read error
    there. No-op when ``done.require_conventional_title`` is disabled.
    """
    if not config.done.require_conventional_title:
        return True
    try:
        task = provider.read_task(issue_number)
    except Exception as exc:
        console.warn(
            f"Could not read issue #{issue_number} to validate its title (non-blocking): {exc}"
        )
        logger.debug("done.title_gate_read_failed", exc_info=True)
        return True
    if is_conventional_title(task.title):
        return True
    console.error(
        f"Issue #{issue_number} title is not a conventional-commit title — "
        "the PR Title Lint CI check would fail."
    )
    # The issue title is provider-derived — render without Rich markup so bracket
    # tokens in it are shown literally, not parsed as markup (which would crash).
    console.detail(conventional_title_error(task.title), markup=False)
    console.hint(_title_fix_hint(config, issue_number))
    console.hint("Bypass: set `done.require_conventional_title: false` in .wade.yml.")
    return False


def _is_placeholder_pr_summary(text: str) -> bool:
    """True when PR-SUMMARY.md is empty, headings-only, or still a template stub."""
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if any(ph in lowered for ph in _PR_SUMMARY_PLACEHOLDERS):
        return True
    # Drop heading lines (``## …``), horizontal rules (``---``), and blanks — if
    # nothing substantive remains, it is a headings-only stub, not a real summary.
    substantive = [
        line
        for line in stripped.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and set(line.strip()) != {"-"}
    ]
    return not substantive


def _gate_pr_summary(config: ProjectConfig, worktree_root: Path) -> bool:
    """Refuse when PR-SUMMARY.md is missing, empty, or a template placeholder."""
    if not config.done.require_pr_summary:
        return True
    path = worktree_root / "PR-SUMMARY.md"
    if not path.is_file():
        console.error("PR-SUMMARY.md is missing — the PR would have no description.")
        console.hint("Write PR-SUMMARY.md in the worktree root, then re-run done.")
        console.hint("Bypass: set `done.require_pr_summary: false` in .wade.yml.")
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        console.error(f"Could not read PR-SUMMARY.md: {exc}")
        return False
    if _is_placeholder_pr_summary(text):
        console.error("PR-SUMMARY.md is empty or still contains template placeholders.")
        console.hint("Replace the placeholder text with a real summary of your changes.")
        console.hint("Bypass: set `done.require_pr_summary: false` in .wade.yml.")
        return False
    return True


def _gate_review_ran(
    config: ProjectConfig,
    worktree_root: Path,
    head_sha: str,
    skip_review: bool,
    *,
    session_type: str = "implementation",
) -> bool:
    """Refuse unless ``wade review implementation`` ran for ``head_sha``.

    Fast path (**both** session types): an exact-sha ``reviewed@<head_sha>``
    marker means done — a review for the current commit always passes on the
    first try.

    Implementation sessions additionally apply a **code-enforced pass cap** so the
    review→fix→re-review loop is bounded (#384). Committing after the last review
    moves the tip sha and invalidates the exact-sha marker; without a bound the
    agent re-reviews, re-commits, and loops forever. Once
    ``done.max_review_passes`` distinct commits have carried a delegation-backed
    ``review-pass@<sha>`` marker, ``done`` completes **anyway** — with a prominent
    notice — rather than looping. ``review-pr-comments`` sessions keep the
    unbounded fast-path-or-refuse behavior; #384 is scoped to impl sessions and
    this gate is shared (knowledge 851bb6ec), so the cap branch is impl-only.

    The pass count is the number of distinct ``review-pass@*`` markers; a listdir
    failure yields ``0`` (fail toward re-gating), never a false "cap reached".

    Auto-skipped when reviews are disabled (``review_implementation.enabled:
    false``) — the marker is not written then either. Hatches: ``--skip-review``
    and ``done.require_review: false``.
    """
    if config.ai.review_implementation.enabled is False:
        return True  # reviews disabled project-wide → gate off (no marker written)
    if skip_review or not config.done.require_review:
        return True
    if markers.marker_present(worktree_root, "reviewed", head_sha):
        return True

    # review-pr-comments: unchanged fast-path-or-refuse (no cap — out of scope).
    if session_type != "implementation":
        _print_review_refusal()
        return False

    # Implementation session: apply the bounded review-pass cap (done.max_review_passes).
    passes = markers.count_review_passes(worktree_root)
    limit = config.done.max_review_passes
    if passes >= limit:
        console.warn(
            f"Review-pass safety limit reached ({passes} of {limit}) — the current "
            "commit was not re-reviewed."
        )
        console.detail(
            f"{passes} commit(s) have been reviewed on this worktree; the cap bounds "
            "the review→fix→re-review loop so `done` can't be blocked indefinitely. "
            "Completing without requiring another review."
        )
        console.hint(
            "Raise the cap with `done.max_review_passes`, or bypass this run with "
            "`wade implementation-session done --skip-review`."
        )
        return True

    console.error(f"Review has not run for the current commit (review pass {passes} of {limit}).")
    console.hint("Run `wade review implementation` (or pass --skip-review), then re-run done.")
    console.detail(
        "A clean merge of main is accepted without re-review, but new commits "
        "require a fresh review — the marker is keyed to the commit sha."
    )
    console.hint(
        "If review keeps looping, break it with `wade implementation-session done --skip-review`."
    )
    console.hint("Bypass: set `done.require_review: false` in .wade.yml.")
    return False


def _print_review_refusal() -> None:
    """Print the standard review-ran refusal (fast-path-or-refuse, no cap)."""
    console.error("Review has not run for the current commit.")
    console.hint("Run `wade review implementation` (or pass --skip-review), then re-run done.")
    console.detail(
        "A clean merge of main is accepted without re-review, but new commits "
        "require a fresh review — the marker is keyed to the commit sha."
    )
    console.hint("Bypass: set `done.require_review: false` in .wade.yml.")


def _behind_count(repo_root: Path, main_branch: str, branch: str) -> int | None:
    """Commits on the base that ``branch`` lacks — how far *behind* it is.

    ⚠ Argument order: ``commits_ahead(repo, base_in_branch_position, branch)`` —
    the base ref (``origin/<main>`` or the local ``<main>``) goes in the *branch*
    position, so it counts commits present on the base but not on the session
    branch. This is the OPPOSITE role assignment from the Stop hook's ahead-count
    (``_stop_git_facts``), which puts the session branch in the branch position.
    Returns None when neither base ref resolves.
    """
    for base in (f"origin/{main_branch}", main_branch):
        try:
            return git_branch.commits_ahead(repo_root, base, branch)
        except GitError:
            continue
    return None


def _gate_sync(
    config: ProjectConfig,
    repo_root: Path,
    worktree_root: Path,
    branch: str,
    main_branch: str,
    session_type: str,
) -> bool:
    """Auto-sync a branch behind main; refuse only on conflict."""
    if not config.done.require_sync:
        return True

    with contextlib.suppress(GitError):
        git_sync.fetch_origin(repo_root)

    behind = _behind_count(repo_root, main_branch, branch)
    if behind is None:
        console.warn(
            f"Could not determine whether '{branch}' is behind '{main_branch}' "
            "— skipping the sync gate."
        )
        return True
    if behind == 0:
        return True

    console.step(f"Branch is {behind} commit(s) behind {main_branch} — auto-syncing...")
    # Reuse the existing sync service rather than merging inline (planning
    # decision: auto-sync, refuse only on conflict).
    from wade.services.implementation_service.sync import sync as do_sync

    result = do_sync(
        main_branch=main_branch,
        session_type=session_type,
        project_root=worktree_root,
    )
    if result.success:
        console.success(f"Synced with {main_branch}.")
        return True

    session_cmd = "review-pr-comments" if session_type == "review-pr-comments" else "implementation"
    if result.conflicts:
        console.error(f"Sync hit conflicts with {main_branch} — resolve them, then re-run done.")
        console.hint(
            f"Resolve via `wade {session_cmd}-session sync`, fix the conflicts, then re-run done."
        )
    else:
        console.error(f"Could not sync with {main_branch}.")
        console.hint(f"Run `wade {session_cmd}-session sync`, then re-run done.")
    console.hint("Bypass: set `done.require_sync: false` in .wade.yml.")
    return False


def _gate_resolved_threads(
    config: ProjectConfig,
    provider: AbstractTaskProvider,
    repo_root: Path,
    branch: str,
) -> bool:
    """Refuse on unresolved PR review threads; a transient lookup is non-blocking.

    Consistent with #357 B1/B2 typed-lookup handling: a provider/lookup error is
    logged and warned, never blocking — a flaky ``gh`` call must not trap
    completion. Only an actually-fetched, non-empty unresolved-thread list
    refuses.
    """
    if not config.done.require_resolved_threads:
        return True

    from wade.models.review import filter_unresolved_threads

    try:
        lookup = git_pr.get_pr_for_branch(repo_root, branch)
    except Exception:
        console.warn("Could not look up the PR to check review threads — skipping the thread gate.")
        logger.debug("done.thread_gate_pr_lookup_failed", exc_info=True)
        return True
    if lookup.lookup_failed or not lookup.is_open or lookup.pr is None:
        # Transient failure or no open PR — non-blocking (nothing to gate on yet).
        return True

    pr_number = lookup.pr.number
    try:
        threads = provider.get_pr_review_threads(pr_number)
    except Exception as exc:
        console.warn(f"Could not fetch review threads (non-blocking): {exc}")
        logger.debug("done.thread_gate_fetch_failed", exc_info=True)
        return True

    unresolved = filter_unresolved_threads(threads)
    if unresolved:
        console.error(f"{len(unresolved)} unresolved review thread(s) remain.")
        console.hint(
            "Resolve each via `wade review-pr-comments-session resolve <thread-id>`, "
            "then re-run done."
        )
        console.hint("Bypass: set `done.require_resolved_threads: false` in .wade.yml.")
        return False
    return True


# A bare "rejected" is intentionally NOT here: it matches every rejection git
# reports (e.g. a pre-receive hook / branch-protection "push rejected"), which a
# force-push cannot fix — offering the force-with-lease recovery menu for those
# would be misleading. The specific signals below (including the "! [rejected]
# ... (non-fast-forward)" line, matched via "non-fast-forward") already cover a
# real non-fast-forward rejection.
_NON_FAST_FORWARD_SIGNALS = (
    "non-fast-forward",
    "fetch first",
    "updates were rejected",
    "tip of your current branch is behind",
)


def _is_non_fast_forward(message: str) -> bool:
    """Return True if a push error message indicates a non-fast-forward rejection."""
    lowered = message.lower()
    return any(sig in lowered for sig in _NON_FAST_FORWARD_SIGNALS)


def _write_done_marker(marker_root: Path, repo_root: Path, branch: str) -> None:
    """Write ``.wade/done@<branch tip>`` best-effort (clears any prior ``done@*``).

    Resolving ``branch`` (not HEAD) keeps the key correct even when ``done
    <target>`` runs from the main checkout, where ``repo_root``'s HEAD is not the
    branch tip.
    """
    try:
        pushed_sha = git_repo.rev_parse(repo_root, branch)
    except GitError:
        logger.warning("done.marker_write_head_failed", exc_info=True)
        return
    markers.write_marker(marker_root, "done", pushed_sha)


def _push_branch_with_recovery(
    repo_root: Path,
    branch: str,
    worktree_path: Path | None,
    marker_root: Path,
) -> bool:
    """Push *branch*, recovering interactively from a non-fast-forward rejection.

    On a non-FF rejection the remote branch has commits the local branch lacks.
    We fetch, report the divergence, and — only behind an explicit confirm —
    offer to merge the remote in and retry, or force-push with
    ``--force-with-lease``. wade never force-pushes silently (C4).

    Owns the ``done`` marker lifecycle so the pre-push backstop and Stop hook
    stay consistent with what is actually on the remote:

    - The marker is written **immediately before each push attempt** (so the
      worktree's own pre-push backstop passes), keyed to the branch tip being
      pushed. A recovery merge advances that tip, so the marker is **re-written**
      for the new sha before the retry — otherwise the backstop would reject
      ``done``'s own recovery push.
    - Every failure path **clears** the marker: if nothing reached the remote,
      no stale ``done@<sha>`` may linger (which would tell the Stop hook the
      session finished and let the next push skip the backstop).
    """
    _write_done_marker(marker_root, repo_root, branch)
    try:
        git_repo.push_branch(repo_root, branch, set_upstream=True)
        console.success("Branch pushed.")
        return True
    except GitError as e:
        if not _is_non_fast_forward(str(e)):
            console.error(f"Push failed: {e}")
            markers.clear_markers(marker_root, "done")
            return False

    console.warn(f"Push rejected — '{branch}' has diverged from its remote.")
    with contextlib.suppress(GitError):
        git_sync.fetch_origin(repo_root)

    merge_cwd = worktree_path if worktree_path and worktree_path.is_dir() else repo_root
    with contextlib.suppress(GitError):
        behind = git_branch.commits_ahead(repo_root, f"origin/{branch}", branch)
        ahead = git_branch.commits_ahead(repo_root, branch, f"origin/{branch}")
        console.detail(f"Local is {ahead} commit(s) ahead, {behind} behind origin/{branch}.")

    if not prompts.is_tty():
        console.error(
            "Remote branch has diverged. Resolve it manually "
            f"(e.g. `git -C {merge_cwd} pull --no-rebase`), then re-run done."
        )
        markers.clear_markers(marker_root, "done")
        return False

    choice = prompts.select(
        "The remote branch has diverged. How do you want to proceed?",
        [
            "Merge the remote in, then push (safe)",
            "Force-push with --force-with-lease (overwrites remote history)",
            "Cancel",
        ],
    )
    if choice == 0:
        try:
            merge_result = git_sync.merge_branch(merge_cwd, f"origin/{branch}")
            if not merge_result.success:
                console.error("Merge conflicts with the remote branch — resolve them, then re-run.")
                markers.clear_markers(marker_root, "done")
                return False
            # The merge advanced the branch tip — re-key the marker to it so the
            # retry push satisfies the backstop.
            _write_done_marker(marker_root, repo_root, branch)
            git_repo.push_branch(repo_root, branch, set_upstream=True)
            console.success("Merged the remote and pushed.")
            return True
        except GitError as e:
            console.error(f"Could not merge and push: {e}")
            markers.clear_markers(marker_root, "done")
            return False
    if choice == 1:
        # Force-push sends the same local tip, so the marker from the initial
        # write still matches — no re-write needed.
        try:
            git_repo.push_branch(repo_root, branch, set_upstream=True, force=True)
            console.success("Force-pushed with lease.")
            return True
        except GitError as e:
            console.error(f"Force push failed: {e}")
            markers.clear_markers(marker_root, "done")
            return False
    console.info("Push cancelled — the branch was not updated on the remote.")
    markers.clear_markers(marker_root, "done")
    return False


def _done_via_pr(
    repo_root: Path,
    branch: str,
    issue_number: str,
    main_branch: str,
    close_issue: bool,
    draft: bool,
    config: ProjectConfig,
    worktree_path: Path | None = None,
) -> bool:
    """Finalize implementation — update existing draft PR or create a new one.

    In the new workflow, a draft PR should already exist (created by plan
    or implement). This function:
    1. Pushes the branch
    2. Appends PR-SUMMARY content to the existing PR body
    3. Marks the draft PR as ready for review
    """
    provider = get_provider(config)
    pr_url = ""

    # Read issue for title and body
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Cannot read issue #{issue_number}: {e}")
        return False

    # Backstop for _gate_pr_title's non-blocking read path. task.title becomes the
    # PR title verbatim — both when syncing an existing PR and when creating a new
    # one below. The done() gate normally validates it, but that gate returns True
    # (skips validation) if its own provider.read_task RAISED. If that read failed
    # in the gate yet the read just above succeeded, task.title is unvalidated —
    # refuse here, before any push or PR mutation, rather than let a non-conventional
    # title reach the PR and fail PR Title Lint (which would silently undermine
    # require_conventional_title). Re-running done re-validates via the gate (whose
    # read likely succeeds now) for a clean, actionable block.
    if config.done.require_conventional_title and not is_conventional_title(task.title):
        console.error(
            f"Issue #{issue_number} title is not a conventional-commit title — "
            "refusing to put it on the PR (PR Title Lint would fail)."
        )
        console.hint("Re-run done — the title gate will re-validate and guide the fix.")
        return False

    # Push branch (with non-fast-forward divergence recovery — never a silent
    # force-push). `_push_branch_with_recovery` owns the `done` marker: it writes
    # `.wade/done@<pushed sha>` right before each push (so `done`'s own push
    # satisfies the pre-push backstop and the Stop hook reads the session as
    # finalized) and clears it on any push failure. A new commit changes the sha
    # and invalidates the marker.
    marker_root = worktree_path or repo_root
    console.step("Pushing branch...")
    if not _push_branch_with_recovery(repo_root, branch, worktree_path, marker_root):
        return False

    # Check for existing PR (expected from plan or implement bootstrap).
    # A lookup failure is transient — do NOT fall through to "create a new PR"
    # (that would duplicate the draft); report and let the user retry. A merged
    # or closed PR must not be body-updated / re-marked-ready as if it were open.
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.lookup_failed:
        console.error(f"Could not look up the PR for branch '{branch}' — try again shortly.")
        return False
    if lookup.is_merged:
        console.error(f"PR #{lookup.number} is already merged — nothing to finalize.")
        return False
    if lookup.found and not lookup.is_open:
        console.error(
            f"PR #{lookup.number} is {lookup.state.lower()} — reopen it or start a new branch."
        )
        return False
    existing_pr = lookup.pr if lookup.is_open else None

    # Resolve PR-SUMMARY.md from worktree root
    pr_summary_path: Path | None = None
    if worktree_path and (worktree_path / "PR-SUMMARY.md").exists():
        pr_summary_path = worktree_path / "PR-SUMMARY.md"

    if pr_summary_path is None:
        console.warn("No PR-SUMMARY.md found — PR description will have no summary.")
        if worktree_path:
            console.detail(f"Expected: {worktree_path / 'PR-SUMMARY.md'}")

    if existing_pr is not None:
        # Update existing PR: append summary
        pr_number = existing_pr.number
        pr_url = existing_pr.url
        console.step(f"Updating existing PR #{pr_number}...")

        # Sync the PR title to the issue title. A PR opened before conventional-
        # title enforcement — or whose issue title was corrected after the PR
        # opened — can carry a stale title that fails PR Title Lint. task.title is
        # guaranteed conventional here (validated by the done() gate, or by the
        # backstop above when the gate's read failed), so pushing it is safe.
        #
        # The response to a sync failure hinges on whether the *current* PR title
        # would pass PR Title Lint. The sync fires on any title mismatch, and a
        # stale PR title may itself already be conventional (e.g. a manually
        # edited PR title that merely differs from the issue). In that case a
        # transient gh failure is non-blocking — lint still passes, so warn and
        # let an otherwise-complete done succeed. But if the stale title is NOT
        # conventional, lint will fail; failing the sync must then fail done —
        # that is the whole point of require_conventional_title. The branch and
        # `.wade/done@<sha>` marker are already pushed, so re-running done retries
        # the sync idempotently.
        if config.done.require_conventional_title and existing_pr.title != task.title:
            if git_pr.update_pr_title(repo_root, pr_number, task.title):
                # markup=False: the issue title is provider-derived — a bracket
                # token like `[/]` in it would be parsed as Rich markup and crash
                # this success line (the very MarkupError class this PR removes).
                console.success(f"PR title synced to issue title: {task.title}", markup=False)
            elif not is_conventional_title(existing_pr.title):
                console.error(
                    "Could not sync the PR title to the issue title, and the "
                    "current PR title is not conventional — PR Title Lint would "
                    "fail. Re-run done to retry (it is idempotent), or fix the "
                    "PR title manually."
                )
                return False
            else:
                console.warn(
                    "Could not update the PR title to match the issue — "
                    "update it manually so the PR title tracks the issue."
                )

        # Build summary content
        summary_content = ""
        if pr_summary_path and pr_summary_path.is_file():
            summary_content = pr_summary_path.read_text(encoding="utf-8").strip()

        # Detect parent tracking issue
        parent_issue: str | None = None
        try:
            parent_issue = provider.find_parent_issue(
                issue_number, label=config.project.issue_label
            )
            if parent_issue:
                console.detail(f"Detected parent tracking issue: #{parent_issue}")
        except Exception:
            logger.debug("implementation.parent_issue_detection_failed", exc_info=True)

        def _transform(body: str) -> str:
            # Keep existing content; refresh close/parent refs; rewrite ONLY the
            # wade:summary block so a concurrent edit elsewhere survives (A4).
            body = _apply_pr_refs(body, issue_number, close_issue, parent_issue)
            # Remove the prior marked block FIRST: the legacy heading stripper is
            # not marker-aware, so running it on a body that still contains the
            # marked block would match the `## Summary` *inside* the block,
            # orphan the start marker, and drop the end marker (leaving an
            # unbalanced pair remove_marker_block can no longer clean). After the
            # marked block is gone, strip any genuinely legacy unmarked heading.
            body = remove_marker_block(body, SUMMARY_MARKER_START, SUMMARY_MARKER_END)
            body = _strip_summary_section(body)
            if not summary_content:
                return body.rstrip("\n") + "\n"
            block = build_marked_block(
                SUMMARY_MARKER_START, SUMMARY_MARKER_END, f"## Summary\n\n{summary_content}"
            )
            # Keep ordering content → summary → impl-usage.
            marker_pos = body.find(IMPL_USAGE_MARKER_START)
            if marker_pos != -1:
                before = body[:marker_pos].rstrip("\n")
                after = body[marker_pos:]
                return f"{before}\n\n{block}\n\n{after}\n"
            return body.rstrip("\n") + "\n\n" + block + "\n"

        if not update_body_preserving_markers(
            read_body=lambda: git_pr.get_pr_body(repo_root, pr_number) or "",
            write_body=lambda b: git_pr.update_pr_body(repo_root, pr_number, b),
            transform=_transform,
            warn=console.warn,
            label=f"PR #{pr_number} body",
        ):
            console.error("Could not update the PR body.")
            return False
        console.success("PR body updated with summary.")

        # Mark draft as ready — but only if the caller did not request a draft.
        is_draft = existing_pr.is_draft
        if is_draft and not draft:
            if git_pr.mark_pr_ready(repo_root, pr_number):
                console.success("PR marked as ready for review.")
            else:
                console.warn("Could not mark PR as ready — do it manually.")
    else:
        # No existing PR — create one (fallback)
        console.warn("No existing draft PR found — creating new PR.")

        # Detect parent tracking issue
        parent_issue = None
        try:
            parent_issue = provider.find_parent_issue(
                issue_number, label=config.project.issue_label
            )
            if parent_issue:
                console.detail(f"Detected parent tracking issue: #{parent_issue}")
        except Exception:
            logger.debug("implementation.parent_issue_detection_failed", exc_info=True)

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary_path,
            close_issue=close_issue,
            parent_issue=parent_issue,
        )

        console.step("Creating pull request...")
        try:
            pr_info = git_pr.create_pr(
                repo_root=repo_root,
                title=task.title,
                body=body,
                base=main_branch,
                head=branch,
                draft=draft,
            )
            if pr_info is None:
                console.error("PR creation failed — could not determine the new PR number.")
                return False
            pr_url = str(pr_info.get("url", ""))
            console.success(f"PR created: {pr_url}")
        except Exception as e:
            console.error(f"PR creation failed: {e}")
            return False

    # Remove in-progress label
    with contextlib.suppress(Exception):
        remove_in_progress_label(provider, issue_number)

    lines = []
    lines.append(f"  PR      [url]{pr_url}[/]")
    lines.append(f"  Issue   {console.issue_ref(issue_number, task.title)}")
    console.panel("\n".join(lines), title="Implementation done")

    return True

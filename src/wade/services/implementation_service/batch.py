"""Batch implementation — parallel sessions, tracking issues, polling."""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path

import structlog
from crossby.models.ai import EffortLevel

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import sync as git_sync
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.deps import DependencyGraph
from wade.models.permission import PermissionMode
from wade.models.task import (
    Task,
    has_checklist_items,
    is_tracking_issue,
    parse_all_issue_refs,
    parse_dependency_refs,
    parse_tracking_child_ids,
)
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_permission_mode,
)
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.terminal import launch_batch_in_terminals

logger = structlog.get_logger()

__all__ = [
    "_BATCH_STATUS_DONE",
    "_BATCH_STATUS_IN_PROGRESS",
    "_BATCH_STATUS_MERGED",
    "_BATCH_STATUS_NOT_STARTED",
    "_BATCH_STATUS_UNKNOWN",
    "_POLL_INTERVAL_SECONDS",
    "_POLL_TIMEOUT_SECONDS",
    "_build_graph_from_issues",
    "_build_implement_cmd",
    "_build_pr_index",
    "_classify_issue_status",
    "_find_tracking_issue",
    "_get_remote_branches",
    "_is_merged_to_main",
    "_query_branches",
    "batch",
    "check_tracking_issue_and_batch",
    "poll_batch_completion",
]


def _build_implement_cmd(
    issue_id: str,
    *,
    tool: str | None,
    model: str | None,
    model_explicit: bool,
    effort: EffortLevel | None,
    permission_mode: PermissionMode,
    permission_mode_explicit: bool,
    chain_ids: list[str] | None = None,
) -> list[str]:
    """Build the ``wade implement`` child command for one batch issue.

    The permission mode is forwarded **only when it was explicit** — the user
    passed ``--permission-mode``/``--yolo`` or changed it in the confirmation UI.
    Two failure modes bound this:

    - *Forward an explicit mode.* A child reloads the project config and
      re-resolves its own permission mode, so an explicit ``default`` (e.g. the
      user downgrading from a yolo-configured ``ai.implement``) that was *not*
      forwarded would have every child resolve back to ``yolo`` — more autonomy
      than the user confirmed. Forwarding it pins children to that decision.
    - *Don't forward an implicit one.* ``wade implement`` derives
      ``permission_mode_explicit`` from ``--permission-mode`` being present, and
      that explicitness propagates into the post-implementation review flow. So
      forwarding an *implicit* ``default`` would make it look chosen and suppress a
      child's own config-driven autonomy downstream (e.g. a non-default
      ``ai.review_pr_comments.permission_mode``). An implicit mode is left for each
      child to re-resolve from the same config, reaching the same result.
    """
    cmd = ["wade", "implement", issue_id]
    if tool:
        cmd.extend(["--ai", tool])
    if model and model_explicit:
        cmd.extend(["--model", model])
    if effort:
        cmd.extend(["--effort", effort.value])
    if permission_mode_explicit:
        cmd.extend(["--permission-mode", permission_mode.value])
    if chain_ids:
        cmd.extend(["--chain", ",".join(chain_ids)])
    return cmd


def check_tracking_issue_and_batch(
    task: Task,
    *,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    ai_explicit: bool,
    model_explicit: bool,
    effort: str | None,
    effort_explicit: bool,
    yolo: bool | None,
    permission_mode: str | None = None,
    cd_only: bool = False,
) -> bool | None:
    """Detect tracking issues and redirect to batch implementation.

    Returns True/False if the tracking-issue path was taken, or None if
    the task is not a tracking issue (caller should continue normally).
    """
    if not is_tracking_issue(task.title):
        return None

    child_ids = (
        parse_tracking_child_ids(task.body)
        if has_checklist_items(task.body)
        else parse_all_issue_refs(task.body)
    )
    if not child_ids:
        return None

    if cd_only:
        console.info("Tracking issue detected — batch redirect skipped for cd-only mode")
        return None

    refs = ", ".join(f"#{cid}" for cid in child_ids)
    console.info(f"#{task.id} is a tracking issue for: {refs}")
    if prompts.confirm("Start batch implementation?", default=True):
        return batch(
            issue_numbers=child_ids,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
            permission_mode=permission_mode,
        )
    return False


def batch(
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
    permission_mode: str | None = None,
) -> bool:
    """Start parallel implementation sessions for multiple issues.

    Independent issues launch in parallel terminals.
    Dependent chains: only the first issue in each chain is launched; the
    remaining chain members are printed in order for manual sequential
    execution (one cannot work on a dependent issue before its blocker is done).
    """
    # Deduplicate while preserving order
    issue_numbers = list(dict.fromkeys(issue_numbers))

    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    console.rule(f"implement-batch ({len(issue_numbers)} issues)")

    # Resolve AI tool and model, then offer interactive confirmation.
    resolved_tool = resolve_ai_tool(ai_tool, config, "implement")
    resolved_model = resolve_model(model, config, "implement", tool=resolved_tool)
    resolved_effort = resolve_effort(effort, config, "implement", tool=resolved_tool)
    resolved_permission_mode = resolve_permission_mode(permission_mode, yolo, config, "implement")
    _pre_model = resolved_model
    _pre_permission_mode = resolved_permission_mode
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
        permission_mode_explicit=permission_mode is not None or yolo is not None,
    )
    # If the user changed the model interactively, propagate it explicitly to child sessions
    if not model_explicit and resolved_model != _pre_model:
        model_explicit = True
    # Forward the permission mode to children only when the user actually chose it —
    # a CLI flag, or a change in the confirmation UI. An implicit default/yolo
    # (resolved purely from config) is left for each child to re-resolve, so we do
    # not make an implicit mode look explicit and suppress a child's own
    # config-driven autonomy downstream (see _build_implement_cmd).
    permission_mode_explicit = (
        permission_mode is not None
        or yolo is not None
        or resolved_permission_mode != _pre_permission_mode
    )

    if not model_explicit:
        console.info("Model: auto (per-issue complexity)")

    # Check for dependency ordering
    # Try to load deps from issue bodies (look for "Depends on" references)
    graph = _build_graph_from_issues(issue_numbers, config)

    if graph and graph.edges:
        try:
            independent, chains = graph.partition(issue_numbers)
        except ValueError:
            console.error(
                "Dependency cycle detected among the requested issues. "
                "Remove or fix the circular 'Depends on' references and retry."
            )
            return False
        console.info(f"Dependency analysis: {len(independent)} independent, {len(chains)} chain(s)")
    else:
        independent = issue_numbers
        chains = []

    def _build_cmd(issue_id: str, chain_ids: list[str] | None = None) -> list[str]:
        """Build the ``wade implement`` command for a single issue.

        Thin closure over the resolved selection; see :func:`_build_implement_cmd`
        for the command shape and when the permission mode is forwarded.
        """
        return _build_implement_cmd(
            issue_id,
            tool=resolved_tool,
            model=resolved_model,
            model_explicit=model_explicit,
            effort=resolved_effort,
            permission_mode=resolved_permission_mode,
            permission_mode_explicit=permission_mode_explicit,
            chain_ids=chain_ids,
        )

    # Collect all items to launch in one batch
    batch_items: list[tuple[list[str], str | None, str | None]] = []

    for issue_id in independent:
        console.step(f"Preparing #{issue_id} (independent)")
        batch_items.append((_build_cmd(issue_id), str(repo_root), f"wade #{issue_id}"))

    # Chains: launch only the first item with --chain for auto-continuation
    for chain in chains:
        console.info(f"Dependency chain: {' → '.join(f'#{n}' for n in chain)}")
        chain_rest = chain[1:] if len(chain) > 1 else None
        batch_items.append(
            (_build_cmd(chain[0], chain_ids=chain_rest), str(repo_root), f"wade #{chain[0]}")
        )

    if not batch_items:
        console.panel("  No issues to launch", title="Batch started")
        return False

    # Try to launch terminals (best-effort, non-fatal)
    console.step(f"Launching {len(batch_items)} session(s) in new terminal window")
    try:
        launched = launch_batch_in_terminals(batch_items)
    except Exception as exc:
        logger.warning("batch.launch_failed", error=str(exc), exc_info=True)
        launched = False

    if launched:
        console.panel(
            f"  Launched {len(batch_items)} implementation session(s)",
            title="Batch started",
        )
    else:
        console.warn("Could not launch terminals — run these commands manually:")
        for cmd, _cwd, _title in batch_items:
            console.detail(f"  {' '.join(cmd)}")

    # Find tracking issue by checking all batch issues (not just the first)
    tracking_id = _find_tracking_issue(issue_numbers, config)

    # Enter polling loop to monitor session progress
    poll_batch_completion(
        issue_numbers=issue_numbers,
        repo_root=repo_root,
        config=config,
        tracking_id=tracking_id,
    )

    return True


def _find_tracking_issue(
    issue_numbers: list[str],
    config: ProjectConfig,
) -> str | None:
    """Find a parent/tracking issue by checking all batch issue numbers."""
    try:
        provider = get_provider(config)
    except Exception:
        logger.debug("batch.find_parent_failed", exc_info=True)
        return None
    for num in issue_numbers:
        try:
            tracking_id = provider.find_parent_issue(num, label=config.project.issue_label)
        except Exception:
            logger.debug("batch.find_parent_failed", exc_info=True, issue=num)
            continue
        if tracking_id:
            return tracking_id
    return None


# --- Batch session status ---

_BATCH_STATUS_NOT_STARTED = "not_started"
_BATCH_STATUS_IN_PROGRESS = "in_progress"
_BATCH_STATUS_DONE = "done"
_BATCH_STATUS_MERGED = "merged"
# Distinct from NOT_STARTED: either a branch exists but git couldn't be read to
# measure it, or the branch-list query itself failed. wade won't guess — it
# reports UNKNOWN rather than pretending the issue is untouched (B3, #359).
_BATCH_STATUS_UNKNOWN = "unknown"

_POLL_INTERVAL_SECONDS = 30
_POLL_TIMEOUT_SECONDS = 4 * 60 * 60  # 4 hours


def _branch_merged_into_main(repo_root: Path, branch: str, main_branch: str) -> bool:
    """Return True if *branch*'s tip is an ancestor of the (origin) main branch.

    Deterministic: uses ``git merge-base --is-ancestor`` (via the git layer)
    against the real branch ref instead of grepping commit subjects (which
    false-matches any commit that merely mentions the issue number). The origin
    ref is authoritative when it resolves; a bad/missing ref falls back to the
    local main. Returns False when neither ref can be resolved.
    """
    for base in (f"origin/{main_branch}", main_branch):
        # None = the base ref could not be resolved, so try the other base;
        # a definitive True/False from the origin ref is authoritative.
        merged = git_branch.is_merged_into(repo_root, branch, base)
        if merged is not None:
            return merged
    return False


def _is_merged_to_main(
    repo_root: Path,
    issue_num: str,
    main_branch: str,
    branch_set: set[str],
) -> bool:
    """Return True if a branch for this issue is fully merged into main.

    Ref-based (``git merge-base --is-ancestor``) — never the old fragile
    commit-subject grep. Only branches present in *branch_set* are checked, so
    an unrelated commit mentioning the number can no longer cause a false
    "merged". Returns False when no branch ref for the issue exists.

    *branch_set* is required (no default): a stale three-argument caller must
    fail at the call site, not silently return False (which reads as
    NOT_STARTED / IN_PROGRESS for an already-merged issue).
    """
    pattern = rf"/0*{re.escape(issue_num)}(?:-|$)"
    for branch in branch_set:
        if re.search(pattern, branch) and _branch_merged_into_main(repo_root, branch, main_branch):
            return True
    return False


def _classify_issue_status(
    issue_num: str,
    pr_by_issue: dict[str, git_pr.PRSummary],
    branch_set: set[str] | None,
    main_branch: str,
    repo_root: Path,
) -> str:
    """Classify the status of a single issue in a batch.

    ``branch_set`` is the set of known branch names, or ``None`` when the branch
    query failed this cycle. For an issue with no (usable) PR we need the branch
    list to decide; if it is unavailable we return ``_BATCH_STATUS_UNKNOWN``
    rather than falsely reporting ``_BATCH_STATUS_NOT_STARTED``.

    Returns one of the _BATCH_STATUS_* constants.
    """
    pr = pr_by_issue.get(issue_num)
    if pr:
        if pr.merged_at:
            return _BATCH_STATUS_MERGED
        if pr.is_draft:
            return _BATCH_STATUS_IN_PROGRESS
        if pr.state != "CLOSED":
            # Open, non-draft → done (done marks PR ready)
            return _BATCH_STATUS_DONE
        # CLOSED without merged_at — PR was abandoned; fall through to branch check

    # No PR (or abandoned PR) — branch state decides. If the branch-list query
    # failed we can't tell "no branch" from "query failed", so report unknown
    # rather than falsely reporting NOT_STARTED (#359).
    if branch_set is None:
        return _BATCH_STATUS_UNKNOWN

    # Deterministic merge check against the real branch ref (B3).
    if _is_merged_to_main(repo_root, issue_num, main_branch, branch_set):
        return _BATCH_STATUS_DONE

    # No PR (or abandoned PR) — check if a branch for the issue exists.
    pattern = rf"/0*{re.escape(issue_num)}(?:-|$)"
    matching_branches = [b for b in branch_set if re.search(pattern, b)]
    if matching_branches:
        errored = False
        for branch in matching_branches:
            try:
                if git_branch.commits_ahead(repo_root, branch, main_branch) > 0:
                    return _BATCH_STATUS_IN_PROGRESS
            except GitError:
                errored = True
        # A branch exists. If we could measure at least one and it had no
        # commits ahead, it is still an in-progress (scaffolded) branch. If we
        # could NOT measure any (all git errors), report UNKNOWN rather than
        # guessing.
        return _BATCH_STATUS_UNKNOWN if errored else _BATCH_STATUS_IN_PROGRESS

    return _BATCH_STATUS_NOT_STARTED


def _pick_pr(candidates: list[git_pr.PRSummary]) -> git_pr.PRSummary:
    """Pick one PR from several for the same issue, deterministically.

    Prefers an open non-draft PR, then the most-recently-updated, then the
    highest PR number — so a re-run with the same remote state always yields the
    same choice (B3, no last-wins nondeterminism).
    """

    def sort_key(pr: git_pr.PRSummary) -> tuple[bool, str, int]:
        open_nondraft = pr.state.upper() == "OPEN" and not pr.is_draft
        return (open_nondraft, pr.updated_at or "", pr.number)

    return max(candidates, key=sort_key)


def _build_pr_index(
    repo_root: Path,
    issue_numbers: list[str],
) -> dict[str, git_pr.PRSummary]:
    """Build a mapping from issue number to PR data using a single gh pr list call.

    When an issue has multiple PRs, one is picked deterministically via
    :func:`_pick_pr` (never last-wins). Warns if the ``gh pr list`` result was
    truncated at the limit, since a needed PR may be missing (B3).
    """
    from wade.services.implementation_service._shared import extract_issue_from_branch

    limit = 200
    prs = git_pr.list_prs(repo_root, state="all", limit=limit)
    if len(prs) >= limit:
        logger.warning("batch.pr_list_truncated", limit=limit, returned=len(prs))
        console.warn(
            f"PR list hit the {limit}-PR limit — batch status may be incomplete for older PRs."
        )

    issue_set = set(issue_numbers)
    candidates: dict[str, list[git_pr.PRSummary]] = {}
    for pr in prs:
        extracted = extract_issue_from_branch(pr.head_ref_name)
        if extracted and extracted in issue_set:
            candidates.setdefault(extracted, []).append(pr)

    return {issue: _pick_pr(prs_for_issue) for issue, prs_for_issue in candidates.items()}


def _get_remote_branches(repo_root: Path) -> set[str]:
    """Get the set of remote and local branch names.

    Delegates to the git layer (``git_branch.list_branch_names``), which retries
    on lock contention and raises ``GitError`` on failure. The failure is
    propagated — not swallowed into an empty set — so ``poll_batch_completion``
    can distinguish "no branches" from "the query failed".

    Raises:
        GitError: If the branch listing fails.
    """
    return git_branch.list_branch_names(repo_root)


def _query_branches(repo_root: Path, *, previous: set[str] | None) -> set[str] | None:
    """Query branch names, falling back to the last good snapshot on failure.

    Returns the freshly queried branch set on success. On a git failure it keeps
    ``previous`` (which may be ``None`` on the first cycle, meaning "still
    unknown") so a transient lock/query error does not misclassify issues as
    "not started".
    """
    try:
        return _get_remote_branches(repo_root)
    except GitError:
        logger.warning("batch.branch_query_failed", exc_info=True)
        return previous


def poll_batch_completion(
    issue_numbers: list[str],
    repo_root: Path,
    config: ProjectConfig,
    tracking_id: str | None = None,
    *,
    poll_interval: int = _POLL_INTERVAL_SECONDS,
    timeout: int = _POLL_TIMEOUT_SECONDS,
) -> None:
    """Poll for completion of all batch sessions, showing live progress.

    Monitors PRs and branches until all sessions complete, then optionally
    auto-triggers coherence review. Handles Ctrl+C gracefully.
    """

    poll_interval = max(1, poll_interval)
    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            main_branch = "main"

    console.info("Monitoring batch progress (Ctrl+C to exit)...")

    interrupted = False
    elapsed = 0
    pr_index: dict[str, git_pr.PRSummary] = {}
    # None means "no successful branch query yet" — distinct from an empty set
    # (a repo with genuinely no branches). Kept across cycles so a transient
    # failure falls back to the last good snapshot instead of misclassifying.
    branch_set: set[str] | None = None

    try:
        while elapsed < timeout:
            # Fetch latest remote state
            with contextlib.suppress(GitError):
                git_sync.fetch_origin(repo_root)

            pr_index = _build_pr_index(repo_root, issue_numbers)
            branch_set = _query_branches(repo_root, previous=branch_set)

            statuses: dict[str, str] = {}
            for num in issue_numbers:
                statuses[num] = _classify_issue_status(
                    num, pr_index, branch_set, main_branch, repo_root
                )

            done_count = sum(
                1 for s in statuses.values() if s in (_BATCH_STATUS_DONE, _BATCH_STATUS_MERGED)
            )
            in_progress = sum(1 for s in statuses.values() if s == _BATCH_STATUS_IN_PROGRESS)
            # NOT_STARTED and UNKNOWN both count as "pending" for the progress
            # line; UNKNOWN is kept distinct in `statuses` so it is not silently
            # treated as done.
            not_started = sum(
                1
                for s in statuses.values()
                if s in (_BATCH_STATUS_NOT_STARTED, _BATCH_STATUS_UNKNOWN)
            )
            total = len(issue_numbers)

            console.step(
                f"Waiting... ({done_count}/{total} done, "
                f"{in_progress} in progress, {not_started} pending)"
            )

            if done_count == total:
                break

            time.sleep(poll_interval)
            elapsed += poll_interval

    except KeyboardInterrupt:
        interrupted = True
        console.info("")  # newline after ^C

    # Print final summary
    pr_index = _build_pr_index(repo_root, issue_numbers) if not interrupted else pr_index
    branch_set = _query_branches(repo_root, previous=branch_set) if not interrupted else branch_set
    final_statuses: dict[str, str] = {}
    for num in issue_numbers:
        final_statuses[num] = _classify_issue_status(
            num, pr_index, branch_set, main_branch, repo_root
        )

    done_count = sum(
        1 for s in final_statuses.values() if s in (_BATCH_STATUS_DONE, _BATCH_STATUS_MERGED)
    )
    total = len(issue_numbers)
    lines = []
    for num in issue_numbers:
        status = final_statuses[num]
        label = {
            _BATCH_STATUS_DONE: "completed",
            _BATCH_STATUS_MERGED: "merged",
            _BATCH_STATUS_IN_PROGRESS: "in progress",
            _BATCH_STATUS_NOT_STARTED: "not started",
            _BATCH_STATUS_UNKNOWN: "unknown",
        }.get(status, status)
        pr = pr_index.get(num)
        url = f" {pr.url}" if pr and pr.url else ""
        lines.append(f"  #{num}: {label}{url}")

    console.panel("\n".join(lines), title=f"Batch summary ({done_count}/{total} done)")

    if interrupted:
        console.hint(
            "Interrupted. To resume monitoring, rerun `wade implement-batch` with the same issues."
        )
        return

    if elapsed >= timeout:
        console.warn("Polling timed out. Check session status manually.")
        return

    # All sessions complete — auto-trigger coherence review
    if tracking_id and done_count == total:
        console.info(f"All sessions complete. Running coherence review for #{tracking_id}...")
        from wade.services.batch_review_service import review_batch

        review_batch(tracking_id, project_root=repo_root)


def _build_graph_from_issues(
    issue_numbers: list[str],
    config: ProjectConfig,
) -> DependencyGraph | None:
    """Try to build a dependency graph from issue body cross-references."""
    from wade.models.deps import DependencyEdge, DependencyGraph

    provider = get_provider(config)
    edges: list[DependencyEdge] = []
    valid_set = set(issue_numbers)

    for num in issue_numbers:
        try:
            task = provider.read_task(num)
        except Exception:
            logger.debug("batch.issue_read_failed", issue_num=num, exc_info=True)
            continue

        refs = parse_dependency_refs(task.body)
        for dep_id in refs["depends_on"]:
            if dep_id in valid_set:
                edges.append(DependencyEdge(from_task=dep_id, to_task=num))

    if edges:
        return DependencyGraph(edges=edges)
    return None


# ---------------------------------------------------------------------------
# Implementation cd
# ---------------------------------------------------------------------------

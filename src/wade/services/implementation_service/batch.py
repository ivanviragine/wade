"""Batch implementation — parallel sessions, tracking issues, polling."""

from __future__ import annotations

import contextlib
import re
import subprocess
import time
from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import sync as git_sync
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.deps import DependencyGraph
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
    resolve_yolo,
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
    "_POLL_INTERVAL_SECONDS",
    "_POLL_TIMEOUT_SECONDS",
    "_build_graph_from_issues",
    "_build_pr_index",
    "_classify_issue_status",
    "_find_tracking_issue",
    "_get_remote_branches",
    "_is_merged_to_main",
    "batch",
    "check_tracking_issue_and_batch",
    "poll_batch_completion",
]


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
    resolved_yolo = resolve_yolo(yolo, config, "implement", tool=resolved_tool)
    _pre_model = resolved_model
    resolved_tool, resolved_model, resolved_effort, resolved_yolo = confirm_ai_selection(
        resolved_tool,
        resolved_model,
        tool_explicit=ai_explicit,
        model_explicit=model_explicit,
        resolved_effort=resolved_effort,
        effort_explicit=effort_explicit,
        resolved_yolo=resolved_yolo,
        yolo_explicit=yolo is not None,
    )
    # If the user changed the model interactively, propagate it explicitly to child sessions
    if not model_explicit and resolved_model != _pre_model:
        model_explicit = True

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
        """Build the wade implement command for a single issue.

        Args:
            issue_id: The issue number to implement.
            chain_ids: Optional remaining issue IDs for --chain continuation.
        """
        cmd = ["wade", "implement", issue_id]
        if resolved_tool:
            cmd.extend(["--ai", resolved_tool])
        if resolved_model and model_explicit:
            cmd.extend(["--model", resolved_model])
        if resolved_effort:
            cmd.extend(["--effort", resolved_effort.value])
        if resolved_yolo:
            cmd.append("--yolo")
        if chain_ids:
            cmd.extend(["--chain", ",".join(chain_ids)])
        return cmd

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

_POLL_INTERVAL_SECONDS = 30
_POLL_TIMEOUT_SECONDS = 4 * 60 * 60  # 4 hours


def _is_merged_to_main(repo_root: Path, issue_num: str, main_branch: str) -> bool:
    """Return True if a branch for this issue was merged directly into main.

    Searches recent merge commits on ``origin/<main_branch>`` for the typical
    branch-name pattern (e.g. "feat/227-..." or "fix/227-...").
    """
    try:
        result = subprocess.run(
            ["git", "log", f"origin/{main_branch}", "--oneline", "-100"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        pattern = rf"/0*{re.escape(issue_num)}(?:[^0-9]|$)"
        return bool(re.search(pattern, result.stdout))
    except FileNotFoundError:
        return False


def _classify_issue_status(
    issue_num: str,
    pr_by_issue: dict[str, git_pr.PRSummary],
    branch_set: set[str],
    main_branch: str,
    repo_root: Path,
) -> str:
    """Classify the status of a single issue in a batch.

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

    # No PR (or abandoned PR) — check if branch exists
    pattern = rf"/0*{re.escape(issue_num)}(?:-|$)"
    matching_branches = [b for b in branch_set if re.search(pattern, b)]
    if matching_branches:
        # Branch exists — check for commits ahead of base
        for branch in matching_branches:
            try:
                ahead = git_branch.commits_ahead(repo_root, branch, main_branch)
                if ahead > 0:
                    return _BATCH_STATUS_IN_PROGRESS
            except GitError:
                pass
        return _BATCH_STATUS_IN_PROGRESS

    # No PR, no branch — check for direct merge to main
    if _is_merged_to_main(repo_root, issue_num, main_branch):
        return _BATCH_STATUS_DONE

    return _BATCH_STATUS_NOT_STARTED


def _build_pr_index(
    repo_root: Path,
    issue_numbers: list[str],
) -> dict[str, git_pr.PRSummary]:
    """Build a mapping from issue number to PR data using a single gh pr list call."""
    prs = git_pr.list_prs(repo_root, state="all", limit=200)
    issue_set = set(issue_numbers)
    result: dict[str, git_pr.PRSummary] = {}
    for pr in prs:
        from wade.services.implementation_service.core import extract_issue_from_branch

        extracted = extract_issue_from_branch(pr.head_ref_name)
        if extracted and extracted in issue_set:
            result[extracted] = pr
    return result


def _get_remote_branches(repo_root: Path) -> set[str]:
    """Get the set of remote and local branch names."""
    branches: set[str] = set()
    for args in (
        ["git", "branch", "-r", "--format=%(refname:short)"],
        ["git", "branch", "--format=%(refname:short)"],
    ):
        try:
            result = subprocess.run(
                args,
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            branches.update(line.strip() for line in result.stdout.splitlines() if line.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return branches


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
    branch_set: set[str] = set()

    try:
        while elapsed < timeout:
            # Fetch latest remote state
            with contextlib.suppress(GitError):
                git_sync.fetch_origin(repo_root)

            pr_index = _build_pr_index(repo_root, issue_numbers)
            branch_set = _get_remote_branches(repo_root)

            statuses: dict[str, str] = {}
            for num in issue_numbers:
                statuses[num] = _classify_issue_status(
                    num, pr_index, branch_set, main_branch, repo_root
                )

            done_count = sum(
                1 for s in statuses.values() if s in (_BATCH_STATUS_DONE, _BATCH_STATUS_MERGED)
            )
            in_progress = sum(1 for s in statuses.values() if s == _BATCH_STATUS_IN_PROGRESS)
            not_started = sum(1 for s in statuses.values() if s == _BATCH_STATUS_NOT_STARTED)
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
    branch_set = _get_remote_branches(repo_root) if not interrupted else branch_set
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

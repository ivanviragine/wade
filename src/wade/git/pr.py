"""Pull Request operations via the ``gh`` CLI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from wade.git.repo import GitError

log = structlog.get_logger(__name__)


class GhCliError(GitError):
    """Raised when a ``gh`` CLI command fails."""


class PRSummary(BaseModel):
    """Summary of a pull request as returned by ``gh pr list``."""

    model_config = {"populate_by_name": True}

    number: int
    url: str
    head_ref_name: str = Field(alias="headRefName")
    state: str
    is_draft: bool = Field(alias="isDraft")
    merged_at: str | None = Field(default=None, alias="mergedAt")


def _run_gh(
    *args: str,
    cwd: Path,
    check: bool = True,
    retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run a ``gh`` CLI command and return the result.

    Args:
        *args: gh subcommand and arguments.
        cwd: Working directory for the command.
        check: If True, raise GhCliError on non-zero exit.
        retries: Number of times to retry on non-zero exit (default 0).

    Returns:
        CompletedProcess with captured stdout/stderr.

    Raises:
        GhCliError: If check is True and the command fails after all retries.
    """
    cmd = ["gh", *args]
    log.debug("gh.run", cmd=cmd, cwd=str(cwd))
    attempt = 0
    while True:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise GhCliError("gh CLI not found — install it from https://cli.github.com/") from exc

        if result.returncode != 0 and attempt < retries:
            attempt += 1
            log.warning(
                "gh.retrying",
                cmd=cmd,
                returncode=result.returncode,
                attempt=attempt,
                retries=retries,
                stderr=result.stderr.strip()[:200],
            )
            time.sleep(attempt)
            continue

        if check and result.returncode != 0:
            raise GhCliError(
                f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result


def create_pr(
    repo_root: Path,
    title: str,
    body: str,
    base: str,
    head: str | None = None,
    draft: bool = False,
) -> dict[str, str | int]:
    """Create a pull request via ``gh pr create``.

    Args:
        repo_root: Repository root directory.
        title: PR title.
        body: PR body (Markdown).
        base: Base branch to merge into (e.g., "main").
        head: Head branch with changes. If None, gh infers the current branch.
        draft: If True, create as a draft PR.

    Returns:
        Dict with "number" (int) and "url" (str) keys.

    Raises:
        GhCliError: If PR creation fails.
    """
    cmd_args = [
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--base",
        base,
    ]
    if head is not None:
        cmd_args.extend(["--head", head])
    if draft:
        cmd_args.append("--draft")

    log.info("pr.create", title=title, base=base, head=head, draft=draft)
    result = _run_gh(*cmd_args, cwd=repo_root)

    # gh pr create prints the PR URL to stdout
    pr_url = result.stdout.strip()

    # Try to get structured info via gh pr view
    pr_info = _get_pr_info_from_url(repo_root, pr_url)
    if pr_info:
        return pr_info

    # Fallback: return URL only (number unknown)
    return {"number": 0, "url": pr_url}


def _get_pr_info_from_url(repo_root: Path, pr_url: str) -> dict[str, str | int] | None:
    """Extract PR number and URL from a PR URL via gh pr view."""
    if not pr_url:
        return None
    result = _run_gh(
        "pr",
        "view",
        pr_url,
        "--json",
        "number,url",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return {"number": data["number"], "url": data["url"]}
    except (json.JSONDecodeError, KeyError):
        return None


def merge_pr(
    repo_root: Path,
    pr_number: int,
    strategy: str = "squash",
    delete_branch: bool = True,
) -> None:
    """Merge a pull request via ``gh pr merge``.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to merge.
        strategy: Merge strategy — "squash", "merge", or "rebase".
        delete_branch: If True, delete the branch after merging.

    Raises:
        GhCliError: If the merge fails.
        ValueError: If strategy is not one of the allowed values.
    """
    allowed = ("squash", "merge", "rebase")
    if strategy not in allowed:
        raise ValueError(f"strategy must be one of {allowed}, got {strategy!r}")

    flag = f"--{strategy}"
    log.info("pr.merge", pr_number=pr_number, strategy=strategy, delete_branch=delete_branch)
    cmd_args = [
        "pr",
        "merge",
        str(pr_number),
        flag,
    ]
    if delete_branch:
        cmd_args.append("--delete-branch")
    _run_gh(*cmd_args, cwd=repo_root)


def update_pr_body(repo_root: Path, pr_number: int, body: str) -> bool:
    """Update the body of an existing pull request.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to update.
        body: New PR body content (Markdown).

    Returns:
        True if the update succeeded, False otherwise.
    """
    result = _run_gh(
        "pr",
        "edit",
        str(pr_number),
        "--body",
        body,
        cwd=repo_root,
        check=False,
        retries=3,
    )
    return result.returncode == 0


def get_pr_for_branch(repo_root: Path, branch: str) -> dict[str, str | int | bool] | None:
    """Find an open PR for the given branch.

    Args:
        repo_root: Repository root directory.
        branch: Branch name to search for.

    Returns:
        Dict with "number" (int), "url" (str), "title" (str),
        "state" (str), and "isDraft" (bool) keys, or None if no PR exists.
    """
    result = _run_gh(
        "pr",
        "view",
        branch,
        "--json",
        "number,url,title,state,isDraft",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return {
            "number": data["number"],
            "url": data["url"],
            "title": data.get("title", ""),
            "state": data.get("state", ""),
            "isDraft": data.get("isDraft", False),
        }
    except (json.JSONDecodeError, KeyError):
        return None


def list_prs(
    repo_root: Path,
    *,
    state: str = "all",
    limit: int = 100,
) -> list[PRSummary]:
    """List PRs for the repository.

    Returns a list of PRSummary objects with number, url, headRefName, state,
    isDraft, mergedAt fields.  Uses a single ``gh pr list`` call to avoid
    per-branch API requests.  Returns an empty list on any failure (missing
    ``gh`` binary, non-zero exit, or bad JSON).
    """
    try:
        result = _run_gh(
            "pr",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number,url,headRefName,state,isDraft,mergedAt",
            cwd=repo_root,
            check=False,
        )
    except GhCliError:
        return []
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
        return [PRSummary(**row) for row in rows]
    except (json.JSONDecodeError, KeyError, ValueError):
        return []


def get_pr_body(repo_root: Path, pr_number: int) -> str | None:
    """Fetch the body of a pull request.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to fetch.

    Returns:
        The PR body as a string, or None if the PR cannot be found.
    """
    result = _run_gh(
        "pr",
        "view",
        str(pr_number),
        "--json",
        "body",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        body: str = data.get("body", "")
        return body
    except (json.JSONDecodeError, KeyError):
        return None


def mark_pr_ready(repo_root: Path, pr_number: int) -> bool:
    """Mark a draft PR as ready for review.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to mark ready.

    Returns:
        True if the operation succeeded, False otherwise.
    """
    log.info("pr.ready", pr_number=pr_number)
    result = _run_gh(
        "pr",
        "ready",
        str(pr_number),
        cwd=repo_root,
        check=False,
        retries=3,
    )
    return result.returncode == 0


def get_pr_base_branch(repo_root: Path, pr_number: int) -> str | None:
    """Fetch the base branch name of a pull request.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to query.

    Returns:
        The base branch name, or None if the PR cannot be found.
    """
    result = _run_gh(
        "pr",
        "view",
        str(pr_number),
        "--json",
        "baseRefName",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        base: str = data.get("baseRefName", "")
        return base if base else None
    except (json.JSONDecodeError, KeyError):
        return None


def update_pr_base(repo_root: Path, pr_number: int, new_base: str) -> bool:
    """Change the base branch of a pull request.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to update.
        new_base: New base branch name.

    Returns:
        True if the update succeeded, False otherwise.
    """
    log.info("pr.update_base", pr_number=pr_number, new_base=new_base)
    result = _run_gh(
        "pr",
        "edit",
        str(pr_number),
        "--base",
        new_base,
        cwd=repo_root,
        check=False,
        retries=3,
    )
    return result.returncode == 0


def comment_on_pr(repo_root: Path, pr_number: int, body: str) -> None:
    """Post a comment on a pull request via ``gh pr comment``.

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to comment on.
        body: Comment body (Markdown).

    Raises:
        GhCliError: If the comment fails.
    """
    log.info("pr.comment", pr_number=pr_number)
    _run_gh(
        "pr",
        "comment",
        str(pr_number),
        "--body",
        body,
        cwd=repo_root,
    )

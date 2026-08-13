"""Pull Request operations via the ``gh`` CLI."""

from __future__ import annotations

import json
import re
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
    updated_at: str | None = Field(default=None, alias="updatedAt")


class PRRef(BaseModel):
    """Details of the PR associated with a branch (``gh pr view <branch>``)."""

    model_config = {"populate_by_name": True}

    number: int
    url: str = ""
    title: str = ""
    state: str = ""
    is_draft: bool = Field(default=False, alias="isDraft")
    base_ref_name: str = Field(default="", alias="baseRefName")


class PRLookup(BaseModel):
    """Result of looking up the PR for a branch.

    Distinguishes the three realities that the old ``dict | None`` return type
    conflated:

    - ``found=False, lookup_failed=False`` — no PR exists for the branch.
    - ``found=False, lookup_failed=True`` — the lookup itself failed (transient
      ``gh`` error, bad auth, or unparseable output). Callers MUST NOT treat
      this as "no PR" — retry or report the failure instead.
    - ``found=True`` — a PR exists; ``pr`` holds its details and ``state`` its
      ``OPEN`` / ``CLOSED`` / ``MERGED`` state.

    Callers acting on an *open* PR must check :attr:`is_open` (or ``state``)
    first — a merged or closed PR is ``found`` but not open.
    """

    found: bool = False
    lookup_failed: bool = False
    pr: PRRef | None = None

    @property
    def state(self) -> str:
        """The PR's state (``OPEN`` / ``CLOSED`` / ``MERGED``), or ``""``."""
        return self.pr.state if self.pr else ""

    @property
    def number(self) -> int | None:
        """The PR number, or ``None`` when no PR was found."""
        return self.pr.number if self.pr else None

    @property
    def url(self) -> str:
        """The PR URL, or ``""`` when no PR was found."""
        return self.pr.url if self.pr else ""

    @property
    def is_draft(self) -> bool:
        """Whether the found PR is a draft."""
        return bool(self.pr and self.pr.is_draft)

    @property
    def is_open(self) -> bool:
        """Whether a PR exists and is in the OPEN state."""
        return self.pr is not None and self.pr.state.upper() == "OPEN"

    @property
    def is_merged(self) -> bool:
        """Whether a PR exists and has been MERGED."""
        return self.pr is not None and self.pr.state.upper() == "MERGED"

    @property
    def is_closed_or_merged(self) -> bool:
        """Whether a PR exists and is CLOSED or MERGED (not actionable as open)."""
        return self.pr is not None and self.pr.state.upper() in ("CLOSED", "MERGED")


# Stderr substrings that mark a TRANSIENT gh/GitHub failure worth retrying.
# Permanent failures (404 not found, 422 unprocessable, bad auth) are NOT here —
# retrying them just wastes time and, for merge_pr, risks acting twice (B4).
_TRANSIENT_GH_SIGNALS = (
    "rate limit",
    "was submitted too quickly",
    "connection reset",
    "connection refused",
    "connection timed out",
    "timeout",
    "timed out",
    "temporary failure",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    # Specific EOF phrasings only — a bare "eof" substring also matches benign
    # tokens (e.g. a branch named ``feat/eof-parser``) echoed back in stderr.
    "unexpected eof",
    "eof occurred",
    "tls handshake",
    "i/o timeout",
)


def _is_transient_gh_error(stderr: str) -> bool:
    """Return True when *stderr* indicates a transient (retryable) gh failure."""
    lowered = stderr.lower()
    return any(sig in lowered for sig in _TRANSIENT_GH_SIGNALS)


# GitHub rejects a merge with this message when `gh pr merge` sends an
# `expectedHeadOid` (the PR record's head SHA) that no longer matches the
# branch ref's real tip. Kept OUT of _TRANSIENT_GH_SIGNALS on purpose: that
# tuple is shared by every _run_gh(retries=N) caller (update_pr_body,
# mark_pr_ready, update_pr_base, update_pr_title), so adding it there would
# broaden retry behavior well beyond merges. This signal needs its own bounded
# fast-retry + diagnosis path local to merge_pr (see _diagnose_stale_pr_head).
_STALE_PR_HEAD_SIGNAL = "head branch is out of date"


def _is_stale_pr_head_error(stderr: str) -> bool:
    """Return True when *stderr* is GitHub's stale-PR-head merge rejection."""
    return _STALE_PR_HEAD_SIGNAL in stderr.lower()


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
        retries: Max retries on a **transient** failure (default 0). Permanent
            failures (404, 422, bad auth) never retry — only stderr matching
            :data:`_TRANSIENT_GH_SIGNALS` does.

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

        if result.returncode != 0 and attempt < retries and _is_transient_gh_error(result.stderr):
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


def _get_pr_state(repo_root: Path, pr_number: int) -> str | None:
    """Return a PR's state (``OPEN`` / ``CLOSED`` / ``MERGED``) or None on failure."""
    result = _run_gh("pr", "view", str(pr_number), "--json", "state", cwd=repo_root, check=False)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        state: str = data.get("state", "")
        return state or None
    except (json.JSONDecodeError, KeyError):
        return None


def _get_pr_head_ref(repo_root: Path, pr_number: int) -> tuple[str, str] | None:
    """Return a PR's ``(headRefOid, headRefName)`` from GitHub's PR record.

    ``headRefOid`` is the head SHA ``gh pr merge`` sends as its optimistic-
    concurrency ``expectedHeadOid`` guard; ``headRefName`` is the branch to
    cross-check against. Returns None on non-zero exit or incomplete/unparseable
    output.
    """
    result = _run_gh(
        "pr",
        "view",
        str(pr_number),
        "--json",
        "headRefOid,headRefName",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        oid = data.get("headRefOid", "")
        name = data.get("headRefName", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not oid or not name:
        return None
    return oid, name


def _get_branch_tip_oid(repo_root: Path, branch: str) -> str | None:
    """Return the true tip SHA of *branch* as GitHub's git ref sees it.

    Uses the SINGULAR ``git/ref/heads/<branch>`` path — an exact match that
    returns a single ref object with ``.object.sha``. The plural
    ``git/refs/...`` form is a prefix match returning an array; it must not be
    used here. ``gh api`` substitutes ``{owner}``/``{repo}`` from the repo at
    ``cwd=repo_root``; branch names with slashes are fine in the path. Returns
    None on non-zero exit or unparseable output.
    """
    result = _run_gh(
        "api",
        f"repos/{{owner}}/{{repo}}/git/ref/heads/{branch}",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        sha: str = data["object"]["sha"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return sha or None


def _diagnose_stale_pr_head(repo_root: Path, pr_number: int) -> str | None:
    """Return an actionable message when a PR's head record is stale, else None.

    GitHub's "Head branch is out of date" merge rejection is ambiguous: it fires
    both when the branch is genuinely behind its base (GitHub's rebase advice is
    correct) AND when GitHub's async PR-synchronize job hasn't advanced the PR's
    ``head.sha`` to the branch's real tip (the branch is already fine; rebase
    advice is misleading). Comparing the PR head OID against the branch ref tip
    distinguishes the two:

    - Either fetch fails → None (never fabricate a diagnosis).
    - OIDs equal → None (genuine branch-behind-base; the caller keeps GitHub's
      raw message, whose rebase advice fits).
    - OIDs differ → the stale-sync case; return a message naming both SHAs and
      the ``gh pr close``/``reopen`` resync that clears it.
    """
    head = _get_pr_head_ref(repo_root, pr_number)
    if head is None:
        return None
    head_oid, branch = head
    tip_oid = _get_branch_tip_oid(repo_root, branch)
    if tip_oid is None:
        return None
    if head_oid == tip_oid:
        return None
    return (
        f"PR #{pr_number} merge blocked: GitHub's PR record is stale "
        f"(head {head_oid[:9]} ≠ branch tip {tip_oid[:9]}). The branch is "
        f"fully pushed and fine — GitHub's async PR sync hasn't caught up. "
        f"Force a resync with:  gh pr close {pr_number} && gh pr reopen "
        f"{pr_number}  then retry the merge."
    )


def create_pr(
    repo_root: Path,
    title: str,
    body: str,
    base: str,
    head: str | None = None,
    draft: bool = False,
) -> dict[str, str | int] | None:
    """Create a pull request via ``gh pr create``.

    Args:
        repo_root: Repository root directory.
        title: PR title.
        body: PR body (Markdown).
        base: Base branch to merge into (e.g., "main").
        head: Head branch with changes. If None, gh infers the current branch.
        draft: If True, create as a draft PR.

    Returns:
        Dict with "number" (int) and "url" (str) keys, or ``None`` when the PR
        number cannot be determined — this function never fabricates a ``#0``
        (B5). Callers must handle ``None`` and report the failure.

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
    # gh pr create is NOT idempotent — an attempt that reached GitHub but whose
    # response was lost must never create a second PR for the branch. So we run
    # the retry loop here (not inside _run_gh) and, before every retry, re-check
    # whether a PR for the head branch now exists; if so the earlier attempt
    # actually succeeded, so we return it instead of creating a duplicate (or
    # failing with GitHub's "a pull request already exists"). Mirrors merge_pr's
    # state-aware retry (B4).
    max_retries = 3
    attempt = 0
    while True:
        result = _run_gh(*cmd_args, cwd=repo_root, check=False)
        if result.returncode == 0:
            break
        if head is not None:
            existing = get_pr_for_branch(repo_root, head)
            if existing.found and existing.pr is not None:
                log.info("pr.create.already_exists", pr_number=existing.pr.number)
                return {"number": existing.pr.number, "url": existing.pr.url}
        if attempt < max_retries and _is_transient_gh_error(result.stderr):
            attempt += 1
            log.warning(
                "pr.create.retrying",
                attempt=attempt,
                stderr=result.stderr.strip()[:200],
            )
            time.sleep(attempt)
            continue
        raise GhCliError(f"gh pr create failed (exit {result.returncode}): {result.stderr.strip()}")

    # gh pr create prints the PR URL to stdout
    pr_url = result.stdout.strip()

    # Prefer structured info via gh pr view.
    pr_info = _get_pr_info_from_url(repo_root, pr_url)
    if pr_info:
        return pr_info

    # Second fallback: parse the number straight from the returned URL.
    number = _parse_pr_number_from_url(pr_url)
    if number is not None:
        return {"number": number, "url": pr_url}

    # Never fabricate a PR number — surface the failure to the caller (B5).
    log.warning("pr.create.number_undeterminable", url=pr_url)
    return None


_PR_URL_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _parse_pr_number_from_url(pr_url: str) -> int | None:
    """Extract the PR number from a ``.../pull/<n>`` URL, or None."""
    match = _PR_URL_NUMBER_RE.search(pr_url)
    return int(match.group(1)) if match else None


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

    # merge_pr is NOT idempotent — a completed remote merge cannot be re-run.
    # So we do the retry loop here (not inside _run_gh) and, before every retry,
    # re-check the PR state: if it is already MERGED the earlier attempt actually
    # succeeded (its response was just lost to a transient error), so we return
    # success instead of re-attempting the irreversible merge (knowledge
    # b6ca74e5). Transient failures and GitHub's stale-PR-head rejection
    # ("head branch is out of date") are retried; the latter, if it persists,
    # then gets an actionable diagnosis (see _diagnose_stale_pr_head) in place of
    # GitHub's misleading raw message.
    max_retries = 3
    attempt = 0
    while True:
        result = _run_gh(*cmd_args, cwd=repo_root, check=False)
        if result.returncode == 0:
            return
        if _get_pr_state(repo_root, pr_number) == "MERGED":
            log.info("pr.merge.already_merged", pr_number=pr_number)
            return
        stale = _is_stale_pr_head_error(result.stderr)
        if attempt < max_retries and (_is_transient_gh_error(result.stderr) or stale):
            # Bounded fast-retry absorbs a genuine sub-second push→merge race,
            # where GitHub's PR head OID has simply not caught up yet.
            attempt += 1
            log.warning(
                "pr.merge.retrying",
                pr_number=pr_number,
                attempt=attempt,
                stale_head=stale,
                stderr=result.stderr.strip()[:200],
            )
            time.sleep(attempt)
            continue
        if stale:
            diagnosis = _diagnose_stale_pr_head(repo_root, pr_number)
            if diagnosis:
                log.error("pr.merge.stale_head", pr_number=pr_number)
                raise GhCliError(diagnosis)
            # Intentional TOCTOU fallback: if GitHub's async PR sync completes
            # DURING the bounded retries above, _diagnose_stale_pr_head now sees
            # matching OIDs (or genuine branch-behind-base) and returns None, so
            # we fall through to GitHub's raw "try the merge again" message —
            # correct in that instant. Do NOT convert this into an unbounded
            # retry loop.
        raise GhCliError(
            f"gh pr merge {pr_number} failed (exit {result.returncode}): {result.stderr.strip()}"
        )


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


def update_pr_title(repo_root: Path, pr_number: int, title: str) -> bool:
    """Update the title of an existing pull request.

    Used by ``done()`` to sync an open PR's title to a corrected issue title so a
    stale (non-conventional) title reaching the PR before this enforcement can be
    fixed in place — clearing the ``PR Title Lint`` CI check. Mirrors
    :func:`update_pr_body` (``gh pr edit <n> --title``, retry transient failures).

    Args:
        repo_root: Repository root directory.
        pr_number: PR number to update.
        title: New PR title.

    Returns:
        True if the update succeeded, False otherwise.
    """
    result = _run_gh(
        "pr",
        "edit",
        str(pr_number),
        "--title",
        title,
        cwd=repo_root,
        check=False,
        retries=3,
    )
    return result.returncode == 0


# Substrings in ``gh`` stderr that mean "no PR exists for this branch" — a
# normal, non-error result — as opposed to a transient/permanent lookup failure
# (network error, bad auth, rate limit). ``gh pr view <branch>`` exits non-zero
# in BOTH cases, so the message is the only signal that tells them apart.
_NO_PR_SIGNALS = ("no pull requests found", "no open pull requests")


def get_pr_for_branch(repo_root: Path, branch: str) -> PRLookup:
    """Look up the PR associated with *branch*.

    Args:
        repo_root: Repository root directory.
        branch: Branch name to search for.

    Returns:
        A :class:`PRLookup` distinguishing three realities: no PR exists, the
        lookup failed (transient/permanent ``gh`` error), or a PR exists with a
        known ``OPEN`` / ``CLOSED`` / ``MERGED`` state. Callers must check
        :attr:`PRLookup.is_open` before acting on a PR as if it were open, and
        must handle :attr:`PRLookup.lookup_failed` separately from "no PR"
        (retry / report — never assume the PR is absent).
    """
    result = _run_gh(
        "pr",
        "view",
        branch,
        "--json",
        "number,url,title,state,isDraft,baseRefName",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if any(sig in stderr for sig in _NO_PR_SIGNALS):
            return PRLookup(found=False, lookup_failed=False)
        # Non-zero exit without the "no PR" signal is a real lookup failure.
        return PRLookup(found=False, lookup_failed=True)
    try:
        data = json.loads(result.stdout)
        pr = PRRef(
            number=data["number"],
            url=data.get("url", ""),
            title=data.get("title", ""),
            state=data.get("state", ""),
            isDraft=data.get("isDraft", False),
            baseRefName=data.get("baseRefName", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return PRLookup(found=False, lookup_failed=True)
    return PRLookup(found=True, pr=pr)


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
            "number,url,headRefName,state,isDraft,mergedAt,updatedAt",
            cwd=repo_root,
            check=False,
        )
    except GhCliError:
        return []
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            return []
        prs: list[PRSummary] = []
        for row in rows:
            if not isinstance(row, dict):
                return []
            prs.append(PRSummary(**row))
        return prs
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
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

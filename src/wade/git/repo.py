"""Repo introspection via subprocess — is_git_repo, branches, clean checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class GitError(Exception):
    """Raised when a git command fails unexpectedly."""


def _run_git(
    *args: str,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess result.

    Args:
        *args: Git subcommand and arguments (e.g., "rev-parse", "--git-dir").
        cwd: Working directory for the command.
        check: If True, raise GitError on non-zero exit.

    Returns:
        CompletedProcess with captured stdout/stderr.

    Raises:
        GitError: If check is True and the command fails.
    """
    cmd = ["git", *args]
    log.debug("git.run", cmd=cmd, cwd=str(cwd))
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError as exc:
        raise GitError(
            f"git {' '.join(args)} failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    return result


def is_git_repo(path: Path) -> bool:
    """Check whether *path* is inside a git repository.

    Args:
        path: Directory to check.

    Returns:
        True if the path is inside a git repository.
    """
    result = _run_git("rev-parse", "--git-dir", cwd=path, check=False)
    return result.returncode == 0


def is_worktree(path: Path) -> bool:
    """Check whether *path* is inside a git worktree (not the main checkout).

    A linked worktree has a .git *file* (not directory) that points to the
    main repository's worktrees directory.

    Args:
        path: Directory to check.

    Returns:
        True if the path is inside a linked worktree.
    """
    result = _run_git("rev-parse", "--git-common-dir", cwd=path, check=False)
    if result.returncode != 0:
        return False
    common_dir = result.stdout.strip()

    result2 = _run_git("rev-parse", "--git-dir", cwd=path, check=False)
    if result2.returncode != 0:
        return False
    git_dir = result2.stdout.strip()

    # In the main checkout, --git-dir and --git-common-dir resolve to the same
    # directory.  In a linked worktree they differ.
    return Path(common_dir).resolve() != Path(git_dir).resolve()


def get_repo_root(path: Path) -> Path:
    """Return the repository root (top-level working directory).

    Args:
        path: Any directory inside the repo.

    Returns:
        Absolute path to the repo root.

    Raises:
        GitError: If *path* is not inside a git repository.
    """
    result = _run_git("rev-parse", "--show-toplevel", cwd=path)
    return Path(result.stdout.strip())


def get_current_branch(path: Path) -> str:
    """Return the name of the currently checked-out branch.

    Args:
        path: Any directory inside the repo.

    Returns:
        Branch name (e.g., "main", "feat/42-add-auth").

    Raises:
        GitError: If HEAD is detached or not in a repo.
    """
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    branch = result.stdout.strip()
    if branch == "HEAD":
        raise GitError("HEAD is detached — not on any branch")
    return branch


def detect_main_branch(path: Path) -> str:
    """Detect the main branch name — checks for 'main' then 'master'.

    Args:
        path: Any directory inside the repo.

    Returns:
        "main" or "master".

    Raises:
        GitError: If neither main nor master branch exists.
    """
    for candidate in ("main", "master"):
        result = _run_git(
            "rev-parse",
            "--verify",
            f"refs/heads/{candidate}",
            cwd=path,
            check=False,
        )
        if result.returncode == 0:
            log.debug("git.detect_main_branch", branch=candidate)
            return candidate
    raise GitError("Neither 'main' nor 'master' branch found")


def is_clean(path: Path) -> bool:
    """Check whether the working tree has no uncommitted changes.

    This checks both the index (staged) and the working tree (unstaged).

    Args:
        path: Any directory inside the repo.

    Returns:
        True if the working tree is clean.
    """
    result = _run_git("status", "--porcelain", cwd=path)
    return result.stdout.strip() == ""


def get_remote_url(path: Path) -> str | None:
    """Return the URL of the 'origin' remote, or None if not configured.

    Args:
        path: Any directory inside the repo.

    Returns:
        Remote URL string, or None.
    """
    result = _run_git("remote", "get-url", "origin", cwd=path, check=False)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url if url else None


def get_git_dir(path: Path) -> str | None:
    """Return the git directory for *path*, or None on failure.

    Args:
        path: Any directory inside the repo.

    Returns:
        The git directory path string, or None if not in a repo.
    """
    result = _run_git("rev-parse", "--git-dir", cwd=path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def get_dirty_status(path: Path) -> dict[str, int]:
    """Get detailed dirty working tree status.

    Returns a dict with counts for staged, unstaged, and untracked files.
    """
    result = _run_git("status", "--porcelain", cwd=path)
    staged = 0
    unstaged = 0
    untracked = 0
    for line in result.stdout.splitlines():
        if not line or len(line) < 2:
            continue
        index_status = line[0]
        worktree_status = line[1]
        if index_status == "?":
            untracked += 1
        else:
            if index_status not in (" ", "?"):
                staged += 1
            if worktree_status not in (" ", "?"):
                unstaged += 1
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def push_branch(repo_root: Path, branch: str, set_upstream: bool = False) -> None:
    """Push a branch to origin.

    Args:
        repo_root: Repository root directory.
        branch: Branch name to push.
        set_upstream: If True, add ``-u`` to track the remote branch.

    Raises:
        GitError: If the push fails.
    """
    args = ["push"]
    if set_upstream:
        args.extend(["-u", "origin", branch])
    else:
        args.extend(["origin", branch])
    _run_git(*args, cwd=repo_root)


def checkout(repo_root: Path, branch: str) -> None:
    """Check out a branch.

    Args:
        repo_root: Repository root directory.
        branch: Branch name to check out.

    Raises:
        GitError: If the checkout fails.
    """
    _run_git("checkout", branch, cwd=repo_root)


def checkout_detach(repo_root: Path) -> None:
    """Detach HEAD from the current branch.

    Raises:
        GitError: If the detach fails.
    """
    _run_git("checkout", "--detach", cwd=repo_root)


def merge_base(repo_root: Path, ref_a: str, ref_b: str) -> str:
    """Return the merge base commit of two refs.

    Raises:
        GitError: If the merge-base fails.
    """
    result = _run_git("merge-base", ref_a, ref_b, cwd=repo_root)
    return result.stdout.strip()


def rev_parse(repo_root: Path, ref: str) -> str:
    """Resolve a ref to its commit SHA.

    Raises:
        GitError: If the rev-parse fails.
    """
    result = _run_git("rev-parse", ref, cwd=repo_root)
    return result.stdout.strip()


def fetch_ref(repo_root: Path, remote: str, refspec: str) -> None:
    """Fetch a specific refspec from a remote.

    Args:
        repo_root: Repository root directory.
        remote: Remote name (e.g. "origin").
        refspec: Refspec to fetch (e.g. "branch:branch").

    Raises:
        GitError: If the fetch fails.
    """
    _run_git("fetch", remote, refspec, cwd=repo_root)


def has_remote(repo_root: Path) -> bool:
    """Check whether the repo has any configured remotes."""
    result = _run_git("remote", cwd=repo_root, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def diff_stat(repo_root: Path) -> str:
    """Return the ``git diff`` output for the working tree.

    Returns empty string if diff fails or there are no changes.
    """
    result = _run_git("diff", cwd=repo_root, check=False)
    return result.stdout if result.returncode == 0 else ""


def pull_ff_only(repo_root: Path) -> subprocess.CompletedProcess[str]:
    """Pull with fast-forward only.

    Returns the CompletedProcess so callers can inspect stderr on failure.
    Does not raise on failure.
    """
    return _run_git("pull", "--ff-only", "--quiet", cwd=repo_root, check=False)


def merge_squash(repo_root: Path, branch: str) -> None:
    """Squash-merge a branch into the current branch.

    Raises:
        GitError: If the merge fails.
    """
    _run_git("merge", "--squash", branch, cwd=repo_root)


def commit_no_edit(repo_root: Path) -> None:
    """Create a commit using the default merge message.

    Raises:
        GitError: If the commit fails.
    """
    _run_git("commit", "--no-edit", cwd=repo_root)


def push(repo_root: Path) -> None:
    """Push the current branch to origin.

    Raises:
        GitError: If the push fails.
    """
    _run_git("push", cwd=repo_root)


def merge_ff_only(repo_root: Path, ref: str) -> None:
    """Fast-forward merge a ref into the current branch.

    Raises:
        GitError: If the merge fails (not fast-forwardable).
    """
    _run_git("merge", "--ff-only", ref, cwd=repo_root)


def merge_no_edit(repo_root: Path, branch: str) -> None:
    """Merge a branch without opening an editor.

    Raises:
        GitError: If the merge fails.
    """
    _run_git("merge", "--no-edit", branch, cwd=repo_root)


def stash(repo_root: Path) -> subprocess.CompletedProcess[str]:
    """Stash local changes. Returns CompletedProcess (no raise on failure)."""
    return _run_git("stash", "--quiet", cwd=repo_root, check=False)


def stash_pop(repo_root: Path) -> subprocess.CompletedProcess[str]:
    """Pop the top stash entry. Returns CompletedProcess (no raise on failure)."""
    return _run_git("stash", "pop", "--quiet", cwd=repo_root, check=False)


def upstream_tracking_status(repo_root: Path, branch: str) -> str | None:
    """Return the upstream tracking status for a branch.

    Returns the short tracking indicator (e.g. "gone", ">", "<>") or
    None if the branch has no upstream or the check fails.
    """
    result = _run_git(
        "for-each-ref",
        "--format=%(upstream:trackshort)",
        f"refs/heads/{branch}",
        cwd=repo_root,
        check=False,
    )
    status = result.stdout.strip()
    return status if status else None

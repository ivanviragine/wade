"""Branch naming, creation, and comparison utilities."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import uuid
from pathlib import Path

import structlog

from wade.git import repo as git_repo
from wade.git.repo import GitError, _run_git, _run_git_with_retry
from wade.utils.slug import slugify

log = structlog.get_logger(__name__)


def make_branch_name(prefix: str, issue_number: int, title: str) -> str:
    """Build a branch name from a prefix, issue number, and title slug.

    Produces names like ``feat/42-add-user-auth``.

    Args:
        prefix: Branch prefix (e.g., "feat", "fix", "chore").
        issue_number: GitHub issue number.
        title: Human-readable title to slugify.

    Returns:
        A valid git branch name.
    """
    slug = slugify(title, max_length=50)
    return f"{prefix}/{issue_number}-{slug}"


def branch_exists(repo_root: Path, branch_name: str) -> bool:
    """Check whether a local branch exists.

    Args:
        repo_root: Repository root directory.
        branch_name: Name of the branch to check.

    Returns:
        True if the branch exists locally.
    """
    result = _run_git(
        "rev-parse",
        "--verify",
        f"refs/heads/{branch_name}",
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 0


def remote_ref_exists(repo_root: Path, branch_name: str, remote: str = "origin") -> bool:
    """Check whether a remote-tracking ref (``<remote>/<branch>``) exists locally.

    Args:
        repo_root: Repository root directory.
        branch_name: Short branch name (without the remote prefix).
        remote: Remote name (default ``origin``).

    Returns:
        True if ``refs/remotes/<remote>/<branch_name>`` resolves.
    """
    result = _run_git(
        "rev-parse",
        "--verify",
        f"refs/remotes/{remote}/{branch_name}",
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 0


def resolve_start_point(repo_root: Path, base_branch: str) -> str | None:
    """Resolve a base branch to a local commit-ish usable as a branch start point.

    A base declared in a plan (e.g. ``develop``) may exist only as a remote
    tracking ref. ``git branch <new> develop`` will not resolve a remote-only
    ``develop`` (short names resolve local heads, not ``origin/develop``), so this
    prefers the local branch and falls back to ``origin/<base>``.

    Returns:
        ``base_branch`` if a local branch exists, ``origin/<base_branch>`` if only
        the remote ref exists, or ``None`` if neither is found.
    """
    if branch_exists(repo_root, base_branch):
        return base_branch
    if remote_ref_exists(repo_root, base_branch):
        return f"origin/{base_branch}"
    return None


def create_branch(
    repo_root: Path,
    branch_name: str,
    start_point: str = "HEAD",
) -> None:
    """Create a new local branch.

    Args:
        repo_root: Repository root directory.
        branch_name: Name for the new branch.
        start_point: Commit, branch, or tag to base the branch on.

    Raises:
        GitError: If the branch already exists or the start_point is invalid.
    """
    log.info("branch.create", branch=branch_name, start_point=start_point)
    _run_git_with_retry("branch", branch_name, start_point, cwd=repo_root)


def delete_branch(
    repo_root: Path,
    branch_name: str,
    force: bool = False,
) -> None:
    """Delete a local branch.

    Args:
        repo_root: Repository root directory.
        branch_name: Name of the branch to delete.
        force: If True, use -D (force delete even if unmerged).

    Raises:
        GitError: If the branch does not exist or cannot be deleted.
    """
    flag = "-D" if force else "-d"
    log.info("branch.delete", branch=branch_name, force=force)
    # Retry transient ref-lock contention from parallel sessions (C3).
    _run_git_with_retry("branch", flag, branch_name, cwd=repo_root)


def reset_branch(
    repo_root: Path,
    branch_name: str,
    start_point: str,
) -> None:
    """Force-move an existing local branch ref to *start_point* (``git branch -f``).

    Re-roots a branch without a checkout. The branch must not be checked out in any
    worktree (git refuses ``-f`` on a checked-out branch). Used to rebuild a
    scaffold-only branch on a new base before retargeting its PR, so the old base's
    commits do not leak into the new base's diff (#376).

    Args:
        repo_root: Repository root directory.
        branch_name: Existing local branch to move.
        start_point: Commit, branch, or tag to move the branch onto.

    Raises:
        GitError: If the branch is checked out or the move fails.
    """
    log.info("branch.reset", branch=branch_name, start_point=start_point)
    _run_git_with_retry("branch", "-f", branch_name, start_point, cwd=repo_root)


def _reset_inventory(worktree: Path, *args: str) -> tuple[str, ...]:
    """Read a NUL-delimited Git inventory without newline or quoted-path conversion."""
    try:
        result = subprocess.run(["git", *args], cwd=worktree, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitError(f"Cannot inspect reset paths in {worktree}: {exc}") from exc
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise GitError(f"Invalid reset path inventory in {worktree}")
    return tuple(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)


def _reset_collisions(worktree: Path, destination: str) -> tuple[str, ...]:
    """Find occupied untracked/ignored nodes the complete destination would replace.

    Inspect filesystem occupancy rather than Git's ignored/untracked listing, which
    can omit empty directories and collapse nested repositories. Never follow a
    symlink. Resolve actual directory-entry spellings to handle case aliases on
    filesystems that permit them, without conflating distinct hard-linked paths.
    """
    indexed = _reset_inventory(worktree, "ls-files", "-z")
    target = _reset_inventory(worktree, "ls-tree", "-r", "-z", destination)
    # Path equality case-folds on Windows even in case-sensitive directories.
    # Use exact actual spellings for cache and tracked identity on every host.
    directories: dict[str, tuple[Path, ...]] = {}

    def children(directory: Path) -> tuple[Path, ...]:
        key = os.fspath(directory)
        if key not in directories:
            with os.scandir(directory) as entries:
                directories[key] = tuple(sorted(Path(e.path) for e in entries))
        return directories[key]

    def entry(directory: Path, name: str) -> Path | None:
        requested = directory / name
        try:
            info = requested.lstat()
        except FileNotFoundError:
            return None
        entries = children(directory)
        exact = next((path for path in entries if path.name == name), None)
        if exact is not None:
            return exact
        # The requested spelling exists, but isn't the actual entry spelling.
        # This can happen with case/Unicode aliases. Multiple inode matches are
        # ambiguous (hard links); refusing is safer than treating user data as tracked.
        aliases = [
            path
            for path in entries
            if (details := path.lstat()).st_ino == info.st_ino and details.st_dev == info.st_dev
        ]
        if len(aliases) != 1:
            raise OSError(f"Cannot resolve filesystem spelling of {requested}")
        return aliases[0]

    def existing_index_path(name: str) -> Path | None:
        parts = name.split("/")
        node = worktree
        for position, part in enumerate(parts):
            found = entry(node, part)
            if found is None:
                return None
            node = found
            if position < len(parts) - 1 and not stat.S_ISDIR(node.lstat().st_mode):
                return None
        return node

    collisions: set[str] = set()
    try:
        tracked = {
            os.fspath(path) for name in indexed if (path := existing_index_path(name)) is not None
        }

        def inspect_replaced_directory(directory: Path) -> None:
            contents = children(directory)
            if not contents:
                collisions.add(directory.relative_to(worktree).as_posix())
            for child in contents:
                if stat.S_ISDIR(child.lstat().st_mode):
                    inspect_replaced_directory(child)
                elif os.fspath(child) not in tracked:
                    collisions.add(child.relative_to(worktree).as_posix())

        for record in target:
            header, delimiter, name = record.partition("\t")
            mode = header.split(" ", 1)[0]
            if not delimiter or mode not in {"100644", "100755", "120000", "160000"}:
                raise GitError(f"Invalid reset tree inventory in {worktree}")
            node = worktree
            parts = name.split("/")
            for position, part in enumerate(parts):
                found = entry(node, part)
                if found is None:
                    break
                node = found
                if not stat.S_ISDIR(node.lstat().st_mode):
                    if os.fspath(node) not in tracked:
                        collisions.add(node.relative_to(worktree).as_posix())
                    break
                if position == len(parts) - 1 and mode != "160000":
                    # A default (non-recursive) reset leaves submodule directories
                    # and their contents in place. Gitlinks aren't file replacements.
                    inspect_replaced_directory(node)
    except OSError as exc:
        raise GitError(f"Cannot safely inspect reset paths in {worktree}: {exc}") from exc
    return tuple(sorted(collisions))


def reset_worktree_hard(worktree_path: Path, start_point: str) -> None:
    """Reset a checked-out scaffold without overwriting untracked or ignored data.

    Callers must first guard tracked edits. Pin the destination commit and inspect
    the complete tree immediately before resetting. An obstruction or unknown
    inspection result refuses without mutating the branch/index/worktree. This
    also protects the scaffold rollback caller. Explicitly disable recursive
    submodule resets regardless of user configuration: gitlink contents are not
    part of this preflight. Concurrent filesystem writers after the inspection
    are outside its guarantees.
    """
    destination = _run_git(
        "rev-parse", "--verify", "--end-of-options", f"{start_point}^{{commit}}", cwd=worktree_path
    ).stdout.strip()
    collisions = _reset_collisions(worktree_path, destination)
    if collisions:
        paths = ", ".join(repr(path) for path in collisions)
        raise GitError(
            f"Reset in {worktree_path} would overwrite untracked or ignored paths: {paths}. "
            "Preserve or move these paths outside the affected worktree before retrying."
        )
    log.info("branch.reset_worktree_hard", worktree=str(worktree_path), start_point=destination)
    # Do not retry a destructive operation using a stale preflight. A transient
    # lock failure is safe to retry through the guarded operation itself.
    _run_git("reset", "--hard", "--no-recurse-submodules", destination, cwd=worktree_path)


def create_scaffold_commit(
    repo_root: Path,
    branch_name: str,
    message: str,
) -> None:
    """Create an empty commit on a branch without checking it out.

    Uses git plumbing (``commit-tree`` + ``update-ref``) to avoid touching
    the working directory or requiring a checkout.

    Args:
        repo_root: Repository root directory.
        branch_name: Target branch (must already exist locally).
        message: Commit message for the scaffold commit.

    Raises:
        GitError: If the branch does not exist or plumbing commands fail.
    """
    log.info("branch.scaffold_commit", branch=branch_name)
    tree = _run_git("rev-parse", f"{branch_name}^{{tree}}", cwd=repo_root).stdout.strip()
    parent = _run_git("rev-parse", branch_name, cwd=repo_root).stdout.strip()
    commit = _run_git(
        "commit-tree", tree, "-p", parent, "-m", message, cwd=repo_root
    ).stdout.strip()
    _run_git("update-ref", f"refs/heads/{branch_name}", commit, cwd=repo_root)


def list_branch_names(repo_root: Path) -> set[str]:
    """Return the set of all local and remote branch short-names.

    Runs ``git branch`` and ``git branch -r`` with lock-contention retry —
    this is called during batch polling while parallel sessions may hold git
    locks. An empty set means the repo genuinely has no branches; a query
    failure raises ``GitError`` so callers can distinguish "no branch exists"
    from "the branch query failed" (the latter must not be read as
    "not started").

    Args:
        repo_root: Repository root directory.

    Returns:
        Union of local and remote branch short-names (e.g. ``"main"``,
        ``"origin/feat/42-add-auth"``).

    Raises:
        GitError: If either branch listing fails.
    """
    names: set[str] = set()
    for extra_args in (["-r"], []):
        result = _run_git_with_retry(
            "branch", *extra_args, "--format=%(refname:short)", cwd=repo_root
        )
        names.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return names


def is_merged_into(repo_root: Path, branch: str, base: str) -> bool | None:
    """Return whether *branch*'s tip is an ancestor of *base* (i.e. merged in).

    Wraps ``git merge-base --is-ancestor <branch> <base>`` through the git layer
    (consistent process handling + logging) rather than a raw subprocess call.

    Returns:
        - ``True``  — branch is fully contained in base (exit 0): merged.
        - ``False`` — branch is definitively NOT an ancestor of base (exit 1).
        - ``None``  — indeterminate: a ref could not be resolved / git error
          (any other exit). Callers may then try a different base.
    """
    result = _run_git(
        "merge-base",
        "--is-ancestor",
        branch,
        base,
        cwd=repo_root,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def commits_ahead(repo_root: Path, branch: str, base: str) -> int:
    """Count commits on *branch* that are not on *base*.

    Equivalent to ``git rev-list --count base..branch``.

    Args:
        repo_root: Repository root directory.
        branch: The branch to measure.
        base: The reference branch (e.g., "main").

    Returns:
        Number of commits ahead.

    Raises:
        GitError: If either ref is invalid.
    """
    result = _run_git(
        "rev-list",
        "--count",
        f"{base}..{branch}",
        cwd=repo_root,
    )
    return int(result.stdout.strip())


def tip_commit_is_empty(repo_root: Path, ref: str) -> bool | None:
    """Return whether *ref*'s tip commit introduces no changes vs its first parent.

    WADE's scaffold commit is an *empty* commit — :func:`create_scaffold_commit` reuses
    the parent's tree — so an empty tip (tree identical to its first parent's) is the
    reliable signature of a scaffold-only branch. A real one-commit branch (an amended
    scaffold, a squash down to a single commit, or a PR opened outside WADE) changes the
    tree. Callers use this to tell a rerootable scaffold apart from real work a reroot
    would discard when a branch is exactly one commit ahead of its base (#376 review).

    Returns:
        ``True``  — the tip changes nothing (tree == first parent's tree): a scaffold.
        ``False`` — the tip introduces changes, or is a root commit (no parent) which is
                    never WADE's scaffold.
        ``None``  — emptiness could not be determined (ref unresolvable / git error), so
                    callers fail closed rather than reroot an indeterminate branch.
    """
    tip = _run_git(
        "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}", cwd=repo_root, check=False
    )
    if tip.returncode != 0:
        return None
    parent = _run_git(
        "rev-parse", "--verify", "--quiet", f"{ref}~1^{{tree}}", cwd=repo_root, check=False
    )
    if parent.returncode != 0:
        # No first parent → a root commit, which is never WADE's scaffold.
        return False
    return tip.stdout.strip() == parent.stdout.strip()


def resolve_ref_commit(repo_root: Path, ref: str) -> str | None:
    """Resolve one fully qualified ref; only show-ref's no-match status is absence."""
    if not ref.startswith("refs/"):
        raise GitError("Recovery requires a fully qualified ref")
    _run_git("check-ref-format", ref, cwd=repo_root)
    present = _run_git("show-ref", "--verify", "--quiet", "--", ref, cwd=repo_root, check=False)
    if present.returncode == 1:
        return None
    if present.returncode != 0:
        raise GitError(f"Cannot inspect {ref}: {present.stderr.strip()}")
    symbolic = _run_git("symbolic-ref", "--quiet", ref, cwd=repo_root, check=False)
    if symbolic.returncode != 1:
        raise GitError(f"Recovery requires a direct ref, not a symbolic/unreadable ref: {ref}")
    oid = _run_git("rev-parse", "--verify", ref + "^{commit}", cwd=repo_root).stdout.strip()
    if len(oid) not in (40, 64) or any(c not in "0123456789abcdef" for c in oid):
        raise GitError(f"Invalid commit identity for {ref}: {oid!r}")
    return oid


def live_remote_head_exists(repo_root: Path, branch_name: str, remote: str = "origin") -> bool:
    """Query the exact live head, never cached tracking refs or stderr guesses."""
    ref = f"refs/heads/{branch_name}"
    _run_git("check-ref-format", ref, cwd=repo_root)
    result = _run_git("ls-remote", "--exit-code", "--refs", remote, ref, cwd=repo_root, check=False)
    if result.returncode == 2 and not result.stdout.strip():
        return False
    if result.returncode != 0:
        raise GitError(
            f"Live remote query for {ref} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    lines = result.stdout.splitlines()
    fields = lines[0].split("\t") if len(lines) == 1 else []
    if (
        len(fields) != 2
        or fields[1] != ref
        or len(fields[0]) not in (40, 64)
        or any(c not in "0123456789abcdef" for c in fields[0])
    ):
        raise GitError(f"Malformed or ambiguous live remote response for {ref}: {result.stdout!r}")
    return True


def preserve_and_align_branch(
    repo_root: Path, branch_name: str, old_oid: str, selected_oid: str
) -> str | None:
    """Atomically preserve the observed tip and CAS-align a reserved branch.

    Call only after Git has reserved this branch in the new recovery worktree.
    A lock/CAS failure is never retried here: a changed tip needs fresh preparation.
    """
    ref = f"refs/heads/{branch_name}"
    _run_git("check-ref-format", ref, cwd=repo_root)
    for oid in (old_oid, selected_oid):
        if len(oid) not in (40, 64) or any(c not in "0123456789abcdef" for c in oid):
            raise GitError("Recovery requires full commit object identities")
    if old_oid == selected_oid:
        commands = f"start\nverify {ref} {old_oid}\nprepare\ncommit\n"
        backup = None
    else:
        backup = f"refs/wade/recovery/{branch_name}/{old_oid}"
        existing = resolve_ref_commit(repo_root, backup)
        if existing is not None and existing != old_oid:
            raise GitError(f"Recovery ref {backup} does not preserve the expected tip {old_oid}")
        preserve = (
            f"verify {backup} {old_oid}" if existing is not None else f"create {backup} {old_oid}"
        )
        commands = f"start\n{preserve}\nupdate {ref} {selected_oid} {old_oid}\nprepare\ncommit\n"
    _run_git("update-ref", "--no-deref", "--stdin", cwd=repo_root, input_text=commands)
    return backup


def recovery_branch_command(repo_root: Path, backup_ref: str) -> str:
    """Quote a non-forcing command that makes a preserved tip an ordinary branch."""
    name = "recovered-" + backup_ref.rsplit("/", 1)[-1][:12]
    return shlex.join(["git", "-C", str(repo_root), "branch", name, backup_ref])


class ReviewTrackingError(GitError):
    """Snapshot validation/installation failed; never permit local fallback."""


def fetch_review_snapshot(repo_root: Path, branch_name: str, *, remote: str = "origin") -> str:
    """Fetch to an isolated pin, then CAS-install tracking without dereferencing.

    Fetch follows symbolic destination refs, so even a pre-fetch symbolic-ref
    check cannot protect a checked-out branch against an intervening ref swap.
    Only the isolated ref is a fetch destination; the tracking update uses
    expected-old/no-deref semantics and the returned pin never changes with it.
    Verified cleanup may supply its pinned origin URL as ``remote``; review
    recovery retains the ordinary named-origin default.
    """
    tracking = f"refs/remotes/origin/{branch_name}"
    isolated = "refs/wade/review-fetch/" + uuid.uuid4().hex
    try:
        previous = resolve_ref_commit(repo_root, tracking)
        if resolve_ref_commit(repo_root, isolated) is not None:
            raise GitError(f"Isolated review fetch ref already exists: {isolated}")
    except (GitError, OSError) as exc:
        raise ReviewTrackingError(f"Unsafe review tracking destination: {exc}") from exc
    # A unique, non-forcing destination cannot follow the task's tracking symref
    # or discard a stale/divergent local head when the remote was force-pushed.
    # Even explicit refspecs otherwise opportunistically update configured
    # tracking refs; empty refmap plus no tags/pruning confines ref writes to this pin.
    git_repo.fetch_ref(repo_root, remote, f"refs/heads/{branch_name}:{isolated}", isolated=True)
    try:
        selected = resolve_ref_commit(repo_root, isolated)
        if selected is None:
            raise GitError("Successful fetch did not provide its isolated commit pin")
        _run_git(
            "update-ref",
            "--no-deref",
            tracking,
            selected,
            previous if previous is not None else "0" * len(selected),
            cwd=repo_root,
        )
        # Delete only this operation's temporary ref at its captured identity.
        # Any changed ref or installation failure is retained and reported.
        _run_git("update-ref", "--no-deref", "-d", isolated, selected, cwd=repo_root)
    except (GitError, OSError) as exc:
        raise ReviewTrackingError(
            f"Cannot install fetched review snapshot; inspect pin {isolated}: {exc}"
        ) from exc
    return selected

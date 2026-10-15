"""Worktree lifecycle — create, remove, list, prune."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import structlog

from wade.git.repo import GitError, _run_git, _run_git_with_retry
from wade.models import Worktree
from wade.models.review_recovery import (
    RecoveredWorktree,
    RecoveryBlock,
    RecoveryPhase,
)

log = structlog.get_logger(__name__)


def create_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_dir: Path,
    base_branch: str = "main",
) -> Path:
    """Create a new git worktree with a new branch.

    Args:
        repo_root: Root of the main repository checkout.
        branch_name: Name for the new branch.
        worktree_dir: Directory where the worktree will be created.
        base_branch: Branch to base the new branch on.

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.create",
        branch=branch_name,
        worktree=str(worktree_path),
        base=base_branch,
    )
    _run_git_with_retry(
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        base_branch,
        cwd=repo_root,
    )
    return worktree_path


def create_detached_worktree(
    repo_root: Path,
    worktree_dir: Path,
    ref: str = "HEAD",
) -> Path:
    """Create a detached-HEAD worktree at the given ref.

    Args:
        repo_root: Root of the main repository checkout.
        worktree_dir: Directory where the worktree will be created.
        ref: Git ref to check out (default: HEAD).

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.create_detached",
        worktree=str(worktree_path),
        ref=ref,
    )
    _run_git_with_retry(
        "worktree",
        "add",
        "--detach",
        str(worktree_path),
        ref,
        cwd=repo_root,
    )
    return worktree_path


def checkout_existing_branch_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_dir: Path,
) -> Path:
    """Create a worktree that checks out an existing branch.

    Unlike :func:`create_worktree`, this does **not** create a new branch.
    The branch must already exist (locally or as a remote tracking branch).

    Args:
        repo_root: Root of the main repository checkout.
        branch_name: Existing branch to check out.
        worktree_dir: Directory where the worktree will be created.

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created or the branch does not exist.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.checkout_existing",
        branch=branch_name,
        worktree=str(worktree_path),
    )
    _run_git_with_retry(
        "worktree",
        "add",
        str(worktree_path),
        branch_name,
        cwd=repo_root,
    )
    return worktree_path


def remove_worktree(repo_root: Path, worktree_path: Path, force: bool = True) -> None:
    """Remove a linked worktree and clean up its administrative files.

    Args:
        repo_root: Root of the main repository checkout.
        worktree_path: Path to the worktree to remove.
        force: If True (default), use --force flag to remove even if worktree is dirty.

    Raises:
        GitError: If the worktree could not be removed.
    """
    log.info("worktree.remove", worktree=str(worktree_path), force=force)
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    # Retry transient worktree-lock contention from parallel sessions (C3).
    _run_git_with_retry(*args, cwd=repo_root)


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """List all worktrees for a repository.

    Args:
        repo_root: Root of the main repository checkout.

    Returns:
        List of :class:`~wade.models.Worktree` entries with typed ``path``,
        ``head``, and ``branch`` attributes. The branch value is the short ref
        name (e.g., "main") or "(detached)" for detached HEAD worktrees.
    """
    result = _run_git("worktree", "list", "--porcelain", cwd=repo_root)
    worktrees: list[Worktree] = []
    current: Worktree | None = None

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = Worktree(path=line[len("worktree ") :])
        elif current is None:
            # Porcelain output always opens an entry with a "worktree " line;
            # ignore anything before the first one.
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD ") :]
        elif line.startswith("branch "):
            # Refs come as "refs/heads/branch-name"
            current.branch = line[len("branch ") :].removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current.branch = "(detached)"
        elif line.strip() == "":
            worktrees.append(current)
            current = None

    if current is not None:
        worktrees.append(current)

    return worktrees


def prune_worktrees(repo_root: Path) -> None:
    """Prune stale worktree administrative data.

    Removes administrative files for worktrees whose directory no longer
    exists on disk.

    Args:
        repo_root: Root of the main repository checkout.

    Raises:
        GitError: If the prune command fails.
    """
    log.info("worktree.prune")
    _run_git_with_retry("worktree", "prune", cwd=repo_root)


class RecoveryStepError(GitError):
    """A failed guarded recovery retains its newly reserved files and refs."""

    def __init__(
        self,
        detail: str,
        block: RecoveryBlock,
        phase: RecoveryPhase,
        path: Path,
        backup_ref: str | None = None,
    ) -> None:
        self.block = block
        self.phase = phase
        self.path = path
        self.backup_ref = backup_ref
        super().__init__(detail)


def normalized_recovery_path(path: Path) -> Path:
    """Normalize ancestors without following an occupied/dangling target symlink."""
    absolute = Path(os.path.abspath(path))
    return absolute.parent.resolve() / absolute.name


def recover_branch_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_path: Path,
    selected_oid: str,
    *,
    local_only: bool = False,
    progress: Callable[[RecoveryPhase, str | None], None] | None = None,
) -> RecoveredWorktree:
    """Reserve Git ownership, align with a durable CAS backup, then populate safely.

    Existing helpers intentionally keep their original checkout contract. This
    operation never retries a forced checkout, rolls back, or removes partial work.
    """
    from wade.git import branch as git_branch

    path = normalized_recovery_path(worktree_path)
    phase = RecoveryPhase.PREPARING
    block = RecoveryBlock.CREATION
    backup: str | None = None

    def emit(next_phase: RecoveryPhase, recorded_backup: str | None = None) -> None:
        nonlocal phase
        phase = next_phase
        if progress is not None:
            try:
                progress(phase, recorded_backup)
            except (OSError, ValueError) as exc:
                raise RecoveryStepError(
                    f"Cannot persist recovery phase {phase}: {exc}",
                    RecoveryBlock.STATE,
                    phase,
                    path,
                    backup,
                ) from exc

    try:
        _run_git("check-ref-format", "--branch", branch_name, cwd=repo_root)
        if path.exists() or path.is_symlink():
            raise RecoveryStepError(
                f"Recovery destination already exists: {path}",
                RecoveryBlock.DESTINATION,
                phase,
                path,
            )
        owners = [wt.path for wt in list_worktrees(repo_root) if wt.branch == branch_name]
        if owners:
            raise RecoveryStepError(
                f"Branch {branch_name} is already checked out at {', '.join(owners)}",
                RecoveryBlock.OWNERSHIP,
                phase,
                path,
            )
        local_tip = git_branch.resolve_ref_commit(repo_root, f"refs/heads/{branch_name}")
        if local_only and local_tip != selected_oid:
            raise RecoveryStepError(
                "Selected local fallback branch disappeared or changed before reservation",
                RecoveryBlock.VERIFICATION,
                phase,
                path,
            )
        emit(RecoveryPhase.RESERVING)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive mkdir refuses even an empty destination created since preflight.
        try:
            path.mkdir()
        except FileExistsError as exc:
            raise RecoveryStepError(
                f"Recovery destination appeared during reservation: {path}",
                RecoveryBlock.DESTINATION,
                phase,
                path,
            ) from exc
        if local_only:
            _run_git(
                "worktree",
                "add",
                "--detach",
                "--no-checkout",
                "--",
                str(path),
                selected_oid,
                cwd=repo_root,
            )
        elif local_tip is None:
            _run_git(
                "worktree",
                "add",
                "--no-checkout",
                "-b",
                branch_name,
                "--",
                str(path),
                selected_oid,
                cwd=repo_root,
            )
        else:
            _run_git(
                "worktree", "add", "--no-checkout", "--", str(path), branch_name, cwd=repo_root
            )
        emit(RecoveryPhase.RESERVED)
        reserved_owners = [
            normalized_recovery_path(Path(wt.path))
            for wt in list_worktrees(repo_root)
            if wt.branch == branch_name
        ]
        expected_owners = [] if local_only else [path]
        if reserved_owners != expected_owners:
            raise RecoveryStepError(
                f"Branch ownership changed after reservation: {reserved_owners}",
                RecoveryBlock.OWNERSHIP,
                phase,
                path,
            )
        observed_tip = git_branch.resolve_ref_commit(repo_root, f"refs/heads/{branch_name}")
        if observed_tip is None or (local_only and observed_tip != selected_oid):
            raise RecoveryStepError(
                "Selected local branch disappeared or changed after reservation",
                RecoveryBlock.VERIFICATION,
                phase,
                path,
            )
        block = RecoveryBlock.PRESERVATION
        proposed_backup = (
            f"refs/wade/recovery/{branch_name}/{observed_tip}"
            if not local_only and observed_tip != selected_oid
            else None
        )
        emit(RecoveryPhase.ALIGNING, proposed_backup)
        if not local_only:
            backup = git_branch.preserve_and_align_branch(
                repo_root, branch_name, observed_tip, selected_oid
            )
        emit(RecoveryPhase.ALIGNED, backup)
        block = RecoveryBlock.POPULATION
        emit(RecoveryPhase.POPULATING, backup)
        _run_git("read-tree", selected_oid, cwd=path)
        # No force/update flags: tracked-path collisions, including ignored
        # files introduced after reservation, must survive and refuse checkout.
        _run_git("checkout-index", "--all", "--index", cwd=path)
        if local_only:
            block = RecoveryBlock.OWNERSHIP
            emit(RecoveryPhase.ATTACHING)
            if (
                git_branch.resolve_ref_commit(repo_root, f"refs/heads/{branch_name}")
                != selected_oid
            ):
                raise GitError(
                    "Selected local fallback branch disappeared or changed before attachment"
                )
            # The trailing -- prevents a deleted branch being parsed as a path
            # checkout, which could otherwise overwrite matching-path user edits.
            _run_git("checkout", "--no-guess", "--no-overwrite-ignore", branch_name, "--", cwd=path)
        block = RecoveryBlock.VERIFICATION
        emit(RecoveryPhase.VERIFYING, backup)
        selected_tree = _run_git("rev-parse", selected_oid + "^{tree}", cwd=path).stdout.strip()
        if _run_git("write-tree", cwd=path).stdout.strip() != selected_tree:
            raise GitError("Recovered index differs from the selected commit")
        _run_git("diff-files", "--quiet", "--", cwd=path)
        attached = _run_git("symbolic-ref", "--short", "HEAD", cwd=path).stdout.strip()
        head = _run_git("rev-parse", "--verify", "HEAD^{commit}", cwd=path).stdout.strip()
        tip = git_branch.resolve_ref_commit(repo_root, f"refs/heads/{branch_name}")
        if attached != branch_name or head != selected_oid or tip != selected_oid:
            raise GitError("Recovered branch/HEAD changed from the selected commit")
        return RecoveredWorktree(path=path, selected_oid=selected_oid, backup_ref=backup)
    except RecoveryStepError:
        raise
    except (GitError, OSError) as exc:
        raise RecoveryStepError(str(exc), block, phase, path, backup) from exc

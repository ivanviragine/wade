"""Remote-authoritative review recovery and shared incomplete-state gating."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator

from wade.git import branch as git_branch
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.review_recovery import RecoveryPhase, RecoveryRecord
from wade.utils.filelock import file_lock
from wade.utils.safe_state import (
    StateFileError,
    StateFileUnsafeError,
    atomic_write_state_file,
    delete_state_file,
    read_state_file_strict,
)


class RecoveryFailure(StrEnum):
    NO_BRANCH = "no-branch"
    FETCH = "fetch"
    QUERY = "query"
    FETCHED_REF = "fetched-ref"
    INCOMPLETE = "incomplete"
    DESTINATION = "destination"
    OWNERSHIP = "ownership"
    PRESERVATION = "preservation"
    CREATION = "creation"
    POPULATION = "population"
    VERIFICATION = "verification"
    STATE = "state"


class RecoveryOutcome(BaseModel):
    succeeded: bool = False
    path: Path | None = None
    source: Literal["remote", "local"] | None = None
    selected_oid: str | None = None
    backup_ref: str | None = None
    failure: RecoveryFailure | None = None
    detail: str = ""
    marker: Path | None = None

    @model_validator(mode="after")
    def _consistent(self) -> RecoveryOutcome:
        if self.succeeded:
            if self.path is None or self.source is None or self.failure is not None:
                raise ValueError("Successful recovery requires path/source and no failure")
        elif self.failure is None:
            raise ValueError("Failed recovery requires a distinct reason")
        return self


def _state_location(repo_root: Path, target: Path) -> tuple[Path, str]:
    common = git_repo.get_git_common_dir(repo_root)
    if common is None:
        raise GitError("Cannot locate shared Git metadata for review recovery")
    common_root = (repo_root / common).resolve()
    normalized = git_worktree.normalized_recovery_path(target)
    filename = hashlib.sha256(str(normalized).encode()).hexdigest() + ".json"
    return common_root, filename


def _existing_record(
    common: Path, filename: str, target: Path, branch: str
) -> RecoveryOutcome | None:
    marker = common / ".wade" / "review-recovery" / filename
    try:
        raw = read_state_file_strict(common, ("review-recovery",), filename)
        record = RecoveryRecord.model_validate_json(raw)
        if (
            record.target != git_worktree.normalized_recovery_path(target)
            or record.branch != branch
        ):
            raise ValueError("Recovery marker does not match its target/branch")
        detail = f"Incomplete recovery at {record.target}; phase={record.phase.value}."
        if record.error:
            detail += f" Retained error: {record.error}"
        if record.backup_ref:
            detail += f" Recorded recovery ref (verify before use): {record.backup_ref}."
    except StateFileUnsafeError as exc:
        # The strict reader preserves the OS cause. Only actual ENOENT is
        # absence; permission denial, symlinks and unsupported reads must block.
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        detail = f"Cannot trust incomplete recovery marker {marker}: {exc}"
    except (StateFileError, ValidationError, ValueError, OSError) as exc:
        detail = f"Cannot trust incomplete recovery marker {marker}: {exc}"
    return RecoveryOutcome(
        failure=RecoveryFailure.INCOMPLETE,
        path=target,
        marker=marker,
        detail=detail + " Inspect the retained worktree/refs and marker before manual recovery; "
        "do not delete files or retry implementation to bypass it.",
    )


def inspect_incomplete_recovery(
    repo_root: Path, target: Path, branch: str
) -> RecoveryOutcome | None:
    """Existing worktrees must pass this guard before PR checks or quiet success."""
    try:
        common, filename = _state_location(repo_root, target)
        return _existing_record(common, filename, target, branch)
    except (GitError, OSError, ValueError) as exc:
        return RecoveryOutcome(failure=RecoveryFailure.STATE, path=target, detail=str(exc))


def _recover_worktree(repo_root: Path, branch: str, target: Path) -> RecoveryOutcome:
    """Recover exactly one fetched snapshot; only live absence permits local fallback."""
    target = git_worktree.normalized_recovery_path(target)
    try:
        common, filename = _state_location(repo_root, target)
    except (GitError, OSError, ValueError) as exc:
        return RecoveryOutcome(failure=RecoveryFailure.STATE, path=target, detail=str(exc))
    marker = common / ".wade" / "review-recovery" / filename
    with file_lock(marker, create_parent=False, resolve_path=False):
        blocked = _existing_record(common, filename, target, branch)
        if blocked is not None:
            return blocked
        if target.exists() or target.is_symlink():
            return RecoveryOutcome(
                failure=RecoveryFailure.DESTINATION,
                path=target,
                detail=f"Recovery destination already exists: {target}; preserve its contents.",
            )
        try:
            owners = [
                wt.path for wt in git_worktree.list_worktrees(repo_root) if wt.branch == branch
            ]
            if owners:
                return RecoveryOutcome(
                    failure=RecoveryFailure.OWNERSHIP,
                    path=target,
                    detail=f"Branch {branch} is already checked out at {', '.join(owners)}",
                )
        except (GitError, OSError) as exc:
            return RecoveryOutcome(failure=RecoveryFailure.OWNERSHIP, path=target, detail=str(exc))
        source: Literal["remote", "local"] = "remote"
        fetch_error = ""
        selected: str | None = None
        try:
            selected = git_branch.fetch_review_snapshot(repo_root, branch)
        except git_branch.ReviewTrackingError as exc:
            return RecoveryOutcome(
                failure=RecoveryFailure.FETCHED_REF, path=target, detail=str(exc)
            )
        except (GitError, OSError) as exc:
            fetch_error = str(exc)
            try:
                present = git_branch.live_remote_head_exists(repo_root, branch)
            except (GitError, OSError) as query_error:
                return RecoveryOutcome(
                    failure=RecoveryFailure.QUERY,
                    path=target,
                    detail=f"Fetch failed: {fetch_error}; live remote query failed: {query_error}",
                )
            if present:
                return RecoveryOutcome(
                    failure=RecoveryFailure.FETCH,
                    path=target,
                    detail=f"Live remote head exists, but fetch failed: {fetch_error}",
                )
            source = "local"
        if source == "local":
            try:
                selected = git_branch.resolve_ref_commit(repo_root, f"refs/heads/{branch}")
            except (GitError, OSError) as exc:
                return RecoveryOutcome(
                    failure=RecoveryFailure.FETCHED_REF, path=target, detail=str(exc)
                )
        if selected is None:
            return RecoveryOutcome(
                failure=RecoveryFailure.NO_BRANCH
                if source == "local"
                else RecoveryFailure.FETCHED_REF,
                path=target,
                detail=(
                    f"Live remote head and local branch are absent. Fetch context: {fetch_error}"
                    if source == "local"
                    else "Successful fetch did not provide a pinned commit"
                ),
            )
        record = RecoveryRecord(branch=branch, target=target, selected_oid=selected)

        def save() -> None:
            if not atomic_write_state_file(
                common, ("review-recovery",), filename, record.model_dump_json().encode()
            ):
                raise ValueError(f"Cannot persist incomplete recovery marker {marker}")

        def progress(phase: RecoveryPhase, backup_ref: str | None) -> None:
            nonlocal record
            record = record.model_copy(update={"phase": phase, "backup_ref": backup_ref})
            save()

        try:
            save()  # Must become durable before Git reserves any worktree.
            result = git_worktree.recover_branch_worktree(
                repo_root, branch, target, selected, local_only=source == "local", progress=progress
            )
            if not delete_state_file(common, ("review-recovery",), filename):
                raise ValueError(f"Cannot clear verified recovery marker {marker}")
        except git_worktree.RecoveryStepError as exc:
            record = record.model_copy(update={"error": str(exc)})
            detail = str(exc)
            try:
                save()
            except (OSError, ValueError) as persist_error:
                # Keep the original durable marker and expose the secondary
                # failure; it must continue to block an ordinary retry.
                detail += f"; cannot persist failure detail: {persist_error}"
            return RecoveryOutcome(
                failure=RecoveryFailure(exc.block.value),
                path=target,
                marker=marker,
                selected_oid=selected,
                backup_ref=exc.backup_ref,
                detail=detail,
            )
        except (OSError, ValueError) as exc:
            return RecoveryOutcome(
                failure=RecoveryFailure.STATE,
                path=target,
                marker=marker,
                selected_oid=selected,
                backup_ref=record.backup_ref,
                detail=str(exc),
            )
        return RecoveryOutcome(
            succeeded=True,
            path=result.path,
            selected_oid=selected,
            backup_ref=result.backup_ref,
            source=source,
            detail=(
                f"Using local (possibly stale) branch; it may not match the PR head. "
                f"Fetch context: {fetch_error}"
                if source == "local"
                else ""
            ),
        )


def recover_worktree(repo_root: Path, branch: str, target: Path) -> RecoveryOutcome:
    """Return structured state/locking failures without hiding partial recovery."""
    try:
        return _recover_worktree(repo_root, branch, target)
    except (OSError, ValueError) as exc:
        return RecoveryOutcome(failure=RecoveryFailure.STATE, path=target, detail=str(exc))

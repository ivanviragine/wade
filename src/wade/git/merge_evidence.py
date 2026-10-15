"""Pinned Git proof shared by worktree listing and removal."""

from __future__ import annotations

from pathlib import Path

from wade.git import pr as git_pr
from wade.git.repo import GitError, _run_git, fetch_ref
from wade.models.merge_evidence import MergeAssessment, MergeAssessmentCache, MergeEvidenceState


def commit_oid(root: Path, ref: str) -> str:
    """Read a real commit, without replacement-object interpretation."""
    return _run_git(
        "--no-replace-objects", "rev-parse", "--verify", f"{ref}^{{commit}}", cwd=root
    ).stdout.strip()


def _contained(root: Path, head: str, base: str) -> bool:
    result = _run_git(
        "--no-replace-objects", "merge-base", "--is-ancestor", head, base, cwd=root, check=False
    )
    if result.returncode not in {0, 1}:
        raise GitError(f"could not verify ancestry: {result.stderr.strip()}")
    return result.returncode == 0


def assess_branch(
    root: Path, branch: str, base: str, cache: MergeAssessmentCache | None = None
) -> MergeAssessment:
    """Accept local ancestry offline, otherwise require exact merged-PR proof.

    Network proof always refreshes the explicitly selected base, into only its
    remote-tracking ref, ignoring narrow/misconfigured default fetch mappings.
    Cache lifetime is the owning service operation; callers still revalidate
    mutable branch/HEAD/cleanliness before removal.
    """
    result = MergeAssessment(
        state=MergeEvidenceState.UNKNOWN, reason="could not verify branch", branch=branch
    )
    try:
        _run_git("check-ref-format", f"refs/heads/{base}", cwd=root)
        _run_git("check-ref-format", f"refs/heads/{branch}", cwd=root)
        result.head = commit_oid(root, f"refs/heads/{branch}")
        if cache and branch in cache.entries:
            previous = cache.entries[branch]
            if previous.head == result.head:
                return previous
        # Either actual local base can prove containment; cached remote history
        # is sufficient for ancestry but never substitutes for a PR-path fetch.
        for ref in (f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
            try:
                oid = commit_oid(root, ref)
            except GitError:
                continue
            if _contained(root, result.head, oid):
                result.state = MergeEvidenceState.VERIFIED
                result.reason = "branch is contained in locally available base history"
                result.base_ref, result.base_commit = ref, oid
                break
        else:
            repository = git_pr.origin_github_repository(root)
            result.origin_url = repository.origin_url
            lookup = git_pr.find_exact_merged_pr(root, repository, branch, result.head, base)
            result.state, result.reason = lookup.state, lookup.reason
            if lookup.state == MergeEvidenceState.VERIFIED and lookup.pr is not None:
                result.pr = lookup.pr
                ref = f"refs/remotes/origin/{base}"
                # Fetch the pinned configured URL, never a mutable local head.
                fetch_ref(root, repository.origin_url, f"+refs/heads/{base}:{ref}", isolated=True)
                result.base_ref, result.base_commit = ref, commit_oid(root, ref)
                merge = commit_oid(root, lookup.pr.merge_commit)
                if not _contained(root, merge, result.base_commit):
                    result.state = MergeEvidenceState.NOT_MATCHED
                    result.reason = "recorded PR merge is absent from freshly fetched base history"
                else:
                    result.reason = (
                        f"exact final head of merged PR #{lookup.pr.number} "
                        f"verified in fresh origin/{base}"
                    )
    except (GitError, ValueError) as exc:
        result.state = MergeEvidenceState.UNKNOWN
        result.reason = f"could not verify merged history: {exc}"
    if cache is not None:
        cache.entries[branch] = result
    return result


def revalidate_assessment(root: Path, assessment: MergeAssessment) -> None:
    """Reject changed branch/base/origin bindings instead of reusing stale proof."""
    if commit_oid(root, f"refs/heads/{assessment.branch}") != assessment.head:
        raise GitError("local branch head changed during cleanup")
    if assessment.base_ref and commit_oid(root, assessment.base_ref) != assessment.base_commit:
        raise GitError("target base changed during cleanup; retry with fresh evidence")
    if (
        assessment.origin_url
        and git_pr.origin_github_repository(root).origin_url != assessment.origin_url
    ):
        raise GitError("origin repository changed during cleanup")

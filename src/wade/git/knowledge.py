"""Deterministic, literal-path Git queries for knowledge evidence snapshots."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from wade.git.repo import GitError, get_git_common_dir
from wade.models.knowledge import GitBlobSnapshot, GitFileEvidence


def _run_git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Read locally available objects only, without index refresh or lazy fetch."""
    env = os.environ.copy()
    env.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise GitError(result.stderr.strip() or "Local knowledge Git query failed")
    return result


def resolve_commit(root: Path, ref: str) -> str:
    if not ref or "\0" in ref or ref.startswith("-"):
        raise ValueError("Invalid evidence tree reference")
    result = _run_git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}", cwd=root)
    commit = result.stdout.strip()
    if len(commit) not in {40, 64} or any(c not in "0123456789abcdef" for c in commit):
        raise GitError("Git did not resolve a full commit object ID")
    return commit


def resolve_completion_base(root: Path, logical_base: str) -> str:
    """Snapshot the same remote-first local refs used by completion's sync probe."""
    for ref in (f"origin/{logical_base}", logical_base):
        try:
            return resolve_commit(root, ref)
        except GitError:
            continue
    raise GitError(f"Completion base {logical_base!r} is not locally available")


def tree_blob(root: Path, commit: str, path: str) -> GitBlobSnapshot | None:
    # Validate path independently of whether it exists. No magic pathspecs/options.
    GitFileEvidence(path=path, checked_commit="0" * 40, blob="0" * 40)
    result = _run_git("--literal-pathspecs", "ls-tree", "-z", commit, "--", path, cwd=root)
    matches = [piece for piece in result.stdout.split("\0") if piece]
    if not matches:
        return None
    if len(matches) != 1:
        raise GitError("Literal evidence path resolved multiple Git entries")
    metadata, filename = matches[0].split("\t", 1)
    mode, kind, oid = metadata.split(" ", 2)
    if filename != path or kind != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(
            f"Evidence path {path!r} must be an ordinary tracked blob, "
            "not a symlink/submodule/directory"
        )
    return GitBlobSnapshot(path=path, object_id=oid, mode=mode)


def read_tree_text(root: Path, commit: str, path: str) -> str | None:
    blob = tree_blob(root, commit, path)
    if blob is None:
        return None
    try:
        return _run_git("cat-file", "blob", blob.object_id, cwd=root).stdout
    except UnicodeError as exc:
        raise ValueError(f"Knowledge/evidence text {path!r} is not decodable UTF-8") from exc


def assert_source_clean(root: Path, path: str) -> None:
    # Git diff intentionally ignores these per-path index flags. Generated
    # pointers may legitimately use them, but a flagged evidence path cannot
    # establish ordinary committed-source proof. Other paths remain usable.
    records = _run_git("--literal-pathspecs", "ls-files", "-v", "-z", "--", path, cwd=root)
    if any(
        record and (record[0] == "S" or record[0].islower())
        for record in records.stdout.split("\0")
    ):
        raise ValueError(
            f"Referenced source {path!r} has skip-worktree/assume-unchanged index flags; "
            "hidden working edits cannot establish committed proof"
        )
    for flags in (("--cached", "HEAD"), ()):
        result = _run_git(
            "--literal-pathspecs", "diff", "--quiet", *flags, "--", path, cwd=root, check=False
        )
        if result.returncode == 1:
            raise ValueError(f"Referenced source {path!r} is dirty or staged differently from HEAD")
        if result.returncode != 0:
            raise GitError(f"Could not establish source cleanliness: {result.stderr}")


def file_evidence(
    root: Path, head: str, path: str, start: int | None = None, end: int | None = None
) -> GitFileEvidence:
    blob = tree_blob(root, head, path)
    if blob is None:
        raise ValueError(f"Referenced source {path!r} is missing or untracked at HEAD")
    _run_git("cat-file", "-e", blob.object_id, cwd=root)
    assert_source_clean(root, path)
    evidence = GitFileEvidence(
        path=path, checked_commit=head, blob=blob.object_id, start_line=start, end_line=end
    )
    if start is not None:
        content = read_tree_text(root, head, path)
        assert content is not None
        if end is None or end > len(content.splitlines()):
            raise ValueError(f"Evidence line range exceeds committed source {path!r}")
    return evidence


def parse_file_spec(spec: str) -> tuple[str, int | None, int | None]:
    """Parse the explicit #Lx-Ly suffix without interpreting pathspec syntax."""
    import re

    match = re.fullmatch(r"(.*)#L([1-9][0-9]*)-L?([1-9][0-9]*)", spec)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    if "#L" in spec:
        raise ValueError("File ranges use <literal-path>#Lx-Ly")
    return spec, None, None


def changed_paths(root: Path, base: str, target: str) -> set[str]:
    result = _run_git("diff", "--name-only", "-z", base, target, "--", cwd=root)
    return set(result.stdout.split("\0")) - {""}


def local_changes(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    result = _run_git("--literal-pathspecs", "status", "--porcelain", "--", *paths, cwd=root)
    return tuple(result.stdout.splitlines())


def common_directory(root: Path) -> Path:
    raw = get_git_common_dir(root)
    if raw is None:
        raise GitError("Knowledge ownership requires an available Git common directory")
    return (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()


def repository_identity(root: Path) -> str:
    # Distinct local repositories/clones cannot accept one another's proposals.
    return "sha256:" + hashlib.sha256(str(common_directory(root)).encode()).hexdigest()

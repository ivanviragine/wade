"""Invocation-local Git auto-commits that preserve the user's real index.

Leaf utility: no WADE imports. The same file runs the temporary native hook
proxy, so installed hooks/configuration never need to be rewritten.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class CloseCommitError(Exception):
    """Eligibility or candidate-tree safety could not be proven."""


class CloseCommitBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path
    path: Path
    relative_path: str
    head: str
    ref: str
    mode: str
    blob: str
    index_entry: bytes
    flags: bytes


class CloseCommitEligibility(BaseModel):
    baseline: CloseCommitBaseline | None = None
    reason: str = ""


class CloseCommitOutcome(BaseModel):
    attempted: bool = False
    committed: bool = False
    reason: str = ""


class _HookState(BaseModel):
    baseline: CloseCommitBaseline
    expected_blob: str
    original_hooks: Path


def _git(
    root: Path, *args: str, data: bytes | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(["git", *args], cwd=root, input=data, capture_output=True)
    if check and result.returncode:
        raise CloseCommitError(result.stderr.decode("utf-8", errors="replace").strip()[:2000])
    return result


def _oid(data: bytes) -> str:
    value = data.strip()
    if not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        raise CloseCommitError("Git returned an invalid object ID")
    return value.decode("ascii")


def _index_entry(root: Path, relative: str) -> tuple[str, str, bytes, bytes]:
    entry = _git(root, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", relative).stdout
    records = entry.rstrip(b"\0").split(b"\0")
    if len(records) != 1 or b"\t" not in records[0]:
        raise CloseCommitError("task file is untracked or has unresolved index entries")
    header, path = records[0].split(b"\t", 1)
    fields = header.split()
    if len(fields) != 3 or fields[2] != b"0" or os.fsdecode(path) != relative:
        raise CloseCommitError("task file does not have one ordinary stage-0 index entry")
    mode, blob = fields[:2]
    if mode not in {b"100644", b"100755"}:
        raise CloseCommitError("task file is not a tracked regular file")
    flags = _git(root, "--literal-pathspecs", "ls-files", "-v", "-z", "--", relative).stdout
    if flags != b"H " + os.fsencode(relative) + b"\0":
        raise CloseCommitError("task file has hidden or unsupported index flags")
    return mode.decode("ascii"), _oid(blob), entry, flags


def _tree_entry(root: Path, commit: str, relative: str) -> tuple[str, str]:
    output = _git(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", relative).stdout
    records = output.rstrip(b"\0").split(b"\0")
    if len(records) != 1 or b"\t" not in records[0]:
        raise CloseCommitError("task file is not present in the expected tree")
    header, path = records[0].split(b"\t", 1)
    fields = header.split()
    if len(fields) != 3 or fields[1] != b"blob" or os.fsdecode(path) != relative:
        raise CloseCommitError("task tree entry could not be proven")
    return fields[0].decode("ascii"), _oid(fields[2])


def _ref(root: Path) -> str:
    result = _git(root, "symbolic-ref", "--quiet", "HEAD", check=False)
    return os.fsdecode(result.stdout.removesuffix(b"\n")) if result.returncode == 0 else "HEAD"


def _check_operation(root: Path) -> None:
    for marker in (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
        "sequencer",
        "index.lock",
    ):
        raw = _git(root, "rev-parse", "--git-path", marker).stdout.removesuffix(b"\n")
        path = Path(os.fsdecode(raw))
        if not path.is_absolute():
            path = root / path
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise CloseCommitError(f"repository operation or lock is active ({marker})")


def _file_mode(path: Path) -> str:
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise CloseCommitError("task file is not a regular filesystem file")
    return "100755" if mode & 0o111 else "100644"


def inspect_close_commit(path: Path, original: bytes) -> CloseCommitEligibility:
    """Capture a proven clean task baseline without writing the index."""
    try:
        mode = _file_mode(path)
        root = Path(
            os.fsdecode(
                _git(path.parent, "rev-parse", "--show-toplevel").stdout.removesuffix(b"\n")
            )
        )
        relative = path.relative_to(root).as_posix()
        _check_operation(root)
        index_mode, blob, entry, flags = _index_entry(root, relative)
        head = _oid(_git(root, "rev-parse", "--verify", "HEAD").stdout)
        if _tree_entry(root, head, relative) != (index_mode, blob):
            raise CloseCommitError("task file has preexisting staged edits")
        if mode != index_mode or path.read_bytes() != original:
            raise CloseCommitError("task file content or mode changed during inspection")
        working_blob = _oid(
            _git(root, "hash-object", f"--path={relative}", "--stdin", data=original).stdout
        )
        if working_blob != blob:
            raise CloseCommitError("task file has preexisting unstaged edits")
        return CloseCommitEligibility(
            baseline=CloseCommitBaseline(
                root=root,
                path=path,
                relative_path=relative,
                head=head,
                ref=_ref(root),
                mode=index_mode,
                blob=blob,
                index_entry=entry,
                flags=flags,
            )
        )
    except (OSError, ValueError, CloseCommitError) as exc:
        return CloseCommitEligibility(reason=str(exc))


def _check_current(baseline: CloseCommitBaseline, closed: bytes) -> None:
    _check_operation(baseline.root)
    if (
        _oid(_git(baseline.root, "rev-parse", "--verify", "HEAD").stdout) != baseline.head
        or _ref(baseline.root) != baseline.ref
    ):
        raise CloseCommitError("HEAD changed before auto-commit")
    mode, blob, entry, flags = _index_entry(baseline.root, baseline.relative_path)
    if (mode, blob, entry, flags) != (
        baseline.mode,
        baseline.blob,
        baseline.index_entry,
        baseline.flags,
    ):
        raise CloseCommitError("task index entry changed before auto-commit")
    if _file_mode(baseline.path) != baseline.mode or baseline.path.read_bytes() != closed:
        raise CloseCommitError("task file changed before auto-commit")


def _validate_candidate(state: _HookState, candidate: str) -> None:
    baseline = state.baseline
    header = _git(baseline.root, "cat-file", "-p", candidate).stdout.split(b"\n\n", 1)[0]
    parents = [line[7:] for line in header.splitlines() if line.startswith(b"parent ")]
    if parents != [baseline.head.encode("ascii")]:
        raise CloseCommitError("candidate parent differs from the captured HEAD")
    paths = _git(
        baseline.root,
        "diff-tree",
        "--no-commit-id",
        "--no-renames",
        "-r",
        "--name-only",
        "-z",
        baseline.head,
        candidate,
    ).stdout
    if paths != os.fsencode(baseline.relative_path) + b"\0":
        raise CloseCommitError("hook expanded the candidate beyond the close path")
    if _tree_entry(baseline.root, candidate, baseline.relative_path) != (
        baseline.mode,
        state.expected_blob,
    ):
        raise CloseCommitError("hook changed the expected close blob or file mode")


def _reference_guard(state: _HookState, data: bytes) -> None:
    for line in data.splitlines():
        fields = line.split(b" ", 2)
        if len(fields) != 3:
            raise CloseCommitError("invalid reference transaction")
        old, new, raw_ref = fields
        ref = os.fsdecode(raw_ref)
        if ref == "AUTO_MERGE" and new and set(new) == {ord("0")}:
            continue
        if ref not in {state.baseline.ref, "HEAD"} or _oid(old) != state.baseline.head:
            raise CloseCommitError("unexpected reference update during auto-commit")
        _validate_candidate(state, _oid(new))


def _hook_main(arguments: list[str]) -> int:
    try:
        if len(arguments) < 3 or arguments[0] != "hook":
            raise CloseCommitError("invalid hook invocation")
        state_path = Path(arguments[1])
        hook = arguments[2]
        args = arguments[3:]
        state = _HookState.model_validate_json(state_path.read_text())
        data = sys.stdin.buffer.read()
        # Native hook dispatch retains Git's shebang/fallback execution semantics.
        # --to-stdin is necessary: `git hook run` otherwise supplies /dev/null.
        descriptor, input_path = tempfile.mkstemp(dir=state_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            original = _git(
                state.baseline.root,
                "-c",
                f"core.hooksPath={state.original_hooks}",
                "hook",
                "run",
                "--ignore-missing",
                f"--to-stdin={input_path}",
                hook,
                "--",
                *args,
                check=False,
            )
        finally:
            Path(input_path).unlink(missing_ok=True)
        sys.stdout.buffer.write(original.stdout)
        sys.stderr.buffer.write(original.stderr)
        if original.returncode:
            return original.returncode
        if hook == "reference-transaction" and args == ["prepared"]:
            _reference_guard(state, data)
        return 0
    except Exception as exc:
        sys.stderr.write(f"WADE auto-commit guard refused: {exc}\n")
        return 1


def commit_close(
    eligibility: CloseCommitEligibility, closed: bytes, task_id: str
) -> CloseCommitOutcome:
    """Commit a close-only candidate after hooks, without staging unrelated work."""
    baseline = eligibility.baseline
    if baseline is None:
        return CloseCommitOutcome(
            reason=eligibility.reason or "clean task eligibility was not proven"
        )
    attempted = False
    try:
        _check_current(baseline, closed)
        version = _git(baseline.root, "version").stdout
        match = re.search(rb"git version (\d+)\.(\d+)", version)
        help_result = _git(baseline.root, "hook", "run", "-h", check=False)
        if (
            match is None
            or tuple(map(int, match.groups())) < (2, 36)
            or b"--to-stdin" not in help_result.stdout + help_result.stderr
        ):
            raise CloseCommitError("Git hook proxy/ref-transaction support could not be proven")
        expected = _oid(
            _git(
                baseline.root,
                "hash-object",
                f"--path={baseline.relative_path}",
                "--stdin",
                data=closed,
            ).stdout
        )
        hooks = Path(
            os.fsdecode(
                _git(
                    baseline.root, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
                ).stdout.removesuffix(b"\n")
            )
        )
        try:
            names = {
                path.name for path in hooks.iterdir() if path.is_file() and os.access(path, os.X_OK)
            }
        except (FileNotFoundError, NotADirectoryError):
            names = set()
        names.add("reference-transaction")
        with tempfile.TemporaryDirectory(prefix="wade-close-hooks-") as directory:
            proxy = Path(directory) / "hooks"
            proxy.mkdir()
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                _HookState(
                    baseline=baseline, expected_blob=expected, original_hooks=hooks
                ).model_dump_json()
            )
            helper = Path(__file__).resolve()
            for name in names:
                hook_path = proxy / name
                command = shlex.join([sys.executable, str(helper), "hook", str(state_path), name])
                hook_path.write_text(f'#!/bin/sh\nexec {command} "$@"\n')
                hook_path.chmod(0o700)
            _check_current(baseline, closed)
            attempted = True
            result = _git(
                baseline.root,
                "--literal-pathspecs",
                "-c",
                f"core.hooksPath={proxy}",
                "commit",
                "--only",
                "-m",
                f"chore: close #{task_id}",
                "--",
                baseline.relative_path,
                check=False,
            )
        if result.returncode:
            return CloseCommitOutcome(
                attempted=True,
                reason=result.stderr.decode("utf-8", errors="replace").strip()[:2000],
            )
        return CloseCommitOutcome(attempted=True, committed=True)
    except (OSError, ValueError, CloseCommitError) as exc:
        return CloseCommitOutcome(attempted=attempted, reason=str(exc))


if __name__ == "__main__":
    raise SystemExit(_hook_main(sys.argv[1:]))

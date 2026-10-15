"""Exact live-head query errors must never become permission for local fallback."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from wade.git.branch import live_remote_head_exists
from wade.git.repo import GitError


@pytest.mark.parametrize(
    ("status", "output", "expected"),
    [
        (2, "", False),
        (0, "a" * 40 + "\trefs/heads/topic\n", True),
        (0, "a" * 64 + "\trefs/heads/topic\n", True),
        (128, "", None),
        (1, "", None),
        (2, "unexpected output", None),
        (0, "", None),
        (0, "a" * 40 + "\trefs/heads/other\n", None),
        (0, "a" * 40 + "\trefs/heads/topic\n" + "b" * 40 + "\trefs/heads/topic\n", None),
        (0, "not-an-oid\trefs/heads/topic\n", None),
    ],
)
def test_live_head_probe_classifies_only_exact_response(
    tmp_path: Path, status: int, output: str, expected: bool | None
) -> None:
    def git(*args: str, **kwargs: object) -> CompletedProcess[str]:
        if args[0] == "check-ref-format":
            return CompletedProcess(args, 0, "", "")
        assert args == ("ls-remote", "--exit-code", "--refs", "origin", "refs/heads/topic")
        assert kwargs == {"cwd": tmp_path, "check": False}
        return CompletedProcess(args, status, output, "transport detail")

    with patch("wade.git.branch._run_git", side_effect=git):
        if expected is None:
            with pytest.raises(GitError):
                live_remote_head_exists(tmp_path, "topic")
        else:
            assert live_remote_head_exists(tmp_path, "topic") is expected

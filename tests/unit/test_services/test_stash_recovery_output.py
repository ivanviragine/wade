"""Recovery output is literal shell text rather than wrapped Rich prose."""

from __future__ import annotations

import importlib
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git import GitError
from wade.services.implementation_service import lifecycle
from wade.ui import console

sync = importlib.import_module("wade.services.implementation_service.sync")

SHA = "1234567890abcdef1234567890abcdef12345678"


@pytest.fixture(params=[40, 80])
def narrow_console(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> int:
    width = int(request.param)
    monkeypatch.setattr(console.out, "_width", width)
    monkeypatch.setattr(console.err, "_width", width)
    return width


@pytest.mark.parametrize("outcome", ["retained", "conflict", "exception"])
def test_post_merge_commands_have_exact_physical_lines(
    tmp_path: Path,
    narrow_console: int,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    root = tmp_path / ("long path " * 12 + "owner's [red] [/] ; $(touch sentinel)")
    command = f"git -C {shlex.quote(str(root))}"
    result = subprocess.CompletedProcess([], 0 if outcome == "retained" else 1, "", "conflict [/]!")
    with (
        patch.object(
            lifecycle.git_stash,
            "apply_stash_by_sha",
            return_value=result,
            side_effect=GitError("missing object [/]!") if outcome == "exception" else None,
        ) as apply,
        patch.object(lifecycle.git_stash, "drop_stash_by_sha") as drop,
        patch.object(lifecycle.git_repo, "stash_pop") as pop,
    ):
        lifecycle._restore_stash_or_warn(root, SHA, "owner's [/] stash")
    output = capsys.readouterr()
    commands = [line for line in output.out.splitlines() if line.startswith("git ")]
    expected = [f"{command} stash list --format='%H %gd %gs'"]
    if outcome != "retained":
        expected = [f"{command} status", f"{command} stash apply {SHA}", *expected]
    assert commands == expected
    expected_argv = [["git", "-C", str(root), "stash", "list", "--format=%H %gd %gs"]]
    if outcome != "retained":
        expected_argv = [
            ["git", "-C", str(root), "status"],
            ["git", "-C", str(root), "stash", "apply", SHA],
            *expected_argv,
        ]
    assert [shlex.split(line) for line in commands] == expected_argv
    assert "No stash was dropped" in " ".join(output.out.split()) or outcome == "retained"
    assert "[/]" in output.err
    apply.assert_called_once_with(SHA, root)
    drop.assert_not_called()
    pop.assert_not_called()


def test_uncertain_creation_emits_only_inspection_command(
    tmp_path: Path, narrow_console: int, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / ("long path " * 12 + "owner's [red] ; $(touch sentinel)")
    failure = subprocess.CompletedProcess([], 1, "", lifecycle._LOCAL_CHANGES_MARKER)
    with (
        patch.object(lifecycle.git_repo, "main_checkout_root", return_value=root),
        patch.object(lifecycle.git_repo, "pull_ff_only", return_value=failure),
        patch.object(lifecycle.git_repo, "get_current_branch", return_value="main"),
        patch.object(
            lifecycle.git_stash,
            "create_named_stash",
            side_effect=GitError("identity [/]"),
        ),
        patch.object(lifecycle.git_stash, "apply_stash_by_sha") as apply,
    ):
        lifecycle._pull_main_after_merge(root)
    output = capsys.readouterr()
    commands = [line for line in output.out.splitlines() if line.startswith("git ")]
    assert len(commands) == 1
    assert shlex.split(commands[0]) == ["git", "-C", str(root), "stash", "list", "--format=%H %gs"]
    apply.assert_not_called()


@pytest.mark.parametrize("restored", [False, True])
def test_sync_text_commands_and_machine_event_contract(
    tmp_path: Path,
    narrow_console: int,
    capsys: pytest.CaptureFixture[str],
    restored: bool,
) -> None:
    from wade.models.session import SyncEventType

    root = tmp_path / ("long path " * 12 + "owner's [red] ; $(touch sentinel)")
    events: list[tuple[SyncEventType, dict[str, str]]] = []

    def emit(event: SyncEventType, **data: str) -> None:
        events.append((event, data))

    with (
        patch.object(
            sync.git_stash,
            "apply_stash_by_sha",
            return_value=subprocess.CompletedProcess([], 0 if restored else 1, "", "conflict"),
        ),
        patch.object(sync.git_stash, "drop_stash_by_sha", return_value=False),
    ):
        assert sync._handle_stash_restoration(SHA, root, True, emit, False) is restored
    output = capsys.readouterr()
    commands = [line for line in output.out.splitlines() if line.startswith("git ")]
    if restored:
        assert [shlex.split(line) for line in commands] == [
            ["git", "-C", str(root), "stash", "list", "--format=%H %gd %gs"]
        ]
        assert events == [(SyncEventType.STASH_RESTORED, {"stash_ref": SHA})]
    else:
        assert commands == [f"git stash apply {SHA}"]
        assert events == [
            (
                SyncEventType.STASH_LEFT_BEHIND,
                {"stash_ref": SHA, "recovery_hint": f"git stash apply {SHA}"},
            )
        ]
    assert "stash drop" not in output.out


@pytest.mark.parametrize("restored", [False, True])
def test_sync_json_keeps_recovery_contract_without_human_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], restored: bool
) -> None:
    from wade.models.session import SyncEventType

    events: list[tuple[SyncEventType, dict[str, str]]] = []

    def emit(event: SyncEventType, **data: str) -> None:
        events.append((event, data))

    with (
        patch.object(
            sync.git_stash,
            "apply_stash_by_sha",
            return_value=subprocess.CompletedProcess([], 0 if restored else 1, "", "conflict"),
        ),
        patch.object(sync.git_stash, "drop_stash_by_sha", return_value=False),
    ):
        assert sync._handle_stash_restoration(SHA, tmp_path, True, emit, True) is restored
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    if restored:
        assert events == [(SyncEventType.STASH_RESTORED, {"stash_ref": SHA})]
    else:
        assert events == [
            (
                SyncEventType.STASH_LEFT_BEHIND,
                {"stash_ref": SHA, "recovery_hint": f"git stash apply {SHA}"},
            )
        ]

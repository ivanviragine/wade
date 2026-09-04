"""Unit tests for ``_build_implement_cmd`` — the batch ``wade implement`` child command.

The permission mode is forwarded to children **only when it was explicit**. Two
bugs bound this and both are covered here:

- An explicit ``default`` (e.g. the user downgrading from a yolo-configured
  ``ai.implement``) must be forwarded, else each child reloads the config and
  re-resolves back to ``yolo`` — more autonomy than confirmed.
- An implicit ``default`` must NOT be forwarded, else ``wade implement`` treats it
  as explicit and suppresses a child's own config-driven autonomy downstream
  (e.g. a non-default ``ai.review_pr_comments.permission_mode``).
"""

from __future__ import annotations

import importlib
from pathlib import Path

from crossby.models.ai import EffortLevel

from wade.models.permission import PermissionMode
from wade.services.implementation_service.batch import _build_implement_cmd


class TestBuildImplementCmd:
    def test_forwards_explicit_default_permission_mode(self) -> None:
        """An explicitly chosen ``default`` is forwarded so children can't
        re-resolve a yolo config back to yolo."""
        cmd = _build_implement_cmd(
            "42",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=True,
        )
        assert cmd == ["wade", "implement", "42", "--ai", "claude", "--permission-mode", "default"]

    def test_omits_implicit_default_permission_mode(self) -> None:
        """An implicit ``default`` is NOT forwarded — left for the child to
        re-resolve, so it doesn't look explicit downstream."""
        cmd = _build_implement_cmd(
            "42",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
        )
        assert "--permission-mode" not in cmd

    def test_omits_implicit_yolo_permission_mode(self) -> None:
        """Even a non-default mode stays unforwarded when implicit — the child
        re-resolves the same yolo from the shared config."""
        cmd = _build_implement_cmd(
            "9",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.YOLO,
            permission_mode_explicit=False,
        )
        assert "--permission-mode" not in cmd

    def test_forwards_explicit_yolo_permission_mode(self) -> None:
        cmd = _build_implement_cmd(
            "7",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.YOLO,
            permission_mode_explicit=True,
        )
        assert cmd[cmd.index("--permission-mode") + 1] == "yolo"

    def test_model_only_forwarded_when_explicit(self) -> None:
        implicit = _build_implement_cmd(
            "1",
            tool="claude",
            model="claude-opus-4.8",
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
        )
        assert "--model" not in implicit
        explicit = _build_implement_cmd(
            "1",
            tool="claude",
            model="claude-opus-4.8",
            model_explicit=True,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
        )
        assert "--model" in explicit
        assert explicit[explicit.index("--model") + 1] == "claude-opus-4.8"

    def test_effort_and_chain_forwarded(self) -> None:
        cmd = _build_implement_cmd(
            "3",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=EffortLevel.HIGH,
            permission_mode=PermissionMode.ACCEPT_EDITS,
            permission_mode_explicit=True,
            chain_ids=["4", "5"],
        )
        assert cmd[cmd.index("--effort") + 1] == "high"
        assert cmd[cmd.index("--chain") + 1] == "4,5"
        assert cmd[cmd.index("--permission-mode") + 1] == "accept-edits"

    def test_forwards_explicit_sandbox_on(self) -> None:
        """``--sandbox`` on the parent reaches children so they honor the profile
        instead of re-resolving from config."""
        cmd = _build_implement_cmd(
            "42",
            tool="codex",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
            sandbox=True,
        )
        assert "--sandbox" in cmd
        assert "--no-sandbox" not in cmd

    def test_forwards_explicit_sandbox_off(self) -> None:
        """``--no-sandbox`` is forwarded so a child cannot re-sandbox itself via
        a config that turns the sandbox on."""
        cmd = _build_implement_cmd(
            "42",
            tool="codex",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
            sandbox=False,
        )
        assert "--no-sandbox" in cmd
        assert "--sandbox" not in cmd

    def test_omits_sandbox_flag_when_unset(self) -> None:
        """Unset (``None``) forwards no flag — each child re-resolves the profile
        from its own config, mirroring permission mode. Freezing the parent's
        resolved value here would shadow ``ai.<command>.sandbox`` in the child."""
        cmd = _build_implement_cmd(
            "42",
            tool="codex",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
            sandbox=None,
        )
        assert "--sandbox" not in cmd
        assert "--no-sandbox" not in cmd


class TestBatchAddsNoSandboxCheckOfItsOwn:
    """Batch implementation is not a launch path (#480).

    It spawns child ``wade implement`` processes, and each child reaches the
    parent-sandbox check in ``implementation_service.core`` on its own. A check
    here would be a dead no-op that implies coverage it does not add — and a
    *second* diagnosis of the same boundary, one per child, if it ever did fire.
    """

    def test_the_child_command_is_a_plain_wade_implement_invocation(self) -> None:
        cmd = _build_implement_cmd(
            "42",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
            permission_mode_explicit=False,
        )

        assert cmd[:3] == ["wade", "implement", "42"]

    def test_batch_module_holds_no_parent_runtime_probe(self) -> None:
        """Pinned as source text: the point is the *absence* of a check.

        A behavioural assertion cannot distinguish "no check" from "a check that
        happens not to fire", which is exactly the confusion this guards against.
        """
        module = importlib.import_module("wade.services.implementation_service.batch")
        assert module.__file__ is not None
        source = Path(module.__file__).read_text(encoding="utf-8")

        assert "requires_unsandboxed_relaunch" not in source
        assert "detect_parent_runtime" not in source

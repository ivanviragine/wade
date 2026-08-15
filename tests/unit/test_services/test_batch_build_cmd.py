"""Unit tests for ``_build_implement_cmd`` — the batch ``wade implement`` child command.

Regression coverage for the permission-mode propagation bug: batch children reload
the project config and re-resolve their own permission mode, so an explicitly
resolved ``default`` that is *not* forwarded lets each child resolve back to a
``yolo``-configured ``ai.implement`` — launching with more autonomy than confirmed.
The resolved mode must therefore be forwarded unconditionally.
"""

from __future__ import annotations

from crossby.models.ai import EffortLevel

from wade.models.permission import PermissionMode
from wade.services.implementation_service.batch import _build_implement_cmd


def _mode_value(cmd: list[str]) -> str:
    return cmd[cmd.index("--permission-mode") + 1]


class TestBuildImplementCmd:
    def test_forwards_default_permission_mode(self) -> None:
        """An explicitly resolved ``default`` is forwarded so children can't
        re-resolve a yolo config back to yolo."""
        cmd = _build_implement_cmd(
            "42",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
        )
        assert cmd == ["wade", "implement", "42", "--ai", "claude", "--permission-mode", "default"]

    def test_forwards_yolo_permission_mode(self) -> None:
        cmd = _build_implement_cmd(
            "7",
            tool="claude",
            model=None,
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.YOLO,
        )
        assert _mode_value(cmd) == "yolo"

    def test_model_only_forwarded_when_explicit(self) -> None:
        implicit = _build_implement_cmd(
            "1",
            tool="claude",
            model="claude-opus-4.8",
            model_explicit=False,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
        )
        assert "--model" not in implicit
        explicit = _build_implement_cmd(
            "1",
            tool="claude",
            model="claude-opus-4.8",
            model_explicit=True,
            effort=None,
            permission_mode=PermissionMode.DEFAULT,
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
            chain_ids=["4", "5"],
        )
        assert cmd[cmd.index("--effort") + 1] == "high"
        assert cmd[cmd.index("--chain") + 1] == "4,5"
        assert _mode_value(cmd) == "accept-edits"

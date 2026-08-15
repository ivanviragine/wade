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

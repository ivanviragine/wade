"""Tests for the PermissionMode autonomy axis.

Covers the model, config parsing/precedence (including the yolo alias and the
``plan``/invalid rejection), resolution precedence, delegation pass-through to
the crossby autonomy triplet, the headless force-default policy, the crossby
auto→accept-edits downgrade, and CLI wiring.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PermissionMode model + helpers
# ---------------------------------------------------------------------------


class TestPermissionModeModel:
    def test_enum_values(self) -> None:
        from wade.models.permission import PermissionMode

        assert PermissionMode.DEFAULT.value == "default"
        assert PermissionMode.ACCEPT_EDITS.value == "accept-edits"
        assert PermissionMode.AUTO.value == "auto"
        assert PermissionMode.YOLO.value == "yolo"

    def test_hyphenated_value_round_trips(self) -> None:
        from wade.models.permission import PermissionMode

        # The member name (ACCEPT_EDITS) diverges from its string value.
        assert PermissionMode("accept-edits") is PermissionMode.ACCEPT_EDITS
        assert PermissionMode.ACCEPT_EDITS == "accept-edits"

    def test_coerce_valid(self) -> None:
        from wade.models.permission import PermissionMode, coerce_permission_mode

        assert coerce_permission_mode("auto") is PermissionMode.AUTO
        assert coerce_permission_mode("accept-edits") is PermissionMode.ACCEPT_EDITS
        assert coerce_permission_mode(PermissionMode.YOLO) is PermissionMode.YOLO

    def test_coerce_invalid_and_excluded(self) -> None:
        from wade.models.permission import coerce_permission_mode

        assert coerce_permission_mode(None) is None
        assert coerce_permission_mode("plan") is None  # plan is intentionally excluded
        assert coerce_permission_mode("bogus") is None

    def test_coerce_normalizes_underscore_and_case(self) -> None:
        from wade.models.permission import PermissionMode, coerce_permission_mode

        # crossby's kwarg spelling (underscore) and mixed case both round-trip
        # to the canonical hyphenated tier.
        assert coerce_permission_mode("accept_edits") is PermissionMode.ACCEPT_EDITS
        assert coerce_permission_mode("Accept-Edits") is PermissionMode.ACCEPT_EDITS
        assert coerce_permission_mode("AUTO") is PermissionMode.AUTO
        # normalization does not resurrect the excluded value
        assert coerce_permission_mode("PLAN") is None

    def test_launch_kwargs_per_tier(self) -> None:
        from wade.models.permission import PermissionMode, permission_mode_launch_kwargs

        assert permission_mode_launch_kwargs(PermissionMode.YOLO) == {
            "yolo": True,
            "auto": False,
            "accept_edits": False,
        }
        assert permission_mode_launch_kwargs(PermissionMode.AUTO) == {
            "yolo": False,
            "auto": True,
            "accept_edits": False,
        }
        assert permission_mode_launch_kwargs(PermissionMode.ACCEPT_EDITS) == {
            "yolo": False,
            "auto": False,
            "accept_edits": True,
        }
        assert permission_mode_launch_kwargs(PermissionMode.DEFAULT) == {
            "yolo": False,
            "auto": False,
            "accept_edits": False,
        }


# ---------------------------------------------------------------------------
# Config model — get_permission_mode() precedence + yolo alias
# ---------------------------------------------------------------------------


class TestConfigPermissionMode:
    def test_global(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode

        cfg = ProjectConfig(ai=AIConfig(permission_mode="auto"))
        assert cfg.get_permission_mode("plan") is PermissionMode.AUTO

    def test_command_overrides_global(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode

        cfg = ProjectConfig(
            ai=AIConfig(
                permission_mode="auto",
                implement=AICommandConfig(permission_mode="accept-edits"),
            )
        )
        assert cfg.get_permission_mode("implement") is PermissionMode.ACCEPT_EDITS
        assert cfg.get_permission_mode("plan") is PermissionMode.AUTO

    def test_yolo_alias(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode

        cfg = ProjectConfig(ai=AIConfig(yolo=True))
        assert cfg.get_permission_mode("plan") is PermissionMode.YOLO

    def test_permission_mode_beats_yolo_same_level(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode

        cfg = ProjectConfig(ai=AIConfig(permission_mode="accept-edits", yolo=True))
        assert cfg.get_permission_mode("plan") is PermissionMode.ACCEPT_EDITS

    def test_command_yolo_false_blocks_global_yolo(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode

        cfg = ProjectConfig(
            ai=AIConfig(yolo=True, implement=AICommandConfig(yolo=False)),
        )
        assert cfg.get_permission_mode("implement") is PermissionMode.DEFAULT
        assert cfg.get_permission_mode("plan") is PermissionMode.YOLO

    def test_unset_returns_none(self) -> None:
        from wade.models.config import ProjectConfig

        assert ProjectConfig().get_permission_mode("plan") is None


# ---------------------------------------------------------------------------
# Config loader — YAML parsing + plan/invalid rejection
# ---------------------------------------------------------------------------


class TestConfigLoaderPermissionMode:
    def test_parse_global(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        (tmp_path / ".wade.yml").write_text("ai:\n  permission_mode: accept-edits\n")
        config = load_config(tmp_path)
        assert config.ai.permission_mode == "accept-edits"

    def test_parse_per_command(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        (tmp_path / ".wade.yml").write_text("ai:\n  implement:\n    permission_mode: auto\n")
        config = load_config(tmp_path)
        assert config.ai.implement.permission_mode == "auto"

    def test_reject_plan_falls_back_to_none(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        (tmp_path / ".wade.yml").write_text("ai:\n  permission_mode: plan\n")
        config = load_config(tmp_path)
        # plan is dropped to None (treated as unset) — the loader warns.
        assert config.ai.permission_mode is None

    def test_reject_invalid_value(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        (tmp_path / ".wade.yml").write_text("ai:\n  implement:\n    permission_mode: bogus\n")
        config = load_config(tmp_path)
        assert config.ai.implement.permission_mode is None


# ---------------------------------------------------------------------------
# resolve_permission_mode() — fallback chain CLI > command > global > default
# ---------------------------------------------------------------------------


class TestResolvePermissionMode:
    def test_cli_wins_over_config(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        cfg = ProjectConfig(ai=AIConfig(permission_mode="auto"))
        assert (
            resolve_permission_mode("accept-edits", None, cfg, "plan")
            is PermissionMode.ACCEPT_EDITS
        )

    def test_cli_yolo_alias(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        assert resolve_permission_mode(None, True, ProjectConfig(), "plan") is PermissionMode.YOLO

    def test_cli_permission_mode_beats_cli_yolo(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        assert (
            resolve_permission_mode("accept-edits", True, ProjectConfig(), "plan")
            is PermissionMode.ACCEPT_EDITS
        )

    def test_falls_to_command_config(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        cfg = ProjectConfig(ai=AIConfig(implement=AICommandConfig(permission_mode="auto")))
        assert resolve_permission_mode(None, None, cfg, "implement") is PermissionMode.AUTO

    def test_falls_to_global_config(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        cfg = ProjectConfig(ai=AIConfig(permission_mode="accept-edits"))
        assert resolve_permission_mode(None, None, cfg, "implement") is PermissionMode.ACCEPT_EDITS

    def test_default_when_unset(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        assert (
            resolve_permission_mode(None, None, ProjectConfig(), "plan") is PermissionMode.DEFAULT
        )

    def test_invalid_cli_warns_and_defaults(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        with patch("wade.services.ai_resolution.logger") as mock_logger:
            result = resolve_permission_mode("plan", None, ProjectConfig(), "plan")
        assert result is PermissionMode.DEFAULT
        assert mock_logger.warning.called  # type: ignore[union-attr]

    def test_no_capability_gate(self) -> None:
        # Unlike the old resolve_yolo, an unsupported tier is NOT gated here —
        # crossby downgrades at launch. resolve_permission_mode never takes a tool.
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        assert resolve_permission_mode("auto", None, ProjectConfig(), "plan") is PermissionMode.AUTO


# ---------------------------------------------------------------------------
# resolve_yolo() re-expressed via resolve_permission_mode
# ---------------------------------------------------------------------------


class TestResolveYoloDerived:
    def test_yolo_tier_is_true(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        cfg = ProjectConfig(ai=AIConfig(permission_mode="yolo"))
        assert resolve_yolo(None, cfg, "plan") is True

    def test_lower_tier_is_not_yolo(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        cfg = ProjectConfig(ai=AIConfig(permission_mode="accept-edits"))
        assert resolve_yolo(None, cfg, "plan") is False


# ---------------------------------------------------------------------------
# Delegation pass-through — the correct triplet reaches the adapter per tier
# ---------------------------------------------------------------------------


class TestDelegationPassThrough:
    @pytest.mark.parametrize(
        ("mode_value", "expected"),
        [
            ("yolo", {"yolo": True, "auto": False, "accept_edits": False}),
            ("auto", {"yolo": False, "auto": True, "accept_edits": False}),
            ("accept-edits", {"yolo": False, "auto": False, "accept_edits": True}),
            ("default", {"yolo": False, "auto": False, "accept_edits": False}),
        ],
    )
    def test_interactive_forwards_triplet(
        self, mode_value: str, expected: dict[str, bool], tmp_path: Path
    ) -> None:
        from wade.models.delegation import DelegationMode, DelegationRequest
        from wade.models.permission import PermissionMode
        from wade.services.delegation_service import _delegate_interactive

        with (
            patch("wade.services.delegation_service.deliver_prompt_if_needed"),
            patch("wade.services.delegation_service.AbstractAITool.get") as mock_get,
        ):
            adapter = MagicMock()
            adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
            adapter.launch.side_effect = lambda **_kw: (tmp_path / "out.txt").write_text("ok")
            mock_get.return_value = adapter

            req = DelegationRequest(
                mode=DelegationMode.INTERACTIVE,
                prompt="Do work",
                ai_tool="claude",
                output_file=tmp_path / "out.txt",
                permission_mode=PermissionMode(mode_value),
            )
            result = _delegate_interactive(req)

        assert result.success is True
        kwargs = adapter.launch.call_args.kwargs
        assert {k: kwargs[k] for k in ("yolo", "auto", "accept_edits")} == expected

    def test_headless_forces_default(self, tmp_path: Path) -> None:
        """A configured autonomy tier never grants autonomy on the headless path."""
        from wade.models.delegation import DelegationMode, DelegationRequest
        from wade.models.permission import PermissionMode
        from wade.services.delegation_service import _delegate_headless

        with patch("wade.services.delegation_service.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="done\n")
            req = DelegationRequest(
                mode=DelegationMode.HEADLESS,
                prompt="Analyze",
                ai_tool="claude",
                permission_mode=PermissionMode.YOLO,
            )
            result = _delegate_headless(req)

        assert result.success is True
        cmd = mock_run.call_args[0][0]
        # No autonomy flags leak into the headless command.
        assert "--dangerously-skip-permissions" not in cmd
        assert "--permission-mode" not in cmd


# ---------------------------------------------------------------------------
# crossby downgrade — auto→accept-edits on a tool without supports_auto
# ---------------------------------------------------------------------------


class TestAutonomyDowngrade:
    def test_auto_downgrades_to_accept_edits_on_antigravity_cli(self) -> None:
        """Antigravity CLI lacks supports_auto → crossby downgrades to accept-edits."""
        from crossby.ai_tools import AbstractAITool
        from crossby.models.ai import AIToolID

        adapter = AbstractAITool.get(AIToolID("antigravity-cli"))
        with pytest.warns(UserWarning, match=r"downgrading to accept-edits"):
            cmd = adapter.build_launch_command(auto=True)
        # Lands on the accept-edits flag, not an auto flag.
        assert "--mode" in cmd
        assert cmd[cmd.index("--mode") + 1] == "accept-edits"

    def test_claude_auto_not_downgraded(self) -> None:
        """Claude supports auto → no downgrade, no warning."""
        from crossby.ai_tools import AbstractAITool
        from crossby.models.ai import AIToolID

        adapter = AbstractAITool.get(AIToolID("claude"))
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any UserWarning would fail the test
            cmd = adapter.build_launch_command(auto=True)
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "auto"


# ---------------------------------------------------------------------------
# CLI parsing — --permission-mode and the --yolo alias
# ---------------------------------------------------------------------------


class TestCliParsing:
    def test_plan_permission_mode_flag_forwarded(self) -> None:
        from typer.testing import CliRunner

        from wade.cli.main import app

        with patch("wade.services.plan_service.plan", return_value=True) as mock_plan:
            result = CliRunner().invoke(app, ["plan", "--permission-mode", "accept-edits"])
        assert result.exit_code == 0
        assert mock_plan.call_args.kwargs["permission_mode"] == "accept-edits"

    def test_plan_yolo_alias_forwarded(self) -> None:
        from typer.testing import CliRunner

        from wade.cli.main import app

        with patch("wade.services.plan_service.plan", return_value=True) as mock_plan:
            result = CliRunner().invoke(app, ["plan", "--yolo"])
        assert result.exit_code == 0
        # --yolo forwards yolo=True; permission_mode stays None (resolver maps it).
        assert mock_plan.call_args.kwargs["yolo"] is True

    def test_review_pr_comments_permission_mode_forwarded(self) -> None:
        from typer.testing import CliRunner

        from wade.cli.review import review_app

        with patch("wade.services.review_service.start", return_value=True) as mock_start:
            result = CliRunner().invoke(
                review_app, ["pr-comments", "42", "--permission-mode", "auto"]
            )
        assert result.exit_code == 0
        assert mock_start.call_args.kwargs["permission_mode"] == "auto"

    def test_invalid_cli_value_resolves_to_default(self) -> None:
        # `--permission-mode plan` is accepted by Typer (option is a plain str)
        # and rejected at resolution time (warn + default), never erroring.
        from wade.models.config import ProjectConfig
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import resolve_permission_mode

        assert (
            resolve_permission_mode("plan", None, ProjectConfig(), "plan") is PermissionMode.DEFAULT
        )

"""Tests for the ``bot_review`` config section (#431).

Covers the Pydantic models (defaults via ``default_factory``, overrides), the
hand-rolled loader parse/round-trip, and the ``wade check-config`` validator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.config.loader import ConfigError, parse_config_file
from wade.models.config import BotReviewConfig, ProjectConfig, ReviewBotConfig
from wade.services.check_service import _validate_config_file


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".wade.yml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestBotReviewModels:
    def test_project_config_default_ships_three_bots(self) -> None:
        config = ProjectConfig()
        assert config.bot_review.auto_trigger is False
        assert [(b.name, b.trigger, b.enabled) for b in config.bot_review.bots] == [
            ("coderabbit", "@coderabbitai review", True),
            ("codex", "@codex review", True),
            ("bugbot", "bugbot run", True),
        ]

    def test_default_factory_isolates_the_bots_list(self) -> None:
        """The mutable default must not be shared across instances (#431)."""
        a = BotReviewConfig()
        b = BotReviewConfig()
        assert a.bots is not b.bots
        a.bots.append(ReviewBotConfig(name="extra", trigger="go"))
        assert len(a.bots) == 4
        assert len(b.bots) == 3
        # A fresh ProjectConfig is likewise unaffected.
        assert len(ProjectConfig().bot_review.bots) == 3

    def test_review_bot_enabled_defaults_true(self) -> None:
        assert ReviewBotConfig(name="x", trigger="y").enabled is True


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestBotReviewLoader:
    def test_absent_section_loads_defaults(self, tmp_path: Path) -> None:
        config = parse_config_file(_write(tmp_path, "version: 2\nproject:\n  main_branch: main\n"))
        assert config.bot_review.auto_trigger is False
        assert len(config.bot_review.bots) == 3

    def test_auto_trigger_only_keeps_default_bots(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "version: 2\nbot_review:\n  auto_trigger: true\n")
        config = parse_config_file(path)
        assert config.bot_review.auto_trigger is True
        assert [b.name for b in config.bot_review.bots] == ["coderabbit", "codex", "bugbot"]

    def test_explicit_bots_list_replaces_defaults(self, tmp_path: Path) -> None:
        text = (
            "version: 2\n"
            "bot_review:\n"
            "  bots:\n"
            '    - {name: only, trigger: "trigger me", enabled: false}\n'
        )
        config = parse_config_file(_write(tmp_path, text))
        assert len(config.bot_review.bots) == 1
        assert config.bot_review.bots[0].name == "only"
        assert config.bot_review.bots[0].trigger == "trigger me"
        assert config.bot_review.bots[0].enabled is False

    def test_null_auto_trigger_normalizes_to_false(self, tmp_path: Path) -> None:
        config = parse_config_file(_write(tmp_path, "version: 2\nbot_review:\n  auto_trigger:\n"))
        assert config.bot_review.auto_trigger is False

    def test_bots_not_a_list_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            parse_config_file(_write(tmp_path, "version: 2\nbot_review:\n  bots: nope\n"))

    def test_bot_missing_name_raises(self, tmp_path: Path) -> None:
        text = "version: 2\nbot_review:\n  bots:\n    - {trigger: go}\n"
        with pytest.raises(ConfigError):
            parse_config_file(_write(tmp_path, text))

    def test_override_enabled_field(self, tmp_path: Path) -> None:
        text = (
            "version: 2\n"
            "bot_review:\n"
            "  auto_trigger: true\n"
            "  bots:\n"
            '    - {name: coderabbit, trigger: "@coderabbitai review", enabled: true}\n'
            '    - {name: codex, trigger: "@codex review", enabled: false}\n'
        )
        config = parse_config_file(_write(tmp_path, text))
        enabled_by_name = {b.name: b.enabled for b in config.bot_review.bots}
        assert enabled_by_name == {"coderabbit": True, "codex": False}


# ---------------------------------------------------------------------------
# check-config validator
# ---------------------------------------------------------------------------


class TestBotReviewValidation:
    def test_valid_section_has_no_errors(self, tmp_path: Path) -> None:
        text = (
            "version: 2\n"
            "bot_review:\n"
            "  auto_trigger: true\n"
            "  bots:\n"
            '    - {name: coderabbit, trigger: "@coderabbitai review", enabled: true}\n'
        )
        assert _validate_config_file(_write(tmp_path, text)) == []

    def test_bot_review_is_a_supported_top_level_key(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "version: 2\nbot_review:\n  auto_trigger: false\n")
        errors = _validate_config_file(path)
        assert not any("unsupported key 'bot_review'" in e for e in errors)

    def test_non_bool_auto_trigger_errors(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "version: 2\nbot_review:\n  auto_trigger: nope\n")
        errors = _validate_config_file(path)
        assert any("bot_review.auto_trigger" in e for e in errors)

    def test_bots_not_a_list_errors(self, tmp_path: Path) -> None:
        errors = _validate_config_file(_write(tmp_path, "version: 2\nbot_review:\n  bots: nope\n"))
        assert any("bot_review.bots: must be a list" in e for e in errors)

    def test_bot_missing_trigger_errors(self, tmp_path: Path) -> None:
        text = "version: 2\nbot_review:\n  bots:\n    - {name: coderabbit}\n"
        errors = _validate_config_file(_write(tmp_path, text))
        assert any("bot_review.bots[0].trigger" in e for e in errors)

    def test_unsupported_bot_key_errors(self, tmp_path: Path) -> None:
        text = "version: 2\nbot_review:\n  bots:\n    - {name: a, trigger: b, bogus: 1}\n"
        errors = _validate_config_file(_write(tmp_path, text))
        assert any("bot_review.bots[0].bogus" in e for e in errors)

    def test_unsupported_top_level_bot_review_key_errors(self, tmp_path: Path) -> None:
        errors = _validate_config_file(_write(tmp_path, "version: 2\nbot_review:\n  bogus: 1\n"))
        assert any("bot_review.bogus" in e for e in errors)

    def test_duplicate_bot_names_error(self, tmp_path: Path) -> None:
        text = (
            "version: 2\n"
            "bot_review:\n"
            "  bots:\n"
            "    - {name: codex, trigger: a}\n"
            "    - {name: codex, trigger: b}\n"
        )
        errors = _validate_config_file(_write(tmp_path, text))
        assert any("duplicated" in e and "bot_review.bots[1].name" in e for e in errors)

    def test_distinct_bot_names_ok(self, tmp_path: Path) -> None:
        text = (
            "version: 2\n"
            "bot_review:\n"
            "  bots:\n"
            "    - {name: codex, trigger: a}\n"
            "    - {name: bugbot, trigger: b}\n"
        )
        assert _validate_config_file(_write(tmp_path, text)) == []

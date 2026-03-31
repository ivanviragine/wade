"""Tests for the Gemini CLI policy file writer."""

from __future__ import annotations

from pathlib import Path

from wade.config.gemini_policy import write_gemini_policy


class TestWriteGeminiPolicy:
    def test_creates_policy_file(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, ["wade *"])
        policy_file = tmp_path / ".gemini" / "policies" / "wade.toml"
        assert policy_file.is_file()

    def test_single_command_rule(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, ["wade *"])
        content = (tmp_path / ".gemini" / "policies" / "wade.toml").read_text()
        assert "[[rule]]" in content
        assert 'toolName = "run_shell_command"' in content
        assert 'commandPrefix = "wade"' in content
        assert 'decision = "allow"' in content
        assert "priority = 100" in content

    def test_multiple_commands_produce_multiple_rules(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, ["wade *", "./scripts/check.sh"])
        content = (tmp_path / ".gemini" / "policies" / "wade.toml").read_text()
        assert content.count("[[rule]]") == 2
        assert 'commandPrefix = "wade"' in content
        assert 'commandPrefix = "./scripts/check.sh"' in content

    def test_command_prefix_uses_binary_only(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, ["git commit -m msg"])
        content = (tmp_path / ".gemini" / "policies" / "wade.toml").read_text()
        assert 'commandPrefix = "git"' in content

    def test_empty_list_writes_nothing(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, [])
        policy_file = tmp_path / ".gemini" / "policies" / "wade.toml"
        assert not policy_file.exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested"
        write_gemini_policy(nested, ["wade *"])
        assert (nested / ".gemini" / "policies" / "wade.toml").is_file()

    def test_overwrites_previous_policy(self, tmp_path: Path) -> None:
        write_gemini_policy(tmp_path, ["old-cmd"])
        write_gemini_policy(tmp_path, ["wade *"])
        content = (tmp_path / ".gemini" / "policies" / "wade.toml").read_text()
        assert 'commandPrefix = "wade"' in content
        assert "old-cmd" not in content

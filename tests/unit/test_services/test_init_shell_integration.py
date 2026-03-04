"""Tests for shell integration configuration during init."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.services.init_service import (
    _configure_shell_integration,
    _get_shell_profile,
    _is_shell_integration_configured,
    _prompt_configure_shell_integration,
)


class TestGetShellProfile:
    """Tests for _get_shell_profile shell detection."""

    def test_zsh_detection(self):
        """zsh should map to ~/.zshrc."""
        result = _get_shell_profile("/bin/zsh")
        assert result == Path.home() / ".zshrc"

    def test_bash_detection_linux(self):
        """bash on Linux should map to ~/.bashrc."""
        with patch("sys.platform", "linux"):
            result = _get_shell_profile("/bin/bash")
            assert result == Path.home() / ".bashrc"

    def test_bash_detection_macos(self):
        """bash on macOS should map to ~/.bash_profile."""
        with patch("sys.platform", "darwin"):
            result = _get_shell_profile("/bin/bash")
            assert result == Path.home() / ".bash_profile"

    def test_fish_detection(self):
        """fish should map to ~/.config/fish/config.fish."""
        result = _get_shell_profile("/usr/bin/fish")
        assert result == Path.home() / ".config" / "fish" / "config.fish"

    def test_unknown_shell(self):
        """Unknown shell should return None."""
        result = _get_shell_profile("/usr/bin/unknown")
        assert result is None

    def test_shell_path_variants(self):
        """Shell name extraction should work with various paths."""
        # Test that it extracts just the shell name from full path
        result = _get_shell_profile("/usr/local/bin/zsh")
        assert result == Path.home() / ".zshrc"


class TestIsShellIntegrationConfigured:
    """Tests for _is_shell_integration_configured detection."""

    def test_not_configured_file_missing(self, tmp_path):
        """Missing file should return False."""
        profile = tmp_path / "nonexistent"
        assert _is_shell_integration_configured(profile) is False

    def test_not_configured_empty_file(self, tmp_path):
        """Empty profile should return False."""
        profile = tmp_path / ".zshrc"
        profile.write_text("")
        assert _is_shell_integration_configured(profile) is False

    def test_configured_with_eval(self, tmp_path):
        """Profile with eval line should return True."""
        profile = tmp_path / ".zshrc"
        profile.write_text('eval "$(wade shell-init)"\n')
        assert _is_shell_integration_configured(profile) is True

    def test_configured_with_source(self, tmp_path):
        """Profile with source line should return True."""
        profile = tmp_path / "config.fish"
        profile.write_text("wade shell-init | source\n")
        assert _is_shell_integration_configured(profile) is True

    def test_configured_substring_match(self, tmp_path):
        """Any mention of 'wade shell-init' should return True."""
        profile = tmp_path / ".zshrc"
        profile.write_text("# Some comment about wade shell-init here\n")
        assert _is_shell_integration_configured(profile) is True

    def test_configured_with_extra_content(self, tmp_path):
        """Detection should work with other content in file."""
        profile = tmp_path / ".zshrc"
        profile.write_text(
            "export PATH=/usr/bin:$PATH\neval \"$(wade shell-init)\"\nalias ll='ls -la'\n"
        )
        assert _is_shell_integration_configured(profile) is True


class TestConfigureShellIntegration:
    """Tests for _configure_shell_integration writing."""

    def test_configure_bash_creates_new_file(self, tmp_path):
        """Should create profile file and add bash eval line."""
        profile = tmp_path / ".bashrc"
        _configure_shell_integration(profile, is_fish=False)

        assert profile.is_file()
        content = profile.read_text()
        assert "# wade shell integration" in content
        assert 'eval "$(wade shell-init)"' in content

    def test_configure_fish_creates_new_file(self, tmp_path):
        """Should create profile file and add fish source line."""
        fish_dir = tmp_path / ".config" / "fish"
        profile = fish_dir / "config.fish"
        _configure_shell_integration(profile, is_fish=True)

        assert profile.is_file()
        content = profile.read_text()
        assert "# wade shell integration" in content
        assert "wade shell-init | source" in content

    def test_configure_bash_appends_to_existing(self, tmp_path):
        """Should append to existing profile, preserving content."""
        profile = tmp_path / ".bashrc"
        profile.write_text("export PATH=/usr/local/bin:$PATH\n")

        _configure_shell_integration(profile, is_fish=False)

        content = profile.read_text()
        # Original content should be preserved
        assert "export PATH=/usr/local/bin:$PATH" in content
        # New content should be appended
        assert 'eval "$(wade shell-init)"' in content

    def test_configure_fish_appends_to_existing(self, tmp_path):
        """Should append to existing fish config, preserving content."""
        fish_dir = tmp_path / ".config" / "fish"
        fish_dir.mkdir(parents=True)
        profile = fish_dir / "config.fish"
        profile.write_text("alias ll 'ls -la'\n")

        _configure_shell_integration(profile, is_fish=True)

        content = profile.read_text()
        # Original content should be preserved
        assert "alias ll 'ls -la'" in content
        # New content should be appended
        assert "wade shell-init | source" in content

    def test_configure_creates_parent_directories(self, tmp_path):
        """Should create parent directories if missing."""
        profile = tmp_path / ".config" / "fish" / "config.fish"
        assert not profile.parent.exists()

        _configure_shell_integration(profile, is_fish=True)

        assert profile.is_file()
        assert profile.parent.exists()


class TestMaybeConfigureShellIntegration:
    """Tests for _prompt_configure_shell_integration conditional logic."""

    def test_non_interactive_skips_configuration(self, tmp_path):
        """non_interactive=True should skip without prompting."""
        with (
            patch.dict("os.environ", {"SHELL": "/bin/zsh"}),
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=True)
            mock_config.assert_not_called()

    def test_already_configured_skips(self, tmp_path):
        """Already configured profile should skip silently."""
        profile = tmp_path / ".zshrc"
        profile.write_text('eval "$(wade shell-init)"\n')

        with (
            patch.dict("os.environ", {"SHELL": str(profile.parent / "zsh")}),
            patch("wade.services.init_service._get_shell_profile", return_value=profile),
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=False)
            mock_config.assert_not_called()

    def test_unknown_shell_shows_hint(self):
        """Unknown shell should show hint instead of prompting."""
        with (
            patch.dict("os.environ", {"SHELL": "/usr/bin/unknown"}),
            patch("wade.services.init_service.console") as mock_console,
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=False)
            # Should show hint
            mock_console.hint.assert_called_once()
            # Should not configure
            mock_config.assert_not_called()

    def test_prompts_and_configures_on_confirm(self, tmp_path):
        """Should prompt and configure when user confirms."""
        profile = tmp_path / ".zshrc"

        with (
            patch.dict("os.environ", {"SHELL": "/bin/zsh"}),
            patch("wade.services.init_service._get_shell_profile", return_value=profile),
            patch(
                "wade.services.init_service._is_shell_integration_configured",
                return_value=False,
            ),
            patch("wade.ui.prompts.confirm", return_value=True),
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=False)
            mock_config.assert_called_once()

    def test_does_not_configure_on_decline(self, tmp_path):
        """Should not configure when user declines."""
        profile = tmp_path / ".zshrc"

        with (
            patch.dict("os.environ", {"SHELL": "/bin/zsh"}),
            patch("wade.services.init_service._get_shell_profile", return_value=profile),
            patch(
                "wade.services.init_service._is_shell_integration_configured",
                return_value=False,
            ),
            patch("wade.ui.prompts.confirm", return_value=False),
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=False)
            mock_config.assert_not_called()

    def test_fish_shell_detected(self):
        """Should detect fish shell and pass is_fish=True."""
        profile = MagicMock()
        with (
            patch.dict("os.environ", {"SHELL": "/usr/bin/fish"}),
            patch("wade.services.init_service._get_shell_profile", return_value=profile),
            patch(
                "wade.services.init_service._is_shell_integration_configured",
                return_value=False,
            ),
            patch("wade.ui.prompts.confirm", return_value=True),
            patch("wade.services.init_service._configure_shell_integration") as mock_config,
        ):
            _prompt_configure_shell_integration(non_interactive=False)
            # Should pass is_fish=True
            mock_config.assert_called_once()
            _, kwargs = mock_config.call_args
            assert kwargs.get("is_fish") is True

"""Tests for self-upgrade helpers — install method detection, upgrade, re-exec."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ghaiw.utils.install import (
    InstallMethod,
    detect_install_method,
    get_installed_version,
    re_exec,
    self_upgrade,
)

# ---------------------------------------------------------------------------
# detect_install_method
# ---------------------------------------------------------------------------


class TestDetectInstallMethod:
    def test_detects_uv_tool_linux(self) -> None:
        """sys.executable in ~/.local/share/uv/tools/ → UV_TOOL."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/home/user/.local/share/uv/tools/ghaiw/bin/python"
            assert detect_install_method() == InstallMethod.UV_TOOL

    def test_detects_uv_tool_macos(self) -> None:
        """macOS uv tool path also contains /.local/share/uv/tools/."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/Users/user/.local/share/uv/tools/ghaiw/bin/python3.11"
            assert detect_install_method() == InstallMethod.UV_TOOL

    def test_detects_pipx(self) -> None:
        """sys.executable in /.local/pipx/venvs/ → PIPX."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/home/user/.local/pipx/venvs/ghaiw/bin/python"
            assert detect_install_method() == InstallMethod.PIPX

    def test_detects_brew(self) -> None:
        """sys.executable in /homebrew/ → BREW."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/opt/homebrew/Cellar/ghaiw/1.0.0/libexec/bin/python3"
            assert detect_install_method() == InstallMethod.BREW

    def test_detects_editable(self) -> None:
        """sys.executable in /.venv/ → EDITABLE."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/home/user/projects/ghaiw/.venv/bin/python"
            assert detect_install_method() == InstallMethod.EDITABLE

    def test_detects_unknown(self) -> None:
        """Unrecognised path → UNKNOWN."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.executable = "/usr/local/bin/python3"
            assert detect_install_method() == InstallMethod.UNKNOWN


# ---------------------------------------------------------------------------
# get_installed_version
# ---------------------------------------------------------------------------


class TestGetInstalledVersion:
    def test_returns_current_version(self) -> None:
        """Returns the __version__ from the installed ghaiw package."""
        from ghaiw import __version__

        result = get_installed_version()

        assert result == __version__

    def test_returns_string(self) -> None:
        """The return value is always a non-empty string."""
        result = get_installed_version()

        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# self_upgrade
# ---------------------------------------------------------------------------


class TestSelfUpgrade:
    def test_uv_tool_calls_uv_tool_upgrade(self) -> None:
        """When UV_TOOL, runs `uv tool upgrade ghaiw`."""
        ok_result = MagicMock(returncode=0)
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch("ghaiw.utils.install.subprocess.run", return_value=ok_result) as mock_run,
        ):
            result = self_upgrade()

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["uv", "tool", "upgrade", "ghaiw"]

    def test_pipx_calls_pipx_upgrade(self) -> None:
        """When PIPX, runs `pipx upgrade ghaiw`."""
        ok_result = MagicMock(returncode=0)
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.PIPX),
            patch("ghaiw.utils.install.subprocess.run", return_value=ok_result) as mock_run,
        ):
            result = self_upgrade()

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["pipx", "upgrade", "ghaiw"]

    def test_brew_calls_brew_upgrade(self) -> None:
        """When BREW, runs `brew upgrade ghaiw`."""
        ok_result = MagicMock(returncode=0)
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.BREW),
            patch("ghaiw.utils.install.subprocess.run", return_value=ok_result) as mock_run,
        ):
            result = self_upgrade()

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["brew", "upgrade", "ghaiw"]

    def test_editable_returns_false(self) -> None:
        """When EDITABLE, skip silently and return False."""
        with patch(
            "ghaiw.utils.install.detect_install_method", return_value=InstallMethod.EDITABLE
        ):
            result = self_upgrade()

        assert result is False

    def test_unknown_returns_false(self) -> None:
        """When UNKNOWN, skip silently and return False."""
        with patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.UNKNOWN):
            result = self_upgrade()

        assert result is False

    def test_command_failure_returns_false(self) -> None:
        """When the upgrade command exits non-zero, return False."""
        fail_result = MagicMock(returncode=1, stderr="error")
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch("ghaiw.utils.install.subprocess.run", return_value=fail_result),
        ):
            result = self_upgrade()

        assert result is False

    def test_timeout_returns_false(self) -> None:
        """When the upgrade command times out, return False."""
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch(
                "ghaiw.utils.install.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["uv"], timeout=120),
            ),
        ):
            result = self_upgrade()

        assert result is False

    def test_os_error_returns_false(self) -> None:
        """When the upgrade command raises OSError, return False."""
        with (
            patch("ghaiw.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch("ghaiw.utils.install.subprocess.run", side_effect=OSError("not found")),
        ):
            result = self_upgrade()

        assert result is False


# ---------------------------------------------------------------------------
# re_exec
# ---------------------------------------------------------------------------


class TestReExec:
    def test_calls_execv_with_absolute_path(self) -> None:
        """When argv[0] is absolute, pass it directly to os.execv."""
        with (
            patch("ghaiw.utils.install.sys") as mock_sys,
            patch("ghaiw.utils.install.os.execv") as mock_execv,
            patch("ghaiw.utils.install.os.path.isabs", return_value=True),
        ):
            mock_sys.argv = ["/usr/local/bin/ghaiw", "work", "start", "42"]

            re_exec()

        mock_execv.assert_called_once_with(
            "/usr/local/bin/ghaiw",
            ["/usr/local/bin/ghaiw", "work", "start", "42"],
        )

    def test_resolves_relative_path_via_which(self) -> None:
        """When argv[0] is relative, resolve it via shutil.which before execv."""
        with (
            patch("ghaiw.utils.install.sys") as mock_sys,
            patch("ghaiw.utils.install.os.execv") as mock_execv,
            patch("ghaiw.utils.install.os.path.isabs", return_value=False),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/ghaiw"),
        ):
            mock_sys.argv = ["ghaiw", "update"]

            re_exec()

        mock_execv.assert_called_once_with(
            "/usr/local/bin/ghaiw",
            ["/usr/local/bin/ghaiw", "update"],
        )

    def test_uses_original_when_which_returns_none(self) -> None:
        """When shutil.which fails to resolve, use the original argv[0]."""
        with (
            patch("ghaiw.utils.install.sys") as mock_sys,
            patch("ghaiw.utils.install.os.execv") as mock_execv,
            patch("ghaiw.utils.install.os.path.isabs", return_value=False),
            patch("ghaiw.utils.install.shutil.which", return_value=None),
        ):
            mock_sys.argv = ["ghaiw", "check"]

            re_exec()

        mock_execv.assert_called_once_with(
            "ghaiw",
            ["ghaiw", "check"],
        )

    def test_no_extra_args(self) -> None:
        """When argv has only the executable, execv receives a single-element list."""
        with (
            patch("ghaiw.utils.install.sys") as mock_sys,
            patch("ghaiw.utils.install.os.execv") as mock_execv,
            patch("ghaiw.utils.install.os.path.isabs", return_value=True),
        ):
            mock_sys.argv = ["/usr/local/bin/ghaiw"]

            re_exec()

        mock_execv.assert_called_once_with(
            "/usr/local/bin/ghaiw",
            ["/usr/local/bin/ghaiw"],
        )

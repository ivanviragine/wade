"""Tests for self-upgrade helpers — venv detection, version parsing, upgrade, re-exec."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghaiw.utils.install import (
    get_installed_version,
    get_source_repo_path,
    get_source_version,
    get_venv_dir,
    re_exec,
    self_upgrade,
)

# ---------------------------------------------------------------------------
# get_venv_dir
# ---------------------------------------------------------------------------


class TestGetVenvDir:
    def test_returns_path_when_in_venv(self, tmp_path: Path) -> None:
        """When sys.prefix points to a directory with bin/python and pyvenv.cfg, return it."""
        # Arrange
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        # Act
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_venv_dir()

        # Assert
        assert result == tmp_path

    def test_returns_none_when_no_pyvenv_cfg(self, tmp_path: Path) -> None:
        """When pyvenv.cfg is missing, we are not in a venv."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_venv_dir()

        assert result is None

    def test_returns_none_when_no_bin_python(self, tmp_path: Path) -> None:
        """When bin/python is missing, we are not in a venv."""
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_venv_dir()

        assert result is None

    def test_returns_none_when_neither_exist(self, tmp_path: Path) -> None:
        """When both bin/python and pyvenv.cfg are missing, return None."""
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_venv_dir()

        assert result is None

    def test_returns_none_when_bin_python_is_directory(self, tmp_path: Path) -> None:
        """When bin/python exists but is a directory (not a file), return None."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").mkdir()  # directory, not file
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_venv_dir()

        assert result is None


# ---------------------------------------------------------------------------
# get_source_repo_path
# ---------------------------------------------------------------------------


class TestGetSourceRepoPath:
    def test_returns_path_from_marker_file(self, tmp_path: Path) -> None:
        """When ghaiw-source.txt exists and points to a real directory, return it."""
        # Arrange — set up a fake venv
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source_dir = tmp_path / "source-repo"
        source_dir.mkdir()
        (tmp_path / "ghaiw-source.txt").write_text(str(source_dir) + "\n")

        # Act
        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_source_repo_path()

        # Assert
        assert result == source_dir

    def test_returns_none_when_marker_missing(self, tmp_path: Path) -> None:
        """When ghaiw-source.txt does not exist, return None."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_source_repo_path()

        assert result is None

    def test_returns_none_when_source_dir_does_not_exist(self, tmp_path: Path) -> None:
        """When ghaiw-source.txt points to a non-existent directory, return None."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        nonexistent = tmp_path / "does-not-exist"
        (tmp_path / "ghaiw-source.txt").write_text(str(nonexistent) + "\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_source_repo_path()

        assert result is None

    def test_returns_none_when_not_in_venv(self, tmp_path: Path) -> None:
        """When get_venv_dir returns None, return None without reading marker."""
        with patch("ghaiw.utils.install.get_venv_dir", return_value=None):
            result = get_source_repo_path()

        assert result is None

    def test_strips_whitespace_from_marker(self, tmp_path: Path) -> None:
        """Whitespace and newlines in ghaiw-source.txt are stripped."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source_dir = tmp_path / "source-repo"
        source_dir.mkdir()
        (tmp_path / "ghaiw-source.txt").write_text(f"  {source_dir}  \n\n")

        with patch("ghaiw.utils.install.sys") as mock_sys:
            mock_sys.prefix = str(tmp_path)
            result = get_source_repo_path()

        assert result == source_dir


# ---------------------------------------------------------------------------
# get_source_version
# ---------------------------------------------------------------------------


class TestGetSourceVersion:
    def test_parses_double_quoted_version(self, tmp_path: Path) -> None:
        """Extracts version from __version__ = "X.Y.Z"."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text('"""ghaiw."""\n\n__version__ = "1.2.3"\n')

        result = get_source_version(tmp_path)

        assert result == "1.2.3"

    def test_parses_single_quoted_version(self, tmp_path: Path) -> None:
        """Extracts version from __version__ = 'X.Y.Z'."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text("__version__ = '0.9.1'\n")

        result = get_source_version(tmp_path)

        assert result == "0.9.1"

    def test_parses_version_with_extra_spaces(self, tmp_path: Path) -> None:
        """Handles extra whitespace around the = sign."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text('__version__   =   "2.0.0"\n')

        result = get_source_version(tmp_path)

        assert result == "2.0.0"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """When __init__.py does not exist, return None."""
        result = get_source_version(tmp_path)

        assert result is None

    def test_returns_none_for_missing_version_line(self, tmp_path: Path) -> None:
        """When __init__.py exists but has no __version__, return None."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text('"""ghaiw — no version here."""\n\nname = "ghaiw"\n')

        result = get_source_version(tmp_path)

        assert result is None

    def test_returns_none_for_malformed_version(self, tmp_path: Path) -> None:
        """When __version__ is present but not in a parseable format, return None."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text("__version__ = compute_version()\n")

        result = get_source_version(tmp_path)

        assert result is None

    def test_parses_prerelease_version(self, tmp_path: Path) -> None:
        """Handles pre-release versions like 1.0.0a1."""
        init_file = tmp_path / "src" / "ghaiw" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text('__version__ = "1.0.0a1"\n')

        result = get_source_version(tmp_path)

        assert result == "1.0.0a1"


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
        """The return value is always a string."""
        result = get_installed_version()

        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# self_upgrade
# ---------------------------------------------------------------------------


class TestSelfUpgrade:
    def test_uv_success(self, tmp_path: Path) -> None:
        """When uv is available and succeeds, return True without trying pip."""
        # Arrange — fake venv
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        uv_result = MagicMock(returncode=0)

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", return_value=uv_result) as mock_run,
        ):
            result = self_upgrade(source)

        # Assert
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "uv"
        assert "pip" in args
        assert "install" in args
        assert "--python" in args
        assert str(bin_dir / "python") in args
        assert str(source) in args

    def test_uv_fails_then_pip_succeeds(self, tmp_path: Path) -> None:
        """When uv fails but pip succeeds, return True."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        uv_result = MagicMock(returncode=1, stderr="uv error")
        pip_result = MagicMock(returncode=0)

        call_count = 0

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if cmd[0] == "uv":
                return uv_result
            return pip_result

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = self_upgrade(source)

        assert result is True
        assert call_count == 2

    def test_both_fail(self, tmp_path: Path) -> None:
        """When both uv and pip fail, return False."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        failed_result = MagicMock(returncode=1, stderr="install error")

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", return_value=failed_result),
        ):
            result = self_upgrade(source)

        assert result is False

    def test_no_uv_pip_succeeds(self, tmp_path: Path) -> None:
        """When uv is not available, skip uv and use pip directly."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        pip_result = MagicMock(returncode=0)

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value=None),
            patch("ghaiw.utils.install.subprocess.run", return_value=pip_result) as mock_run,
        ):
            result = self_upgrade(source)

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == str(bin_dir / "python")
        assert "-m" in args
        assert "pip" in args
        assert "install" in args
        assert str(source) in args

    def test_no_venv_returns_false(self, tmp_path: Path) -> None:
        """When not in a venv, return False immediately."""
        source = tmp_path / "source"
        source.mkdir()

        with patch("ghaiw.utils.install.get_venv_dir", return_value=None):
            result = self_upgrade(source)

        assert result is False

    def test_uv_timeout_falls_back_to_pip(self, tmp_path: Path) -> None:
        """When uv times out, fall back to pip."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        pip_result = MagicMock(returncode=0)

        call_count = 0

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if cmd[0] == "uv":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
            return pip_result

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = self_upgrade(source)

        assert result is True
        assert call_count == 2

    def test_uv_os_error_falls_back_to_pip(self, tmp_path: Path) -> None:
        """When uv raises OSError, fall back to pip."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        pip_result = MagicMock(returncode=0)

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[0] == "uv":
                raise OSError("uv binary corrupted")
            return pip_result

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = self_upgrade(source)

        assert result is True

    def test_pip_timeout_returns_false(self, tmp_path: Path) -> None:
        """When pip also times out, return False."""
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").write_text("#!/usr/bin/env python3\n")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        source = tmp_path / "source"
        source.mkdir()

        def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

        with (
            patch("ghaiw.utils.install.get_venv_dir", return_value=venv),
            patch("ghaiw.utils.install.shutil.which", return_value="/usr/local/bin/uv"),
            patch("ghaiw.utils.install.subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = self_upgrade(source)

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

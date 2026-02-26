"""Self-upgrade helpers — detect source repo, compare versions, re-exec.

Used by `ghaiw update` to upgrade the installed venv from the local source
before updating project-level files. Editable (dev) installs skip this
automatically because they don't have a `ghaiw-source.txt` marker.

Behavioral reference: N/A — new in Python version.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger()


def get_venv_dir() -> Path | None:
    """Discover the venv directory from sys.prefix.

    Returns None if we're not running inside a venv.
    """
    # sys.prefix points to the venv root when activated
    prefix = Path(sys.prefix)
    if (prefix / "bin" / "python").is_file() and (prefix / "pyvenv.cfg").is_file():
        return prefix
    return None


def get_source_repo_path() -> Path | None:
    """Read the source repository path from the venv's ghaiw-source.txt.

    This file is written by install.sh after `uv pip install`.
    Returns None for editable installs or when the file doesn't exist.
    """
    venv_dir = get_venv_dir()
    if not venv_dir:
        return None

    marker = venv_dir / "ghaiw-source.txt"
    if not marker.is_file():
        return None

    source_path = Path(marker.read_text(encoding="utf-8").strip())
    if source_path.is_dir():
        return source_path
    return None


def get_source_version(source: Path) -> str | None:
    """Read __version__ from the source repo's __init__.py.

    Args:
        source: Root of the source repository (contains src/ghaiw/__init__.py).

    Returns:
        The version string, or None if it can't be parsed.
    """
    init_file = source / "src" / "ghaiw" / "__init__.py"
    if not init_file.is_file():
        return None

    text = init_file.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def get_installed_version() -> str:
    """Get the currently installed version."""
    from ghaiw import __version__

    return __version__


def self_upgrade(source: Path) -> bool:
    """Reinstall ghaiw from source into the current venv.

    Tries uv first, falls back to pip.

    Args:
        source: Root of the source repository.

    Returns:
        True if the upgrade succeeded.
    """
    venv_dir = get_venv_dir()
    if not venv_dir:
        logger.warning("self_upgrade.no_venv")
        return False

    python = str(venv_dir / "bin" / "python")

    # Try uv first (faster)
    if shutil.which("uv"):
        try:
            result = subprocess.run(
                ["uv", "pip", "install", "--python", python, str(source)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("self_upgrade.success", method="uv", source=str(source))
                return True
            logger.warning("self_upgrade.uv_failed", stderr=result.stderr[:200])
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("self_upgrade.uv_error", error=str(e))

    # Fallback to pip
    try:
        result = subprocess.run(
            [python, "-m", "pip", "install", str(source)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("self_upgrade.success", method="pip", source=str(source))
            return True
        logger.warning("self_upgrade.pip_failed", stderr=result.stderr[:200])
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("self_upgrade.pip_error", error=str(e))

    return False


def re_exec() -> None:
    """Re-execute the current process with the same arguments.

    This replaces the current process image, so the newly installed code
    is loaded. Does not return.
    """
    executable = sys.argv[0]

    # If argv[0] is a relative path, resolve it
    if not os.path.isabs(executable):
        resolved = shutil.which(executable)
        if resolved:
            executable = resolved

    logger.info("self_upgrade.re_exec", executable=executable, args=sys.argv[1:])
    os.execv(executable, [executable, *sys.argv[1:]])

"""Self-upgrade helpers — detect install method, upgrade, re-exec.

Used by `ghaiw update` to upgrade the installed binary before updating
project-level files. Editable (dev) installs skip this automatically.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from enum import StrEnum

import structlog

logger = structlog.get_logger()


class InstallMethod(StrEnum):
    """How ghaiw was installed on this machine."""

    UV_TOOL = "uv-tool"  # uv tool install ghaiw
    PIPX = "pipx"  # pipx install ghaiw
    BREW = "brew"  # brew install ghaiw
    EDITABLE = "editable"  # uv pip install -e (dev)
    UNKNOWN = "unknown"


def detect_install_method() -> InstallMethod:
    """Infer how ghaiw was installed by inspecting sys.executable."""
    exe = sys.executable

    if "/.local/share/uv/tools/" in exe or "\\.local\\share\\uv\\tools\\" in exe:
        return InstallMethod.UV_TOOL
    if "/.local/pipx/venvs/" in exe or "\\pipx\\venvs\\" in exe:
        return InstallMethod.PIPX
    if "/homebrew/" in exe.lower() or "/cellar/" in exe.lower():
        return InstallMethod.BREW
    if "/.venv/" in exe or "/venv/" in exe:
        return InstallMethod.EDITABLE

    return InstallMethod.UNKNOWN


def get_installed_version() -> str:
    """Get the currently installed version."""
    from ghaiw import __version__

    return __version__


def self_upgrade() -> bool:
    """Upgrade ghaiw using the appropriate package manager.

    Detects install method and runs the matching upgrade command.
    Returns True if the upgrade succeeded, False otherwise.
    """
    method = detect_install_method()

    if method == InstallMethod.UV_TOOL:
        return _run_upgrade(["uv", "tool", "upgrade", "ghaiw"], method="uv-tool")
    if method == InstallMethod.PIPX:
        return _run_upgrade(["pipx", "upgrade", "ghaiw"], method="pipx")
    if method == InstallMethod.BREW:
        return _run_upgrade(["brew", "upgrade", "ghaiw"], method="brew")

    # Editable or unknown — nothing to do
    logger.info("self_upgrade.skipped", method=str(method))
    return False


def _run_upgrade(cmd: list[str], *, method: str) -> bool:
    """Run an upgrade command. Returns True on success."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("self_upgrade.success", method=method)
            return True
        logger.warning("self_upgrade.failed", method=method, stderr=result.stderr[:200])
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("self_upgrade.error", method=method, error=str(e))
    return False


def re_exec() -> None:
    """Re-execute the current process with the same arguments.

    Replaces the current process image so the newly installed code is loaded.
    Does not return.
    """
    executable = sys.argv[0]

    if not os.path.isabs(executable):
        resolved = shutil.which(executable)
        if resolved:
            executable = resolved

    logger.info("self_upgrade.re_exec", executable=executable, args=sys.argv[1:])
    os.execv(executable, [executable, *sys.argv[1:]])

"""Self-upgrade helpers — detect install method, upgrade, re-exec.

Used by `wade update` to upgrade the installed binary before updating
project-level files. Editable (dev) installs skip this automatically.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from enum import StrEnum
from pathlib import Path

import structlog

logger = structlog.get_logger()


class InstallMethod(StrEnum):
    """How wade was installed on this machine."""

    UV_TOOL = "uv-tool"  # uv tool install wade-cli
    PIPX = "pipx"  # pipx install wade-cli
    BREW = "brew"  # brew install wade
    EDITABLE = "editable"  # uv pip install -e (dev)
    UNKNOWN = "unknown"


PACKAGE_NAME = "wade-cli"
LEGACY_PACKAGE_NAME = "wade"


def _package_name_for_self_upgrade() -> str:
    """Return the package name that matches the current install root."""
    exe = sys.executable.replace("\\", "/")
    if "/.local/share/uv/tools/wade/" in exe or "/.local/pipx/venvs/wade/" in exe:
        return LEGACY_PACKAGE_NAME
    return PACKAGE_NAME


def _is_editable_uv_tool(exe: str) -> bool:
    """Return True if the uv-tool env at ``exe`` was installed with --editable.

    `uv tool upgrade` is a no-op against an editable install — it just
    re-resolves the local source path, which reports the same version until
    that source changes — so treating it as a normal UV_TOOL would make
    self-upgrade silently do nothing forever. The tool env's uv-receipt.toml
    records an `editable = "<path>"` key on the requirement for such installs.
    """
    receipt = Path(exe).parent.parent / "uv-receipt.toml"
    try:
        with receipt.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False

    requirements = data.get("tool", {}).get("requirements", [])
    return any(isinstance(req, dict) and "editable" in req for req in requirements)


def detect_install_method() -> InstallMethod:
    """Infer how wade was installed by inspecting sys.executable."""
    exe = sys.executable

    if "/.local/share/uv/tools/" in exe or "\\.local\\share\\uv\\tools\\" in exe:
        return InstallMethod.EDITABLE if _is_editable_uv_tool(exe) else InstallMethod.UV_TOOL
    if "/.local/pipx/venvs/" in exe or "\\pipx\\venvs\\" in exe:
        return InstallMethod.PIPX
    if "/homebrew/" in exe.lower() or "/cellar/" in exe.lower():
        return InstallMethod.BREW
    if "/.venv/" in exe or "/venv/" in exe:
        return InstallMethod.EDITABLE

    return InstallMethod.UNKNOWN


def get_installed_version() -> str:
    """Get the currently installed version."""
    from wade import __version__

    return __version__


def self_upgrade() -> bool:
    """Upgrade wade using the appropriate package manager.

    Detects install method and runs the matching upgrade command.
    Returns True if the upgrade succeeded, False otherwise.
    """
    method = detect_install_method()
    package_name = _package_name_for_self_upgrade()

    if method == InstallMethod.UV_TOOL:
        return _run_upgrade(["uv", "tool", "upgrade", package_name], method="uv-tool")
    if method == InstallMethod.PIPX:
        return _run_upgrade(["pipx", "upgrade", package_name], method="pipx")
    if method == InstallMethod.BREW:
        return _run_upgrade(["brew", "upgrade", "wade"], method="brew")

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

    Uses os.execv (not execve), so the replacement process inherits the
    calling process's environment unchanged — callers rely on this to pass
    state across the re-exec (e.g. init_service._maybe_self_upgrade sets
    WADE_SELF_UPGRADE_FROM beforehand to guard against re-exec loops).
    """
    executable = sys.argv[0]

    if not os.path.isabs(executable):
        resolved = shutil.which(executable)
        if resolved:
            executable = resolved

    logger.info("self_upgrade.re_exec", executable=executable, args=sys.argv[1:])
    os.execv(executable, [executable, *sys.argv[1:]])

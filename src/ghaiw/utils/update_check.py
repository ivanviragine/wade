"""Background update check — poll PyPI for newer ghaiw releases.

Checks are cached for 2 hours to avoid network overhead on every command.
All errors are silently swallowed; this must never disrupt user workflows.

Usage (called via atexit in cli/main.py):
    atexit.register(maybe_print_update_hint, __version__, ctx.invoked_subcommand)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import structlog

logger = structlog.get_logger()

CHECK_INTERVAL_SECS = 7200  # 2 hours
PYPI_URL = "https://pypi.org/pypi/ghaiw/json"
CACHE_FILE = Path.home() / ".local" / "share" / "ghaiw" / "update-check.json"


def _is_newer(candidate: str, current: str) -> bool:
    """Return True if candidate version is strictly newer than current.

    Uses simple numeric tuple comparison — sufficient for standard semver.
    Falls back to False on any parse error.
    """
    try:

        def to_tuple(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())

        return to_tuple(candidate) > to_tuple(current)
    except (ValueError, TypeError):
        return False


def _load_cache() -> dict[str, object] | None:
    """Load cached check result. Returns None if missing or malformed."""
    try:
        if CACHE_FILE.is_file():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "timestamp" in data and "latest_version" in data:
                return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _save_cache(latest_version: str, timestamp: float) -> None:
    """Write cache file atomically. Silently ignores errors."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"timestamp": timestamp, "latest_version": latest_version}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _fetch_latest_version() -> str | None:
    """Fetch the latest ghaiw version from PyPI. Returns None on any error."""
    try:
        with urlopen(PYPI_URL, timeout=5) as resp:
            data = json.loads(resp.read())
            version = data["info"]["version"]
            if isinstance(version, str):
                return version
    except (URLError, OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def check_for_update(current_version: str) -> str | None:
    """Return latest PyPI version if newer than current, else None.

    Uses a 2-hour file cache to avoid hammering PyPI.

    Args:
        current_version: The currently installed version string.

    Returns:
        The latest version string if an update is available, otherwise None.
    """
    now = datetime.now(UTC).timestamp()

    cached = _load_cache()
    if cached is not None:
        age = now - float(str(cached["timestamp"]))
        if age < CHECK_INTERVAL_SECS:
            latest = str(cached["latest_version"])
            return latest if _is_newer(latest, current_version) else None

    fetched = _fetch_latest_version()
    if fetched:
        _save_cache(fetched, now)
        return fetched if _is_newer(fetched, current_version) else None

    return None


def maybe_print_update_hint(current_version: str, invoked_subcommand: str | None) -> None:
    """Print an update nag to stderr if a newer version is available.

    Suppressed when:
    - GHAIW_NO_UPDATE_CHECK=1 is set
    - stderr is not a TTY (CI / piped output)
    - the invoked subcommand is "update" (avoid recursive nag)

    Safe to register with atexit — all exceptions are swallowed.
    """
    try:
        if os.environ.get("GHAIW_NO_UPDATE_CHECK"):
            return
        if invoked_subcommand == "update":
            return
        if not sys.stderr.isatty():
            return

        latest = check_for_update(current_version)
        if latest:
            msg = (
                f"\n  ghaiw {latest} is available"
                f" (you have {current_version}).  Run: ghaiw update\n"
            )
            print(msg, file=sys.stderr)
    except Exception:
        logger.debug("update_check.error", exc_info=True)

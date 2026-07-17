"""Init service auth helpers — gh auth check, ClickUp token validation, .env save.

Small provider-authentication utilities used by the provider-setup wizard. Leaf
module — imports nothing from siblings.
"""

from __future__ import annotations

from pathlib import Path

from wade.ui.console import console

__all__ = [
    "_check_gh_auth",
    "_save_token_to_env",
    "_validate_clickup_token",
]


def _check_gh_auth() -> bool:
    """Check whether the ``gh`` CLI is authenticated."""
    import subprocess

    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _validate_clickup_token(token: str) -> bool:
    """Validate a ClickUp API token by hitting the authenticated user endpoint."""
    from wade.utils.http import HTTPClient

    try:
        with HTTPClient(
            base_url="https://api.clickup.com",
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=10.0,
        ) as client:
            client.get("/api/v2/user")
        return True
    except Exception:
        return False


def _save_token_to_env(
    project_root: Path,
    env_var: str,
    token: str,
) -> bool:
    """Append a token to the project's ``.env`` file (skip if already present).

    Returns ``True`` when the file was written (or already had the var),
    ``False`` on write failure.
    """
    import re

    env_path = project_root / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(env_var)}=", content, re.MULTILINE):
            console.info(f"{env_var} already present in .env")
            return True
    else:
        content = ""

    try:
        with env_path.open("a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(f"{env_var}={token}\n")
    except OSError as exc:
        console.warn(f"Could not write to .env: {exc}")
        return False

    console.success(f"Token saved to .env as {env_var}")

    # Check if .env is gitignored
    gitignore_path = project_root / ".gitignore"
    env_ignored = False
    if gitignore_path.exists():
        for line in gitignore_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped in (".env", ".env/", "/.env"):
                env_ignored = True
                break
    if not env_ignored:
        console.hint("Consider adding .env to your .gitignore to avoid committing secrets")
    return True

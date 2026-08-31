"""Packaged template-asset resolution — prompt, skill, and git-hook templates."""

from __future__ import annotations

from pathlib import Path


def get_wade_repo_root() -> Path:
    """Get the wade package repository root (for self-init detection).

    Works in both editable installs (dev) and regular installs.
    """
    return Path(__file__).parent.parent.parent.parent


def get_templates_dir() -> Path:
    """Get the path to the templates directory.

    Looks in two places:
    1. Repo root (development / editable install) — templates/ next to src/
    2. Inside the installed package (pip install) — wade/templates/
    """
    # 1. Dev mode: walk up from src/wade/utils/templates.py → repo root
    repo_root = get_wade_repo_root()
    dev_templates = repo_root / "templates"
    if dev_templates.is_dir() and (dev_templates / "skills").is_dir():
        return dev_templates

    # 2. Installed package: templates are force-included as wade/templates/
    import importlib.resources

    pkg_templates = importlib.resources.files("wade").joinpath("templates")
    pkg_path = Path(str(pkg_templates))
    if pkg_path.is_dir():
        return pkg_path

    # Last resort — return the dev path (will trigger "not found" warning)
    return dev_templates


def get_skills_templates_dir() -> Path:
    """Get the path to the skill templates directory."""
    return get_templates_dir() / "skills"


def get_workflows_templates_dir() -> Path:
    """Get the path to the fixed workflow templates directory."""

    return get_templates_dir() / "workflows"


def load_prompt_template(name: str) -> str:
    """Load a prompt template by name from templates/prompts/.

    Args:
        name: Template filename (e.g. "review-plan.md").

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    template = get_templates_dir() / "prompts" / name
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8").strip()


def load_workflow_template(name: str) -> str:
    """Load a fixed workflow template by name from ``templates/workflows``."""

    template = get_workflows_templates_dir() / name
    if not template.is_file():
        raise FileNotFoundError(f"Workflow template not found: {template}")
    return template.read_text(encoding="utf-8").strip()


def load_hook_template(name: str) -> str:
    """Load a git-hook script template by name from templates/hooks/.

    Unlike :func:`load_prompt_template`, the content is returned verbatim (no
    ``.strip()``) — a shell script's leading shebang and trailing newline matter.

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    template = get_templates_dir() / "hooks" / name
    if not template.is_file():
        raise FileNotFoundError(f"Hook template not found: {template}")
    return template.read_text(encoding="utf-8")

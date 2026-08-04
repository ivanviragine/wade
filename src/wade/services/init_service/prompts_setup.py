"""Init service setup prompts — provider, project, hooks, knowledge, commands.

Interactive wizard sections for the non-AI-tool parts of ``wade init``. Imports
auth helpers, ``_resolve_models``/``_COMMAND_OVERRIDE_NAMES`` from ``config_io``,
model prompts from ``prompts_ai``, and the statusline prompt from ``shell``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from crossby.ai_tools import AbstractAITool

from wade.git import repo
from wade.models.config import ComplexityModelMapping, ProjectSettings
from wade.services.ai_resolution import valid_effort_levels
from wade.services.init_service.auth import (
    _check_gh_auth,
    _save_token_to_env,
    _validate_clickup_token,
)
from wade.services.init_service.config_io import _COMMAND_OVERRIDE_NAMES, _resolve_models
from wade.services.init_service.prompts_ai import (
    _collect_model_options,
    _prompt_model_mapping,
    _select_or_skip,
    _suggest_model_for_tool,
)
from wade.services.init_service.shell import _prompt_configure_statusline
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_prompt_claude_code_settings",
    "_prompt_command_overrides",
    "_prompt_configure_completions",
    "_prompt_hooks_setup",
    "_prompt_implementation_setup",
    "_prompt_knowledge_setup",
    "_prompt_project_settings",
    "_prompt_provider_setup",
]


def _prompt_configure_completions(non_interactive: bool) -> None:
    """Prompt user to install CLI autocompletions."""
    import subprocess
    import sys

    from wade.ui import prompts

    if non_interactive or not prompts.is_tty():
        return

    if prompts.confirm("Install CLI autocompletion for wade?", default=True):
        try:
            subprocess.run(
                [sys.executable, "-m", "wade", "--install-completion"],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            console.success("Installed CLI autocompletion")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning("init.completion_failed", error=getattr(exc, "stderr", None))
            console.warn("Could not install CLI autocompletion")


def _prompt_claude_code_settings(non_interactive: bool) -> None:
    """Prompt for Claude Code-specific settings: statusline."""
    import contextlib
    import json

    settings_path = Path.home() / ".claude" / "settings.json"
    statusline_done = False
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "statusLine" in raw:
                statusline_done = True
    if statusline_done:
        return

    if not non_interactive:
        console.rule("Claude Code")
    _prompt_configure_statusline(non_interactive)


def _prompt_provider_setup(
    project_root: Path,
    non_interactive: bool,
    current_provider: str | None = None,
    current_api_token_env: str | None = None,
    current_settings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Collect task provider selection and authentication setup.

    Returns a dict with keys: name, api_token_env (optional),
    settings (optional), add_env_to_copy (optional).
    """
    import subprocess
    import webbrowser

    from wade.ui import prompts

    default_result: dict[str, Any] = {"name": "github"}

    if non_interactive:
        return default_result

    console.rule("Provider")

    providers = ["GitHub Issues", "ClickUp", "Markdown file"]
    hints = ["gh CLI", "API token", "Local .md file"]
    default_idx = 0
    if current_provider == "clickup":
        default_idx = 1
    elif current_provider == "markdown":
        default_idx = 2

    idx = prompts.select("Task management provider", providers, default=default_idx, hints=hints)

    # --- Markdown path ---
    if idx == 2:
        current_settings = current_settings or {}
        path = (
            prompts.input_prompt(
                "Path to issues markdown file",
                default=current_settings.get("path", "ISSUES.md"),
            ).strip()
            or "ISSUES.md"
        )
        # File creation is deferred to the post-wizard write phase so
        # "Modify" / "Cancel" doesn't leave stray files behind. See
        # _ensure_markdown_file() invoked alongside the config write.
        return {"name": "markdown", "settings": {"path": path}}

    # --- GitHub path ---
    if idx == 0:
        if _check_gh_auth():
            console.success("GitHub CLI authenticated")
        else:
            console.warn("GitHub CLI is not authenticated")
            if prompts.confirm("Set up GitHub authentication now?", default=True):
                console.info("Running gh auth login...")
                try:
                    subprocess.run(["gh", "auth", "login"], check=False)
                except FileNotFoundError:
                    console.error_with_fix(
                        "gh CLI is not installed",
                        "Install it from https://cli.github.com/",
                    )
                    return {"name": "github"}
                if _check_gh_auth():
                    console.success("GitHub CLI authenticated")
                else:
                    console.hint("Run 'gh auth login' before using wade commands")
            else:
                console.hint("Run 'gh auth login' or set GH_TOKEN before using wade commands")
        return {"name": "github"}

    # --- ClickUp path ---
    console.info("To get your ClickUp API token:")
    console.detail("1. Go to https://app.clickup.com/settings/apps")
    console.detail('2. Under "API Token", click Generate')
    console.detail("3. Copy the token (starts with pk_)")

    if prompts.confirm("Open ClickUp settings in browser?", default=True):
        webbrowser.open("https://app.clickup.com/settings/apps")

    _cur_settings = current_settings or {}

    # Prompt for token with validation
    token = ""
    token_validated = False
    for attempt in range(3):
        token = prompts.input_prompt("ClickUp API token").strip()
        if not token:
            if attempt < 2:
                console.warn("Token cannot be empty — try again")
            continue
        if _validate_clickup_token(token):
            console.success("ClickUp token validated")
            token_validated = True
            break
        if attempt < 2:
            console.warn("Token validation failed — check the token and try again")
        else:
            console.warn("Token validation failed — continuing anyway")

    if not token and not token_validated:
        console.error("No token provided — falling back to GitHub")
        return {"name": "github"}

    # Env var name — validate format
    import re

    env_var = ""
    while not env_var:
        candidate = prompts.input_prompt(
            "Environment variable name for the token",
            default=current_api_token_env or "CLICKUP_API_TOKEN",
        )
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            env_var = candidate
        else:
            console.warn("Invalid env var name — use letters, digits, and underscores only")

    # Offer to save to .env
    add_env_to_copy = False
    if (
        token
        and prompts.confirm("Save token to .env file? (recommended — never committed to git)")
        and _save_token_to_env(project_root, env_var, token)
    ):
        add_env_to_copy = True

    # Required IDs — re-prompt until non-empty
    team_id = ""
    while not team_id:
        team_id = prompts.input_prompt(
            "ClickUp team/workspace ID", default=_cur_settings.get("team_id", "")
        ).strip()
        if not team_id:
            console.warn("Team/workspace ID is required")
    list_id = ""
    while not list_id:
        list_id = prompts.input_prompt(
            "ClickUp list ID", default=_cur_settings.get("list_id", "")
        ).strip()
        if not list_id:
            console.warn("List ID is required")

    # Optional space ID
    space_id = prompts.input_prompt(
        "ClickUp space ID",
        default=_cur_settings.get("space_id", ""),
        allow_empty=True,
    ).strip()

    settings: dict[str, str] = {"team_id": team_id, "list_id": list_id}
    if space_id:
        settings["space_id"] = space_id

    return {
        "name": "clickup",
        "api_token_env": env_var,
        "settings": settings,
        "add_env_to_copy": add_env_to_copy,
    }


def _prompt_project_settings(
    project_root: Path,
    non_interactive: bool,
    *,
    current_main_branch: str | None = None,
    current_branch_prefix: str | None = None,
    current_issue_label: str | None = None,
    current_worktrees_dir: str | None = None,
) -> ProjectSettings:
    """Collect project settings — interactively or with defaults.

    Auto-detects the main branch via git. Returns a :class:`ProjectSettings`
    model; ``merge_strategy`` is always ``PR`` (``direct`` retired in #357), so
    it defaults from the model and is never prompted for.
    """
    from wade.ui import prompts

    # Auto-detect main branch (works in both modes)
    try:
        main_branch = repo.detect_main_branch(project_root)
    except Exception:
        logger.debug("init.main_branch_detect_failed", exc_info=True)
        main_branch = "main"

    branch_prefix = current_branch_prefix or "feat"
    issue_label = current_issue_label or "feature-plan"
    worktrees_dir = current_worktrees_dir or "../.worktrees"

    if not non_interactive:
        console.rule("Project")
        branch_prefix = prompts.input_prompt("Branch prefix", branch_prefix)
        issue_label = prompts.input_prompt("Issue label", issue_label)
        worktrees_dir = prompts.input_prompt("Worktrees directory", worktrees_dir)

    # merge_strategy defaults to PR in the model (``direct`` retired in #357).
    return ProjectSettings(
        main_branch=current_main_branch or main_branch,
        branch_prefix=branch_prefix,
        issue_label=issue_label,
        worktrees_dir=worktrees_dir,
    )


def _prompt_hooks_setup(
    non_interactive: bool,
    *,
    current_post_worktree_create: str | None = None,
    current_copy_to_worktree: list[str] | None = None,
) -> dict[str, Any]:
    """Collect worktree hooks settings — setup script and files to copy.

    Returns a dict with keys: post_worktree_create (str | None), copy_to_worktree (list[str]).
    """
    from wade.ui import prompts

    defaults: dict[str, Any] = {
        "post_worktree_create": current_post_worktree_create,
        "copy_to_worktree": current_copy_to_worktree or [],
    }

    if non_interactive:
        return defaults

    console.rule("Worktree hooks")

    script_path = prompts.input_prompt(
        "Setup script for new worktrees (e.g. scripts/setup-worktree.sh)",
        default=current_post_worktree_create or "",
        allow_empty=True,
    )
    defaults["post_worktree_create"] = script_path.strip() or None

    current_copy_str = ", ".join(current_copy_to_worktree or [])
    copy_files = prompts.input_prompt(
        "Files to copy into worktrees (comma-separated, e.g. .env)",
        default=current_copy_str,
        allow_empty=True,
    )
    if copy_files.strip():
        defaults["copy_to_worktree"] = [f.strip() for f in copy_files.split(",") if f.strip()]
    else:
        defaults["copy_to_worktree"] = []

    return defaults


def _prompt_knowledge_setup(
    non_interactive: bool,
    *,
    current_enabled: bool = False,
    current_path: str = "KNOWLEDGE.md",
) -> dict[str, Any]:
    """Collect knowledge file settings — opt-in feature for cross-session learning.

    Returns a dict with keys: enabled (bool), path (str).
    """
    from wade.ui import prompts

    defaults: dict[str, Any] = {
        "enabled": current_enabled,
        "path": current_path,
    }

    if non_interactive:
        return defaults

    console.rule("Project knowledge")

    enabled = prompts.confirm(
        "Enable project knowledge file for cross-session AI learning?", default=current_enabled
    )
    if not enabled:
        defaults["enabled"] = False
        return defaults

    defaults["enabled"] = True
    path = prompts.input_prompt(
        "Knowledge file path",
        default=current_path,
        allow_empty=False,
    )
    if path.strip():
        defaults["path"] = path.strip()

    return defaults


def _prompt_implementation_setup(
    default_tool: str | None,
    installed_tools: list[str],
    non_interactive: bool,
    *,
    current_implement_tool: str | None = None,
    current_model_mapping: ComplexityModelMapping | None = None,
    current_effective_tool: str | None = None,
) -> dict[str, Any]:
    """Prompt for implementation tool and per-complexity model overrides.

    The default tool and default model are set in the AI section. This section
    only handles implementation-specific overrides that fall back to those defaults.

    Returns a dict with keys:
        ``tool``          - implement-specific tool override, or None (use default_tool)
        ``model_mapping`` - ComplexityModelMapping for the effective tool
    """
    if non_interactive:
        mapping = current_model_mapping or _resolve_models(default_tool)
        return {"tool": None, "model_mapping": mapping}

    console.rule("Implementation")

    base_tools = installed_tools if installed_tools else ["claude", "copilot", "antigravity-cli"]
    implement_tool = _select_or_skip(
        "AI tool for implementation work", base_tools, current_implement_tool
    )

    current_effective = implement_tool or default_tool
    # Discard stale mapping when the effective tool has changed since last pass.
    cached = current_model_mapping if current_effective == current_effective_tool else None
    mapping = cached or _resolve_models(current_effective)
    mapping = _prompt_model_mapping(current_effective, mapping, non_interactive=False)

    return {"tool": implement_tool, "model_mapping": mapping}


def _prompt_command_overrides(
    installed_tools: list[str],
    non_interactive: bool,
    default_model: str | None = None,
    default_tool: str | None = None,
    current_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Prompt for per-command AI tool, model, effort, and yolo overrides.

    Implementation configuration is handled separately by ``_prompt_implementation_setup()``.

    Returns a dict like:
        {"plan": {"tool": "claude", "model": "...", "effort": "high", "yolo": "true"},
         "deps": {},
         "review_plan": {"enabled": "true", "mode": "prompt"},
         "review_implementation": {"enabled": "false"},
         "review_batch": {"enabled": "true", "mode": "interactive"}}

    Empty dicts for commands with no overrides.
    Review commands include an "enabled" key ("true"/"false" as strings).
    Effort and yolo values are stored as strings for uniform serialization.
    """
    from wade.ui import prompts

    if non_interactive:
        return {cmd_name: {} for cmd_name in _COMMAND_OVERRIDE_NAMES}

    current = current_overrides or {}

    # Build selectable list: installed tools + "Skip (use default)"
    skip_label = "Skip (use default)"
    tool_options = (
        installed_tools if installed_tools else ["claude", "copilot", "antigravity-cli"]
    ) + [skip_label]

    cmd_triples = [
        ("plan", "AI tool", "Planning"),
        ("deps", "AI tool", "Dependency analysis"),
        ("review_plan", "AI tool", "Plan review"),
        ("review_implementation", "AI tool", "Implementation review"),
        ("review_batch", "AI tool", "Batch review"),
    ]
    result: dict[str, dict[str, Any]] = {cmd_name: {} for cmd_name in _COMMAND_OVERRIDE_NAMES}
    tool_for_cmd: list[str | None] = [None] * len(cmd_triples)

    def _ask_effort_and_permission_mode(
        cmd_name: str, effective_tool: str | None, *, skip_autonomy: bool = False
    ) -> None:
        """Prompt for per-command effort and permission-mode overrides (capability-gated).

        skip_autonomy suppresses the autonomy prompt for headless commands,
        which run at ``default`` (no write permissions needed) regardless.
        """
        if not effective_tool:
            return
        try:
            adapter = AbstractAITool.get(effective_tool)
            caps = adapter.capabilities()
        except (ValueError, KeyError):
            return

        current_cmd = current.get(cmd_name, {})

        if caps.supports_effort:
            effort_choices = [
                "Skip (inherit defaults)",
                *[e.value for e in valid_effort_levels(effective_tool)],
            ]
            current_effort = current_cmd.get("effort")
            effort_default_idx = 0
            if current_effort and current_effort in effort_choices:
                effort_default_idx = effort_choices.index(current_effort)
            effort_idx = prompts.select(
                "  Effort level", effort_choices, default=effort_default_idx
            )
            if effort_idx > 0:
                result[cmd_name]["effort"] = effort_choices[effort_idx]

        if not skip_autonomy:
            # Offer default plus each autonomy tier the tool supports. crossby
            # owns downgrades at launch, but gating the menu avoids offering a
            # tier the user picks only to see it silently downgraded.
            tiers = ["default"]
            if caps.supports_accept_edits:
                tiers.append("accept-edits")
            if caps.supports_auto:
                tiers.append("auto")
            if caps.supports_yolo:
                tiers.append("yolo")

            if len(tiers) > 1:
                # Seed the default from an existing permission_mode, falling back
                # to the legacy yolo alias (yolo: true → yolo tier).
                current_pm = current_cmd.get("permission_mode")
                if current_pm is None and (
                    current_cmd.get("yolo") == "true" or current_cmd.get("yolo") is True
                ):
                    current_pm = "yolo"

                pm_choices = ["Skip (use default)", *tiers]
                pm_default = tiers.index(current_pm) + 1 if current_pm in tiers else 0
                pm_idx = prompts.select(
                    "  Permission (autonomy) mode for this command?",
                    pm_choices,
                    default=pm_default,
                )
                if pm_idx > 0:
                    result[cmd_name]["permission_mode"] = tiers[pm_idx - 1]
                # idx == 0 means Skip — omit to inherit the global setting

    def _ask_tool_and_model(
        cmd_idx: int,
        cmd_name: str,
        prompt_label: str,
        section: str,
        *,
        allow_skip: bool = True,
    ) -> None:
        """Ask for AI tool and model, updating result and tool_for_cmd in place."""
        current_cmd = current.get(cmd_name, {})
        selectable_tools = (
            tool_options if allow_skip else [t for t in tool_options if t != skip_label]
        )
        # Pre-select current tool if present
        tool_default_idx = len(selectable_tools) - 1 if allow_skip else 0
        current_tool_val = current_cmd.get("tool")
        if current_tool_val and current_tool_val in selectable_tools:
            tool_default_idx = selectable_tools.index(current_tool_val)

        idx = prompts.select(prompt_label, selectable_tools, default=tool_default_idx)
        selected_tool = selectable_tools[idx]
        tool_for_cmd[cmd_idx] = None if selected_tool == skip_label else selected_tool

        if tool_for_cmd[cmd_idx] is not None:
            result[cmd_name]["tool"] = selected_tool
            maybe_tool = selected_tool

            available = _collect_model_options(maybe_tool)
            suggested = _suggest_model_for_tool(maybe_tool)
            current_model_val = current_cmd.get("model")
            if current_model_val and current_model_val in available:
                model_default = current_model_val
            elif default_model and default_model in available:
                model_default = default_model
            else:
                model_default = suggested

            custom_label = "Custom..."
            skip_model_label = "Skip (use default)"
            model_options = list(available)
            if model_default and model_default not in model_options:
                model_options.insert(0, model_default)
            model_options += [custom_label, skip_model_label]

            model_default_idx = (
                model_options.index(model_default) if model_default in model_options else 0
            )
            chosen_idx = prompts.select(
                f"  Model for {section.lower()}", model_options, default=model_default_idx
            )

            chosen = model_options[chosen_idx]
            if chosen == custom_label:
                chosen = prompts.input_prompt(
                    f"  Model for {section.lower()} (model ID)", model_default
                )
            if chosen and chosen != skip_model_label:
                result[cmd_name]["model"] = chosen

    for cmd_idx, (cmd_name, prompt_label, section) in enumerate(cmd_triples):
        console.rule(section)
        current_cmd = current.get(cmd_name, {})

        if cmd_name == "plan":
            result[cmd_name] = {}
            _ask_tool_and_model(cmd_idx, cmd_name, prompt_label, section)
            effective_tool = tool_for_cmd[cmd_idx] or default_tool
            if effective_tool:
                _ask_effort_and_permission_mode(cmd_name, effective_tool)

        elif cmd_name.startswith("review_"):
            # 1. Enable?
            current_enabled = current_cmd.get("enabled")
            enabled_default = 0 if current_enabled != "false" else 1
            enable_idx = prompts.select(
                f"Enable {section.lower()}?",
                ["Yes", "No"],
                default=enabled_default,
            )
            if enable_idx == 1:
                result[cmd_name] = {"enabled": "false"}
                continue
            result[cmd_name] = {"enabled": "true"}

            # 2. Delegation mode (before tool/model — if "prompt", no AI needed)
            mode_options = [
                "prompt (self-review)",
                "headless (AI one-shot)",
                "interactive (AI session)",
            ]
            mode_values = ["prompt", "headless", "interactive"]
            current_mode = current_cmd.get("mode")
            if current_mode and current_mode in mode_values:
                default_mode_idx = mode_values.index(current_mode)
            else:
                default_mode_idx = 2 if cmd_name == "review_batch" else 0
            mode_idx = prompts.select(
                f"  Delegation mode for {section.lower()}",
                mode_options,
                default=default_mode_idx,
            )
            mode = mode_values[mode_idx]
            result[cmd_name]["mode"] = mode

            # 3. Tool, model, effort, and (for interactive) yolo — only for AI-backed modes.
            # When no default_tool is configured, skip is not a valid choice —
            # a concrete tool must be selected or the resulting config would have
            # no resolvable AI tool for headless/interactive execution.
            if mode != "prompt":
                _ask_tool_and_model(
                    cmd_idx,
                    cmd_name,
                    prompt_label,
                    section,
                    allow_skip=default_tool is not None,
                )
                effective_tool = tool_for_cmd[cmd_idx] or default_tool
                if effective_tool:
                    _ask_effort_and_permission_mode(
                        cmd_name, effective_tool, skip_autonomy=(mode == "headless")
                    )

        elif cmd_name == "deps":
            result[cmd_name] = {}

            # 1. Tool and model
            _ask_tool_and_model(cmd_idx, cmd_name, prompt_label, section)

            effective_tool = tool_for_cmd[cmd_idx] or default_tool
            if effective_tool:
                # 2. Mode — ask before yolo so yolo can be gated (headless skips yolo)
                deps_mode_options = [
                    "headless (AI one-shot)",
                    "interactive (AI session)",
                ]
                deps_mode_values = ["headless", "interactive"]
                current_mode = current_cmd.get("mode")
                deps_mode_default = 0
                if current_mode and current_mode in deps_mode_values:
                    deps_mode_default = deps_mode_values.index(current_mode)
                mode_idx = prompts.select(
                    f"  Delegation mode for {section.lower()}",
                    deps_mode_options,
                    default=deps_mode_default,
                )
                deps_mode = deps_mode_values[mode_idx]
                result[cmd_name]["mode"] = deps_mode

                # 3. Effort + yolo (yolo skipped when mode=headless)
                _ask_effort_and_permission_mode(
                    cmd_name, effective_tool, skip_autonomy=(deps_mode == "headless")
                )

    return result

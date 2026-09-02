"""AI tool and model resolution logic (wade-side).

This intentionally parallels ``crossby.services.ai_resolution`` rather than
delegating to it: crossby's resolvers operate on ``CrossbyConfig`` (whose AI
section is a ``commands`` dict), while wade's operate on ``ProjectConfig``
(whose AI section exposes named per-command fields, e.g. ``ai.plan``). The two
config shapes are not interchangeable, so wade keeps its own thin resolvers.

Capability parity, however, now lives in crossby: as of crossby v0.2.5,
``resolve_effort`` supports per-complexity-tier effort and a configurable
effort env var (wade passes ``WADE_EFFORT``). Full delegation is achievable
via a thin adapter that maps wade's named-field ``ProjectConfig.ai`` onto the
``commands``-dict shape crossby expects — tracked as a follow-up rather than
done here to keep this PR focused.
"""

from __future__ import annotations

import os

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID, EffortLevel

from wade.models.config import AICommandConfig, ProjectConfig
from wade.models.delegation import DelegationMode
from wade.models.permission import (
    PermissionMode,
    coerce_permission_mode,
    describe_permission_mode,
)

logger = structlog.get_logger()

_CUSTOM_OPTION = "Custom…"


def resolve_ai_tool(
    ai_tool: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    auto_detect: bool = True,
) -> str | None:
    """Resolve AI tool from args -> config -> detection.

    Fallback chain: explicit arg -> command-specific config -> global default
    -> auto-detect (when *auto_detect* is True).

    Set *auto_detect=False* when the caller handles multi-tool selection
    itself (e.g. TTY prompts in implement).
    """
    if ai_tool:
        return ai_tool

    config_tool = config.get_ai_tool(command)
    if config_tool:
        return config_tool

    if auto_detect:
        installed = AbstractAITool.detect_installed()
        if installed:
            return installed[0].value

    return None


def resolve_model(
    model: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
    complexity: str | None = None,
) -> str | None:
    """Resolve model from args -> config -> complexity -> default.

    Fallback chain:
      1. Explicit *model* arg (e.g. ``--model`` CLI flag)
      2. Command-specific config (``ai.<command>.model``)
      3. Complexity-based mapping (``models.<tool>.<complexity>``)
      4. Global default (``ai.default_model``)

    When *tool* is provided, the resolved model is checked for compatibility
    with that tool.  Incompatible models are dropped (returns ``None``).
    """
    resolved: str | None = model

    # 2. Command-specific config
    if not resolved:
        cmd_config = getattr(config.ai, command, None)
        if isinstance(cmd_config, AICommandConfig) and cmd_config.model:
            resolved = cmd_config.model

    # 3. Complexity-based mapping
    if not resolved and tool and complexity:
        resolved = config.get_complexity_model(tool, complexity)

    # 4. Global default
    if not resolved:
        resolved = config.ai.default_model

    # Compatibility gate
    if resolved and tool:
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            if not adapter.is_model_compatible(resolved):
                logger.info(
                    "model.incompatible",
                    model=resolved,
                    tool=tool,
                )
                return None
        except (ValueError, KeyError):
            pass

    return resolved


def _tool_honors_effort(tool: str | None, level: EffortLevel) -> bool:
    """Whether *tool* can actually honor the effort *level*.

    Returns ``False`` when the tool has no effort concept, or restricts its
    efforts and *level* is excluded (e.g. ``antigravity-cli`` rejects ``xhigh``
    and ``max`` — ``agy --effort xhigh`` is rejected by the CLI). Unknown or
    absent tools are treated permissively — capability gating is best-effort,
    matching the resolvers' other tool lookups.
    """
    if not tool:
        return True
    try:
        caps = AbstractAITool.get(AIToolID(tool)).capabilities()
    except (ValueError, KeyError):
        return True
    if not caps.supports_effort:
        return False
    return not caps.supported_efforts or level in caps.supported_efforts


def resolve_effort(
    effort: str | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
    complexity: str | None = None,
) -> EffortLevel | None:
    """Resolve effort level from args -> env var -> config -> None.

    Fallback chain:
      1. Explicit *effort* arg (e.g. ``--effort`` CLI flag)
      2. ``WADE_EFFORT`` environment variable
      3. Command-specific config (``ai.<command>.effort``)
      4. Per-complexity-tier config (``models.<tool>.<tier>.effort``)
      5. Global config (``ai.effort``)

    When *tool* is provided and it cannot honor the resolved level — the tool
    has no effort concept, or restricts efforts to a subset that excludes the
    level (e.g. ``antigravity-cli`` rejects ``xhigh``/``max``) — the level is
    dropped and ``None`` is returned, so config/CLI input can never forward an
    unsupported level.
    """
    resolved: str | None = effort

    if not resolved:
        resolved = os.environ.get("WADE_EFFORT")

    # Command-specific config (ai.<command>.effort)
    if not resolved:
        cmd_config = getattr(config.ai, command, None)
        if isinstance(cmd_config, AICommandConfig) and cmd_config.effort:
            resolved = cmd_config.effort

    # Per-complexity-tier config (models.<tool>.<tier>.effort)
    if not resolved and tool and complexity:
        resolved = config.get_complexity_effort(tool, complexity)

    # Global config (ai.effort)
    if not resolved:
        resolved = config.ai.effort

    if not resolved:
        return None

    # Validate
    try:
        level = EffortLevel(resolved)
    except ValueError:
        logger.warning("effort.invalid_level", effort=resolved)
        return None

    # Check tool support — the tool must expose effort AND accept this specific
    # level (antigravity-cli rejects xhigh/max), else drop it.
    if tool and not _tool_honors_effort(tool, level):
        logger.info("effort.unsupported", tool=tool, effort=resolved)
        return None

    return level


def resolve_permission_mode(
    permission_mode: str | PermissionMode | None,
    yolo: bool | None,
    config: ProjectConfig,
    command: str = "plan",
) -> PermissionMode:
    """Resolve the autonomy tier from args -> config -> ``default``.

    The fallback chain (highest precedence first) is:
      1. Explicit ``--permission-mode`` CLI value
      2. ``--yolo`` CLI alias (equivalent to ``permission_mode=yolo``)
      3. Command/global config (``ai.<command>.permission_mode`` / ``yolo``,
         then ``ai.permission_mode`` / ``ai.yolo``)
      4. ``default``

    Unlike the old ``resolve_yolo``, this does **not** gate on per-tool
    capability support: crossby owns capability-aware downgrades and warnings
    (see ``_autonomy_launch_args``), so WADE forwards the requested tier
    verbatim. Invalid CLI values (including ``plan``) warn and fall back to
    ``default`` rather than erroring.
    """
    if permission_mode is not None:
        mode = coerce_permission_mode(permission_mode)
        if mode is None:
            logger.warning(
                "permission_mode.invalid",
                value=permission_mode,
                source="cli",
                fallback="default",
            )
            return PermissionMode.DEFAULT
        return mode

    if yolo:
        return PermissionMode.YOLO

    configured = config.get_permission_mode(command)
    if configured is not None:
        return configured

    return PermissionMode.DEFAULT


LAUNCH_NETWORK_ACCESS = True
"""Network access forwarded to every crossby launch — unconditionally on (#478).

This is a *constant*, not a resolved setting. The retired ``ai.network_access``
key made network a Codex-only, per-command axis (on for ``implement`` /
``review_pr_comments``, off elsewhere), and that asymmetry is precisely what
broke delegated tools: every WADE session needs the network for ``git
fetch``/``push``, ``gh``, and package installs, planning sessions included.

It still has to be *passed*, not omitted: crossby's ``network_access`` parameter
defaults to ``False``, so a sandboxed launch that dropped it would pin
``sandbox_workspace_write.network_access=false`` and take the network away again.
Passing ``True`` explicitly also keeps the pin defensive — an ambient
``network_access`` in the user's own Codex ``config.toml`` can never decide the
boundary of a wade-managed session. Under ``sandbox=False`` crossby ignores the
value entirely (there is no sandbox to pin it on).
"""


class SandboxCapabilityError(ValueError):
    """A tool cannot honor an explicitly requested sandbox profile.

    Raised only for the one asymmetric case wade refuses to fake: ``sandbox:
    true`` on a runtime with no sandbox toggle. Wade cannot impose a boundary the
    runtime does not have, and silently launching unsandboxed would be a security
    posture the project did not choose.
    """


def enforce_sandbox_capability(tool: str, sandbox: bool) -> None:
    """Fail loudly, or warn, when *tool* cannot honor the resolved profile.

    Call this only on paths that actually launch a runtime. Callers whose
    resolution immediately precedes a launch get it for free by passing ``tool``
    to :func:`resolve_sandbox`; callers that resolve early and may still exit
    without launching (``wade implement --cd``, or a nested AI session that only
    prints the worktree path) resolve without a tool and call this at the launch
    site instead. Enforcing it earlier would fail a command that was never going
    to start the runtime it cannot satisfy.

    Deliberately asymmetric, because the terminal default is *unrestricted*:

    - **Toggle-capable tool** (Codex, Cursor) — nothing to check; crossby emits
      the flag in both directions.
    - **``sandbox=False`` on a tool that never sandboxed anything** (Claude,
      Copilot, …) — silent no-op. Those runtimes are already unrestricted, so
      erroring would break every default launch.
    - **``sandbox=False`` on a tool that *does* sandbox writes but exposes no
      toggle** — warn. The request cannot be honored and the session will run
      confined; never silently pretend otherwise. Unreachable with crossby
      >=0.29 (Codex has both flags) and kept as the forward-compat guard.
    - **``sandbox=True`` on a tool with no toggle** — hard error. The profile was
      explicitly asked for (the default is False, so a True can only come from
      ``--sandbox`` or config) and cannot be delivered.

    An unknown/absent tool is treated permissively, matching the other resolvers'
    best-effort capability lookups.
    """
    try:
        caps = AbstractAITool.get(AIToolID(tool)).capabilities()
    except (ValueError, KeyError):
        return

    if caps.supports_sandbox_toggle:
        return

    if sandbox:
        raise SandboxCapabilityError(
            f"ai.sandbox: '{tool}' cannot be sandboxed — it exposes no sandbox toggle. "
            "Remove the explicit sandbox request, or run this command with a tool "
            "that supports one (codex, cursor)."
        )

    if caps.sandboxes_writes:
        from wade.ui.console import console

        logger.warning("sandbox.unrestricted_unsupported", tool=tool)
        console.warn(
            f"{tool} sandboxes writes but exposes no sandbox toggle — this session "
            "runs sandboxed despite the unrestricted profile."
        )


def resolve_sandbox(
    sandbox: bool | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
) -> bool:
    """Resolve the AI-runtime sandbox profile from args -> config -> default.

    Fallback chain (highest precedence first):
      1. Explicit ``--sandbox`` / ``--no-sandbox`` CLI value
      2. Command-specific config (``ai.<command>.sandbox``)
      3. Global config (``ai.sandbox``)
      4. :data:`~wade.models.config.DEFAULT_SANDBOX` — ``False`` (unrestricted)

    ``True`` confines the AI runtime to its own sandbox (Codex
    ``workspace-write``, Cursor ``--sandbox enabled``); ``False`` launches it
    unrestricted so delegated child tools keep their own host credentials.
    Network access is on either way — it is no longer a wade-managed axis.

    This profile governs the **external AI runtime boundary only**. It is
    orthogonal to ``permission_mode``: an unrestricted sandbox is not ``yolo``
    and never changes the resolved autonomy tier. It also never relaxes wade's
    own guards — see ``_install_guard_hooks``, which *widens* the
    worktree-containment guard when the sandbox is off.

    Wade pins the resolved profile explicitly in both directions, so an ambient
    tool config can never silently move the boundary of a managed session.

    *tool* is a convenience for callers that launch immediately after resolving:
    the profile is capability-checked before it is returned. Callers that resolve
    early and may exit without launching omit it and call
    :func:`enforce_sandbox_capability` at the launch site instead.

    Raises:
        SandboxCapabilityError: *tool* cannot honor an explicit ``sandbox=True``.
    """
    resolved = sandbox if sandbox is not None else config.get_sandbox(command)
    if tool:
        enforce_sandbox_capability(tool, resolved)
    return resolved


def describe_sandbox(sandbox: bool) -> str:
    """Human-readable meaning of a resolved sandbox profile.

    Spelled out at launch so the security posture of a session is never implicit
    — the unrestricted default in particular must be visible, not inferred.
    """
    if sandbox:
        return "sandboxed — AI runtime confined to its workspace sandbox"
    return "unrestricted — AI runtime sandbox disabled; host credentials available"


def resolve_yolo(
    yolo: bool | None,
    config: ProjectConfig,
    command: str = "plan",
    *,
    tool: str | None = None,
) -> bool:
    """Whether the resolved permission mode is ``yolo`` (back-compat shim).

    Derives from :func:`resolve_permission_mode` so the yolo alias has one
    source of truth. The ``tool`` argument is accepted for signature
    compatibility but no longer gates on ``supports_yolo`` — crossby now owns
    capability-aware downgrades, so an unsupported tier downgrades (with a
    crossby warning) instead of WADE silently returning ``False``.
    """
    return resolve_permission_mode(None, yolo, config, command) is PermissionMode.YOLO


def _display_ai_selection(
    tool: str | None,
    model: str | None,
    effort: EffortLevel | None,
    permission_mode: PermissionMode,
    sandbox: bool | None = None,
) -> None:
    """Print the resolved AI selection (tool, model, effort, permission, sandbox).

    The permission-mode line is **always** printed with a human-readable
    descriptor (including ``default``), so every launch states both which tier
    is active and what it means. The sandbox line follows the same rule for the
    same reason — the profile decides whether the runtime can reach the host, so
    it must be stated, never inferred. It is omitted only when the caller
    resolved no profile (``sandbox=None``), which no launch path does.

    When no tool resolved, renders a single ``AI tool: not resolved`` line rather
    than passing ``None`` to :meth:`console.kv` (which is typed ``str`` and would
    print a nonsense line).
    """
    from wade.ui.console import console

    if tool is None:
        console.kv("AI tool", "not resolved")
        return
    console.kv("AI tool", tool)
    if model:
        console.kv("Model", model)
    if effort:
        console.kv("Effort", effort.value)
    console.kv(
        "Permission mode",
        f"{permission_mode.value} — {describe_permission_mode(permission_mode)}",
    )
    if sandbox is not None:
        console.kv("Sandbox", describe_sandbox(sandbox))


def confirm_ai_selection(
    resolved_tool: str | None,
    resolved_model: str | None,
    *,
    tool_explicit: bool,
    model_explicit: bool,
    resolved_effort: EffortLevel | None = None,
    effort_explicit: bool = False,
    resolved_permission_mode: PermissionMode = PermissionMode.DEFAULT,
    permission_mode_explicit: bool = True,
    mode: DelegationMode | None = None,
    sandbox: bool | None = None,
) -> tuple[str | None, str | None, EffortLevel | None, PermissionMode]:
    """Display the resolved AI selection, then interactively confirm/change it.

    The resolved selection (tool, model, effort, permission mode, sandbox
    profile) is **always displayed exactly once** — before any skip guard — so it
    surfaces on every launch path (TTY, non-TTY, headless, all-flags-explicit).

    *sandbox* is display-only: it is never offered in the change-loop, because
    the profile is a launch-time OS property that cannot be renegotiated after
    the process starts.

    The interactive change-loop then fires only when stdin is a TTY and at least
    one of the flags was not explicitly provided by the caller.  When all flags
    are explicit (e.g. because ``wade implement-batch`` passes
    ``--ai``/``--model``/``--effort`` to child calls), it is skipped. It is also
    skipped when *mode* is ``DelegationMode.HEADLESS``: headless mode is defined
    as unattended, so it must never block on a TTY prompt even when one happens
    to be attached.

    Returns the (tool, model, effort, permission_mode) tuple after any
    user-driven changes.
    """
    from wade.ui import prompts

    # Always surface the resolved selection once, before the skip guard below.
    _display_ai_selection(
        resolved_tool, resolved_model, resolved_effort, resolved_permission_mode, sandbox
    )

    # Skip the change-loop when non-TTY, no tool resolved, all flags were
    # explicit, or headless. The display above has already run regardless.
    all_explicit = tool_explicit and model_explicit and effort_explicit and permission_mode_explicit
    if (
        not prompts.is_tty()
        or resolved_tool is None
        or all_explicit
        or mode == DelegationMode.HEADLESS
    ):
        return resolved_tool, resolved_model, resolved_effort, resolved_permission_mode

    tool = resolved_tool
    model = resolved_model
    effort = resolved_effort
    permission_mode = resolved_permission_mode

    first_render = True
    while True:
        # Refresh the displayed selection after an interactive change. The first
        # iteration was already rendered by the hoisted call above — don't
        # double-print it.
        if not first_render:
            _display_ai_selection(tool, model, effort, permission_mode, sandbox)
        first_render = False

        # Build menu dynamically based on which flags were NOT explicit.
        menu_items: list[str] = ["Proceed"]
        installed = AbstractAITool.detect_installed()
        can_change_tool = not tool_explicit and len(installed) > 1
        if can_change_tool:
            menu_items.append("Change AI tool")
        if not model_explicit:
            menu_items.append("Change model")

        # Show "Change effort" / "Change permission mode" only when the tool
        # supports the corresponding capability.
        tool_supports_effort = False
        tool_supports_autonomy = False
        try:
            adapter = AbstractAITool.get(AIToolID(tool))
            caps = adapter.capabilities()
            tool_supports_effort = caps.supports_effort
            tool_supports_autonomy = (
                caps.supports_accept_edits or caps.supports_auto or caps.supports_yolo
            )
        except (ValueError, KeyError):
            pass
        if not effort_explicit and tool_supports_effort:
            menu_items.append("Change effort")
        if not permission_mode_explicit and tool_supports_autonomy:
            menu_items.append("Change permission mode")

        if len(menu_items) == 1:
            break

        idx = prompts.select("Confirm AI selection", menu_items)
        choice = menu_items[idx]

        if choice == "Proceed":
            break

        if choice == "Change AI tool":
            tool_names = [str(t) for t in installed]
            current_idx = tool_names.index(tool) if tool in tool_names else 0
            new_idx = prompts.select("Select AI tool", tool_names, default=current_idx)
            new_tool = tool_names[new_idx]
            if new_tool != tool:
                tool = new_tool
                model = _prompt_model_selection(tool)
                # Clear stale effort when the new tool can't honor it — either it
                # has no effort concept, or it restricts efforts to a subset that
                # excludes the retained level (e.g. antigravity-cli rejects
                # xhigh/max). This prevents Proceed from returning an unsupported
                # level without reopening the effort picker. The permission mode
                # is left as requested — crossby downgrades any unsupported tier
                # at launch (WADE must not reimplement that).
                if effort is not None and not _tool_honors_effort(tool, effort):
                    effort = None

        elif choice == "Change model":
            model = _prompt_model_selection(tool)

        elif choice == "Change effort":
            effort = _prompt_effort_selection(effort, tool)

        elif choice == "Change permission mode":
            permission_mode = _prompt_permission_mode_selection(permission_mode, tool)

    return tool, model, effort, permission_mode


def _prompt_model_selection(tool: str) -> str | None:
    """Show a model picker for *tool* and return the chosen model (or None)."""
    from crossby.data import get_models_for_tool

    from wade.ui import prompts

    models = get_models_for_tool(tool)
    choices = [*models, _CUSTOM_OPTION]
    idx = prompts.select(f"Select model for {tool}", choices)
    chosen = choices[idx]
    if chosen == _CUSTOM_OPTION:
        custom = prompts.input_prompt("Enter model name", allow_empty=True)
        return custom or None
    return chosen or None


def _prompt_permission_mode_selection(current: PermissionMode, tool: str) -> PermissionMode:
    """Show a permission-mode picker gated by *tool* capabilities.

    Offers ``default`` plus each autonomy tier the tool declares support for.
    This only reads capability flags for UX affordance — it does not reimplement
    crossby's precedence/downgrade ladder.
    """
    from wade.ui import prompts

    tiers: list[PermissionMode] = [PermissionMode.DEFAULT]
    try:
        caps = AbstractAITool.get(AIToolID(tool)).capabilities()
        if caps.supports_accept_edits:
            tiers.append(PermissionMode.ACCEPT_EDITS)
        if caps.supports_auto:
            tiers.append(PermissionMode.AUTO)
        if caps.supports_yolo:
            tiers.append(PermissionMode.YOLO)
    except (ValueError, KeyError):
        pass

    labels = [t.value for t in tiers]
    default_idx = tiers.index(current) if current in tiers else 0
    idx = prompts.select("Select permission mode", labels, default=default_idx)
    return tiers[idx]


def valid_effort_levels(tool: str | None) -> list[EffortLevel]:
    """Return the effort levels *tool* actually supports.

    Reads crossby's ``capabilities().supported_efforts`` and falls back to the
    full ``EffortLevel`` set when the tool is unknown or declares no restriction
    (an empty/unset value). Callers prepend their own skip/none label — this
    returns only the level list, since those labels differ per call site.
    """
    if tool:
        try:
            caps = AbstractAITool.get(AIToolID(tool)).capabilities()
        except (ValueError, KeyError):
            caps = None
        if caps is not None and caps.supported_efforts:
            return list(caps.supported_efforts)
    return list(EffortLevel)


def _prompt_effort_selection(
    current: EffortLevel | None, tool: str | None = None
) -> EffortLevel | None:
    """Show an effort level picker and return the chosen level (or None).

    Only the levels *tool* supports are offered (see :func:`valid_effort_levels`).
    A *current* level the tool no longer supports is not pre-selected.
    """
    from wade.ui import prompts

    level_values = [e.value for e in valid_effort_levels(tool)]
    choices = ["(none — use tool default)", *level_values]
    default_idx = 0
    if current is not None and current.value in level_values:
        default_idx = level_values.index(current.value) + 1
    idx = prompts.select("Select effort level", choices, default=default_idx)
    if idx == 0:
        return None
    return EffortLevel(choices[idx])

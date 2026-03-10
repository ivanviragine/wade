"""Abstract base class for AI tool adapters with self-registration.

Adding a new AI tool = one file with one class. No other files to modify.
The `__init_subclass__` hook auto-registers each concrete adapter.
"""

from __future__ import annotations

import inspect
import shutil
import warnings
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import structlog

from wade.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    EffortLevel,
    ModelTier,
    TokenUsage,
)
from wade.models.config import ComplexityModelMapping

logger = structlog.get_logger()


class AbstractAITool(ABC):
    """Base for all AI tool adapters.

    Concrete subclasses must set TOOL_ID as a class variable.
    Registration happens automatically via __init_subclass__.
    """

    TOOL_ID: ClassVar[AIToolID]
    _registry: ClassVar[dict[AIToolID, type[AbstractAITool]]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "TOOL_ID") and not inspect.isabstract(cls):
            if cls.TOOL_ID in AbstractAITool._registry:
                existing_cls = AbstractAITool._registry[cls.TOOL_ID]
                warnings.warn(
                    f"TOOL_ID '{cls.TOOL_ID}' already registered by {existing_cls.__name__}; "
                    f"overwriting with {cls.__name__}",
                    UserWarning,
                    stacklevel=2,
                )
            AbstractAITool._registry[cls.TOOL_ID] = cls

    @classmethod
    def get(cls, tool_id: str | AIToolID) -> AbstractAITool:
        """Get an adapter instance by tool ID."""
        tid = AIToolID(tool_id) if not isinstance(tool_id, AIToolID) else tool_id
        if tid not in cls._registry:
            raise ValueError(f"Unknown AI tool: {tool_id}")
        return cls._registry[tid]()

    @classmethod
    def available_tools(cls) -> list[AIToolID]:
        """List all registered tool IDs."""
        return list(cls._registry.keys())

    @classmethod
    def detect_installed(cls) -> list[AIToolID]:
        """Detect which registered AI tools are installed on the system."""
        installed = []
        for tool_id, tool_cls in cls._registry.items():
            adapter = tool_cls()
            if shutil.which(adapter.capabilities().binary):
                installed.append(tool_id)
        return installed

    @abstractmethod
    def capabilities(self) -> AIToolCapabilities:
        """Declare what this tool can do."""
        raise NotImplementedError

    def get_models(self) -> list[AIModel]:
        """Return known models from the static registry.

        Uses universal tier classification. Override for tools with
        special model ID formats (e.g. OpenCode's provider/model).
        Returns an empty list if no models are registered for this tool.
        """
        from wade.ai_tools.model_utils import classify_tier_universal, has_date_suffix
        from wade.data import get_models_for_tool

        return [
            AIModel(
                id=mid,
                tier=classify_tier_universal(mid),
                is_alias=not has_date_suffix(mid),
            )
            for mid in get_models_for_tool(str(self.TOOL_ID))
        ]

    def get_default_model(self, tier: ModelTier) -> AIModel | None:
        """Get the best model for a given tier.

        Override in subclasses for tool-specific tier keywords.
        """
        models = self.get_models()
        tier_models = [m for m in models if m.tier == tier]
        if not tier_models:
            return None
        return pick_best_model(tier_models)

    def get_recommended_mapping(self) -> ComplexityModelMapping:
        """Get recommended model mapping for all complexity levels."""
        fast = self.get_default_model(ModelTier.FAST)
        balanced = self.get_default_model(ModelTier.BALANCED)
        powerful = self.get_default_model(ModelTier.POWERFUL)

        return ComplexityModelMapping(
            easy=fast.id if fast else None,
            medium=fast.id if fast else None,
            complex=balanced.id if balanced else None,
            very_complex=powerful.id if powerful else None,
        )

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
        trusted_dirs: list[str] | None = None,
        effort: EffortLevel | None = None,
        allowed_commands: list[str] | None = None,
        yolo: bool = False,
    ) -> int:
        """Launch the AI tool in the given worktree.

        Default implementation builds a command via build_launch_command()
        and runs it with transcript capture. Override for tools with
        non-standard launch behavior (e.g. GUI tools).

        Args:
            worktree_path: Directory to run in.
            model: Model ID to use (or None for tool default).
            prompt: Optional initial message passed to the tool on launch.
            detach: If True, launch in background (GUI tools).
            transcript_path: Optional path to write session transcript for
                token usage extraction.
            trusted_dirs: Optional list of directory paths to pre-authorize.
                Tools that support directory-trust flags (e.g. --add-dir) will
                pass these so the user is not prompted for confirmation.
            effort: Optional reasoning effort level for the AI tool.
            allowed_commands: Optional list of canonical command patterns to
                pre-authorize (e.g. ``["wade *", "./scripts/check.sh *"]``).
            yolo: If True, skip all permission prompts (YOLO mode).

        Returns:
            Exit code from the tool process (0 for detached).
        """
        from wade.utils.process import run_with_transcript

        cmd = self.build_launch_command(
            model=model,
            initial_message=prompt,
            trusted_dirs=trusted_dirs,
            effort=effort,
            allowed_commands=allowed_commands,
            yolo=yolo,
        )
        logger.info("ai_tool.launch", tool=str(self.TOOL_ID), model=model, cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        """Parse a transcript file for token usage.

        Default implementation extracts token usage from transcript text.
        Override for tools with special parsing needs (e.g. premium requests).
        Returns TokenUsage with whatever fields could be parsed.
        """
        from wade.ai_tools.transcript import parse_transcript_common

        return parse_transcript_common(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Check if a model ID is valid for this tool."""
        return True  # Default: allow all. Override per tool.

    def initial_message_args(self, prompt: str) -> list[str]:
        """Get CLI args to pass an initial message for an interactive session.

        Default: no support (returns empty list). Override per tool.
        Tools that support positional initial messages return [prompt].
        Tools that require a flag return [flag, prompt].
        """
        return []

    def plan_mode_args(self) -> list[str]:
        """Get extra CLI args for native plan/approval mode."""
        return []  # Default: no plan mode support

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Get extra CLI args to grant write access to a plan output directory."""
        return []  # Default: no plan dir support

    def trusted_dirs_args(self, dirs: list[str]) -> list[str]:
        """Get extra CLI args to grant access to a list of trusted directories.

        Default implementation delegates to plan_dir_args() per directory, so
        any adapter that overrides plan_dir_args() automatically supports this
        method. Adapters without directory-trust support return [].
        """
        result: list[str] = []
        for d in dirs:
            result.extend(self.plan_dir_args(d))
        return result

    def normalize_model_format(self, model_id: str) -> str:
        """Normalize a model ID to this tool's expected format.

        For example, Copilot uses dotted format (claude-haiku-4.5) while
        Claude uses dashed format (claude-haiku-4-5).

        Default: return as-is. Override per tool.
        """
        return model_id

    def standardize_model_id(self, raw_model_id: str) -> str:
        """Convert a tool-specific model ID to the internal standard format.

        Our internal registry uses dotted notation (e.g. claude-haiku-4.5).
        Tools that output dashed notation (claude-haiku-4-5) should override
        this to convert it back to dotted notation.

        Default: return as-is. Override per tool.
        """
        return raw_model_id

    def allowed_commands_args(self, commands: list[str]) -> list[str]:
        """Get CLI args to pre-authorize a list of command patterns.

        Canonical patterns use shell-style syntax (e.g. ``"wade *"``,
        ``"./scripts/check.sh *"``).  Each adapter translates them into
        tool-specific flags.

        Default: no support (returns empty list). Override per tool.
        """
        return []

    def structured_output_args(self, json_schema: dict[str, Any]) -> list[str]:
        """Get extra CLI args to enforce structured JSON output according to a schema.

        Default: return empty list. Override per tool if they support it.
        """
        return []

    def effort_args(self, effort: EffortLevel) -> list[str]:
        """Get extra CLI args to set reasoning effort level.

        Default: return empty list. Override per tool.
        """
        return []

    def yolo_args(self) -> list[str]:
        """Get extra CLI args to skip all permission prompts (YOLO mode).

        Default: return empty list. Override per tool.
        """
        return []

    def resolve_effort_model(self, model: str | None, effort: EffortLevel) -> str | None:
        """Resolve model variant based on effort level.

        Some tools use different model IDs for higher effort (e.g., thinking
        model variants). Default: return model unchanged. Override per tool.
        """
        return model

    def preserve_session_data(self, worktree_path: Path, main_checkout_path: Path) -> bool:
        """Preserve AI tool session data from a worktree before it is deleted.

        Called before removing a worktree so that sessions started in the worktree
        can be resumed from the main checkout after the worktree is gone.

        Default: no-op (return True). Override in tools that store path-bound
        session data.

        Args:
            worktree_path: The worktree directory being deleted.
            main_checkout_path: The main checkout to migrate session data into.

        Returns:
            True if preservation succeeded (or is not needed), False on failure.
        """
        return True

    def session_data_dirs(self) -> list[str]:
        """Return directory names that indicate this tool may have session data.

        Used as a fallback when the DB has no record of the tool used in a
        worktree. If any of these directories exist under a worktree, this
        adapter is selected for preservation.

        Default: empty list (no detection). Override per tool.
        """
        return []

    def build_resume_command(self, session_id: str) -> list[str] | None:
        """Build a command to resume a previous session.

        Returns None if the tool does not support session resume.
        Override in adapters that support resume (and set supports_resume=True
        in capabilities).
        """
        return None

    def build_launch_command(
        self,
        model: str | None = None,
        prompt: str | None = None,
        plan_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        trusted_dirs: list[str] | None = None,
        initial_message: str | None = None,
        effort: EffortLevel | None = None,
        allowed_commands: list[str] | None = None,
        yolo: bool = False,
    ) -> list[str]:
        """Build the command line for launching this tool."""
        caps = self.capabilities()
        cmd = [caps.binary]

        # Initial message comes first so it is the first positional arg seen by
        # the tool's parser (before any flags that could interfere).
        if initial_message:
            cmd.extend(self.initial_message_args(initial_message))

        # Resolve effort-based model variant before applying model flag
        effective_model = model
        if effort and effective_model:
            effective_model = self.resolve_effort_model(effective_model, effort)
        elif effort and not effective_model:
            effective_model = self.resolve_effort_model(None, effort)

        if effective_model and caps.supports_model_flag:
            cmd.extend([caps.model_flag, self.normalize_model_format(effective_model)])

        if prompt and caps.supports_headless and caps.headless_flag:
            cmd.extend([caps.headless_flag, prompt])

        # YOLO mode supersedes plan_mode: YOLO grants full-auto permissions
        # which is a superset of plan permissions. If the tool doesn't support
        # YOLO, emit a warning and fall back to plan_mode_args.
        if yolo:
            if caps.supports_yolo:
                cmd.extend(self.yolo_args())
            else:
                warnings.warn(
                    f"{caps.display_name} does not support YOLO mode; "
                    f"{'falling back to plan mode' if plan_mode else 'ignoring yolo'}",
                    UserWarning,
                    stacklevel=2,
                )
                if plan_mode:
                    cmd.extend(self.plan_mode_args())
        elif plan_mode:
            cmd.extend(self.plan_mode_args())

        if json_schema:
            cmd.extend(self.structured_output_args(json_schema))

        if trusted_dirs:
            cmd.extend(self.trusted_dirs_args(trusted_dirs))

        # Effort args (tool-specific flags like --settings, --variant, etc.)
        if effort and caps.supports_effort:
            cmd.extend(self.effort_args(effort))

        if allowed_commands:
            cmd.extend(self.allowed_commands_args(allowed_commands))

        return cmd


def pick_best_model(models: list[AIModel]) -> AIModel | None:
    """Pick the best model from a list — prefer aliases (no date suffix)."""
    if not models:
        return None

    # Prefer models without date suffix (alias models)
    aliases = [m for m in models if m.is_alias]
    if aliases:
        return aliases[0]

    # Fallback: sort by ID and take the last (newest)
    return sorted(models, key=lambda m: m.id)[-1]

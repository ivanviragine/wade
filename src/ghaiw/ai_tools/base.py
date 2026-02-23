"""Abstract base class for AI tool adapters with self-registration.

Adding a new AI tool = one file with one class. No other files to modify.
The `__init_subclass__` hook auto-registers each concrete adapter.
"""

from __future__ import annotations

import inspect
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ghaiw.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    ModelTier,
    TokenUsage,
)
from ghaiw.models.config import ComplexityModelMapping


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
        ...

    @abstractmethod
    def get_models(self) -> list[AIModel]:
        """Query the tool for its available models.

        Returns an empty list if probing fails.
        """
        ...

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

    @abstractmethod
    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
    ) -> int:
        """Launch the AI tool in the given worktree.

        Args:
            worktree_path: Directory to run in.
            model: Model ID to use (or None for tool default).
            prompt: Optional prompt text (clipboard or headless).
            detach: If True, launch in background (GUI tools).

        Returns:
            Exit code from the tool process (0 for detached).
        """
        ...

    @abstractmethod
    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        """Parse a transcript file for token usage.

        Returns TokenUsage with whatever fields could be parsed.
        """
        ...

    def is_model_compatible(self, model: str) -> bool:
        """Check if a model ID is valid for this tool.

        Behavioral ref: lib/common.sh:_is_model_compatible_with_tool()
        """
        return True  # Default: allow all. Override per tool.

    def build_launch_command(
        self,
        model: str | None = None,
        prompt: str | None = None,
        plan_mode: bool = False,
    ) -> list[str]:
        """Build the command line for launching this tool."""
        caps = self.capabilities()
        cmd = [caps.binary]

        if model and caps.supports_model_flag:
            cmd.extend([caps.model_flag, model])

        if prompt and caps.supports_headless and caps.headless_flag:
            cmd.extend([caps.headless_flag, prompt])

        return cmd


def pick_best_model(models: list[AIModel]) -> AIModel | None:
    """Pick the best model from a list — prefer aliases (no date suffix).

    Behavioral reference: lib/init.sh:_init_pick_best_model()
    """
    if not models:
        return None

    # Prefer models without date suffix (alias models)
    aliases = [m for m in models if m.is_alias]
    if aliases:
        return aliases[0]

    # Fallback: sort by ID and take the last (newest)
    return sorted(models, key=lambda m: m.id)[-1]

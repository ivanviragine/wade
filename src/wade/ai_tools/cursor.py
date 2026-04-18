"""Cursor CLI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    EffortLevel,
)

logger = structlog.get_logger()

_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-xhigh", "-max")


def _is_new_style_cursor_model(model: str) -> bool:
    """New-style Cursor models embed effort in the ID (e.g. claude-opus-4-7-high)."""
    return "-thinking-" in model or any(model.endswith(s) for s in _EFFORT_SUFFIXES)


class CursorAdapter(AbstractAITool):
    """Adapter for Cursor CLI (``agent`` binary).

    Cursor is an AI-powered IDE with a terminal CLI that supports plan mode,
    model selection, headless execution, and skill discovery.

    Cursor uses its own model ID namespace — e.g. ``sonnet-4.6``, ``opus-4.6``,
    ``gpt-5.3-codex`` — so no format normalization is needed.

    For high/max effort, Cursor uses thinking model variants (e.g.,
    ``sonnet-4.6-thinking``) rather than a separate effort flag.
    """

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CURSOR

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CURSOR,
            display_name="Cursor",
            binary="agent",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag="--print",
            supports_headless=True,
            supports_effort=True,
            supports_yolo=True,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Cursor accepts the initial message as a positional argument."""
        return [prompt]

    def is_model_compatible(self, model: str) -> bool:
        """Cursor accepts all model IDs."""
        return True

    def plan_mode_args(self) -> list[str]:
        """Cursor supports ``--mode plan``."""
        return ["--mode", "plan"]

    def yolo_args(self) -> list[str]:
        """Cursor uses ``--force`` (``--yolo`` is an alias)."""
        return ["--force"]

    def resolve_effort_model(self, model: str | None, effort: EffortLevel) -> str | None:
        """Select the appropriate model variant for the given effort level.

        New-style models (effort embedded in ID, e.g. ``claude-opus-4-7-high``):
          XHIGH/MAX → insert ``-thinking-`` before the effort suffix.
          All other levels → use the model ID as-is.

        Old-style models (short-name, e.g. ``sonnet-4.6``):
          HIGH/XHIGH/MAX → append ``-thinking``.
          All other levels → use the model ID as-is.
        """
        if not model:
            return model
        if _is_new_style_cursor_model(model):
            if effort in (EffortLevel.XHIGH, EffortLevel.MAX) and "-thinking-" not in model:
                for suffix in _EFFORT_SUFFIXES:
                    if model.endswith(suffix):
                        base = model[: -len(suffix)]
                        return f"{base}-thinking{suffix}"
            return model
        if effort in (EffortLevel.HIGH, EffortLevel.XHIGH, EffortLevel.MAX) and not model.endswith(
            "-thinking"
        ):
            return f"{model}-thinking"
        return model

    def preserve_session_data(self, worktree_path: Path, main_checkout_path: Path) -> bool:
        """Copy Cursor session data from worktree to main checkout's project dir.

        Cursor stores sessions in ``~/.cursor/projects/<encoded-path>/``.
        The path encoding strips the leading ``/`` then replaces remaining
        ``/`` with ``-``, so ``/Users/foo/bar`` becomes ``Users-foo-bar``
        (note: no leading dash, unlike Claude Code).
        """
        cursor_projects_dir = Path.home() / ".cursor" / "projects"
        wt_dir = cursor_projects_dir / str(worktree_path).lstrip("/").replace("/", "-")
        main_dir = cursor_projects_dir / str(main_checkout_path).lstrip("/").replace("/", "-")

        copied = self._copy_session_data_dir(wt_dir, main_dir)
        logger.info(
            "cursor.preserve_session_data.copied",
            worktree=str(worktree_path),
            main=str(main_checkout_path),
            items=copied,
        )
        return True

    def session_data_dirs(self) -> list[str]:
        return [".cursor"]

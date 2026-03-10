"""Cursor CLI adapter."""

from __future__ import annotations

import shutil
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
        """For high/max effort, append ``-thinking`` to the model ID."""
        if (
            effort in (EffortLevel.HIGH, EffortLevel.MAX)
            and model
            and not model.endswith("-thinking")
        ):
            return f"{model}-thinking"
        return model

    def preserve_session_data(self, worktree_path: Path, main_checkout_path: Path) -> bool:
        """Copy Cursor session data from worktree to main checkout's project dir.

        Cursor stores sessions in ``~/.cursor/projects/<encoded-path>/``.
        The path encoding strips the leading ``/`` then replaces remaining
        ``/`` with ``-``, so ``/Users/foo/bar`` becomes ``Users-foo-bar``
        (note: no leading dash, unlike Claude Code).

        Files are copied without overwriting any that already exist in the
        main checkout's session directory, so existing data is preserved.
        """
        cursor_projects_dir = Path.home() / ".cursor" / "projects"

        wt_encoded = str(worktree_path).lstrip("/").replace("/", "-")
        main_encoded = str(main_checkout_path).lstrip("/").replace("/", "-")

        wt_session_dir = cursor_projects_dir / wt_encoded
        main_session_dir = cursor_projects_dir / main_encoded

        if not wt_session_dir.exists():
            logger.debug(
                "cursor.preserve_session_data.no_source",
                worktree=str(worktree_path),
            )
            return True

        main_session_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for item in wt_session_dir.iterdir():
            dest = main_session_dir / item.name
            if dest.exists():
                continue
            if item.is_file():
                shutil.copy2(item, dest)
                copied += 1
            elif item.is_dir():
                shutil.copytree(item, dest)
                copied += 1

        logger.info(
            "cursor.preserve_session_data.copied",
            worktree=str(worktree_path),
            main=str(main_checkout_path),
            items=copied,
        )
        return True

    def session_data_dirs(self) -> list[str]:
        return [".cursor"]

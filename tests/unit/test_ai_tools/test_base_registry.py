"""Tests for AbstractAITool registry and __init_subclass__ behavior."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import ClassVar

import pytest

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.models.ai import AIModel, AIToolCapabilities, AIToolID, AIToolType, TokenUsage


class TestDuplicateToolIDWarning:
    """Test duplicate TOOL_ID registration warning."""

    def test_duplicate_tool_id_warns(self) -> None:
        """Registering a duplicate TOOL_ID should emit a UserWarning."""
        # Create a fresh registry for this test
        original_registry = AbstractAITool._registry.copy()
        AbstractAITool._registry.clear()

        try:
            # Define first adapter using an existing AIToolID
            class FirstAdapter(AbstractAITool):
                TOOL_ID: ClassVar[AIToolID] = AIToolID.CLAUDE

                def capabilities(self) -> AIToolCapabilities:
                    return AIToolCapabilities(
                        tool_id=AIToolID.CLAUDE,
                        display_name="Claude",
                        binary="claude",
                        tool_type=AIToolType.TERMINAL,
                    )

                def get_models(self) -> list[AIModel]:
                    return []

                def launch(
                    self,
                    worktree_path: Path,
                    model: str | None = None,
                    prompt: str | None = None,
                    detach: bool = False,
                    transcript_path: Path | None = None,
                ) -> int:
                    return 0

                def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                    return TokenUsage()

            # Define second adapter with same TOOL_ID
            with pytest.warns(UserWarning) as record:

                class SecondAdapter(AbstractAITool):
                    TOOL_ID: ClassVar[AIToolID] = AIToolID.CLAUDE

                    def capabilities(self) -> AIToolCapabilities:
                        return AIToolCapabilities(
                            tool_id=AIToolID.CLAUDE,
                            display_name="Claude",
                            binary="claude",
                            tool_type=AIToolType.TERMINAL,
                        )

                    def get_models(self) -> list[AIModel]:
                        return []

                    def launch(
                        self,
                        worktree_path: Path,
                        model: str | None = None,
                        prompt: str | None = None,
                        detach: bool = False,
                        transcript_path: Path | None = None,
                    ) -> int:
                        return 0

                    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                        return TokenUsage()

            # Verify warning was raised
            assert len(record) == 1
            assert issubclass(record[0].category, UserWarning)
            assert "already registered by FirstAdapter" in str(record[0].message)
            assert "overwriting with SecondAdapter" in str(record[0].message)

        finally:
            # Restore original registry
            AbstractAITool._registry.clear()
            AbstractAITool._registry.update(original_registry)

    def test_duplicate_tool_id_overwrites(self) -> None:
        """Registering a duplicate TOOL_ID should overwrite the previous registration."""
        # Create a fresh registry for this test
        original_registry = AbstractAITool._registry.copy()
        AbstractAITool._registry.clear()

        try:
            # Define first adapter
            class FirstAdapter(AbstractAITool):
                TOOL_ID: ClassVar[AIToolID] = AIToolID.COPILOT

                def capabilities(self) -> AIToolCapabilities:
                    return AIToolCapabilities(
                        tool_id=AIToolID.COPILOT,
                        display_name="Copilot",
                        binary="copilot",
                        tool_type=AIToolType.TERMINAL,
                    )

                def get_models(self) -> list[AIModel]:
                    return []

                def launch(
                    self,
                    worktree_path: Path,
                    model: str | None = None,
                    prompt: str | None = None,
                    detach: bool = False,
                    transcript_path: Path | None = None,
                ) -> int:
                    return 0

                def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                    return TokenUsage()

            # Verify first adapter is registered
            assert AbstractAITool._registry[AIToolID.COPILOT] is FirstAdapter

            # Define second adapter with same TOOL_ID (suppress warning for this test)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                class SecondAdapter(AbstractAITool):
                    TOOL_ID: ClassVar[AIToolID] = AIToolID.COPILOT

                    def capabilities(self) -> AIToolCapabilities:
                        return AIToolCapabilities(
                            tool_id=AIToolID.COPILOT,
                            display_name="Copilot",
                            binary="copilot",
                            tool_type=AIToolType.TERMINAL,
                        )

                    def get_models(self) -> list[AIModel]:
                        return []

                    def launch(
                        self,
                        worktree_path: Path,
                        model: str | None = None,
                        prompt: str | None = None,
                        detach: bool = False,
                        transcript_path: Path | None = None,
                    ) -> int:
                        return 0

                    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                        return TokenUsage()

            # Verify second adapter overwrote the first
            assert AbstractAITool._registry[AIToolID.COPILOT] is SecondAdapter

        finally:
            # Restore original registry
            AbstractAITool._registry.clear()
            AbstractAITool._registry.update(original_registry)

    def test_no_warning_for_unique_tool_ids(self) -> None:
        """Registering unique TOOL_IDs should not emit any warnings."""
        # Create a fresh registry for this test
        original_registry = AbstractAITool._registry.copy()
        AbstractAITool._registry.clear()

        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")

                # Define first adapter
                class FirstAdapter(AbstractAITool):
                    TOOL_ID: ClassVar[AIToolID] = AIToolID.GEMINI

                    def capabilities(self) -> AIToolCapabilities:
                        return AIToolCapabilities(
                            tool_id=AIToolID.GEMINI,
                            display_name="Gemini",
                            binary="gemini",
                            tool_type=AIToolType.TERMINAL,
                        )

                    def get_models(self) -> list[AIModel]:
                        return []

                    def launch(
                        self,
                        worktree_path: Path,
                        model: str | None = None,
                        prompt: str | None = None,
                        detach: bool = False,
                        transcript_path: Path | None = None,
                    ) -> int:
                        return 0

                    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                        return TokenUsage()

                # Define second adapter with different TOOL_ID
                class SecondAdapter(AbstractAITool):
                    TOOL_ID: ClassVar[AIToolID] = AIToolID.CODEX

                    def capabilities(self) -> AIToolCapabilities:
                        return AIToolCapabilities(
                            tool_id=AIToolID.CODEX,
                            display_name="Codex",
                            binary="codex",
                            tool_type=AIToolType.TERMINAL,
                        )

                    def get_models(self) -> list[AIModel]:
                        return []

                    def launch(
                        self,
                        worktree_path: Path,
                        model: str | None = None,
                        prompt: str | None = None,
                        detach: bool = False,
                        transcript_path: Path | None = None,
                    ) -> int:
                        return 0

                    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
                        return TokenUsage()

            # Verify no warnings were raised
            assert len(w) == 0

        finally:
            # Restore original registry
            AbstractAITool._registry.clear()
            AbstractAITool._registry.update(original_registry)

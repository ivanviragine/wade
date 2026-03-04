"""Autocompletion functions for Typer CLI."""

from __future__ import annotations

import typer


def complete_ai_tools(incomplete: str) -> list[str]:
    """Autocomplete AI tool IDs."""
    from wade.models.ai import AIToolID

    tools = [t.value for t in AIToolID]
    return sorted([t for t in tools if t.startswith(incomplete)])


def complete_models(ctx: typer.Context, incomplete: str) -> list[str]:
    """Autocomplete AI model IDs."""
    from wade.data import MODELS

    # Extract the --ai tool arg if any from context
    ai_arg = ctx.params.get("ai")

    candidates: list[str] = []
    if ai_arg:
        tools = ai_arg if isinstance(ai_arg, list) else [ai_arg]
        for t in tools:
            candidates.extend(MODELS.get(t, []))
    else:
        # If no tool specified, provide all models
        for models in MODELS.values():
            candidates.extend(models)

    # Deduplicate and match prefix
    unique_candidates = sorted(set(candidates))
    return [m for m in unique_candidates if m.startswith(incomplete)]

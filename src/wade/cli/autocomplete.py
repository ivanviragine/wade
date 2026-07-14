"""Autocompletion functions for Typer CLI."""

from __future__ import annotations

import typer


def complete_ai_tools(incomplete: str) -> list[str]:
    """Autocomplete AI tool IDs."""
    from crossby.models.ai import AIToolID

    tools = [t.value for t in AIToolID]
    return sorted([t for t in tools if t.startswith(incomplete)])


def complete_models(ctx: typer.Context, incomplete: str) -> list[str]:
    """Autocomplete AI model IDs."""
    from crossby.data import MODELS

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


def complete_delegation_modes(incomplete: str) -> list[str]:
    """Autocomplete delegation mode values."""
    from wade.models.delegation import DelegationMode

    modes = [m.value for m in DelegationMode]
    return sorted([m for m in modes if m.startswith(incomplete)])


def complete_effort_levels(incomplete: str) -> list[str]:
    """Autocomplete effort levels."""
    from crossby.models.ai import EffortLevel

    levels = [e.value for e in EffortLevel]
    return sorted([e for e in levels if e.startswith(incomplete)])

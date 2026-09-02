"""Model autocompletion reads Crossby's catalog at runtime, never a wade copy.

Every expectation here is derived from ``crossby.data.MODELS`` rather than
hardcoded, so a Crossby version bump that changes the catalog cannot make these
tests stale — it can only make them fail if ``complete_models`` stops tracking
the catalog. Re-run on every Crossby dependency change.
"""

from __future__ import annotations

import click
import pytest
import typer
from crossby.data import MODELS
from crossby.models.ai import AIToolID

from wade.cli.autocomplete import complete_models

_CLAUDE = AIToolID.CLAUDE.value
_CODEX = AIToolID.CODEX.value


def _ctx(ai: str | list[str] | None = None) -> typer.Context:
    """A Typer context carrying the ``--ai`` param exactly as Click would."""
    ctx = typer.Context(click.Command("wade"))
    if ai is not None:
        ctx.params["ai"] = ai
    return ctx


def _catalog(*tools: str) -> list[str]:
    """Sorted, deduplicated Crossby models for ``tools`` (all tools when empty)."""
    keys = tools or tuple(MODELS)
    return sorted({model for key in keys for model in MODELS.get(key, [])})


@pytest.fixture(scope="module")
def claude_prefix() -> str:
    """A prefix taken from the live catalog that filters Claude's models strictly."""
    models = _catalog(_CLAUDE)
    prefix = models[0][:8]
    assert [m for m in models if m.startswith(prefix)] != models, (
        f"prefix {prefix!r} matches every Claude model — it cannot prove filtering"
    )
    return prefix


class TestCompleteModelsWithExplicitTool:
    """With ``--ai`` present, completion stays scoped to the selected tool(s)."""

    def test_returns_the_full_catalog_for_that_tool(self) -> None:
        expected = _catalog(_CLAUDE)
        assert expected, "crossby exposes no Claude models"
        assert complete_models(_ctx(_CLAUDE), "") == expected

    def test_excludes_models_belonging_only_to_another_tool(self) -> None:
        codex_only = set(MODELS.get(_CODEX, [])) - set(MODELS.get(_CLAUDE, []))
        assert codex_only, "crossby's codex and claude catalogs no longer differ"
        assert codex_only.isdisjoint(complete_models(_ctx(_CLAUDE), ""))

    def test_filters_by_prefix(self, claude_prefix: str) -> None:
        result = complete_models(_ctx(_CLAUDE), claude_prefix)
        assert result, f"no Claude model starts with {claude_prefix!r}"
        assert result == [m for m in _catalog(_CLAUDE) if m.startswith(claude_prefix)]
        assert set(result) < set(_catalog(_CLAUDE))

    def test_repeated_flag_unions_the_selected_catalogs(self) -> None:
        assert complete_models(_ctx([_CLAUDE, _CODEX]), "") == _catalog(_CLAUDE, _CODEX)

    def test_unknown_tool_yields_no_candidates(self) -> None:
        assert complete_models(_ctx("not-a-tool"), "") == []


class TestCompleteModelsWithoutTool:
    """With no ``--ai``, completion spans the whole deduplicated catalog."""

    def test_returns_every_catalog_entry_deduplicated(self) -> None:
        expected = _catalog()
        result = complete_models(_ctx(), "")
        assert result == expected
        assert len(result) == len(set(result))
        assert len(result) < sum(len(models) for models in MODELS.values()), (
            "no tool catalogs overlap, so deduplication is untested"
        )

    def test_is_a_superset_of_the_tool_scoped_result(self) -> None:
        assert set(complete_models(_ctx(_CLAUDE), "")) < set(complete_models(_ctx(), ""))

    def test_filters_by_prefix(self, claude_prefix: str) -> None:
        result = complete_models(_ctx(), claude_prefix)
        assert result == [m for m in _catalog() if m.startswith(claude_prefix)]
        assert all(m.startswith(claude_prefix) for m in result)

    def test_unmatched_prefix_yields_no_candidates(self) -> None:
        assert complete_models(_ctx(), "zzz-no-such-model") == []

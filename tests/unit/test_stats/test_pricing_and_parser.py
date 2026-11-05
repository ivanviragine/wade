"""Price lookup and the PR/issue-body usage parser (round-trips WADE's renderers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from crossby.models.ai import ModelBreakdown, TokenUsage

from wade.services.implementation_service.usage_tracking import (
    IMPL_USAGE_MARKER_END,
    IMPL_USAGE_MARKER_START,
    REVIEW_USAGE_MARKER_END,
    REVIEW_USAGE_MARKER_START,
    append_impl_usage_entry,
    append_review_usage_entry,
)
from wade.services.task_service import (
    PLAN_SUMMARY_MARKER_END,
    PLAN_SUMMARY_MARKER_START,
    build_plan_summary_block,
)
from wade.stats.pricing import ModelPrice, PriceBook, normalize_model
from wade.stats.usage_parser import (
    IMPL_USAGE_MARKERS,
    PLAN_USAGE_MARKERS,
    REVIEW_USAGE_MARKERS,
    parse_usage_block,
    usage_fingerprint,
)

# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_input"),
    [
        ("claude-opus-5.5", 4.0),
        ("claude-opus-5-5", 4.0),
        ("Claude Opus 5.5", 4.0),
        ("claude-fable-5-1-high", 10.0),  # cursor effort suffix
        ("claude-4.6-opus-high-thinking", 5.0),  # cursor version-first naming
        ("claude-sonnet-5", 2.0),
        ("claude-haiku-5.5", 0.10),
        ("sonnet-4-6", 3.0),
    ],
)
def test_builtin_claude_prices(name: str, expected_input: float) -> None:
    price = PriceBook().price_for(name)
    assert price is not None and price.input == expected_input


@pytest.mark.parametrize("name", ["gpt-5-codex", "auto", "claude-opus-4-20250514", "", None])
def test_unknown_models_are_unpriced(name: str | None) -> None:
    assert PriceBook().price_for(name) is None


def test_cost_math_and_cached_default() -> None:
    assert PriceBook().cost("claude-opus-5.5", 1000, 500, 9000) == pytest.approx(0.0158)
    assert ModelPrice(input=2.0, output=8.0).cached_rate == pytest.approx(0.2)


def test_user_overrides_win_and_match_prefix(tmp_path: Path) -> None:
    f = tmp_path / "pricing.yml"
    f.write_text(
        "models:\n  gpt-5: {input: 1.0, output: 8.0, cached: 0.1}\n"
        "  claude-opus-5.5: {input: 1.0, output: 1.0}\n"
    )
    book = PriceBook.load(f)
    codex = book.price_for("gpt-5-codex-high")
    assert codex is not None and codex.output == 8.0
    opus = book.price_for("claude-opus-5-5")
    assert opus is not None and opus.input == 1.0


def test_unreadable_pricing_file_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "pricing.yml"
    f.write_text("models: [not, a, mapping")
    assert PriceBook.load(f).price_for("claude-opus-5.5") is not None


def test_normalize_model() -> None:
    assert normalize_model(" Claude Opus 5.5 ") == "claude-opus-5-5"


# ---------------------------------------------------------------------------
# usage parser
# ---------------------------------------------------------------------------


def test_marker_constants_match_writers() -> None:
    assert IMPL_USAGE_MARKERS == (IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END)
    assert REVIEW_USAGE_MARKERS == (REVIEW_USAGE_MARKER_START, REVIEW_USAGE_MARKER_END)
    assert PLAN_USAGE_MARKERS == (PLAN_SUMMARY_MARKER_START, PLAN_SUMMARY_MARKER_END)


def test_round_trips_implementation_sessions() -> None:
    multi = TokenUsage(
        model_breakdown=[
            ModelBreakdown(model="a", input_tokens=10, output_tokens=5, cached_tokens=1),
            ModelBreakdown(model="b", input_tokens=20, output_tokens=6),
        ]
    )
    body = append_impl_usage_entry("PR text", ai_tool="claude", token_usage=multi)
    body = append_impl_usage_entry(
        body,
        ai_tool="copilot",
        model="gpt-5",
        token_usage=TokenUsage(input_tokens=7, output_tokens=3, premium_requests=2),
    )
    body = append_impl_usage_entry(body, ai_tool="vscode", token_usage=None)

    s1, s2, s3 = parse_usage_block(body, IMPL_USAGE_MARKERS)
    assert (s1.ai_tool, s1.total_tokens, s1.input_tokens, s1.cached_tokens) == ("claude", 42, 30, 1)
    assert [m.model for m in s1.models] == ["a", "b"]
    assert s1.models[1].input_tokens == 20
    assert (s2.model, s2.total_tokens, s2.premium_requests) == ("gpt-5", 10, 2)
    assert s2.cached_tokens is None
    assert s3.ai_tool == "vscode" and not s3.has_tokens


def test_round_trips_review_and_legacy_layout() -> None:
    body = append_review_usage_entry(
        "", ai_tool="codex", token_usage=TokenUsage(total_tokens=500, input_tokens=400)
    )
    [s] = parse_usage_block(body, REVIEW_USAGE_MARKERS)
    assert (s.ai_tool, s.total_tokens, s.input_tokens) == ("codex", 500, 400)

    legacy = (
        f"{IMPL_USAGE_MARKER_START}\n## Token Usage\n| Metric | Value |\n| --- | --- |\n"
        f"| Tool | `claude` |\n| Total tokens | **1,234** |\n{IMPL_USAGE_MARKER_END}"
    )
    [old] = parse_usage_block(legacy, IMPL_USAGE_MARKERS)
    assert old.index == 1 and old.total_tokens == 1234


def test_round_trips_plan_summary() -> None:
    block = build_plan_summary_block(
        ai_tool="claude",
        model="claude-opus-5.5",
        total_tokens=1500,
        input_tokens=1000,
        output_tokens=300,
        cached_tokens=200,
        per_issue_estimate=750,
        model_breakdown=[{"model": "claude-opus-5.5", "input": 1000, "output": 300, "cached": 200}],
    )
    [p] = parse_usage_block("issue body\n\n" + block, PLAN_USAGE_MARKERS)
    assert (p.total_tokens, p.per_issue_estimate, p.model) == (1500, 750, "claude-opus-5.5")
    assert p.models[0].cached_tokens == 200


def test_missing_or_unterminated_block_yields_nothing() -> None:
    assert parse_usage_block("nothing here", IMPL_USAGE_MARKERS) == []
    assert parse_usage_block(IMPL_USAGE_MARKER_START + " | x |", IMPL_USAGE_MARKERS) == []


def test_fingerprint_ignores_model_and_tool_case() -> None:
    a = usage_fingerprint("implement", "Claude", 1, 2, None, 3)
    b = usage_fingerprint("implement", "claude", 1, 2, None, 3)
    assert a == b
    assert a != usage_fingerprint("review_pr_comments", "claude", 1, 2, None, 3)

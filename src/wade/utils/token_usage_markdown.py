"""Shared markdown rendering for plan/implementation token usage blocks."""

from __future__ import annotations

from typing import TypedDict


class TokenUsageBreakdownRow(TypedDict):
    """Normalized per-model token usage row."""

    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int


def _format_count(n: int | None) -> str:
    if n is None:
        return ""
    return f"{n:,}"


def build_token_usage_block(
    *,
    marker_start: str,
    marker_end: str,
    heading: str,
    ai_tool: str | None = None,
    model: str | None = None,
    total_tokens: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    extra_metric_rows: list[str] | None = None,
    premium_requests: int | None = None,
    model_breakdown: list[TokenUsageBreakdownRow] | None = None,
) -> str:
    """Render a token-usage markdown block with optional per-model rows."""
    lines = [
        marker_start,
        "",
        heading,
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]

    if ai_tool:
        lines.append(f"| Tool | `{ai_tool}` |")
    if model:
        lines.append(f"| Model | `{model}` |")

    if total_tokens is not None and total_tokens > 0:
        lines.append(f"| Total tokens | **{_format_count(total_tokens)}** |")
        if input_tokens is not None:
            lines.append(f"| Input tokens | **{_format_count(input_tokens)}** |")
        if output_tokens is not None:
            lines.append(f"| Output tokens | **{_format_count(output_tokens)}** |")
        if cached_tokens is not None:
            lines.append(f"| Cached tokens | **{_format_count(cached_tokens)}** |")
    else:
        lines.append("| Total tokens | *unavailable* |")

    if extra_metric_rows:
        lines.extend(extra_metric_rows)

    if premium_requests is not None and premium_requests > 0:
        lines.append(f"| Premium requests (est.) | **{premium_requests}** |")

    if model_breakdown:
        for row in model_breakdown:
            inp = _format_count(row["input_tokens"])
            out = _format_count(row["output_tokens"])
            parts = [f"**{inp}** in", f"**{out}** out"]
            if row["cached_tokens"]:
                parts.append(f"**{_format_count(row['cached_tokens'])}** cached")
            lines.append(f"| `{row['model']}` | {' · '.join(parts)} |")

    lines.append("")
    lines.append(marker_end)

    return "\n".join(lines)

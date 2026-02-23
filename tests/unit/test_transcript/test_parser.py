"""Tests for transcript parsing — token extraction from AI CLI output."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghaiw.ai_tools.transcript import (
    allocate_tokens,
    extract_model_breakdown_from_text,
    extract_premium_requests_from_text,
    extract_token_usage_from_text,
    format_count,
    parse_claude_transcript,
    parse_codex_transcript,
    parse_copilot_transcript,
    parse_gemini_transcript,
    parse_token_count,
    read_transcript_excerpt,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "transcripts"


# ---------------------------------------------------------------------------
# parse_token_count
# ---------------------------------------------------------------------------


class TestParseTokenCount:
    def test_plain_integer(self) -> None:
        assert parse_token_count("614") == 614

    def test_with_commas(self) -> None:
        assert parse_token_count("12,345") == 12_345

    def test_k_suffix(self) -> None:
        assert parse_token_count("1.2k") == 1_200

    def test_K_suffix_uppercase(self) -> None:
        assert parse_token_count("1.2K") == 1_200

    def test_m_suffix(self) -> None:
        assert parse_token_count("2m") == 2_000_000

    def test_decimal_m(self) -> None:
        assert parse_token_count("1.5m") == 1_500_000

    def test_with_whitespace(self) -> None:
        assert parse_token_count("  1,234  ") == 1_234

    def test_with_underscores(self) -> None:
        assert parse_token_count("1_234") == 1_234

    def test_trailing_period(self) -> None:
        assert parse_token_count("1234.") == 1_234

    def test_empty_string(self) -> None:
        assert parse_token_count("") is None

    def test_none_input(self) -> None:
        assert parse_token_count("") is None

    def test_invalid(self) -> None:
        assert parse_token_count("abc") is None

    def test_just_k(self) -> None:
        assert parse_token_count("k") is None

    def test_large_number_with_commas(self) -> None:
        assert parse_token_count("1,234,567") == 1_234_567

    def test_copilot_style(self) -> None:
        """Copilot reports like '597.8k'."""
        assert parse_token_count("597.8k") == 597_800


# ---------------------------------------------------------------------------
# format_count
# ---------------------------------------------------------------------------


class TestFormatCount:
    def test_small_number(self) -> None:
        assert format_count(614) == "614"

    def test_thousands(self) -> None:
        assert format_count(12_345) == "12,345"

    def test_millions(self) -> None:
        assert format_count(1_234_567) == "1,234,567"

    def test_none(self) -> None:
        assert format_count(None) == ""

    def test_zero(self) -> None:
        assert format_count(0) == "0"


# ---------------------------------------------------------------------------
# Claude footer extraction
# ---------------------------------------------------------------------------


class TestClaudeExtraction:
    def test_basic_footer(self) -> None:
        text = "in:614 out:94"
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 614
        assert usage.output_tokens == 94
        assert usage.total_tokens == 708

    def test_with_cached(self) -> None:
        text = "in:614 out:94 cached:2,345"
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 614
        assert usage.output_tokens == 94
        assert usage.cached_tokens == 2_345
        assert usage.total_tokens == 3_053

    def test_multiline(self) -> None:
        text = "Some output\nin:1000 out:200\nDone."
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 1_000
        assert usage.output_tokens == 200

    def test_with_space_after_colon(self) -> None:
        text = "in: 614 out: 94"
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 614
        assert usage.output_tokens == 94

    def test_fixture_file(self) -> None:
        usage = parse_claude_transcript(FIXTURES / "claude_session.txt")
        assert usage.input_tokens == 12_345
        assert usage.output_tokens == 2_456
        assert usage.cached_tokens == 8_901
        assert usage.total_tokens == 23_702
        assert usage.raw_transcript_path is not None


# ---------------------------------------------------------------------------
# Copilot extraction
# ---------------------------------------------------------------------------


class TestCopilotExtraction:
    def test_summary_line(self) -> None:
        text = "1.3m in, 14.6k out, 1.1m cached"
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 1_300_000
        assert usage.output_tokens == 14_600
        assert usage.cached_tokens == 1_100_000

    def test_per_model_breakdown(self) -> None:
        text = (
            "gpt-4o 736.6k in, 8.8k out, 625.5k cached (Est. 2 Premium requests)\n"
            "claude-opus-4 597.8k in, 5.8k out, 520.9k cached\n"
        )
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens is not None
        assert usage.output_tokens is not None
        assert len(usage.model_breakdown) == 2
        assert usage.model_breakdown[0].model == "gpt-4o"
        assert usage.model_breakdown[0].premium_requests == 2
        assert usage.model_breakdown[1].model == "claude-opus-4"

    def test_premium_requests(self) -> None:
        text = (
            "gpt-4o 100k in, 10k out, 50k cached (Est. 3 Premium requests)\n"
            "Total usage est. 5 Premium requests\n"
        )
        premium = extract_premium_requests_from_text(text)
        assert premium == 5

    def test_premium_fallback_to_first(self) -> None:
        text = "Some model 100 in, 10 out, 50 cached (Est. 2 Premium requests)"
        premium = extract_premium_requests_from_text(text)
        assert premium == 2

    def test_fixture_file(self) -> None:
        usage = parse_copilot_transcript(FIXTURES / "copilot_session.txt")
        assert usage.input_tokens is not None
        assert usage.output_tokens is not None
        assert usage.premium_requests == 5
        assert len(usage.model_breakdown) == 2
        assert usage.model_breakdown[0].model == "gpt-4o"
        assert usage.model_breakdown[1].model == "claude-opus-4"


# ---------------------------------------------------------------------------
# Gemini table extraction
# ---------------------------------------------------------------------------


class TestGeminiExtraction:
    def test_single_model(self) -> None:
        text = "gemini-2.0-flash 12 45234 12345 8901"
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 45_234
        assert usage.cached_tokens == 12_345
        assert usage.output_tokens == 8_901

    def test_multi_model(self) -> None:
        text = (
            "gemini-2.0-flash 12 45234 12345 8901\n"
            "gemini-2.5-pro 3 15000 5000 3000\n"
        )
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 60_234
        assert usage.output_tokens == 11_901
        assert usage.cached_tokens == 17_345
        assert len(usage.model_breakdown) == 2
        assert usage.model_breakdown[0].model == "gemini-2.0-flash"
        assert usage.model_breakdown[1].model == "gemini-2.5-pro"

    def test_model_breakdown_detail(self) -> None:
        text = "gemini-2.0-flash 12 45234 12345 8901"
        breakdowns = extract_model_breakdown_from_text(text)
        assert len(breakdowns) == 1
        assert breakdowns[0].model == "gemini-2.0-flash"
        assert breakdowns[0].input_tokens == 45_234
        assert breakdowns[0].cached_tokens == 12_345
        assert breakdowns[0].output_tokens == 8_901

    def test_fixture_file(self) -> None:
        usage = parse_gemini_transcript(FIXTURES / "gemini_session.txt")
        assert usage.input_tokens == 60_234
        assert usage.output_tokens == 11_901
        assert usage.cached_tokens == 17_345
        assert len(usage.model_breakdown) == 2


# ---------------------------------------------------------------------------
# Codex extraction
# ---------------------------------------------------------------------------


class TestCodexExtraction:
    def test_full_footer(self) -> None:
        text = "Token usage: total=9,490 input=9,268 (+ 7,296 cached) output=222"
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 9_490
        assert usage.input_tokens == 9_268
        assert usage.output_tokens == 222
        assert usage.cached_tokens == 7_296

    def test_without_cached(self) -> None:
        text = "Token usage: total=500 input=400 output=100"
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 500
        assert usage.input_tokens == 400
        assert usage.output_tokens == 100
        assert usage.cached_tokens is None

    def test_fixture_file(self) -> None:
        usage = parse_codex_transcript(FIXTURES / "codex_session.txt")
        assert usage.total_tokens == 9_490
        assert usage.input_tokens == 9_268
        assert usage.output_tokens == 222
        assert usage.cached_tokens == 7_296


# ---------------------------------------------------------------------------
# Generic fallback extraction
# ---------------------------------------------------------------------------


class TestGenericExtraction:
    def test_total_tokens(self) -> None:
        text = "Total tokens: 5,678"
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 5_678

    def test_all_fields(self) -> None:
        text = (
            "Total tokens: 5,678\n"
            "Input tokens: 4,000\n"
            "Output tokens: 1,678\n"
        )
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 5_678
        assert usage.input_tokens == 4_000
        assert usage.output_tokens == 1_678

    def test_session_tokens_keyword(self) -> None:
        text = "Session tokens: 1,234"
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 1_234

    def test_fixture_file(self) -> None:
        path = FIXTURES / "generic_session.txt"
        text = read_transcript_excerpt(path)
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens == 5_678
        assert usage.input_tokens == 4_000
        assert usage.output_tokens == 1_678


# ---------------------------------------------------------------------------
# Priority / cascading tests
# ---------------------------------------------------------------------------


class TestCascadingPriority:
    def test_gemini_takes_priority(self) -> None:
        """Gemini table format should win over generic token mentions."""
        text = (
            "I used about 1000 tokens earlier.\n"
            "gemini-2.0-flash 5 2000 500 1000\n"
            "Total tokens: 99999\n"  # Should be ignored
        )
        usage = extract_token_usage_from_text(text)
        # Gemini table values should win
        assert usage.input_tokens == 2_000
        assert usage.output_tokens == 1_000

    def test_copilot_over_generic(self) -> None:
        """Copilot format should win over generic fallback."""
        text = (
            "500k in, 10k out, 400k cached\n"
            "Total tokens: 1\n"  # Generic — should be ignored
        )
        usage = extract_token_usage_from_text(text)
        assert usage.input_tokens == 500_000
        assert usage.output_tokens == 10_000

    def test_empty_text(self) -> None:
        usage = extract_token_usage_from_text("")
        assert usage.total_tokens is None
        assert usage.input_tokens is None

    def test_no_token_info(self) -> None:
        text = "Hello, I completed the task. Everything looks good!"
        usage = extract_token_usage_from_text(text)
        assert usage.total_tokens is None


# ---------------------------------------------------------------------------
# read_transcript_excerpt
# ---------------------------------------------------------------------------


class TestReadTranscriptExcerpt:
    def test_nonexistent_file(self, tmp_path: Path) -> None:
        result = read_transcript_excerpt(tmp_path / "nonexistent.txt")
        assert result == ""

    def test_control_characters_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "transcript.txt"
        # Write bytes with control chars
        path.write_bytes(b"Hello\x00\x01\x02World\nin:100 out:50\n")
        text = read_transcript_excerpt(path)
        assert "HelloWorld" in text
        assert "in:100" in text

    def test_max_lines_respected(self, tmp_path: Path) -> None:
        path = tmp_path / "long.txt"
        lines = [f"line {i}" for i in range(1000)]
        lines.append("in:999 out:111")
        path.write_text("\n".join(lines))

        text = read_transcript_excerpt(path, max_lines=10)
        assert "in:999" in text
        # Older lines should be truncated
        assert "line 0" not in text

    def test_fixture_file(self) -> None:
        text = read_transcript_excerpt(FIXTURES / "claude_session.txt")
        assert "in:12,345" in text


# ---------------------------------------------------------------------------
# allocate_tokens
# ---------------------------------------------------------------------------


class TestAllocateTokens:
    def test_single_issue(self) -> None:
        assert allocate_tokens(1000, [50]) == [1000]

    def test_even_distribution(self) -> None:
        """When line counts are all zero, distribute evenly."""
        result = allocate_tokens(1000, [0, 0, 0])
        assert sum(result) == 1000
        assert result == [334, 333, 333]

    def test_proportional(self) -> None:
        result = allocate_tokens(1000, [100, 200, 300])
        assert sum(result) == 1000
        assert result[0] < result[1] < result[2]

    def test_remainder_goes_to_last(self) -> None:
        """Last issue gets remainder to ensure exact sum."""
        result = allocate_tokens(1000, [100, 200, 300])
        assert sum(result) == 1000

    def test_empty_list(self) -> None:
        assert allocate_tokens(1000, []) == []

    def test_two_equal_issues(self) -> None:
        result = allocate_tokens(100, [50, 50])
        assert sum(result) == 100
        assert result == [50, 50]

    def test_uneven_remainder(self) -> None:
        result = allocate_tokens(10, [0, 0, 0])
        assert sum(result) == 10
        # 10 / 3 = 3 remainder 1
        assert result == [4, 3, 3]

    def test_proportional_example(self) -> None:
        """Example from behavioral reference."""
        result = allocate_tokens(1000, [100, 200, 300])
        # issue 0: floor(1000 * 100 / 600) = 166
        # issue 1: floor(1000 * 200 / 600) = 333
        # issue 2: 1000 - 166 - 333 = 501
        assert result == [166, 333, 501]
        assert sum(result) == 1000


# ---------------------------------------------------------------------------
# Model breakdown extraction
# ---------------------------------------------------------------------------


class TestModelBreakdown:
    def test_gemini_breakdown(self) -> None:
        text = (
            "gemini-2.0-flash 12 45234 12345 8901\n"
            "gemini-2.5-pro 3 15000 5000 3000\n"
        )
        breakdowns = extract_model_breakdown_from_text(text)
        assert len(breakdowns) == 2
        assert breakdowns[0].model == "gemini-2.0-flash"
        assert breakdowns[0].input_tokens == 45_234
        assert breakdowns[1].model == "gemini-2.5-pro"
        assert breakdowns[1].input_tokens == 15_000

    def test_copilot_breakdown(self) -> None:
        text = (
            "gpt-4o 736.6k in, 8.8k out, 625.5k cached (Est. 2 Premium requests)\n"
            "claude-opus-4 597.8k in, 5.8k out, 520.9k cached\n"
        )
        breakdowns = extract_model_breakdown_from_text(text)
        assert len(breakdowns) == 2
        assert breakdowns[0].model == "gpt-4o"
        assert breakdowns[0].premium_requests == 2
        assert breakdowns[1].model == "claude-opus-4"
        assert breakdowns[1].premium_requests == 0

    def test_empty_text(self) -> None:
        assert extract_model_breakdown_from_text("") == []

    def test_no_breakdown(self) -> None:
        assert extract_model_breakdown_from_text("Just some text") == []


# ---------------------------------------------------------------------------
# Premium request extraction
# ---------------------------------------------------------------------------


class TestPremiumRequests:
    def test_total_usage_line(self) -> None:
        text = "Total usage est. 5 Premium requests"
        assert extract_premium_requests_from_text(text) == 5

    def test_inline_estimate(self) -> None:
        text = "gpt-4o 100k in, 10k out, 50k cached (Est. 3 Premium requests)"
        assert extract_premium_requests_from_text(text) == 3

    def test_prefers_total_line(self) -> None:
        text = (
            "gpt-4o 100k in, 10k out, 50k cached (Est. 2 Premium requests)\n"
            "Total usage est. 7 Premium requests\n"
        )
        assert extract_premium_requests_from_text(text) == 7

    def test_none_when_absent(self) -> None:
        text = "Just a regular session output"
        assert extract_premium_requests_from_text(text) is None

    def test_case_insensitive(self) -> None:
        text = "Total usage est. 3 premium Requests"
        assert extract_premium_requests_from_text(text) == 3

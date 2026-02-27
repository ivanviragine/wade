"""Transcript parsing — extract token usage from AI CLI session output.

Supports: Claude, Copilot, Gemini, Codex, and a generic fallback.
Each AI tool has a distinct output format for reporting token usage.

Behavioral reference: lib/task/tokens.sh (734 lines)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ghaiw.models.ai import ModelBreakdown, TokenUsage

# ---------------------------------------------------------------------------
# Numeric parsing / formatting
# ---------------------------------------------------------------------------


def parse_token_count(raw: str) -> int | None:
    """Parse a human-readable token count to an integer.

    Handles formats: "12,345", "1.2k", "2m", "614", "1_234", " 1,234 "
    Returns None if the input is not parseable.

    Behavioral reference: lib/task/tokens.sh:_task_parse_token_count()
    """
    if not raw:
        return None

    # Normalize: lowercase, strip whitespace, remove commas/underscores/trailing period
    s = raw.strip().lower()
    s = s.replace(",", "").replace("_", "")
    s = s.rstrip(".")

    if not s:
        return None

    # Detect multiplier suffix
    multiplier = 1
    if s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    elif s.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]

    if not s:
        return None

    # Validate: must be a number (possibly decimal)
    if not re.match(r"^\d+(\.\d+)?$", s):
        return None

    value = float(s) * multiplier
    return round(value)


def format_count(n: int | None) -> str:
    """Format an integer with thousands separators.

    1234567 → "1,234,567"
    None → ""

    Behavioral reference: lib/task/tokens.sh:_task_format_count()
    """
    if n is None:
        return ""
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Transcript text extraction
# ---------------------------------------------------------------------------


def read_transcript_excerpt(transcript_path: Path, max_lines: int = 400) -> str:
    """Read the tail of a transcript file, stripping control characters.

    AI CLI session footers (with token summaries) appear near the end.
    Reading only the tail avoids false positives from earlier discussion
    and keeps parsing fast.

    Behavioral reference: lib/task/tokens.sh:_task_read_transcript_excerpt()
    """
    if not transcript_path.is_file():
        return ""

    try:
        content = transcript_path.read_bytes()
    except OSError:
        return ""

    # Strip control characters, keeping TAB (0x09), LF (0x0A), CR (0x0D),
    # and printable ASCII (0x20-0x7E)
    cleaned = bytearray()
    for b in content:
        if b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E:
            cleaned.append(b)

    text = cleaned.decode("ascii", errors="ignore")
    # Strip remaining ANSI parameter fragments left after ESC removal.
    # ESC is 0x1B (stripped above), but the trailing "[NNN;NNNm" sequences
    # remain as literal ASCII and must be removed too, otherwise token
    # counts like "in:[33m166[0m" fail to match.
    # Mirrors: gsub(/\[[0-9;?]*[[:alpha:]]/, "", raw) in tokens.sh
    text = re.sub(r"\[[0-9;?]*[a-zA-Z]", "", text)
    # Replace CR with newline
    text = text.replace("\r", "\n")

    # Take last max_lines lines
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[-max_lines:]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool-specific token usage extractors
# ---------------------------------------------------------------------------


def _extract_last_number(text: str) -> str | None:
    """Extract the last numeric fragment from a string.

    Matches: "12,345", "1.2k", "2m", "614"
    """
    matches: list[str] = re.findall(r"[\d,]+(?:\.\d+)?[kKmM]?", text)
    if matches:
        return matches[-1]
    return None


def _extract_gemini_table(text: str) -> tuple[TokenUsage | None, list[ModelBreakdown]]:
    """Parse Gemini CLI 'Model Usage' table rows.

    Format: <model> <requests> <input> <cache> <output>
    Example: gemini-2.0-flash 12 45,234 12,345 8,901

    This is the most structured format and gets highest priority.
    """
    # Pattern: model_name requests input_tokens cache_tokens output_tokens
    row_re = re.compile(
        r"^"
        r"([a-z0-9._-]+)"  # model name
        r"\s+"
        r"(\d+)"  # request count
        r"\s+"
        r"([\d,]+(?:\.\d+)?[kKmM]?)"  # input tokens
        r"\s+"
        r"([\d,]+(?:\.\d+)?[kKmM]?)"  # cache tokens
        r"\s+"
        r"([\d,]+(?:\.\d+)?[kKmM]?)"  # output tokens
        r"\s*$",
        re.MULTILINE,
    )

    breakdowns: dict[str, list[int]] = {}  # model -> [input, output, cached]
    model_order: list[str] = []

    for m in row_re.finditer(text):
        model = m.group(1)
        inp = parse_token_count(m.group(3)) or 0
        cache = parse_token_count(m.group(4)) or 0
        out = parse_token_count(m.group(5)) or 0

        if model not in breakdowns:
            breakdowns[model] = [0, 0, 0]
            model_order.append(model)

        breakdowns[model][0] += inp
        breakdowns[model][1] += out
        breakdowns[model][2] += cache

    if not breakdowns:
        return None, []

    total_in = sum(v[0] for v in breakdowns.values())
    total_out = sum(v[1] for v in breakdowns.values())
    total_cache = sum(v[2] for v in breakdowns.values())
    total = total_in + total_out + total_cache

    model_breakdowns = [
        ModelBreakdown(
            model=model,
            input_tokens=breakdowns[model][0],
            output_tokens=breakdowns[model][1],
            cached_tokens=breakdowns[model][2],
        )
        for model in model_order
    ]

    usage = TokenUsage(
        total_tokens=total if total > 0 else None,
        input_tokens=total_in if total_in > 0 else None,
        output_tokens=total_out if total_out > 0 else None,
        cached_tokens=total_cache if total_cache > 0 else None,
        model_breakdown=model_breakdowns,
    )

    return usage, model_breakdowns


def _extract_copilot_summary(text: str) -> tuple[TokenUsage | None, list[ModelBreakdown]]:
    """Parse Copilot CLI summary format.

    Summary: 597.8k in, 5.8k out, 520.9k cached
    Per-model: gpt-4o 736.6k in, 8.8k out, 625.5k cached (Est. 2 Premium requests)
    """
    # Per-model breakdown pattern (more specific, try first for breakdown)
    model_re = re.compile(
        r"^"
        r"(\S+)"  # model name
        r"\s+"
        r"([\d,.]+[kKmM]?)\s+in,\s+"
        r"([\d,.]+[kKmM]?)\s+out,\s+"
        r"([\d,.]+[kKmM]?)\s+cached"
        r"(.*?)$",
        re.MULTILINE,
    )

    # Summary line pattern (no model prefix)
    summary_re = re.compile(
        r"([\d,.]+[kKmM]?)\s+in,\s+"
        r"([\d,.]+[kKmM]?)\s+out,\s+"
        r"([\d,.]+[kKmM]?)\s+cached",
    )

    # Premium request pattern
    premium_re = re.compile(
        r"est\.?\s*(\d+(?:\.\d+)?)\s+premium\s+requests?",
        re.IGNORECASE,
    )

    breakdowns: dict[str, list[int]] = {}  # model -> [in, out, cached, premium]
    model_order: list[str] = []
    total_premium = 0

    for m in model_re.finditer(text):
        model = m.group(1)
        inp = parse_token_count(m.group(2)) or 0
        out = parse_token_count(m.group(3)) or 0
        cached = parse_token_count(m.group(4)) or 0
        tail = m.group(5)

        premium = 0
        pm = premium_re.search(tail)
        if pm:
            premium = parse_token_count(pm.group(1)) or 0
            total_premium += premium

        if model not in breakdowns:
            breakdowns[model] = [0, 0, 0, 0]
            model_order.append(model)

        breakdowns[model][0] += inp
        breakdowns[model][1] += out
        breakdowns[model][2] += cached
        breakdowns[model][3] += premium

    # Also check for a standalone summary line (captures session total)
    total_in: int | None = None
    total_out: int | None = None
    total_cached: int | None = None

    for line in text.splitlines():
        sm = summary_re.search(line)
        if sm:
            # If the line doesn't start with a model name (i.e., it's the summary)
            # or if we have no breakdowns yet, use it as the total
            line_stripped = line.strip()
            has_model_prefix = model_re.match(line_stripped) is not None
            if not has_model_prefix:
                total_in = parse_token_count(sm.group(1))
                total_out = parse_token_count(sm.group(2))
                total_cached = parse_token_count(sm.group(3))

    # If no standalone summary, sum from breakdowns
    if total_in is None and breakdowns:
        total_in = sum(v[0] for v in breakdowns.values())
        total_out = sum(v[1] for v in breakdowns.values())
        total_cached = sum(v[2] for v in breakdowns.values())

    if total_in is None and total_out is None:
        return None, []

    total = (total_in or 0) + (total_out or 0) + (total_cached or 0)

    # Check for "Total usage est. N Premium requests" line
    for line in text.splitlines():
        if "total usage" in line.lower():
            pm = premium_re.search(line)
            if pm:
                total_premium = parse_token_count(pm.group(1)) or 0

    model_breakdowns = [
        ModelBreakdown(
            model=model,
            input_tokens=breakdowns[model][0],
            output_tokens=breakdowns[model][1],
            cached_tokens=breakdowns[model][2],
            premium_requests=breakdowns[model][3],
        )
        for model in model_order
    ]

    usage = TokenUsage(
        total_tokens=total if total > 0 else None,
        input_tokens=total_in,
        output_tokens=total_out,
        cached_tokens=total_cached,
        premium_requests=total_premium if total_premium > 0 else None,
        model_breakdown=model_breakdowns,
    )

    return usage, model_breakdowns


def _extract_claude_footer(text: str) -> TokenUsage | None:
    """Parse Claude Code status footer.

    Format: ``Sonnet 4.6 [] 30% in:56.0k out:17.5k``

    Claude Code redraws the status bar on every response, so the transcript
    may contain many occurrences.  Use the LAST match to get final session totals.
    Also extracts the model name from the same status bar lines — display names
    like ``Sonnet 4.6`` are normalized to API IDs like ``claude-sonnet-4-6``.
    """
    in_re = re.compile(r"in:\s?([\d,]+(?:\.\d+)?[kKmM]?)")
    out_re = re.compile(r"out:\s?([\d,]+(?:\.\d+)?[kKmM]?)")
    cache_re = re.compile(r"cache[d]?:\s?([\d,]+(?:\.\d+)?[kKmM]?)")

    inp: int | None = None
    out: int | None = None
    cached: int | None = None

    in_matches: list[str] = in_re.findall(text)
    out_matches: list[str] = out_re.findall(text)
    cache_matches: list[str] = cache_re.findall(text)

    if in_matches:
        inp = parse_token_count(in_matches[-1])
    if out_matches:
        out = parse_token_count(out_matches[-1])
    if cache_matches:
        cached = parse_token_count(cache_matches[-1])

    if inp is None and out is None:
        return None

    total = (inp or 0) + (out or 0) + (cached or 0)

    # Model detection from status bar lines containing both in: and out:
    model_name: str | None = None
    display_re = re.compile(r"(Sonnet|Opus|Haiku)\s+(\d+\.\d+)", re.IGNORECASE)
    api_re = re.compile(r"(claude-(?:opus|sonnet|haiku)-\d+-\d+(?:-\d+)?)")
    status_re = re.compile(r".*in:\s?[\d,].*out:\s?[\d,].*")

    for m in status_re.finditer(text):
        line = m.group(0)
        dm = display_re.search(line)
        if dm:
            model_name = f"claude-{dm.group(1).lower()}-{dm.group(2)}"
        else:
            am = api_re.search(line)
            if am:
                model_name = am.group(1)

    breakdown: list[ModelBreakdown] = []
    if model_name:
        breakdown = [
            ModelBreakdown(
                model=model_name,
                input_tokens=inp or 0,
                output_tokens=out or 0,
                cached_tokens=cached or 0,
            )
        ]

    return TokenUsage(
        total_tokens=total if total > 0 else None,
        input_tokens=inp,
        output_tokens=out,
        cached_tokens=cached,
        model_breakdown=breakdown,
    )


def _extract_codex_footer(text: str) -> TokenUsage | None:
    """Parse Codex CLI footer.

    Format: Token usage: total=9,490 input=9,268 (+ 7,296 cached) output=222
    """
    total_re = re.compile(r"total\s*=\s*([\d,]+(?:\.\d+)?[kKmM]?)")
    input_re = re.compile(r"input\s*=\s*([\d,]+(?:\.\d+)?[kKmM]?)")
    output_re = re.compile(r"output\s*=\s*([\d,]+(?:\.\d+)?[kKmM]?)")
    cached_re = re.compile(r"\+\s*([\d,]+(?:\.\d+)?[kKmM]?)\s+cached")

    # Only match if we see "token" + "usage" in the text
    token_usage_re = re.compile(r"token\s+usage:", re.IGNORECASE)
    if not token_usage_re.search(text):
        return None

    total: int | None = None
    inp: int | None = None
    out: int | None = None
    cached: int | None = None

    tm = total_re.search(text)
    im = input_re.search(text)
    om = output_re.search(text)
    cm = cached_re.search(text)

    if tm:
        total = parse_token_count(tm.group(1))
    if im:
        inp = parse_token_count(im.group(1))
    if om:
        out = parse_token_count(om.group(1))
    if cm:
        cached = parse_token_count(cm.group(1))

    if total is None and inp is None and out is None:
        return None

    # Reconstruct total from parts if missing
    if total is None or total <= 0:
        total = (inp or 0) + (out or 0) + (cached or 0)

    return TokenUsage(
        total_tokens=total if total > 0 else None,
        input_tokens=inp,
        output_tokens=out,
        cached_tokens=cached,
    )


def _extract_generic_tokens(text: str) -> TokenUsage | None:
    """Generic fallback: look for lines containing 'token' + keywords.

    Matches patterns like:
        Total tokens: 1,234
        Input tokens: 900
        Output tokens: 334
        Cached tokens: 100
    """
    total: int | None = None
    inp: int | None = None
    out: int | None = None
    cached: int | None = None

    total_re = re.compile(
        r"(?:total|session|overall)\s+tokens?\s*[:\-=]?\s*([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    )
    input_re = re.compile(
        r"(?:input|prompt)\s+tokens?\s*[:\-=]?\s*([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    )
    output_re = re.compile(
        r"(?:output|completion)\s+tokens?\s*[:\-=]?\s*([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    )
    cache_re = re.compile(
        r"cached?\s+tokens?\s*[:\-=]?\s*([\d,]+(?:\.\d+)?[kKmM]?)",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        if "token" not in line.lower():
            continue

        tm = total_re.search(line)
        if tm and total is None:
            total = parse_token_count(tm.group(1))

        im = input_re.search(line)
        if im and inp is None:
            inp = parse_token_count(im.group(1))

        om = output_re.search(line)
        if om and out is None:
            out = parse_token_count(om.group(1))

        cm = cache_re.search(line)
        if cm and cached is None:
            cached = parse_token_count(cm.group(1))

    if total is None and inp is None and out is None:
        return None

    # Reconstruct total from parts if missing
    if total is None or total <= 0:
        total = (inp or 0) + (out or 0) + (cached or 0)

    return TokenUsage(
        total_tokens=total if total > 0 else None,
        input_tokens=inp,
        output_tokens=out,
        cached_tokens=cached,
    )


# ---------------------------------------------------------------------------
# Unified extraction (cascading strategy)
# ---------------------------------------------------------------------------


def extract_token_usage_from_text(text: str) -> TokenUsage:
    """Extract token usage from transcript text using cascading strategies.

    Priority order (most structured → least):
    1. Gemini table format
    2. Copilot summary format
    3. Claude footer format
    4. Codex footer format
    5. Generic keyword-based fallback

    Behavioral reference: lib/task/tokens.sh:_task_extract_token_usage_from_text()
    """
    if not text.strip():
        return TokenUsage()

    # Strategy 1: Gemini table (most structured, highest priority)
    gemini_usage, _gemini_breakdowns = _extract_gemini_table(text)
    if gemini_usage is not None:
        return gemini_usage

    # Strategy 2: Copilot summary (includes per-model + premium)
    copilot_usage, _copilot_breakdowns = _extract_copilot_summary(text)
    if copilot_usage is not None:
        return copilot_usage

    # Strategy 3: Claude footer
    claude_usage = _extract_claude_footer(text)
    if claude_usage is not None:
        return claude_usage

    # Strategy 4: Codex footer
    codex_usage = _extract_codex_footer(text)
    if codex_usage is not None:
        return codex_usage

    # Strategy 5: Generic fallback
    generic_usage = _extract_generic_tokens(text)
    if generic_usage is not None:
        return generic_usage

    return TokenUsage()


def extract_model_breakdown_from_text(text: str) -> list[ModelBreakdown]:
    """Extract per-model token breakdown from transcript text.

    Tries Gemini table format, then Copilot per-model format.

    Behavioral reference: lib/task/tokens.sh:_task_extract_model_breakdown_from_text()
    """
    if not text.strip():
        return []

    # Try Gemini table
    _, gemini_breakdowns = _extract_gemini_table(text)
    if gemini_breakdowns:
        return gemini_breakdowns

    # Try Copilot per-model
    _, copilot_breakdowns = _extract_copilot_summary(text)
    if copilot_breakdowns:
        return copilot_breakdowns

    return []


def extract_premium_requests_from_text(text: str) -> int | None:
    """Extract Copilot premium request estimates from transcript text.

    Prefers "Total usage est." line; falls back to first match.

    Behavioral reference: lib/task/tokens.sh:_task_extract_premium_requests_from_text()
    """
    premium_re = re.compile(
        r"(\d+(?:\.\d+)?)\s+premium\s+requests?",
        re.IGNORECASE,
    )

    # Prefer "total usage" line
    for line in text.splitlines():
        if "total usage" in line.lower():
            m = premium_re.search(line)
            if m:
                val = parse_token_count(m.group(1))
                return val if val and val > 0 else None

    # Fallback: first match anywhere
    m = premium_re.search(text)
    if m:
        val = parse_token_count(m.group(1))
        return val if val and val > 0 else None

    return None


# ---------------------------------------------------------------------------
# File-level wrappers
# ---------------------------------------------------------------------------


def parse_claude_transcript(transcript_path: Path) -> TokenUsage:
    """Parse Claude CLI transcript for token usage."""
    text = read_transcript_excerpt(transcript_path)
    if not text:
        return TokenUsage(raw_transcript_path=transcript_path)
    usage = extract_token_usage_from_text(text)
    usage.raw_transcript_path = transcript_path
    return usage


def parse_copilot_transcript(transcript_path: Path) -> TokenUsage:
    """Parse Copilot CLI transcript for token usage."""
    text = read_transcript_excerpt(transcript_path)
    if not text:
        return TokenUsage(raw_transcript_path=transcript_path)
    usage = extract_token_usage_from_text(text)
    # Also try to get premium requests if not already found
    if usage.premium_requests is None:
        usage.premium_requests = extract_premium_requests_from_text(text)
    usage.raw_transcript_path = transcript_path
    return usage


def parse_gemini_transcript(transcript_path: Path) -> TokenUsage:
    """Parse Gemini CLI transcript for token usage."""
    text = read_transcript_excerpt(transcript_path)
    if not text:
        return TokenUsage(raw_transcript_path=transcript_path)
    usage = extract_token_usage_from_text(text)
    usage.raw_transcript_path = transcript_path
    return usage


def parse_codex_transcript(transcript_path: Path) -> TokenUsage:
    """Parse Codex CLI transcript for token usage."""
    text = read_transcript_excerpt(transcript_path)
    if not text:
        return TokenUsage(raw_transcript_path=transcript_path)
    usage = extract_token_usage_from_text(text)
    usage.raw_transcript_path = transcript_path
    return usage


# ---------------------------------------------------------------------------
# Token allocation for multi-issue plans
# ---------------------------------------------------------------------------


def allocate_tokens(
    total_tokens: int,
    line_counts: list[int],
) -> list[int]:
    """Distribute tokens across issues proportionally by line count.

    For multi-issue plans, tokens are allocated based on each issue's
    body line count relative to the total. The last issue gets the
    remainder to ensure exact sum.

    Behavioral reference: lib/task/tokens.sh:_task_allocate_token_estimates()
    """
    n = len(line_counts)
    if n == 0:
        return []

    if n == 1:
        return [total_tokens]

    total_lines = sum(line_counts)

    # Fallback to even distribution if no line counts
    if total_lines <= 0:
        base = total_tokens // n
        remainder = total_tokens % n
        even_result = [base + (1 if i < remainder else 0) for i in range(n)]
        return even_result

    # Proportional allocation
    result: list[int] = []
    running_sum = 0

    for i in range(n - 1):
        alloc = math.floor(total_tokens * line_counts[i] / total_lines)
        result.append(alloc)
        running_sum += alloc

    # Last issue gets the remainder
    result.append(total_tokens - running_sum)
    return result

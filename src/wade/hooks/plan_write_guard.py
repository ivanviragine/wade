#!/usr/bin/env python3
"""Plan-session file-write guard for AI tools.

Standalone script (no wade imports) that intercepts pre-tool-use hooks from
Claude Code, Cursor, Copilot, and Gemini CLI.  Blocks writes to files that
are NOT plan artifacts.

Allowed basenames / patterns:
  - PLAN.md, PLAN-*.md
  - prompt.txt
  - .transcript
  - .commit-msg
  - PR-SUMMARY.md
  - Anything under .claude/plans/ or .wade/plans/

Exit codes:
  0 — allow (or fail-open on parse error)
  2 — deny (with informative message on stderr + JSON on stdout)
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import sys

ALLOWED_BASENAMES = [
    "PLAN.md",
    "PLAN-*.md",
    "prompt.txt",
    ".transcript",
    ".commit-msg",
    "PR-SUMMARY.md",
]

ALLOWED_DIR_PREFIXES = [
    ".claude/plans/",
    ".claude/plans\\",  # Windows paths
    ".wade/plans/",
    ".wade/plans\\",  # Windows paths
]


def _is_allowed(file_path: str) -> bool:
    """Check if a file path is an allowed plan artifact."""
    if not file_path:
        return True  # No file path found — fail open

    # Normalize to forward slashes and resolve traversal (e.g. "../../")
    normalized = posixpath.normpath(file_path.replace("\\", "/"))

    # Check if path is under an allowed directory
    for prefix in ALLOWED_DIR_PREFIXES:
        norm_prefix = posixpath.normpath(prefix.replace("\\", "/")).rstrip("/")
        if (
            normalized == norm_prefix
            or normalized.startswith(f"{norm_prefix}/")
            or f"/{norm_prefix}/" in f"{normalized}/"
        ):
            return True

    # Check basename against allowed patterns
    basename = os.path.basename(normalized)
    return any(fnmatch.fnmatch(basename, pattern) for pattern in ALLOWED_BASENAMES)


def _extract_file_path(data: dict[str, object]) -> str | None:
    """Extract the target file path from the tool input JSON.

    Handles all supported tool stdin formats:
    - Claude Code: tool_input.file_path or tool_input.command (for Write/Edit)
    - Cursor: tool_input.filePath or tool_input.file_path
    - Copilot: toolArgs (JSON string) with file/path/filePath keys
    - Gemini: tool_input.file_path or tool_input.path
    """
    # Claude Code / Cursor / Gemini: tool_input dict
    tool_input = data.get("tool_input") or data.get("toolInput") or {}
    if isinstance(tool_input, dict):
        for key in ("file_path", "filePath", "path"):
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                return str(val)

    # Copilot: toolArgs is a JSON string
    tool_args = data.get("toolArgs")
    if isinstance(tool_args, str):
        try:
            args: object = json.loads(tool_args)
            if isinstance(args, dict):
                for key in ("file", "path", "filePath", "file_path"):
                    val = args.get(key)
                    if isinstance(val, str) and val:
                        return str(val)
        except (json.JSONDecodeError, ValueError):
            pass

    return None


WRITE_TOOL_NAMES = {
    "write",
    "edit",
    "multiedit",
    "create",
    "delete",
    "save",
    "append",
    "notebookedit",
}


def _extract_tool_name(data: dict[str, object]) -> str | None:
    """Extract the tool name from the hook payload."""
    for key in ("tool_name", "toolName"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return None


def _deny(file_path: str) -> None:
    """Output denial and exit."""
    msg = (
        f"BLOCKED by plan-session guard: cannot write to '{file_path}'. "
        "In plan mode, only plan artifacts (PLAN.md, PLAN-*.md, prompt.txt, "
        ".transcript, .commit-msg, PR-SUMMARY.md, .claude/plans/*, .wade/plans/*) may be written. "
        "Do NOT modify source code files."
    )
    # stderr for human-readable output
    print(msg, file=sys.stderr)
    # stdout JSON — multi-tool compatible:
    # - Claude Code: reads hookSpecificOutput.permissionDecision
    # - Cursor: reads top-level permission
    # - Copilot: reads top-level permissionDecision
    # - Gemini: reads top-level decision (also uses exit code 2 as emergency brake)
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "block",
            "permissionDecisionReason": msg,
        },
        "permission": "deny",
        "permissionDecision": "deny",
        "decision": "block",
        "reason": msg,
    }
    print(json.dumps(result))
    sys.exit(2)


def _fail_closed(e: Exception) -> None:
    """Fail-closed: any unhandled exception blocks the edit."""
    error_msg = f"Guard error: {type(e).__name__}: {e}"
    print(error_msg, file=sys.stderr)
    # Output JSON in all tool formats to ensure the block is respected
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "block",
            "permissionDecisionReason": error_msg,
        },
        "permission": "deny",
        "permissionDecision": "deny",
        "decision": "block",
        "reason": error_msg,
    }
    print(json.dumps(result))
    sys.exit(2)


def main() -> None:
    """Read tool call JSON from stdin, check file path, allow or deny.

    Wrapped by try/except in _main_with_wrapper to ensure fail-closed on exceptions.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)  # No input — fail open
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)  # Malformed input — fail open

    if not isinstance(data, dict):
        sys.exit(0)  # Unexpected format — fail open

    tool_name = _extract_tool_name(data)
    if tool_name is not None and tool_name not in WRITE_TOOL_NAMES:
        sys.exit(0)  # Non-write tool — allow unconditionally

    file_path = _extract_file_path(data)
    if file_path is None:
        sys.exit(0)  # No file path found — fail open (might be a non-write tool)

    if _is_allowed(file_path):
        sys.exit(0)

    _deny(file_path)


def _main_with_wrapper() -> None:
    """Wrapper that enforces fail-closed behavior on any unhandled exception."""
    try:
        main()
    except Exception as e:
        _fail_closed(e)


if __name__ == "__main__":
    _main_with_wrapper()

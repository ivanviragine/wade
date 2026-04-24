#!/usr/bin/env python3
"""Worktree file-write guard for AI tools.

Standalone script (no wade imports) that intercepts pre-tool-use hooks from
Claude Code, Cursor, Copilot, and Gemini CLI.  Blocks writes to files outside
the worktree root.

The worktree root is derived from this script's install location:
    .{tool}/hooks/worktree_guard.py  →  Path(__file__).resolve().parent.parent.parent

Exit codes:
  0 — allow (or fail-open on parse error / missing file path)
  2 — deny (with informative message on stderr + JSON on stdout)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _get_worktree_root() -> Path:
    """Derive the worktree root from this script's install location.

    When installed at ``.{tool}/hooks/worktree_guard.py``, three parent
    traversals reach the worktree root.
    """
    return Path(__file__).resolve().parent.parent.parent


def _resolve_path(file_path: str) -> Path:
    """Resolve a file path to absolute: absolute paths as-is, relative against CWD."""
    p = Path(file_path)
    if p.is_absolute():
        return p.resolve()
    return (Path(os.getcwd()) / p).resolve()


def _is_inside_worktree(file_path: str, worktree_root: Path) -> bool:
    """Return True if file_path resolves to a path inside worktree_root."""
    if not file_path:
        return True  # No file path — fail open

    try:
        resolved = _resolve_path(file_path)
    except (OSError, ValueError):
        return True  # Cannot resolve — fail open

    try:
        resolved.relative_to(worktree_root)
        return True
    except ValueError:
        return False


def _extract_file_path(data: dict[str, object]) -> str | None:
    """Extract the target file path from the tool input JSON.

    Handles all supported tool stdin formats:
    - Claude Code: tool_input.file_path or tool_input.path
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


def _deny(file_path: str, worktree_root: Path) -> None:
    """Output denial and exit."""
    msg = (
        f"BLOCKED by worktree guard: cannot write to '{file_path}'. "
        f"You should only edit files inside your worktree at '{worktree_root}'."
    )
    # stderr for human-readable output
    print(msg, file=sys.stderr)
    # stdout JSON — Claude Code strict schema: only hookSpecificOutput at root
    # Gemini uses exit code 2 as its block signal regardless of JSON
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        },
    }
    print(json.dumps(result))
    sys.exit(2)


def _fail_closed(e: Exception) -> None:
    """Fail-closed: any unhandled exception blocks the edit."""
    error_msg = f"Guard error: {type(e).__name__}: {e}"
    print(error_msg, file=sys.stderr)
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": error_msg,
        },
    }
    print(json.dumps(result))
    sys.exit(2)


def main() -> None:
    """Read tool call JSON from stdin, check file path, allow or deny."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)  # No input — fail open
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # Malformed input — fail open

    if not isinstance(data, dict):
        sys.exit(0)  # Unexpected format — fail open

    tool_name = _extract_tool_name(data)
    if tool_name is not None and tool_name not in WRITE_TOOL_NAMES:
        sys.exit(0)  # Non-write tool — allow unconditionally

    file_path = _extract_file_path(data)
    if file_path is None:
        sys.exit(0)  # No file path found — fail open (might be a non-write tool)

    worktree_root = _get_worktree_root()

    if _is_inside_worktree(file_path, worktree_root):
        sys.exit(0)

    _deny(file_path, worktree_root)


def _main_with_wrapper() -> None:
    """Wrapper that enforces fail-closed behavior on any unhandled exception."""
    try:
        main()
    except Exception as e:
        _fail_closed(e)


if __name__ == "__main__":
    _main_with_wrapper()

"""Reversible branch/path identity codec for provider task IDs.

Leaf module (stdlib only) so the git layer and services share one codec.

Provider task IDs are authoritative strings: GitHub numbers, Markdown's
generated decimals, and opaque ClickUp IDs such as ``abc1234``. Branches keep the
established ``{prefix}/{id}-{slug}`` shape for canonical ASCII decimals (no sign,
no leading zero) so existing numeric branches and worktrees stay compatible.
Every other ID is encoded as one delimiter-free token, ``idv1_<lowercase UTF-8
hex>``. The token cannot contain ``-`` or ``/``, so it never merges with the title
slug or prefix, and distinct IDs (case, leading zeros, punctuation, an ID that
itself looks like a codec token) always produce distinct tokens.

Decoding is strict: only the exact identity component of the final branch
segment is considered, and a malformed, non-canonical, or oversized token yields
``None`` rather than a numeric substring.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "CODEC_PREFIX",
    "MAX_TASK_ID_BYTES",
    "TaskIdentityError",
    "decode_task_token",
    "encode_task_id",
    "is_canonical_decimal",
    "is_explicit_plan_path",
    "task_id_from_branch",
    "validate_task_id",
]

CODEC_PREFIX = "idv1_"

# Bound the encoded component (5 + 2 * 64 = 133 chars) so branch names, worktree
# directory names and state filenames stay well inside common filesystem limits.
MAX_TASK_ID_BYTES = 64

_CANONICAL_DECIMAL_RE = re.compile(r"[1-9][0-9]*")
_ENCODED_RE = re.compile(rf"{CODEC_PREFIX}((?:[0-9a-f]{{2}})+)")


class TaskIdentityError(ValueError):
    """A task ID cannot be represented safely in a branch, path, or reference."""


def is_canonical_decimal(value: str) -> bool:
    """Return True for ASCII decimal IDs without sign or leading zero (``42``)."""
    return _CANONICAL_DECIMAL_RE.fullmatch(value) is not None


def is_explicit_plan_path(target: str) -> bool:
    """Return whether *target* uses anchored syntax reserved for plan files.

    Unanchored tokens such as ``a/b`` and ``plan.md`` remain exact task IDs.
    Share this predicate with identity validation so a supported ID can never
    be interpreted as a plan file instead.
    """
    separators = (os.sep,) + ((os.altsep,) if os.altsep else ())
    anchors = tuple(f"{prefix}{sep}" for prefix in (".", "..", "~") for sep in separators)
    return os.path.isabs(target) or target.startswith(anchors)


def validate_task_id(task_id: str) -> str:
    """Return *task_id* unchanged when it is a supported identity, else raise.

    Supported IDs are non-empty printable text without whitespace, not starting
    with ``-`` or anchored plan-path syntax, at most :data:`MAX_TASK_ID_BYTES` UTF-8 bytes,
    so every ID is one positional argv element (never parsed as an option or plan
    file) and one dependency-output token. Validation never normalizes: case,
    leading zeros and punctuation are part of the identity.
    """
    if not task_id:
        raise TaskIdentityError("Task ID is empty; pass the exact ID the task provider reports.")
    if any(char.isspace() for char in task_id) or not task_id.isprintable():
        raise TaskIdentityError(
            f"Task ID {task_id!r} contains whitespace or control characters; "
            "pass the exact ID the task provider reports."
        )
    if task_id.startswith("-"):
        raise TaskIdentityError(
            f"Task ID {task_id!r} starts with '-' and would be parsed as a command-line "
            "option; IDs beginning with '-' are not supported."
        )
    if is_explicit_plan_path(task_id):
        raise TaskIdentityError(
            f"Task ID {task_id!r} uses anchored path syntax reserved for plan files; "
            "absolute paths and IDs beginning with './', '../' or '~/' are not supported."
        )
    if len(task_id.encode("utf-8")) > MAX_TASK_ID_BYTES:
        raise TaskIdentityError(
            f"Task ID {task_id!r} exceeds {MAX_TASK_ID_BYTES} UTF-8 bytes and cannot be "
            "encoded into a branch or worktree name."
        )
    return task_id


def encode_task_id(task_id: str | int) -> str:
    """Encode one task ID as its branch/path identity token.

    Canonical decimals stay as-is (``42`` -> ``42``); everything else becomes
    ``idv1_<hex>`` (``abc1234`` -> ``idv1_61626331323334``, ``0042`` ->
    ``idv1_30303432``). Raises :class:`TaskIdentityError` for unsupported IDs.
    """
    value = validate_task_id(str(task_id))
    if is_canonical_decimal(value):
        return value
    return CODEC_PREFIX + value.encode("utf-8").hex()


def decode_task_token(token: str) -> str | None:
    """Decode an identity token, or ``None`` when it is not a canonical token."""
    if is_canonical_decimal(token):
        return token
    match = _ENCODED_RE.fullmatch(token)
    if match is None:
        return None
    try:
        decoded = bytes.fromhex(match.group(1)).decode("utf-8")
        # Exactly one token per ID: reject e.g. idv1_3432 (an encoded "42") and
        # IDs the encoder itself would refuse. (The pattern already rejects
        # uppercase hex.)
        if encode_task_id(decoded) != token:
            return None
    except (UnicodeDecodeError, TaskIdentityError):
        return None
    return decoded


def task_id_from_branch(branch: str) -> str | None:
    """Recover the exact task ID from a WADE branch name, or ``None``.

    WADE branches are ``{prefix}/{token}-{slug}`` or ``{prefix}/{token}``; the
    prefix may itself contain ``/`` and digits (``release/2026/42-title``).
    Only the final segment's leading token is decoded — never a numeric
    substring elsewhere (``feat/42abc`` and ``feat/idv1_zz-x`` are not task
    branches).
    """
    prefix, slash, last = branch.rpartition("/")
    if not slash or not prefix:
        return None
    token = last.split("-", 1)[0]
    return decode_task_token(token) if token else None

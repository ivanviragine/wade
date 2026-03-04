"""Title → URL/branch-safe slug conversion."""

from __future__ import annotations

import re


def slugify(title: str, max_length: int = 50) -> str:
    """Convert a title to a URL/branch-safe slug.

    - Lowercase
    - Non-alphanumeric chars → hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate to max_length (at a word boundary when possible)

    >>> slugify("Add User Authentication!")
    'add-user-authentication'
    >>> slugify("Fix: bug #42 in the API layer")
    'fix-bug-42-in-the-api-layer'
    """
    # Take first line, strip whitespace
    text = title.split("\n")[0].strip()
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")

    if len(slug) > max_length:
        # Try to truncate at a hyphen boundary
        truncated = slug[:max_length]
        last_hyphen = truncated.rfind("-")
        slug = truncated[:last_hyphen] if last_hyphen > max_length // 2 else truncated.rstrip("-")

    return slug

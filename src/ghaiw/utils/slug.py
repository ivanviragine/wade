"""Title → URL-safe slug conversion."""

from __future__ import annotations

import re


def slugify(title: str, max_length: int = 40) -> str:
    """Convert a title to a URL-safe slug.

    - Lowercase
    - Non-alphanumeric chars → hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate to max_length

    >>> slugify("Add User Authentication!")
    'add-user-authentication'
    >>> slugify("Fix: bug #42 in the API layer")
    'fix-bug-42-in-the-api-layer'
    """
    # Take first line, strip whitespace
    text = title.split("\n")[0].strip()
    # Lowercase
    text = text.lower()
    # Replace non-alphanumeric with hyphens
    text = re.sub(r"[^a-z0-9]", "-", text)
    # Collapse consecutive hyphens
    text = re.sub(r"-+", "-", text)
    # Strip leading/trailing hyphens
    text = text.strip("-")
    # Truncate
    return text[:max_length].rstrip("-")

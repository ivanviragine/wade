"""Write and validate deterministic documentation-decision receipts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from wade.git import repo as git_repo
from wade.models.session_manifest import (
    DocumentationDecision,
    DocumentationReceipt,
)
from wade.models.workflow import SessionKind
from wade.utils.safe_state import atomic_write_state_file, read_state_file


def _filename(session: SessionKind, commit: str) -> str:
    return f"docs@{session.value}@{commit}.json"


def write_documentation_receipt(
    root: Path,
    *,
    session: SessionKind,
    decision: DocumentationDecision,
    reason: str | None = None,
) -> DocumentationReceipt | None:
    """Record the decision for current HEAD without judging documentation quality."""

    try:
        commit = git_repo.rev_parse(root, "HEAD")
        receipt = DocumentationReceipt(
            commit=commit,
            session=session,
            decision=decision,
            reason=reason,
        )
    except (Exception, ValidationError):
        return None
    data = (json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode()
    return receipt if atomic_write_state_file(root, (), _filename(session, commit), data) else None


def read_documentation_receipt(
    root: Path,
    *,
    session: SessionKind,
    commit: str,
) -> DocumentationReceipt | None:
    """Read the exact current-session decision, failing closed on unsafe state."""

    raw = read_state_file(root, (), _filename(session, commit))
    if raw is None:
        return None
    try:
        receipt = DocumentationReceipt.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None
    return receipt if receipt.session is session and receipt.commit == commit else None

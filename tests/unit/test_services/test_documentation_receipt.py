"""Documentation-decision receipt and CLI contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.session_manifest import DocumentationDecision
from wade.models.workflow import SessionKind
from wade.services.documentation_receipt_service import (
    read_documentation_receipt,
    write_documentation_receipt,
)
from wade.services.implementation_service.done import _gate_documentation_decision

HEAD = "a" * 40
runner = CliRunner()


def test_updated_receipt_satisfies_only_exact_session_and_commit(tmp_path: Path) -> None:
    with patch("wade.git.repo.rev_parse", return_value=HEAD):
        receipt = write_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            decision=DocumentationDecision.UPDATED,
        )
    assert receipt is not None
    assert (
        read_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            commit=HEAD,
        )
        == receipt
    )
    assert (
        read_documentation_receipt(
            tmp_path,
            session=SessionKind.REVIEW_PR_COMMENTS,
            commit=HEAD,
        )
        is None
    )
    assert (
        read_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            commit="b" * 40,
        )
        is None
    )


def test_not_needed_requires_and_normalizes_reason(tmp_path: Path) -> None:
    with patch("wade.git.repo.rev_parse", return_value=HEAD):
        missing = write_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            decision=DocumentationDecision.NOT_NEEDED,
        )
        receipt = write_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            decision=DocumentationDecision.NOT_NEEDED,
            reason="  internal   refactor only ",
        )
    assert missing is None
    assert receipt is not None and receipt.reason == "internal refactor only"


def test_gate_refuses_without_receipt_and_accepts_current_receipt(tmp_path: Path) -> None:
    assert not _gate_documentation_decision(tmp_path, HEAD, "implementation")
    with patch("wade.git.repo.rev_parse", return_value=HEAD):
        write_documentation_receipt(
            tmp_path,
            session=SessionKind.IMPLEMENTATION,
            decision=DocumentationDecision.UPDATED,
        )
    assert _gate_documentation_decision(tmp_path, HEAD, "implementation")


def test_docs_cli_requires_exactly_one_decision(tmp_path: Path) -> None:
    with patch("wade.git.repo.get_repo_root", return_value=tmp_path):
        result = runner.invoke(app, ["implementation-session", "docs"])
    assert result.exit_code == 2
    assert "exactly one" in result.output


def test_docs_cli_records_not_needed_reason(tmp_path: Path) -> None:
    with (
        patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        patch("wade.git.repo.rev_parse", return_value=HEAD),
    ):
        result = runner.invoke(
            app,
            ["review-pr-comments-session", "docs", "--not-needed", "no user-facing change"],
        )
    assert result.exit_code == 0
    assert "documentation=not-needed" in result.output
    assert (
        read_documentation_receipt(
            tmp_path,
            session=SessionKind.REVIEW_PR_COMMENTS,
            commit=HEAD,
        )
        is not None
    )

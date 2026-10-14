"""Stable task identity must not turn uncertain discovery into a fresh PR."""

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.pr import GhCliError, PRLookup, PRSummary
from wade.services.implementation_service._shared import resolve_draft_pr_target


def test_replanning_rejects_incomplete_open_pr_listing_even_with_a_match() -> None:
    match = PRSummary(
        number=7,
        url="https://example.test/pull/7",
        headRefName="feat/42-old",
        state="OPEN",
        isDraft=True,
    )
    with (
        patch(
            "wade.services.implementation_service._shared.resolve_task_branch",
            return_value="feat/42-stale",
        ),
        patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup()),
        patch("wade.git.pr.list_prs", return_value=[match, match]),
        patch("wade.services.implementation_service._shared._OPEN_PR_SCAN_LIMIT", 2),
        pytest.raises(GhCliError, match="incomplete"),
    ):
        resolve_draft_pr_target(Path("/repo"), "42", "fix: renamed", "feat")


def test_replanning_refuses_a_live_listing_that_cannot_be_verified() -> None:
    match = PRSummary(
        number=7,
        url="https://example.test/pull/7",
        headRefName="feat/42-old",
        state="OPEN",
        isDraft=True,
    )
    with (
        patch(
            "wade.services.implementation_service._shared.resolve_task_branch",
            return_value="feat/42-stale",
        ),
        patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup()),
        patch("wade.git.pr.list_prs", return_value=[match]),
        pytest.raises(GhCliError, match="verify"),
    ):
        resolve_draft_pr_target(Path("/repo"), "42", "fix: renamed", "feat")


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_plan_extraction_uses_structural_owner_and_preserves_inner_bytes(newline: str) -> None:
    from wade.services.implementation_service.draft_pr import extract_plan_from_pr_body

    payload = "## Context\nPreserve \x85 interior text.\n```markdown\n<!-- wade:plan:end -->\n```"
    payload = payload.replace("\n", newline)
    example = "```markdown\n<!-- wade:plan:start -->\n```\n\n".replace("\n", newline)
    body = (
        example
        + "<!-- wade:plan:start -->"
        + newline
        + payload
        + newline
        + "<!-- wade:plan:end -->"
    )
    assert extract_plan_from_pr_body(body) == payload


@pytest.mark.parametrize(
    "body",
    [
        "Human body.",
        "<!-- wade:plan:start -->",
        "<!-- wade:plan:end -->",
        "<!-- wade:plan:start -->\nA.\n<!-- wade:plan:end -->\n"
        "<!-- wade:plan:start -->\nB.\n<!-- wade:plan:end -->",
        "<!-- wade:plan:start -->\n<!-- wade:future:start -->\n"
        "<!-- wade:future:end -->\n<!-- wade:plan:end -->",
        "```markdown\n<!-- wade:plan:start -->\n<!-- wade:plan:end -->\n```",
    ],
)
def test_plan_extraction_keeps_no_safe_plan_none_contract(body: str) -> None:
    from wade.services.implementation_service.draft_pr import extract_plan_from_pr_body

    assert extract_plan_from_pr_body(body) is None


def test_plan_extraction_keeps_valid_empty_string_distinct_from_missing_plan() -> None:
    from wade.services.implementation_service.draft_pr import extract_plan_from_pr_body

    assert extract_plan_from_pr_body("<!-- wade:plan:start -->\n\n<!-- wade:plan:end -->") == ""

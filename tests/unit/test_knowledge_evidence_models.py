"""Knowledge authority is explicit, versioned and independent of append order."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wade.models.knowledge import AssessmentEvent, DecisionEvidence, GitFileEvidence
from wade.utils.knowledge_events import decode_history, live_assessments


def assessment(
    identity: str, outcome: str = "current", replaces: tuple[str, ...] = ()
) -> AssessmentEvent:
    return AssessmentEvent.model_validate(
        {
            "event_id": identity,
            "entry_id": "claim-a",
            "body_digest": "sha256:" + "a" * 64,
            "outcome": outcome,
            "checked_at": datetime.now(UTC),
            "note": "Inspected the decision",
            "evidence": [DecisionEvidence(reference="ADR-42")],
            "replaces": replaces,
        }
    )


@pytest.mark.parametrize(
    "path", ["/absolute", "../escape", "a/../b", "a//b", "a/./b", "a\\b", ".", "", "a\x00b"]
)
def test_git_evidence_requires_literal_normalized_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        GitFileEvidence(path=path, checked_commit="a" * 40, blob="b" * 40)


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, None), (None, 1)])
def test_git_evidence_rejects_ambiguous_ranges(start: int | None, end: int | None) -> None:
    with pytest.raises(ValidationError):
        GitFileEvidence(
            path="source.py", checked_commit="a" * 40, blob="b" * 40, start_line=start, end_line=end
        )


def test_union_is_idempotent_and_replacements_ignore_line_order() -> None:
    old = assessment("old")
    new = assessment("new", "contradicted", ("old",))
    records = (old.model_dump_json(), new.model_dump_json())
    first = decode_history("\n".join((*records, *records)))
    second = decode_history("\n".join(reversed(records)))
    assert not first.problems and first == second
    assert live_assessments(first, "claim-a") == (new,)


def test_conflicting_durable_identity_is_corruption() -> None:
    first = assessment("same")
    second = first.model_copy(update={"outcome": "contradicted"})
    for records in ((first, second), (second, first)):
        decoded = decode_history("\n".join(e.model_dump_json() for e in records))
        assert any("conflicting payloads" in problem for problem in decoded.problems)


def test_missing_replacement_and_cycles_are_not_silent_authority() -> None:
    missing = decode_history(assessment("new", replaces=("absent",)).model_dump_json())
    assert missing.problems
    a = assessment("a", replaces=("b",))
    b = assessment("b", replaces=("a",))
    cycle = decode_history(a.model_dump_json() + "\n" + b.model_dump_json())
    assert "Assessment replacement cycle" in cycle.problems


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"id":"a","dir":"approve"}',
        '{"schema_version":99,"type":"assessment"}',
        '{"id":"a","seed":true,"up":true}',
    ],
)
def test_authoritative_decode_rejects_malformed_history(text: str) -> None:
    assert decode_history(text).problems


def test_boolean_schema_version_is_not_an_integer_protocol_version() -> None:
    payload = assessment("version").model_dump(mode="json")
    payload["schema_version"] = True
    import json

    assert decode_history(json.dumps(payload)).problems


def test_duplicate_json_keys_cannot_select_authority() -> None:
    assert decode_history('{"id":"claim-a","dir":"up","dir":"down"}').problems


def test_exact_duplicates_respect_kind_key_and_internal_prose() -> None:
    from wade.utils.knowledge_events import exact_duplicate_groups
    from wade.utils.knowledge_file import parse_entries

    text = (
        "## a | 2026-10-14 | plan | kind: invariant | key: shared\n\nSame claim.\n\n---\n"
        "## b | 2026-10-15 | implementation | tags: different | kind: invariant | key: shared\n"
        "\nSame claim.\n\n---\n"
        "## c | 2026-10-14 | implementation | kind: decision | key: shared\n\nSame claim.\n\n---\n"
        "## d | 2026-10-14 | implementation | kind: invariant | key: different\n"
        "\nSame claim.\n\n---\n"
        "## e | 2026-10-14 | implementation | kind: invariant | key: shared\n"
        "\nSame  claim.\n\n---\n"
    )
    groups = exact_duplicate_groups(parse_entries(text))
    assert len(groups) == 1 and groups[0].member_ids == ("a", "b")

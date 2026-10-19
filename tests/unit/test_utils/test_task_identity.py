"""Branch/path identity codec for exact provider task IDs (#490)."""

from __future__ import annotations

import pytest

from wade.git.branch import make_branch_name
from wade.services.implementation_service._shared import extract_issue_from_branch
from wade.utils.task_identity import (
    MAX_TASK_ID_BYTES,
    TaskIdentityError,
    decode_task_token,
    encode_task_id,
    task_id_from_branch,
    validate_task_id,
)

IDS = [
    "42",
    "0042",
    "0",
    "abc1234",
    "ABC1234",
    "86a1b2c3d",
    "DEV-123",
    "a/b",
    "a-b",
    "a.b:c",
    "idv1_3432",
    "idv1_61626331323334",
    "ÄBC",
    "x" * MAX_TASK_ID_BYTES,
]


class TestCodec:
    @pytest.mark.parametrize("task_id", IDS)
    def test_round_trip_is_exact(self, task_id: str) -> None:
        token = encode_task_id(task_id)
        assert decode_task_token(token) == task_id
        assert "-" not in token and "/" not in token

    def test_tokens_are_injective(self) -> None:
        tokens = [encode_task_id(task_id) for task_id in IDS]
        assert len(set(tokens)) == len(IDS)

    def test_canonical_decimals_keep_the_legacy_shape(self) -> None:
        assert encode_task_id("42") == "42"
        assert encode_task_id(42) == "42"
        assert encode_task_id("47239185") == "47239185"  # Markdown's generated IDs

    def test_case_and_leading_zeros_stay_distinct(self) -> None:
        assert encode_task_id("0042") == "idv1_30303432" != encode_task_id("42")
        assert encode_task_id("abc1234") != encode_task_id("ABC1234")

    def test_codec_looking_ids_are_encoded_not_trusted(self) -> None:
        assert encode_task_id("idv1_3432") == "idv1_" + b"idv1_3432".hex()

    @pytest.mark.parametrize(
        "token",
        [
            "idv1_",  # empty payload
            "idv1_616",  # odd hex length
            "idv1_ZZ",  # not hex
            "idv1_6162636G",
            "idv1_3432",  # an encoded canonical decimal ("42") is not canonical
            "IDV1_6162",  # wrong prefix case
            "idv1_ff",  # invalid UTF-8
            "idv1_6162 ",  # whitespace
            "idv1_2d616263",  # "-abc" is option-like and unsupported
            "idv1_" + "61" * (MAX_TASK_ID_BYTES + 1),  # oversized
            "0042",  # non-canonical decimal is not a token
            "123abc",
            "",
        ],
    )
    def test_malformed_tokens_decode_to_none(self, token: str) -> None:
        assert decode_task_token(token) is None

    def test_uppercase_hex_is_not_canonical(self) -> None:
        assert decode_task_token("idv1_7a") == "z"
        assert decode_task_token("idv1_7A") is None

    @pytest.mark.parametrize(
        "task_id", ["", " 42", "a b", "a\nb", "\t", "x" * 65, "-abc", "--help", "-"]
    )
    def test_unsupported_ids_raise_before_any_ref(self, task_id: str) -> None:
        with pytest.raises(TaskIdentityError):
            validate_task_id(task_id)
        with pytest.raises(TaskIdentityError):
            make_branch_name("feat", task_id, "title")


class TestBranches:
    def test_make_branch_name_encodes_opaque_ids(self) -> None:
        assert (
            make_branch_name("feat", "abc1234", "fix: opaque lifecycle")
            == "feat/idv1_61626331323334-fix-opaque-lifecycle"
        )
        assert make_branch_name("feat", 42, "Add auth") == "feat/42-add-auth"

    @pytest.mark.parametrize(
        ("branch", "expected"),
        [
            ("feat/42-add-auth", "42"),
            ("feat/42", "42"),
            ("fix/7-x", "7"),
            ("release/2026/42-title", "42"),  # numeric multi-segment prefix
            ("team/2026/idv1_61626331323334-x", "abc1234"),
            ("feat/idv1_61626331323334", "abc1234"),
            ("feat/47239185-markdown-task", "47239185"),
            ("feat/idv1_30303432-zeros", "0042"),
            ("origin/feat/42-x", "42"),
        ],
    )
    def test_decodes_only_the_final_identity_token(self, branch: str, expected: str) -> None:
        assert task_id_from_branch(branch) == expected
        assert extract_issue_from_branch(branch) == expected

    @pytest.mark.parametrize(
        "branch",
        [
            "main",
            "42-no-prefix",
            "/42-empty-prefix",
            "feat/123abc-slug",  # never truncated to 123
            "feat/0042-slug",  # zero padding is not a numeric match
            "feat/idv1_zz-malformed",
            "feat/idv1_3432-noncanonical",
            "feat/issue-42",
            "feat/42abc",
            "dependabot/npm_and_yarn/foo-1.2.3",
        ],
    )
    def test_non_identity_branches_decode_to_none(self, branch: str) -> None:
        assert task_id_from_branch(branch) is None

    @pytest.mark.parametrize("task_id", IDS)
    def test_every_branch_round_trips_with_any_prefix(self, task_id: str) -> None:
        for prefix in ("feat", "release/2026", "a/b/c"):
            branch = make_branch_name(prefix, task_id, "Title: with - punctuation/")
            assert task_id_from_branch(branch) == task_id

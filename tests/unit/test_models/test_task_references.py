"""Exact task-reference grammar, runtime headings, and PR reference policy (#490)."""

from __future__ import annotations

import pytest

from wade.models.deps import DependencyEdge, DependencyGraph
from wade.models.task import (
    TaskReference,
    escape_reference_id,
    format_issue_heading,
    format_task_ref,
    parse_all_issue_refs,
    parse_dependency_refs,
    parse_issue_heading,
    parse_task_refs,
    parse_tracking_child_ids,
    task_id_from_target,
    unescape_reference_id,
)


class TestTaskIdFromTarget:
    @pytest.mark.parametrize(
        ("provider", "target", "task_id"),
        [
            ("github", "#42", "42"),
            ("github", "42", "42"),
            ("markdown", "#47239185", "47239185"),
            # Opaque providers have no "#" shorthand: the target is the exact ID.
            ("clickup", "#abc", "#abc"),
            ("clickup", "abc1234", "abc1234"),
        ],
    )
    def test_hash_shorthand_only_for_hash_reference_providers(
        self, provider: str, target: str, task_id: str
    ) -> None:
        assert task_id_from_target(target, provider) == task_id


class TestFormatAndParse:
    @pytest.mark.parametrize(
        ("provider", "task_id", "rendered"),
        [
            ("github", "42", "#42"),
            ("markdown", "47239185", "#47239185"),
            ("clickup", "abc1234", "clickup:abc1234"),
            ("clickup", "42", "clickup:42"),
            ("clickup", "DEV-123", "clickup:DEV-123"),
            ("clickup", "a.b:c d", "clickup:a%2Eb%3Ac%20d"),
            ("github", "0042", "github:0042"),  # never a numeric #42 alias
        ],
    )
    def test_round_trip(self, provider: str, task_id: str, rendered: str) -> None:
        assert format_task_ref(task_id, provider) == rendered
        assert parse_task_refs(f"See {rendered}, then more.", provider) == [task_id]

    @pytest.mark.parametrize("task_id", ["abc1234", "ÄBC", "a%b", "x/y", "-_-"])
    def test_escaping_is_reversible_and_canonical(self, task_id: str) -> None:
        token = escape_reference_id(task_id)
        assert unescape_reference_id(token) == task_id

    @pytest.mark.parametrize("token", ["", "a%2e", "%41", "a%ZZ", "a%2", "%FF", "a b"])
    def test_malformed_or_noncanonical_escapes_are_rejected(self, token: str) -> None:
        assert unescape_reference_id(token) is None

    def test_hash_refs_are_complete_tokens(self) -> None:
        text = "#42, #123abc, repo#7, &#38; https://x/y#9 (#10) #0042 ##11 #12."
        assert parse_task_refs(text) == ["42", "10", "12"]

    def test_namespaces_are_distinct(self) -> None:
        text = "clickup:abc1234 github:42 #43 linear:XYZ-1"
        assert parse_task_refs(text, "clickup") == ["abc1234"]
        assert parse_task_refs(text, "github") == ["42", "43"]
        assert parse_task_refs(text, "markdown") == ["43"]

    def test_legacy_opaque_hash_only_when_requested(self) -> None:
        assert parse_task_refs("#abc1234", "clickup") == []
        assert parse_task_refs("#abc1234 #ABC1234", "clickup", legacy_hash=True) == [
            "abc1234",
            "ABC1234",
        ]

    def test_plain_refs_follow_the_provider(self) -> None:
        assert parse_all_issue_refs("Child #42 and #123abc") == ["42"]
        assert parse_all_issue_refs("clickup:abc1234 and #def5678", "clickup") == ["abc1234"]


class TestTrackingAndDependencies:
    @pytest.mark.parametrize(
        "task_id",
        ["a/b", "a.b", "a:b", "a%b", "a+b", "a]b", "a`b", "a,b", "a:#b", "a`#b", "a[#b", "aé"],
    )
    def test_ambiguous_legacy_refs_never_resolve_a_prefix(self, task_id: str) -> None:
        assert parse_tracking_child_ids(f"- [ ] #{task_id}", provider="clickup") == []
        assert parse_dependency_refs(
            f"**Depends on:** #{task_id}, #valid\n**Blocks:** #{task_id}", "clickup"
        ) == {"depends_on": ["valid"], "blocks": []}

    def test_legacy_structured_delimiters_and_encoded_punctuation(self) -> None:
        body = (
            "- [ ] #plain — title\n"
            "- [ ] `#code`\n"
            "- [ ] [#linked](https://app.clickup.com/t/linked)\n"
            "- [ ] clickup:a%2Fb\n"
        )
        assert parse_tracking_child_ids(body, provider="clickup") == [
            "plain",
            "code",
            "linked",
            "a/b",
        ]
        assert parse_dependency_refs(
            "**Depends on:** #plain, `#code`, [#linked](url), clickup:a%2Fb", "clickup"
        ) == {"depends_on": ["plain", "code", "linked", "a/b"], "blocks": []}

    def test_github_checklist_never_truncates(self) -> None:
        body = "- [ ] #42 — a\n- [ ] #123abc\n  - [x] `#7`\n- [ ] Fix #9\n"
        assert parse_tracking_child_ids(body, include_checked=True) == ["42", "7"]
        assert parse_tracking_child_ids(body) == ["42"]

    def test_clickup_checklist_forms(self) -> None:
        body = (
            "- [ ] clickup:abc1234 — generated\n"
            "- [x] `clickup:DEV-1`\n"
            "- [ ] [clickup:x%2Ey](https://app.clickup.com/t/x.y)\n"
            "- [ ] #def5678 — legacy WADE checklist\n"
            "- [ ] clickup:zz%ZZ — malformed\n"
            "- [ ] github:42 — other namespace\n"
        )
        assert parse_tracking_child_ids(body, include_checked=True, provider="clickup") == [
            "abc1234",
            "DEV-1",
            "x.y",
            "def5678",
        ]

    def test_dependency_lines(self) -> None:
        body = "**Depends on:** #42, #123abc, repo#9\n**Blocks:** #10\n"
        assert parse_dependency_refs(body) == {"depends_on": ["42"], "blocks": ["10"]}
        opaque = "**Depends on:** clickup:abc1234, #def5678\n**Blocks:** clickup:0042\n"
        assert parse_dependency_refs(opaque, "clickup") == {
            "depends_on": ["abc1234", "def5678"],
            "blocks": ["0042"],
        }


class TestIssueHeading:
    @pytest.mark.parametrize("task_id", ["42", "abc1234", "a:b", "x y", "0042"])
    def test_round_trip(self, task_id: str) -> None:
        line = format_issue_heading(task_id, "fix: title: with colon")
        assert parse_issue_heading(line) == (task_id, "fix: title: with colon")

    def test_legacy_numeric_heading(self) -> None:
        assert format_issue_heading("42", "Title") == "# Issue #42: Title"
        assert parse_issue_heading("#  Issue #42:  Title ") == ("42", "Title")

    @pytest.mark.parametrize("line", ["# Issue #: x", "# Issue #a%ZZ: x", "# Issue 42: x"])
    def test_malformed(self, line: str) -> None:
        assert parse_issue_heading(line) is None


class TestTaskReference:
    def test_github_issue(self) -> None:
        ref = TaskReference(task_id="42", url="https://github.com/o/r/issues/42")
        assert ref.is_github_issue
        assert ref.link == "#42"
        assert ref.reference_line(close=True) == "Closes #42"
        assert ref.reference_line(close=False) == "Implements #42"
        assert ref.part_of_line() == "Part of #42"

    @pytest.mark.parametrize("provider", ["clickup", "markdown"])
    def test_numeric_external_ids_never_use_github_syntax(self, provider: str) -> None:
        ref = TaskReference(provider=provider, task_id="42")
        assert not ref.is_github_issue
        for line in (ref.reference_line(close=True), ref.reference_line(close=False)):
            assert "#42" not in line and "Closes" not in line
        assert ref.reference_line(close=True) == f"Completes {provider}:42"

    def test_external_link_uses_url_when_known(self) -> None:
        ref = TaskReference(
            provider="clickup", task_id="abc1234", url="https://app.clickup.com/t/abc1234"
        )
        assert ref.link == "[clickup:abc1234](https://app.clickup.com/t/abc1234)"
        assert ref.reference_line(close=False) == f"Implements {ref.link}"


class TestMermaid:
    def test_safe_aliases_keep_exact_labels(self) -> None:
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="a]b", to_task='q"x', reason="first | second"),
                DependencyEdge(from_task="abc1234", to_task="a]b"),
            ]
        )
        mermaid = graph.generate_mermaid({"abc1234": 'Title "quoted"'}, "clickup")
        lines = mermaid.splitlines()
        assert lines[0] == "graph TD"
        assert '    t0["clickup:a%5Db clickup:a%5Db"]' in lines
        assert '    t1["clickup:abc1234 Title #quot;quoted#quot;"]' in lines
        assert "    t0 -->|first #124; second| t2" in lines
        assert "    t1 --> t0" in lines
        for line in lines[1:]:
            assert line.split()[0].rstrip('"[').startswith("t")
